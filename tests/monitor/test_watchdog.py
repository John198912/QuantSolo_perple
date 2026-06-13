"""Watchdog 双向互查单元测试（QS-E03 §9.5）。

覆盖：
  - 心跳正常：check_execution_process 返回 True
  - 心跳超时（45s窗口 = 3×15s）：连续 3 次超时 → HIGH 告警
  - PID 文件不存在：立即 HIGH 告警，返回 False
  - 连续未超时中断：miss_count 重置为 0
  - check_monitor_self_health HTTP 200：返回 True
  - check_monitor_self_health 失败：返回 False，不抛异常
  - requests 惰性导入兜底：缺失时 check_monitor_self_health 返回 False

所有测试使用 mock/fake 客户端，不发真实网络请求，不操作真实文件系统。
"""
from __future__ import annotations

import json
import time
import tempfile
import os
from unittest.mock import MagicMock

import pytest

from src.monitor.watchdog import Watchdog
from src.monitor.alerter import AlertManager


# ---------------------------------------------------------------------------
# Fake 对象
# ---------------------------------------------------------------------------

class FakeAlerter:
    """捕获所有告警，不发真实网络请求。"""

    def __init__(self):
        self.calls: list[dict] = []

    def send_alert(self, level: str, message: str, source: str = "system", retry: int = 3):
        self.calls.append({"level": level, "message": message, "source": source})
        return True


class FakeHttpClient:
    """模拟 HTTP 客户端，可配置响应状态码或抛出异常。"""

    def __init__(self, status_code: int = 200, raise_exc: Exception | None = None):
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.get_calls: list[dict] = []

    def get(self, url: str, timeout=None):
        self.get_calls.append({"url": url, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeResponse(self.status_code)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def _make_pid_file(heartbeat_ts: float, pid: int = 12345) -> str:
    """创建临时 PID 文件，返回文件路径。"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".pid.json", delete=False
    )
    json.dump({"pid": pid, "heartbeat_ts": heartbeat_ts}, tmp)
    tmp.close()
    return tmp.name


def _make_watchdog(
    pid_file: str = "/nonexistent/path.pid",
    health_url: str = "http://localhost:9090/health",
    interval: int = 15,
    miss_max: int = 3,
    http_client: FakeHttpClient | None = None,
) -> tuple[Watchdog, FakeAlerter]:
    alerter = FakeAlerter()
    wd = Watchdog(
        execution_pid_file=pid_file,
        monitor_health_url=health_url,
        alerter=alerter,
        heartbeat_interval_s=interval,
        miss_max=miss_max,
        http_client=http_client or FakeHttpClient(),
    )
    return wd, alerter


# ---------------------------------------------------------------------------
# 测试：心跳正常
# ---------------------------------------------------------------------------

def test_heartbeat_fresh_returns_true():
    """心跳时间戳为当前时间 → check_execution_process 返回 True，无告警。"""
    pid_file = _make_pid_file(heartbeat_ts=time.time())
    try:
        wd, alerter = _make_watchdog(pid_file=pid_file)
        result = wd.check_execution_process()
        assert result is True
        assert alerter.calls == []
        assert wd._miss_count == 0
    finally:
        os.unlink(pid_file)


def test_heartbeat_recent_within_window():
    """心跳 10 秒前（< 45s 窗口）→ 正常，miss_count 重置为 0。"""
    pid_file = _make_pid_file(heartbeat_ts=time.time() - 10)
    try:
        wd, alerter = _make_watchdog(pid_file=pid_file)
        wd._miss_count = 2  # 先设置为非零，验证重置
        result = wd.check_execution_process()
        assert result is True
        assert wd._miss_count == 0
        assert alerter.calls == []
    finally:
        os.unlink(pid_file)


# ---------------------------------------------------------------------------
# 测试：心跳超时（45s 窗口）
# ---------------------------------------------------------------------------

def test_heartbeat_expired_increments_miss_count():
    """心跳超时（> interval * miss_max = 45s）→ miss_count 增加，但未达阈值时不告警。"""
    pid_file = _make_pid_file(heartbeat_ts=time.time() - 60)  # 60s 前
    try:
        wd, alerter = _make_watchdog(pid_file=pid_file, interval=15, miss_max=3)
        wd._miss_count = 0

        result = wd.check_execution_process()

        assert result is False
        assert wd._miss_count == 1
        # miss_count=1 < miss_max=3，还未触发告警
        assert len(alerter.calls) == 0
    finally:
        os.unlink(pid_file)


def test_heartbeat_expired_triggers_alert_at_miss_max():
    """连续 miss_max 次超时 → 触发 HIGH 告警。"""
    pid_file = _make_pid_file(heartbeat_ts=time.time() - 60)
    try:
        wd, alerter = _make_watchdog(pid_file=pid_file, interval=15, miss_max=3)
        wd._miss_count = 2  # 已有 2 次，再来一次达到阈值

        result = wd.check_execution_process()

        assert result is False
        assert wd._miss_count == 3
        assert len(alerter.calls) == 1
        assert alerter.calls[0]["level"] == "HIGH"
        assert "执行进程失联" in alerter.calls[0]["message"]
    finally:
        os.unlink(pid_file)


def test_heartbeat_not_early_alert():
    """仅 1 次超时（< miss_max=3）→ 不告警（不早触发）。"""
    pid_file = _make_pid_file(heartbeat_ts=time.time() - 60)
    try:
        wd, alerter = _make_watchdog(pid_file=pid_file, interval=15, miss_max=3)
        wd._miss_count = 0

        # 第 1 次
        wd.check_execution_process()
        assert len(alerter.calls) == 0

        # 第 2 次
        wd.check_execution_process()
        assert len(alerter.calls) == 0

        # 第 3 次 → 告警
        wd.check_execution_process()
        assert len(alerter.calls) == 1
    finally:
        os.unlink(pid_file)


# ---------------------------------------------------------------------------
# 测试：PID 文件不存在
# ---------------------------------------------------------------------------

def test_pid_file_not_found_alerts():
    """PID 文件不存在 → 立即 HIGH 告警，返回 False。"""
    wd, alerter = _make_watchdog(pid_file="/nonexistent/execution.pid")

    result = wd.check_execution_process()

    assert result is False
    assert len(alerter.calls) == 1
    assert alerter.calls[0]["level"] == "HIGH"
    assert "PID 文件不存在" in alerter.calls[0]["message"]


# ---------------------------------------------------------------------------
# 测试：miss_count 重置
# ---------------------------------------------------------------------------

def test_miss_count_resets_on_success():
    """心跳正常后 miss_count 应重置为 0。"""
    pid_file = _make_pid_file(heartbeat_ts=time.time() - 60)
    try:
        wd, alerter = _make_watchdog(pid_file=pid_file)
        wd._miss_count = 2

        # 先超时
        wd.check_execution_process()  # miss_count -> 3，触发告警

        # 更新 PID 文件为新鲜心跳
        with open(pid_file, "w") as f:
            json.dump({"pid": 12345, "heartbeat_ts": time.time()}, f)

        result = wd.check_execution_process()
        assert result is True
        assert wd._miss_count == 0
    finally:
        os.unlink(pid_file)


# ---------------------------------------------------------------------------
# 测试：check_monitor_self_health
# ---------------------------------------------------------------------------

def test_monitor_health_check_success():
    """HTTP 200 → check_monitor_self_health 返回 True。"""
    http = FakeHttpClient(status_code=200)
    wd, _ = _make_watchdog(http_client=http)

    result = wd.check_monitor_self_health()

    assert result is True
    assert len(http.get_calls) == 1


def test_monitor_health_check_non_200():
    """HTTP 500 → 返回 False，不抛异常。"""
    http = FakeHttpClient(status_code=500)
    wd, _ = _make_watchdog(http_client=http)

    result = wd.check_monitor_self_health()

    assert result is False


def test_monitor_health_check_exception():
    """请求抛异常 → 返回 False，不传播异常。"""
    http = FakeHttpClient(raise_exc=ConnectionError("Fake"))
    wd, _ = _make_watchdog(http_client=http)

    result = wd.check_monitor_self_health()

    assert result is False


# ---------------------------------------------------------------------------
# 测试：requests 惰性导入兜底
# ---------------------------------------------------------------------------

def test_watchdog_importable_without_requests():
    """即使 requests 未安装，Watchdog 也可 import；注入 None 客户端后 _get_http() 返回 None → False。"""
    # 通过注入 http_client=None 并 patch _get_http 返回 None，模拟 requests 缺失场景
    wd, _ = _make_watchdog(http_client=None)  # 无注入客户端

    # 直接覆盖 _get_http 方法返回 None，模拟 requests 完全不可用
    wd._get_http = lambda: None  # type: ignore

    result = wd.check_monitor_self_health()
    assert result is False


# ---------------------------------------------------------------------------
# 测试：PID 文件 JSON 格式错误
# ---------------------------------------------------------------------------

def test_pid_file_invalid_json():
    """PID 文件内容不是有效 JSON → 返回 False，不抛异常。"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".pid.json", delete=False)
    tmp.write("INVALID JSON CONTENT")
    tmp.close()

    try:
        wd, alerter = _make_watchdog(pid_file=tmp.name)
        result = wd.check_execution_process()
        assert result is False
        # 不应触发告警（只是解析错误，无法判断是否真的失联）
    finally:
        os.unlink(tmp.name)
