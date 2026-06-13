"""监控告警器 — 告警推送（QS-E03 §9.2 / QS-C01 §14.3）。

支持 Server酱 + 钉钉 Webhook 双渠道推送。
  - HIGH/CRITICAL 级别同步发送（retry=3，指数退避）
  - INFO/MEDIUM 级别放入内存队列（异步）
  - requests 惰性导入兜底（缺失时降级到日志）
  - 支持注入 http 客户端便于测试 mock

红线遵守：
  R1：不 import xtquant。
  R3：不硬编码冻结参数（告警渠道 key 经环境变量注入）。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from src.logger import StructuredLogger


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class AlertMessage:
    """告警消息（QS-C01 §14.3）。"""
    level: str          # 'INFO' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
    title: str
    content: str
    source_module: str
    timestamp: str


# ---------------------------------------------------------------------------
# AlertManager
# ---------------------------------------------------------------------------

class AlertManager:
    """告警发送器（Server酱 + 钉钉机器人）。

    关键告警事件（QS-C01 §14.3）：
      风控触发 / 下单失败 / 数据管道失败 / 进程失联 / UPS 切换 / 对账差错

    参数：
        http_client: 可注入自定义 HTTP 客户端（用于测试 mock）。
                     接口：
                       http_client.post(url, data=None, json=None, timeout=None)
                     默认为 None，惰性导入 requests。
    """

    LEVEL_MAP = {
        "INFO":     "📘",
        "MEDIUM":   "🟡",
        "HIGH":     "🔴",
        "CRITICAL": "🚨",
    }

    def __init__(
        self,
        http_client: Optional[Any] = None,
    ) -> None:
        self._server_chan_key: str = os.getenv("SERVER_CHAN_KEY", "")
        self._dingtalk_url: str = os.getenv("DINGTALK_WEBHOOK", "")
        self._queue: list[AlertMessage] = []
        self._lock = threading.Lock()
        # 注入式 http 客户端（测试用）；None 时惰性导入 requests
        self._http = http_client

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def send_alert(
        self,
        level: str,
        message: str,
        source: str = "system",
        retry: int = 3,
    ) -> bool:
        """发送告警（HIGH/CRITICAL 同步；其余入队异步）。

        Args:
            level:   告警级别（INFO/MEDIUM/HIGH/CRITICAL）。
            message: 告警内容。
            source:  来源模块名称（用于日志追踪）。
            retry:   同步发送失败最大重试次数（默认 3）。

        Returns:
            True 表示告警已发送/入队，False 表示同步发送全部失败。
        """
        alert = AlertMessage(
            level=level,
            title=f"[QuantSolo] {level}: {message[:50]}",
            content=message,
            source_module=source,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        )
        StructuredLogger.log(level, source, "ALERT_SENT", message=message)

        if level in ("HIGH", "CRITICAL"):
            return self._send_sync(alert, retry)
        else:
            with self._lock:
                self._queue.append(alert)
            return True

    def flush_queue(self, retry: int = 3) -> list[bool]:
        """将队列中的告警同步发送（盘后批处理调用）。

        Returns:
            每条告警发送结果列表（True=成功/降级日志，False=全渠道失败）。
        """
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
        return [self._send_sync(a, retry) for a in items]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _send_sync(self, alert: AlertMessage, retry: int) -> bool:
        """同步发送到 Server酱 + 钉钉（指数退避重试）。"""
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
                # 两个渠道均未配置：降级到日志，视为成功
                StructuredLogger.log(
                    "WARNING",
                    "monitor.alerter",
                    "ALERT_NO_CHANNEL",
                    message=alert.content,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                StructuredLogger.log(
                    "WARNING",
                    "monitor.alerter",
                    "ALERT_RETRY",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt < retry - 1:
                    time.sleep(2 ** attempt)
        return success

    def _get_http(self) -> Any:
        """惰性获取 HTTP 客户端（requests 或注入客户端）。"""
        if self._http is not None:
            return self._http
        try:
            import requests as _requests  # 惰性导入
            return _requests
        except ImportError:
            return None

    def _send_server_chan(self, alert: AlertMessage) -> None:
        """Server酱推送（https://sctapi.ftqq.com/）。"""
        http = self._get_http()
        if http is None:
            StructuredLogger.log(
                "WARNING",
                "monitor.alerter",
                "REQUESTS_UNAVAILABLE",
                message="requests 未安装，跳过 Server酱推送",
            )
            return
        url = f"https://sctapi.ftqq.com/{self._server_chan_key}.send"
        http.post(
            url,
            data={"title": alert.title, "desp": alert.content},
            timeout=10,
        )

    def _send_dingtalk(self, alert: AlertMessage) -> None:
        """钉钉机器人推送。"""
        http = self._get_http()
        if http is None:
            StructuredLogger.log(
                "WARNING",
                "monitor.alerter",
                "REQUESTS_UNAVAILABLE",
                message="requests 未安装，跳过钉钉推送",
            )
            return
        emoji = self.LEVEL_MAP.get(alert.level, "❓")
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": alert.title,
                "text": (
                    f"## {emoji} {alert.title}\n\n"
                    f"**时间**：{alert.timestamp}\n\n"
                    f"**来源**：{alert.source_module}\n\n"
                    f"**详情**：{alert.content}"
                ),
            },
        }
        http.post(self._dingtalk_url, json=payload, timeout=10)
