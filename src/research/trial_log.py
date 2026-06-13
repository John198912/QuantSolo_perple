"""试验登记与 N_eff 预算体系（研究协议 §四 / §五）。

§四：每测试一个 atomic_test 即记一行到 research_ledger（append-only，禁补记）。
§五：全生命周期 test 段评估次数上限 ≤ n_eff_budget_total（= 6）。
     validation 查看轮次上限 ≤ validation_view_budget（= 5）。

R2 红线：research_ledger / test_eval_registry 只追加，禁止 UPDATE/DELETE。
R3 红线：预算数字 n_eff_budget_total=6 / validation_view_budget=5 来自 load_frozen()['gates']。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.common.config import load_frozen

logger = logging.getLogger(__name__)


def _gates_cfg() -> dict:
    return dict(load_frozen()["gates"])


# ─────────────────────────────────────────────────────────────────────────────
# 数据库路径（默认在项目根目录 data/research_ledger.db）
# ─────────────────────────────────────────────────────────────────────────────

def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "research_ledger.db"


def get_connection(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """获取 SQLite 连接并初始化表结构（首次调用时建表）。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """初始化点时表结构（仅追加表，不含 UPDATE/DELETE）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS research_ledger (
            row_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at       TEXT    NOT NULL,   -- ISO datetime
            trial_type      TEXT    NOT NULL,   -- 'factor'/'model'/'param'/'data_gate'
            atomic_test_id  TEXT    NOT NULL,
            spec_json       TEXT    NOT NULL,   -- 试验规格 JSON
            result_json     TEXT,               -- 结果 JSON
            git_hash        TEXT    NOT NULL,
            data_cut_id     TEXT    NOT NULL,
            n_trials_raw    INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS test_eval_registry (
            eval_id                 TEXT PRIMARY KEY,      -- TEST-{YYYY}-{NN}
            eval_date               TEXT NOT NULL,
            budget_seq              INTEGER NOT NULL,      -- 1~6，超 6 自动 REJECT
            strategy_snapshot_id    TEXT NOT NULL,
            factor_set_json         TEXT NOT NULL,
            model_type              TEXT NOT NULL,
            param_hash              TEXT NOT NULL,
            n_eff_used              REAL NOT NULL,
            data_cut_id             TEXT NOT NULL,
            purpose                 TEXT NOT NULL,
            approved_by             TEXT NOT NULL,
            run_timestamp           TEXT,                  -- 运行后填写
            verdict                 TEXT,                  -- A1/A2/PASS（运行后填写）
            notes                   TEXT
        );

        CREATE TABLE IF NOT EXISTS validation_round_log (
            round_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at       TEXT    NOT NULL,
            round_seq       INTEGER NOT NULL,
            description     TEXT    NOT NULL,
            git_hash        TEXT    NOT NULL,
            data_cut_id     TEXT    NOT NULL
        );
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# atomic_test_id 生成
# ─────────────────────────────────────────────────────────────────────────────

def make_atomic_test_id(
    hypothesis: str,
    formula: str,
    lookback: int,
    universe: str,
    preprocessing: str,
    label_horizon: int,
    model: str,
) -> str:
    """生成 atomic_test_id（研究协议 §四 v1.3 C2 修复口径）。

    atomic_test_id = hash(hypothesis × formula × lookback × universe
                          × preprocessing × label_horizon × model)

    preprocessing 维度取值域 = factor_variant（raw/processed/orthogonal）。
    version（公式变更）并入 formula 维度；variant 不再独立乘入。
    """
    payload = json.dumps(
        {
            "hypothesis": hypothesis,
            "formula": formula,
            "lookback": lookback,
            "universe": universe,
            "preprocessing": preprocessing,
            "label_horizon": label_horizon,
            "model": model,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# research_ledger 记录（append-only）
# ─────────────────────────────────────────────────────────────────────────────

def log_trial(
    trial_type: str,
    spec: dict[str, Any],
    result: Optional[dict[str, Any]],
    git_hash: str,
    data_cut_id: str,
    db_path: Optional[str | Path] = None,
) -> str:
    """每测试一个 atomic_test 即记一行到 research_ledger（append-only，禁补记）。

    Args:
        trial_type:   'factor' / 'model' / 'param' / 'data_gate'
        spec:         试验规格 dict（含 atomic_test_id 字段）
        result:       试验结果 dict（可为 None，运行后补充）
        git_hash:     代码版本 git commit hash
        data_cut_id:  QS-C03 数据版本

    Returns:
        row_id（str，SQLite AUTOINCREMENT id）

    Note:
        禁止对此表执行 UPDATE/DELETE（R2 红线）。
    """
    atomic_test_id = spec.get("atomic_test_id", "")
    if not atomic_test_id:
        raise ValueError("spec 必须包含 'atomic_test_id' 字段。")

    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO research_ledger
                (logged_at, trial_type, atomic_test_id, spec_json, result_json,
                 git_hash, data_cut_id, n_trials_raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                trial_type,
                atomic_test_id,
                json.dumps(spec, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False) if result else None,
                git_hash,
                data_cut_id,
            ),
        )
        conn.commit()
        row_id = str(cur.lastrowid)
        logger.info("trial 已登记：row_id=%s type=%s atomic_id=%s", row_id, trial_type, atomic_test_id)
        return row_id
    finally:
        conn.close()


def count_trials(
    trial_type: Optional[str] = None,
    db_path: Optional[str | Path] = None,
) -> int:
    """统计 research_ledger 中的累计试验数（N_trials_raw）。

    Args:
        trial_type: 若指定，只统计该类型；None = 全部。

    Returns:
        累计行数（int）
    """
    conn = get_connection(db_path)
    try:
        if trial_type:
            row = conn.execute(
                "SELECT COUNT(*) FROM research_ledger WHERE trial_type=?", (trial_type,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM research_ledger").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def count_distinct_atomic_tests(
    trial_type: Optional[str] = None,
    db_path: Optional[str | Path] = None,
) -> int:
    """统计 distinct atomic_test_id 数（M_registered 用于 BH-FDR / Bonferroni）。"""
    conn = get_connection(db_path)
    try:
        if trial_type:
            row = conn.execute(
                "SELECT COUNT(DISTINCT atomic_test_id) FROM research_ledger WHERE trial_type=?",
                (trial_type,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(DISTINCT atomic_test_id) FROM research_ledger"
            ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# test 段 N_eff 预算
# ─────────────────────────────────────────────────────────────────────────────

def get_n_eff_budget_status(
    db_path: Optional[str | Path] = None,
) -> dict:
    """查询当前 N_eff 预算使用状态。

    Returns:
        {
            'budget_total': 6,
            'used': int,
            'remaining': int,
            'status': 'GREEN'/'YELLOW'/'RED'/'LOCKED',
        }
    """
    cfg = _gates_cfg()
    budget_total = int(cfg["n_eff_budget_total"])

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(budget_seq) FROM test_eval_registry"
        ).fetchone()
        used = int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()

    remaining = budget_total - used

    if remaining >= 4:
        status = "GREEN"
    elif remaining >= 2:
        status = "YELLOW"
    elif remaining == 1:
        status = "RED"
    else:
        status = "LOCKED"

    return {
        "budget_total": budget_total,
        "used": used,
        "remaining": remaining,
        "status": status,
    }


def register_test_eval(
    eval_date: str,
    strategy_snapshot_id: str,
    factor_set: list[str],
    model_type: str,
    param_hash: str,
    n_eff_used: float,
    data_cut_id: str,
    purpose: str,
    approved_by: str = "研究者自审，独立日期确认",
    notes: str = "",
    db_path: Optional[str | Path] = None,
) -> dict:
    """在 test_eval_registry 中提前登记 test 段评估（§5.2 强制流程）。

    必须先调用此函数，再运行 test 段评估。禁止先运行后补记。

    Args:
        eval_date:              登记日期（格式 'YYYY-MM-DD'）
        strategy_snapshot_id:   factor_registry 快照 git commit hash
        factor_set:             上线因子列表
        model_type:             模型类型（'linear'/'lightgbm'/'mixed'）
        param_hash:             模型参数 hash（防事后替换）
        n_eff_used:             本次喂入 DSR 的 N_eff 值
        data_cut_id:            QS-C03 数据版本
        purpose:                本次评估目的描述（≥ 20 字）
        approved_by:            自审声明
        notes:                  附注

    Returns:
        {'eval_id': str, 'budget_seq': int, 'status': str}

    Raises:
        RuntimeError: 预算已用完（remaining = 0），拒绝登记。
        ValueError:   purpose 长度 < 20 字。
    """
    if len(purpose) < 20:
        raise ValueError(f"purpose 必须 ≥ 20 字，当前 {len(purpose)} 字：{purpose!r}")

    budget = get_n_eff_budget_status(db_path)
    if budget["remaining"] <= 0:
        raise RuntimeError(
            f"N_eff 预算已用完（budget_total={budget['budget_total']}，used={budget['used']}）。"
            "只能用 2026 年后的新数据段，不得继续评估旧 test 段。"
        )

    budget_seq = budget["used"] + 1
    year = eval_date[:4]
    eval_id = f"TEST-{year}-{budget_seq:02d}"

    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO test_eval_registry
                (eval_id, eval_date, budget_seq, strategy_snapshot_id,
                 factor_set_json, model_type, param_hash, n_eff_used,
                 data_cut_id, purpose, approved_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eval_id,
                eval_date,
                budget_seq,
                strategy_snapshot_id,
                json.dumps(factor_set, ensure_ascii=False),
                model_type,
                param_hash,
                n_eff_used,
                data_cut_id,
                purpose,
                approved_by,
                notes,
            ),
        )
        conn.commit()
        logger.info(
            "test_eval 已登记：eval_id=%s budget_seq=%d remaining=%d",
            eval_id, budget_seq, budget["remaining"] - 1,
        )
    finally:
        conn.close()

    return {
        "eval_id": eval_id,
        "budget_seq": budget_seq,
        "remaining_after": budget["remaining"] - 1,
    }


def complete_test_eval(
    eval_id: str,
    run_timestamp: str,
    verdict: str,
    db_path: Optional[str | Path] = None,
) -> None:
    """运行完成后填写 run_timestamp 和 verdict（§5.2 运行步骤 7）。

    R2 警告：此函数对非点时表 test_eval_registry 使用 UPDATE，
    符合 R2 规定（R2 仅禁止对点时表 UPDATE）。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            UPDATE test_eval_registry
            SET run_timestamp=?, verdict=?
            WHERE eval_id=?
            """,
            (run_timestamp, verdict, eval_id),
        )
        conn.commit()
        logger.info("test_eval 结果已更新：eval_id=%s verdict=%s", eval_id, verdict)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# validation 轮次管理
# ─────────────────────────────────────────────────────────────────────────────

def get_validation_round_status(
    db_path: Optional[str | Path] = None,
) -> dict:
    """查询当前 validation 查看轮次状态。

    Returns:
        {
            'budget_total': 5,
            'used': int,
            'remaining': int,
            'exceeded': bool,
        }
    """
    cfg = _gates_cfg()
    budget_total = int(cfg["validation_view_budget"])

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM validation_round_log").fetchone()
        used = int(row[0]) if row else 0
    finally:
        conn.close()

    remaining = max(0, budget_total - used)
    return {
        "budget_total": budget_total,
        "used": used,
        "remaining": remaining,
        "exceeded": used >= budget_total,
    }


def log_validation_round(
    description: str,
    git_hash: str,
    data_cut_id: str,
    db_path: Optional[str | Path] = None,
) -> dict:
    """记录一轮 validation 查看（触发条件：factor_registry 内容变更后重新运行阶段一）。

    Args:
        description:  本轮描述（强制填写）
        git_hash:     代码版本
        data_cut_id:  数据版本

    Returns:
        {'round_seq': int, 'remaining': int, 'exceeded': bool}

    Raises:
        RuntimeError: 已超 validation_view_budget 轮次。
    """
    status = get_validation_round_status(db_path)
    if status["exceeded"]:
        raise RuntimeError(
            f"validation 查看轮次已用完（budget={status['budget_total']}）。"
            "须换新时间段，禁止继续在同一 validation 段挑选。"
        )

    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO validation_round_log (logged_at, round_seq, description, git_hash, data_cut_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                status["used"] + 1,
                description,
                git_hash,
                data_cut_id,
            ),
        )
        conn.commit()
        round_seq = int(cur.lastrowid)
        remaining = status["remaining"] - 1
        logger.info("validation 轮次已记录：round_seq=%d remaining=%d", status["used"] + 1, remaining)
    finally:
        conn.close()

    return {
        "round_seq": status["used"] + 1,
        "remaining": remaining,
        "exceeded": remaining <= 0,
    }
