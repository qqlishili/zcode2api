"""仅在隔离测试进程加载HEAD网关，保存换号契约相对基线的红例。"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest

from app.routes import gateway

source = subprocess.run(
    ["git", "show", "HEAD:app/routes/gateway.py"],
    check=True, capture_output=True, encoding="utf-8",
).stdout
exec(compile(source, "HEAD:app/routes/gateway.py", "exec"), gateway.__dict__)
raise SystemExit(pytest.main([
    "tests/integration/test_gateway_unavailability.py", "-k", "upstream_failure or retry_headers or cooling_and_model", "-q",
    "--basetemp=openspec/changes/clarify-gateway-unavailability-reasons/tmp-baseline-red",
    "-p", "no:cacheprovider",
]))
