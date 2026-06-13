"""向量化回测引擎（QS-C01 §4 / §12.2；功能设计文档 §4.4）。

快速因子研究用。假设每期满仓调仓，用截面收益近似模拟。
适用场景：因子 IC 检验、快速参数扫描。

R3 红线：成本数字经 load_frozen()['cost'] 读取，不硬编码。
R6 红线：成本计算使用 Decimal；汇总指标转 float 输出。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

import numpy as np
import pandas as pd

from src.research.backtest.cost_models import CostModel, get_baseline_model

logger = logging.getLogger(__name__)


class VectorizedBacktest:
    """向量化回测引擎（快速因子研究）。

    假设每期满仓调仓，用截面收益近似模拟。
    适用场景：因子 IC 检验、快速参数扫描。

    R6：成本通过 CostModel.calc_transaction_cost (Decimal) 计算后转 float。
    """

    def __init__(
        self,
        cost_model: Optional[CostModel] = None,
        data_cut_id: Optional[int] = None,
        pit_engine: Optional[object] = None,
    ) -> None:
        """
        Args:
            cost_model:  成本模型（默认 cm_v3_baseline）
            data_cut_id: PIT 数据版本 ID（透传给 PitQueryEngine）
            pit_engine:  PitQueryEngine 实例（可选，不传时不做 PIT 查询）
        """
        self.cost = cost_model if cost_model is not None else get_baseline_model()
        self.data_cut_id = data_cut_id
        self.pit = pit_engine

    def run(
        self,
        factor_df: pd.DataFrame,
        start_date: str,
        end_date: str,
        rebal_freq: str = "W",
        top_n: int = 15,
        weight_scheme: str = "inv_vol",
        price_col: str = "close_adj",
    ) -> dict:
        """执行向量化回测。

        Args:
            factor_df:    DataFrame，列：ts_code / trade_date / factor_value
            start_date:   回测开始日期，如 '2022-01-03'
            end_date:     回测结束日期，如 '2023-12-29'
            rebal_freq:   调仓频率，'W'=周度 / 'M'=月度
            top_n:        每期选 Top N 股
            weight_scheme: 加权方式 'inv_vol' | 'equal'
            price_col:    价格列名，默认 'close_adj'

        Returns:
            {
                'nav_series': pd.Series,    # 净值曲线（index=调仓日期）
                'sharpe': float,
                'max_drawdown': float,
                'calmar': float,
                'annual_return': float,
                'cost_model_id': str,
                'ic_series': pd.Series,     # 截面 rank-IC 时间序列
                'ic_mean': float | None,
                'ic_std': float | None,
                'ic_ir': float | None,
            }

        异常处理：
          - 某日截面股票数 < top_n → 跳过该期（nav 维持上期值）
          - 因子值全为 NaN → 该期权重设为 0
        """
        rebal_dates = self._get_rebal_dates(start_date, end_date, rebal_freq)
        if len(rebal_dates) < 2:
            logger.warning("回测区间内调仓日期 < 2，无法回测。")
            return {}

        # 确保 factor_df 有 trade_date 列
        if factor_df.empty:
            logger.warning("factor_df 为空，跳过回测。")
            return {}

        nav = 1.0
        nav_series: dict[str, float] = {}
        ic_list: list[float] = []

        # 预先按 trade_date 分组，加速截面查找
        if "trade_date" in factor_df.columns:
            grouped = {
                str(k): v for k, v in factor_df.groupby("trade_date")
            }
        else:
            grouped = {}

        for i, rdate in enumerate(rebal_dates[:-1]):
            next_rdate = rebal_dates[i + 1]

            cross_section = grouped.get(rdate, pd.DataFrame())

            if cross_section.empty or cross_section["factor_value"].isna().all():
                nav_series[next_rdate] = nav
                continue

            valid_cs = cross_section.dropna(subset=["factor_value"])
            if len(valid_cs) < top_n:
                nav_series[next_rdate] = nav
                continue

            # 选 Top N
            top_stocks = valid_cs.nlargest(top_n, "factor_value")["ts_code"].tolist()

            # 获取持有期行情（若有 PIT 引擎则走 PIT；否则从 factor_df 附带的 close_adj 列）
            bars = self._fetch_bars(top_stocks, rdate, next_rdate, price_col)

            if bars.empty:
                nav_series[next_rdate] = nav
                continue

            # 计算持有期收益
            period_return = self._calc_period_return(bars, top_stocks, weight_scheme, price_col)

            # 成本估算（R6：Decimal 计算后转 float）
            # 向量化回测中用名义 amount=nav 估算双边成本占比
            try:
                avg_daily_turnover = float(bars["amount"].mean()) if "amount" in bars.columns else 0.0
                trade_size = nav / top_n
                cost_dec = self.cost.calc_transaction_cost(
                    Decimal(str(nav)),
                    "BUY",
                    Decimal(str(avg_daily_turnover)),
                    Decimal(str(trade_size)),
                )
                cost_pct = float(cost_dec) / nav
            except Exception:
                # 成本估算失败时使用 slippage_floor 下限
                cost_pct = float(self.cost.slippage_floor)

            # 双边成本（买入 + 卖出）
            nav *= 1 + period_return - cost_pct * 2
            nav_series[next_rdate] = nav

            # 计算截面 rank-IC（因子值排名 vs 下期收益排名）
            ic = self._calc_rank_ic(valid_cs, bars, rdate, next_rdate, price_col)
            if ic is not None:
                ic_list.append(ic)

        nav_s = pd.Series(nav_series)
        return self._compute_metrics(nav_s, ic_list, self.cost.model_id)

    def _fetch_bars(
        self,
        ts_codes: list[str],
        start_date: str,
        end_date: str,
        price_col: str,
    ) -> pd.DataFrame:
        """获取持仓期价格数据。

        若有 PIT 引擎则走 PIT 查询；否则返回空 DataFrame（由调用方跳过该期）。
        """
        if self.pit is None:
            return pd.DataFrame()
        try:
            as_of = f"{start_date}T17:00:00+08:00"
            bars = self.pit.daily_bar_asof(
                ts_codes=ts_codes,
                date_range=(start_date, end_date),
                as_of=as_of,
                data_cut_id=self.data_cut_id,
            )
            return bars if bars is not None else pd.DataFrame()
        except Exception as exc:
            logger.warning("PIT 查询失败，跳过该期：%s", exc)
            return pd.DataFrame()

    def _calc_period_return(
        self,
        bars: pd.DataFrame,
        ts_codes: list[str],
        weight_scheme: str,
        price_col: str,
    ) -> float:
        """计算持有期等权或波动率倒数加权收益。"""
        if weight_scheme == "inv_vol":
            vols: dict[str, float] = {}
            for ts_code in ts_codes:
                sub = bars[bars["ts_code"] == ts_code].sort_values("trade_date")
                if len(sub) > 1 and price_col in sub.columns:
                    daily_ret = sub[price_col].pct_change().dropna()
                    vols[ts_code] = float(daily_ret.std()) + 1e-8
                else:
                    vols[ts_code] = 1.0
            total_inv_vol = sum(1.0 / v for v in vols.values())
            if total_inv_vol == 0:
                weights = {ts: 1.0 / len(ts_codes) for ts in ts_codes}
            else:
                weights = {ts: (1.0 / v) / total_inv_vol for ts, v in vols.items()}
        else:
            weights = {ts: 1.0 / len(ts_codes) for ts in ts_codes}

        returns: dict[str, float] = {}
        for ts_code in ts_codes:
            sub = bars[bars["ts_code"] == ts_code].sort_values("trade_date")
            if len(sub) >= 2 and price_col in sub.columns:
                p0 = sub[price_col].iloc[0]
                p1 = sub[price_col].iloc[-1]
                if p0 and p0 != 0:
                    returns[ts_code] = float(p1 / p0) - 1.0
                else:
                    returns[ts_code] = 0.0
            else:
                returns[ts_code] = 0.0

        return sum(weights.get(ts, 0.0) * returns.get(ts, 0.0) for ts in ts_codes)

    def _calc_rank_ic(
        self,
        cross_section: pd.DataFrame,
        bars: pd.DataFrame,
        start_date: str,
        end_date: str,
        price_col: str,
    ) -> Optional[float]:
        """截面 rank-IC（因子值排名 vs 下期收益排名的 Spearman 相关）。"""
        try:
            # 计算持有期收益
            ret_list: list[dict] = []
            for ts_code, sub in bars.groupby("ts_code"):
                sub = sub.sort_values("trade_date")
                if len(sub) >= 2 and price_col in sub.columns:
                    p0 = sub[price_col].iloc[0]
                    p1 = sub[price_col].iloc[-1]
                    if p0 and p0 != 0:
                        ret_list.append({"ts_code": ts_code, "period_return": float(p1 / p0) - 1.0})

            if not ret_list:
                return None

            ret_df = pd.DataFrame(ret_list)
            merged = cross_section.merge(ret_df, on="ts_code")
            if len(merged) < 5:
                return None

            ic = float(
                merged["factor_value"].rank().corr(
                    merged["period_return"].rank(), method="spearman"
                )
            )
            return ic if not np.isnan(ic) else None
        except Exception:
            return None

    def _compute_metrics(
        self,
        nav: pd.Series,
        ic_list: list[float],
        cost_model_id: str,
    ) -> dict:
        """计算回测汇总指标。"""
        if len(nav) < 2:
            return {"cost_model_id": cost_model_id}

        daily_ret = nav.pct_change().dropna()
        annual_factor = 52.0  # 周频调仓用 52
        n_periods = len(daily_ret)
        if n_periods == 0:
            return {"cost_model_id": cost_model_id}

        # 年化收益（按持有周期数估算）
        total_return = float(nav.iloc[-1] / nav.iloc[0]) - 1.0
        years = n_periods / annual_factor
        annual_return = float((1 + total_return) ** (1.0 / years) - 1) if years > 0 else 0.0

        # 夏普（周频，年化到 sqrt(52)）
        std_ = float(daily_ret.std()) + 1e-10
        sharpe = float(daily_ret.mean()) / std_ * np.sqrt(annual_factor)

        # 最大回撤
        rolling_max = nav.cummax()
        drawdown = (nav - rolling_max) / rolling_max
        max_drawdown = float(drawdown.min())

        # Calmar
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        # IC 统计
        ic_s = pd.Series(ic_list)
        ic_mean = float(ic_s.mean()) if len(ic_list) > 0 else None
        ic_std_ = float(ic_s.std()) if len(ic_list) > 1 else None
        ic_ir = (float(ic_s.mean() / ic_s.std()) if (ic_std_ and ic_std_ > 0) else None)

        return {
            "nav_series": nav,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "calmar": calmar,
            "annual_return": annual_return,
            "cost_model_id": cost_model_id,
            "ic_series": ic_s,
            "ic_mean": ic_mean,
            "ic_std": ic_std_,
            "ic_ir": ic_ir,
        }

    @staticmethod
    def _get_rebal_dates(start_date: str, end_date: str, freq: str) -> list[str]:
        """按调仓频率生成调仓日期序列（含首尾）。"""
        idx = pd.date_range(start_date, end_date, freq=freq)
        dates = [d.strftime("%Y-%m-%d") for d in idx]
        # 确保 end_date 在序列中（用于计算末期收益）
        if dates and dates[-1] < end_date:
            dates.append(end_date)
        return dates
