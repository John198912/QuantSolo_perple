"""结构化日志模块（QS-E03 §14）。

提供 StructuredLogger，供告警器等模块调用。
若不存在外部日志配置，默认输出到 stderr（stdlib logging）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

_root = logging.getLogger("quantsolo")
if not _root.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _root.addHandler(_handler)
    _root.setLevel(logging.DEBUG)


class StructuredLogger:
    """结构化日志（JSON Lines），与 Python logging 兼容。

    用法::
        StructuredLogger.log("HIGH", "monitor.alerter", "ALERT_SENT", message="xxx")
    """

    _LEVEL_MAP = {
        "INFO":     logging.INFO,
        "MEDIUM":   logging.WARNING,
        "HIGH":     logging.ERROR,
        "CRITICAL": logging.CRITICAL,
        "DEBUG":    logging.DEBUG,
        "WARNING":  logging.WARNING,
        "ERROR":    logging.ERROR,
    }

    @classmethod
    def log(
        cls,
        level: str,
        module: str,
        event: str,
        **kwargs: Any,
    ) -> None:
        """写入一条结构化日志。

        Args:
            level: 日志级别字符串（INFO/MEDIUM/HIGH/CRITICAL）。
            module: 来源模块名称。
            event: 事件代码（如 ALERT_SENT、RECON_PASSED）。
            **kwargs: 附加字段（message 等）。
        """
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "level": level,
            "module": module,
            "event": event,
            **kwargs,
        }
        log_level = cls._LEVEL_MAP.get(level.upper(), logging.INFO)
        logger = logging.getLogger(f"quantsolo.{module}")
        logger.log(log_level, json.dumps(payload, ensure_ascii=False))
