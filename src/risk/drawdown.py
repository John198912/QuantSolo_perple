"""三级回撤检测（QS-C04 §6.2 · QS-C01 §7.1）。

DrawdownMonitor：输入净值序列，输出状态及建议动作。
三个级别：
  NORMAL   — 回撤 < 20%，正常运行
  WARN     — 20% ≤ 回撤 < 25%，一级预警：降仓 50%（先卖卫星）
  HARD_STOP — 回撤 ≥ 25%，二级硬止损：全清仓+冻结新开仓

纯函数为主，DrawdownMonitor 作为薄包装维护峰值状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from src.common.config import load_frozen


class DrawdownStatus(str, Enum):
    """三级回撤状态（§6.2 SSOT 唯一口径）。"""
    NORMAL = "NORMAL"
    WARN = "WARN"
    HARD_STOP = "HARD_STOP"


@dataclass(frozen=True)
class DrawdownResult:
    """回撤检测结果。"""
    status: DrawdownStatus
    drawdown_pct: float          # 当前回撤比例（正数，如 0.21 = 21%）
    peak_nav: float              # 最近峰值净值
    current_nav: float           # 当前净值
    suggested_action: str        # 建议动作（人类可读）
    level: int                   # 数字级别：0/1/2，便于比较

    @property
    def is_normal(self) -> bool:
        return self.status == DrawdownStatus.NORMAL

    @property
    def is_warn(self) -> bool:
        return self.status == DrawdownStatus.WARN

    @property
    def is_hard_stop(self) -> bool:
        return self.status == DrawdownStatus.HARD_STOP


# ---------------------------------------------------------------------------
# 纯函数 API
# ---------------------------------------------------------------------------

def _get_thresholds() -> tuple[float, float]:
    """从 frozen.toml [risk] 读取回撤阈值。"""
    frozen = load_frozen()
    risk = frozen["risk"]
    return float(risk["drawdown_warn"]), float(risk["drawdown_hard_stop"])


def compute_drawdown(current_nav: float, peak_nav: float) -> float:
    """计算当前回撤比例。peak_nav ≤ 0 时返回 0.0。"""
    if peak_nav <= 0:
        return 0.0
    dd = (peak_nav - current_nav) / peak_nav
    return max(dd, 0.0)


def check_drawdown(
    current_nav: float,
    peak_nav: float,
    *,
    warn_threshold: float | None = None,
    hard_stop_threshold: float | None = None,
) -> DrawdownResult:
    """纯函数：根据当前净值与峰值净值判断回撤级别。

    Args:
        current_nav: 当前净值（账户总资产/起始净值，均可）。
        peak_nav: 自策略启动以来的历史最高净值。
        warn_threshold: 一级预警阈值（默认读 frozen.toml）。
        hard_stop_threshold: 二级硬止损阈值（默认读 frozen.toml）。

    Returns:
        DrawdownResult，携带 status、drawdown_pct、suggested_action。
    """
    if warn_threshold is None or hard_stop_threshold is None:
        _warn, _hard = _get_thresholds()
        warn_threshold = warn_threshold if warn_threshold is not None else _warn
        hard_stop_threshold = hard_stop_threshold if hard_stop_threshold is not None else _hard

    dd = compute_drawdown(current_nav, peak_nav)

    if dd >= hard_stop_threshold:
        return DrawdownResult(
            status=DrawdownStatus.HARD_STOP,
            drawdown_pct=dd,
            peak_nav=peak_nav,
            current_nav=current_nav,
            suggested_action=(
                "FULL_LIQUIDATION_AND_FREEZE: 全清仓+冻结新开仓+人工复盘。"
                "系统继续运行做对账（非 BREAK_GLASS 全停）。"
            ),
            level=2,
        )
    elif dd >= warn_threshold:
        return DrawdownResult(
            status=DrawdownStatus.WARN,
            drawdown_pct=dd,
            peak_nav=peak_nav,
            current_nav=current_nav,
            suggested_action=(
                "REDUCE_TO_50PCT_SELL_SATELLITE_FIRST: "
                "目标仓位降至 50%，优先卖出卫星仓（低权重/低流动性）。"
            ),
            level=1,
        )
    else:
        return DrawdownResult(
            status=DrawdownStatus.NORMAL,
            drawdown_pct=dd,
            peak_nav=peak_nav,
            current_nav=current_nav,
            suggested_action="NORMAL: 正常运行，无需干预。",
            level=0,
        )


def peak_nav_from_series(nav_series: Sequence[float]) -> float:
    """从净值序列中取历史最高值。序列为空时返回 0.0。"""
    if not nav_series:
        return 0.0
    return max(nav_series)


def check_drawdown_from_series(nav_series: Sequence[float]) -> DrawdownResult:
    """从净值序列直接计算回撤（取最大值为峰值，最后一个为当前值）。"""
    if not nav_series:
        return DrawdownResult(
            status=DrawdownStatus.NORMAL,
            drawdown_pct=0.0,
            peak_nav=0.0,
            current_nav=0.0,
            suggested_action="NORMAL: 净值序列为空。",
            level=0,
        )
    peak = peak_nav_from_series(nav_series)
    current = nav_series[-1]
    return check_drawdown(current, peak)


# ---------------------------------------------------------------------------
# 有状态包装器：DrawdownMonitor
# ---------------------------------------------------------------------------

@dataclass
class DrawdownMonitor:
    """有状态回撤监控器，维护历史峰值并暴露 update() 接口。

    典型用法::

        monitor = DrawdownMonitor(initial_nav=1.0)
        result = monitor.update(current_nav=0.78)
        if result.is_hard_stop:
            trigger_liquidation()

    线程安全性：单线程使用；如需并发请在调用方加锁。
    """

    initial_nav: float = 1.0
    _peak_nav: float = field(init=False, repr=False)
    _nav_history: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._peak_nav = self.initial_nav
        self._nav_history = [self.initial_nav]

    def update(self, current_nav: float) -> DrawdownResult:
        """更新当前净值，返回最新回撤状态。"""
        self._nav_history.append(current_nav)
        if current_nav > self._peak_nav:
            self._peak_nav = current_nav
        return check_drawdown(current_nav, self._peak_nav)

    def update_series(self, nav_series: Sequence[float]) -> DrawdownResult:
        """批量更新净值序列（追加），返回最后一次的状态。"""
        result: DrawdownResult | None = None
        for nav in nav_series:
            result = self.update(nav)
        if result is None:
            return check_drawdown(self.initial_nav, self._peak_nav)
        return result

    @property
    def peak_nav(self) -> float:
        return self._peak_nav

    @property
    def nav_history(self) -> list[float]:
        return list(self._nav_history)

    def reset(self, new_initial_nav: float | None = None) -> None:
        """重置监控器（通常在人工复盘+解冻后调用）。"""
        nav = new_initial_nav if new_initial_nav is not None else self.initial_nav
        self.initial_nav = nav
        self._peak_nav = nav
        self._nav_history = [nav]


# ---------------------------------------------------------------------------
# 向后兼容别名（对齐 QS-E02 设计文档中的旧签名）
# ---------------------------------------------------------------------------

@dataclass
class DrawdownLevel:
    """轻量别名，保持与设计文档旧接口兼容。"""
    level: int
    drawdown_pct: float
    action: str


def check_drawdown_level(
    current_nav: float,
    peak_nav: float,
    warning_pct: float = 0.20,
    halt_pct: float = 0.25,
) -> DrawdownLevel:
    """向后兼容接口（QS-E02 设计文档签名）。"""
    result = check_drawdown(
        current_nav,
        peak_nav,
        warn_threshold=warning_pct,
        hard_stop_threshold=halt_pct,
    )
    return DrawdownLevel(
        level=result.level,
        drawdown_pct=result.drawdown_pct,
        action=result.status.value,
    )
