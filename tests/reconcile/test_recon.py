"""日终对账器单元测试（QS-E03 §8.4）。

覆盖：
  - 零差异对账：通过
  - corp_action 差异：被正确分类为豁免（CORP_ACTION）
  - 零股差异（<100 股）：ODD_LOT，不触发 MANUAL_REVIEW
  - 不可解释差异（≥100 股）：UNEXPLAINED，触发 HIGH 告警
  - 现金差异超出容忍：passed=False
  - order_remark 命中率计算：空订单→1.0，部分命中→正确比例
  - B3 连续零差错周数从冻结参数读取

所有测试使用 fake/mock 对象，不依赖真实 broker / xtquant / 网络。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.reconcile.daily_recon import DailyRecon, ReconResult


# ---------------------------------------------------------------------------
# Fake 对象（零外部依赖）
# ---------------------------------------------------------------------------

class FakeAlerter:
    """捕获所有告警，不发真实网络请求。"""

    def __init__(self):
        self.calls: list[dict] = []

    def send_alert(self, level: str, message: str, source: str = "system", retry: int = 3):
        self.calls.append({"level": level, "message": message, "source": source})
        return True


class FakeLedger:
    """模拟 ExecutionLedger，可配置返回值。"""

    def __init__(
        self,
        theory_positions: dict[str, int] | None = None,
        cash_balance: float = 100_000.0,
        orders: list[dict] | None = None,
    ):
        self._theory = theory_positions or {}
        self._cash = cash_balance
        self._orders = orders or []
        self.recon_results: list[ReconResult] = []

    def compute_position_ledger(self, account_id: str, trade_date: str) -> dict[str, int]:
        return dict(self._theory)

    def compute_cash_balance(self, account_id: str, trade_date: str) -> float:
        return self._cash

    def get_orders_by_date(self, account_id: str, trade_date: str) -> list[dict]:
        return list(self._orders)

    def record_recon_result(self, result: ReconResult) -> None:
        self.recon_results.append(result)


class FakeBroker:
    """模拟 BrokerInterface，可配置返回值。"""

    def __init__(
        self,
        broker_positions: dict[str, int] | None = None,
        cash: float = 100_000.0,
    ):
        self._positions = broker_positions or {}
        self._cash = cash

    def get_positions(self, account_id: str) -> dict[str, int]:
        return dict(self._positions)

    def get_cash(self, account_id: str) -> float:
        return self._cash


def _make_ca_db(rows: list[tuple] | None = None) -> sqlite3.Connection:
    """创建内存 corporate_action 表，可注入测试行。

    Args:
        rows: (ts_code, ex_date, record_status) 列表。
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE corporate_action (
            ts_code TEXT,
            ex_date TEXT,
            record_status TEXT
        )
        """
    )
    if rows:
        conn.executemany(
            "INSERT INTO corporate_action VALUES (?, ?, ?)", rows
        )
    conn.commit()
    return conn


def _make_recon(
    theory: dict[str, int] | None = None,
    broker: dict[str, int] | None = None,
    ca_rows: list[tuple] | None = None,
    ledger_cash: float = 100_000.0,
    broker_cash: float = 100_000.0,
    orders: list[dict] | None = None,
) -> tuple[DailyRecon, FakeAlerter, FakeLedger]:
    """工厂：返回 (DailyRecon, alerter, ledger)。"""
    alerter = FakeAlerter()
    ledger = FakeLedger(
        theory_positions=theory or {},
        cash_balance=ledger_cash,
        orders=orders,
    )
    fake_broker = FakeBroker(broker_positions=broker or {}, cash=broker_cash)
    ca_db = _make_ca_db(ca_rows)
    recon = DailyRecon(
        ledger=ledger,
        broker=fake_broker,
        corp_action_db=ca_db,
        alerter=alerter,
    )
    return recon, alerter, ledger


# ---------------------------------------------------------------------------
# 测试：零差异对账
# ---------------------------------------------------------------------------

def test_zero_diff_passes():
    """理论持仓 == 券商持仓，现金相同 → passed=True，无差异记录，无告警。"""
    positions = {"000001.SZ": 1000, "000002.SZ": 500}
    recon, alerter, ledger = _make_recon(theory=positions, broker=positions)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is True
    assert result.diff_records == []
    assert result.unexplained_qty_diff == {}
    assert result.cash_diff == 0.0
    assert alerter.calls == []
    # record_recon_result 被调用一次
    assert len(ledger.recon_results) == 1


# ---------------------------------------------------------------------------
# 测试：corp_action 差异豁免
# ---------------------------------------------------------------------------

def test_corp_action_diff_exempt():
    """有 corporate_action 记录的差异分类为 CORP_ACTION，passed=True，无告警。"""
    theory = {"000001.SZ": 1000}
    broker = {"000001.SZ": 1200}  # 送转后多 200 股
    ca_rows = [("000001.SZ", "2024-03-01", "ACTIVE")]
    recon, alerter, _ = _make_recon(theory=theory, broker=broker, ca_rows=ca_rows)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is True
    assert len(result.diff_records) == 1
    assert result.diff_records[0]["category"] == "CORP_ACTION"
    assert result.unexplained_qty_diff == {}
    assert alerter.calls == []


# ---------------------------------------------------------------------------
# 测试：零股差异（ODD_LOT）
# ---------------------------------------------------------------------------

def test_odd_lot_diff_no_alert():
    """差异 < 100 股 → ODD_LOT，不触发 MANUAL_REVIEW，不告警。"""
    theory = {"000001.SZ": 1000}
    broker = {"000001.SZ": 1050}  # diff=50（<100 股）
    recon, alerter, _ = _make_recon(theory=theory, broker=broker)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is True
    assert result.diff_records[0]["category"] == "ODD_LOT"
    assert result.unexplained_qty_diff == {}
    assert alerter.calls == []


def test_odd_lot_boundary_99():
    """差异 99 股：ODD_LOT（边界值测试）。"""
    theory = {"000001.SZ": 1000}
    broker = {"000001.SZ": 1099}  # diff=99
    recon, alerter, _ = _make_recon(theory=theory, broker=broker)
    result = recon.run("2024-03-01", "A001")

    assert result.diff_records[0]["category"] == "ODD_LOT"
    assert alerter.calls == []


def test_diff_exactly_100_is_unexplained():
    """差异 100 股：恰好不是零股，分类为 UNEXPLAINED（边界值测试）。"""
    theory = {"000001.SZ": 1000}
    broker = {"000001.SZ": 1100}  # diff=100
    recon, alerter, _ = _make_recon(theory=theory, broker=broker)
    result = recon.run("2024-03-01", "A001")

    assert result.diff_records[0]["category"] == "UNEXPLAINED"
    assert "000001.SZ" in result.unexplained_qty_diff
    assert len(alerter.calls) == 1
    assert alerter.calls[0]["level"] == "HIGH"


# ---------------------------------------------------------------------------
# 测试：不可解释差异触发告警
# ---------------------------------------------------------------------------

def test_unexplained_diff_triggers_alert():
    """不可解释差异 → passed=False，HIGH 告警，unexplained_qty_diff 非空。"""
    theory = {"000001.SZ": 1000}
    broker = {"000001.SZ": 1500}  # diff=500，无 CA 记录
    recon, alerter, _ = _make_recon(theory=theory, broker=broker)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is False
    assert "000001.SZ" in result.unexplained_qty_diff
    assert result.unexplained_qty_diff["000001.SZ"] == 500
    assert any(c["level"] == "HIGH" for c in alerter.calls)


def test_multiple_unexplained_diffs():
    """多个不可解释差异：全部收集，触发一次告警。"""
    theory = {"000001.SZ": 1000, "000002.SZ": 500}
    broker = {"000001.SZ": 1500, "000002.SZ": 200}  # 两个 diff
    recon, alerter, _ = _make_recon(theory=theory, broker=broker)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is False
    assert len(result.unexplained_qty_diff) == 2
    # 只触发一次告警（统一发送）
    assert len(alerter.calls) == 1


# ---------------------------------------------------------------------------
# 测试：现金差异
# ---------------------------------------------------------------------------

def test_cash_diff_within_tolerance():
    """现金差异 ≤ 1.0（默认容忍）→ passed=True。"""
    recon, _, _ = _make_recon(ledger_cash=100_000.0, broker_cash=100_000.50)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is True
    assert result.cash_diff == pytest.approx(0.50, abs=1e-6)


def test_cash_diff_exceeds_tolerance():
    """现金差异 > 1.0 → passed=False。"""
    recon, _, _ = _make_recon(ledger_cash=100_000.0, broker_cash=100_002.0)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is False
    assert result.cash_diff == pytest.approx(2.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 测试：order_remark 命中率
# ---------------------------------------------------------------------------

def test_order_remark_hit_rate_no_orders():
    """无订单时，命中率返回 1.0（完美）。"""
    recon, _, _ = _make_recon(orders=[])
    result = recon.run("2024-03-01", "A001")
    assert result.order_remark_hit_rate == 1.0


def test_order_remark_hit_rate_all_hit():
    """所有订单 order_remark_matched=True → 命中率 1.0。"""
    orders = [
        {"order_remark_matched": True},
        {"order_remark_matched": True},
        {"order_remark_matched": True},
    ]
    recon, _, _ = _make_recon(orders=orders)
    result = recon.run("2024-03-01", "A001")
    assert result.order_remark_hit_rate == pytest.approx(1.0)


def test_order_remark_hit_rate_partial():
    """2/4 命中 → 命中率 0.5。"""
    orders = [
        {"order_remark_matched": True},
        {"order_remark_matched": True},
        {"order_remark_matched": False},
        {"order_remark_matched": False},
    ]
    recon, _, _ = _make_recon(orders=orders)
    result = recon.run("2024-03-01", "A001")
    assert result.order_remark_hit_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 测试：B3 冻结参数读取
# ---------------------------------------------------------------------------

def test_b3_recon_zero_error_weeks_from_frozen():
    """b3_recon_zero_error_weeks 从冻结参数读取，应等于 4。"""
    recon, _, _ = _make_recon()
    assert recon.b3_recon_zero_error_weeks == 4


# ---------------------------------------------------------------------------
# 测试：新券商持仓（理论中无）
# ---------------------------------------------------------------------------

def test_broker_has_extra_position_unexplained():
    """券商有持仓但理论持仓无记录，且 diff ≥100 → UNEXPLAINED 告警。"""
    theory: dict[str, int] = {}
    broker = {"000003.SZ": 200}  # theory 中没有该股
    recon, alerter, _ = _make_recon(theory=theory, broker=broker)
    result = recon.run("2024-03-01", "A001")

    assert result.passed is False
    assert "000003.SZ" in result.unexplained_qty_diff
    assert any(c["level"] == "HIGH" for c in alerter.calls)
