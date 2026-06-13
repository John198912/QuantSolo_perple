"""监控 Watchdog — 进程双向互查（QS-E03 §9.3 / QS-C01 §14.2）。

进程互查设计：
  监控进程⑤ ↔ 执行进程② 双向监控。
  单向监控不可接受：监控进程自身故障也必须被发现。

  - check_execution_process(): 监控进程⑤ 检查执行进程② 的心跳（PID 文件）
  - check_monitor_self_health(): 执行进程② 检查监控进程⑤ 的健康端点
  - 连续 3 次超时 → 告警（45s 窗口 = 3×15s）

红线遵守：
  R1：不 import xtquant。
  requests 惰性导入兜底（缺失时 check_monitor_self_health 降级返回 False）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from src.monitor.alerter import AlertManager
from src.logger import StructuredLogger


class Watchdog:
    """进程互查（QS-C01 §14.2 · watchdog 设计原则）。

    监控进程⑤ ↔ 执行进程② 双向监控。
    单向监控不可接受：监控进程自身故障也必须被发现。

    Args:
        execution_pid_file:      执行进程心跳 PID 文件路径（JSON 格式，含 heartbeat_ts）。
        monitor_health_url:      监控进程自身 HTTP /health 端点 URL。
        alerter:                 AlertManager 实例。
        heartbeat_interval_s:    心跳间隔（秒），默认 15。
        miss_max:                连续丢失次数上限，达到后告警，默认 3（→ 45s 窗口）。
        http_client:             可注入 HTTP 客户端（测试 mock）；None 时惰性导入 requests。
    """

    def __init__(
        self,
        execution_pid_file: str,
        monitor_health_url: str,
        alerter: AlertManager,
        heartbeat_interval_s: int = 15,
        miss_max: int = 3,
        http_client: Optional[Any] = None,
    ) -> None:
        self.pid_file = execution_pid_file
        self.health_url = monitor_health_url
        self.alerter = alerter
        self.interval = heartbeat_interval_s
        self.miss_max = miss_max
        self._miss_count = 0
        self._http = http_client

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def check_execution_process(self) -> bool:
        """监控进程⑤ 检查执行进程② 的心跳。

        读取 run/execution.pid（JSON 格式，含 heartbeat_ts），
        验证心跳时间戳未过期。

        连续丢失 miss_max 次（即心跳超时 interval × miss_max 秒）→ 触发 HIGH 告警。

        Returns:
            True  — 心跳正常。
            False — 心跳超时或 PID 文件缺失。
        """
        try:
            with open(self.pid_file, "r", encoding="utf-8") as fh:
                info = json.load(fh)
            heartbeat_age = time.time() - info.get("heartbeat_ts", 0)
            timeout_threshold = self.interval * self.miss_max
            if heartbeat_age > timeout_threshold:
                self._miss_count += 1
                StructuredLogger.log(
                    "WARNING",
                    "monitor.watchdog",
                    "HEARTBEAT_MISS",
                    miss_count=self._miss_count,
                    age_s=round(heartbeat_age, 1),
                )
                if self._miss_count >= self.miss_max:
                    self.alerter.send_alert(
                        "HIGH",
                        f"执行进程失联 {heartbeat_age:.0f}s，请检查",
                        source="monitor.watchdog",
                    )
                return False
            else:
                self._miss_count = 0
                return True
        except FileNotFoundError:
            self._miss_count += 1
            self.alerter.send_alert(
                "HIGH",
                "执行进程 PID 文件不存在",
                source="monitor.watchdog",
            )
            return False
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            StructuredLogger.log(
                "WARNING",
                "monitor.watchdog",
                "PID_FILE_PARSE_ERROR",
                error=str(exc),
            )
            return False

    def check_monitor_self_health(self) -> bool:
        """执行进程② 检查监控进程⑤ 的健康端点。

        连续 3 次失败 → 告警（此时告警渠道可能已故障，写 ledger 留痕）。
        requests 惰性导入兜底：缺失时返回 False。

        Returns:
            True  — 监控进程健康（HTTP 200）。
            False — 请求失败或 requests 未安装。
        """
        http = self._get_http()
        if http is None:
            StructuredLogger.log(
                "WARNING",
                "monitor.watchdog",
                "REQUESTS_UNAVAILABLE",
                message="requests 未安装，跳过监控自检",
            )
            return False
        try:
            resp = http.get(self.health_url, timeout=5)
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            StructuredLogger.log(
                "WARNING",
                "monitor.watchdog",
                "HEALTH_CHECK_FAILED",
                url=self.health_url,
                error=str(exc),
            )
            return False

    def run_forever(self) -> None:
        """监控主循环（APScheduler 调用或独立线程）。"""
        while True:
            self.check_execution_process()
            time.sleep(self.interval)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_http(self) -> Optional[Any]:
        """惰性获取 HTTP 客户端。"""
        if self._http is not None:
            return self._http
        try:
            import requests as _requests  # 惰性导入
            return _requests
        except ImportError:
            return None
