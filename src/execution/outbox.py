"""Outbox 三态恢复模式（QS-C04 §4.3 · QS-E02 §8.2）。

Outbox 状态：
  PENDING_SEND  — 已落 ledger，尚未发出（或不确定是否发出）
  SENT          — 已调用券商接口，等待回报
  CONFIRMED     — 已收到明确回报（成功/拒单/撤单均视为已确认）

崩溃重启恢复逻辑：
  1. 扫描所有 PENDING_SEND / SENT 记录（未确认订单）
  2. 通过 order_remark（= client_order_id）向券商查询（§8 第一路径）
  3. 两次查询一致 → 按真实状态归位；不一致 → 维持 UNKNOWN 等心跳
  4. 崩溃在 PENDING_SEND：若未确认发送窗口内，可重发；否则先查再决策
  5. 崩溃在 SENT：必须查询，严禁重发（防重复下单）
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outbox 状态枚举
# ---------------------------------------------------------------------------

class OutboxStatus(str, Enum):
    """Outbox 三态。"""
    PENDING_SEND = "PENDING_SEND"   # 已落 ledger，未发出（或不确定）
    SENT = "SENT"                   # 已调用券商接口
    CONFIRMED = "CONFIRMED"         # 已收到明确回报


# ---------------------------------------------------------------------------
# Outbox 记录 dataclass
# ---------------------------------------------------------------------------

@dataclass
class OutboxRecord:
    """单笔 outbox 记录。"""
    client_order_id: str            # 幂等键 = order_remark
    account_id: str
    ts_code: str
    side: str                       # "BUY" | "SELL"
    qty: int
    limit_price: Optional[str]      # Decimal 序列化为 str，None = 市价
    order_type: str                 # "LIMIT" | "MARKET"
    status: OutboxStatus
    send_attempt_id: Optional[str]  # 本次发送尝试唯一 ID（UUID）
    broker_order_id: Optional[str]  # 券商委托编号（SENT 后填充）
    created_at: str                 # ISO-8601
    updated_at: str                 # ISO-8601
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Outbox 三态分类（纯函数）
# ---------------------------------------------------------------------------

def classify_outbox_state(
    record: OutboxRecord,
    now_iso: Optional[str] = None,
    pending_send_resend_window_secs: int = 30,
) -> str:
    """三态分类（崩溃恢复核心逻辑，纯函数）。

    Args:
        record: outbox 记录。
        now_iso: 当前时间 ISO-8601（None = 使用 UTC now）。
        pending_send_resend_window_secs: PENDING_SEND 安全重发窗口（秒）。

    Returns:
        "SAFE_RESEND"   — PENDING_SEND 且在安全重发窗口内，可重发
        "QUERY_FIRST"   — SENT 或超出窗口的 PENDING_SEND，必须先查券商
        "CONFIRMED"     — 已确认，无需处理
    """
    if record.status == OutboxStatus.CONFIRMED:
        return "CONFIRMED"

    if record.status == OutboxStatus.SENT:
        # SENT 状态：必须先查，严禁重发
        return "QUERY_FIRST"

    # PENDING_SEND：判断是否在安全重发窗口内
    if now_iso is None:
        now_iso = datetime.now(tz=timezone.utc).isoformat()

    try:
        created = datetime.fromisoformat(record.created_at)
        now = datetime.fromisoformat(now_iso)
        # 确保有时区信息
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        elapsed = (now - created).total_seconds()
    except (ValueError, TypeError):
        elapsed = float("inf")

    if elapsed < pending_send_resend_window_secs:
        return "SAFE_RESEND"
    return "QUERY_FIRST"


# ---------------------------------------------------------------------------
# SQLite Outbox 存储
# ---------------------------------------------------------------------------

class OutboxStore:
    """SQLite outbox 持久化存储（WAL 模式，线程安全）。

    表结构（DDL v1.3）::

        CREATE TABLE IF NOT EXISTS outbox (
            client_order_id   TEXT PRIMARY KEY,
            account_id        TEXT NOT NULL,
            ts_code           TEXT NOT NULL,
            side              TEXT NOT NULL,
            qty               INTEGER NOT NULL,
            limit_price       TEXT,
            order_type        TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'PENDING_SEND',
            send_attempt_id   TEXT,
            broker_order_id   TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            retry_count       INTEGER NOT NULL DEFAULT 0
        );
    """

    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS outbox (
            client_order_id   TEXT PRIMARY KEY,
            account_id        TEXT NOT NULL,
            ts_code           TEXT NOT NULL,
            side              TEXT NOT NULL,
            qty               INTEGER NOT NULL,
            limit_price       TEXT,
            order_type        TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'PENDING_SEND',
            send_attempt_id   TEXT,
            broker_order_id   TEXT,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            retry_count       INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(self._CREATE_SQL)
        self._conn.commit()

    def insert_pending(self, record: OutboxRecord) -> None:
        """落 PENDING_SEND 记录（事务写，先于发券商）。"""
        sql = """
            INSERT OR IGNORE INTO outbox
            (client_order_id, account_id, ts_code, side, qty, limit_price,
             order_type, status, send_attempt_id, broker_order_id,
             created_at, updated_at, retry_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            self._conn.execute(sql, (
                record.client_order_id,
                record.account_id,
                record.ts_code,
                record.side,
                record.qty,
                record.limit_price,
                record.order_type,
                OutboxStatus.PENDING_SEND.value,
                record.send_attempt_id,
                record.broker_order_id,
                record.created_at,
                record.updated_at,
                record.retry_count,
            ))
            self._conn.commit()

    def mark_sent(self, client_order_id: str, broker_order_id: str) -> None:
        """订单已发出，标记为 SENT。"""
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET status=?, broker_order_id=?, updated_at=? "
                "WHERE client_order_id=?",
                (OutboxStatus.SENT.value, broker_order_id, now, client_order_id),
            )
            self._conn.commit()

    def mark_confirmed(self, client_order_id: str) -> None:
        """已收到明确回报，标记为 CONFIRMED。"""
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET status=?, updated_at=? WHERE client_order_id=?",
                (OutboxStatus.CONFIRMED.value, now, client_order_id),
            )
            self._conn.commit()

    def increment_retry(self, client_order_id: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET retry_count=retry_count+1, updated_at=? "
                "WHERE client_order_id=?",
                (now, client_order_id),
            )
            self._conn.commit()

    def get_unconfirmed(self) -> list[OutboxRecord]:
        """获取所有未确认记录（PENDING_SEND + SENT）。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT client_order_id, account_id, ts_code, side, qty, limit_price, "
                "order_type, status, send_attempt_id, broker_order_id, "
                "created_at, updated_at, retry_count "
                "FROM outbox WHERE status != ?",
                (OutboxStatus.CONFIRMED.value,),
            )
            rows = cur.fetchall()
        return [
            OutboxRecord(
                client_order_id=r[0],
                account_id=r[1],
                ts_code=r[2],
                side=r[3],
                qty=r[4],
                limit_price=r[5],
                order_type=r[6],
                status=OutboxStatus(r[7]),
                send_attempt_id=r[8],
                broker_order_id=r[9],
                created_at=r[10],
                updated_at=r[11],
                retry_count=r[12],
            )
            for r in rows
        ]

    def get(self, client_order_id: str) -> Optional[OutboxRecord]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT client_order_id, account_id, ts_code, side, qty, limit_price, "
                "order_type, status, send_attempt_id, broker_order_id, "
                "created_at, updated_at, retry_count "
                "FROM outbox WHERE client_order_id=?",
                (client_order_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return OutboxRecord(
            client_order_id=row[0],
            account_id=row[1],
            ts_code=row[2],
            side=row[3],
            qty=row[4],
            limit_price=row[5],
            order_type=row[6],
            status=OutboxStatus(row[7]),
            send_attempt_id=row[8],
            broker_order_id=row[9],
            created_at=row[10],
            updated_at=row[11],
            retry_count=row[12],
        )

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# OutboxRecoveryManager：崩溃重启后重放未确认订单
# ---------------------------------------------------------------------------

@dataclass
class RecoveryAction:
    """恢复建议动作。"""
    record: OutboxRecord
    action: str        # "RESEND" | "QUERY_THEN_RECONCILE" | "SKIP"
    detail: str


class OutboxRecoveryManager:
    """崩溃重启后恢复未确认订单（§4.3 outbox 三态恢复骨架）。

    用法（启动时调用）::

        mgr = OutboxRecoveryManager(store, broker_adapter)
        actions = mgr.plan_recovery()
        for action in actions:
            if action.action == "RESEND":
                # 重发订单
                ...
            elif action.action == "QUERY_THEN_RECONCILE":
                # 查询券商状态后归位
                ...
    """

    def __init__(
        self,
        store: OutboxStore,
        pending_send_resend_window_secs: int = 30,
    ) -> None:
        self._store = store
        self._window = pending_send_resend_window_secs

    def plan_recovery(self) -> list[RecoveryAction]:
        """扫描未确认记录，生成恢复动作计划（不实际执行）。

        Returns:
            list[RecoveryAction]，每条记录一个建议动作。
        """
        unconfirmed = self._store.get_unconfirmed()
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        actions: list[RecoveryAction] = []

        for rec in unconfirmed:
            classification = classify_outbox_state(rec, now_iso, self._window)

            if classification == "CONFIRMED":
                actions.append(RecoveryAction(
                    record=rec,
                    action="SKIP",
                    detail="已确认，无需处理。",
                ))
            elif classification == "SAFE_RESEND":
                actions.append(RecoveryAction(
                    record=rec,
                    action="RESEND",
                    detail=(
                        f"PENDING_SEND 且在 {self._window}s 重发窗口内，"
                        "可安全重发（严格幂等保护）。"
                    ),
                ))
            else:
                # SENT 或超窗口 PENDING_SEND → 必须先查
                detail = (
                    "SENT 状态，严禁重发，须先通过 order_remark 查询券商状态后归位。"
                    if rec.status == OutboxStatus.SENT
                    else "PENDING_SEND 超重发窗口，须先查询确认是否已发出。"
                )
                actions.append(RecoveryAction(
                    record=rec,
                    action="QUERY_THEN_RECONCILE",
                    detail=detail,
                ))

            logger.info(
                "outbox_recovery client_order_id=%s status=%s action=%s",
                rec.client_order_id,
                rec.status.value,
                actions[-1].action,
            )

        return actions

    def mark_resent(self, client_order_id: str, new_broker_order_id: str) -> None:
        """重发后更新状态。"""
        self._store.mark_sent(client_order_id, new_broker_order_id)
        self._store.increment_retry(client_order_id)

    def mark_recovered(self, client_order_id: str) -> None:
        """查询归位完成，标记为 CONFIRMED。"""
        self._store.mark_confirmed(client_order_id)
