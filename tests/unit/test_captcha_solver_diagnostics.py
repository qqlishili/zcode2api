"""不可信 stderr 与任务内诊断的安全边界。"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app import settings
from app.captcha import CaptchaManager, CaptchaSolveError, _solver_summary, _SolverError


def summary(**changes):
    return {"v": 1, "code": "param_too_short", "stage": "extract", "exit_code": 6,
            "param_chars": 76, "elapsed_ms": 20, **changes}


@pytest.mark.parametrize("value", [None, {"v": 2}, {"code": "unlisted"}, {"v": True},
    {"stage": "<script>fixture</script>"}, {"exit_code": 4}, {"secret": "fixture-value"},
    {"param_chars": True}, {"param_chars": -1}, {"elapsed_ms": "fixture"}, {"code": []}])
def test_untrusted_summary_falls_back(value):
    data = b"private raw fixture\n" if value is None else b"SOLVER_DIAG=" + json.dumps(summary(**value)).encode()
    assert _solver_summary(data, 6)["reason"] == "param_invalid_unknown"
    assert "fixture" not in json.dumps(_solver_summary(data, 6))


def test_prefix_length_and_valid_summary():
    data = json.dumps(summary()).encode()
    assert _solver_summary(b"wrong=" + data, 6)["reason"] == "param_invalid_unknown"
    assert _solver_summary(b"SOLVER_DIAG={broken", 6)["reason"] == "param_invalid_unknown"
    assert _solver_summary(b"SOLVER_DIAG=" + b" " * 2048 + data, 6)["reason"] == "param_invalid_unknown"
    assert _solver_summary(b"SOLVER_DIAG=" + data, 6)["reason"] == "param_too_short"
    assert _solver_summary(b"", 0)["reason"] == "solver_no_output"
    assert _solver_summary(b"", -9)["reason"] == "solver_failed_unknown"


async def test_missing_node_and_solver_are_safe(monkeypatch, tmp_path):
    manager = CaptchaManager()
    monkeypatch.setattr(settings, "CAPTCHA_SOLVER_JS", tmp_path / "absent.js")
    with pytest.raises(_SolverError, match="^solver_unavailable$"):
        await manager._run_solver("fixture", "fixture", "fixture")
    solver = tmp_path / "solver.js"
    solver.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "CAPTCHA_SOLVER_JS", solver)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("private fixture")))
    with pytest.raises(_SolverError, match="^solver_unavailable$"):
        await manager._run_solver("fixture", "fixture", "fixture")


async def test_interleaved_producers_keep_local_diagnostics(monkeypatch):
    manager = CaptchaManager()
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    monkeypatch.setattr(manager, "fetch_config", AsyncMock(return_value={}))

    async def run(*args, proxy=None):
        if proxy == "http://a.test:80":
            await asyncio.sleep(0)
        raise _SolverError("param_too_short" if proxy == "http://a.test:80" else "sdk_on_error",
                           "extract" if proxy == "http://a.test:80" else "run")

    monkeypatch.setattr(manager, "_run_solver", run)
    results = await asyncio.gather(*(manager.get_verify_param(proxy=p)
                                   for p in ("http://a.test:80", "http://b.test:80")), return_exceptions=True)
    assert [r.diagnostics["last_solver_code"] for r in results] == ["param_too_short", "sdk_on_error"]
    assert len({r.diagnostics["solve_id"] for r in results}) == 2
    assert all("elapsed_ms" in r.diagnostics for r in results)
    assert str(CaptchaSolveError("compatibility fixture")) == "compatibility fixture"
    await manager.close()
