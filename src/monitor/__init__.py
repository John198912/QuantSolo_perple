"""监控告警器子包（QS-E03 §9）。

导出：
  AlertManager    — 告警发送器（Server酱 + 钉钉）
  AlertMessage    — 告警消息数据类
  Watchdog        — 进程双向互查
  run_dashboard   — Streamlit 每日巡检看板（惰性依赖）
"""
from __future__ import annotations

from src.monitor.alerter import AlertManager, AlertMessage
from src.monitor.watchdog import Watchdog
from src.monitor.dashboard import run_dashboard

__all__ = ["AlertManager", "AlertMessage", "Watchdog", "run_dashboard"]
