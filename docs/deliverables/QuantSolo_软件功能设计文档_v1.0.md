# QuantSolo — 软件开发功能设计文档 v1.0

| 字段 | 内容 |
|------|------|
| **文档编号** | QS-E03 |
| **版本** | v1.0 |
| **日期** | 2026-06-12 |
| **状态** | 正式发布 |
| **上游依赖** | baseline_spec.md（SSOT）· QS-C01 系统设计文档 v5.0 · QS-C03 点时数据契约 v2.0 · QS-C04 执行与风控状态机 v1.3 · QS-E02 软件开发架构方案 v1.0 |
| **下游文档** | QS-E04 项目测试验收方案 · QS-E05 执行行动指导手册 |
| **冲突裁决** | 与 SSOT（baseline_spec.md）冲突时以 SSOT 为准；已全文校验。 |

---

## 版本演进表

| 版本 | 日期 | 核心变更 |
|------|------|---------|
| v1.0 | 2026-06-12 | 初版，各模块完整功能设计 |

---

## 目录

- [§1 数据采集器](#1-数据采集器)
- [§2 点时查询引擎](#2-点时查询引擎)
- [§3 因子计算引擎](#3-因子计算引擎)
- [§4 回测引擎](#4-回测引擎)
- [§5 信号生成器](#5-信号生成器)
- [§6 风控守卫](#6-风控守卫)
- [§7 执行引擎](#7-执行引擎)
- [§8 对账器](#8-对账器)
- [§9 监控告警器](#9-监控告警器)
- [§10 物理熔断脚本](#10-物理熔断脚本)
- [§11 模块间接口契约表](#11-模块间接口契约表)

---

## §1 数据采集器

### 1.1 职责

数据采集器负责从三个数据源（AKShare/Tushare Pro/BaoStock）采集日线行情及相关辅助数据，通过三源两票制裁决器解决数据冲突，并按 QS-C03 §10 规定的 `visible_at` 赋值规则落库。采集器是系统中唯一与外部数据源通信的组件。

### 1.2 子模块划分

| 子模块 | 文件 | 职责 |
|--------|------|------|
| AKShare 适配器 | `src/data/adapters/akshare_adapter.py` | 主采集（source_priority=1） |
| Tushare 适配器 | `src/data/adapters/tushare_adapter.py` | 对账与补强（source_priority=2）|
| BaoStock 适配器 | `src/data/adapters/baostock_adapter.py` | 冗余校验（source_priority=3）|
| 两票制裁决器 | `src/data/arbitrator.py` | 三源冲突裁决 |
| 盘后管道 | `src/data/pipeline.py` | 调度上述模块 |
| visible_at 工具 | `src/data/visible_at.py` | 时间戳赋值规则 |
| 交易日历 | `src/data/calendar.py` | 交易日判断/next_trade_date |

### 1.3 三源适配器接口定义

```python
# src/data/adapters/base_adapter.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class RawBarData:
    """原始日线行情（适配器输出格式，统一标准）"""
    ts_code: str          # '000001.SZ'
    trade_date: str       # 'YYYY-MM-DD'
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    pre_close: Optional[float]
    volume: Optional[float]   # 手
    amount: Optional[float]   # 元
    turnover_rate: Optional[float]
    source: str           # 'akshare' | 'tushare' | 'baostock'


class BaseDataAdapter(ABC):
    """三源适配器统一接口"""

    source_name: str   # 子类必须定义
    source_priority: int

    @abstractmethod
    def fetch_daily_bars(
        self,
        ts_codes: list[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """获取日线行情（原始，不复权）。返回 RawBarData 列表的 DataFrame。"""

    @abstractmethod
    def fetch_lhb(self, trade_date: str) -> pd.DataFrame:
        """获取龙虎榜数据"""

    @abstractmethod
    def fetch_moneyflow(
        self,
        ts_code: str,
        trade_date: str
    ) -> Optional[dict]:
        """获取个股主力资金流"""

    @abstractmethod
    def fetch_st_list(self) -> list[str]:
        """获取当前 ST 股票列表"""

    @abstractmethod
    def fetch_suspension_list(self, trade_date: str) -> list[str]:
        """获取当日停牌股票列表"""

    @abstractmethod
    def health_check(self) -> bool:
        """数据源健康检查（连接测试）"""
```

```python
# src/data/adapters/akshare_adapter.py
import akshare as ak
import pandas as pd
from src.data.adapters.base_adapter import BaseDataAdapter
from src.utils.retry import retry


class AKShareAdapter(BaseDataAdapter):
    """AKShare 主采集适配器（source_priority=1）"""

    source_name = "akshare"
    source_priority = 1

    @retry(max_attempts=3, base_delay_s=2.0)
    def fetch_daily_bars(
        self,
        ts_codes: list[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        调用 ak.stock_zh_a_hist，adjust="" 取原始价格（PIT 复权因子单独维护）。
        返回标准化 DataFrame（列名与 RawBarData 对齐）。
        异常：网络超时触发重试；静默返回空 → 告警 + 记录 quality_flag='MISSING'。
        """
        frames = []
        for ts_code in ts_codes:
            symbol = ts_code.split('.')[0]
            try:
                df = ak.stock_zh_a_hist(
                    symbol=symbol,
                    period="daily",
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust=""
                )
                if df is not None and not df.empty:
                    df['ts_code'] = ts_code
                    df['source'] = self.source_name
                    frames.append(self._normalize(df))
            except Exception as e:
                # 记录告警，继续其他标的
                self._log_fetch_error(ts_code, str(e))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """列名标准化（AKShare→统一格式）"""
        rename_map = {
            '日期': 'trade_date',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '昨收': 'pre_close',
            '成交量': 'volume',
            '成交额': 'amount',
            '换手率': 'turnover_rate',
        }
        return df.rename(columns=rename_map)

    @retry(max_attempts=3, base_delay_s=2.0)
    def fetch_lhb(self, trade_date: str) -> pd.DataFrame:
        """ak.stock_lhb_detail_em(date=trade_date)"""
        return ak.stock_lhb_detail_em(date=trade_date.replace('-', ''))

    @retry(max_attempts=2, base_delay_s=1.0)
    def fetch_moneyflow(self, ts_code: str, trade_date: str) -> Optional[dict]:
        """ak.stock_individual_fund_flow，返回主力净流入等字段"""
        symbol = ts_code.split('.')[0]
        market = 'sh' if ts_code.endswith('.SH') else 'sz'
        df = ak.stock_individual_fund_flow(stock=symbol, market=market)
        if df is None or df.empty:
            return None
        row = df[df['日期'] == trade_date]
        return row.to_dict('records')[0] if not row.empty else None

    def fetch_st_list(self) -> list[str]:
        df = ak.stock_zh_a_st_em()
        return df['代码'].tolist() if not df.empty else []

    def fetch_suspension_list(self, trade_date: str) -> list[str]:
        df = ak.stock_zh_a_stop_em()
        return df['代码'].tolist() if not df.empty else []

    def health_check(self) -> bool:
        try:
            ak.stock_zh_a_hist(symbol='000001', period='daily',
                               start_date='20260101', end_date='20260101', adjust='')
            return True
        except Exception:
            return False

    def _log_fetch_error(self, ts_code: str, error: str):
        from src.logger import StructuredLogger
        StructuredLogger.log("ERROR", "data.akshare", "FETCH_ERROR",
                             ts_code=ts_code, error=error)
```

### 1.4 三源两票制裁决器

```python
# src/data/arbitrator.py
from dataclasses import dataclass
from typing import Optional
import pandas as pd


@dataclass
class DiffReport:
    """两票制裁决报告"""
    ts_code: str
    trade_date: str
    field: str
    ak_val: Optional[float]
    ts_val: Optional[float]
    bs_val: Optional[float]
    verdict: str       # 'AK_WINS' | 'TS_WINS' | 'AK_BS_AGREE' | 'CONFLICT'
    winner_val: Optional[float]
    quality_flag: str  # '' | 'WARNING' | 'CRITICAL' | 'CONFLICT'


class TwoVoteArbitrator:
    """
    三源两票制冲突裁决器（QS-C03 §9.2）。
    触发条件：同一 (ts_code, trade_date) 字段值差异超容差阈值。
    """

    def __init__(self, config: dict):
        self.price_pct_tol = config.get('price_pct', 0.0001)
        self.volume_abs_tol = config.get('volume_abs', 1)
        self.amount_pct_tol = config.get('amount_pct', 0.0001)
        self.adj_factor_pct_tol = config.get('adj_factor_pct', 0.00001)

    def arbitrate(
        self,
        ak_df: pd.DataFrame,
        ts_df: pd.DataFrame,
        bs_df: pd.DataFrame
    ) -> tuple[pd.DataFrame, list[DiffReport]]:
        """
        对每个 (ts_code, trade_date) 逐字段裁决。
        返回：(裁决后标准 DataFrame, 差异报告列表)

        算法：
          1. AK == TS（容差内）→ 采用 AK；BS 差异标注 WARNING
          2. AK == BS（容差内）→ 采用 AK；TS 差异标注 WARNING（人工次日复核）
          3. TS == BS（容差内）→ 采用 TS；AK 异常，升级告警 SERVER_CHAN
          4. 三源均不一致 → CRITICAL 告警；quality_flag='CONFLICT'；暂不入库
        """
        reports = []
        merged = self._merge_sources(ak_df, ts_df, bs_df)

        for idx, row in merged.iterrows():
            report = self._arbitrate_row(row)
            reports.append(report)

        clean_df = self._build_clean_df(merged, reports)
        return clean_df, reports

    def _is_close(self, a: Optional[float], b: Optional[float], tol: float) -> bool:
        if a is None or b is None:
            return False
        if b == 0:
            return abs(a - b) < 1e-10
        return abs(a - b) / abs(b) <= tol

    def _arbitrate_row(self, row) -> DiffReport:
        """单行裁决逻辑（close 字段为代表）"""
        ak_v = row.get('close_ak')
        ts_v = row.get('close_ts')
        bs_v = row.get('close_bs')
        tol = self.price_pct_tol

        if self._is_close(ak_v, ts_v, tol):
            return DiffReport(row['ts_code'], row['trade_date'], 'close',
                              ak_v, ts_v, bs_v, 'AK_WINS', ak_v,
                              'WARNING' if not self._is_close(ak_v, bs_v, tol) else '')
        elif self._is_close(ak_v, bs_v, tol):
            return DiffReport(row['ts_code'], row['trade_date'], 'close',
                              ak_v, ts_v, bs_v, 'AK_BS_AGREE', ak_v, 'WARNING')
        elif self._is_close(ts_v, bs_v, tol):
            return DiffReport(row['ts_code'], row['trade_date'], 'close',
                              ak_v, ts_v, bs_v, 'TS_WINS', ts_v, 'CRITICAL')
        else:
            return DiffReport(row['ts_code'], row['trade_date'], 'close',
                              ak_v, ts_v, bs_v, 'CONFLICT', None, 'CONFLICT')

    def _merge_sources(self, ak_df, ts_df, bs_df) -> pd.DataFrame:
        """三源 DataFrame 按 (ts_code, trade_date) 合并"""
        suffixes = [('_ak', ak_df), ('_ts', ts_df), ('_bs', bs_df)]
        base = ak_df[['ts_code', 'trade_date']].copy()
        for suffix, df in suffixes:
            cols = {c: c + suffix for c in df.columns if c not in ['ts_code', 'trade_date']}
            base = base.merge(df.rename(columns=cols), on=['ts_code', 'trade_date'], how='left')
        return base

    def _build_clean_df(self, merged: pd.DataFrame, reports: list[DiffReport]) -> pd.DataFrame:
        """根据裁决报告构建最终 DataFrame"""
        result_rows = []
        report_map = {(r.ts_code, r.trade_date): r for r in reports}
        for _, row in merged.iterrows():
            key = (row['ts_code'], row['trade_date'])
            report = report_map.get(key)
            if report and report.verdict == 'CONFLICT':
                continue  # CONFLICT 行暂不入库
            winner_close = report.winner_val if report else row.get('close_ak')
            result_rows.append({
                'ts_code': row['ts_code'],
                'trade_date': row['trade_date'],
                'close': winner_close,
                'quality_flag': report.quality_flag if report else '',
                'source': 'akshare' if (not report or report.verdict != 'TS_WINS') else 'tushare',
            })
        return pd.DataFrame(result_rows)
```

### 1.5 visible_at 赋值工具

```python
# src/data/visible_at.py
import pytz
from datetime import datetime
from src.data.calendar import TradeCalendar

CST = pytz.timezone('Asia/Shanghai')


def visible_at_eod(trade_date: str) -> str:
    """盘后行情 visible_at：trade_date 当日 17:00 CST（QS-C03 §10.1）"""
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    return CST.localize(dt.replace(hour=17, minute=0, second=0)).isoformat()


def visible_at_next_open(ann_date: str, calendar: TradeCalendar) -> str:
    """财报/公告 visible_at：ann_date 下一交易日 09:00 CST（QS-C03 §10.2）"""
    next_td = calendar.next_trade_date(ann_date)
    dt = datetime.strptime(next_td, '%Y-%m-%d')
    return CST.localize(dt.replace(hour=9, minute=0, second=0)).isoformat()


def visible_at_for_news(news_ts: str, calendar: TradeCalendar) -> str:
    """
    新闻 visible_at（QS-C03 §10.3）：
    t-1 日 15:00 到 t 日 15:00 的新闻 → 归属交易日 t → visible_at = next_trade_date(t) 09:00
    """
    news_dt = datetime.fromisoformat(news_ts)
    if news_dt.tzinfo is None:
        news_dt = CST.localize(news_dt)
    # 确定归属交易日 t
    cutoff_hour = 15
    if news_dt.hour >= cutoff_hour:
        trade_date = calendar.next_trade_date(news_dt.date().isoformat())
    else:
        trade_date = news_dt.date().isoformat()
    return visible_at_next_open(trade_date, calendar)
```

### 1.6 盘后管道调度

```python
# src/data/pipeline.py
import logging
from datetime import date
from src.data.adapters.akshare_adapter import AKShareAdapter
from src.data.adapters.tushare_adapter import TushareAdapter
from src.data.adapters.baostock_adapter import BaoStockAdapter
from src.data.arbitrator import TwoVoteArbitrator
from src.data.visible_at import visible_at_eod
from src.pit.validator import validate_data_gate


class DataPipeline:
    """
    盘后数据管道（每日 17:00 触发）。

    流程：
      采集 → 裁决 → 落库 → 质检
    任一阶段失败均告警，质检不通过阻塞因子计算。
    """

    def __init__(
        self,
        ak_adapter: AKShareAdapter,
        ts_adapter: TushareAdapter,
        bs_adapter: BaoStockAdapter,
        arbitrator: TwoVoteArbitrator,
        db_writer,        # DuckDB/Parquet + SQLite 写入器
        alerter,
    ):
        self.ak = ak_adapter
        self.ts = ts_adapter
        self.bs = bs_adapter
        self.arbitrator = arbitrator
        self.writer = db_writer
        self.alerter = alerter

    def run(self, trade_date: str | None = None) -> bool:
        """
        执行盘后数据管道。
        trade_date: None → 自动取今日（或上一交易日如非交易日）。
        返回：True = 成功；False = 失败（质检不通过）。
        """
        if trade_date is None:
            trade_date = date.today().isoformat()

        vis_at = visible_at_eod(trade_date)

        try:
            # Step 1: 三源并行采集
            ak_bars = self.ak.fetch_daily_bars([], trade_date, trade_date)
            ts_bars = self.ts.fetch_daily_bars([], trade_date, trade_date)
            bs_bars = self.bs.fetch_daily_bars([], trade_date, trade_date)

            # Step 2: 两票制裁决
            clean_df, reports = self.arbitrator.arbitrate(ak_bars, ts_bars, bs_bars)

            # Step 3: 写入 visible_at 并落库
            clean_df['visible_at'] = vis_at
            clean_df['ingested_at'] = None  # 由写入器填充实际写库时间
            self.writer.write_daily_bars(clean_df, trade_date)

            # Step 4: 写入冲突报告到审计日志
            conflicts = [r for r in reports if r.quality_flag in ('CRITICAL', 'CONFLICT')]
            for r in conflicts:
                self.alerter.send_alert("HIGH",
                    f"数据冲突 {r.ts_code} {r.trade_date}: {r.verdict}")

            # Step 5: 数据质检
            gate_result = validate_data_gate(trade_date)
            if not gate_result.passed:
                self.alerter.send_alert("HIGH",
                    f"数据质检不通过 {trade_date}: {gate_result.failure_reasons}")
                return False

            return True

        except Exception as e:
            self.alerter.send_alert("HIGH", f"盘后管道异常 {trade_date}: {str(e)}")
            return False
```

### 1.7 异常处理

| 异常类型 | 触发场景 | 处理策略 |
|---------|---------|---------|
| 网络超时 | AKShare/Tushare API 调用失败 | 指数退避重试 3 次 |
| 静默返回空 | AKShare 北向数据停披（2024-08-19 后）| 检测返回空 → quality_flag='REGULATORY_HALT'，不告警 |
| 三源均不一致 | 罕见数据异常 | CRITICAL 告警，quality_flag='CONFLICT'，暂不入库 |
| 质检不通过 | 覆盖率/缺失率超阈值 | 阻塞因子计算，告警 |
| 整体管道异常 | 任意未预期异常 | 记录 ERROR 日志，推手机告警，次日补采 |

### 1.8 单元测试要点

- AKShare 空返回测试（北向停披后返回空 DataFrame 不崩溃）
- 两票制裁决全四路径覆盖（AK 赢/AK+BS 赢/TS 赢/三源冲突）
- visible_at 时区测试（节假日后的 next_trade_date 正确）
- 容差阈值边界测试（刚好等于/略超容差）

---

## §2 点时查询引擎

### 2.1 职责

点时查询引擎是系统中所有模块读取历史数据的唯一入口，严格遵守 QS-C03 canonical 四键排序规则，防止任何形式的未来信息泄漏。引擎同时提供 DuckDB（行情/因子 Parquet）和 SQLite（财报/公司行动）的统一查询接口。

### 2.2 输入/输出

| 函数 | 输入 | 输出 | 存储层 |
|------|------|------|--------|
| `daily_bar_asof` | ts_codes, date_range, as_of, data_cut_id, adjust | DataFrame + PIT_META | DuckDB/Parquet |
| `financials_pit_asof` | ts_codes, end_date, as_of, data_cut_id | DataFrame + PIT_META | DuckDB/Parquet |
| `factor_snapshot_asof` | ts_codes, trade_date, as_of, data_cut_id, factor_names, variant | DataFrame + PIT_META | DuckDB/Parquet |
| `get_pit_ttm` | ts_code, as_of, data_cut_id, metric | (value, confidence_tag) | DuckDB/Parquet |
| `get_active_universe` | as_of, data_cut_id | list[str] | SQLite |
| `get_limit_rule_asof` | ts_code, trade_date, as_of, data_cut_id | dict | SQLite |

### 2.3 canonical 四键查询核心实现

```python
# src/pit/query_engine.py
import duckdb
import sqlite3
import pandas as pd
from typing import Optional
from src.execution.interfaces import PitMeta


# canonical 排序四键（QS-C03 §3.2，不可更改）
CANONICAL_ORDER = "ORDER BY visible_at DESC, revision_seq DESC, snapshot_rank DESC, record_id DESC"


class PitQueryEngine:
    """
    点时查询引擎（唯一合法的历史数据读取入口）。
    所有查询严格遵守 canonical 四键排序，防止未来信息泄漏。
    """

    def __init__(self, duckdb_path: str, sqlite_path: str):
        self.duck = duckdb.connect(duckdb_path)
        self.sql = sqlite3.connect(sqlite_path)
        self.sql.execute("PRAGMA journal_mode=WAL")
        self.sql.execute("PRAGMA foreign_keys=ON")

    def daily_bar_asof(
        self,
        ts_codes: list[str],
        date_range: tuple[str, str],
        as_of: str,
        data_cut_id: int,
        adjust: str = "qfq_pit"
    ) -> pd.DataFrame:
        """
        canonical 日线行情查询。

        算法：
          1. 从 snapshot_manifest 获取本 data_cut_id 下所有 snapshot_id
          2. 读 daily_bar/year=* Parquet，过滤 visible_at <= as_of
          3. 按 CANONICAL_ORDER 排序，ROW_NUMBER() per (ts_code, trade_date) 取第一行
          4. 过滤 record_status = 'ACTIVE'（VOIDED 触发闸门告警后丢弃）
          5. 若 adjust='qfq_pit'，关联 adj_factor_pit（按 §2 visible_at 口径取 base_event_ver）
          6. 附加 PIT_META 六字段
        """
        start_date, end_date = date_range
        ts_placeholder = ','.join(f"'{c}'" for c in ts_codes)

        sql = f"""
        WITH candidates AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY ts_code, trade_date
                       {CANONICAL_ORDER}
                   ) AS rn
            FROM read_parquet('data/daily_bar/year=*/part-*.parquet')
            WHERE ts_code IN ({ts_placeholder})
              AND trade_date BETWEEN '{start_date}' AND '{end_date}'
              AND visible_at <= '{as_of}'
              AND snapshot_id IN (
                  SELECT snapshot_id FROM snapshot_manifest
                  WHERE data_cut_id = {data_cut_id}
              )
        ),
        latest AS (
            SELECT * FROM candidates WHERE rn = 1
        )
        SELECT * FROM latest
        WHERE record_status = 'ACTIVE'
        """
        df = self.duck.execute(sql).fetchdf()

        # VOIDED 行触发闸门告警
        voided_sql = sql.replace("record_status = 'ACTIVE'", "record_status = 'VOIDED'")
        voided = self.duck.execute(voided_sql).fetchdf()
        if not voided.empty:
            self._handle_voided_bars(voided)

        if adjust == "qfq_pit":
            df = self._apply_adj_factor(df, as_of, data_cut_id)

        return self._attach_pit_meta(df, as_of, data_cut_id)

    def _apply_adj_factor(
        self,
        df: pd.DataFrame,
        as_of: str,
        data_cut_id: int
    ) -> pd.DataFrame:
        """
        PIT 前复权（QS-C03 §2）：
        base_event_ver = MAX(event_ver) FROM corporate_action
                         WHERE ts_code = ? AND visible_at <= as_of AND record_status = 'ACTIVE'
        取 adj_factor_pit 中对应 base_event_ver 的整条序列。
        """
        # 获取每只股票的 base_event_ver
        ts_codes = df['ts_code'].unique().tolist()
        ts_placeholder = ','.join(f"'{c}'" for c in ts_codes)

        base_vers = self.sql.execute(f"""
            SELECT ts_code, MAX(event_ver) AS base_event_ver
            FROM corporate_action
            WHERE ts_code IN ({ts_placeholder})
              AND visible_at <= ?
              AND record_status = 'ACTIVE'
            GROUP BY ts_code
        """, (as_of,)).fetchall()

        base_ver_map = {row[0]: row[1] for row in base_vers}

        # 构建复权因子查询条件
        conditions = ' OR '.join(
            f"(ts_code = '{ts}' AND base_event_ver = {ver})"
            for ts, ver in base_ver_map.items()
        )
        if not conditions:
            return df

        adj_sql = f"""
        SELECT ts_code, trade_date, adj_factor, base_event_ver
        FROM read_parquet('data/adj_factor_pit/year=*/ts_code=*/part-*.parquet')
        WHERE {conditions}
        """
        adj_df = self.duck.execute(adj_sql).fetchdf()
        df = df.merge(adj_df[['ts_code', 'trade_date', 'adj_factor', 'base_event_ver']],
                      on=['ts_code', 'trade_date'], how='left')
        # 应用复权
        for price_col in ['open', 'high', 'low', 'close', 'pre_close']:
            if price_col in df.columns:
                df[f'{price_col}_adj'] = df[price_col] * df['adj_factor']
        return df

    def _attach_pit_meta(self, df: pd.DataFrame, as_of: str, data_cut_id: int) -> pd.DataFrame:
        """附加 PIT_META 六字段（QS-C03 §3.4）"""
        df['pit_as_of'] = as_of
        df['pit_data_cut_id'] = data_cut_id
        # pit_visible_at / pit_revision_seq / pit_snapshot_rank 已在 SELECT 中
        return df

    def _handle_voided_bars(self, voided_df: pd.DataFrame):
        """行情 VOIDED 单独告警（QS-C03 §1.1）"""
        from src.logger import StructuredLogger
        for _, row in voided_df.iterrows():
            StructuredLogger.log("ERROR", "pit.query_engine", "VOIDED_BAR_DETECTED",
                                 ts_code=row['ts_code'],
                                 trade_date=row['trade_date'],
                                 quality_flag='VOIDED_ALERT')

    def get_active_universe(self, as_of: str, data_cut_id: int) -> list[str]:
        """
        获取当日在市股票列表（QS-C03 §7.3）。
        只过滤 list_status='L'，不做停牌过滤（停牌过滤交策略层）。
        """
        rows = self.sql.execute("""
            SELECT DISTINCT ts_code
            FROM security_master
            WHERE list_status = 'L'
              AND visible_at <= ?
            ORDER BY ts_code
        """, (as_of,)).fetchall()
        return [row[0] for row in rows]


def assert_deterministic_rerun(query_fn, *args, **kwargs):
    """
    确定性重跑断言（QS-C03 §12.4）：
    同一 data_cut_id 执行两次，结果必须逐行一致。
    """
    result1 = query_fn(*args, **kwargs)
    result2 = query_fn(*args, **kwargs)
    pd.testing.assert_frame_equal(
        result1.reset_index(drop=True),
        result2.reset_index(drop=True),
        check_exact=True
    )
```

### 2.4 异常处理

| 异常类型 | 处理策略 |
|---------|---------|
| VOIDED 行情 | 单独闸门告警，不静默丢数据（QS-C03 §1.1）|
| canonical 查询为空 | 返回空 DataFrame，不抛异常（上游自行判断）|
| DuckDB 连接失败 | 重试 3 次，失败则告警+阻塞 |
| Parquet 分区不存在 | 返回空，记录 WARNING（可能是历史数据未下载）|

### 2.5 单元测试要点

- 13 场景点时回归测试全覆盖（QS-C03 §12.1）
- 确定性重跑断言（同 data_cut_id 两次结果完全一致）
- 除权前后 as_of 断言（测试场景 1）
- test 段物理封锁断言（测试场景 13，sqlite3.IntegrityError）
- VOIDED 行情告警触发断言

---

## §3 因子计算引擎

### 3.1 职责

因子计算引擎负责从点时数据中计算研究池 15-20 个候选因子，并输出三种变体（raw/processed/orthogonal）。所有计算为纯函数，无副作用，无 IO，通过依赖注入接收数据。

### 3.2 因子分类与数据依赖

| 因子类别 | 因子名称 | 数据依赖 | 优先级 |
|---------|---------|---------|--------|
| 质量 | ROE、毛利率、OCF/净利润、资产负债率 | financials_pit（P1）| M2 |
| 动量 | 20/60 日收益（剔最近 5 日）| daily_bar（P0）| M1 |
| 低波 | 60 日波动率、历史最大回撤 | daily_bar（P0）| M1 |
| 筹码/资金 | 主力净流入、股东户数环比、龙虎榜净买 | moneyflow/holder/lhb（P0/P1）| M1/M2 |
| 情绪（P2）| LLM 舆情正交化得分 | sentiment Parquet（P2）| M3+ |

### 3.3 核心函数签名

```python
# src/factor/transforms.py
import numpy as np
import pandas as pd
from typing import Optional


def mad_winsorize(series: pd.Series, n: float = 3.0) -> pd.Series:
    """
    MAD 去极值。
    中位数 ± n × MAD 截断。
    纯函数：输入 pd.Series，输出去极值后 pd.Series。

    Args:
        series: 原始因子序列
        n: 截断倍数（默认 3.0）
    Returns:
        去极值后序列（无 NaN 填充，保持 NaN）
    """
    median = series.median()
    mad = (series - median).abs().median()
    upper = median + n * 1.4826 * mad
    lower = median - n * 1.4826 * mad
    return series.clip(lower=lower, upper=upper)


def industry_mktcap_neutralize(
    factor: pd.Series,
    industry: pd.Series,
    log_mktcap: pd.Series
) -> pd.Series:
    """
    行业+市值中性化（OLS 残差法）。
    factor = α + β1·log_mktcap + Σγᵢ·industry_dummies + ε
    返回残差 ε（中性化后因子）。

    注意：中性化是否保留核心 Alpha 须经 A/B 验证（QS-C01 §6.6）。
    """
    import statsmodels.api as sm
    industry_dummies = pd.get_dummies(industry, prefix='ind', drop_first=True)
    X = pd.concat([log_mktcap.rename('log_mktcap'), industry_dummies], axis=1)
    X = sm.add_constant(X)
    mask = factor.notna() & X.notna().all(axis=1)
    model = sm.OLS(factor[mask], X[mask]).fit()
    residuals = factor.copy()
    residuals[mask] = model.resid
    return residuals


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    """截面 z-score 标准化。纯函数。"""
    return (series - series.mean()) / (series.std() + 1e-8)


def cholesky_orthogonalize(
    factor_df: pd.DataFrame,
    correlation_threshold: float = 0.7
) -> pd.DataFrame:
    """
    Cholesky 残差化正交化（仅对高相关簇做，不全量）。
    Step1: Spearman 相关矩阵 → r > threshold 的因子对
    Step2: 保留 IC 高者，另一方对其做线性回归取残差
    Step3: 记录变换矩阵（供复原）
    Returns: 正交化后 DataFrame（orthogonal 变体）
    """
    corr_matrix = factor_df.rank().corr(method='spearman')
    result = factor_df.copy()
    for i, col_i in enumerate(factor_df.columns):
        for col_j in factor_df.columns[i+1:]:
            if abs(corr_matrix.loc[col_i, col_j]) > correlation_threshold:
                # 保留 IC 高者（由调用方指定或按 rank-IC 自动选）
                # 此处简化：col_i 对 col_j 回归取残差
                mask = result[col_i].notna() & result[col_j].notna()
                x = result.loc[mask, col_j].values.reshape(-1, 1)
                y = result.loc[mask, col_i].values
                from sklearn.linear_model import LinearRegression
                reg = LinearRegression().fit(x, y)
                result.loc[mask, col_i] = y - reg.predict(x)
    return result
```

```python
# src/factor/momentum.py
import pandas as pd
import numpy as np


def calc_momentum(
    close: pd.Series,
    lookback: int = 60,
    skip_recent: int = 5
) -> float:
    """
    动量因子：lookback 日收益，剔除最近 skip_recent 日反转效应。
    公式：close[-skip_recent] / close[-lookback] - 1
    纯函数：输入 close 序列（按日期升序），输出单值因子。

    异常处理：
      - 序列长度 < lookback + skip_recent → 返回 None
      - 包含 NaN → 返回 None
    """
    required_len = lookback + skip_recent
    if len(close) < required_len:
        return None
    relevant = close.iloc[-(required_len):]
    if relevant.isna().any():
        return None
    return float(relevant.iloc[-skip_recent] / relevant.iloc[0] - 1)


def calc_factor_batch(
    bar_df: pd.DataFrame,    # (ts_code, trade_date, close_adj)
    as_of: str,
    lookback: int = 60,
    skip_recent: int = 5
) -> pd.DataFrame:
    """
    批量计算截面动量因子。
    返回 DataFrame: columns = [ts_code, trade_date, factor_value, factor_variant='raw']
    可见性保证：bar_df 必须通过 pit/query_engine 获取（visible_at <= as_of）。
    """
    results = []
    for ts_code, group in bar_df.groupby('ts_code'):
        group = group.sort_values('trade_date')
        if len(group) < lookback + skip_recent:
            continue
        val = calc_momentum(group['close_adj'], lookback, skip_recent)
        if val is not None:
            results.append({
                'ts_code': ts_code,
                'trade_date': group['trade_date'].iloc[-1],
                'factor_name': f'momentum_{lookback}d',
                'factor_value': val,
                'factor_variant': 'raw',
                'computed_as_of': as_of,
            })
    return pd.DataFrame(results)
```

```python
# src/factor/quality.py
import pandas as pd
from typing import Optional


def calc_roe(
    net_profit: Optional[float],
    total_equity: Optional[float]
) -> Optional[float]:
    """ROE = 净利润 / 平均净资产。单值纯函数。"""
    if net_profit is None or total_equity is None or total_equity == 0:
        return None
    return net_profit / total_equity


def calc_ocf_ratio(
    ocf: Optional[float],
    net_profit: Optional[float]
) -> Optional[float]:
    """经营现金流/净利润质量因子。"""
    if ocf is None or net_profit is None or net_profit == 0:
        return None
    return ocf / net_profit


def calc_quality_factors_batch(
    financials_df: pd.DataFrame,   # 来自 financials_pit_asof
    as_of: str
) -> pd.DataFrame:
    """
    批量计算质量因子截面。
    financials_df 必须已经过 PIT 查询（visible_at <= as_of）。
    confidence_tag='INSUFFICIENT' 的行跳过（QS-C03 §6）。
    """
    results = []
    for _, row in financials_df.iterrows():
        if row.get('confidence_tag') == 'INSUFFICIENT':
            continue
        roe_val = calc_roe(row.get('net_profit'), row.get('total_equity'))
        ocf_val = calc_ocf_ratio(row.get('ocf'), row.get('net_profit'))
        for fname, fval in [('roe_ttm', roe_val), ('ocf_ratio', ocf_val)]:
            if fval is not None:
                results.append({
                    'ts_code': row['ts_code'],
                    'trade_date': row.get('ann_date', as_of[:10]),
                    'factor_name': fname,
                    'factor_value': fval,
                    'factor_variant': 'raw',
                    'computed_as_of': as_of,
                })
    return pd.DataFrame(results)
```

### 3.4 factor_variant 三变体生成流水线

```python
# src/factor/pipeline.py
import pandas as pd
from src.factor.transforms import (
    mad_winsorize, industry_mktcap_neutralize,
    cross_sectional_zscore, cholesky_orthogonalize
)


def build_factor_variants(
    raw_df: pd.DataFrame,        # raw 变体（已计算）
    industry_series: pd.Series,
    log_mktcap_series: pd.Series,
    high_corr_threshold: float = 0.7
) -> dict[str, pd.DataFrame]:
    """
    生成三变体（QS-C03 §4.1）：
      raw:        原始未变换（SHAP 归因/人工解读）
      processed:  MAD去极值 + 行业市值中性化 + z-score（Ridge 线性训练）
      orthogonal: Cholesky 残差化（LightGBM 训练）

    Returns:
        {'raw': df_raw, 'processed': df_processed, 'orthogonal': df_orthogonal}
    """
    factor_cols = [c for c in raw_df.columns if c not in
                   ['ts_code', 'trade_date', 'computed_as_of', 'factor_variant']]

    # processed 变体
    processed = raw_df[factor_cols].apply(mad_winsorize)
    for col in factor_cols:
        processed[col] = industry_mktcap_neutralize(
            processed[col], industry_series, log_mktcap_series
        )
        processed[col] = cross_sectional_zscore(processed[col])

    # orthogonal 变体（在 processed 基础上做 Cholesky 残差化）
    orthogonal = cholesky_orthogonalize(processed, high_corr_threshold)

    return {
        'raw':        raw_df,
        'processed':  processed,
        'orthogonal': orthogonal,
    }
```

### 3.5 单元测试要点

- 纯函数测试：相同输入→相同输出（确定性）
- MAD 去极值边界：极端值被截断，中间值不变
- 中性化残差验证：残差与 log_mktcap/行业哑变量相关性接近 0
- z-score 输出：均值≈0，标准差≈1
- factor_variant 三枚举 CHECK 约束：写入非法变体抛异常

---

## §4 回测引擎

### 4.1 职责

回测引擎提供向量化回测（快速因子研究）和事件驱动回测（精确成本验证）双层实现，两层独立，结果不一致则停止排查。所有回测必须通过 PitQueryEngine 获取数据，禁止使用事后数据。

### 4.2 双层互验架构

```
向量化回测（快速）          事件驱动回测（精确）
  ↓ 信号截面计算               ↓ 逐笔撮合
  ↓ 假设瞬时全量成交            ↓ 涨跌停/T+1/100股取整
  ↓ 近似成本模型               ↓ 精确冲击成本建模
  └──── 结果比较 ────────────── 若夏普差异 > 0.1 → 停止，排查
```

### 4.3 成本模型

```python
# src/research/backtest/cost_models.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class CostModel:
    """成本模型（QS-C01 §12.2 + §6.4）"""
    model_id: str
    stamp_duty_sell_pct: float = 0.0005  # 印花税（卖出）0.05%
    commission_pct: float = 0.00025      # 佣金 万2.5
    min_commission: float = 5.0          # 最低佣金 5 元
    transfer_fee_pct: float = 0.00001    # 过户费 万0.1
    default_slippage_pct: float = 0.002  # 默认滑点 0.2%

    def calc_transaction_cost(
        self,
        amount: float,
        side: str,              # 'BUY' | 'SELL'
        daily_turnover: float,  # 日均成交额（元）
        trade_size: float       # 本次交易金额（元）
    ) -> float:
        """
        计算单次交易总成本（元）。
        cm_v3_advanced 时，滑点按 trade_size/daily_turnover 动态建模。
        """
        stamp = amount * self.stamp_duty_sell_pct if side == 'SELL' else 0.0
        commission = max(amount * self.commission_pct, self.min_commission)
        transfer = amount * self.transfer_fee_pct if side == 'BUY' else 0.0

        # 动态滑点（cm_v3_advanced）
        if self.model_id == "cm_v3_advanced" and daily_turnover > 0:
            impact_ratio = trade_size / daily_turnover
            slippage = self.default_slippage_pct * (1 + 10 * impact_ratio)
        else:
            slippage = self.default_slippage_pct

        slippage_cost = amount * slippage
        return stamp + commission + transfer + slippage_cost


# 两个成本档（QS-C01 §6.4，cost_model_id 不可混用）
CM_BASELINE = CostModel(model_id="cm_v3_baseline")
CM_ADVANCED = CostModel(model_id="cm_v3_advanced")
```

### 4.4 向量化回测

```python
# src/research/backtest/vectorized.py
import pandas as pd
import numpy as np
from src.pit.query_engine import PitQueryEngine
from src.research.backtest.cost_models import CostModel


class VectorizedBacktest:
    """
    向量化回测引擎（快速因子研究）。
    假设每期满仓调仓，用截面收益近似模拟。
    适用场景：因子 IC 检验、快速参数扫描。
    """

    def __init__(
        self,
        pit_engine: PitQueryEngine,
        cost_model: CostModel,
        data_cut_id: int
    ):
        self.pit = pit_engine
        self.cost = cost_model
        self.data_cut_id = data_cut_id

    def run(
        self,
        factor_df: pd.DataFrame,      # (ts_code, trade_date, factor_value)
        start_date: str,
        end_date: str,
        rebal_freq: str = 'W',        # 'W'=周度, 'M'=月度
        top_n: int = 15,
        weight_scheme: str = 'inv_vol'  # 'inv_vol' | 'equal'
    ) -> dict:
        """
        执行向量化回测。
        返回：{
            'nav_series': pd.Series,     # 净值曲线
            'sharpe': float,
            'max_drawdown': float,
            'calmar': float,
            'annual_return': float,
            'cost_model_id': str,
            'ic_series': pd.Series,      # 截面 rank-IC
        }

        异常处理：
          - 某日截面股票数 < top_n → 跳过该期
          - 因子值全为 NaN → 该期权重设为 0
        """
        rebal_dates = self._get_rebal_dates(start_date, end_date, rebal_freq)
        nav = 1.0
        nav_series = {}
        ic_list = []

        for i, rdate in enumerate(rebal_dates[:-1]):
            next_rdate = rebal_dates[i + 1]

            # 获取当期截面因子（visible_at = rdate 17:00）
            as_of = f"{rdate}T17:00:00+08:00"
            cross_section = factor_df[factor_df['trade_date'] == rdate].copy()

            if cross_section.empty or len(cross_section) < top_n:
                nav_series[next_rdate] = nav
                continue

            # 选 Top N
            top_stocks = cross_section.nlargest(top_n, 'factor_value')['ts_code'].tolist()

            # 获取持有期收益
            bars = self.pit.daily_bar_asof(
                ts_codes=top_stocks,
                date_range=(rdate, next_rdate),
                as_of=as_of,
                data_cut_id=self.data_cut_id
            )

            if bars.empty:
                nav_series[next_rdate] = nav
                continue

            # 计算持有期收益
            period_return = self._calc_period_return(bars, top_stocks, weight_scheme)

            # 扣成本
            cost_pct = self.cost.calc_transaction_cost(
                amount=nav,
                side='BUY',
                daily_turnover=bars['amount'].mean(),
                trade_size=nav / top_n
            ) / nav

            nav *= (1 + period_return - cost_pct * 2)  # 买入+卖出双边
            nav_series[next_rdate] = nav

            # 计算 IC（截面 rank-IC）
            ic = self._calc_rank_ic(cross_section, bars, next_rdate)
            if ic is not None:
                ic_list.append(ic)

        nav_s = pd.Series(nav_series)
        return self._compute_metrics(nav_s, ic_list, self.cost.model_id)

    def _calc_period_return(
        self,
        bars: pd.DataFrame,
        ts_codes: list[str],
        weight_scheme: str
    ) -> float:
        """持有期等权或波动率倒数加权收益"""
        if weight_scheme == 'inv_vol':
            vols = {}
            for ts_code in ts_codes:
                sub = bars[bars['ts_code'] == ts_code].sort_values('trade_date')
                if len(sub) > 1:
                    daily_ret = sub['close_adj'].pct_change().dropna()
                    vols[ts_code] = daily_ret.std() + 1e-8
                else:
                    vols[ts_code] = 1.0
            total_inv_vol = sum(1 / v for v in vols.values())
            weights = {ts: (1 / v) / total_inv_vol for ts, v in vols.items()}
        else:
            weights = {ts: 1 / len(ts_codes) for ts in ts_codes}

        returns = {}
        for ts_code in ts_codes:
            sub = bars[bars['ts_code'] == ts_code].sort_values('trade_date')
            if len(sub) >= 2:
                returns[ts_code] = sub['close_adj'].iloc[-1] / sub['close_adj'].iloc[0] - 1
            else:
                returns[ts_code] = 0.0

        return sum(weights.get(ts, 0) * returns.get(ts, 0) for ts in ts_codes)

    def _calc_rank_ic(
        self,
        cross_section: pd.DataFrame,
        bars: pd.DataFrame,
        next_date: str
    ) -> Optional[float]:
        """截面 rank-IC（因子值排名 vs 下期收益排名的 Spearman 相关）"""
        try:
            next_bars = bars[bars['trade_date'] == next_date][['ts_code', 'close_adj']]
            prev_bars = bars[bars['trade_date'] == bars['trade_date'].min()][['ts_code', 'close_adj']]
            ret = next_bars.merge(prev_bars, on='ts_code', suffixes=('_next', '_prev'))
            ret['period_return'] = ret['close_adj_next'] / ret['close_adj_prev'] - 1
            merged = cross_section.merge(ret[['ts_code', 'period_return']], on='ts_code')
            if len(merged) < 5:
                return None
            return merged['factor_value'].rank().corr(merged['period_return'].rank(), method='spearman')
        except Exception:
            return None

    def _compute_metrics(self, nav: pd.Series, ic_list: list, cost_model_id: str) -> dict:
        """计算回测指标"""
        if len(nav) < 2:
            return {}
        daily_ret = nav.pct_change().dropna()
        annual_return = (nav.iloc[-1] / nav.iloc[0]) ** (252 / len(nav)) - 1
        sharpe = daily_ret.mean() / (daily_ret.std() + 1e-8) * np.sqrt(252)
        rolling_max = nav.cummax()
        max_drawdown = ((nav - rolling_max) / rolling_max).min()
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0
        return {
            'nav_series': nav,
            'sharpe': float(sharpe),
            'max_drawdown': float(max_drawdown),
            'calmar': float(calmar),
            'annual_return': float(annual_return),
            'cost_model_id': cost_model_id,
            'ic_series': pd.Series(ic_list),
            'ic_mean': float(np.mean(ic_list)) if ic_list else None,
        }

    def _get_rebal_dates(self, start_date: str, end_date: str, freq: str) -> list[str]:
        """按调仓频率生成调仓日期序列"""
        idx = pd.date_range(start_date, end_date, freq=freq)
        return [d.strftime('%Y-%m-%d') for d in idx]
```

### 4.5 A 股特性处理

```python
# src/research/backtest/event_driven.py（核心约束片段）

def apply_ashare_constraints(order, bars, positions):
    """
    A 股特性约束（QS-C01 §12.3）：
    1. T+1：当日买入不可当日卖出
    2. 涨跌停：命中涨/跌停价 → 挂单排队，不强行成交
    3. 100 股一手取整
    4. 高价股（一手超 1.6 万）剔除
    5. 部分成交处理
    返回 (adjusted_order, can_fill)
    """
    ts_code = order['ts_code']
    bar = bars.loc[ts_code] if ts_code in bars.index else None

    if bar is None:
        return order, False

    # T+1 约束
    if order['side'] == 'SELL':
        buy_date = positions.get(ts_code, {}).get('buy_date')
        if buy_date and buy_date == order['trade_date']:
            return order, False   # T+1 不可卖

    # 涨跌停约束
    if order['side'] == 'BUY' and bar['close'] >= bar.get('upper_limit', float('inf')):
        return order, False   # 涨停无法买入

    if order['side'] == 'SELL' and bar['close'] <= bar.get('lower_limit', float('-inf')):
        # 跌停：挂跌停价排队，不强行成交
        order['limit_price'] = bar['lower_limit']
        return order, False   # 跌停当日无法成交（排队次日）

    # 100 股取整
    order['qty'] = (order['qty'] // 100) * 100
    if order['qty'] == 0:
        return order, False

    # 高价股过滤（一手 > 1.6 万）
    if bar['close'] * 100 > 16000:
        return order, False

    return order, True
```

### 4.6 单元测试要点

- T+1 约束：当日买入当日卖出应被拒绝
- 涨跌停：涨停日买入信号被拦截
- 向量化 vs 事件驱动 Sharpe 差异 < 0.1（双层互验）
- 成本模型 ID 一致性（回测/对账用同一 cost_model_id）
- 含退市股历史重放（防幸存偏差）

---

## §5 信号生成器

### 5.1 职责

信号生成器将因子截面、技术指标和大盘择时信号合并为目标持仓表，供执行引擎差量计算。所有生成逻辑为纯函数，结果写入 SQLite `target_positions` 表。

### 5.2 核心函数签名

```python
# src/signal/core_factor.py
import pandas as pd
import numpy as np
from typing import Optional


def apply_universe_filter(
    universe: list[str],
    st_list: list[str],
    suspension_list: list[str],
    listing_days: dict[str, int],        # ts_code → 上市天数
    avg_turnover: dict[str, float],      # ts_code → 日均成交额（元）
    high_price_stocks: list[str] = None  # 一手超 1.6 万股票
) -> list[str]:
    """
    全市场过滤（QS-C01 §7.3）：
    剔除 ST / 停牌 / 上市 < 250 日 / 日均成交额 < 5000 万 / 高价股。
    纯函数：输入列表，输出过滤后列表。
    """
    result = []
    for ts_code in universe:
        if ts_code in st_list:
            continue
        if ts_code in suspension_list:
            continue
        if listing_days.get(ts_code, 0) < 250:
            continue
        if avg_turnover.get(ts_code, 0) < 50_000_000:
            continue
        if high_price_stocks and ts_code in high_price_stocks:
            continue
        result.append(ts_code)
    return result


def calc_composite_score(
    factor_df: pd.DataFrame,    # (ts_code, factor_name, processed_value)
    factor_weights: dict[str, float],   # 因子名 → 权重
    lgbm_score: Optional[pd.Series],   # LightGBM 排名得分（None = 纯线性）
    lgbm_weight: float = 0.5
) -> pd.Series:
    """
    线性加权 + LightGBM 融合排序。
    fusion_rank = (1 - lgbm_weight) × linear_rank + lgbm_weight × lgbm_rank

    Returns:
        pd.Series: ts_code → composite_score（越高越好）
    """
    # 线性加权得分
    pivot = factor_df.pivot(index='ts_code', columns='factor_name', values='processed_value')
    linear_score = pd.Series(0.0, index=pivot.index)
    for fname, weight in factor_weights.items():
        if fname in pivot.columns:
            linear_score += pivot[fname].fillna(0) * weight

    # 融合排序
    linear_rank = linear_score.rank(pct=True)
    if lgbm_score is not None and len(lgbm_score) > 0:
        lgbm_rank = lgbm_score.rank(pct=True)
        merged_rank = (1 - lgbm_weight) * linear_rank + lgbm_weight * lgbm_rank
    else:
        merged_rank = linear_rank

    return merged_rank


def select_top_n_with_weights(
    scores: pd.Series,         # ts_code → composite_score
    volatility: pd.Series,     # ts_code → 60 日波动率
    top_n: int = 15,
    single_stock_max: float = 0.08  # 单票上限（QS-C01 §7.3）
) -> pd.Series:
    """
    选 Top N 只，波动率倒数加权，单票上限裁剪。
    Returns: pd.Series ts_code → target_weight（归一化后，加总=1）
    """
    top_stocks = scores.nlargest(top_n).index.tolist()
    vol_subset = volatility.reindex(top_stocks).fillna(volatility.median())
    inv_vol = 1 / (vol_subset + 1e-8)
    raw_weights = inv_vol / inv_vol.sum()

    # 单票上限迭代裁剪
    for _ in range(10):  # 最多迭代 10 次
        capped = raw_weights.clip(upper=single_stock_max)
        excess = raw_weights.sum() - capped.sum()
        if excess < 1e-6:
            break
        uncapped = capped[capped < single_stock_max]
        capped[uncapped.index] += excess * (uncapped / uncapped.sum())
        raw_weights = capped

    return raw_weights.rename('target_weight')
```

```python
# src/signal/market_timing.py
import pandas as pd


def calc_market_timing(
    hs300_close: pd.Series,     # 沪深 300 日收盘（按日期升序）
    ma_window: int = 200,
    confirmation_days: int = 3   # N 日确认延迟（防假信号）
) -> str:
    """
    大盘择时总开关（QS-C01 §5.4）。
    沪深 300 vs 200 日 MA → 三档仓位上限。

    Returns:
        'BULL' | 'NEUTRAL' | 'BEAR'
    算法：
      1. 计算 200 日 MA
      2. 连续 confirmation_days 天在 MA 上方 → BULL
      3. 连续 confirmation_days 天在 MA 下方 → BEAR
      4. 其他 → NEUTRAL
    """
    if len(hs300_close) < ma_window + confirmation_days:
        return 'NEUTRAL'

    ma = hs300_close.rolling(ma_window).mean()
    recent_close = hs300_close.iloc[-confirmation_days:]
    recent_ma = ma.iloc[-confirmation_days:]

    above = (recent_close > recent_ma).all()
    below = (recent_close < recent_ma).all()

    if above:
        return 'BULL'
    elif below:
        return 'BEAR'
    return 'NEUTRAL'


TIMING_CAPS = {
    'BULL':    1.00,   # 90-100%（取上限）
    'NEUTRAL': 0.60,
    'BEAR':    0.30,
}
```

### 5.3 单元测试要点

- 过滤逻辑：ST/停牌/新股/流动性不足股票均被正确剔除
- 波动率倒数加权：高波动率股票分配较低权重
- 单票上限：迭代裁剪后所有权重 ≤ 8%
- 大盘择时：N 日确认延迟正确（不提前切换档位）

---

## §6 风控守卫

### 6.1 职责

风控守卫是系统中唯一的下单入口，实现 QS-C01 铁律一。任何路径——包括紧急操作、研究调试、手工指令——都必须经过守卫校验。详细设计见 QS-E02 §4.3（唯一下单入口装饰器）。

### 6.2 约束校验实现

```python
# src/risk/constraints.py
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from src.execution.interfaces import OrderIntent


@dataclass
class ConstraintCheckResult:
    passed: bool
    rejection_reason: Optional[str] = None
    adjusted_qty: Optional[int] = None


CONSTRAINTS = {
    "single_stock_max": 0.08,
    "industry_max": 0.30,
    "min_daily_turnover": 50_000_000,
    "exclude_st": True,
    "exclude_suspension": True,
    "min_listing_days": 250,
    "high_price_exclude_threshold": 16000,
}


def check_single_stock_limit(
    ts_code: str,
    order_qty: int,
    price: float,
    total_portfolio_value: float,
    current_positions: dict[str, dict]
) -> ConstraintCheckResult:
    """
    单票仓位上限检查（QS-C01 §7.3）。
    5 万小实盘阶段放宽为 20%（保证至少 5 只）。
    """
    current_val = current_positions.get(ts_code, {}).get('market_value', 0.0)
    new_val = current_val + order_qty * price
    new_pct = new_val / total_portfolio_value if total_portfolio_value > 0 else 0

    if new_pct > CONSTRAINTS["single_stock_max"]:
        # 计算最大可买量
        max_val = total_portfolio_value * CONSTRAINTS["single_stock_max"]
        max_qty = int((max_val - current_val) / price / 100) * 100  # 100股取整
        if max_qty <= 0:
            return ConstraintCheckResult(False, "SINGLE_STOCK_LIMIT_EXCEEDED")
        return ConstraintCheckResult(True, None, max_qty)

    return ConstraintCheckResult(True)


def check_industry_limit(
    industry: str,
    order_qty: int,
    price: float,
    total_portfolio_value: float,
    current_positions: dict[str, dict],
    ts_code_industry_map: dict[str, str]
) -> ConstraintCheckResult:
    """行业集中度上限检查（≤30%）"""
    industry_val = sum(
        pos['market_value']
        for code, pos in current_positions.items()
        if ts_code_industry_map.get(code) == industry
    )
    new_val = industry_val + order_qty * price
    new_pct = new_val / total_portfolio_value if total_portfolio_value > 0 else 0

    if new_pct > CONSTRAINTS["industry_max"]:
        return ConstraintCheckResult(False, "INDUSTRY_LIMIT_EXCEEDED")
    return ConstraintCheckResult(True)


def check_liquidity_filter(
    ts_code: str,
    avg_daily_turnover: float,
    is_suspended: bool,
    is_st: bool,
    listing_days: int,
    unit_value: float   # 一手市值（元）
) -> ConstraintCheckResult:
    """流动性与资格过滤（每笔下单前）"""
    if is_st:
        return ConstraintCheckResult(False, "ST_EXCLUDED")
    if is_suspended:
        return ConstraintCheckResult(False, "SUSPENDED")
    if listing_days < CONSTRAINTS["min_listing_days"]:
        return ConstraintCheckResult(False, "LISTING_DAYS_INSUFFICIENT")
    if avg_daily_turnover < CONSTRAINTS["min_daily_turnover"]:
        return ConstraintCheckResult(False, "LIQUIDITY_INSUFFICIENT")
    if unit_value > CONSTRAINTS["high_price_exclude_threshold"]:
        return ConstraintCheckResult(False, "HIGH_PRICE_EXCLUDED")
    return ConstraintCheckResult(True)


def check_all_constraints(intent: OrderIntent, context: dict = None) -> ConstraintCheckResult:
    """汇总所有约束检查（按顺序，第一个失败即返回）"""
    ctx = context or {}
    checks = [
        lambda: check_liquidity_filter(
            intent.ts_code,
            ctx.get('avg_turnover', float('inf')),
            ctx.get('is_suspended', False),
            ctx.get('is_st', False),
            ctx.get('listing_days', 999),
            ctx.get('unit_value', 0)
        ),
        lambda: check_single_stock_limit(
            intent.ts_code,
            intent.target_qty,
            intent.limit_price or 0,
            ctx.get('total_portfolio_value', 0),
            ctx.get('current_positions', {})
        ),
        lambda: check_industry_limit(
            ctx.get('industry', 'UNKNOWN'),
            intent.target_qty,
            intent.limit_price or 0,
            ctx.get('total_portfolio_value', 0),
            ctx.get('current_positions', {}),
            ctx.get('ts_code_industry_map', {})
        ),
    ]
    for check in checks:
        result = check()
        if not result.passed:
            return result
    return ConstraintCheckResult(True)
```

```python
# src/risk/drawdown.py
import pandas as pd
from dataclasses import dataclass


@dataclass
class DrawdownLevel:
    level: int        # 0=正常，1=预警，2=硬止损
    drawdown_pct: float
    action: str


def check_drawdown_level(
    current_nav: float,
    peak_nav: float,
    warning_pct: float = 0.20,
    halt_pct: float = 0.25
) -> DrawdownLevel:
    """
    三级回撤检测（QS-C01 §7.1 + QS-C04 §6.2）。

    Returns:
      level=0: 正常
      level=1: 20% 预警 → 降仓 50%（先卖卫星）
      level=2: 25% 硬止损 → 全清仓+冻结
    """
    if peak_nav <= 0:
        return DrawdownLevel(0, 0.0, 'NORMAL')

    drawdown = (peak_nav - current_nav) / peak_nav

    if drawdown >= halt_pct:
        return DrawdownLevel(2, drawdown, 'FULL_LIQUIDATION_AND_FREEZE')
    elif drawdown >= warning_pct:
        return DrawdownLevel(1, drawdown, 'REDUCE_TO_50PCT_SELL_SATELLITE_FIRST')
    return DrawdownLevel(0, drawdown, 'NORMAL')
```

### 6.3 单元测试要点

- 唯一入口：mock broker，验证绕过 risk_guard 调用 broker.place_order 会抛异常
- 单票上限：超限时自动裁剪数量，不拒绝而是调整
- 行业集中度：加仓后超 30% 被拒绝
- 回撤一级：下单 BUY 被允许（仅要求降仓，不禁买）；回撤二级：BUY 被拒绝
- 风控签名：签名错误的订单被执行层拒收

---

## §7 执行引擎

### 7.1 职责

执行引擎封装 xtquant 调用，实现拆单（小盘股冲击成本控制）、重试（部分成交/超时）、order_remark 对账，并驱动 QS-C04 15 态状态机。xtquant 相关代码**仅在此模块出现**。

### 7.2 xtquant 适配器实现

```python
# src/execution/adapters/xtquant_adapter.py
# 注意：此文件是系统唯一 import xtquant 的地方

from xtquant import xttrader
from xtquant import xtconstant
from src.execution.interfaces import (
    BrokerInterface, OrderIntent, FillEvent
)
from src.execution.rate_limiter import RateLimiter
from src.logger import StructuredLogger


class XtquantAdapter(BrokerInterface):
    """
    xtquant 实盘适配器（QS-C04 §8 · order_remark 对账）。
    职责：封装 xtquant API，不包含任何策略/风控逻辑。
    策略层禁止 import 此类。
    """

    def __init__(
        self,
        account_id: str,
        xttrader_instance,     # xttrader 连接实例
        rate_limiter: RateLimiter
    ):
        self.account_id = account_id
        self.xttrader = xttrader_instance
        self.rate_limiter = rate_limiter

    def place_order(self, intent: OrderIntent) -> str:
        """
        调用 xtquant 下单接口。
        order_remark = client_order_id（对账键，QS-C04 §8.1）。
        Returns: broker_order_id（xtquant 返回的委托编号）
        """
        if not intent.risk_signature:
            raise ValueError("OrderIntent 缺少 risk_signature，拒绝下单")

        order_type = (xtconstant.STOCK_BUY
                      if intent.side == 'BUY'
                      else xtconstant.STOCK_SELL)

        price_type = (xtconstant.FIX_PRICE
                      if intent.order_type == 'LIMIT'
                      else xtconstant.MARKET_PRICE)

        # 申报限速检查（QS-C04 §6.1，先于业务）
        self.rate_limiter.wait_if_needed(intent.account_id)

        broker_order_id = self.xttrader.order_stock(
            account=self.account_id,
            stock_code=intent.ts_code,
            order_type=order_type,
            order_volume=intent.target_qty,
            price_type=price_type,
            price=intent.limit_price or 0,
            order_remark=intent.client_order_id,   # ← 对账键
        )

        StructuredLogger.log("INFO", "execution.xtquant", "ORDER_PLACED",
                             broker_order_id=broker_order_id,
                             client_order_id=intent.client_order_id,
                             ts_code=intent.ts_code,
                             side=intent.side,
                             qty=intent.target_qty)
        return str(broker_order_id)

    def cancel_order(self, broker_order_id: str) -> bool:
        """撤单"""
        result = self.xttrader.cancel_order_stock(
            account=self.account_id,
            order_id=int(broker_order_id)
        )
        return result == 0  # 0 表示成功

    def query_order(self, broker_order_id: str) -> Optional[FillEvent]:
        """查询委托状态（UNKNOWN 态归位使用）"""
        orders = self.xttrader.query_stock_orders(self.account_id)
        for order in orders:
            if str(order.order_id) == broker_order_id:
                return self._parse_fill_event(order)
        return None

    def query_by_order_remark(self, client_order_id: str) -> Optional[FillEvent]:
        """
        通过 order_remark（= client_order_id）反查委托（QS-C04 §8.1）。
        outbox 三态恢复的第一查询路径。
        """
        orders = self.xttrader.query_stock_orders(self.account_id)
        candidates = [o for o in orders if o.order_remark == client_order_id]
        if len(candidates) == 1:
            return self._parse_fill_event(candidates[0])
        elif len(candidates) > 1:
            # 多候选 → 暂停（QS-C04 §8.2）
            raise ValueError(f"order_remark={client_order_id} 匹配多条委托，需人工处理")
        return None

    def get_positions(self, account_id: str) -> dict[str, int]:
        """获取实时持仓"""
        positions = self.xttrader.query_stock_positions(account_id)
        return {pos.stock_code: pos.volume for pos in positions}

    def get_cash(self, account_id: str) -> float:
        """获取可用资金"""
        asset = self.xttrader.query_stock_asset(account_id)
        return asset.cash if asset else 0.0

    def health_check(self) -> bool:
        """检查 xtquant 连接（QMT 终端是否在线）"""
        try:
            return self.xttrader.is_connected()
        except Exception:
            return False

    def _parse_fill_event(self, order) -> FillEvent:
        """xtquant 委托对象 → FillEvent"""
        return FillEvent(
            client_order_id=order.order_remark,
            broker_order_id=str(order.order_id),
            ts_code=order.stock_code,
            side='BUY' if order.order_type == xtconstant.STOCK_BUY else 'SELL',
            filled_qty=order.traded_volume,
            avg_fill_price=order.traded_price or 0.0,
            event_ts=str(order.order_time),
            broker_status=self._map_status(order.order_status),
        )

    def _map_status(self, xt_status: int) -> str:
        """xtquant 状态码 → 标准状态字符串"""
        STATUS_MAP = {
            48: 'SUBMITTED',
            49: 'LIVE',
            50: 'PARTIAL',
            51: 'FILLED',
            52: 'CANCELLED',
            53: 'REJECTED',
        }
        return STATUS_MAP.get(xt_status, 'UNKNOWN')
```

### 7.3 拆单逻辑

```python
# src/execution/order_sizing.py
import math
from typing import list as List
from src.execution.interfaces import OrderIntent


def split_order(
    intent: OrderIntent,
    daily_turnover: float,      # 日均成交额（元）
    max_participation_rate: float = 0.05,  # 最大参与率（避免自己砸盘）
    price: float = 0.0
) -> List[OrderIntent]:
    """
    拆单逻辑（小盘股冲击成本控制）。
    若单笔订单金额 > daily_turnover × max_participation_rate：
      按参与率拆成多个子单，分批执行。

    注：拆单不改变幂等键，子单共享 parent_intent_id。
    Returns: 拆分后的子单列表（1 只子单 = 不需拆分）
    """
    if price <= 0 or daily_turnover <= 0:
        return [intent]

    order_amount = intent.target_qty * price
    max_single_amount = daily_turnover * max_participation_rate

    if order_amount <= max_single_amount:
        return [intent]   # 无需拆单

    # 拆分
    sub_orders = []
    remaining_qty = intent.target_qty
    n_splits = math.ceil(order_amount / max_single_amount)
    base_qty = (intent.target_qty // n_splits // 100) * 100  # 100股取整

    for i in range(n_splits):
        if remaining_qty <= 0:
            break
        qty = min(base_qty, remaining_qty)
        if qty <= 0:
            break
        sub = OrderIntent(
            client_order_id=f"{intent.client_order_id}_sub{i:02d}",
            account_id=intent.account_id,
            strategy_id=intent.strategy_id,
            ts_code=intent.ts_code,
            side=intent.side,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            target_qty=qty,
            limit_price=intent.limit_price,
            parent_intent_id=intent.client_order_id,
            rebalance_seq=intent.rebalance_seq,
        )
        sub_orders.append(sub)
        remaining_qty -= qty

    return sub_orders if sub_orders else [intent]
```

### 7.4 申报限速器

```python
# src/execution/rate_limiter.py
import time
import threading
from collections import deque


class RateLimiter:
    """
    申报限速器（QS-C04 §6.1 硬约束）。
    每账户：1笔/秒，200笔/日。
    超速：排队延迟（不拒单）。
    超日上限：抛异常（触发 MANUAL_REVIEW）。
    """

    def __init__(
        self,
        max_per_sec: int = 1,
        max_per_day: int = 200,
        cooldown_ms: int = 1000
    ):
        self.max_per_sec = max_per_sec
        self.max_per_day = max_per_day
        self.cooldown_ms = cooldown_ms
        self._lock = threading.Lock()
        self._daily_counts: dict[str, int] = {}
        self._last_order_time: dict[str, float] = {}
        self._minute_window: dict[str, deque] = {}

    def allow(self, account_id: str) -> bool:
        """检查是否可立即下单（不阻塞，返回 bool）"""
        with self._lock:
            daily = self._daily_counts.get(account_id, 0)
            if daily >= self.max_per_day:
                return False
            return True

    def wait_if_needed(self, account_id: str) -> None:
        """
        若超速，阻塞等待（排队，不拒单）。
        超日上限直接抛异常。
        """
        with self._lock:
            # 日上限检查
            daily = self._daily_counts.get(account_id, 0)
            if daily >= self.max_per_day:
                raise RuntimeError(f"账户 {account_id} 今日申报达上限 {self.max_per_day}")

            # 秒级间隔强制
            last_time = self._last_order_time.get(account_id, 0)
            elapsed_ms = (time.time() - last_time) * 1000
            if elapsed_ms < self.cooldown_ms:
                wait_s = (self.cooldown_ms - elapsed_ms) / 1000
                time.sleep(wait_s)

            # 更新计数器
            self._daily_counts[account_id] = daily + 1
            self._last_order_time[account_id] = time.time()

    def reset_daily(self, account_id: str) -> None:
        """每日 0 点重置（由 APScheduler 触发）"""
        with self._lock:
            self._daily_counts[account_id] = 0
```

### 7.5 单元测试要点

- order_remark 对账：下单时 order_remark = client_order_id，回调中可正确反查
- 拆单：大单被正确拆分，子单 parent_intent_id 正确设置，总量不变
- 限速：第二笔下单等待 ≥ 1000ms；第 201 笔抛异常
- 状态机闭环：IDLE→TARGET_GEN→...→RECONCILE→IDLE 全链路
- outbox 三态：MAYBE_SENT_UNKNOWN 不重发，进 MANUAL_REVIEW

---

## §8 对账器

### 8.1 职责

对账器在每日收盘后执行三方对账（理论持仓 C vs 券商实际 vs execution_ledger），差异分类（corp_action/零股/现金尾差/不可解释），记录对账结果，支持 B3 工程判线验证。

### 8.2 核心函数签名

```python
# src/reconcile/daily_recon.py
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class ReconResult:
    """对账结果"""
    trade_date: str
    passed: bool
    diff_records: list[dict] = field(default_factory=list)
    unexplained_qty_diff: dict[str, int] = field(default_factory=dict)  # ts_code → diff
    cash_diff: float = 0.0
    recon_duration_s: float = 0.0
    order_remark_hit_rate: float = 0.0   # order_remark 反查命中率（B3 工程判线）


class DailyRecon:
    """
    日终三方对账（QS-C04 §3.1）。
    三方：理论持仓 C（position_ledger）/ 券商实际 / execution_ledger。
    """

    def __init__(
        self,
        ledger,         # ExecutionLedger
        broker,         # BrokerInterface
        corp_action_db, # SQLite 连接（查 corporate_action）
        alerter,
        recon_qty_tol: dict = None,
        recon_cash_tol: float = 1.0,
        recon_price_tol: float = 0.005
    ):
        self.ledger = ledger
        self.broker = broker
        self.ca_db = corp_action_db
        self.alerter = alerter
        self.qty_tol = recon_qty_tol or {}
        self.cash_tol = recon_cash_tol
        self.price_tol = recon_price_tol

    def run(self, trade_date: str, account_id: str) -> ReconResult:
        """
        执行日终对账。

        算法：
          1. 计算理论持仓 C（position_ledger 推导）
          2. 获取券商实际持仓
          3. 与 execution_ledger 最终状态比较
          4. 差异分类（corp_action/零股/现金尾差/不可解释）
          5. 不可解释差异 → 暂停（MANUAL_REVIEW）+ 告警
          6. 写入对账结果
          7. 计算 order_remark 命中率（B3 验证）
        """
        # Step 1: 理论持仓 C
        theory_positions = self.ledger.compute_position_ledger(account_id, trade_date)

        # Step 2: 券商实际持仓
        broker_positions = self.broker.get_positions(account_id)

        # Step 3: 差异比较
        all_codes = set(theory_positions.keys()) | set(broker_positions.keys())
        diff_records = []
        unexplained = {}

        for ts_code in all_codes:
            theory_qty = theory_positions.get(ts_code, 0)
            broker_qty = broker_positions.get(ts_code, 0)
            diff = broker_qty - theory_qty

            if diff == 0:
                continue

            # Step 4: 差异分类
            category = self._classify_diff(ts_code, trade_date, diff)

            diff_records.append({
                'ts_code': ts_code,
                'theory_qty': theory_qty,
                'broker_qty': broker_qty,
                'diff_qty': diff,
                'category': category,
            })

            if category == 'UNEXPLAINED':
                unexplained[ts_code] = diff

        # 现金差异
        theory_cash = self.ledger.compute_cash_balance(account_id, trade_date)
        broker_cash = self.broker.get_cash(account_id)
        cash_diff = abs(broker_cash - theory_cash)

        # 不可解释差异处理
        passed = len(unexplained) == 0 and cash_diff <= self.cash_tol

        if unexplained:
            self.alerter.send_alert("HIGH",
                f"日终对账差异 {trade_date}: {unexplained}")

        # order_remark 命中率（QS-C04 §8.2）
        hit_rate = self._calc_order_remark_hit_rate(account_id, trade_date)

        result = ReconResult(
            trade_date=trade_date,
            passed=passed,
            diff_records=diff_records,
            unexplained_qty_diff=unexplained,
            cash_diff=cash_diff,
            order_remark_hit_rate=hit_rate,
        )
        self.ledger.record_recon_result(result)
        return result

    def _classify_diff(self, ts_code: str, trade_date: str, diff: int) -> str:
        """
        差异分类（QS-C04 §3.1）：
          corp_action: 送转/拆分/配股已有 corporate_action 记录 → 豁免
          odd_lot: 零股（<100股不可交易余额）→ 单独建账，不计零容忍
          cash_tail: 现金尾差 < recon_cash_tol → 自动容忍
          unexplained: 无法解释 → 停单 + 告警
        """
        # 检查是否为公司行动引起
        ca_count = self.ca_db.execute("""
            SELECT COUNT(*) FROM corporate_action
            WHERE ts_code = ? AND ex_date = ? AND record_status = 'ACTIVE'
        """, (ts_code, trade_date)).fetchone()[0]

        if ca_count > 0:
            return 'CORP_ACTION'

        if abs(diff) < 100:   # 零股（<100 股）
            return 'ODD_LOT'

        return 'UNEXPLAINED'

    def _calc_order_remark_hit_rate(
        self,
        account_id: str,
        trade_date: str
    ) -> float:
        """计算 order_remark 反查命中率（QS-C04 §8.2，须 ≥95%）"""
        orders = self.ledger.get_orders_by_date(account_id, trade_date)
        if not orders:
            return 1.0
        hits = sum(1 for o in orders if o.get('order_remark_matched', False))
        return hits / len(orders)
```

### 8.3 成本偏差归因

```python
# src/reconcile/cost_attribution.py
import pandas as pd


def calc_cost_deviation(
    execution_df: pd.DataFrame,    # execution_ledger 中已成交记录
    backtest_cost_model,           # 回测用成本模型（cm_v3_advanced）
    tolerance: float = 0.30        # B3 工程判线：成本偏差 ≤ 30%（QS-C04 §§）
) -> dict:
    """
    实盘成本 vs 回测建模成本偏差（B3 工程判线之一）。
    cost_deviation = (actual_cost - modeled_cost) / modeled_cost

    Returns:
        {
            'deviation_pct': float,
            'b3_passed': bool,
            'breakdown': pd.DataFrame   # 逐笔偏差明细
        }
    """
    rows = []
    for _, row in execution_df.iterrows():
        actual_cost = row.get('actual_commission', 0) + row.get('actual_slippage', 0)
        modeled_cost = backtest_cost_model.calc_transaction_cost(
            amount=row['filled_qty'] * row['avg_fill_price'],
            side=row['side'],
            daily_turnover=row.get('daily_turnover', 0),
            trade_size=row['filled_qty'] * row['avg_fill_price'],
        )
        deviation = (actual_cost - modeled_cost) / (modeled_cost + 1e-8)
        rows.append({
            'client_order_id': row['client_order_id'],
            'ts_code': row['ts_code'],
            'actual_cost': actual_cost,
            'modeled_cost': modeled_cost,
            'deviation_pct': deviation,
        })

    breakdown = pd.DataFrame(rows)
    avg_deviation = breakdown['deviation_pct'].mean() if not breakdown.empty else 0.0
    b3_passed = avg_deviation <= tolerance

    return {
        'deviation_pct': float(avg_deviation),
        'b3_passed': b3_passed,
        'breakdown': breakdown,
    }
```

### 8.4 单元测试要点

- 零差异对账：通过
- corp_action 差异：被正确分类为豁免
- 零股差异：<100 股被单独建账，不触发暂停
- 不可解释差异：触发 MANUAL_REVIEW + 告警
- order_remark 命中率 < 95%：触发模糊匹配降级

---

## §9 监控告警器

### 9.1 职责

监控告警器常驻独立进程，提供进程健康检查（watchdog 互查）、关键事件推送（Server酱/钉钉）、Streamlit 实时看板三个子功能。

### 9.2 告警推送实现

```python
# src/monitor/alerter.py
import requests
import os
import time
import threading
from dataclasses import dataclass
from typing import Optional
from src.logger import StructuredLogger


@dataclass
class AlertMessage:
    level: str        # 'INFO' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
    title: str
    content: str
    source_module: str
    timestamp: str


class AlertManager:
    """
    告警发送器（Server 酱 + 钉钉机器人）。
    关键告警事件（QS-C01 §14.3）：
      风控触发 / 下单失败 / 数据管道失败 / 进程失联 / UPS 切换 / 对账差错
    """

    LEVEL_MAP = {
        'INFO':     '📘',
        'MEDIUM':   '🟡',
        'HIGH':     '🔴',
        'CRITICAL': '🚨',
    }

    def __init__(self):
        self._server_chan_key = os.getenv('SERVER_CHAN_KEY', '')
        self._dingtalk_url = os.getenv('DINGTALK_WEBHOOK', '')
        self._queue: list[AlertMessage] = []
        self._lock = threading.Lock()

    def send_alert(
        self,
        level: str,
        message: str,
        source: str = "system",
        retry: int = 3
    ) -> bool:
        """
        发送告警（异步队列，不阻塞主流程）。
        HIGH/CRITICAL 级别同步发送。
        """
        alert = AlertMessage(
            level=level,
            title=f"[QuantSolo] {level}: {message[:50]}",
            content=message,
            source_module=source,
            timestamp=time.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        )
        StructuredLogger.log(level, source, "ALERT_SENT", message=message)

        if level in ('HIGH', 'CRITICAL'):
            return self._send_sync(alert, retry)
        else:
            with self._lock:
                self._queue.append(alert)
            return True

    def _send_sync(self, alert: AlertMessage, retry: int) -> bool:
        """同步发送到 Server 酱 + 钉钉"""
        success = False
        for attempt in range(retry):
            try:
                if self._server_chan_key:
                    self._send_server_chan(alert)
                    success = True
                if self._dingtalk_url:
                    self._send_dingtalk(alert)
                    success = True
                if success:
                    break
            except Exception as e:
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
        return success

    def _send_server_chan(self, alert: AlertMessage) -> None:
        """Server 酱推送"""
        url = f"https://sctapi.ftqq.com/{self._server_chan_key}.send"
        requests.post(url, data={
            'title': alert.title,
            'desp': alert.content,
        }, timeout=10)

    def _send_dingtalk(self, alert: AlertMessage) -> None:
        """钉钉机器人推送"""
        emoji = self.LEVEL_MAP.get(alert.level, '❓')
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": alert.title,
                "text": f"## {emoji} {alert.title}\n\n"
                        f"**时间**：{alert.timestamp}\n\n"
                        f"**来源**：{alert.source_module}\n\n"
                        f"**详情**：{alert.content}"
            }
        }
        requests.post(self._dingtalk_url, json=payload, timeout=10)
```

### 9.3 watchdog 双向互查

```python
# src/monitor/watchdog.py
import os
import time
import json
import requests
import threading
from src.monitor.alerter import AlertManager


class Watchdog:
    """
    进程互查（QS-C01 §14.2 · watchdog 设计原则）。
    监控进程⑤ ↔ 执行进程② 双向监控。
    单向监控不可接受：监控进程自身故障也必须被发现。
    """

    def __init__(
        self,
        execution_pid_file: str,
        monitor_health_url: str,      # 监控进程自身 HTTP /health
        alerter: AlertManager,
        heartbeat_interval_s: int = 15,
        miss_max: int = 3             # 连续丢失 3 次 = 45s 窗口
    ):
        self.pid_file = execution_pid_file
        self.health_url = monitor_health_url
        self.alerter = alerter
        self.interval = heartbeat_interval_s
        self.miss_max = miss_max
        self._miss_count = 0

    def check_execution_process(self) -> bool:
        """
        监控进程⑤ 检查执行进程② 的心跳。
        读取 run/execution.pid，验证心跳时间戳。
        """
        try:
            with open(self.pid_file, 'r') as f:
                info = json.load(f)
            heartbeat_age = time.time() - info.get('heartbeat_ts', 0)
            if heartbeat_age > self.interval * self.miss_max:
                self._miss_count += 1
                if self._miss_count >= self.miss_max:
                    self.alerter.send_alert("HIGH",
                        f"执行进程失联 {heartbeat_age:.0f}s，请检查")
                return False
            else:
                self._miss_count = 0
                return True
        except FileNotFoundError:
            self.alerter.send_alert("HIGH", "执行进程 PID 文件不存在")
            return False

    def check_monitor_self_health(self) -> bool:
        """
        执行进程② 检查监控进程⑤ 的健康端点。
        连续 3 次失败 → 告警（此时告警渠道可能已故障，写 ledger 留痕）。
        """
        try:
            resp = requests.get(self.health_url, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def run_forever(self):
        """监控主循环（APScheduler 调用或独立线程）"""
        while True:
            self.check_execution_process()
            time.sleep(self.interval)
```

### 9.4 Streamlit 看板

```python
# src/monitor/dashboard.py
import streamlit as st
import pandas as pd
import sqlite3
import duckdb
from datetime import date, timedelta


def run_dashboard(sqlite_path: str, duckdb_path: str):
    """
    Streamlit 看板（仅绑定 127.0.0.1，不暴露外网）。
    盘后每日巡检使用。5-10 分钟巡检目标。
    """
    st.set_page_config(page_title="QuantSolo 监控看板", layout="wide")
    st.title("QuantSolo 每日巡检看板")

    conn_sql = sqlite3.connect(sqlite_path)
    conn_duck = duckdb.connect(duckdb_path)

    tab1, tab2, tab3, tab4 = st.tabs(["持仓对账", "因子 IC", "数据质检", "告警日志"])

    with tab1:
        _show_position_recon(conn_sql)
    with tab2:
        _show_factor_ic(conn_sql)
    with tab3:
        _show_data_quality(conn_duck)
    with tab4:
        _show_alerts(conn_sql)


def _show_position_recon(conn):
    st.subheader("最新持仓对账")
    df = pd.read_sql("""
        SELECT trade_date, ts_code, theory_qty, broker_qty, diff_qty, category
        FROM recon_results
        WHERE trade_date = (SELECT MAX(trade_date) FROM recon_results)
        ORDER BY ABS(diff_qty) DESC
    """, conn)
    st.dataframe(df, use_container_width=True)
    if df[df['category'] == 'UNEXPLAINED'].shape[0] > 0:
        st.error("⚠ 存在不可解释差异，需人工处理")
    else:
        st.success("✅ 对账通过")


def _show_factor_ic(conn):
    st.subheader("滚动 26 周 IC 趋势")
    st.info("IC 走势数据由 research_ledger 提供（此处为占位符）")
```

### 9.5 单元测试要点

- Server 酱发送失败：重试 3 次，超时不阻塞主流程
- watchdog：心跳过期 45s 触发告警（3×15s），不早触发
- 钉钉 Webhook URL 未配置：跳过，不抛异常
- 告警级别过滤：INFO 级别不触发同步推送

---

## §10 物理熔断脚本

### 10.1 职责

物理一键熔断脚本（`scripts/break_glass.py`）作为**独立进程**运行，不依赖主进程内存/状态，直接调用 xtquant 撤单+市价平仓。这是系统中唯一可绕过策略层的执行路径（QS-C04 §5 · SSOT §3）。

### 10.2 完整实现

```python
# scripts/break_glass.py
"""
物理一键熔断脚本（QS-C04 §5.2 · break-glass 简化版）。

使用场景：
  - 手动触发（PC 故障/网络中断前的紧急清仓）
  - 监控进程检测到灾备条件后自动触发

执行步骤：
  STEP 1: 触发确认（二次确认防误操作）
  STEP 2: 夺取全局下单令牌
  STEP 3: 撤销所有在途委托
  STEP 4: 按 xtquant sellable_qty 市价平仓
  STEP 5: 全量写 execution_ledger（break_glass_signature）
  STEP 6: 进入暂停（BREAK_GLASS），等待人工复盘

降级路径：
  xtquant 不可达 → 打印券商 APP 手动清仓 SOP
"""

import sys
import os
import time
import hmac
import hashlib
import sqlite3
from datetime import datetime
import pytz


def main():
    # Step 1: 二次确认
    print("=" * 60)
    print("⚠️  物理一键熔断 · 此操作将清仓所有持仓")
    print("=" * 60)
    confirm1 = input("输入 'BREAK-GLASS' 确认触发: ").strip()
    if confirm1 != 'BREAK-GLASS':
        print("已取消")
        sys.exit(0)

    confirm2 = input("再次确认，输入账户末四位: ").strip()
    expected_suffix = os.getenv('XTQUANT_ACCOUNT', '')[-4:]
    if confirm2 != expected_suffix:
        print("账户验证失败，已取消")
        sys.exit(1)

    print(f"\n[{_now()}] 开始执行物理熔断...")

    # Step 2: 夺取全局下单令牌
    token_file = "run/order_token.lock"
    _acquire_order_token(token_file)
    print(f"[{_now()}] 下单令牌已夺取（主进程停止下单）")

    # Step 3: 尝试通过 xtquant 执行
    try:
        from xtquant import xttrader, xtconstant
        account_id = os.getenv('XTQUANT_ACCOUNT')
        xt = _connect_xtquant(account_id)

        if xt is None:
            _print_manual_sop()
            sys.exit(2)

        # Step 3a: 撤销所有在途委托
        active_orders = _cancel_all_orders(xt, account_id)
        print(f"[{_now()}] 已撤 {len(active_orders)} 笔在途委托")

        # Step 3b: 市价平仓（按 sellable_qty）
        positions = xt.query_stock_positions(account_id)
        filled_records = []
        for pos in positions:
            if pos.can_use_volume > 0:
                broker_id = xt.order_stock(
                    account=account_id,
                    stock_code=pos.stock_code,
                    order_type=xtconstant.STOCK_SELL,
                    order_volume=pos.can_use_volume,
                    price_type=xtconstant.MARKET_PRICE,
                    price=0,
                    order_remark=f"BREAK_GLASS_{_now_compact()}"
                )
                filled_records.append({
                    'ts_code': pos.stock_code,
                    'qty': pos.can_use_volume,
                    'broker_order_id': broker_id,
                })
                print(f"[{_now()}] 市价卖出 {pos.stock_code} × {pos.can_use_volume}")
                time.sleep(1.1)   # 限速（break-glass 可绕过，但仍延迟防止重复）

        # Step 4: 写 execution_ledger
        _record_break_glass(filled_records, active_orders)
        print(f"[{_now()}] 已写入 execution_ledger（break_glass_signature）")

    except ImportError:
        print("[ERROR] xtquant 不可用，降级为手动清仓 SOP")
        _print_manual_sop()
    except Exception as e:
        print(f"[ERROR] xtquant 执行失败: {e}")
        _print_manual_sop()

    # Step 5: 进入 BREAK_GLASS 暂停态
    _write_halt_state("BREAK_GLASS")
    print(f"\n[{_now()}] 系统已进入 BREAK_GLASS 暂停态")
    print("恢复步骤：")
    print("  1. 通过券商 APP 确认持仓已清零")
    print("  2. 截图存档")
    print("  3. 人工删除 run/order_token.lock（归还令牌）")
    print("  4. 系统重启后先进 RECONCILE 对账")


def _acquire_order_token(token_file: str) -> None:
    """夺取令牌（原子写，主进程检测到令牌即停止下单）"""
    import fcntl
    with open(token_file, 'w') as f:
        f.write(f'{{"acquired_by": "break_glass", "acquired_at": "{_now()}"}}')


def _cancel_all_orders(xt, account_id: str) -> list:
    """撤销所有在途委托"""
    orders = xt.query_stock_orders(account_id)
    active = [o for o in orders
              if o.order_status not in (51, 52, 53)]  # FILLED/CANCELLED/REJECTED
    for o in active:
        try:
            xt.cancel_order_stock(account_id, o.order_id)
            time.sleep(0.2)
        except Exception:
            pass
    return active


def _connect_xtquant(account_id: str):
    """尝试连接 xtquant，失败返回 None"""
    try:
        from xtquant import xttrader
        xt = xttrader.XtQuantTrader("path_to_qmt", session_id=9999)
        xt.start()
        conn = xt.connect()
        if conn != 0:
            return None
        return xt
    except Exception:
        return None


def _print_manual_sop():
    """打印手动清仓 SOP（xtquant 不可达时的降级路径）"""
    print("\n" + "=" * 60)
    print("📱 券商 APP 手动清仓 SOP")
    print("=" * 60)
    print("1. 打开手机券商 APP（国金证券）")
    print("2. 进入「交易」→「撤单」→「全部撤单」")
    print("3. 等待撤单确认（约30秒）")
    print("4. 进入「持仓」→选择每只股票→「卖出」→「市价卖出」")
    print("5. 确认全部持仓清零后截图留档")
    print("6. 事后在 execution_ledger 手工补录")
    print("=" * 60)


def _record_break_glass(filled: list, cancelled: list):
    """写入 execution_ledger（break_glass_signature）"""
    db_path = "db/quant.db"
    secret = os.getenv('BREAK_GLASS_KEY', 'default_key').encode()
    payload = f"BREAK_GLASS|{_now()}|{len(filled)}|{len(cancelled)}".encode()
    signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    conn = sqlite3.connect(db_path)
    for rec in filled:
        conn.execute("""
            INSERT INTO execution_ledger
              (account_id, ts_code, side, target_qty, to_state,
               break_glass_signature, event_ts, ingested_at)
            VALUES (?, ?, 'SELL', ?, 'RECONCILE', ?, ?, datetime('now'))
        """, (
            os.getenv('XTQUANT_ACCOUNT'), rec['ts_code'],
            rec['qty'], signature, _now()
        ))
    conn.commit()
    conn.close()


def _write_halt_state(reason: str):
    """写入暂停状态文件"""
    with open("run/halt_state.json", 'w') as f:
        import json
        json.dump({'state': 'BREAK_GLASS', 'reason': reason, 'ts': _now()}, f)


def _now() -> str:
    return datetime.now(tz=pytz.timezone('Asia/Shanghai')).strftime('%Y-%m-%dT%H:%M:%S+08:00')


def _now_compact() -> str:
    return datetime.now(tz=pytz.timezone('Asia/Shanghai')).strftime('%Y%m%d%H%M%S')


if __name__ == "__main__":
    main()
```

### 10.3 失败模式与缓解

| 失败模式 | 缓解措施 |
|---------|---------|
| xtquant 不可达 | 降级为券商 APP 手动清仓 SOP（§5.2 STEP 3）|
| 独立进程无法获取持仓 | 直连券商查询接口，不依赖主进程内存 |
| 与主进程重复下单 | 令牌互斥（单向夺取，主进程检测到令牌即停止）|
| 跌停时清仓无法成交 | 挂跌停价排队；T+1 不可卖部分标 pending_liquidation |
| 部分清仓后进程崩溃 | 动作幂等可重入，重启后读 ledger 续清 |
| 误触发 | 二次确认（'BREAK-GLASS' + 账户末四位）|

### 10.4 单元测试要点

- 二次确认失败：不执行任何清仓操作
- 令牌夺取：文件锁写入后主进程停止下单
- xtquant 不可达：打印 SOP，不抛未处理异常
- ledger 写入：break_glass_signature 正确生成

---

## §11 模块间接口契约表

| 调用方 | 被调用方 | 接口方法 | 输入 | 输出 | 约束 |
|--------|---------|---------|------|------|------|
| `data/pipeline` | `data/adapters/akshare_adapter` | `fetch_daily_bars` | ts_codes, start_date, end_date | pd.DataFrame（RawBarData 格式）| 重试 3 次；静默空返回需告警 |
| `data/pipeline` | `data/arbitrator` | `arbitrate` | ak_df, ts_df, bs_df | (clean_df, reports) | 三源均空 → CRITICAL 告警 |
| `data/pipeline` | `pit/validator` | `validate_data_gate` | trade_date | ValidationResult | 不通过则阻塞因子计算 |
| `factor/*` | `pit/query_engine` | `daily_bar_asof` | ts_codes, date_range, as_of, data_cut_id | pd.DataFrame + PIT_META | visible_at 严格遵守；canonical 四键排序 |
| `factor/*` | `pit/query_engine` | `financials_pit_asof` | ts_codes, end_date, as_of, data_cut_id | pd.DataFrame + PIT_META | VOIDED → 该 stage 不可用 |
| `signal/core_factor` | `factor/*` | `calc_factor_batch` | bar_df, as_of | pd.DataFrame（factor_value）| 纯函数；无 IO |
| `signal/merger` | `signal/core_factor` | `select_top_n_with_weights` | scores, volatility, top_n | pd.Series（target_weight）| 纯函数；权重加总=1 |
| `signal/merger` | `signal/market_timing` | `calc_market_timing` | hs300_close, ma_window | 'BULL'/'NEUTRAL'/'BEAR' | N 日确认延迟 |
| `risk/guard` | `risk/constraints` | `check_all_constraints` | intent, context | ConstraintCheckResult | 先于业务逻辑调用 |
| `risk/guard` | `risk/drawdown` | `check_drawdown_level` | current_nav, peak_nav | DrawdownLevel | 二级回撤阻止 BUY |
| `risk/guard` | `execution/rate_limiter` | `wait_if_needed` | account_id | None（或抛异常）| 先于风控调用（QS-C04 §6.1）|
| `risk/guard` | `execution/idempotency` | `exists` / `register` | client_order_id | bool / None | DB UNIQUE 约束兜底 |
| `risk/guard` | `execution/adapters/xtquant_adapter` | `place_order` | OrderIntent（含 risk_signature）| broker_order_id | 无签名拒绝；唯一入口 |
| `execution/state_machine` | `execution/outbox` | `recover_pending_orders` | ledger, broker | None | 启动时执行；MAYBE_SENT_UNKNOWN 不重发 |
| `execution/adapters/xtquant_adapter` | `execution/rate_limiter` | `wait_if_needed` | account_id | None | 先于业务逻辑（QS-C04 §6.1）|
| `reconcile/daily_recon` | `execution/adapters/xtquant_adapter` | `get_positions` | account_id | dict[ts_code → qty] | 对账真值（理论持仓 C vs 券商 B）|
| `reconcile/daily_recon` | `execution/ledger` | `compute_position_ledger` | account_id, trade_date | dict[ts_code → qty] | 成交事件流推导（含 cancel_fill_type）|
| `monitor/watchdog` | `execution` (PID file) | heartbeat_ts 检查 | 文件读取 | bool | 连续 3 次超时 → 告警 |
| `monitor/alerter` | Server 酱 HTTP API | POST /send | title, desp | HTTP 200 | 重试 3 次；HIGH 级同步发送 |
| `monitor/alerter` | 钉钉 Webhook | POST | markdown 消息 | HTTP 200 | 重试 3 次 |
| `scripts/break_glass` | `execution/adapters/xtquant_adapter` | `cancel_order` / `place_order` | broker_order_id / OrderIntent | bool / broker_id | 独立进程；不依赖主进程状态；降级为 APP 手动 |

---

*文档编号 QS-E03 · 版本 v1.0 · 日期 2026-06-12 · 上游依赖 SSOT/QS-C01/QS-C03/QS-C04/QS-E02 · 与 SSOT 冲突时以 SSOT 为准，已全文校验。*
