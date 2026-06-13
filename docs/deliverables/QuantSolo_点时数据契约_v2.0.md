# QuantSolo —《点时数据契约》v2.0（全文版）

| 属性 | 值 |
|------|----|
| **文档编号** | QS-C03 |
| **版本** | v2.0 |
| **日期** | 2026-06-12 |
| **状态** | 正式发布 |
| **上游依赖** | QS-CAL-001（统计闸门校准报告）、《决策基线与一致性规范 SSOT》 |
| **下游依赖** | QS-C01（设计文档）、QS-C02（研究协议）、QS-C04（执行与风控状态机）、QS-C05（模拟盘验收手册） |
| **与 SSOT 冲突时** | 以基线（SSOT）为准，已校验 |

> **说明：** 本文档为全文版，不再使用增量补丁形式。v1.2.1 与 v1.2.2 历史已合并，全部条款统一于此。引用其他文档只用编号（QS-C01/C02/C04/QS-CAL-001），不重抄条款全文。

---

## 版本演进表

| 版本 | 日期 | 触发事件 | 核心变更摘要 |
|------|------|----------|-------------|
| v1.0 | 2026-05 | 初版 | 基础点时契约框架，7 表 schema，8 场景 pytest |
| v1.1 | 2026-05 | 第一轮审查 | 新增 ST/停复牌/退市/新股无限制场景，8 场景 |
| v1.2 | 2026-05 | 第二轮审查 | factor_variant 初引入；12 场景；PostgreSQL 14+ |
| v1.2.1 | 2026-06 | 三份严格审查（F-1~F-4、P1-a~P1-e） | 修改≠撤销显式区分；复权基准改 visible_at；canonical 四键排序；factor_variant 三枚举写死；封板 |
| v1.2.2 | 2026-06-11 | 二次筛查 C2+S3 | variant_count 不喂 DSR；新增第 13 场景（test 物理封锁） |
| **v2.0** | **2026-06-12** | **SSOT 裁决：存储层重构、三源章、visible_at 规则落地** | **砍掉 PostgreSQL 双库；全部落 DuckDB/Parquet+SQLite；新增三源采集章；北向字段降级 P3；visible_at 规则明文化；13 场景继承；全文合并** |

---

## 目录

- [零、文档定位与范围](#零文档定位与范围)
- [一、修改 vs 撤销语义](#一修改-vs-撤销语义)
- [二、复权基准查询](#二复权基准查询)
- [三、canonical 确定性查询](#三canonical-确定性查询)
- [四、factor_snapshot 双因子集](#四factor_snapshot-双因子集)
- [五、涨跌停规则表](#五涨跌停规则表)
- [六、get_pit_ttm 预告区间与流量/存量项](#六get_pit_ttm-预告区间与流量存量项)
- [七、data_cut 时间边界与一致性](#七data_cut-时间边界与一致性)
- [八、存储层架构（v2.0 重构）](#八存储层架构v20-重构)
- [九、数据源三源体系](#九数据源三源体系)
- [十、visible_at 赋值规则（全套）](#十visible_at-赋值规则全套)
- [十一、9 表 + trade_calendar 完整 Schema](#十一9-表--trade_calendar-完整-schema)
- [十二、自动化点时回归测试（13 场景）](#十二自动化点时回归测试13-场景)
- [十三、点时正确性自检清单（v2.0）](#十三点时正确性自检清单v20)
- [附录 A：二次筛查审查报告 v1.0 重建摘要](#附录-a二次筛查审查报告-v10-重建摘要)
- [附录 B：北向数据停披处置说明](#附录-b北向数据停披处置说明)

---

## 零、文档定位与范围

### 0.1 核心目标

本契约是 QuantSolo 一人公司 A 股量化系统的**数据点时（Point-In-Time, PIT）正确性基准**。任何研究、回测、实盘代码在读取历史数据时，必须且只能通过本契约规定的 canonical 查询接口，以防止**未来信息泄漏（look-ahead bias）**进入策略信号。

### 0.2 点时铁律（引用 QS-C01）

本契约实现 QS-C01 §1 五条铁律第③条：**点时正确性——一切研究数据按 visible_at 重放**。铁律全文见 QS-C01，此处不重抄。

### 0.3 范围说明

本契约覆盖：
- 9 张核心表 + trade_calendar 的完整 DDL（SQLite 与 DuckDB/Parquet 方言）
- canonical 查询语义与 Python API 签名
- 数据源三源体系（AKShare / Tushare Pro / BaoStock）
- visible_at 赋值规则
- 存储层两层架构（DuckDB/Parquet + SQLite）
- 13 场景 pytest 回归测试规范

本契约**不**覆盖：统计闸门数字（见 QS-CAL-001）、状态机状态全集（见 QS-C04）、五条铁律全文（见 QS-C01）、IC 背离定义（见 QS-C02）。

---

## 一、修改 vs 撤销语义

### 1.1 两种操作显式区分

| 操作 | 动作 | 旧行处理 | canonical 对早期 as_of 可见性 |
|------|------|----------|-------------------------------|
| **修改（revision）** | 追加新 ACTIVE 版本（revision_seq+1） | **保留 ACTIVE，永不改写** | 旧 as_of 仍取到旧值（正确重放） |
| **撤销（void）** | 追加新 VOIDED 版本（revision_seq+1） | 保留不动 | 撤销前 as_of 取原值；撤销后取最新 = VOIDED → 返空 |

**关键约定：**

- **修改不 void 旧行**：财报更正、数据商修正均走「追加 ACTIVE」路径。
- **撤销才 void**：预告撤销、财报重述作废走「追加 VOIDED」路径。
- **行情类（daily_bar_raw）几乎只用修改**：极少真正作废；任何 VOIDED 行情必须进 `validate_data_gate` **单独告警**，防止静默丢数据在截面制造缺口。
- **绝不**先 WHERE 过滤 VOIDED/was_revised，再取最新版本——必须取全部候选后再按业务键取最新，最新版本若为 VOIDED 则该业务键返空。

### 1.2 canonical 通用规则

取 `visible_at <= as_of`（且在本 data_cut 的 snapshot 范围内）的全部候选，按业务键取**最新版本**；若该最新版本 `record_status = 'VOIDED'` → 该业务键返空。

**绝不先 WHERE 过滤 VOIDED/was_revised。**

---

## 二、复权基准查询

### 2.1 复权基准取数规则（F-2）

```
给定 as_of，对每只股票：
  base_event_ver = MAX(event_ver)
    FROM corporate_action
    WHERE ts_code = ?
      AND visible_at <= as_of          -- 改用 visible_at（原 announce_date 已废弃）
      AND record_status = 'ACTIVE'
    （无除权则 base_event_ver = 0）
  取 adj_factor_pit 中 base_event_ver = 该值的整条复权序列
```

**修改理由：** `announce_date` 仅有 date 粒度，无法表达盘后披露、延迟披露等情况，会造成时分塌缩的未来信息泄漏。统一走 `visible_at` 才与本契约第一铁律一致。

### 2.2 event_ver 唯一事件排序

```sql
-- event_ver: per-stock 累计, 唯一排序键: (ex_date, announce_visible_at, ca_type, record_id)
-- SQLite 方言（v2.0 主库）:
CREATE UNIQUE INDEX IF NOT EXISTS uq_ca_ver ON corporate_action (ts_code, event_ver);
-- VOIDED 的公司行动不计入 event_ver 递增（撤销的除权不参与复权基准）
```

---

## 三、canonical 确定性查询

### 3.1 snapshot_manifest 冲突优先级字段

```sql
-- SQLite DDL（v2.0 主库）
CREATE TABLE IF NOT EXISTS snapshot_manifest (
    data_cut_id    INTEGER NOT NULL REFERENCES data_cut(data_cut_id),
    table_name     TEXT    NOT NULL,
    snapshot_id    INTEGER NOT NULL REFERENCES data_snapshot(snapshot_id),
    source         TEXT    NOT NULL,
    source_priority INTEGER NOT NULL,  -- 多源冲突: 数字小者优先
                                       --   AKShare=1（主采集）
                                       --   Tushare=2（对账）
                                       --   BaoStock=3（冗余校验）
    snapshot_rank  INTEGER NOT NULL,   -- 同源多批次: 大者为新
    include_mode   TEXT    NOT NULL,   -- 'FULL' / 'INCR'
    row_count      INTEGER,
    checksum       TEXT,
    PRIMARY KEY (data_cut_id, table_name, snapshot_id)
);
```

### 3.2 统一 canonical SQL 模板

所有 `*_asof` 查询函数共用同一管道：

```
manifest → candidates → latest_per_business_key → drop_if_voided → return_with_meta
```

`latest_per_business_key` 排序固定为（保证同一 data_cut 重跑结果完全一致）：

```sql
ORDER BY visible_at DESC,
         revision_seq DESC,
         snapshot_rank DESC,
         record_id DESC
```

### 3.3 核心 canonical Python API

```python
def daily_bar_asof(
    ts_codes: list[str],
    dr: tuple[str, str],      # (start_date, end_date)
    as_of: str,               # ISO datetime, e.g. '2024-03-01T17:00:00+08:00'
    data_cut_id: int,
    adjust: str = "raw"       # 'raw' | 'qfq_pit' | 'hfq_pit'
) -> pd.DataFrame:
    """
    DuckDB 查询 daily_bar_raw Parquet，canonical 四键排序取最新版本。
    adjust='qfq_pit': base_event_ver 按 §2 visible_at 口径取同基准整条复权序列。
    命中行 record_status='VOIDED' → 该 (ts_code, trade_date) 剔除并经闸门告警。
    返回含 PIT_META 六字段（含 corp_actions_known_ver=base_event_ver）。
    """

def financials_pit_asof(
    ts_codes: list[str],
    end_date: str,
    as_of: str,
    data_cut_id: int
) -> pd.DataFrame:
    """
    SQLite 查询 financials_pit。对每个 (ts_code, end_date)：
    1) 对每个 stage 各取最新版本（canonical 四键排序）；
    2) 若某 stage 最新版本 = VOIDED → 该 stage 不可用；
    3) 在可用 stage 中按 OFFICIAL > EXPRESS > FORECAST 选用；
    4) 全部 stage 不可用 → 返空。
    """

def get_limit_rule_asof(
    ts_code: str,
    trade_date: str,
    as_of: str,
    data_cut_id: int
) -> dict:
    """
    SQLite 查询 price_limit_rule_pit。
    取 rule_effective_date <= trade_date 且 visible_at <= as_of 的最新规则（canonical 四键），
    结合 security_state 与 pre_close 派生涨跌停价。
    limit_price 为纯派生视图，不存独立事实表。
    """
```

### 3.4 PIT_META 元数据六字段

每个 canonical 查询结果附带：

| 字段 | 含义 |
|------|------|
| `pit_as_of` | 本次查询使用的 as_of 时间戳 |
| `pit_data_cut_id` | 本次查询使用的 data_cut_id |
| `pit_visible_at` | 命中行的 visible_at |
| `pit_revision_seq` | 命中行的 revision_seq |
| `pit_snapshot_rank` | 命中行的 snapshot_rank |
| `corp_actions_known_ver` | 复权查询时的 base_event_ver（非复权查询置 NULL） |

---

## 四、factor_snapshot 双因子集

### 4.1 factor_variant 三枚举（写死）

`factor_variant ∈ {raw, processed, orthogonal}`，对齐 QS-C01 §6 双因子集定义：

| 枚举值 | 含义 | 用途 |
|--------|------|------|
| `raw` | 原始未变换值 | SHAP 归因、人工解读 |
| `processed` | MAD 去极值 + 行业市值中性化 + z-score | Ridge 线性模型训练 |
| `orthogonal` | Cholesky 残差化 | LightGBM 训练 |

> 训练用双集（processed + orthogonal）+ 归因一份（raw），与 QS-C01 §6.1 措辞统一。

### 4.2 factor_snapshot DDL（SQLite）

```sql
CREATE TABLE IF NOT EXISTS factor_snapshot (
    record_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code          TEXT    NOT NULL,
    trade_date       TEXT    NOT NULL,     -- ISO date 'YYYY-MM-DD'
    factor_name      TEXT    NOT NULL,
    version          TEXT    NOT NULL,     -- 公式版本（公式变更时递增）
    factor_variant   TEXT    NOT NULL DEFAULT 'raw'
                     CHECK (factor_variant IN ('raw', 'processed', 'orthogonal')),
    -- factor_variant 含义见 §4.1
    computed_as_of   TEXT    NOT NULL,     -- ISO datetime，数据修正后重算时点
    factor_value     REAL,
    data_cut_id      INTEGER NOT NULL REFERENCES data_cut(data_cut_id),
    -- data_cut_id NOT NULL: 防可复现链断裂（P1-b）
    snapshot_id      INTEGER NOT NULL,
    record_status    TEXT    NOT NULL DEFAULT 'ACTIVE'
                     CHECK (record_status IN ('ACTIVE', 'VOIDED')),
    visible_at       TEXT    NOT NULL,     -- ISO datetime with timezone
    ingested_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE (ts_code, trade_date, factor_name, version, computed_as_of, factor_variant)
);

CREATE TABLE IF NOT EXISTS factor_registry (
    factor_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_name      TEXT    NOT NULL UNIQUE,
    description      TEXT,
    formula_version  TEXT    NOT NULL,
    variant_count    INTEGER,
    -- variant_count: 点时去重/可复现审计用；不喂 DSR（C2 裁决）
    -- DSR 的 N 唯一来源：research_ledger atomic_test_id（见 QS-C02 §3.5）
    created_at       TEXT    DEFAULT (datetime('now'))
);
```

### 4.3 计数语义（C2 裁决）

**v1.2.2 C2 裁决，v2.0 继承：**

- `factor_variant` 等价于 QS-C02 §3.5 atomic_test 的 **`preprocessing` 维度取值**；`version`（公式变更）并入 atomic 的 `formula` 维度。
- `factor_registry.variant_count` **仅用于点时层去重与可复现审计**，**不独立乘入 DSR 的 N**。
- 喂 DSR 的 `N_trials_raw / n_eff_total` 唯一来源：`research_ledger` 的 `atomic_test_id` 自动统计（口径权威方为 QS-C02）。
- **静态检查护栏**：CI 检查 `factor_registry.variant_count` 不出现在任何 DSR N 计算调用栈（防旧口径回潮，对应第 13 场景 C2 断言）。

---

## 五、涨跌停规则表

### 5.1 price_limit_rule_pit DDL（SQLite）

```sql
CREATE TABLE IF NOT EXISTS price_limit_rule_pit (
    record_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange             TEXT    NOT NULL,       -- 'SSE' / 'SZSE' / 'BSE'
    board_type           TEXT    NOT NULL,        -- 'MAIN' / 'GEM' / 'STAR' / 'BSE' / 'ST'
    security_state       TEXT    NOT NULL,
    -- security_state 枚举：
    --   'NORMAL'          : 正常交易
    --   'ST'              : ST / *ST 股票
    --   'NEW_IPO_5D'      : 新股/次新前 5 日（注册制主板/创业/科创）
    --   'DELIST_REORG'    : 退市整理期
    --   'RESUME_FIRST'    : 复牌首日（特殊规则）
    pct_limit            REAL,                   -- NULL = 无涨跌幅限制
    is_price_limit_free  INTEGER NOT NULL DEFAULT 0,   -- BOOLEAN: 1=无限制
    rule_effective_date  TEXT    NOT NULL,        -- ISO date
    -- 通用点时列：
    snapshot_id          INTEGER NOT NULL,
    source               TEXT    NOT NULL,
    record_status        TEXT    NOT NULL DEFAULT 'ACTIVE'
                         CHECK (record_status IN ('ACTIVE', 'VOIDED')),
    revision_seq         INTEGER NOT NULL DEFAULT 0,
    visible_at           TEXT    NOT NULL,
    visible_at_policy    TEXT,
    ingested_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE (exchange, board_type, security_state, rule_effective_date, revision_seq)
);
```

### 5.2 涨跌停覆盖矩阵

| 板块 | 正常 | ST | 新股前 5 日 | 退市整理 | 复牌首日 |
|------|------|----|------------|---------|---------|
| 主板（注册制后） | ±10% | ±5% | 无限制 | ±10% | 规则见公告 |
| 创业板 | ±20% | ±20% | 无限制 | ±20% | 规则见公告 |
| 科创板 | ±20% | ±20% | 无限制 | ±20% | 规则见公告 |
| 北交所 | ±30% | ±30% | 无限制 | ±30% | 规则见公告 |

### 5.3 limit_price 派生规则

`limit_price` 为**纯派生视图**，不存独立事实表。由 `get_limit_rule_asof()` + `pre_close` + `security_state` 实时派生：

```python
def derive_limit_price(pre_close: float, pct_limit: float | None) -> tuple[float | None, float | None]:
    """
    pct_limit=None（is_price_limit_free=True）→ (None, None)
    否则: upper = pre_close * (1 + pct_limit), lower = pre_close * (1 - pct_limit)
    结果精确到分（round 0.01）
    """
```

---

## 六、get_pit_ttm 预告区间与流量/存量项

```python
def get_pit_ttm(
    ts_code: str,
    as_of: str,
    data_cut_id: int,
    metric: str    # 'revenue' | 'net_profit' | 'ocf' | 'total_assets' | 'total_equity' | 'debt_ratio' | ...
) -> tuple[float | None, str]:
    """
    返回 (value, confidence_tag)。

    流量项（revenue / net_profit / ocf）：
      拼接最近四季，每季取 ann_date <= as_of 最新未撤销版本；
      允许混 stage，但若最近一季仅 FORECAST（区间值）：
        收益类 → 取 forecast_low（保守估计）；
        比率类（roe / gross_margin）→ 取区间中点推算；
        confidence_tag = 'FORECAST_PARTIAL'（供因子层决定是否采用）。

    存量项（total_assets / total_equity / debt_ratio）：
      直接取最近一期【时点值】，不做四季拼接。

    任一所需季缺失 → (None, 'INSUFFICIENT')。

    confidence_tag 枚举：
      'OFFICIAL'          : 全部来自年报/季报正式公告
      'EXPRESS_INCLUDED'  : 含快报，无预告
      'FORECAST_PARTIAL'  : 最近一季来自预告
      'INSUFFICIENT'      : 数据不足，返空
    """
```

---

## 七、data_cut 时间边界与一致性

### 7.1 data_cut 表结构

```sql
CREATE TABLE IF NOT EXISTS data_cut (
    data_cut_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    cut_name       TEXT    NOT NULL UNIQUE,
    cut_date       TEXT    NOT NULL,           -- 该 cut 取到哪一天的数据（研究闸门按此选 cut）
    purpose        TEXT    NOT NULL DEFAULT 'research'
                   CHECK (purpose IN ('research', 'final_test', 'prod')),
    created_at     TEXT    DEFAULT (datetime('now')),
    approved_by    TEXT,                       -- final_test cut 须人工审批并留痕
    notes          TEXT,
    -- test 段封锁约束（S3，对应第 13 场景）：
    -- 研发期 cut 的 cut_date 必须 < test 起始日（2024-01-01）
    CHECK (purpose != 'research' OR cut_date < '2024-01-01')
);
-- purpose='final_test' 的 cut 单独审批创建并留痕，cut_date 允许 >= 2024-01-01
```

### 7.2 data_snapshot 不可变性

```sql
CREATE TABLE IF NOT EXISTS data_snapshot (
    snapshot_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    data_cut_id    INTEGER NOT NULL REFERENCES data_cut(data_cut_id),
    table_name     TEXT    NOT NULL,
    created_at     TEXT    DEFAULT (datetime('now')),
    quality_status TEXT    NOT NULL DEFAULT 'PENDING'
                   CHECK (quality_status IN ('PENDING', 'PASS', 'FAIL')),
    row_count      INTEGER,
    checksum       TEXT,
    notes          TEXT
);
-- quality_status='PASS' 即冻结，应用层禁止对 PASS snapshot 做 UPDATE/DELETE
-- 可通过 SQLite trigger 实现物理护栏（见 §12 第 12 场景）
```

### 7.3 universe 与关注点分离

- `get_active_universe(as_of, data_cut_id)` → 仅返回 `list_status='L'` 全部在市股（含日后退市的历史股），**不做停牌过滤**。
- 停牌/ST/流动性/高价过滤一律交策略层 `apply_stock_filters()` 处理（与 QS-C01 关注点分离一致）。

### 7.4 supersession_log

`supersession_log` **仅用于审计追溯**，不参与在线 canonical 查询（消除与 `supersedes_record_id` 双写疑虑）。

```sql
CREATE TABLE IF NOT EXISTS supersession_log (
    log_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name     TEXT    NOT NULL,
    old_record_id  INTEGER NOT NULL,
    new_record_id  INTEGER NOT NULL,
    reason         TEXT,
    created_at     TEXT    DEFAULT (datetime('now'))
);
-- 仅审计，canonical 查询绝不读此表
```

---

## 八、存储层架构（v2.0 重构）

### 8.1 迁移裁决与理由

**v2.0 砍掉 PostgreSQL 双库方案**，全部落 **DuckDB/Parquet + SQLite 两层**。

**迁移理由（一人公司零运维原则）：**

| 维度 | PostgreSQL（已废弃） | DuckDB/Parquet + SQLite（v2.0） |
|------|---------------------|--------------------------------|
| 运维负担 | 需要服务进程常驻、用户权限管理、WAL 维护、pg_dump 备份 | 文件即数据库，零服务进程，rclone 直接备份文件 |
| Windows 兼容 | 需单独安装、版本管理 | DuckDB/SQLite 均为嵌入式，Python pip 安装 |
| 分析性能 | 向量化差，大批量行情回测慢 | DuckDB 列式引擎，Parquet 压缩存储，回测查询快 10-50× |
| 事务安全 | 强事务 | SQLite WAL 模式足够单机并发，交易/持仓/审计事务安全 |
| 成本 | 免费但有复杂维护 | 零成本，零维护 |
| 合规 | N/A | 文件级加密可选（SQLCipher） |

**结论：** 对 QuantSolo 一人公司场景，PostgreSQL 的优势全部在分布式/高并发场景才体现，家用 Windows PC 单机场景完全不需要。

### 8.2 两层存储分配

#### 第一层：DuckDB + Parquet（行情/因子面板）

存放**只读分析型、大批量写入、列式扫描为主**的数据：

| 数据 | 格式 | 分区策略 |
|------|------|---------|
| `daily_bar_raw` | Parquet（列式） | 按 `trade_date` 年分区：`daily_bar/year={YYYY}/` |
| `adj_factor_pit` | Parquet（列式）| **按年分区**：`adj_factor_pit/year={YYYY}/`（大表，约 1.5-2 亿行） |
| `factor_snapshot` | Parquet（列式）| 按 `trade_date` 年分区 |
| `financials_pit` | Parquet（列式）| 按 `end_date` 年分区 |
| 舆情 Parquet 缓存 | Parquet | 按月分区（P2 阶段） |

DuckDB 用于查询引擎，直接读 Parquet 文件，无需 import：

```python
import duckdb
conn = duckdb.connect()   # in-memory，或 conn=duckdb.connect('quant.duckdb')
df = conn.execute("""
    SELECT * FROM read_parquet('data/daily_bar/year=*/part-*.parquet')
    WHERE ts_code = '000001.SZ'
      AND trade_date BETWEEN '2023-01-01' AND '2023-12-31'
      AND visible_at <= '2023-12-31T17:00:00+08:00'
    ORDER BY visible_at DESC, revision_seq DESC, snapshot_rank DESC, record_id DESC
    LIMIT 1
""").fetchdf()
```

#### 第二层：SQLite（交易/持仓/信号/审计）

存放**事务型、小批量写入、强一致性**的数据：

| 表 | 说明 |
|----|------|
| `data_cut` | cut 元数据，含 test 封锁约束 |
| `data_snapshot` | snapshot 不可变性管理 |
| `snapshot_manifest` | 多源冲突优先级 |
| `corporate_action` | 公司行动（除权/分红） |
| `price_limit_rule_pit` | 涨跌停规则点时表 |
| `trade_calendar` | 交易日历（见 §11） |
| `supersession_log` | 审计追溯（只写） |
| `factor_registry` | 因子注册表 |
| 交易记录（QS-C04 管辖） | 实盘订单/持仓/信号 |
| 审计日志（QS-C04 管辖） | 状态机转换、风控触发 |

SQLite 使用 **WAL 模式**（`PRAGMA journal_mode=WAL`）支持读写并发：

```python
import sqlite3
conn = sqlite3.connect('quant.db')
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA foreign_keys=ON')
```

### 8.3 adj_factor_pit 大表按年分区

`adj_factor_pit` 约 1.5-2 亿行，**按 `trade_date` 年分区写 Parquet**：

```python
# 构建时按年写入
import pyarrow as pa
import pyarrow.parquet as pq

def build_pit_adj_factors(
    ts_code: str,
    batch_size: int = 100_000,
    progress_callback=None
) -> None:
    """
    逐基准版本（base_event_ver）重算复权因子序列。
    结果写入 data/adj_factor_pit/year={YYYY}/ts_code={ts_code}/part-{batch}.parquet
    batch_size: 控制内存峰值（单批处理行数）
    progress_callback: (current_batch, total_batches) -> None
    构建可能数小时级，离线一次性操作。
    """
```

```python
# 查询时用 DuckDB 分区裁剪
def adj_factor_pit_asof(ts_code: str, as_of: str, data_cut_id: int) -> pd.DataFrame:
    """
    DuckDB 读分区 Parquet，自动裁剪 year 分区。
    canonical 四键排序取每个 (ts_code, trade_date, base_event_ver) 的最新版本。
    """
```

### 8.4 备份策略

```
SQLite 文件（quant.db）：
  盘后 17:30 用 rclone 增量同步至外置盘 + OneDrive/百度网盘
  周度恢复演练（还原至临时目录验证读取）

Parquet 文件（data/ 目录）：
  盘后 17:30 用 rclone 增量同步（--checksum 校验）
  按年 tar.gz 归档（完成年度后压缩封存）

代码：
  Git + GitHub 私有仓库
```

---

## 九、数据源三源体系

### 9.1 三源定位

| 数据源 | 角色 | 费用 | 主要数据 |
|--------|------|------|---------|
| **AKShare** | 主采集（Primary） | 免费 | 日线行情（后复权）、龙虎榜、个股资金流（东财）、停复牌/ST/退市名录、沪深港通（历史存量） |
| **Tushare Pro 2000积分档** | 对账与补强（Secondary） | 约 200 元/年 | 日线/财报/龙虎榜明细/股东户数，做第二来源交叉校验 |
| **BaoStock** | 冗余校验（Tertiary） | 免费 | 日线第三源，参与三源两票制冲突裁决 |

> 注：5000 积分档（THS 资金流）暂不采购，P2 阶段再评估（见 QS-C01 §5）。

### 9.2 三源两票制冲突裁决流程

**触发条件：** 同一 `(ts_code, trade_date)` 的字段值在两个或以上数据源之间存在差异，且差异超过容差阈值。

**容差阈值（首次设定，可按需调整）：**

| 字段类型 | 绝对容差 | 相对容差 | 说明 |
|---------|---------|---------|------|
| 收盘价、高/低价 | — | 0.01%（万分之一） | 数据商独立舍入 |
| 成交量（手） | 1 手 | 0.1% | 单位换算差异 |
| 成交额（元） | 100 元 | 0.01% | |
| 复权因子 | — | 0.001% | 精度敏感 |

**裁决流程：**

```
Step 1: 采集阶段
  AKShare（source_priority=1）采集完成后写入 daily_bar_raw（visible_at=当日17:00）

Step 2: 对账阶段（盘后 18:00-20:00）
  Tushare Pro（source_priority=2）采集同日数据
  BaoStock（source_priority=3）采集同日数据

Step 3: 差异检测
  compare_sources(akshare_df, tushare_df, baostock_df, tolerance) -> DiffReport

Step 4: 两票制裁决
  IF akshare == tushare（在容差内）:
    采用 AKShare，BaoStock 差异标注 WARNING 进审计日志
  ELIF akshare == baostock（在容差内）:
    采用 AKShare，Tushare 差异标注 WARNING，人工次日复核
  ELIF tushare == baostock（在容差内）:
    -- AKShare 异常，升级告警
    采用 Tushare（source_priority=2 覆盖），追加新 ACTIVE 版本（修改语义）
    告警：SERVER_CHAN 推送手机，标注 AKShare_ANOMALY
  ELSE:
    -- 三源均不一致
    升级为 CRITICAL 告警，人工处理
    当日该字段暂标注 quality_flag='CONFLICT'，不进入研究截面

Step 5: 写入
  裁决结果追加至 daily_bar_raw（canonical 追加 ACTIVE）
  裁决记录写入 supersession_log（审计）
  data_pipeline_audit 写入裁决详情
```

**自动化配置文件（`config/source_priority.yaml`）：**

```yaml
source_priority:
  akshare: 1
  tushare: 2
  baostock: 3

conflict_tolerance:
  price_pct: 0.0001
  volume_abs: 1
  amount_pct: 0.0001
  adj_factor_pct: 0.00001

alert_channels:
  - server_chan
  - dingtalk_bot

conflict_escalation:
  two_source_mismatch: WARNING   # 人工次日复核
  three_source_mismatch: CRITICAL  # 立即人工处理
```

### 9.3 各源采集范围与调用约定

#### AKShare 主采集

```python
import akshare as ak

# 日线行情（前复权，后自行计算 PIT 复权因子）
ak.stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust="")
# adjust="" 取原始价格，PIT 复权因子由 adj_factor_pit 自行维护

# 龙虎榜（东财）
ak.stock_lhb_detail_em(date)

# 个股资金流（东财）
ak.stock_individual_fund_flow(stock, market)

# 停复牌/ST/退市名录
ak.stock_zh_a_st_em()           # ST 名录
ak.stock_zh_a_stop_em()         # 停牌名录（当日）

# 除权除息（公司行动）
ak.stock_dividents_cninfo(code)  # 分红配股历史
```

#### Tushare Pro 对账

```python
import tushare as ts
pro = ts.pro_api(token=os.getenv('TUSHARE_TOKEN'))  # 2000 积分档

# 日线行情
pro.daily(ts_code, start_date, end_date)

# 复权因子（用于交叉校验 adj_factor_pit）
pro.adj_factor(ts_code, start_date, end_date)

# 财报（合并报表）
pro.income(ts_code, period)
pro.balancesheet(ts_code, period)
pro.cashflow(ts_code, period)

# 龙虎榜明细
pro.top_list(trade_date)

# 股东户数
pro.stk_holdernumber(ts_code)
```

#### BaoStock 冗余校验

```python
import bsapi as bs   # baostock 包

bs.login()
# 日线行情（第三源）
rs = bs.query_history_k_data_plus(
    code,            # 'sh.600000' 格式
    "date,code,open,high,low,close,volume,amount,adjustflag",
    start_date, end_date,
    frequency="d",
    adjustflag="3"   # "3"=不复权，自行维护 PIT 复权
)
bs.logout()
```

### 9.4 舆情源（P2 阶段）

- 公开新闻 + 研报 RSS 源，LLM（本地 Qwen 或低价 API）结构化打分。
- 结果写 Parquet 缓存（按月分区），visible_at 规则见 §10.3。
- M1/M2 阶段不实施，M3 后 P2 再启动。

---

## 十、visible_at 赋值规则（全套）

`visible_at` 是本契约最核心的防未来信息泄漏字段，代表**数据在现实中对研究者可见的最早时刻**。以下规则按 SSOT §5 落地。

### 10.1 日线行情

```
visible_at = 当日 17:00:00+08:00（盘后采集时间戳）

规则：
  每个交易日盘后，AKShare 管道在 17:00 启动采集。
  采集完成后，所有当日 trade_date 的 daily_bar_raw 行，
  统一打 visible_at = 当日 17:00（而非实际写库时间 ingested_at）。
  ingested_at 记录真实写库时间（供运维排查，不用于 canonical 查询）。

示例：
  2024-03-01 的行情，visible_at = '2024-03-01T17:00:00+08:00'
  即使实际写库时间是 17:23，visible_at 仍打 17:00。
```

### 10.2 财报/公司公告

```
visible_at = announce_date 的下一个交易日 09:00:00+08:00

规则（保守处理，防止公告日当天盘中使用尚未在数据库的财报）：
  ann_date = 公告日（如 2024-04-30）
  visible_at = next_trade_date(ann_date) + T09:00:00+08:00
             = '2024-05-06T09:00:00+08:00'（若 2024-05-01-05 为节假日）

  若公告日当天为交易日且公告在收盘后（15:00 后），仍取下一交易日。
  若公告日当天为交易日且公告在开盘前（如 8:30 前），亦取下一交易日（保守）。

trade_calendar 查询：
  next_trade_date(date) = MIN(cal_date) FROM trade_calendar
                          WHERE cal_date > date AND is_open = 1
```

### 10.3 新闻/舆情（t-1 日 15:00 到 t 日 15:00 归 t+1）

```
新闻发布时间戳 → 归属交易日 t → visible_at = next_trade_date(t) + T09:00:00+08:00

归属规则：
  若 news_ts 在 [t-1 交易日 15:00, t 交易日 15:00) 内
    → 归属交易日 = t（即该新闻隔夜才对下一日策略可见）
    → visible_at = next_trade_date(t) + T09:00:00+08:00

直觉：
  2024-03-04 14:00 发布的新闻（当日 A 股收盘前）
    → 归属 t = 2024-03-04
    → visible_at = '2024-03-05T09:00:00+08:00'（即 t+1 开盘）

  2024-03-04 16:00 发布的新闻（收盘后）
    → 属于 [2024-03-04 15:00, 2024-03-05 15:00) 区间
    → 归属 t = 2024-03-05
    → visible_at = '2024-03-06T09:00:00+08:00'

目的：防止使用收盘后发布的新闻来预测同日收盘价格（属未来信息泄漏）
```

### 10.4 公司行动（除权除息）

```
visible_at = announce_visible_at（除权公告首次在数据源中出现的时间戳）
  若数据源只有 announce_date（date 粒度）：
    visible_at = announce_date + T17:00:00+08:00（按盘后口径保守处理）
```

### 10.5 涨跌停规则

```
visible_at = rule_effective_date + T00:00:00+08:00（规则生效当日零时）
  对于监管新规（如注册制改革当日起生效），
  visible_at 与 rule_effective_date 对齐，不向后推延。
```

### 10.6 visible_at 赋值代码约定

```python
from datetime import datetime, timezone, timedelta
import pytz

CST = pytz.timezone('Asia/Shanghai')

def visible_at_eod(trade_date: str) -> str:
    """盘后行情的 visible_at：trade_date 当日 17:00 CST"""
    dt = datetime.strptime(trade_date, '%Y-%m-%d')
    dt_cst = CST.localize(dt.replace(hour=17, minute=0, second=0))
    return dt_cst.isoformat()

def visible_at_next_open(ann_date: str, trade_calendar) -> str:
    """财报/公告的 visible_at：ann_date 下一交易日 09:00 CST"""
    next_td = trade_calendar.next_trade_date(ann_date)
    dt = datetime.strptime(next_td, '%Y-%m-%d')
    dt_cst = CST.localize(dt.replace(hour=9, minute=0, second=0))
    return dt_cst.isoformat()
```

---

## 十一、9 表 + trade_calendar 完整 Schema

以下为 v2.0 两层存储完整 DDL。DuckDB 层以 Parquet schema 形式描述（Python 类型注解），SQLite 层以 SQL DDL 描述。

### 11.1 表总览

| # | 表名 | 存储层 | 说明 |
|---|------|--------|------|
| 1 | `daily_bar_raw` | DuckDB/Parquet | 原始日线行情（多源版本追加） |
| 2 | `adj_factor_pit` | DuckDB/Parquet（按年分区） | 点时复权因子序列 |
| 3 | `financials_pit` | DuckDB/Parquet | 点时财报（季报/年报/快报/预告） |
| 4 | `factor_snapshot` | DuckDB/Parquet | 因子截面快照 |
| 5 | `corporate_action` | SQLite | 公司行动（除权除息） |
| 6 | `price_limit_rule_pit` | SQLite | 涨跌停规则点时表 |
| 7 | `data_cut` | SQLite | 数据切割元数据 |
| 8 | `data_snapshot` | SQLite | 快照元数据（不可变性管理） |
| 9 | `snapshot_manifest` | SQLite | 多源冲突优先级清单 |
| + | `trade_calendar` | SQLite | 交易日历 |

### 11.2 daily_bar_raw（DuckDB/Parquet）

```python
# Parquet schema（PyArrow 类型）
DAILY_BAR_RAW_SCHEMA = pa.schema([
    pa.field('record_id',     pa.int64(),    nullable=False),  # 全局唯一，追加递增
    pa.field('ts_code',       pa.string(),   nullable=False),  # '000001.SZ'
    pa.field('trade_date',    pa.string(),   nullable=False),  # 'YYYY-MM-DD'
    # 行情字段（原始，不复权）
    pa.field('open',          pa.float64(),  nullable=True),
    pa.field('high',          pa.float64(),  nullable=True),
    pa.field('low',           pa.float64(),  nullable=True),
    pa.field('close',         pa.float64(),  nullable=True),
    pa.field('pre_close',     pa.float64(),  nullable=True),
    pa.field('volume',        pa.float64(),  nullable=True),   # 手
    pa.field('amount',        pa.float64(),  nullable=True),   # 元
    pa.field('turnover_rate', pa.float64(),  nullable=True),
    # 点时管理字段
    pa.field('visible_at',    pa.string(),   nullable=False),  # ISO datetime+tz
    pa.field('ingested_at',   pa.string(),   nullable=True),
    pa.field('revision_seq',  pa.int32(),    nullable=False, metadata={'default': '0'}),
    pa.field('record_status', pa.string(),   nullable=False, metadata={'default': 'ACTIVE'}),
    # ACTIVE / VOIDED
    pa.field('source',        pa.string(),   nullable=False),  # 'akshare'/'tushare'/'baostock'
    pa.field('snapshot_id',   pa.int64(),    nullable=False),
    # 质量标志
    pa.field('quality_flag',  pa.string(),   nullable=True),
    # NULL=正常; 'CONFLICT'=三源冲突待处理; 'VOIDED_ALERT'=行情撤销告警
])
# 分区键：year（从 trade_date 提取）
# 文件路径：data/daily_bar/year={YYYY}/part-{N:05d}.parquet
```

### 11.3 adj_factor_pit（DuckDB/Parquet，按年分区）

```python
ADJ_FACTOR_PIT_SCHEMA = pa.schema([
    pa.field('record_id',       pa.int64(),    nullable=False),
    pa.field('ts_code',         pa.string(),   nullable=False),
    pa.field('trade_date',      pa.string(),   nullable=False),  # 'YYYY-MM-DD'
    pa.field('base_event_ver',  pa.int32(),    nullable=False),
    # base_event_ver: 对应 corporate_action.event_ver，
    # 同一 ts_code 的全部复权序列按此版本分隔
    pa.field('adj_factor',      pa.float64(),  nullable=False),
    # 前复权因子（相对最新基准归一化为 1.0）
    # 点时管理字段
    pa.field('visible_at',      pa.string(),   nullable=False),
    pa.field('ingested_at',     pa.string(),   nullable=True),
    pa.field('revision_seq',    pa.int32(),    nullable=False, metadata={'default': '0'}),
    pa.field('record_status',   pa.string(),   nullable=False, metadata={'default': 'ACTIVE'}),
    pa.field('snapshot_id',     pa.int64(),    nullable=False),
    pa.field('source',          pa.string(),   nullable=False),
])
# 分区键：year（从 trade_date 提取）
# 文件路径：data/adj_factor_pit/year={YYYY}/ts_code={ts_code}/part-{N}.parquet
# 大表约 1.5-2 亿行；build_pit_adj_factors() 离线一次性构建（数小时级）
```

### 11.4 financials_pit（DuckDB/Parquet）

```python
FINANCIALS_PIT_SCHEMA = pa.schema([
    pa.field('record_id',       pa.int64(),    nullable=False),
    pa.field('ts_code',         pa.string(),   nullable=False),
    pa.field('end_date',        pa.string(),   nullable=False),  # 报告期末日 'YYYY-MM-DD'
    pa.field('ann_date',        pa.string(),   nullable=True),   # 公告日期
    pa.field('stage',           pa.string(),   nullable=False),
    # stage 枚举：'OFFICIAL'(正式年/季报) / 'EXPRESS'(业绩快报) / 'FORECAST'(业绩预告)
    # 财务指标（P&L）
    pa.field('revenue',         pa.float64(),  nullable=True),   # 营业收入（元）
    pa.field('net_profit',      pa.float64(),  nullable=True),   # 归母净利润（元）
    pa.field('ocf',             pa.float64(),  nullable=True),   # 经营现金流（元）
    pa.field('gross_profit',    pa.float64(),  nullable=True),
    pa.field('ebit',            pa.float64(),  nullable=True),
    # 财务指标（B/S）
    pa.field('total_assets',    pa.float64(),  nullable=True),
    pa.field('total_equity',    pa.float64(),  nullable=True),
    pa.field('total_debt',      pa.float64(),  nullable=True),
    pa.field('cash',            pa.float64(),  nullable=True),
    # 预告专属字段（stage='FORECAST'）
    pa.field('forecast_low',    pa.float64(),  nullable=True),
    pa.field('forecast_high',   pa.float64(),  nullable=True),
    pa.field('forecast_type',   pa.string(),   nullable=True),
    # 点时管理字段
    pa.field('visible_at',      pa.string(),   nullable=False),
    pa.field('ingested_at',     pa.string(),   nullable=True),
    pa.field('revision_seq',    pa.int32(),    nullable=False, metadata={'default': '0'}),
    pa.field('record_status',   pa.string(),   nullable=False, metadata={'default': 'ACTIVE'}),
    pa.field('snapshot_id',     pa.int64(),    nullable=False),
    pa.field('data_cut_id',     pa.int64(),    nullable=False),  # P1-b: NOT NULL
    pa.field('source',          pa.string(),   nullable=False),
])
# 截面索引（DuckDB 统计信息）：(end_date, ann_date, visible_at DESC)
# 分区键：year（从 end_date 提取）
```

### 11.5 factor_snapshot（DuckDB/Parquet）

见 §4.2 DDL，Parquet 版本字段类型映射同上（TEXT→pa.string()，INTEGER→pa.int64()，REAL→pa.float64()）。

### 11.6 corporate_action（SQLite）

```sql
CREATE TABLE IF NOT EXISTS corporate_action (
    record_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code              TEXT    NOT NULL,
    ca_type              TEXT    NOT NULL,
    -- ca_type 枚举：'DIV'(现金分红) / 'SPLIT'(拆股) / 'RIGHTS'(配股) / 'ALLOT'(送股)
    ex_date              TEXT    NOT NULL,              -- 除权除息日 'YYYY-MM-DD'
    announce_date        TEXT,                          -- 原始公告日（仅备存，不用于 canonical）
    announce_visible_at  TEXT    NOT NULL,              -- visible_at（见 §10.4）
    event_ver            INTEGER NOT NULL,              -- per-stock 累计事件版本号
    -- event_ver 唯一排序键：(ex_date, announce_visible_at, ca_type, record_id)
    -- VOIDED 的公司行动不计入 event_ver 递增
    cash_div             REAL,                          -- 每股现金分红（元）
    split_ratio          REAL,                          -- 拆股比例（>1 为拆股）
    rights_ratio         REAL,                          -- 配股比例
    rights_price         REAL,                          -- 配股价格
    allot_ratio          REAL,                          -- 送股比例
    -- 点时管理字段
    visible_at           TEXT    NOT NULL,
    ingested_at          TEXT    DEFAULT (datetime('now')),
    revision_seq         INTEGER NOT NULL DEFAULT 0,
    record_status        TEXT    NOT NULL DEFAULT 'ACTIVE'
                         CHECK (record_status IN ('ACTIVE', 'VOIDED')),
    snapshot_id          INTEGER NOT NULL,
    source               TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ca_ver ON corporate_action (ts_code, event_ver);
-- VOIDED 行不计入此唯一约束的业务语义
-- 即：VOIDED 行保留在表中（点时历史），但 event_ver 不再被后续行引用
```

### 11.7 trade_calendar（SQLite）

```sql
CREATE TABLE IF NOT EXISTS trade_calendar (
    cal_date      TEXT    NOT NULL PRIMARY KEY,  -- 'YYYY-MM-DD'
    exchange      TEXT    NOT NULL DEFAULT 'SSE', -- 'SSE'=上交所 / 'SZSE'=深交所
    is_open       INTEGER NOT NULL,              -- 1=交易日 0=非交易日
    pretrade_date TEXT,                          -- 上一交易日
    -- 来源：AKShare ak.tool_trade_date_hist_sina()
    updated_at    TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cal_open ON trade_calendar (is_open, cal_date);

-- 辅助函数（Python 实现，通过 SQLite 查询支撑）
-- next_trade_date(date) = SELECT MIN(cal_date) FROM trade_calendar
--                          WHERE cal_date > :date AND is_open = 1
-- prev_trade_date(date) = SELECT MAX(cal_date) FROM trade_calendar
--                          WHERE cal_date < :date AND is_open = 1
```

### 11.8 其余 SQLite 表

`data_cut`、`data_snapshot`、`snapshot_manifest`、`price_limit_rule_pit`、`supersession_log`、`factor_registry` 的完整 DDL 见 §3.1、§5.1、§7.1、§7.2、§7.4、§4.2（已包含）。

### 11.9 validate_adj_coverage（纳入闸门）

```python
def validate_adj_coverage(data_cut_id: int) -> ValidationResult:
    """
    校验每个除权事件（corporate_action ACTIVE 行）都有对应 PIT 序列，
    且序列内 base_event_ver 唯一（无混用基准）。
    返回 ValidationResult(passed: bool, missing_events: list, mixed_basis: list)
    其输出纳入 validate_data_gate.passed（与 coverage/adj_missing 同级阻断）：
      validate_adj_coverage.passed = True 为 validate_data_gate 前置条件之一
    """
```

---

## 十二、自动化点时回归测试（13 场景）

本章为 v2.0 唯一防回归手段。**13 场景全 PASS 接入 pre-commit**，任何 schema/逻辑修改须先保证 13 场景通过。

> **注：** v2.0 存储层已从 PostgreSQL 迁移至 DuckDB/Parquet + SQLite，pytest fixture 相应改用 SQLite in-memory + DuckDB in-memory，不再依赖 Docker PostgreSQL。

### 12.1 13 场景清单

| # | 场景名 | 来源版本 | 核心断言 |
|---|--------|----------|---------|
| 1 | 除权前后 as_of 断言 | v1.1 | 除权日前 as_of 取除权前复权因子；除权日后 as_of 取新基准 |
| 2 | 三释放场景 | v1.1 | 预告→快报→正式报披露三次，每次 as_of 分别取对应最新可见版本 |
| 3 | 行情修正后回看 | v1.1 | 数据商修正日期前 as_of 仍取原始值（修改追加 ACTIVE，旧行不动） |
| 4 | 行情撤销断言 | v1.1 | 追加 VOIDED 后，撤销日之后 as_of 该业务键返空，且触发闸门告警 |
| 5 | ST 涨跌停规则断言 | v1.1 | ST 股 as_of 取 ±5% 规则，普通股取 ±10% |
| 6 | 停复牌点时断言 | v1.1 | 停牌期间 visible_at 缺口不引发截面缺口（get_active_universe 不做停牌过滤） |
| 7 | 退市股历史重放 | v1.1 | 退市股在其退市日前的 as_of 仍可取到历史行情，不被 universe 过滤 |
| 8 | 新股前 5 日无涨跌限断言 | v1.1 | is_price_limit_free=True，derive_limit_price 返回 (None, None) |
| 9 | 修正后回看修正前 | v1.2.1 | 修正日前 as_of 仍取原值（F-1 修改不 void 旧行） |
| 10 | 同股多次除权唯一基准 | v1.2.1 | 任意 as_of，序列内 base_event_ver 唯一，无混用基准 |
| 11 | 行业回溯重分类 | v1.2.1 | 行业中性化走点时行业 scheme，重分类后 as_of 仍取重分类前 scheme |
| 12 | data_cut 不可变性 | v1.2.1 | 对已 PASS 的 snapshot 做 UPDATE/DELETE 被 trigger/应用层拒绝 |
| 13 | test 段物理封锁（S3） | v1.2.2 | 研发期 data_cut.cut_date < '2024-01-01'；注入 cut_date='2024-03-01' 的研发 cut 被 CHECK 约束拒绝，抛 TEST_LEAK_GUARD 异常 |

### 12.2 场景 13 详细规范（S3）

```python
def test_research_cut_before_test():
    """
    断言：任何 purpose='research' 的 data_cut，其 cut_date 必须 < '2024-01-01'
    注入：尝试 INSERT purpose='research', cut_date='2024-03-01' 的 data_cut
    期望：SQLite CHECK 约束拒绝，抛 sqlite3.IntegrityError（或封装后 TEST_LEAK_GUARD 异常）
    目的：把 test 段封锁从口头铁律升级为数据库强制约束
    """
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys=ON')
    # ... 建表 DDL with CHECK constraint ...
    with pytest.raises(sqlite3.IntegrityError, match='CHECK constraint failed'):
        conn.execute("""
            INSERT INTO data_cut (cut_name, cut_date, purpose)
            VALUES ('test_leak_injection', '2024-03-01', 'research')
        """)
        conn.commit()
```

### 12.3 场景 12 追加：C2 静态检查断言

```python
def test_variant_count_not_in_dsr_callstack():
    """
    C2 断言：factor_registry.variant_count 不出现在任何 DSR N 计算调用栈
    实现：静态检查 research/ 目录下所有 .py 文件，
          断言不存在形如 variant_count ... N 或 n_eff 的表达式组合
    """
    import ast, pathlib
    violations = []
    for pyfile in pathlib.Path('research').rglob('*.py'):
        source = pyfile.read_text()
        if 'variant_count' in source:
            # 检查是否在 DSR/N_eff 计算上下文中
            if any(kw in source for kw in ['N_eff', 'n_eff_total', 'dsr', 'DSR']):
                violations.append(str(pyfile))
    assert not violations, f"variant_count 出现在 DSR 计算上下文: {violations}"
```

### 12.4 确定性重跑断言（贯穿所有场景）

```python
def assert_deterministic_rerun(query_fn, *args, **kwargs):
    """
    同一 data_cut_id 对同一 query_fn 执行两次，
    断言结果逐行一致（验证 canonical 四键排序的确定性）
    """
    result1 = query_fn(*args, **kwargs)
    result2 = query_fn(*args, **kwargs)
    pd.testing.assert_frame_equal(
        result1.reset_index(drop=True),
        result2.reset_index(drop=True),
        check_exact=True
    )
```

---

## 十三、点时正确性自检清单（v2.0）

本清单为上线前必过检查项，按章节组织。

### 13.1 修改 vs 撤销语义（§1）
- [ ] 修改追加 ACTIVE（旧行不动）/ 撤销追加 VOIDED，两者显式区分（F-1）
- [ ] 行情 VOIDED 进闸门单独告警，不静默丢数据（F-1）
- [ ] 绝不先 WHERE 过滤 VOIDED 再取最新（§1.2 通用规则）

### 13.2 复权基准（§2）
- [ ] 复权基准取数走 `visible_at <= as_of AND record_status = 'ACTIVE'`（F-2，废弃 announce_date）
- [ ] `event_ver` per-stock 唯一排序，UNIQUE(ts_code, event_ver)，VOIDED 不计入（F-2/P1-e）
- [ ] `validate_adj_coverage()` 校验每个除权事件有对应 PIT 序列且序列内基准唯一

### 13.3 canonical 查询（§3）
- [ ] canonical 排序固定四键（visible_at DESC, revision_seq DESC, snapshot_rank DESC, record_id DESC），重跑逐行一致（F-3）
- [ ] `snapshot_manifest` 有 source_priority/snapshot_rank 字段（F-3）
- [ ] `validate_adj_coverage` 纳入 `validate_data_gate.passed`（P1-e）

### 13.4 因子集（§4）
- [ ] `factor_variant ∈ {raw, processed, orthogonal}` 写死，CHECK 约束生效（F-4）
- [ ] `factor_snapshot.data_cut_id NOT NULL`（P1-b）
- [ ] `factor_registry.variant_count` 注释已更新为「点时去重/可复现审计用；不喂 DSR」（C2）
- [ ] DSR 的 N 唯一来源 `research_ledger atomic_test_id`；静态检查无 `variant_count` 进 N（C2）

### 13.5 财报/TTM（§5、§6）
- [ ] `limit_price` 为纯派生视图，`price_limit_rule_pit` 唯一运行时来源 + `get_limit_rule_asof()`（P1-a）
- [ ] `financials_pit.data_cut_id NOT NULL`；`data_cut.cut_date NOT NULL`（P1-b）
- [ ] `get_pit_ttm` 流量拼接/存量时点/预告区间聚合规则落地（P1-c）
- [ ] `financials_pit_asof` 每 stage 取最新 → VOIDED 不可用 → 优先级（P1-d）

### 13.6 data_cut 与不可变性（§7）
- [ ] `supersession_log` 标注仅审计；在线 canonical 查询绝不读此表
- [ ] `data_snapshot` PASS 即应用层锁，禁止 UPDATE/DELETE
- [ ] `data_cut` CHECK 约束：`purpose != 'research' OR cut_date < '2024-01-01'`（S3）
- [ ] `get_active_universe` 不做停牌过滤，停牌过滤交策略层

### 13.7 存储层（§8）
- [ ] PostgreSQL 相关代码/配置已全部移除
- [ ] DuckDB/Parquet 层：daily_bar_raw / adj_factor_pit / financials_pit / factor_snapshot 已落 Parquet 按年分区
- [ ] SQLite 层：WAL 模式 + foreign_keys=ON 已开启
- [ ] `adj_factor_pit` 按年分区，`build_pit_adj_factors` 有 batch_size 参数 + 进度回调
- [ ] 备份 rclone 配置已建立，周度恢复演练已排期

### 13.8 数据源三源（§9）
- [ ] AKShare 主采集管道已建，`visible_at = 当日 17:00`
- [ ] Tushare Pro 2000积分档对账管道已建
- [ ] BaoStock 冗余校验管道已建
- [ ] 三源两票制冲突裁决流程已实现，`source_priority.yaml` 已配置
- [ ] 北向相关字段已降级 P3（见附录 B）

### 13.9 visible_at 规则（§10）
- [ ] 日线行情 `visible_at = 当日 17:00+08:00`（盘后采集时间戳）
- [ ] 财报 `visible_at = ann_date` 下一交易日 `09:00+08:00`
- [ ] 新闻 `visible_at = t+1` 交易日 `09:00+08:00`（t-1 15:00 到 t 15:00 归 t+1）
- [ ] `visible_at_eod()` 和 `visible_at_next_open()` 函数已实现并单测覆盖

### 13.10 pytest 回归（§12）
- [ ] 13 场景回归测试全 PASS（含 data_cut 不可变性、确定性重跑一致、S3 封锁、C2 静态检查）
- [ ] pre-commit hook 已配置，任何 schema 修改触发 pytest 运行
- [ ] coverage ≥ 99% + 跨表参照 + `validate_adj_coverage` 通过

---

## 附录 A：二次筛查审查报告 v1.0 重建摘要

> **[基线缺失，据增量重建]**
>
> 原文《二次筛查审查报告 v1.0》未在文档库中留存完整版本。以下内容据 v1.2.2 引用上下文重建，仅供本契约条款引用使用。

### A.1 报告背景

本报告为 v1.2.1 封板后的受控解封审查，针对两项遗留语义风险进行专项审查：

- **C1**（已解）：factor_variant「三枚举」与「双因子集」措辞歧义——研究侧训练用 processed/orthogonal 双集，raw 仅供归因，文档措辞需与 QS-C01 §6.1 统一。
- **C2**（采纳）：`variant_count` 计数口径混入 DSR N，导致试验计数虚高，破坏 DSR 防过拟合护栏。
- **S3**（采纳）：test 段封锁仅口头约定，缺乏数据库级强制。

### A.2 C2 审查结论

**问题：** v1.2.1 注释「`variant_count`（喂DSR的N）按 version×variant 组合计数」导致 factor_variant 三枚举（raw/processed/orthogonal）各自作为独立 N 计入 DSR，使 N_eff 虚增约 3×，DSR 判断严重失真。

**裁决：** `factor_variant` 仅为 QS-C02 §3.5 atomic_test 的 `preprocessing` 维度标签，不独立乘入 N。DSR 的 N 唯一来源为 `research_ledger` 的 `atomic_test_id` 自动统计。

**影响范围：** `factor_registry.variant_count`（改为仅审计用）、第 13 场景静态检查断言。

### A.3 S3 审查结论

**问题：** test 段（2024-01-01 起）封锁仅为文档口头约定，无物理阻断。研究人员误建 `cut_date='2024-03-01'` 的研发期 data_cut 不会被系统自动拦截，存在 test 数据泄漏风险。

**裁决：** 在 `data_cut` 表加 CHECK 约束 `purpose != 'research' OR cut_date < '2024-01-01'`，pytest 第 13 场景强制验证。

---

## 附录 B：北向数据停披处置说明

### B.1 停披事件

**2024-08-19 起**，沪深港通北向资金个股持仓数据停止公开披露（AKShare `stock_hsgt_hist_em` 静默返回空）。

### B.2 影响评估

| 字段/数据类 | 历史数据（< 2024-08-19）| 实时数据（≥ 2024-08-19）| 处置 |
|------------|------------------------|------------------------|------|
| 北向个股持仓（ts_code 级） | 可用，已存 Parquet 归档 | **不可获取** | 降级 P3，标注 `data_available_until='2024-08-18'` |
| 北向前十大活跃股（汇总） | 可用 | 可用（保留汇总）| 保留 P3 |
| 北向资金净买入合计（市场级）| 可用 | 可用 | 保留 P2 |
| 沪港通/深港通 AH 价差 | 可用 | 可用 | 与策略无关，不单独维护 |

### B.3 「聪明钱」主线重定向

停披后，「聪明钱」信号主线更新为：

1. **龙虎榜**（AKShare 主采集 + Tushare 对账）——主机构席位净买入
2. **个股主力资金流**（AKShare 东财数据）——大单净流入
3. **股东户数变化**（Tushare Pro）——筹码集中度趋势

北向个股数据不再作为主线信号，历史数据（< 2024-08-19）仅用于 2016-2023 回测因子验证。

### B.4 schema 处置

```sql
-- 北向相关字段在 factor_snapshot 中降级为 P3
-- 新增字段标注：
--   data_available_until: 该字段数据可用截止日（北向个股：'2024-08-18'）
--   priority_tier: P3（不纳入主线因子筛选）

-- factor_registry 中北向相关因子标注示例：
INSERT OR REPLACE INTO factor_registry
  (factor_name, description, formula_version, variant_count)
VALUES
  ('northbound_holding_chg', '北向个股持仓变化（已降级P3）',
   'v1.0_deprecated', 3);
-- notes: 2024-08-19后无新数据，历史因子仅用于2016-2023回测段
```

### B.5 validate_data_gate 北向字段处理

```python
# 北向个股字段超过 2024-08-19 的 trade_date 时，validate_data_gate 不告警缺失
# 而是自动填充 NULL 并标注 quality_flag='REGULATORY_HALT'
NORTHBOUND_INDIVIDUAL_HALT_DATE = '2024-08-19'

def validate_northbound_field(ts_code, trade_date, field_value):
    if trade_date >= NORTHBOUND_INDIVIDUAL_HALT_DATE and field_value is None:
        return 'REGULATORY_HALT'   # 合规停披，非数据质量问题
    elif trade_date < NORTHBOUND_INDIVIDUAL_HALT_DATE and field_value is None:
        return 'MISSING'           # 真实数据缺失，需告警
    return 'OK'
```

---

*本契约 QS-C03 v2.0 为 QuantSolo 宪法文档第三份，合并 v1.2.1 + v1.2.2 全部条款并落实 SSOT 裁决。后续修订须递增版本号，提交 research_ledger 登记，并保证 13 场景 pytest 全 PASS。*

*编号 QS-C03 · 版本 v2.0 · 日期 2026-06-12 · QuantSolo 内部文档*
