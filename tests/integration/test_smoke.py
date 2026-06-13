"""冒烟测试（集成层）。

覆盖：
  1. 导入全部 src 包，确认无 import 错误（除需 live 的适配器外）
  2. frozen_params 加载与只读性
  3. config 加载（tunable.yaml, source_priority.yaml）
"""
from __future__ import annotations

import importlib
import types
from pathlib import Path
from types import MappingProxyType

import pytest


# ---------------------------------------------------------------------------
# 1. 导入全部 src 包（无 import 错误）
# ---------------------------------------------------------------------------

SAFE_MODULES = [
    # pit 模块
    "src.pit.query_engine",
    "src.pit.daily_bar",
    "src.pit.financials",
    "src.pit.factor_snapshot",
    "src.pit.validator",
    # data 模块
    "src.data.arbitrator",
    "src.data.visible_at",
    "src.data.calendar",
    # risk 模块
    "src.risk.guard",
    "src.risk.constraints",
    "src.risk.drawdown",
    # execution 模块
    "src.execution.state_machine",
    "src.execution.order_sizing",
    "src.execution.rate_limiter",
    "src.execution.idempotency",
    "src.execution.interfaces",
    # common 模块
    "src.common.config",
]

# live 适配器（需 xtquant/akshare/tushare/baostock）——跳过
LIVE_MODULES = [
    "src.adapters.xtquant_adapter",
    "src.adapters.akshare_adapter",
    "src.adapters.tushare_adapter",
    "src.adapters.baostock_adapter",
]


@pytest.mark.parametrize("module_name", SAFE_MODULES)
def test_import_safe_module(module_name: str):
    """安全模块应能无错误导入。"""
    mod = importlib.import_module(module_name)
    assert isinstance(mod, types.ModuleType), f"{module_name} 导入失败"


@pytest.mark.live
@pytest.mark.parametrize("module_name", LIVE_MODULES)
def test_import_live_module(module_name: str):
    """live 适配器模块（需外部依赖），标记为 live 并 skip（无依赖时）。"""
    pytest.skip(f"live 模块 {module_name} 需要第三方数据库/券商依赖，跳过")


# ---------------------------------------------------------------------------
# 2. frozen_params 加载与只读性
# ---------------------------------------------------------------------------

def test_frozen_params_load():
    """load_frozen() 应返回 MappingProxyType（不可变）。"""
    from src.common.config import load_frozen
    frozen = load_frozen()
    assert isinstance(frozen, MappingProxyType), "frozen 应是 MappingProxyType"


def test_frozen_params_readonly():
    """frozen 参数不可在运行时修改（TypeError）。"""
    from src.common.config import load_frozen
    frozen = load_frozen()
    with pytest.raises(TypeError):
        frozen["new_key"] = "tamper"  # type: ignore


def test_frozen_params_has_required_sections():
    """frozen.toml 应包含 risk/compliance 两节（风控守卫依赖）。"""
    from src.common.config import load_frozen
    frozen = load_frozen()
    assert "risk" in frozen, "frozen.toml 缺少 [risk] 段"
    assert "compliance" in frozen, "frozen.toml 缺少 [compliance] 段"

    risk = frozen["risk"]
    assert "max_position_per_stock" in risk
    assert "max_position_per_industry" in risk
    assert "drawdown_warn" in risk
    assert "drawdown_hard_stop" in risk

    compliance = frozen["compliance"]
    assert "max_orders_per_second" in compliance
    assert "max_orders_per_day" in compliance


def test_frozen_params_risk_values():
    """冻结风控参数值在预期范围内。"""
    from src.common.config import load_frozen
    frozen = load_frozen()
    risk = frozen["risk"]

    max_stock_pct = float(risk["max_position_per_stock"])
    assert 0 < max_stock_pct <= 0.10, f"单票上限 {max_stock_pct} 不合理"

    max_industry_pct = float(risk["max_position_per_industry"])
    assert 0 < max_industry_pct <= 0.50, f"行业上限 {max_industry_pct} 不合理"

    drawdown_warn = float(risk["drawdown_warn"])
    drawdown_hard = float(risk["drawdown_hard_stop"])
    assert drawdown_warn < drawdown_hard, "WARN 阈值应小于 HARD_STOP 阈值"


# ---------------------------------------------------------------------------
# 3. config 加载（tunable.yaml）
# ---------------------------------------------------------------------------

def test_config_load_tunable():
    """load_tunable() 应返回 dict。"""
    from src.common.config import load_tunable
    tunable = load_tunable()
    assert isinstance(tunable, dict), "tunable 应是 dict"


def test_config_load_source_priority():
    """load_source_priority() 应返回 dict。"""
    from src.common.config import load_source_priority
    src_priority = load_source_priority()
    assert isinstance(src_priority, dict), "source_priority 应是 dict"


# ---------------------------------------------------------------------------
# 4. 核心类实例化冒烟
# ---------------------------------------------------------------------------

def test_trade_calendar_instantiation():
    """TradeCalendar 能正常实例化并操作。"""
    from src.data.calendar import TradeCalendar
    cal = TradeCalendar(["2024-03-01", "2024-03-04", "2024-03-05"])
    assert cal.is_trading_day("2024-03-01")
    assert not cal.is_trading_day("2024-03-02")
    assert cal.next_trading_day("2024-03-01") == "2024-03-04"
    assert cal.count() == 3


def test_source_arbitrator_instantiation():
    """SourceArbitrator 能正常实例化并执行裁决。"""
    from src.data.arbitrator import SourceArbitrator, ArbitrationStatus
    arb = SourceArbitrator()
    result = arb.arbitrate("close", {"akshare": 10.0, "tushare": 10.0})
    assert result.status == ArbitrationStatus.CONSENSUS


def test_order_state_machine_instantiation():
    """OrderStateMachine 能正常实例化。"""
    from src.execution.state_machine import OrderStateMachine, OrderState
    sm = OrderStateMachine()
    assert sm.state == OrderState.IDLE
    assert len(sm.history) == 0


def test_risk_guard_instantiation():
    """RiskGuard 能正常实例化（依赖 frozen.toml）。"""
    from src.risk.guard import RiskGuard
    guard = RiskGuard()
    assert guard is not None
    from src.risk.drawdown import DrawdownStatus
    assert guard.drawdown_status == DrawdownStatus.NORMAL
