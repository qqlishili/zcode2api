"""Linux 多进程排空确认绑定、固定引用及无前台流量回归。"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from app import egress
from app.egress import process_identity, write_private_json
from scripts import mihomo_auto_probe as probe
from tests.integration.test_mihomo_probe_runtime import free_tier
from tests.unit.test_egress_operations import write_registry

pytestmark = [pytest.mark.integration, pytest.mark.skipif(sys.platform != "linux", reason="真实 /proc 启动身份")]

WORKER = """
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
from app.captcha import CaptchaManager
from app.client_pool import AccountClientPool
from app import egress

async def run():
    root=Path(sys.argv[1]);egress.REGION_PORT_TIERS=json.loads((root/'ports.json').read_bytes())
    pool=AccountClientPool(registry_path=root/'registry.json')
    old_port=egress.REGION_PORT_TIERS['HK'][0]
    manager=CaptchaManager();account=SimpleNamespace(id='worker',assigned_region='HK',proxy=None)
    store=SimpleNamespace(list_accounts=lambda: [account])
    held=await pool.acquire_operation(account) if sys.argv[2]=='hold' else None
    await pool.start_coordinator(manager,store)
    (root/(str(__import__('os').getpid())+'.ready')).touch()
    try:
        while not (root/'stop').exists():
            if (root/'release').exists() and held:
                await pool.release_operation(held);held=None
            if (root/'fixed').exists():account.proxy='http://127.0.0.1:'+str(old_port)
            else:account.proxy=None
            await asyncio.sleep(.05)
    finally:
        if held:await pool.release_operation(held)
        await manager.close();await pool.aclose();await pool.stop_coordinator()
asyncio.run(run())
"""


@pytest.fixture
async def consumers(tmp_path, monkeypatch):
    root = tmp_path / "consumers"
    root.mkdir()
    script = root / "cli.py"
    script.write_text(WORKER, encoding="utf-8")
    low, high = free_tier(2)
    tiers = {"HK": (low, high)}
    (root / "ports.json").write_text(json.dumps(tiers))
    monkeypatch.setattr(probe, "REGION_PORT_TIERS", tiers)
    monkeypatch.setattr(egress, "REGION_PORT_TIERS", tiers)
    monkeypatch.setattr(probe, "REPO_ROOT", root)
    monkeypatch.setattr(probe, "LIFECYCLE_PATH", root / "lifecycle.json")
    monkeypatch.setattr(probe, "REGISTRY_PATH", root / "registry.json")
    write_registry(probe.REGISTRY_PATH, [(low, "old", 1, "active")])
    processes = []

    async def start(mode="hold"):
        env = dict(os.environ, PYTHON_DOTENV_DISABLED="1", ZCODE_DATA_DIR=str(root / "private"),
                   PYTHONPATH=str(Path(probe.__file__).resolve().parents[1]))
        child = await asyncio.create_subprocess_exec(sys.executable, str(script), str(root), mode,
                    cwd=root, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        processes.append(child)
        async with asyncio.timeout(10):
            while not (root / (str(child.pid) + ".ready")).exists():
                if child.returncode is not None:
                    _, error = await child.communicate()
                    pytest.fail(error.decode())
                await asyncio.sleep(.02)
        return child
    yield root, start
    (root / "stop").touch()
    for child in processes:
        try:
            await asyncio.wait_for(child.wait(), 5)
        finally:
            await probe.stop_child(child)


async def wait_ack(root, request):
    async with asyncio.timeout(5):
        while True:
            values = []
            for boot in request["consumers"]:
                path = root / "egress_consumers" / (boot + ".ack.json")
                if path.is_file():
                    ack = json.loads(path.read_bytes())
                    if ack.get("request_id") == request["request_id"]:
                        values.append(ack)
            if len(values) == len(request["consumers"]):
                return values
            await asyncio.sleep(.05)


async def test_all_current_workers_and_stale_or_fixed_confirmation(consumers, monkeypatch):
    root, start = consumers
    low, high = probe.REGION_PORT_TIERS["HK"]
    first = await start()
    await start("idle")
    current = probe.current_consumers()
    assert len(current) == 2
    registry = write_registry(probe.REGISTRY_PATH, [(low, "old", 1, "draining"), (high, "new", 1, "active")])
    request = {"request_id": "current-request", "generation": registry["generation"],
               "slots": {str(low): registry["slots"][str(low)]}, "consumers": current}
    write_private_json(root / "egress_retirement.json", request)
    ack = await wait_ack(root, request)
    assert sum(a["slots"][str(low)]["operations"] for a in ack) == 1
    response = httpx.MockTransport(lambda _: httpx.Response(200, json={"connections": []}))
    async with httpx.AsyncClient(transport=response, base_url="http://controller") as client:
        assert await probe.retirement_ready(request, client) == (False, "references_pending")
        (root / "release").touch()
        async with asyncio.timeout(5):
            while not (await probe.retirement_ready(request, client))[0]:
                await asyncio.sleep(.05)
        (root / "fixed").touch()
        async with asyncio.timeout(5):
            while (await probe.retirement_ready(request, client))[0]:
                await asyncio.sleep(.05)
        assert await probe.retirement_ready(request, client) == (False, "references_pending")
        (root / "fixed").unlink()
        async with asyncio.timeout(5):
            while not (await probe.retirement_ready(request, client))[0]:
                await asyncio.sleep(.05)
        old_ack = root / "egress_consumers" / (next(iter(current)) + ".ack.json")
        corrupted = json.loads(old_ack.read_bytes())
        corrupted["request_id"] = "old-request"
        write_private_json(old_ack, corrupted)
        assert await probe.retirement_ready(request, client) == (False, "confirmation_stale")
        first.kill()
        await first.wait()
        replacement = await start("idle")
        assert process_identity(replacement.pid) != current[next(k for k, v in current.items() if v["pid"] == first.pid)]["start"]
        assert await probe.retirement_ready(request, client) == (False, "consumer_or_generation_changed")
        current = probe.current_consumers()
        request = dict(request, request_id="new-request", consumers=current)
        write_private_json(root / "egress_retirement.json", request)
        await wait_ack(root, request)
        assert (await probe.retirement_ready(request, client))[0]


async def test_unknown_socket_owner_and_unattributed_connection_block_reclaim(consumers, monkeypatch):
    root, start = consumers
    low, high = probe.REGION_PORT_TIERS["HK"]
    await start("idle")
    current = probe.current_consumers()
    # 只枚举本测试的真实进程，避免宿主机不可读 /proc 项先触发观测失败。
    real_iterdir = Path.iterdir
    processes = [Path("/proc") / str(pid) for pid in (os.getpid(), *(v["pid"] for v in current.values()))]
    monkeypatch.setattr(Path, "iterdir", lambda path: iter(processes) if path == Path("/proc") else real_iterdir(path))
    registry = write_registry(probe.REGISTRY_PATH, [(low, "old", 1, "draining"), (high, "new", 1, "active")])
    request = {"request_id": "socket-request", "generation": registry["generation"],
               "slots": {str(low): registry["slots"][str(low)]}, "consumers": current}
    write_private_json(root / "egress_retirement.json", request)
    await wait_ack(root, request)
    async def handle(reader, writer):
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        assert not probe.connection_owners({port}, current)
        unknown = json.loads((probe.LIFECYCLE_PATH.parent / "egress_unknown_consumers.json").read_bytes())
        assert unknown[str(os.getpid())] == {"start": process_identity(os.getpid()), "ports": [port]}
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()
    assert not probe.connection_owners({port}, current)
    response = httpx.MockTransport(lambda _: httpx.Response(200, json={"connections": [{"metadata": {}}]}))
    async with httpx.AsyncClient(transport=response, base_url="http://controller") as client:
        assert await probe.retirement_ready(request, client) == (False, "connection_unattributed")
