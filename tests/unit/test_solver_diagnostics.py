"""Node 纯校验协议：使用合成参数，不初始化 SDK 或网络。"""

import base64
import json
import subprocess

import pytest

from app import settings


@pytest.fixture
def params():
    def encode(data):
        return base64.b64encode(json.dumps(data).encode()).decode()
    return {"valid": encode({"securityToken": "fixture-value-" * 15}),
            "short": encode({"certifyId": "fixture-id"}),
            "missing": encode({"padding": "fixture-value-" * 20}),
            "invalid": "!" * 220, "empty": None}


@pytest.mark.parametrize("debug", ["", "1"])
def test_parameter_validation_and_safe_summary(params, debug):
    script = """
const d = require('./solver_diagnostics');
const params = JSON.parse(process.argv[1]);
for (const [name, value] of Object.entries(params)) {
  try { d.extractVerifyParam(value); console.log(JSON.stringify({name, valid:true})); }
  catch (err) { console.log(JSON.stringify({name, summary:d.makeDiagnostic(err, 12)})); }
}
"""
    import os
    result = subprocess.run([settings.NODE_PATH, "-e", script, json.dumps(params)],
                            cwd=settings.CAPTCHA_SOLVER_DIR, capture_output=True, text=True,
                            env={**os.environ, "CAPTCHA_DEBUG": debug}, timeout=5)
    assert result.returncode == 0, result.stderr
    rows = {row["name"]: row for row in map(json.loads, result.stdout.splitlines())}
    assert rows["valid"]["valid"] is True
    for name, code in [("short", "param_too_short"), ("missing", "param_missing_security_token"),
                       ("invalid", "param_not_base64_json"), ("empty", "param_empty")]:
        diag = rows[name]["summary"]
        assert diag["code"] == code and diag["stage"] == "extract"
        assert set(diag) == {"v", "code", "stage", "exit_code", "param_chars", "elapsed_ms"}
    assert "fixture-value" not in result.stdout and "fixture-id" not in result.stdout


def test_emit_failure_without_debug():
    result = subprocess.run([settings.NODE_PATH, "-e",
        "const d=require('./solver_diagnostics'); d.emitFailure(d.failure('sdk_failed'), 1);"],
        cwd=settings.CAPTCHA_SOLVER_DIR, capture_output=True, text=True, timeout=5)
    assert result.stderr.startswith("SOLVER_DIAG=")
    assert len(result.stderr.encode()) <= 2048
    assert json.loads(result.stderr.removeprefix("SOLVER_DIAG="))["exit_code"] == 4
