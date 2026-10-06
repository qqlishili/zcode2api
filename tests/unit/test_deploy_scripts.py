"""发版脚本边界：真实 Git 清单 + Bash，SSH/rsync 替换为本地记录桩。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git")
BASH = shutil.which("bash")
if not BASH and GIT:
    # Windows 的 Git 安装通常自带 Bash，不依赖本机个人路径。
    candidate = Path(GIT).resolve().parent.parent / "bin" / "bash.exe"
    if candidate.is_file():
        BASH = str(candidate)

pytestmark = pytest.mark.skipif(not BASH or not GIT, reason="需要 Git 与 Bash")

HARNESS = """
rsync() {
  printf '%s\\0' "$@" > "$DEPLOY_TEST_ARGS"
  cat > "$DEPLOY_TEST_MANIFEST"
  return "${DEPLOY_TEST_RSYNC_EXIT:-0}"
}
ssh() {
  printf '%s\\0' "$@" >> "$DEPLOY_TEST_SSH"
}
export -f rsync ssh
bash "$@"
"""


@pytest.fixture
def deploy_repo(tmp_path):
    """仅在临时仓库建索引，不访问当前项目数据或远端。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(ROOT / "scripts", repo / "scripts")
    tracked = {
        "app/main.py", "cli.py", "requirements.txt", "captcha_node/solver.js",
        "captcha_node/package.json", "captcha_node/package-lock.json",
        "frontend/index.html", "frontend/version",
    }
    private = {
        ".env", ".env.deploy.local", "data/accounts.db", ".venv/private.txt",
        "app/private.txt", "captcha_node/node_modules/private.txt", "frontend/private.txt",
    }
    for name in tracked | private:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    subprocess.run([GIT, "init", "--quiet", str(repo)], check=True, capture_output=True)
    subprocess.run([GIT, "-C", str(repo), "add", "--", *sorted(tracked)], check=True, capture_output=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("DEPLOY_")}
    env.update({
        "DEPLOY_HOST": "example.invalid",
        "DEPLOY_DIR": "/srv/example-hub",
        "DEPLOY_SERVICE": "example-hub.service",
        "DEPLOY_PORT": "9001",
        "DEPLOY_TEST_ARGS": (tmp_path / "rsync.args").as_posix(),
        "DEPLOY_TEST_MANIFEST": (tmp_path / "manifest").as_posix(),
        "DEPLOY_TEST_SSH": (tmp_path / "ssh.args").as_posix(),
    })
    return repo, env, tracked


def _run(deploy_repo, script: str, *args: str):
    repo, env, _ = deploy_repo
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", HARNESS, "deploy-test",
         (repo / "scripts" / script).as_posix(), *args],
        cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


def _record(env, key: str) -> list[str]:
    path = Path(env[key])
    if not path.exists():
        return []
    return [item.decode("utf-8") for item in path.read_bytes().split(b"\0") if item]


@pytest.mark.parametrize("name", ["deploy-common.sh", "deploy-backend.sh", "deploy-frontend.sh"])
def test_bash_syntax(name):
    result = subprocess.run([BASH, "-n", str(ROOT / "scripts" / name)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8")


@pytest.mark.parametrize("script,missing", [
    ("deploy-backend.sh", "DEPLOY_HOST"), ("deploy-backend.sh", "DEPLOY_DIR"),
    ("deploy-backend.sh", "DEPLOY_SERVICE"), ("deploy-backend.sh", "DEPLOY_PORT"),
    ("deploy-frontend.sh", "DEPLOY_HOST"), ("deploy-frontend.sh", "DEPLOY_DIR"),
])
def test_missing_parameter_stops_before_commands(deploy_repo, script, missing):
    _, env, _ = deploy_repo
    env.pop(missing)
    result = _run(deploy_repo, script)
    assert result.returncode != 0
    assert missing in result.stderr
    assert not _record(env, "DEPLOY_TEST_ARGS")
    assert not _record(env, "DEPLOY_TEST_SSH")


@pytest.mark.parametrize("key,value", [
    ("DEPLOY_HOST", "-oProxyCommand=bad"), ("DEPLOY_DIR", "/"),
    ("DEPLOY_DIR", "///"), ("DEPLOY_DIR", "/srv/../data"),
    ("DEPLOY_DIR", "/srv/app;bad"), ("DEPLOY_SERVICE", "bad;service"),
    ("DEPLOY_PORT", "0"), ("DEPLOY_PORT", "65536"),
    ("DEPLOY_FRONTEND_DIR", "/srv/./frontend"),
])
def test_unsafe_target_stops_before_commands(deploy_repo, key, value):
    _, env, _ = deploy_repo
    env[key] = value
    script = "deploy-frontend.sh" if key == "DEPLOY_FRONTEND_DIR" else "deploy-backend.sh"
    assert _run(deploy_repo, script).returncode != 0
    assert not _record(env, "DEPLOY_TEST_ARGS")
    assert not _record(env, "DEPLOY_TEST_SSH")


@pytest.mark.parametrize("mode", ["--dry-run", "--sync-only", ""])
def test_backend_manifest_and_restart_boundary(deploy_repo, mode):
    _, env, tracked = deploy_repo
    result = _run(deploy_repo, "deploy-backend.sh", *([mode] if mode else []))
    assert result.returncode == 0, result.stderr
    manifest = set(_record(env, "DEPLOY_TEST_MANIFEST"))
    assert manifest == {name for name in tracked if not name.startswith("frontend/")}
    args = _record(env, "DEPLOY_TEST_ARGS")
    assert "--delete" not in args
    assert ("--dry-run" in args) == (mode == "--dry-run")
    assert ("预演完成" in result.stdout) == (mode == "--dry-run")
    ssh_args = _record(env, "DEPLOY_TEST_SSH")
    if mode:
        assert not ssh_args
    else:
        assert ssh_args[0] == "example.invalid"
        assert "systemctl restart -- 'example-hub.service'" in ssh_args[1]
        assert "http://127.0.0.1:9001/meta" in ssh_args[1]


@pytest.mark.parametrize("mode", ["--dry-run", ""])
def test_frontend_manifest_custom_path_and_no_restart(deploy_repo, mode):
    _, env, _ = deploy_repo
    env["DEPLOY_FRONTEND_DIR"] = "/srv/static"
    result = _run(deploy_repo, "deploy-frontend.sh", *([mode] if mode else []))
    assert result.returncode == 0, result.stderr
    assert set(_record(env, "DEPLOY_TEST_MANIFEST")) == {"index.html", "version"}
    args = _record(env, "DEPLOY_TEST_ARGS")
    assert args[-1] == "example.invalid:/srv/static/"
    assert "--delete" not in args
    assert ("预演完成" in result.stdout) == (mode == "--dry-run")
    assert not _record(env, "DEPLOY_TEST_SSH")


def test_sync_failure_does_not_restart(deploy_repo):
    _, env, _ = deploy_repo
    env["DEPLOY_TEST_RSYNC_EXIT"] = "23"
    result = _run(deploy_repo, "deploy-backend.sh")
    assert result.returncode == 23
    assert not _record(env, "DEPLOY_TEST_SSH")


def test_unknown_argument_stops_before_commands(deploy_repo):
    _, env, _ = deploy_repo
    assert _run(deploy_repo, "deploy-backend.sh", "--unknown").returncode != 0
    assert not _record(env, "DEPLOY_TEST_ARGS")
