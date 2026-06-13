"""点时财报查询（QS-C03 §3.3，§6）。

financials_pit_asof：点时取指定季度财报（OFFICIAL / EXPRESS / FORECAST 三阶段）。
get_pit_ttm：TTM（滚动12个月）财务指标计算。

流量项（revenue / net_profit / ocf）：拼接最近四季，每季取最新可见版本。
存量项（total_assets / total_equity / debt_ratio）：直接取最近一期时点值。

confidence_tag 枚举（QS-C03 §6）：
  'OFFICIAL'         : 全部来自年报/季报正式公告
  'EXPRESS_INCLUDED' : 含快报，无预告
  'FORECAST_PARTIAL' : 最近一季来自预告
  'INSUFFICIENT'     : 数据不足，返空
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from src.pit.query_engine import canonical_select

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 流量项：做四季拼接求和
_FLOW_METRICS: frozenset[str] = frozenset(
    {"revenue", "net_profit", "ocf", "gross_profit", "ebit"}
)

# 存量项：取最近一期时点值，不做四季拼接
_STOCK_METRICS: frozenset[str] = frozenset(
    {"total_assets", "total_equity", "total_debt", "cash"}
)

# stage 优先级（数字小者优先，OFFICIAL > EXPRESS > FORECAST）
_STAGE_PRIORITY: dict[str, int] = {
    "OFFICIAL": 1,
    "EXPRESS": 2,
    "FORECAST": 3,
}

# 标准季度末日期（报告期末日模式）
_QUARTER_ENDS: tuple[str, ...] = ("-03-31", "-06-30", "-09-30", "-12-31")


# ---------------------------------------------------------------------------
# financials_pit_asof
# ---------------------------------------------------------------------------

def financials_pit_asof(
    ts_code: str,
    as_of: str,
    *,
    end_dates: Optional[Sequence[str]] = None,
    data_cut_id: Optional[int] = None,
    financials_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """点时取财报（QS-C03 §3.3，三阶段：OFFICIAL / EXPRESS / FORECAST）。

    对每个 (ts_code, end_date, stage) 组合执行 canonical_select，
    取 visible_at <= as_of 的最新未撤销版本。

    VOIDED 最新版本 → 该 stage 不可用（不在结果中，不静默丢弃）。

    Args:
        ts_code:       股票代码，如 '000001.SZ'。
        as_of:         时间截止点，ISO datetime 字符串。
        end_dates:     报告期末日列表（'YYYY-MM-DD'）；None 返回所有可见期。
        data_cut_id:   数据切割 ID，附加到 PIT_META。
        financials_df: 测试注入：直接传入财报 DataFrame，跳过 Parquet 读取。

    Returns:
        DataFrame，每行为一个 (ts_code, end_date, stage) 的最新 ACTIVE 版本，
        含 PIT_META 六字段。

    财报查询结果中每个 end_date 可能有多个 stage（OFFICIAL > EXPRESS > FORECAST）；
    调用方根据 stage 字段自行选择最高优先级的版本。
    """
    if financials_df is None:
        financials_df = _load_financials_parquet(ts_code)

    if financials_df.empty:
        return pd.DataFrame()

    # 过滤当前 ts_code
    df = financials_df[financials_df["ts_code"] == ts_code].copy()
    if end_dates is not None:
        df = df[df["end_date"].isin(end_dates)]

    if df.empty:
        return pd.DataFrame()

    # 确保有 snapshot_rank
    if "snapshot_rank" not in df.columns:
        df["snapshot_rank"] = 0

    # canonical_select：业务键 = (ts_code, end_date, stage)
    result = canonical_select(
        df=df,
        as_of=as_of,
        key_cols=["ts_code", "end_date", "stage"],
        data_cut_id=data_cut_id,
    )

    return result


# ---------------------------------------------------------------------------
# get_pit_ttm
# ---------------------------------------------------------------------------

def get_pit_ttm(
    ts_code: str,
    as_of: str,
    metric: str,
    *,
    data_cut_id: Optional[int] = None,
    financials_df: Optional[pd.DataFrame] = None,
) -> tuple[Optional[float], str]:
    """计算 TTM（滚动12个月）财务指标（QS-C03 §6）。

    流量项：拼接最近四季，每季取最新可见版本（允许混 stage）。
    存量项：直接取最近一期时点值。

    Args:
        ts_code:       股票代码。
        as_of:         时间截止点，ISO datetime 字符串。
        metric:        指标名称，如 'revenue'/'net_profit'/'total_assets' 等。
        data_cut_id:   数据切割 ID。
        financials_df: 测试注入。

    Returns:
        (value, confidence_tag) 元组：
          value:          计算结果（float 或 None）。
          confidence_tag: 'OFFICIAL'/'EXPRESS_INCLUDED'/'FORECAST_PARTIAL'/'INSUFFICIENT'。
    """
    is_flow = metric in _FLOW_METRICS
    is_stock = metric in _STOCK_METRICS

    if not is_flow and not is_stock:
        logger.warning("get_pit_ttm: 未知指标 metric=%s，尝试作存量项处理", metric)
        is_stock = True

    # 获取点时财报截面
    fin_df = financials_pit_asof(
        ts_code=ts_code,
        as_of=as_of,
        data_cut_id=data_cut_id,
        financials_df=financials_df,
    )

    if fin_df.empty:
        return None, "INSUFFICIENT"

    if is_stock:
        return _calc_stock_metric(fin_df, metric)
    else:
        return _calc_flow_ttm(fin_df, metric)


# ---------------------------------------------------------------------------
# 内部：存量项计算
# ---------------------------------------------------------------------------

def _calc_stock_metric(
    fin_df: pd.DataFrame,
    metric: str,
) -> tuple[Optional[float], str]:
    """取最近一期存量值（QS-C03 §6 存量项）。"""
    if metric not in fin_df.columns:
        return None, "INSUFFICIENT"

    # 取最新的 end_date，stage 优先级 OFFICIAL > EXPRESS > FORECAST
    latest = _get_best_stage_row(fin_df, metric)
    if latest is None:
        return None, "INSUFFICIENT"

    value = latest.get(metric)
    if value is None or pd.isna(value):
        return None, "INSUFFICIENT"

    stage = latest.get("stage", "OFFICIAL")
    tag = _stage_to_confidence_tag(stage)
    return float(value), tag


# ---------------------------------------------------------------------------
# 内部：流量项 TTM 计算
# ---------------------------------------------------------------------------

def _calc_flow_ttm(
    fin_df: pd.DataFrame,
    metric: str,
) -> tuple[Optional[float], str]:
    """拼接最近四季流量值（QS-C03 §6 流量项）。

    每个 end_date 选取最高优先级 stage（OFFICIAL > EXPRESS > FORECAST）。
    若最近一季仅有 FORECAST：
      - 收益类 → 取 forecast_low（保守）
      - confidence_tag = 'FORECAST_PARTIAL'
    """
    if metric not in fin_df.columns and "forecast_low" not in fin_df.columns:
        return None, "INSUFFICIENT"

    # 每个 end_date 取最高优先级 stage 的行
    best_rows = _select_best_per_end_date(fin_df, metric)

    if best_rows.empty:
        return None, "INSUFFICIENT"

    # 按 end_date 降序取最近 4 个季度
    best_rows = best_rows.sort_values("end_date", ascending=False)
    recent_4 = best_rows.head(4)

    if len(recent_4) < 4:
        return None, "INSUFFICIENT"

    stages_used: list[str] = []
    total = 0.0

    for _, row in recent_4.iterrows():
        stage = row.get("stage", "OFFICIAL")
        stages_used.append(stage)

        val = row.get(metric)
        if stage == "FORECAST" and (val is None or pd.isna(val)):
            # 预告：取 forecast_low（保守估计，QS-C03 §6）
            val = row.get("forecast_low")

        if val is None or pd.isna(val):
            return None, "INSUFFICIENT"

        total += float(val)

    confidence = _calc_ttm_confidence(stages_used)
    return total, confidence


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_best_stage_row(
    fin_df: pd.DataFrame,
    metric: str,
) -> Optional[dict]:
    """取最近一期中优先级最高的 stage 行（有 metric 值）。"""
    df = fin_df.copy()
    df = df.sort_values("end_date", ascending=False)

    for end_date in df["end_date"].unique():
        group = df[df["end_date"] == end_date].copy()
        group["_stage_pri"] = group["stage"].map(_STAGE_PRIORITY).fillna(99)
        group = group.sort_values("_stage_pri")
        for _, row in group.iterrows():
            val = row.get(metric)
            if val is not None and not pd.isna(val):
                return row.to_dict()

    return None


def _select_best_per_end_date(
    fin_df: pd.DataFrame,
    metric: str,
) -> pd.DataFrame:
    """每个 end_date 取最高优先级 stage 的行（有或无 metric 值）。"""
    df = fin_df.copy()
    df["_stage_pri"] = df["stage"].map(_STAGE_PRIORITY).fillna(99)
    df = df.sort_values(["end_date", "_stage_pri"])
    best = df.groupby("end_date", sort=False).first().reset_index()
    return best


def _stage_to_confidence_tag(stage: str) -> str:
    """将最高 stage 映射为 confidence_tag。"""
    mapping = {
        "OFFICIAL": "OFFICIAL",
        "EXPRESS": "EXPRESS_INCLUDED",
        "FORECAST": "FORECAST_PARTIAL",
    }
    return mapping.get(stage, "OFFICIAL")


def _calc_ttm_confidence(stages: list[str]) -> str:
    """根据四季 stage 列表计算 TTM confidence_tag。"""
    if "FORECAST" in stages:
        return "FORECAST_PARTIAL"
    if "EXPRESS" in stages:
        return "EXPRESS_INCLUDED"
    return "OFFICIAL"


def _load_financials_parquet(ts_code: str) -> pd.DataFrame:
    """从 Parquet 加载财报数据（按 year 分区）。"""
    repo_root = Path(__file__).resolve().parents[2]
    glob_pattern = str(repo_root / "data" / "financials_pit" / "year=*" / "part-*.parquet")

    try:
        import glob as glob_mod

        files = glob_mod.glob(glob_pattern)
        if not files:
            return pd.DataFrame()
        frames = [pd.read_parquet(f) for f in files]
        df = pd.concat(frames, ignore_index=True)
        return df[df["ts_code"] == ts_code].copy() if "ts_code" in df.columns else df
    except Exception as exc:
        logger.warning("_load_financials_parquet 失败 ts_code=%s error=%s", ts_code, exc)
        return pd.DataFrame()
