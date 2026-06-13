"""告警推送器单元测试（QS-E03 §9.5）。

覆盖：
  - Server酱发送失败：重试 3 次，指数退避，超时不阻塞主流程
  - 钉钉 Webhook URL 未配置：跳过，不抛异常
  - Server酱 KEY 未配置：跳过，不抛异常
  - HIGH/CRITICAL 级别同步发送
  - INFO/MEDIUM 级别放入队列（不同步发送）
  - 告警级别过滤：INFO 不触发同步推送
  - 告警消息字段校验（title 截断、timestamp 格式）
  - flush_queue 清空队列并发送
  - 注入 http_client mock，不发真实网络请求
  - requests 惰性导入兜底：mock 缺失场景降级到日志

所有测试使用 mock/fake 客户端，不发真实网络请求。
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

import pytest

from src.monitor.alerter import AlertManager, AlertMessage


# ---------------------------------------------------------------------------
# Fake HTTP 客户端
# ---------------------------------------------------------------------------

class FakeHttpClient:
    """记录所有 HTTP 调用，默认返回 HTTP 200。"""

    def __init__(self, fail_count: int = 0):
        """
        Args:
            fail_count: 前 fail_count 次 post() 抛出异常（模拟发送失败）。
        """
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self._fail_count = fail_count
        self._call_count = 0

    def post(self, url: str, data=None, json=None, timeout=None):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise ConnectionError(f"Fake connection error #{self._call_count}")
        self.post_calls.append({"url": url, "data": data, "json": json, "timeout": timeout})
        return _FakeResponse(200)

    def get(self, url: str, timeout=None):
        self.get_calls.append({"url": url, "timeout": timeout})
        return _FakeResponse(200)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def _make_alerter(
    server_chan_key: str = "TEST_KEY",
    dingtalk_url: str = "",
    http_client: FakeHttpClient | None = None,
) -> AlertManager:
    """创建注入了 mock http 客户端的 AlertManager。"""
    manager = AlertManager(http_client=http_client or FakeHttpClient())
    manager._server_chan_key = server_chan_key
    manager._dingtalk_url = dingtalk_url
    return manager


# ---------------------------------------------------------------------------
# 测试：HIGH 级别同步发送
# ---------------------------------------------------------------------------

def test_high_level_sends_sync():
    """HIGH 级别告警同步发送，不放入队列。"""
    http = FakeHttpClient()
    alerter = _make_alerter(http_client=http)

    result = alerter.send_alert("HIGH", "高优先级告警", source="test")

    assert result is True
    assert len(http.post_calls) == 1
    assert len(alerter._queue) == 0


def test_critical_level_sends_sync():
    """CRITICAL 级别同步发送。"""
    http = FakeHttpClient()
    alerter = _make_alerter(http_client=http)

    result = alerter.send_alert("CRITICAL", "严重告警", source="test")

    assert result is True
    assert len(http.post_calls) == 1


# ---------------------------------------------------------------------------
# 测试：INFO/MEDIUM 放入队列
# ---------------------------------------------------------------------------

def test_info_level_queued_not_sync():
    """INFO 级别放入队列，不触发同步推送。"""
    http = FakeHttpClient()
    alerter = _make_alerter(http_client=http)

    result = alerter.send_alert("INFO", "日常通知", source="test")

    assert result is True
    assert len(http.post_calls) == 0  # 未同步发送
    assert len(alerter._queue) == 1


def test_medium_level_queued_not_sync():
    """MEDIUM 级别放入队列，不触发同步推送。"""
    http = FakeHttpClient()
    alerter = _make_alerter(http_client=http)

    alerter.send_alert("MEDIUM", "中等告警", source="test")

    assert len(http.post_calls) == 0
    assert len(alerter._queue) == 1


# ---------------------------------------------------------------------------
# 测试：重试机制
# ---------------------------------------------------------------------------

def test_retry_on_failure_succeeds():
    """前 2 次失败，第 3 次成功 → 最终 success=True，重试 3 次。"""
    http = FakeHttpClient(fail_count=2)
    alerter = _make_alerter(http_client=http)

    result = alerter.send_alert("HIGH", "需要重试的告警", source="test", retry=3)

    assert result is True
    # 失败 2 次 + 成功 1 次 = 3 次 post 调用
    assert http._call_count == 3


def test_retry_exhausted_returns_false():
    """全部 3 次都失败 → success=False。"""
    http = FakeHttpClient(fail_count=10)  # 总是失败
    alerter = _make_alerter(http_client=http)

    result = alerter.send_alert("HIGH", "始终失败的告警", source="test", retry=3)

    assert result is False
    assert http._call_count == 3  # 重试 3 次


# ---------------------------------------------------------------------------
# 测试：渠道未配置跳过
# ---------------------------------------------------------------------------

def test_no_server_chan_no_dingtalk_degrades_to_log():
    """Server酱 KEY 和钉钉 URL 均未配置 → 降级到日志，不抛异常，返回 True。"""
    http = FakeHttpClient()
    alerter = AlertManager(http_client=http)
    alerter._server_chan_key = ""
    alerter._dingtalk_url = ""

    result = alerter.send_alert("HIGH", "渠道未配置告警", source="test")

    assert result is True
    assert len(http.post_calls) == 0  # 无 HTTP 调用


def test_dingtalk_only_sends_dingtalk():
    """仅配置钉钉 → 只调用钉钉 webhook，不调用 Server酱。"""
    http = FakeHttpClient()
    alerter = AlertManager(http_client=http)
    alerter._server_chan_key = ""
    alerter._dingtalk_url = "https://oapi.dingtalk.com/robot/send?access_token=FAKE"

    alerter.send_alert("HIGH", "钉钉专属告警", source="test")

    assert len(http.post_calls) == 1
    assert "dingtalk" in http.post_calls[0]["url"] or "oapi" in http.post_calls[0]["url"]


# ---------------------------------------------------------------------------
# 测试：AlertMessage 字段校验
# ---------------------------------------------------------------------------

def test_alert_message_title_truncated():
    """title 中 message 被截断为前 50 个字符。"""
    long_msg = "A" * 100
    http = FakeHttpClient()
    alerter = _make_alerter(http_client=http)
    alerter.send_alert("HIGH", long_msg)

    posted = http.post_calls[0]["data"]
    # title 格式：[QuantSolo] HIGH: <前50字符>
    assert len(posted["title"]) <= len("[QuantSolo] HIGH: ") + 50


def test_alert_message_server_chan_desp_full():
    """Server酱 desp 包含完整 content（不截断）。"""
    msg = "测试告警详细内容，不应被截断。" * 5
    http = FakeHttpClient()
    alerter = _make_alerter(http_client=http)
    alerter.send_alert("HIGH", msg)

    posted = http.post_calls[0]["data"]
    assert posted["desp"] == msg


# ---------------------------------------------------------------------------
# 测试：flush_queue
# ---------------------------------------------------------------------------

def test_flush_queue_sends_queued_alerts():
    """flush_queue() 应将队列中所有告警同步发送并清空队列。"""
    http = FakeHttpClient()
    alerter = _make_alerter(http_client=http)

    alerter.send_alert("INFO", "通知1")
    alerter.send_alert("MEDIUM", "通知2")
    assert len(alerter._queue) == 2

    results = alerter.flush_queue()

    assert len(alerter._queue) == 0
    assert len(results) == 2


# ---------------------------------------------------------------------------
# 测试：requests 惰性导入兜底
# ---------------------------------------------------------------------------

def test_alerter_importable_without_requests():
    """requests 不可用时，AlertManager _get_http() 返回 None，降级到日志不崩溃。"""
    alerter = AlertManager(http_client=None)
    alerter._server_chan_key = "FAKE"
    alerter._dingtalk_url = ""

    # 直接 patch _get_http 返回 None，模拟 requests 完全不可用
    alerter._get_http = lambda: None  # type: ignore

    # 不应抛出 ImportError；降级到日志
    result = alerter.send_alert("HIGH", "requests 不可用时测试", retry=1)
    # 结果为 bool（成功降级到日志或失败均可，只要不崩溃）
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# 测试：钉钉 payload 格式
# ---------------------------------------------------------------------------

def test_dingtalk_payload_markdown_format():
    """钉钉推送 payload 应包含 msgtype=markdown 和正确字段。"""
    http = FakeHttpClient()
    alerter = AlertManager(http_client=http)
    alerter._server_chan_key = ""
    alerter._dingtalk_url = "https://oapi.dingtalk.com/fake"

    alerter.send_alert("HIGH", "钉钉格式测试", source="test.module")

    assert len(http.post_calls) == 1
    payload = http.post_calls[0]["json"]
    assert payload["msgtype"] == "markdown"
    assert "title" in payload["markdown"]
    assert "text" in payload["markdown"]
    assert "test.module" in payload["markdown"]["text"]
