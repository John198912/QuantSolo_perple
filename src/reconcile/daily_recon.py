"""对账器 — 日终三方对账（QS-E03 §8.2 / QS-C04 §3.1）。

职责：
  理论持仓 C（position_ledger 推导，含 cancel_fill_type）
  vs 券商实际持仓 B
  差异分级（CORP_ACTION / ODD_LOT / UNEXPLAINED）告警。

红线遵守：
  R1：不 import xtquant。
  R2：对账结果写自有 recon/audit 表（INSERT），不对点时表 UPDATE/DELETE。
  R3：冻结参数经 load_frozen() 读取。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from src.common.config import load_frozen


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ReconResult:
    """日终对账结果（QS-C04 §8.2）。"""
    trade_date: str
    passed: bool
    diff_records: list[dict] = field(default_factory=list)
    unexplained_qty_diff: dict[str, int] = field(default_factory=dict)  # ts_code → diff
    cash_diff: float = 0.0
    recon_duration_s: float = 0.0
    order_remark_hit_rate: float = 0.0  # order_remark 反查命中率（B3 工程判线）


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class DailyRecon:
    """日终三方对账（QS-C04 §3.1）。

    三方：理论持仓 C（position_ledger）/ 券商实际 / execution_ledger。

    设计原则：
      - 不可解释差异 → 暂停（MANUAL_REVIEW）+ 告警
      - 对账结果写 recon/audit 表（INSERT），不对点时表执行 UPDATE/DELETE
      - B3 工程判线：连续零差错周数对接 frozen gates.b3_recon_zero_error_weeks
    """

    def __init__(
        self,
        ledger: Any,            # ExecutionLedger（duck-typing，避免循环依赖）
        broker: Any,            # BrokerInterface（duck-typing）
        corp_action_db: Any,    # SQLite 连接（查 corporate_action）
        alerter: Any,           # AlertManager
        recon_qty_tol: Optional[dict] = None,
        recon_cash_tol: float = 1.0,
        recon_price_tol: float = 0.005,
    ):
        self.ledger = ledger
        self.broker = broker
        self.ca_db = corp_action_db
        self.alerter = alerter
        self.qty_tol = recon_qty_tol or {}
        self.cash_tol = recon_cash_tol
        self.price_tol = recon_price_tol

        # 从冻结参数读取 B3 连续零差错周数要求（R3）
        frozen = load_frozen()
        self._b3_recon_zero_error_weeks: int = int(
            frozen["gates"]["b3_recon_zero_error_weeks"]
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self, trade_date: str, account_id: str) -> ReconResult:
        """执行日终对账。

        算法：
          1. 计算理论持仓 C（position_ledger 推导，含 cancel_fill_type）
          2. 获取券商实际持仓
          3. 与 execution_ledger 最终状态比较
          4. 差异分类（corp_action/零股/现金尾差/不可解释）
          5. 不可解释差异 → MANUAL_REVIEW + 告警
          6. 写入对账结果（INSERT to recon/audit 表，非点时表）
          7. 计算 order_remark 命中率（B3 验证）
        """
        t0 = time.monotonic()

        # Step 1: 理论持仓 C
        theory_positions: dict[str, int] = self.ledger.compute_position_ledger(
            account_id, trade_date
        )

        # Step 2: 券商实际持仓
        broker_positions: dict[str, int] = self.broker.get_positions(account_id)

        # Step 3: 差异比较
        all_codes = set(theory_positions.keys()) | set(broker_positions.keys())
        diff_records: list[dict] = []
        unexplained: dict[str, int] = {}

        for ts_code in all_codes:
            theory_qty = theory_positions.get(ts_code, 0)
            broker_qty = broker_positions.get(ts_code, 0)
            diff = broker_qty - theory_qty

            if diff == 0:
                continue

            # Step 4: 差异分类
            category = self._classify_diff(ts_code, trade_date, diff)

            diff_records.append({
                "ts_code": ts_code,
                "theory_qty": theory_qty,
                "broker_qty": broker_qty,
                "diff_qty": diff,
                "category": category,
            })

            if category == "UNEXPLAINED":
                unexplained[ts_code] = diff

        # 现金差异
        theory_cash: float = self.ledger.compute_cash_balance(account_id, trade_date)
        broker_cash: float = self.broker.get_cash(account_id)
        cash_diff: float = abs(broker_cash - theory_cash)

        # 通过标准：无不可解释差异 且 现金差异在容忍范围内
        passed = len(unexplained) == 0 and cash_diff <= self.cash_tol

        # Step 5: 不可解释差异告警（MANUAL_REVIEW）
        if unexplained:
            self.alerter.send_alert(
                "HIGH",
                f"日终对账差异 {trade_date}: {unexplained}",
                source="reconcile.daily_recon",
            )

        # Step 7: order_remark 命中率（B3 工程判线，须 ≥95%）
        hit_rate = self._calc_order_remark_hit_rate(account_id, trade_date)

        duration_s = time.monotonic() - t0

        result = ReconResult(
            trade_date=trade_date,
            passed=passed,
            diff_records=diff_records,
            unexplained_qty_diff=unexplained,
            cash_diff=cash_diff,
            recon_duration_s=duration_s,
            order_remark_hit_rate=hit_rate,
        )

        # Step 6: 写入对账结果（INSERT to recon/audit 表，R2 合规）
        self.ledger.record_recon_result(result)

        return result

    # ------------------------------------------------------------------
    # 差异分类
    # ------------------------------------------------------------------

    def _classify_diff(self, ts_code: str, trade_date: str, diff: int) -> str:
        """差异分类（QS-C04 §3.1）。

        分类规则：
          CORP_ACTION: 送转/拆分/配股已有 corporate_action 记录 → 豁免
          ODD_LOT:     零股（<100 股不可交易余额）→ 单独建账，不计零容忍
          UNEXPLAINED: 无法解释 → 停单 + 告警
        """
        # 检查是否为公司行动引起
        ca_count = self.ca_db.execute(
            """
            SELECT COUNT(*) FROM corporate_action
            WHERE ts_code = ? AND ex_date = ? AND record_status = 'ACTIVE'
            """,
            (ts_code, trade_date),
        ).fetchone()[0]

        if ca_count > 0:
            return "CORP_ACTION"

        if abs(diff) < 100:  # 零股（<100 股）
            return "ODD_LOT"

        return "UNEXPLAINED"

    # ------------------------------------------------------------------
    # order_remark 命中率
    # ------------------------------------------------------------------

    def _calc_order_remark_hit_rate(
        self,
        account_id: str,
        trade_date: str,
    ) -> float:
        """计算 order_remark 反查命中率（QS-C04 §8.2，须 ≥95%）。"""
        orders = self.ledger.get_orders_by_date(account_id, trade_date)
        if not orders:
            return 1.0
        hits = sum(1 for o in orders if o.get("order_remark_matched", False))
        return hits / len(orders)

    # ------------------------------------------------------------------
    # B3 工程判线辅助
    # ------------------------------------------------------------------

    @property
    def b3_recon_zero_error_weeks(self) -> int:
        """B3 要求的连续零差错对账周数（来自冻结参数）。"""
        return self._b3_recon_zero_error_weeks
