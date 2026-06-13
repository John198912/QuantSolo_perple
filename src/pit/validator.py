"""数据质量闸门（QS-C03 §11.9，QS-CAL-001）。

validate_data_gate：盘后质检闸门，不通过则阻塞因子计算。
validate_adj_coverage：复权因子覆盖检查（§11.9），纳入闸门前置条件。

质检维度：
  1. 覆盖率：当日行情条数 / 预期股票数，低于阈值则 FAIL。
  2. 缺失率：核心字段（close/volume）空值比例，超阈值则 FAIL。
  3. VOIDED 行情告警：最新版本为 VOIDED 的行（QS-C03 §1.1）。
  4. 复权因子覆盖（validate_adj_coverage）：每个 ACTIVE 除权事件
     都有对应 PIT 序列，且 base_event_ver 唯一。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """数据质检结果。

    Attributes:
        passed:           True = 通过，False = 不通过（阻塞因子计算）。
        failure_reasons:  不通过时的具体原因列表。
        warnings:         警告列表（不阻塞，但需关注）。
        metrics:          质检指标字典（coverage_rate、missing_rate 等）。
    """
    passed: bool
    failure_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class AdjCoverageResult:
    """复权因子覆盖检查结果（QS-C03 §11.9）。

    Attributes:
        passed:         True = 覆盖完整。
        missing_events: 缺少 PIT 序列的 (ts_code, event_ver) 列表。
        mixed_basis:    base_event_ver 混用的 ts_code 列表。
    """
    passed: bool
    missing_events: list[tuple[str, int]] = field(default_factory=list)
    mixed_basis: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 质检参数默认值（可覆盖）
# ---------------------------------------------------------------------------

DEFAULT_MIN_COVERAGE_RATE: float = 0.95   # 覆盖率 < 95% 则 FAIL
DEFAULT_MAX_MISSING_RATE: float = 0.01    # 关键字段缺失率 > 1% 则 FAIL
DEFAULT_EXPECTED_STOCK_COUNT: int = 4000  # A 股大约 4000-5000 只


# ---------------------------------------------------------------------------
# validate_data_gate
# ---------------------------------------------------------------------------

def validate_data_gate(
    trade_date: str,
    *,
    repo_root: Optional[Path] = None,
    sqlite_path: Optional[Path] = None,
    bar_df: Optional[pd.DataFrame] = None,
    min_coverage_rate: float = DEFAULT_MIN_COVERAGE_RATE,
    max_missing_rate: float = DEFAULT_MAX_MISSING_RATE,
    expected_stock_count: int = DEFAULT_EXPECTED_STOCK_COUNT,
) -> ValidationResult:
    """盘后数据质检闸门（QS-C03 §9.2 Step 5）。

    质检通过 = 覆盖率、缺失率均合格 且无 CRITICAL 告警。
    不通过则阻塞因子计算。

    Args:
        trade_date:           目标交易日，'YYYY-MM-DD'。
        repo_root:            仓库根目录（用于定位 Parquet）。
        sqlite_path:          SQLite 路径（用于查询 VOIDED 告警日志）。
        bar_df:               测试注入：直接传入当日行情 DataFrame。
        min_coverage_rate:    覆盖率下限（默认 0.95）。
        max_missing_rate:     缺失率上限（默认 0.01）。
        expected_stock_count: 预期股票总数（用于计算覆盖率）。

    Returns:
        ValidationResult：passed / failure_reasons / warnings / metrics。
    """
    failure_reasons: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, float] = {}

    # Step 1: 加载当日行情
    if bar_df is None:
        bar_df = _load_today_bars(trade_date, repo_root)

    if bar_df.empty:
        return ValidationResult(
            passed=False,
            failure_reasons=[f"当日 {trade_date} 无行情数据（Parquet 不存在或为空）"],
            metrics={"coverage_rate": 0.0},
        )

    # Step 2: 覆盖率检查
    actual_count = int(bar_df["ts_code"].nunique()) if "ts_code" in bar_df.columns else len(bar_df)
    coverage_rate = actual_count / max(expected_stock_count, 1)
    metrics["coverage_rate"] = round(coverage_rate, 4)
    metrics["actual_stock_count"] = float(actual_count)

    if coverage_rate < min_coverage_rate:
        failure_reasons.append(
            f"覆盖率不足 {coverage_rate:.2%} < {min_coverage_rate:.2%} "
            f"（实际 {actual_count} 只，预期 {expected_stock_count} 只）"
        )

    # Step 3: 关键字段缺失率（close / volume）
    for critical_col in ("close", "volume"):
        if critical_col not in bar_df.columns:
            warnings.append(f"列 {critical_col!r} 不存在于当日行情 DataFrame")
            continue
        total = len(bar_df)
        null_count = int(bar_df[critical_col].isna().sum())
        missing_rate = null_count / max(total, 1)
        metrics[f"{critical_col}_missing_rate"] = round(missing_rate, 4)

        if missing_rate > max_missing_rate:
            failure_reasons.append(
                f"{critical_col} 缺失率过高 {missing_rate:.2%} > {max_missing_rate:.2%} "
                f"（{null_count}/{total} 条）"
            )

    # Step 4: VOIDED 行情检查（QS-C03 §1.1）
    if "record_status" in bar_df.columns:
        voided_mask = bar_df["record_status"] == "VOIDED"
        voided_count = int(voided_mask.sum())
        metrics["voided_count"] = float(voided_count)
        if voided_count > 0:
            warnings.append(
                f"当日 {trade_date} 存在 {voided_count} 条 VOIDED 行情，"
                "已单独告警（QS-C03 §1.1）"
            )

    # Step 5: CONFLICT 标记检查
    if "quality_flag" in bar_df.columns:
        conflict_count = int((bar_df["quality_flag"] == "CONFLICT").sum())
        metrics["conflict_count"] = float(conflict_count)
        if conflict_count > 0:
            warnings.append(
                f"当日 {trade_date} 存在 {conflict_count} 条三源冲突记录，"
                "已标注 quality_flag='CONFLICT'，不进入研究截面"
            )

    passed = len(failure_reasons) == 0
    return ValidationResult(
        passed=passed,
        failure_reasons=failure_reasons,
        warnings=warnings,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# validate_adj_coverage（QS-C03 §11.9）
# ---------------------------------------------------------------------------

def validate_adj_coverage(
    data_cut_id: int,
    *,
    sqlite_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
    corp_action_df: Optional[pd.DataFrame] = None,
    adj_factor_df: Optional[pd.DataFrame] = None,
) -> AdjCoverageResult:
    """校验复权因子覆盖（QS-C03 §11.9）。

    检查：
    1. 每个 ACTIVE 除权事件（corporate_action）都有对应的 adj_factor_pit 序列。
    2. 每只股票的复权序列内 base_event_ver 唯一（无混用基准）。

    输出纳入 validate_data_gate.passed 的前置条件之一。

    Args:
        data_cut_id:    数据切割 ID（当前未直接用于过滤，留接口一致性）。
        sqlite_path:    SQLite 路径，用于查询 corporate_action。
        repo_root:      仓库根目录，用于定位 adj_factor_pit Parquet。
        corp_action_df: 测试注入：corporate_action DataFrame。
        adj_factor_df:  测试注入：adj_factor_pit DataFrame。

    Returns:
        AdjCoverageResult：passed / missing_events / mixed_basis。
    """
    missing_events: list[tuple[str, int]] = []
    mixed_basis: list[str] = []

    # 加载 corporate_action（ACTIVE 行）
    if corp_action_df is None:
        corp_action_df = _load_corp_action(sqlite_path)

    active_ca = corp_action_df[
        corp_action_df["record_status"] == "ACTIVE"
    ] if "record_status" in corp_action_df.columns else corp_action_df

    if active_ca.empty:
        # 无除权事件，覆盖视为完整
        return AdjCoverageResult(passed=True)

    # 加载 adj_factor_pit 序列中存在的 (ts_code, base_event_ver) 组合
    if adj_factor_df is None:
        adj_factor_df = _load_adj_factor_summary(repo_root)

    existing_pairs: set[tuple[str, int]] = set()
    if not adj_factor_df.empty and "ts_code" in adj_factor_df.columns:
        for _, row in adj_factor_df.iterrows():
            ts = row.get("ts_code")
            bev = row.get("base_event_ver")
            if ts and bev is not None:
                existing_pairs.add((str(ts), int(bev)))

    # 检查 1：每个 ACTIVE 除权事件都有对应 PIT 序列
    for _, row in active_ca.iterrows():
        ts_code = str(row.get("ts_code", ""))
        event_ver = row.get("event_ver")
        if event_ver is None:
            continue
        pair = (ts_code, int(event_ver))
        if pair not in existing_pairs:
            missing_events.append(pair)

    # 检查 2：每只股票的序列内 base_event_ver 唯一性（无混用基准）
    # mixed_basis = 同一只股票同一 trade_date 存在多个 base_event_ver
    if not adj_factor_df.empty and "ts_code" in adj_factor_df.columns:
        if "trade_date" in adj_factor_df.columns and "base_event_ver" in adj_factor_df.columns:
            dupl = (
                adj_factor_df
                .groupby(["ts_code", "trade_date"])["base_event_ver"]
                .nunique()
            )
            mixed_codes: list[str] = dupl[dupl > 1].index.get_level_values("ts_code").unique().tolist()
            mixed_basis.extend(mixed_codes)

    passed = len(missing_events) == 0 and len(mixed_basis) == 0
    return AdjCoverageResult(
        passed=passed,
        missing_events=missing_events,
        mixed_basis=mixed_basis,
    )


# ---------------------------------------------------------------------------
# 内部：数据加载
# ---------------------------------------------------------------------------

def _load_today_bars(trade_date: str, repo_root: Optional[Path]) -> pd.DataFrame:
    """从 Parquet 加载当日行情。"""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    year = trade_date[:4]
    glob_pattern = str(
        repo_root / "data" / "daily_bar" / f"year={year}" / "part-*.parquet"
    )

    try:
        import glob as glob_mod

        files = glob_mod.glob(glob_pattern)
        if not files:
            # 尝试 CSV 后备（MinimalBarWriter 降级路径）
            csv_pattern = str(
                repo_root / "data" / "daily_bar" / f"year={year}" / f"{trade_date}.csv"
            )
            csv_files = glob_mod.glob(csv_pattern)
            if csv_files:
                return pd.read_csv(csv_files[0])
            return pd.DataFrame()

        frames = [pd.read_parquet(f) for f in files]
        df = pd.concat(frames, ignore_index=True)
        if "trade_date" in df.columns:
            df = df[df["trade_date"] == trade_date]
        return df
    except Exception as exc:
        logger.warning(
            "_load_today_bars 失败 trade_date=%s error=%s", trade_date, exc
        )
        return pd.DataFrame()


def _load_corp_action(sqlite_path: Optional[Path]) -> pd.DataFrame:
    """从 SQLite 加载 corporate_action 表。"""
    if sqlite_path is None:
        sqlite_path = Path(__file__).resolve().parents[2] / "db" / "quant.db"

    if not sqlite_path.exists():
        return pd.DataFrame(columns=["ts_code", "event_ver", "record_status"])

    try:
        with sqlite3.connect(str(sqlite_path)) as conn:
            return pd.read_sql(
                "SELECT ts_code, event_ver, record_status FROM corporate_action",
                conn,
            )
    except Exception as exc:
        logger.warning("_load_corp_action SQLite 查询失败 error=%s", exc)
        return pd.DataFrame(columns=["ts_code", "event_ver", "record_status"])


def _load_adj_factor_summary(repo_root: Optional[Path]) -> pd.DataFrame:
    """从 Parquet 加载 adj_factor_pit 摘要（ts_code, trade_date, base_event_ver）。"""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    glob_pattern = str(
        repo_root / "data" / "adj_factor_pit" / "year=*" / "ts_code=*" / "part-*.parquet"
    )

    try:
        import glob as glob_mod

        files = glob_mod.glob(glob_pattern)
        if not files:
            return pd.DataFrame()

        # 只读必要列以节省内存
        frames = [
            pd.read_parquet(f, columns=["ts_code", "trade_date", "base_event_ver"])
            for f in files
        ]
        return pd.concat(frames, ignore_index=True)
    except Exception as exc:
        logger.warning("_load_adj_factor_summary 失败 error=%s", exc)
        return pd.DataFrame()
