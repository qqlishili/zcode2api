"""二审修复的回归防护（1/3）：读循环结束必须让 is_alive() 说真话。

背景（2026-10-15 二审发现）：
    _read_loop 在异常或 stdout EOF 时只打日志就返回，而 _ready 仍 True、
    进程 returncode 仍 None，于是 is_alive() 继续返回 True。网关会把请求继续
    交给这个"哑"宿主，每个都等到 PLUGIN_HEAD_TIMEOUT（300s），且永不恢复。
"""

from __future__ import annotations

import asyncio

import pytest

from app.channels.plugin_channel import PluginHost, PluginHostError


class _FakeStdout:
    """可编排的假 stdout：先给一行，然后抛异常或 EOF。"""

    def __init__(self, mode: str) -> None:
        self._mode = mode
        self._served = False

    async def readline(self) -> bytes:
        if not self._served:
            self._served = True
            if self._mode == "raise":
                raise OSError("模拟 stdout 读取异常")
            return b""            # 立即 EOF
        return b""


class _FakeProc:
    def __init__(self, stdout) -> None:  # noqa: ANN001
        self.stdout = stdout
        self.returncode = None     # 进程"还活着"
        self.stdin = None

    async def wait(self) -> int:
        return 0


def _host_with(stdout) -> PluginHost:  # noqa: ANN001
    host = PluginHost()
    host._proc = _FakeProc(stdout)      # type: ignore[assignment]
    host._ready = True
    return host


def test_read_loop_eof_marks_host_unavailable() -> None:
    """stdout EOF → _ready 必须置 False，否则 is_alive() 会撒谎。"""
    host = _host_with(_FakeStdout("eof"))
    assert host.is_alive() is True

    asyncio.run(host._read_loop())

    assert host._ready is False, "读循环结束后 _ready 必须为 False"
    assert host.is_alive() is False, "is_alive 必须反映真实可用性"


def test_read_loop_exception_marks_host_unavailable() -> None:
    """读循环抛异常同样要标记不可用（否则请求会白等 300s）。"""
    host = _host_with(_FakeStdout("raise"))
    asyncio.run(host._read_loop())
    assert host._ready is False
    assert host.is_alive() is False


def test_read_loop_wakes_pending_on_eof() -> None:
    """EOF 时所有等待中的请求必须被唤醒为异常，而不是永远挂住。"""
    host = _host_with(_FakeStdout("eof"))
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        host._pending[1] = fut
        loop.run_until_complete(host._read_loop())
        assert fut.done(), "等待者必须被唤醒"
        # 断言的是宿主自己抛的类型，不是宽泛的 Exception（ruff B017）
        with pytest.raises(PluginHostError):
            fut.result()
    finally:
        loop.close()


def test_normal_close_does_not_warn_ready_already_false() -> None:
    """正常关闭路径（aclose 先置 _ready=False）不应触发「读循环结束」告警分支。"""
    host = _host_with(_FakeStdout("eof"))
    host._ready = False        # 模拟 aclose 已置位
    asyncio.run(host._read_loop())
    assert host._ready is False    # 保持 False，不误报
