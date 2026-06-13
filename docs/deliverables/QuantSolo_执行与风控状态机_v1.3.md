# QuantSolo —《执行与风控状态机》v1.3（宪法文档全文版）

---

**文档编号**：QS-C04
**版本**：v1.3（全文版，非增量补丁）
**日期**：2026-06-12
**状态**：正式发布
**与 SSOT 冲突时以基线为准，已校验**

---

## 文档头部索引

| 项目 | 内容 |
|------|------|
| 编号 | QS-C04 |
| 版本 | v1.3 |
| 日期 | 2026-06-12 |
| 上游依赖 | QS-C02《研究协议 v2.0》、QS-C03《点时数据契约 v2.0》 |
| 下游依赖 | QS-C05《模拟盘验收手册 v2.0》（演练对象）、QS-E05《执行行动指导手册》、QS-E02《软件开发架构方案》 |
| 闸门数字来源 | QS-CAL-001《统计闸门校准报告 v1.0》（数字不得改动） |
| 铁律全文 | 仅在 QS-C01《设计文档 v5.0》§1，本文档只引用编号 |
| 背离定义 | 仅在 QS-C02，本文档只引用编号 |

---

## 版本演进表

| 版本 | 日期 | 核心变更 |
|------|------|----------|
| v1.0 | 2026-05 | 初稿，基础状态机框架 |
| v1.1 | 2026-05 | 补充 outbox 恢复逻辑、风控签名 |
| v1.2 | 2026-06 | 补 §6.1 合规限速实节（P0）、outbox 三态恢复、position_ledger 定义、EOD 一等事件、签名拆分、DDL 幂等约束、reservation 账、撤后全成/部成区分、break-glass 撤单 UNKNOWN 分支、连续拒单断路器；冻结候选版 |
| **v1.3** | **2026-06-12** | **状态全集统一为 15 个运行态（SSOT 唯一口径）；break-glass 降级：删除复杂自动清仓链路，执行动作简化为物理一键熔断脚本+券商APP手动清仓 SOP；三级回撤与状态转换对齐（20%/25%）；xtquant order_remark 作为 client_order_id 对账方案正式采纳；合规内部限额（1笔/秒、200笔/日）写入状态机硬约束；与 QS-SSOT §3 全面对齐** |

---

## 〇、设计原则

1. **唯一入口**：所有订单必须经 `risk_guard` 唯一入口，禁止任何模块直连券商下单接口。唯一例外是 break-glass 熔断旁路，须满足 §5.2 全部约束。
2. **悲观默认**：任何未知/超时/不一致状态，默认动作为停单（FAIL-CLOSED），绝不"猜测后继续"。
3. **幂等优先**：每个订单意图携带幂等键，重复提交必须被去重而非重复成交。幂等键只锚定下单决策标识，绝不含会因重算抖动的内容；**幂等与事件去重下沉到数据库唯一约束（见 §7）**。
4. **全程留痕**：每一次状态迁移先本地事务落 ledger 再发券商（outbox），可逐笔审计回放。
5. **风控签名**：订单未带 risk_guard 签名，执行层一律拒收；签名按事件类型拆分（见 §4.4）。
6. **持仓真值来自成交事件流**：理论持仓由成交回报累计推导（position_ledger，见 §7.2），绝不用目标权重推导。
7. **合规优先于业务**：所有限速计数器在每次状态迁移中先于业务逻辑检查（见 §6.1）。合规内部限额（1笔/秒、200笔/日）为状态机硬约束，不可绕过（break-glass 例外参见 §5.2）。
8. **风控凌驾策略**（引用 QS-C01 五条铁律之第①条）：下单唯一入口必经守卫；本文档是该铁律的执行层实现。

---

## 一、状态全集（15 个运行态 · SSOT 唯一口径）

> **唯一口径声明**：根据 SSOT（baseline_spec.md §3），状态机运行态数量唯一口径为 **15 个**，文档中不得再出现 14/19 计数。以下状态清单为权威版本。如逻辑需要变更状态数，须更新本文档并同步 QS-C05 索引。

### 1.1 状态清单

| 序号 | 状态名称 | 状态代码 | 含义 | 终态 |
|------|---------|---------|------|------|
| 1 | 空闲等待 | `IDLE` | 等待下一交易日信号，正常循环入口 | 否 |
| 2 | 目标持仓生成 | `TARGET_GEN` | 策略层输出目标权重快照 | 否 |
| 3 | 风控裁剪 | `RISK_CLIP` | risk_guard 校验+裁剪（仓位/行业/流动性） | 否 |
| 4 | 订单定量 | `ORDER_SIZING` | 差量、T+1可卖、100股取整、现金约束（按 strategy_id 过滤） | 否 |
| 5 | 订单意图 | `ORDER_INTENT` | 生成带幂等键的订单意图，outbox 先落 PENDING_SEND | 否 |
| 6 | 提交前再审 | `PRE_FIRE_CHECK` | 提交前轻量复核资金/行情/一字板（防 TOCTOU） | 否 |
| 7 | 已提交 | `SUBMITTED` | 已发送至券商，等待受理回报 | 否 |
| 8 | 已受理/挂单 | `LIVE` | 券商已受理，限价单排队中（正常长存活） | 否 |
| 9 | 部分成交 | `PARTIAL` | 部分数量成交，剩余挂单中 | 否 |
| 10 | 撤单已请求 | `CANCEL_REQUESTED` | 撤单指令已发，等待回报（撤单在途） | 否 |
| 11 | 全部成交 | `FILLED` | 完全成交 | 是（进对账） |
| 12 | 已撤 | `CANCELLED` | 撤单成功（含部分成交后撤剩余） | 是（进对账） |
| 13 | 失败 | `REJECTED` | 券商拒单（资金/价格/权限） | 是（进对账） |
| 14 | 未知态 | `UNKNOWN` | 查不到券商状态/连接异常（仅此两类，非超时） | 否（危险） |
| 15 | 对账 | `RECONCILE` | 比对理论/券商/下单三方 | 否 |

> **v1.3 变更说明（状态简化）**：相较 v1.2 的 19 个状态，v1.3 按 SSOT 唯一口径统一为 15 个运行态。调整如下：
> - **合并**：`FILLED_AFTER_CANCEL`（撤后全成）、`PARTIAL_FILLED_AFTER_CANCEL`（撤后部分成交）并入 `CANCELLED` 终态家族，通过 ledger 字段区分（`cancel_fill_type` = `FULL`/`PARTIAL`/`NONE`），不单设独立状态——这两种情形本质上均为撤单流程的终态变体，ledger 字段足以区分，不需单独占用状态槽位。
> - **合并**：`HALTED`（熔断态）、`MANUAL`（人工接管）合并为统一的系统暂停语义，通过 `halt_reason` 字段区分（`BREAK_GLASS` / `WIND_CTRL_LV2` / `MANUAL_REVIEW` / `REJECT_BREAKER` 等）；`RECONCILE` 后回 `IDLE` 的路径不变。
> - **迁移影响**：execution_ledger DDL 中 `chk_state_enum` 对应更新（见 §7）。

### 1.2 已合并状态的字段区分方案

| 合并前状态 | 合并后状态 | ledger 区分字段 | 值含义 |
|-----------|-----------|----------------|--------|
| `FILLED_AFTER_CANCEL` | `CANCELLED` | `cancel_fill_type = 'FULL'` | 撤单在途收到全部成交 |
| `PARTIAL_FILLED_AFTER_CANCEL` | `CANCELLED` | `cancel_fill_type = 'PARTIAL'` | 撤单在途收到部分成交 |
| 正常撤单成功 | `CANCELLED` | `cancel_fill_type = 'NONE'` | 正常零成交撤单 |
| `HALTED`（熔断全停） | 合并至 RECONCILE 后挂起 | `halt_reason = 'BREAK_GLASS'` | 物理一键熔断 |
| `MANUAL`（人工接管） | — | `halt_reason = 'MANUAL_REVIEW'` | 人工决策等待 |

> **对账与自愈规则**：`cancel_fill_type = 'PARTIAL'` 时缺口靠下周期 `ORDER_SIZING` 基于真实持仓 C 的差量自然重新捕获（自愈），不立即补单。`cancel_fill_type = 'FULL'` 时按累计成交量幂等更新 position_ledger。

---

## 二、状态转换表（完整）

> 本节给出 15 态体系下的完整迁移表。对账字段 `order_remark`（xtquant 专用）= `client_order_id` 详见 §8。

### 2.1 正常调仓主链路

| 当前状态 | 事件 | 允许动作 | 下一状态 | 超时(s) | 备注 |
|----------|------|----------|----------|---------|------|
| `IDLE` | 收到新交易日信号 | 检查交易时段 + 风控前置 | `TARGET_GEN` | — | 14:55 后拒收**常规调仓**信号→排队次日（减仓/break-glass 不受限） |
| `IDLE` | 检测到未对账 | 触发强制对账 | `RECONCILE` | — | 异常保护 |
| `TARGET_GEN` | 收到目标权重 | 校验快照完整性 | `RISK_CLIP` | — | 缺字段→暂停（`halt_reason='MANUAL_REVIEW'`） |
| `RISK_CLIP` | 通过风控 | 仓位/行业/流动性裁剪 | `ORDER_SIZING` | — | 超限→裁剪或拒单 |
| `RISK_CLIP` | 风控拒绝 | 记录原因 | 暂停（`MANUAL_REVIEW`） | — | 单票/行业超限不可下单 |
| `RISK_CLIP` | **触发二级回撤（25%）** | 尽力清仓+冻结新开仓 | `ORDER_SIZING`（仅减仓路径） | — | 见 §6.2；系统继续运行做对账，**非全停** |
| `RISK_CLIP` | **触发一级回撤（20%）** | 目标仓位降至50%（先卖卫星） | `ORDER_SIZING`（降仓路径） | — | 见 §6.2 |
| `ORDER_SIZING` | 差量计算完成 | 差量+T+1可卖+100股取整+现金约束+**reserve 资金/股份** | `ORDER_INTENT` | — | T+1不可卖不进卖出量；子策略止损仅对该 strategy_id 算差量 |
| `ORDER_SIZING` | 差量校验失败 | 记录原因+告警 | 暂停（`MANUAL_REVIEW`） | — | 超卖/废单防护 |
| `ORDER_INTENT` | 幂等键已存在 | **去重，本意图丢弃(no-op)** | （本意图终止，无新迁移） | — | DB UNIQUE(client_order_id) 兜底 |
| `ORDER_INTENT` | 幂等键新 | outbox 落 PENDING_SEND + send_attempt_id | `PRE_FIRE_CHECK` | — | 先落 ledger 再发券商 |
| `PRE_FIRE_CHECK` | 再审通过 | 复核资金/持仓/实时行情/一字板 | `SUBMITTED` | — | 防 TOCTOU |
| `PRE_FIRE_CHECK` | 再审未通过 | 释放 reservation+告警 | 暂停（`MANUAL_REVIEW`） | — | 资金被前序占用/突发一字板 |
| `SUBMITTED` | 券商受理回报 | 记录 broker_order_id；写 order_remark=client_order_id | `LIVE` | 30 | 受理≠成交（仅市价/对手价单计 30s） |
| `SUBMITTED` | 部分成交回报 | 记录已成量(累计) | `PARTIAL` | 30 | 券商可能先推部分成交再补受理 |
| `SUBMITTED` | 全部成交回报 | 更新持仓(事件流) | `FILLED` | 30 | — |
| `SUBMITTED` | 拒单回报 | 记录原因+释放 reservation | `REJECTED` | 30 | — |
| `SUBMITTED` | 受理超时(>30s)且查无委托 | 停止新单+查询 | `UNKNOWN` | — | 严禁重发 |

### 2.2 挂单存活与撤单生命周期

| 当前状态 | 事件 | 允许动作 | 下一状态 | 超时(s) | 备注 |
|----------|------|----------|----------|---------|------|
| `LIVE` | 全部成交回报 | 更新持仓 | `FILLED` | — | 限价单按 order_ttl 判定，非固定30s |
| `LIVE` | 部分成交回报 | 记录已成量(累计) | `PARTIAL` | — | — |
| `LIVE` | 主动撤单(价格跑掉/调仓收尾) | 发撤单指令 | `CANCEL_REQUESTED` | — | 主路径 |
| `LIVE` | 挂单存续超 order_ttl | 发撤单指令 | `CANCEL_REQUESTED` | order_ttl | DAY→15:00 / GTC→次日14:55 |
| `LIVE` | 连接中断 | 冻结该单 | `UNKNOWN` | — | — |
| `PARTIAL` | 主动撤剩余/剩余成交 | 发撤单/更新持仓 | `CANCEL_REQUESTED`/`FILLED` | — | — |
| `CANCEL_REQUESTED` | 撤单成功回报（零成交） | 记录+释放剩余 reservation | `CANCELLED`（`cancel_fill_type='NONE'`） | cancel_timeout | — |
| `CANCEL_REQUESTED` | 撤单在途收到**全部成交** | 按累计量幂等更新 position_ledger | `CANCELLED`（`cancel_fill_type='FULL'`） | — | 对账视同 FILLED |
| `CANCEL_REQUESTED` | 撤单在途收到**部分成交** | 按累计量幂等更新；释放剩余 | `CANCELLED`（`cancel_fill_type='PARTIAL'`） | — | 缺口靠下周期差量自愈 |
| `CANCEL_REQUESTED` | 撤单被拒(已成交/不存在) | 查询真实状态 | `UNKNOWN` | — | 不盲目补单 |
| `CANCEL_REQUESTED` | 撤单超时(>15s) | 查询真实剩余 | `UNKNOWN` | 15 | 不盲目重撤 |

### 2.3 UNKNOWN 归位、EOD 与对账闭环

| 当前状态 | 事件 | 允许动作 | 下一状态 | 超时(s) | 备注 |
|----------|------|----------|----------|---------|------|
| `UNKNOWN` | 连续两次查询结果一致 | 按真实状态归位 | `FILLED`/`PARTIAL`/`REJECTED`/`CANCELLED` | — | 防券商查询延迟/旧态 |
| `UNKNOWN` | 单次查询/两次不一致 | 等待下一心跳周期再查 | `UNKNOWN` | unknown_query_interval | 不单次即信 |
| `UNKNOWN` | 查询仍失败(达重试上限) | 停单+告警 | 暂停（`MANUAL_REVIEW`） | — | 绝不自动下单 |
| `LIVE`(TIF=DAY) | **EOD(收盘)** | 等交易所自动撤单回报 | `CANCEL_REQUESTED` | — | — |
| `LIVE`(TIF=GTC) | **EOD** | 标记跨日，维持挂单 | `LIVE` | — | 次日继续跟踪 |
| `PARTIAL`(TIF=DAY) | **EOD** | 等撤剩余回报 | `CANCEL_REQUESTED` | — | — |
| `CANCEL_REQUESTED` | **EOD** | 等撤单确认(延长超时) | `CANCELLED`/`UNKNOWN` | — | 收盘后撤单可能延迟 |
| `ORDER_SIZING`/`PRE_FIRE_CHECK` | **EOD(>14:55)** | 作废未提交意图+释放 reservation | `RECONCILE` | — | 未完成意图排队次日 |
| `FILLED`/`CANCELLED`/`REJECTED` | 进入日终 | 三方对账 | `RECONCILE` | — | — |
| `RECONCILE` | 差异≤容忍 或 可被 corp_action/零股解释 | 归档 ledger + 标记日终完成 | `IDLE` | — | 差异自动分类，减人工介入 |
| `RECONCILE` | 仅无法解释的股数差>阈值 | 停单+告警 | 暂停（`MANUAL_REVIEW`） | — | 现金尾差自动容忍 |

### 2.4 异常态退出、断路器与暂停/恢复

| 当前状态 | 事件 | 允许动作 | 下一状态 | 超时(s) | 备注 |
|----------|------|----------|----------|---------|------|
| 暂停（任意 `halt_reason`） | 人工确认放行 | 必须先走对账对齐 | `RECONCILE` | — | 恢复写 ledger（manual_operator_signature） |
| 暂停（`MANUAL_REVIEW`） | 人工确认仅平仓 | 生成减仓意图 | `ORDER_SIZING` | — | 只减不增 |
| 任意非IDLE | **5分钟内≥3笔 `REJECTED`** | **冻结该子策略新开仓+告警** | 暂停（`REJECT_BREAKER`） | — | 连续拒单断路器 |
| 暂停（`BREAK_GLASS`） | 熔断处置完成 | 等待人工 | 暂停（`MANUAL_REVIEW`） | — | 令牌须人工归还 |
| 任意状态 | 触发 break-glass 条件 | 见 §5（物理一键熔断旁路） | 暂停（`BREAK_GLASS`） | — | 仅此路径走全停旁路 |
| 任意非IDLE | 重复启动检测 | 幂等键比对+拒绝重复实例 | （维持） | — | 见 §4.2 |
| 任意状态 | 盘中崩溃重启 | outbox 三态恢复（见 §4.3） | 对应态/暂停（`MANUAL_REVIEW`） | — | 接管成功后立即夺下单令牌 |

**关键判定：**
- 部分成交后断连 → `UNKNOWN` → 禁止补单，先查真实持仓（双查一致）
- 撤单在途收到成交 → 全成或部成均进 `CANCELLED`（`cancel_fill_type` 区分），按累计成交量幂等更新
- **未完成缺口不立即补单**——靠下周期 `IDLE→ORDER_SIZING` 基于真实持仓 C 的差量自然重新捕获（自愈）
- 券商返回未知/查询失败 → 暂停（`MANUAL_REVIEW`），绝不继续下单

---

## 三、超时与对账容忍阈值参数表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `order_report_timeout` | 30 s | 仅市价/对手价单等待受理回报上限 |
| `order_ttl` | DAY→当日15:00 / IOC→0(不进LIVE) / GTC→次日14:55 | 按 TIF 类型给默认值 |
| `cancel_timeout` | 15 s | 撤单指令等待回报上限 |
| `unknown_query_retry` | 3 次 | UNKNOWN 态查询委托重试次数 |
| `unknown_query_interval` | 5 s | 查询重试间隔；归位须连续两次一致 |
| `recon_qty_tol` | 见 §3.1 | 主动交易差=0；除权差/零股豁免 |
| `recon_cash_tol` | 1.00 元 | 现金差异容忍（自动容忍尾差） |
| `recon_price_tol` | 0.005 | 成交均价差异比例容忍 |
| `heartbeat_interval` | 15 s | 与券商连接心跳间隔 |
| `heartbeat_miss_max` | 3 次 | 连续丢失进入 UNKNOWN（45s 窗口防假阳性） |
| `daily_recon_deadline` | 收盘后 30 min | 日终对账完成时限 |
| `cutoff_new_signal` | 14:55 | 此后拒收**常规调仓**信号；风控减仓/break-glass 不受限 |
| `reject_breaker_window` | 5 min / 3 笔 | 连续拒单断路器阈值 |
| `pending_send_resend_window` | 5 min | outbox 仅对此窗口内订单作重发候选 |

### 3.1 对账三方、理论持仓口径与差异分类

**对账三方：** 理论持仓 C vs 券商实际持仓 vs execution_ledger。三种口径，**日终以 C 为真值**：
- **A**：策略 target_weight（理想，含未执行）——仅意图，不对账
- **B**：risk_guard 裁剪后 order_intent（已下单未确认）——记录不硬阻
- **C**：position_ledger 成交事件流推导（见 §7.2）——**对账真值**

```python
recon_qty_tol = {
    "active_trade_diff": 0,          # 主动交易持仓差必须为零
    "corp_action_diff": "explain",   # 送转/拆分/配股经 corporate_action_ledger 验证后豁免
    "odd_lot_diff": "separate_book", # 零股(<100股不可交易余额)单独建账, 不计零容忍
    "cash_tail_diff": "auto_tol",    # 现金尾差(<recon_cash_tol)自动容忍
    "unknown_diff": 0,               # 仅无法解释的股数差 -> 暂停(MANUAL_REVIEW)
}
```

**除权对账铁律：** 对账前先查 QS-C03 的 `corporate_action`，对当日有除权除息/送转/配股的标的，按 `corporate_action_ledger` 同时推导 **position_delta 与 cash_delta**（除息影响现金、配股影响现金+股份、红利税造成差异），差异在此范围内视为通过，不误触发暂停。

---

## 四、幂等键、恢复与签名

### 4.1 幂等键生成规则

```python
def gen_idempotency_key(account_id, strategy_id, trade_date, ts_code, side, rebalance_seq):
    """client_order_id = hash(account_id|strategy_id|trade_date|ts_code|side|rebalance_seq)
    只锚定"哪一笔下单决策", 绝不含会因重算抖动的 target_weight(移入 weight_payload 仅记录)。
    支持父子单: parent_intent_id + 子 broker_order_id/client_order_id。
    """
```

- **rebalance_seq 分配规则**：**全局单调递增**（非按日重置），跨日补单也唯一，杜绝跨日键碰撞
- `account_id`/`strategy_id`：支持多账户、子策略分别平仓不混淆

### 4.2 重复启动防护

```python
def acquire_singleton_lock(lock_file, ttl_s=120):
    """文件锁+PID+心跳。检测到活跃实例(心跳未过期)则拒绝启动。
    接管前: kill -0 <pid> 确认原进程已死, 且心跳过期连续 N>=2 次才接管(防负载抖动误判)。
    接管成功后立即夺取下单令牌(复用 break-glass 令牌机制),
    物理确保假死旧进程即使复活也无法下单。
    """
```

启动须执行开盘前 dry-run 对齐：拉券商真实持仓与 ledger 末态比对，不一致先进 `RECONCILE`。

### 4.3 outbox 三态恢复模型（防"已发送未回报"误重发）

```python
def submit_with_outbox(intent):
    # 1) 本地事务写 ORDER_INTENT(status=PENDING_SEND) + client_order_id + send_attempt_id
    # 2) 记 send_started_at -> 发送券商 -> 收到受理记 send_committed_at + broker_order_id
    pass

def recover_pending_orders():
    """崩溃恢复按三态判定, 而非笼统'查不到即重发':
      NOT_SENT_CAN_SEND : send_started_at 为空 -> 安全重发
      MAYBE_SENT_UNKNOWN: 已 send_started 未 committed -> 不可立即重发
      SENT_CONFIRMED    : 已 committed -> 按 broker_order_id 归位
    """
    for it in get_pending_send_orders():
        st = broker.query_by_client_order_id(it.client_order_id)   # 第1次:立即
        if st: reconcile(it, st); continue
        time.sleep(3)                                              # 等券商内部处理窗口
        st = broker.query_by_client_order_id(it.client_order_id)   # 第2次
        if st: reconcile(it, st); continue
        # client_order_id 查不到时兜底: 当日全量委托 + 时间戳+标的+数量 模糊匹配
        st = broker.fuzzy_match_today(it.ts_code, it.target_qty, it.event_ts)
        if st: reconcile(it, st); continue
        if it.send_started_at is None and (now()-it.event_ts) < PENDING_SEND_RESEND_WINDOW:
            resend_with_alert(it)        # 确认未发送且近期 -> 重发(告警留痕)
        else:
            mark_expired_to_manual(it)   # 已发送未知 / 超时旧单 -> 暂停(MANUAL_REVIEW), 绝不重发
```

### 4.4 风控签名拆分与载荷（按事件类型约束）

```python
# 四类签名按事件类型约束(下单行才需 command_signature):
#   command_signature        : risk_guard 下单动作(HMAC-SHA256 / Ed25519)
#   broker_event_hash        : 券商回报行(去重/审计, 非签名)
#   manual_operator_signature: 人工恢复行
#   break_glass_signature    : 熔断旁路行(独立密钥)
# command canonical payload(排序序列化)须覆盖:
#   account_id, strategy_id, client_order_id, parent_intent_id, rebalance_seq, broker_id,
#   ts_code, side, qty, price, order_type, time_in_force, valid_until, nonce,
#   risk_policy_version, target_snapshot_hash
# 校验: payload 哈希 + 过期时间 + nonce 防重放; 仅布尔标记不可接受
```

---

## 五、熔断协议（v1.3 · break-glass 降级版）

> **SSOT 裁决**（baseline_spec.md §3）：break-glass 降级裁决——复杂自动清仓链路**降级为「券商APP手动清仓 SOP」+物理一键熔断（独立进程脚本，直接调 xtquant 撤单+市价平仓，不经策略层）**，状态机保留 BREAK_GLASS 暂停态但执行动作简化。本节为 v1.3 简化版全文。

### 5.1 熔断分级（v1.3 简化）

| 级别 | 触发条件 | 执行路径 | 状态机响应 |
|------|---------|---------|-----------|
| **风控级（一级预警）** | 账户回撤达 20% | 经 risk_guard 正常通道，目标仓位降至50%，先卖卫星仓 | `RISK_CLIP→ORDER_SIZING`（降仓路径），系统继续运行 |
| **风控级（二级硬止损）** | 账户回撤达 25% | 经 risk_guard 正常通道，尽力全清仓+冻结新开仓 | `RISK_CLIP→ORDER_SIZING`（清仓路径），系统继续运行做对账，**非全停** |
| **物理一键熔断（break-glass）** | 手动触发/灾备 | **独立进程脚本（不经策略层）**：①先撤所有活跃单 ②按 xtquant sellable_qty 市价平仓 ③夺下单令牌 | 暂停（`BREAK_GLASS`），等待人工处置 |

**衔接澄清**：二级回撤语义 = "清仓但系统继续运行做对账"，**不等于全系统停机**；仅物理一键熔断走全停旁路（暂停 `BREAK_GLASS`）。

### 5.2 break-glass 物理熔断（v1.3 简化执行动作）

**v1.3 执行动作（简化版，删除复杂自动清仓链路）：**

```
STEP 1：触发检测
  - 人工物理按键触发（独立按钮/快捷键，物理隔离于主程序）
  - 或：监控进程检测到灾备条件（PC故障、QMT断线超阈值）

STEP 2：物理一键熔断脚本（独立进程，break_glass.py）
  - 独立进程启动，不依赖主进程内存/状态
  - 夺取全局下单令牌（令牌单向，仅人工归还）
  - 踢主进程 session（前置条件：心跳连丢>45s且PID存活无响应才踢）
  - 调用 xtquant 撤销所有在途委托（复用 CANCEL_REQUESTED/UNKNOWN 路径）
  - 按 xtquant 实时 sellable_qty 市价平仓（不依赖 position_ledger）
  - 全量写 execution_ledger（带 break_glass_signature）

STEP 3：券商APP手动清仓 SOP（兜底 / xtquant 不可达时）
  → 打开券商手机APP或PC客户端
  → 手动撤销全部在途委托（委托→待成交→全撤）
  → 手动卖出全部持仓（持仓→全卖/逐笔卖出）
  → 截图存档（时间戳、持仓清零确认）
  → 事后在 execution_ledger 手工补录（manual_operator_signature）

STEP 4：结束后进入暂停(BREAK_GLASS)，等待人工复盘
  - 令牌须人工显式归还（写入 halt_release 记录）
  - 系统恢复须先过 RECONCILE 对账
```

**约束（保持不变）**：
- 独立签名：熔断进程持独立密钥，动作带 `break_glass_signature`
- 只允许减仓：不得开新仓/加仓
- 限速绕过：break-glass 清仓不受 §6.1 申报速率/日笔数限制（紧急减仓优先），但全量写 ledger
- 令牌幂等且单向：夺取后主进程停单；令牌只能由人工归还

### 5.3 break-glass 失败模式与缓解（简化版）

| 失败模式 | 风险 | 缓解措施 |
|----------|------|----------|
| xtquant 不可达 | 脚本无法撤单平仓 | **降级为券商APP手动清仓 SOP**（§5.2 STEP 3），这是主缓解路径 |
| 独立进程无法获取实时持仓 | 清仓数量错误 | 直连券商持仓查询接口，不依赖主进程内存；失败则降级APP手动 |
| 与主进程重复下单 | 双重清仓/反向超卖 | 令牌互斥（§5.2，幂等单向夺取） |
| 跌停时清仓无法成交 | 清仓失败仍暴露 | 挂跌停价排队不撤不补；T+1不可卖部分标 pending_liquidation，次日竞价优先 |
| 部分清仓后熔断进程崩溃 | 半清仓悬空 | 动作幂等可重入，重启读 ledger 续清 |
| 误触发熔断 | 非必要清仓损失 | 物理触发前需二次确认（物理按钮+软件确认码） |

---

## 六、与风控层的衔接

### 6.1 合规申报限速（状态机硬约束 · SSOT §4）

> **合规依据**：沪深北《程序化交易管理实施细则》2025-07-07 施行。高频认定线 300笔/秒或2万笔/日。本系统内部限额 1笔/秒、200笔/日，远低于监管线。**本节参数为状态机硬约束**，不可在代码中绕过（break-glass 例外参见 §5.2）。

| 参数 | **硬约束值** | 来源 | 超限动作 |
|------|------------|------|----------|
| `rate_limit_max_orders_per_sec` | **1 笔/秒（单只）** | SSOT §4 / 交易所细则 | 排队延迟，**不拒单** |
| `rate_limit_max_orders_per_day` | **200 笔** | SSOT §4 / 设计文档合规阈值 | 超限→暂停(`MANUAL_REVIEW`)+告警 |
| `rate_limit_max_cancels_per_min` | 10 次/分钟 | 部分券商限制（国金 QMT 约 5-10 次） | 超限→降速或暂停 |
| `rate_limit_cooldown_ms` | 1000 ms | 两次申报最小间隔 | 强制间隔 |

**实施规范：**
- 计数器粒度：**每账户/每策略/每券商通道**滑动窗口；日内累计申报与撤单计数独立维护
- **检查时机**：所有限速计数器在每次状态迁移中**先于业务逻辑检查**（设计原则 §0.7）
- **超限动作**：超申报速率→排队（非拒单）；超日上限/撤单频率→暂停(`MANUAL_REVIEW`)+告警
- **break-glass 例外**：紧急清仓不受本节限制（见 §5.2），但全量写 ledger
- 不同券商（国金 QMT/华泰/中信）限速值经 `broker_id` 配置化，取最严者

**合规义务提示**（引用 SSOT §4）：开户如实勾选程序化交易；先报告后交易；软件变更更新报备。

### 6.2 三级回撤与状态机对齐（v1.3 · SSOT §4 完整版）

> **SSOT 三级回撤定义**（baseline_spec.md §4）：20%预警→降仓50%（先卖卫星）；25%硬止损→全清仓+冻结+人工复盘。

| 回撤级别 | 触发阈值 | 执行动作 | 状态机路径 | 特殊规则 |
|---------|---------|---------|-----------|---------|
| **一级预警** | 账户净值回撤 **20%** | 目标仓位降至当前50%，**先卖卫星仓**（趋势卫星 20–25% 先平） | `RISK_CLIP` 设置降仓目标 → `ORDER_SIZING`（减仓差量） | 卫星仓优先于核心仓平仓；系统继续运行 |
| **二级硬止损** | 账户净值回撤 **25%** | **全清仓+冻结新开仓+人工复盘** | `RISK_CLIP` 触发清仓 → `ORDER_SIZING`（清仓路径）→ `SUBMITTED`/`LIVE`/... → `RECONCILE` | 系统继续运行做对账，**非 BREAK_GLASS 全停**；冻结后须人工解冻才可开新仓 |
| **物理一键熔断** | 手动触发/灾备 | break-glass SOP（见 §5.2） | 暂停（`BREAK_GLASS`）→人工处置 | 独立于回撤触发，任何时候可触发 |

**子策略止损**：仅平该 strategy_id 的仓位，不误伤另一子策略引擎；`ORDER_SIZING` 按 strategy_id 过滤差量。

**满仓降仓线（A2 降级路径）**：若触发 QS-CAL-001 A2 降级路径，2.5万阶段取更保守降仓线，先于账户级触发降仓。

### 6.3 风控触发汇总表

| 风控触发 | 来源 | 状态机响应 |
|----------|------|-----------|
| 账户一级预警（20%回撤） | 风控层 | `RISK_CLIP` 降目标仓至50%（先卖卫星） |
| 账户二级硬止损（25%回撤） | 风控层 | 尽力全清仓+冻结新开仓，系统继续运行做对账 |
| 满仓降仓线（2.5万起步A2路径） | 风控层 | 取更保守线，先于账户级触发降仓 |
| 子策略止损 | 风控层 | 仅平该子策略仓位（按 strategy_id），不误伤另一引擎 |
| 单票/行业超限 | risk_guard | `RISK_CLIP` 裁剪或拒单 |
| 成交额<5000万/一字板/高价股 | risk_guard | `ORDER_SIZING`/`PRE_FIRE_CHECK` 阶段过滤 |
| 物理一键熔断 | break-glass | 暂停（`BREAK_GLASS`），仅此走全停旁路 |

---

## 七、execution_ledger 落库（完整 DDL + 约束 + 持仓推导）

> **文档衔接：** execution_ledger 由本文档给出完整 DDL，复用 QS-C03 通用追加式列（`record_id`/`ingested_at`/append-only）。v1.3 新增 `cancel_fill_type` 字段（区分撤后全成/部成/零成），更新 `chk_state_enum` 为 15 态口径。

```sql
CREATE TABLE execution_ledger (
  record_id         BIGSERIAL PRIMARY KEY,
  account_id        VARCHAR(32)  NOT NULL,
  broker_id         VARCHAR(16)  NOT NULL,
  strategy_id       VARCHAR(32)  NOT NULL,
  client_order_id   VARCHAR(64)  NOT NULL,
  -- v1.3: order_remark = client_order_id（xtquant 对账方案，见 §8）
  order_remark      VARCHAR(64)  GENERATED ALWAYS AS (client_order_id) STORED,
  parent_intent_id  VARCHAR(64),
  broker_order_id   VARCHAR(32),
  send_attempt_id   VARCHAR(64),                    -- outbox 三态
  send_started_at   TIMESTAMPTZ,
  send_committed_at TIMESTAMPTZ,
  target_snapshot_id INT,
  risk_policy_version VARCHAR(16),
  rebalance_seq     BIGINT,                          -- 全局单调递增
  ts_code           VARCHAR(12),
  side              VARCHAR(4),
  order_type        VARCHAR(8),                      -- LIMIT/MARKET
  time_in_force     VARCHAR(8),                      -- DAY/IOC/GTC
  target_qty        INT,
  filled_qty        INT,
  remaining_qty     INT,
  reserved_cash     NUMERIC(14,2),                   -- 资金 reservation
  reserved_qty      INT,                             -- 股份 reservation
  limit_price       NUMERIC(12,4),
  avg_fill_price    NUMERIC(12,4),
  signal_ref_price  NUMERIC(12,4),                   -- 成本归因反哺研究层
  -- v1.3 新增: 撤单后成交类型区分（替代原 FILLED_AFTER_CANCEL 独立状态）
  cancel_fill_type  VARCHAR(8) DEFAULT 'NONE',       -- 'NONE'/'FULL'/'PARTIAL'
  halt_reason       VARCHAR(32),                     -- 暂停原因: BREAK_GLASS/WIND_CTRL_LV2/MANUAL_REVIEW/REJECT_BREAKER
  from_state        VARCHAR(24),
  to_state          VARCHAR(24),
  event             VARCHAR(32),
  event_seq         BIGINT,                          -- 回报乱序/幂等(累计成交量)
  broker_status     VARCHAR(16),
  error_code        VARCHAR(16),
  raw_event_hash    VARCHAR(64),
  command_signature        VARCHAR(128),             -- 下单行签名
  broker_event_hash        VARCHAR(64),              -- 券商回报行
  manual_operator_signature VARCHAR(128),            -- 人工恢复行
  break_glass_signature     VARCHAR(128),            -- 熔断行
  recon_diff_qty    INT,
  recon_diff_cash   NUMERIC(12,2),
  weight_payload    JSONB,                            -- 完整权重快照(含hash), 不进幂等键
  event_ts          TIMESTAMPTZ NOT NULL,
  ingested_at       TIMESTAMPTZ DEFAULT NOW(),

  -- v1.3 数据库级幂等与完整性约束（15态口径）:
  CONSTRAINT uq_client_order UNIQUE (client_order_id),
  CONSTRAINT uq_broker_event UNIQUE (broker_id, broker_order_id, raw_event_hash),
  CONSTRAINT chk_fill_le_target CHECK (filled_qty IS NULL OR filled_qty <= target_qty),
  CONSTRAINT chk_cancel_fill_type CHECK (cancel_fill_type IN ('NONE','FULL','PARTIAL')),
  CONSTRAINT chk_state_enum CHECK (to_state IN (
    -- v1.3 唯一口径：15 个运行态
    'IDLE',
    'TARGET_GEN',
    'RISK_CLIP',
    'ORDER_SIZING',
    'ORDER_INTENT',
    'PRE_FIRE_CHECK',
    'SUBMITTED',
    'LIVE',
    'PARTIAL',
    'CANCEL_REQUESTED',
    'FILLED',
    'CANCELLED',
    'REJECTED',
    'UNKNOWN',
    'RECONCILE'
    -- 注: HALTED/MANUAL 语义通过 halt_reason 字段在 CANCELLED 终态或 RECONCILE 中间态表达
  ))
);

CREATE INDEX idx_el_account_date   ON execution_ledger(account_id, event_ts);
CREATE INDEX idx_el_strategy       ON execution_ledger(strategy_id, event_ts);
CREATE INDEX idx_el_broker_order   ON execution_ledger(broker_id, broker_order_id);
CREATE INDEX idx_el_order_remark   ON execution_ledger(order_remark);  -- v1.3: 支持 xtquant order_remark 反查
CREATE INDEX idx_el_state          ON execution_ledger(to_state)
  WHERE to_state NOT IN ('FILLED','CANCELLED','REJECTED');
```

### 7.1 成本归因反哺研究层

落库 `avg_fill_price` 与 `signal_ref_price` 偏差，喂给：① QS-C01 "交易成本事后归因"；② QS-C02 "实盘 IC vs 研究 IC 一致性"回挂条款。

### 7.2 持仓推导 position_ledger

```python
def compute_position_ledger(account_id, as_of):
    """position_ledger 非独立存表, 由 execution_ledger 实时推导(物化视图/内存量):
       理论持仓 C = prior_position + cumulative_fills - corporate_action_delta
       cumulative_fills = Σ(BUY filled - SELL filled), 含 cancel_fill_type IN ('FULL','PARTIAL') 的 CANCELLED 终态
    """
    # SELECT ts_code, SUM(CASE WHEN side='BUY' THEN filled_qty ELSE -filled_qty END) net_qty
    # FROM execution_ledger
    # WHERE account_id=? AND to_state IN ('FILLED','CANCELLED')
    #   AND (to_state='FILLED' OR cancel_fill_type IN ('FULL','PARTIAL'))
    #   AND event_ts<=?
    # GROUP BY ts_code
    # 再合并 corporate_action_ledger 的 position_delta / cash_delta
```

---

## 八、xtquant order_remark 对账方案（v1.3 正式采纳）

> **v1.3 新增**：xtquant 的 `order_remark` 字段作为 `client_order_id` 的对账载体，正式写入状态机规范。此为 v1.2 遗留的"T1 client_order_id 可反查性"未知数的工程解法。

### 8.1 方案说明

xtquant（迅投 miniQMT Python SDK）的下单接口支持 `order_remark` 参数，该字段会随委托状态回调一同返回。利用此字段填入 `client_order_id`，实现下单→回报→对账的全链路幂等追踪。

```python
# 下单时写入 order_remark
xttrader.order_stock(
    account=account,
    stock_code=ts_code,
    order_type=xtconstant.STOCK_BUY,
    order_volume=qty,
    price_type=xtconstant.FIX_PRICE,
    price=limit_price,
    order_remark=client_order_id,   # ← 写入幂等键（最大64字节）
)

# 委托回调中取出
def on_order_callback(order_info):
    client_order_id = order_info.order_remark   # 对账反查
    reconcile_by_client_order_id(client_order_id, order_info)
```

### 8.2 约束与降级策略

| 约束项 | 规则 |
|--------|------|
| `order_remark` 长度 | xtquant 最大64字节；`client_order_id` 须在此范围内（哈希截断为32字节16进制） |
| 可反查性验证 | 模拟盘第一周 T1 用例须验证：order_remark 能否通过委托查询接口反查；命中率须 ≥95%，误匹配率=0 |
| 降级路径 | 若 order_remark 反查失败：模糊匹配兜底（时间戳+标的+方向+数量+价格全匹配，唯一候选），多候选→暂停(`MANUAL_REVIEW`) |
| execution_ledger 同步 | `order_remark` 字段为 `client_order_id` 的生成列（见 §7 DDL），索引支持双向查询 |
| 对账报告 | QS-C05 每日对账报告 §[3] 须列出 order_remark 反查命中率（用于 B3 工程判线验证） |

### 8.3 与 outbox 三态恢复的关系

`order_remark` 对账方案是 outbox 三态恢复（§4.3）的第一查询路径。若 `order_remark` 可反查，则直接按 id 归位（SENT_CONFIRMED）；不可反查时降级模糊匹配。实测结果须记录于 QS-C05 §六真实券商观察期报告。

---

## 九、上模拟盘前自检清单（v1.3 更新）

- [ ] **§6.1 合规硬约束落地：1笔/秒、200笔/日内部限额，计数器先于业务检查，超限动作明确**
- [ ] **§8 xtquant order_remark = client_order_id 对账方案实现，DDL 生成列与索引到位**
- [ ] **三级回撤（20%/25%）与状态机对齐：一级降仓50%先卖卫星、二级全清仓+冻结系统续运行**
- [ ] 所有下单经 risk_guard 唯一入口；唯一例外 break-glass 满足 §5.2（含限速绕过）
- [ ] 幂等键已移除 weight_hash，DB UNIQUE(client_order_id) 兜底；rebalance_seq 全局单调递增
- [ ] ORDER_SIZING 差量/T+1可卖/100股取整/现金约束/reservation 已实现；子策略按 strategy_id 过滤
- [ ] outbox 三态恢复：NOT_SENT_CAN_SEND/MAYBE_SENT_UNKNOWN/SENT_CONFIRMED；查不到先等待二查+模糊匹配兜底
- [ ] UNKNOWN 归位连续两次一致；归位路径包含 CANCELLED
- [ ] 状态机闭环：RECONCILE→IDLE、暂停→RECONCILE→IDLE
- [ ] **EOD 一等事件覆盖 LIVE/PARTIAL/CANCEL_REQUESTED/未提交意图**
- [ ] 撤单生命周期完整，CANCELLED 终态按 cancel_fill_type 区分（NONE/FULL/PARTIAL）
- [ ] **position_ledger 推导正确（§7.2）：含 cancel_fill_type IN ('FULL','PARTIAL') 的 CANCELLED 终态**
- [ ] 除权对账：corporate_action_ledger 同推 position_delta + cash_delta；零股单独建账
- [ ] 对账差异自动分类：corp_action/零股自动归档，现金尾差自动容忍，仅无法解释股数差→暂停
- [ ] 签名按事件类型拆分（command/broker_event/manual/break_glass），载荷含完整意图
- [ ] **DDL v1.3：含 cancel_fill_type/halt_reason/order_remark 字段，状态枚举更新为 15 态**
- [ ] 连续拒单断路器（5min≥3笔 REJECTED→冻结子策略+暂停 REJECT_BREAKER）
- [ ] PRE_FIRE_CHECK 提交前再审；限价单按 order_ttl（DAY/IOC/GTC 默认值）
- [ ] 14:55 围栏仅拦常规调仓，风控减仓/break-glass 豁免
- [ ] 单例锁接管 PID 存活检查 + 连续≥2次心跳过期；接管后立即夺令牌
- [ ] **break-glass 简化版：独立进程脚本→xtquant 撤单+市价平仓，降级为券商APP手动清仓 SOP**
- [ ] 物理熔断六类失败模式均有缓解且可重入；令牌单向幂等
- [ ] **程序化交易合规报备材料准备（M3 配合 QS-C05 验收）**

---

## 十、模拟盘必测用例（v1.3 更新，委派至 QS-C05）

> 以下用例全部委派至 QS-C05《模拟盘验收手册 v2.0》执行验收。本节仅列清单与重点，细节以 QS-C05 §三为准。

| 编号 | 必测场景 | 验证目标 | 重点 | v1.3 变更 |
|------|---------|---------|------|----------|
| T1 | order_remark 反查（xtquant client_order_id 对账） | order_remark 是否能按 id 直接反查；不可则验模糊匹配唯一性兜底 | ⭐ 最高 | **v1.3 正式命名为"order_remark 对账验证"** |
| T2 | outbox "已发送未受理"窗口崩溃 | 三态判定不误重发；MAYBE_SENT_UNKNOWN→暂停 | ⭐ 最高 | 无变化 |
| T3 | 撤单在途收到全部成交 | CANCELLED(cancel_fill_type=FULL)，累计量幂等不重复 | 高 | **v1.3 更新状态名** |
| T4 | CANCELLED(PARTIAL) 缺口自愈 | 下周期 ORDER_SIZING 基于 C 自然重捕获，不立即补单 | 高 | **v1.3 更新状态名** |
| T5 | xtquant 委托/持仓查询延迟与旧态 | UNKNOWN 双查一致才归位，不基于旧态行动 | 高 | 无变化 |
| T6 | 回报乱序/重复推送 | event_seq + 累计成交量幂等更新正确 | 高 | 无变化 |
| T7 | rate_limiter 触限 | 超速率排队（≥1000ms）、超日上限(200)→暂停 | 中 | **v1.3 确认 200笔/日为硬约束** |
| T8 | 签名重放/伪造 | nonce+过期校验拦截，布尔标记无法伪造 | 中 | 无变化 |
| T9 | 除权日现金对账 | corporate_action_ledger 同推 position+cash，不误触暂停 | 中 | 无变化 |
| T10 | EOD 收盘 LIVE/PARTIAL 处置 | DAY 单自动撤、GTC 跨日、未提交意图作废 | 中 | 无变化 |
| T11 | 原七类异常（断连/部成/撤单失败/涨跌停/停牌/数据缺失/重复启动） | 沿用全过 | 基线 | 无变化 |
| T12 | 进程假死接管 + 夺令牌 | 旧进程复活无法下单 | 中 | 无变化 |
| **T13** | **三级回撤状态机路径验证** | **20%触发降仓50%先卖卫星；25%触发清仓+冻结；系统续运行对账** | **高** | **v1.3 新增** |

---

## 附录 A · v1.3 变更日志（相对 v1.2）

| 编号 | 优先级 | 章节 | v1.2 状态 | v1.3 变更 | SSOT 依据 |
|------|--------|------|-----------|-----------|----------|
| G1 | 🔴 P0 | §一 | 19个状态（含 HALTED/MANUAL/FILLED_AFTER_CANCEL/PARTIAL_FILLED_AFTER_CANCEL） | **统一为 15 个运行态**；CANCELLED 用 cancel_fill_type 字段区分撤后成交类型；HALTED/MANUAL 合并为暂停态用 halt_reason 字段区分 | SSOT §3 |
| G2 | 🔴 P0 | §五 | break-glass 复杂自动清仓链路 | **简化为：物理一键熔断脚本（独立进程，直接 xtquant 撤单+市价平仓）+ 券商APP手动清仓 SOP；删除复杂自动清仓链路** | SSOT §3 |
| G3 | 🔴 P0 | §六.2 | 一级回撤15%/二级25% | **更新为 SSOT 定义：20%预警降仓50%先卖卫星 / 25%硬止损全清仓+冻结+人工复盘** | SSOT §4 |
| G4 | 🟡 P1 | §八 | T1 xtquant client_order_id 未知数 | **xtquant order_remark = client_order_id 对账方案正式采纳写入文档** | SSOT §3 |
| G5 | 🟡 P1 | §六.1 | 合规限速为 §6.1 实节 | **明确为状态机硬约束（1笔/秒、200笔/日），DDL 和检查点均更新** | SSOT §4 |
| G6 | 🟢 P2 | §七 | DDL 含 FILLED_AFTER_CANCEL 等状态枚举 | **DDL 更新为 15 态枚举，新增 cancel_fill_type/halt_reason/order_remark 字段** | G1 |
| G7 | 🟢 P2 | §十 | T1-T12 用例 | **新增 T13（三级回撤状态机路径验证）** | G3 |
| G8 | 🟢 P2 | 版本演进表 | — | 全文版（非增量补丁），版本演进表补全 | SSOT §8 |

---

## 附录 B · 状态图（文字版）

```
正常调仓主链路:
IDLE → TARGET_GEN → RISK_CLIP → ORDER_SIZING → ORDER_INTENT → PRE_FIRE_CHECK → SUBMITTED
         ↓              ↓              ↓               ↓               ↓
     暂停(MR)       暂停(MR)       暂停(MR)          no-op          暂停(MR)
                    ↓ 20%回撤                                            ↓
                ORDER_SIZING(降仓)                                   SUBMITTED
                    ↓ 25%回撤                                           ↙↓↘
                ORDER_SIZING(清仓)                              LIVE  PARTIAL  FILLED
                                                                 ↓      ↓        ↓
已受理:  SUBMITTED → LIVE → CANCEL_REQUESTED → CANCELLED      EOD   撤单/成交  RECONCILE
                         → PARTIAL                                              ↓
                         → FILLED                                             IDLE

UNKNOWN 归位:
SUBMITTED/LIVE/PARTIAL/CANCEL_REQUESTED → UNKNOWN → (连续两次一致) → FILLED/PARTIAL/REJECTED/CANCELLED
                                                   → (达重试上限)  → 暂停(MANUAL_REVIEW)

熔断路径:
任意状态 → break-glass触发 → 暂停(BREAK_GLASS) → 人工处置 → 暂停(MANUAL_REVIEW) → RECONCILE → IDLE

对账闭环:
FILLED/CANCELLED/REJECTED → RECONCILE → IDLE (差异可解释)
                          → RECONCILE → 暂停(MANUAL_REVIEW) (差异不可解释)
```

---

## 附录 C · 交叉引用索引

| 引用对象 | 本文档引用位置 | 目标文档编号 |
|---------|--------------|------------|
| 统计闸门数字（A1/A2/B1/B2/B3） | §六.2 满仓降仓线 | QS-CAL-001 |
| 五条铁律全文 | §〇.8 | QS-C01 |
| 背离（trigger_ic_audit）定义 | — | QS-C02 |
| 点时契约 corporate_action 表 | §三.1 除权对账 | QS-C03 |
| 模拟盘验收用例执行 | §十 | QS-C05 |
| 程序化交易合规义务 | §六.1 | SSOT §4 |

---

*本文档为 QuantSolo v2.0 宪法文档体系 QS-C04，v1.3 全文版（非增量补丁）。冻结后变更须版本号递增并记录于 research_ledger，并同步 QS-C05 交叉引用。*
