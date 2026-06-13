"""成本模型（QS-C01 §4 / §12.2 + §6.4；研究协议 §2.6）。

双成本档：cm_v3_baseline（固定滑点）与 cm_v3_advanced（动态冲击成本）。
cost_model_id 不可混用（§4.2 双层互验、§2.6 口径统一）。

R6 红线：所有金额/费率计算使用 Decimal，禁止 float 直接算钱。
R3 红线：成本数字全部经 load_frozen()['cost'] 读取，禁止硬编码。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from src.common.config import load_frozen


def _cost_cfg() -> dict:
    """获取冻结成本参数字典。"""
    return dict(load_frozen()["cost"])


@dataclass
class CostModel:
    """成本模型（QS-C01 §12.2 + §6.4）

    所有费率均以 Decimal 存储，calc_transaction_cost 返回 Decimal（元）。
    """
    model_id: str
    stamp_duty_sell: Decimal = field(default_factory=lambda: Decimal("0"))
    commission_rate: Decimal = field(default_factory=lambda: Decimal("0"))
    commission_min_cny: Decimal = field(default_factory=lambda: Decimal("0"))
    transfer_fee_rate: Decimal = field(default_factory=lambda: Decimal("0"))
    slippage_floor: Decimal = field(default_factory=lambda: Decimal("0"))

    def calc_transaction_cost(
        self,
        amount: Decimal | float | int,
        side: Literal["BUY", "SELL"],
        daily_turnover: Decimal | float | int = Decimal("0"),
        trade_size: Decimal | float | int = Decimal("0"),
    ) -> Decimal:
        """计算单次交易总成本（Decimal，元）。

        Args:
            amount:         本次交易名义金额（元）
            side:           'BUY' 或 'SELL'
            daily_turnover: 标的日均成交额（元），cm_v3_advanced 动态滑点需要
            trade_size:     本次交易金额（元），cm_v3_advanced 动态滑点需要

        Returns:
            总成本（Decimal，元），含印花税 + 佣金 + 过户费 + 滑点。

        Note:
            cm_v3_advanced 时，滑点按 trade_size / daily_turnover 动态建模：
              slippage = slippage_floor × (1 + 10 × impact_ratio)
            cm_v3_baseline 时，slippage = slippage_floor（常数）。
        """
        amt = Decimal(str(amount))
        dt = Decimal(str(daily_turnover))
        ts = Decimal(str(trade_size))

        # 印花税（卖出单边）
        stamp = amt * self.stamp_duty_sell if side == "SELL" else Decimal("0")

        # 佣金（取 rate × amount 与 min 的较大值）
        commission_raw = amt * self.commission_rate
        commission = max(commission_raw, self.commission_min_cny)

        # 过户费（买入时收取，沪市）
        transfer = amt * self.transfer_fee_rate if side == "BUY" else Decimal("0")

        # 滑点
        if self.model_id == "cm_v3_advanced" and dt > Decimal("0"):
            impact_ratio = ts / dt
            slippage = self.slippage_floor * (Decimal("1") + Decimal("10") * impact_ratio)
        else:
            slippage = self.slippage_floor

        slippage_cost = amt * slippage

        total = stamp + commission + transfer + slippage_cost
        # 精确到分
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def cost_pct(
        self,
        side: Literal["BUY", "SELL"],
        daily_turnover: Decimal | float | int = Decimal("0"),
        trade_size: Decimal | float | int = Decimal("0"),
    ) -> Decimal:
        """返回成本占比（Decimal），amount=1 时的单位成本。

        供向量化回测快速估算用（amount 归一化为 1）。
        """
        return self.calc_transaction_cost(
            Decimal("1"), side, daily_turnover, trade_size
        )


def _build_from_frozen(model_id: str) -> CostModel:
    """从冻结参数构建 CostModel，确保数字来源于 load_frozen()['cost']。"""
    cfg = _cost_cfg()
    return CostModel(
        model_id=model_id,
        stamp_duty_sell=Decimal(str(cfg["stamp_duty_sell"])),
        commission_rate=Decimal(str(cfg["commission_rate"])),
        commission_min_cny=Decimal(str(cfg["commission_min_cny"])),
        transfer_fee_rate=Decimal(str(cfg["transfer_fee_rate"])),
        slippage_floor=Decimal(str(cfg["slippage_floor"])),
    )


def get_baseline_model() -> CostModel:
    """返回 cm_v3_baseline 成本模型（固定滑点，快速向量化回测用）。"""
    return _build_from_frozen("cm_v3_baseline")


def get_advanced_model() -> CostModel:
    """返回 cm_v3_advanced 成本模型（动态冲击成本，精确事件驱动回测用）。"""
    return _build_from_frozen("cm_v3_advanced")


# 模块级全局常量（延迟初始化以兼容无 frozen.toml 的测试环境）
# 使用时通过 get_baseline_model() / get_advanced_model() 获取，避免导入时失败。
# 以下两个变量保留文档兼容名，但建议代码使用工厂函数。
def _lazy_cm(model_id: str) -> CostModel:
    """惰性构建成本模型（避免模块级 IO）。"""
    return _build_from_frozen(model_id)


# QS-C01 §6.4 双成本档名称常量
COST_MODEL_BASELINE_ID = "cm_v3_baseline"
COST_MODEL_ADVANCED_ID = "cm_v3_advanced"
