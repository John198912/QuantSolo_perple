"""物理一键熔断（QS-C04 §5.2 · QS-E02 §10）。

独立进程脚本：不依赖主进程内存/状态，直接通过 xtquant 撤单+市价平仓。
此文件是系统中唯一可绕过策略层的执行路径（白名单例外）。

⚠️ import xtquant 白名单：
   此文件是 break-glass 熔断专用，允许直接 import xtquant。

执行步骤（§5.2 STEP 1-6）：
  STEP 1: 触发确认（二次确认防误操作）
  STEP 2: 夺取全局下单令牌
  STEP 3: 撤销所有在途委托
  STEP 4: 按 xtquant sellable_qty 市价平仓
  STEP 5: 全量写 execution_ledger（break_glass_signature）
  STEP 6: 进入暂停（BREAK_GLASS），等待人工复盘

降级路径：
  xtquant 不可达 → 打印券商 APP 手动清仓 SOP

合规注意（§5.2）：
  - break-glass 清仓不受申报限速（§6.1）约束（紧急减仓优先）
  - 所有动作全量写 execution_ledger，带 break_glass_signature
  - 令牌单向幂等：夺取后只能人工归还（删除 lock 文件）
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 允许 import xtquant（白名单）
try:
    from xtquant import xttrader as _xttrader_mod
    from xtquant import xtconstant as _xtconstant
    _XTQUANT_AVAILABLE = True
except ImportError:
    _xttrader_mod = None   # type: ignore[assignment]
    _xtconstant = None     # type: ignore[assignment]
    _XTQUANT_AVAILABLE = False


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TOKEN_FILE = Path("run/order_token.lock")
HALT_STATE_FILE = Path("run/halt_state.json")
LEDGER_DB = Path("run/execution_ledger.db")

# 熔断动作延迟（break-glass 绕过正常限速，但仍加少量延迟防止刷单）
BREAK_GLASS_ORDER_INTERVAL_SECS = 1.1

# 熔断签名密钥（从环境变量读取，用于 HMAC 签名）
_BG_HMAC_KEY: bytes = os.environb.get(b"QS_BREAK_GLASS_KEY", b"break-glass-fallback-key")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_compact() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")


def _make_signature(payload: str) -> str:
    """HMAC-SHA256 熔断签名（§5.2 break_glass_signature）。"""
    sig = hmac.new(_BG_HMAC_KEY, payload.encode("utf-8"), digestmod=hashlib.sha256)
    return sig.hexdigest()


# ---------------------------------------------------------------------------
# STEP 1: 二次确认
# ---------------------------------------------------------------------------

def _confirm_break_glass(account_id: str) -> bool:
    """二次确认防误操作（§5.2 Step 1）。

    Returns:
        True 表示确认触发，False 表示取消。
    """
    print("=" * 60)
    print("  ⚠️  物理一键熔断 · 此操作将清仓所有持仓")
    print("  ⚠️  PHYSICAL BREAK-GLASS: WILL LIQUIDATE ALL POSITIONS")
    print("=" * 60)
    print()
    try:
        confirm1 = input("输入 'BREAK-GLASS' 确认触发: ").strip()
    except EOFError:
        return False
    if confirm1 != "BREAK-GLASS":
        print("已取消。")
        return False

    expected_suffix = account_id[-4:] if len(account_id) >= 4 else account_id
    try:
        confirm2 = input(f"再次确认，输入账户末四位 ({expected_suffix}): ").strip()
    except EOFError:
        return False
    if confirm2 != expected_suffix:
        print("账户验证失败，已取消。")
        return False

    return True


# ---------------------------------------------------------------------------
# STEP 2: 夺取全局下单令牌
# ---------------------------------------------------------------------------

def _acquire_order_token(token_file: Path = TOKEN_FILE) -> None:
    """原子写令牌文件（主进程检测到令牌即停止下单，§5.2 Step 2）。

    令牌单向幂等：只能由人工删除归还。
    """
    token_file.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "acquired_by": "break_glass",
        "acquired_at": _now(),
        "pid": os.getpid(),
    })
    token_file.write_text(payload, encoding="utf-8")
    print(f"[{_now()}] 下单令牌已夺取（主进程停止下单）: {token_file}")


# ---------------------------------------------------------------------------
# STEP 3: 撤销所有在途委托
# ---------------------------------------------------------------------------

_ACTIVE_XT_STATUSES = {48, 49, 50}  # SUBMITTED / LIVE / PARTIAL


def _cancel_all_orders(xttrader: object, account_id: str) -> list[dict]:
    """撤销所有活跃委托，返回已撤委托列表（§5.2 Step 3）。"""
    cancelled: list[dict] = []
    try:
        orders = _xttrader_mod.query_stock_orders(account_id)  # type: ignore[attr-defined]
        if orders is None:
            orders = []
    except Exception as exc:
        print(f"[{_now()}] [WARN] 查询委托失败: {exc}")
        return cancelled

    for o in orders:
        status = getattr(o, "order_status", -1)
        if int(status) in _ACTIVE_XT_STATUSES:
            try:
                result = xttrader.cancel_order_stock(  # type: ignore[union-attr]
                    account=account_id,
                    order_id=int(o.order_id),
                )
                ok = result == 0
                cancelled.append({
                    "broker_order_id": str(o.order_id),
                    "ts_code": getattr(o, "stock_code", ""),
                    "cancel_ok": ok,
                })
                print(
                    f"[{_now()}] 撤单 {getattr(o, 'stock_code', '?')} "
                    f"order_id={o.order_id} ok={ok}"
                )
            except Exception as exc:
                print(f"[{_now()}] [WARN] 撤单失败 order_id={o.order_id}: {exc}")

    return cancelled


# ---------------------------------------------------------------------------
# STEP 4: 市价平仓
# ---------------------------------------------------------------------------

def _market_liquidate(xttrader: object, account_id: str) -> list[dict]:
    """按 xtquant sellable_qty 市价平仓（§5.2 Step 4）。"""
    filled_records: list[dict] = []
    try:
        positions = xttrader.query_stock_positions(account_id)  # type: ignore[union-attr]
        if positions is None:
            positions = []
    except Exception as exc:
        print(f"[{_now()}] [WARN] 查询持仓失败: {exc}")
        return filled_records

    for pos in positions:
        sellable = int(getattr(pos, "can_use_volume", 0))
        if sellable <= 0:
            continue

        ts_code = getattr(pos, "stock_code", "UNKNOWN")
        bg_remark = f"BG_{_now_compact()}"
        try:
            broker_id = xttrader.order_stock(  # type: ignore[union-attr]
                account=account_id,
                stock_code=ts_code,
                order_type=_xtconstant.STOCK_SELL,    # type: ignore[union-attr]
                order_volume=sellable,
                price_type=_xtconstant.MARKET_PRICE,  # type: ignore[union-attr]
                price=0,
                order_remark=bg_remark,
            )
            filled_records.append({
                "ts_code": ts_code,
                "qty": sellable,
                "broker_order_id": str(broker_id),
                "order_remark": bg_remark,
            })
            print(
                f"[{_now()}] 市价卖出 {ts_code} × {sellable}"
                f" broker_order_id={broker_id}"
            )
        except Exception as exc:
            print(f"[{_now()}] [WARN] 市价卖出 {ts_code} 失败: {exc}")

        # 紧急减仓绕过正常限速，但仍加小延迟避免快速重复（§5.2）
        time.sleep(BREAK_GLASS_ORDER_INTERVAL_SECS)

    return filled_records


# ---------------------------------------------------------------------------
# STEP 5: 写 execution_ledger
# ---------------------------------------------------------------------------

def _record_break_glass(
    filled_records: list[dict],
    cancelled_orders: list[dict],
    account_id: str,
    ledger_db: Path = LEDGER_DB,
) -> None:
    """全量写 execution_ledger，带 break_glass_signature（§5.2 Step 5）。"""
    ledger_db.parent.mkdir(parents=True, exist_ok=True)
    sig_payload = json.dumps({
        "account_id": account_id,
        "executed_at": _now(),
        "filled_count": len(filled_records),
        "cancelled_count": len(cancelled_orders),
    }, sort_keys=True)
    signature = _make_signature(sig_payload)

    try:
        conn = sqlite3.connect(str(ledger_db))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS break_glass_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      TEXT NOT NULL,
                executed_at     TEXT NOT NULL,
                action          TEXT NOT NULL,
                ts_code         TEXT,
                qty             INTEGER,
                broker_order_id TEXT,
                order_remark    TEXT,
                break_glass_signature TEXT NOT NULL
            )
        """)
        for rec in filled_records:
            conn.execute(
                "INSERT INTO break_glass_log "
                "(account_id, executed_at, action, ts_code, qty, broker_order_id, "
                "order_remark, break_glass_signature) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    account_id, _now(), "MARKET_SELL",
                    rec.get("ts_code"), rec.get("qty"),
                    rec.get("broker_order_id"), rec.get("order_remark"),
                    signature,
                ),
            )
        for rec in cancelled_orders:
            conn.execute(
                "INSERT INTO break_glass_log "
                "(account_id, executed_at, action, ts_code, qty, broker_order_id, "
                "order_remark, break_glass_signature) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    account_id, _now(), "CANCEL",
                    rec.get("ts_code"), None,
                    rec.get("broker_order_id"), None,
                    signature,
                ),
            )
        conn.commit()
        conn.close()
        print(f"[{_now()}] 已写入 execution_ledger: {ledger_db}")
    except Exception as exc:
        print(f"[{_now()}] [WARN] 写 ledger 失败: {exc}")


# ---------------------------------------------------------------------------
# STEP 6: 写 BREAK_GLASS 暂停态
# ---------------------------------------------------------------------------

def _write_halt_state(
    reason: str,
    halt_state_file: Path = HALT_STATE_FILE,
) -> None:
    """写入系统暂停状态文件（§5.2 Step 6）。"""
    halt_state_file.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "halt_reason": reason,
        "halted_at": _now(),
        "instructions": [
            "1. 通过券商 APP 确认持仓已清零",
            "2. 截图存档",
            "3. 人工删除 run/order_token.lock（归还令牌）",
            "4. 系统重启后先进 RECONCILE 对账",
        ],
    }
    halt_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{_now()}] 已写入暂停状态: {halt_state_file}")


# ---------------------------------------------------------------------------
# 降级路径：手动清仓 SOP
# ---------------------------------------------------------------------------

def _print_manual_sop() -> None:
    """xtquant 不可达时打印人工清仓 SOP（§5.2 降级）。"""
    print()
    print("=" * 60)
    print("  xtquant 不可达 · 请执行手动清仓 SOP")
    print("=" * 60)
    print("  1. 登录券商 APP / WEB 交易界面")
    print("  2. 全选持仓，一键清仓（市价卖出所有持仓）")
    print("  3. 确认所有订单已成交或已撤")
    print("  4. 截图存档（附时间戳）")
    print("  5. 删除 run/order_token.lock（归还下单令牌）")
    print("  6. 系统重启后进 RECONCILE 对账")
    print()


# ---------------------------------------------------------------------------
# 连接 xtquant
# ---------------------------------------------------------------------------

def _connect_xtquant(account_id: str) -> Optional[object]:
    """尝试连接 xtquant，返回 XtQuantTrader 实例或 None。"""
    if not _XTQUANT_AVAILABLE:
        return None
    try:
        path = os.getenv("XTQUANT_PATH", "")
        xt = _xttrader_mod.XtQuantTrader(path, int(os.getpid()))  # type: ignore[union-attr]
        connect_result = xt.connect()
        if connect_result != 0:
            print(f"[{_now()}] [WARN] xtquant 连接失败 code={connect_result}")
            return None
        return xt
    except Exception as exc:
        print(f"[{_now()}] [WARN] xtquant 初始化失败: {exc}")
        return None


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main(
    account_id: Optional[str] = None,
    token_file: Path = TOKEN_FILE,
    ledger_db: Path = LEDGER_DB,
    halt_state_file: Path = HALT_STATE_FILE,
    skip_confirm: bool = False,  # 仅供自动化测试使用
) -> int:
    """物理一键熔断主函数（可作为库调用，也可作为 CLI 执行）。

    Returns:
        退出码：0=完成，1=确认失败，2=xtquant降级，3=异常
    """
    account_id = account_id or os.getenv("XTQUANT_ACCOUNT", "")

    if not account_id:
        print("[ERROR] 未配置 XTQUANT_ACCOUNT 环境变量，无法执行熔断。")
        return 3

    # STEP 1: 二次确认
    if not skip_confirm:
        if not _confirm_break_glass(account_id):
            return 1

    print(f"\n[{_now()}] ===== 开始执行物理熔断 =====")

    # STEP 2: 夺取令牌
    _acquire_order_token(token_file)

    # STEP 3 & 4: 尝试 xtquant 执行
    cancelled_orders: list[dict] = []
    filled_records: list[dict] = []

    xt = _connect_xtquant(account_id)
    if xt is not None:
        print(f"[{_now()}] xtquant 连接成功，开始撤单+平仓...")
        # STEP 3: 撤所有在途单
        cancelled_orders = _cancel_all_orders(xt, account_id)
        print(f"[{_now()}] 已撤 {len(cancelled_orders)} 笔在途委托")

        # STEP 4: 市价平仓
        filled_records = _market_liquidate(xt, account_id)
        print(f"[{_now()}] 已发出 {len(filled_records)} 笔市价平仓单")

        # STEP 5: 写 ledger
        _record_break_glass(filled_records, cancelled_orders, account_id, ledger_db)

        exit_code = 0
    else:
        print(f"[{_now()}] xtquant 不可达，降级为手动清仓 SOP")
        _print_manual_sop()
        exit_code = 2

    # STEP 6: 进入 BREAK_GLASS 暂停态
    _write_halt_state("BREAK_GLASS", halt_state_file)
    print(f"\n[{_now()}] ===== 系统已进入 BREAK_GLASS 暂停态 =====")
    print("恢复步骤：")
    print("  1. 通过券商 APP 确认持仓已清零")
    print("  2. 截图存档")
    print("  3. 人工删除 run/order_token.lock（归还令牌）")
    print("  4. 系统重启后先进 RECONCILE 对账")

    return exit_code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
