"""回测引擎子包（QS-C01 §4 / 功能设计文档 §4）。"""
from __future__ import annotations

from src.research.backtest.cost_models import (
    CostModel,
    COST_MODEL_BASELINE_ID,
    COST_MODEL_ADVANCED_ID,
    get_baseline_model,
    get_advanced_model,
)
from src.research.backtest.event_driven import (
    EventDrivenBacktest,
    Order,
    Position,
    apply_ashare_constraints,
)
from src.research.backtest.vectorized import VectorizedBacktest

__all__ = [
    "CostModel",
    "COST_MODEL_BASELINE_ID",
    "COST_MODEL_ADVANCED_ID",
    "get_baseline_model",
    "get_advanced_model",
    "EventDrivenBacktest",
    "Order",
    "Position",
    "apply_ashare_constraints",
    "VectorizedBacktest",
]
