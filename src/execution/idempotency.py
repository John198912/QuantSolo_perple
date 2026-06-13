"""幂等键生成与去重（QS-C04 §四 · QS-E02 §8.1）。

幂等键规则（§4.1）：
  client_order_id = HMAC-SHA256(account_id|strategy_id|trade_date|ts_code|side|rebalance_seq)
  截断为 32 字节十六进制（64 字符），满足 xtquant order_remark ≤ 64 字节约束（§8.2）。

禁止将 weight_hash 或任何会因重算抖动的内容纳入幂等键。
rebalance_seq 全局单调递增，由调用方（策略引擎）管理。

去重逻辑：
  1. DB UNIQUE(client_order_id) 兜底（DDL §7 约束）。
  2. 内存缓存（运行时快速去重，防止同进程重复提交）。
  3. OrderStateMachine.transition(IDEMPOTENT_DUPLICATE) → no-op。

order_remark 对账方案（§8）：
  order_remark = client_order_id（下单时由 XtquantAdapter 写入）。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 幂等键生成
# ---------------------------------------------------------------------------

# HMAC-SHA256 签名密钥（从环境变量读取，fallback 为进程级随机值）
# 注意：密钥变化会导致历史键无法复现，建议持久化到 secrets 管理
_HMAC_KEY: bytes = os.environb.get(
    b"QS_IDEMPOTENCY_KEY",
    os.urandom(32),  # 无环境变量时进程级随机（适合测试）
)


def generate_client_order_id(
    account_id: str,
    strategy_id: str,
    trade_date: str,   # "YYYY-MM-DD"
    ts_code: str,
    side: str,         # "BUY" | "SELL"
    rebalance_seq: int,
) -> str:
    """生成幂等键（QS-C04 §4.1）。

    幂等键 = HMAC-SHA256(account_id|strategy_id|trade_date|ts_code|side|rebalance_seq)
    截断为前 32 字节（64 hex chars），满足 xtquant order_remark ≤ 64 字节。

    Args:
        account_id: 资金账户 ID。
        strategy_id: 策略 ID（子策略隔离）。
        trade_date: 交易日，格式 "YYYY-MM-DD"。
        ts_code: 股票代码（如 "000001.SZ"）。
        side: "BUY" 或 "SELL"。
        rebalance_seq: 全局单调递增调仓序号（由调用方保证单调性）。

    Returns:
        64 字符十六进制字符串（32 字节 HMAC）。
    """
    payload = "|".join([
        account_id,
        strategy_id,
        trade_date,
        ts_code,
        side.upper(),
        str(rebalance_seq),
    ]).encode("utf-8")

    digest = hmac.new(_HMAC_KEY, payload, digestmod=hashlib.sha256).digest()
    # 取前 32 字节（64 hex），满足 xtquant 64 字节上限
    return digest[:32].hex()


def generate_sub_order_id(parent_client_order_id: str, sub_index: int) -> str:
    """子单幂等键（拆单场景，QS-E02 §7.3）。

    格式：parent_id 前 56 字符 + "_s" + 两位子单序号。
    保持 ≤ 64 字节。
    """
    prefix = parent_client_order_id[:56]
    return f"{prefix}_s{sub_index:02d}"


# ---------------------------------------------------------------------------
# 运行时内存去重缓存
# ---------------------------------------------------------------------------

class _InMemoryIdempotencyCache:
    """进程内幂等键缓存（防止同一进程内重复提交）。

    线程安全。崩溃重启后清空（DB UNIQUE 约束作为持久化兜底）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, str] = {}  # {client_order_id: first_seen_iso}

    def register(self, client_order_id: str) -> bool:
        """注册幂等键。

        Returns:
            True 表示首次注册（可继续处理）；
            False 表示已存在（重复，应触发 IDEMPOTENT_DUPLICATE）。
        """
        with self._lock:
            if client_order_id in self._seen:
                return False
            self._seen[client_order_id] = datetime.now(tz=timezone.utc).isoformat()
            return True

    def exists(self, client_order_id: str) -> bool:
        with self._lock:
            return client_order_id in self._seen

    def clear(self) -> None:
        """清空缓存（仅供测试/EOD 重置使用）。"""
        with self._lock:
            self._seen.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._seen)


# 模块级单例
_cache = _InMemoryIdempotencyCache()


# ---------------------------------------------------------------------------
# SQLite 持久化存储（DB UNIQUE 兜底）
# ---------------------------------------------------------------------------

@dataclass
class IdempotencyRecord:
    """幂等键持久化记录。"""
    client_order_id: str
    account_id: str
    strategy_id: str
    ts_code: str
    side: str
    trade_date: str
    rebalance_seq: int
    registered_at: str       # ISO-8601


class IdempotencyStore:
    """SQLite 幂等键存储（QS-E02 §8.1 DB UNIQUE 兜底）。

    表结构::

        CREATE TABLE idempotency_keys (
            client_order_id TEXT PRIMARY KEY,   -- UNIQUE 约束
            account_id      TEXT NOT NULL,
            strategy_id     TEXT NOT NULL,
            ts_code         TEXT NOT NULL,
            side            TEXT NOT NULL,
            trade_date      TEXT NOT NULL,
            rebalance_seq   INTEGER NOT NULL,
            registered_at   TEXT NOT NULL
        );

    线程安全：使用 check_same_thread=False + 外部锁。
    """

    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            client_order_id TEXT PRIMARY KEY,
            account_id      TEXT NOT NULL,
            strategy_id     TEXT NOT NULL,
            ts_code         TEXT NOT NULL,
            side            TEXT NOT NULL,
            trade_date      TEXT NOT NULL,
            rebalance_seq   INTEGER NOT NULL,
            registered_at   TEXT NOT NULL
        );
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(self._CREATE_SQL)
        self._conn.commit()

    def register(self, record: IdempotencyRecord) -> bool:
        """尝试注册幂等键。

        Returns:
            True 首次注册成功；False 已存在（UNIQUE 冲突）。
        """
        sql = """
            INSERT OR IGNORE INTO idempotency_keys
            (client_order_id, account_id, strategy_id, ts_code, side,
             trade_date, rebalance_seq, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            cur = self._conn.execute(sql, (
                record.client_order_id,
                record.account_id,
                record.strategy_id,
                record.ts_code,
                record.side,
                record.trade_date,
                record.rebalance_seq,
                record.registered_at,
            ))
            self._conn.commit()
            return cur.rowcount > 0

    def exists(self, client_order_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM idempotency_keys WHERE client_order_id = ?",
                (client_order_id,),
            )
            return cur.fetchone() is not None

    def get(self, client_order_id: str) -> Optional[IdempotencyRecord]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT client_order_id, account_id, strategy_id, ts_code, side, "
                "trade_date, rebalance_seq, registered_at "
                "FROM idempotency_keys WHERE client_order_id = ?",
                (client_order_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return IdempotencyRecord(*row)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# 高级 API：IdempotencyManager（内存 + 持久化双层）
# ---------------------------------------------------------------------------

class IdempotencyManager:
    """双层幂等管理：内存缓存（快速）+ SQLite 持久化（兜底）。

    用法::

        mgr = IdempotencyManager(db_path="run/idempotency.db")
        if not mgr.try_register(client_order_id, record):
            # 重复订单，触发 IDEMPOTENT_DUPLICATE
            state_machine.transition(OrderEvent.IDEMPOTENT_DUPLICATE)
    """

    def __init__(self, db_path: str | Path) -> None:
        self._memory = _InMemoryIdempotencyCache()
        self._db = IdempotencyStore(db_path)

    def try_register(
        self,
        client_order_id: str,
        record: Optional[IdempotencyRecord] = None,
    ) -> bool:
        """尝试注册幂等键（内存 + 持久化）。

        Args:
            client_order_id: 幂等键。
            record: 完整记录（可选，为 None 时仅做内存检查）。

        Returns:
            True 表示首次（可继续处理）；False 表示重复。
        """
        # 快速内存检查
        if self._memory.exists(client_order_id):
            return False

        # 持久化尝试
        if record is not None:
            registered = self._db.register(record)
            if not registered:
                # DB 已有（可能是崩溃重启后内存缓存已清空），同步到内存
                self._memory.register(client_order_id)
                return False

        # 注册到内存
        return self._memory.register(client_order_id)

    def exists(self, client_order_id: str) -> bool:
        return self._memory.exists(client_order_id) or self._db.exists(client_order_id)

    def close(self) -> None:
        self._db.close()


# ---------------------------------------------------------------------------
# 模块级便捷函数
# ---------------------------------------------------------------------------

def make_idempotency_record(
    client_order_id: str,
    account_id: str,
    strategy_id: str,
    ts_code: str,
    side: str,
    trade_date: str,
    rebalance_seq: int,
) -> IdempotencyRecord:
    """创建幂等键记录（registered_at 自动填充为 UTC now）。"""
    return IdempotencyRecord(
        client_order_id=client_order_id,
        account_id=account_id,
        strategy_id=strategy_id,
        ts_code=ts_code,
        side=side,
        trade_date=trade_date,
        rebalance_seq=rebalance_seq,
        registered_at=datetime.now(tz=timezone.utc).isoformat(),
    )
