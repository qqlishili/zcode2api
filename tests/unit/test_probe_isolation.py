"""周期探测隔离、参数透传和固定五文件发布回归。"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from scripts import mihomo_auto_probe as probe


def probe_account_stub(enabled: bool = True):
    """探针账号替身：满足探针身份判据（凭证有效且启用），对齐真实 Account 契约。"""
    return SimpleNamespace(secret="probe.fixture.sig", enabled=enabled)


@pytest.fixture
def isolated_files(tmp_path, monkeypatch):
    from app import egress
    tiers = {'HK': (21100, 21102)}
    monkeypatch.setattr(probe, 'REGION_PORT_TIERS', tiers)
    monkeypatch.setattr(egress, 'REGION_PORT_TIERS', tiers)
    paths = [tmp_path / n for n in ('config.yaml', 'lifecycle.json', 'scan.json', '.env', 'registry.json')]
    for name, path in zip(('CONFIG_PATH', 'LIFECYCLE_PATH', 'RESULTS_PATH', 'ENV_PATH', 'REGISTRY_PATH'), paths, strict=True):
        monkeypatch.setattr(probe, name, path)
    proxy = {'name': 'HK-old', 'type': 'http', 'server': '127.0.0.1', 'port': 32001}
    config = {'log-level': 'silent', 'secret': 'test-secret', 'rules': ['MATCH,DIRECT'],
              'proxy-groups': [], 'proxies': [proxy],
              'listeners': [{'name': 'mixed-21100', 'type': 'mixed', 'port': 21100,
                             'listen': '127.0.0.1', 'proxy': proxy['name']}], 'ipv6': False}
    fp = probe.compute_node_fingerprint(proxy)
    nodes = {fp: {'name': proxy['name'], 'proxy': proxy, 'state': 'active', 'region': 'HK',
                  'port': 21100, 'epoch': 1, 'sources': ['fixture']}}
    config = probe.build_maintenance_structure(config, nodes, tmp_path / 'provider.yaml')
    paths[0].write_text(yaml.safe_dump(config), encoding='utf-8')
    lifecycle = {'version': 2, 'sources': {}, 'nodes': nodes, 'epochs': {'21100': 1},
                 'base_hash': probe.content_hash(paths[0].read_bytes())}
    probe.provider_path(config).write_bytes(probe.build_provider(nodes))
    paths[1].write_text(json.dumps(lifecycle), encoding='utf-8')
    paths[2].write_text('{"old": true}', encoding='utf-8')
    paths[3].write_bytes(b'# keep\r\nOTHER=value\r\nZCODE_PROXIES=old\r\n')
    paths[4].write_text('{"regions": {"HK": ["http://127.0.0.1:21100"]}}', encoding='utf-8')
    return config, lifecycle, probe.read_snapshot()



@pytest.mark.asyncio
async def test_probe_setup_never_replaces_business_listeners(isolated_files, monkeypatch):
    """红复现修复后，缺少二进制也不能修改生产监听或调用服务命令。"""
    _, _, old = isolated_files
    monkeypatch.setenv('MIHOMO_BINARY', str(probe.CONFIG_PATH.parent / 'missing-mihomo'))
    calls = []
    monkeypatch.setattr(os, 'system', calls.append)
    with pytest.raises((probe.ProbeError, FileNotFoundError)):
        async with probe.setup_probe_listener([{'name': 'HK-test', 'type': 'direct'}]):
            pytest.fail('不存在的二进制不应就绪')
    assert probe.snapshot_matches(old)
    assert calls == []
    tree = ast.parse(Path(probe.__file__).read_text(encoding='utf-8'))
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and isinstance(n.func.value, ast.Name) and n.func.value.id == 'os'
                   and n.func.attr == 'system' for n in ast.walk(tree))


@pytest.mark.asyncio
async def test_probe_uses_only_explicit_controller_and_proxy(isolated_files, monkeypatch):
    from app import agent as agent_module
    from app import store as store_module

    proxies = [{'name': 'HK-test', 'type': 'http', 'server': '127.0.0.1', 'port': 32001}]
    monkeypatch.setattr(store_module, 'store', SimpleNamespace(list_accounts=lambda _: [probe_account_stub()]))
    monkeypatch.setattr(agent_module, 'build_request', lambda *a, **kw: ('http://model.test/messages', {}, b'{}'))
    monkeypatch.setattr(probe, 'TRACE_URL', 'http://trace.test/cdn-cgi/trace')
    targets, creations = [], []
    original = httpx.AsyncClient

    def response(request):
        targets.append((request.method, str(request.url)))
        return httpx.Response(200, text='loc=HK\n' if request.url.host == 'trace.test' else '{}')

    def client(**kwargs):
        creations.append(kwargs.copy())
        kwargs.pop('proxy')
        return original(transport=httpx.MockTransport(response), **kwargs)

    async with original(transport=httpx.MockTransport(response), base_url='http://probe', trust_env=False) as controller:
        monkeypatch.setattr(probe.httpx, 'AsyncClient', client)
        result = await probe.probe_all_candidates(proxies, {}, 'http://127.0.0.1:32002', controller)
    assert next(iter(result.values()))['state'] == 'active'
    assert targets == [('PUT', 'http://probe/proxies/PROBE_GROUP'), ('POST', 'http://model.test/messages'),
                       ('GET', 'http://trace.test/cdn-cgi/trace'), ('GET', 'http://probe/version')]
    assert all(c['proxy'] == 'http://127.0.0.1:32002' and c['trust_env'] is False for c in creations)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['controller', 'zero_active', 'no_account'])
async def test_incomplete_scan_keeps_old_snapshot(isolated_files, monkeypatch, failure):
    from app import agent as agent_module
    from app import store as store_module

    config, _, old = isolated_files
    accounts = [] if failure == 'no_account' else [probe_account_stub()]
    monkeypatch.setattr(store_module, 'store', SimpleNamespace(list_accounts=lambda _: accounts))
    monkeypatch.setattr(agent_module, 'build_request', lambda *a, **kw: ('http://model.test/messages', {}, b'{}'))
    original = httpx.AsyncClient

    def respond(request):
        return httpx.Response(500 if failure == 'controller' or request.url.host == 'model.test' else 200)

    monkeypatch.setattr(probe.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), trust_env=False))
    async with original(transport=httpx.MockTransport(respond), base_url='http://probe') as controller:
        with pytest.raises(probe.ProbeError):
            await probe.probe_all_candidates(config['proxies'], {}, 'http://127.0.0.1:32002', controller)
    assert probe.snapshot_matches(old)


def test_candidate_preserves_old_identities_and_env(isolated_files):
    config, db, old = isolated_files
    node = next(iter(db['nodes'].values()))
    node['name'] = 'HK-renamed'
    node['proxy'] = dict(node['proxy'], name='HK-renamed')
    candidate = probe.build_candidate(db, config, old)
    assert yaml.safe_load(candidate[0]['content']) == yaml.safe_load(old[0]['content'])
    assert candidate[3]['content'] == b'# keep\r\nOTHER=value\r\nZCODE_PROXIES=http://127.0.0.1:21100\r\n'
    old[3]['content'] = None
    assert probe.build_candidate(db, config, old)[3]['content'] is None


def test_candidate_appends_duplicate_names_without_redirecting_old_port(isolated_files):
    config, db, old = isolated_files
    original = next(iter(db['nodes'].values()))['proxy']
    new_proxy = dict(original, port=32003)
    fp = probe.compute_node_fingerprint(new_proxy)
    db['nodes'][fp] = dict(proxy=new_proxy, region='HK', state='active', port=21101, sources=['fixture'])
    candidate = yaml.safe_load(probe.build_candidate(db, config, old)[0]['content'])['proxies']
    before = yaml.safe_load(old[0]['content'])['proxies']
    assert candidate[0] == before[0]
    assert candidate[1]['port'] == 32003
    assert candidate[1]['name'].startswith('zcode-slot-21101-')
    assert len({p['name'] for p in candidate}) == len(candidate)


@pytest.fixture
def publication_helpers(isolated_files, monkeypatch):
    config, db, _ = isolated_files
    proxy = dict(next(iter(db['nodes'].values()))['proxy'], port=32003)
    db['nodes'][probe.compute_node_fingerprint(proxy)] = dict(proxy=proxy, state='active', region='HK',
                                                           port=21101, sources=['fixture'])
    monkeypatch.setattr(probe, 'process_identity', lambda _: 'fixture-start')
    events = []

    async def validate(_):
        events.append('validate')

    async def load(snapshot, pid):
        events.append('load')
        assert pid == 123

    monkeypatch.setattr(probe, 'production_pid', lambda _: 123)
    monkeypatch.setattr(probe, 'validate_config', validate)
    monkeypatch.setattr(probe, 'load_and_verify', load)
    return events


@pytest.mark.asyncio
async def test_registry_is_last_and_no_target_written_before_load(isolated_files, publication_helpers, monkeypatch):
    _, db, old = isolated_files
    events = publication_helpers
    original = probe.atomic_write
    writes = []

    def write(path, content, mode=0o600):
        if path in probe.snapshot_paths():
            assert events.count('load') == (0 if path == probe.snapshot_paths()[0] else 1)
            if path != probe.REGISTRY_PATH:
                assert probe.REGISTRY_PATH.read_bytes() == old[-1]['content']
            writes.append(path)
        return original(path, content, mode)

    monkeypatch.setattr(probe, 'atomic_write', write)
    before = probe.REGISTRY_PATH.stat().st_mtime_ns
    await probe.apply_assembly_and_reload(db, old)
    assert writes == list(probe.snapshot_paths())
    assert probe.REGISTRY_PATH.stat().st_mtime_ns != before
    assert not probe.pending_directory().exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault_index', range(5))
async def test_each_file_failure_recovers_old_snapshot(isolated_files, publication_helpers, monkeypatch, fault_index):
    _, db, old = isolated_files
    original = probe.atomic_write
    failed = False

    def write(path, content, mode=0o600):
        nonlocal failed
        if path == probe.snapshot_paths()[fault_index] and not failed:
            failed = True
            raise OSError('injected')
        return original(path, content, mode)

    monkeypatch.setattr(probe, 'atomic_write', write)
    with pytest.raises(OSError):
        await probe.apply_assembly_and_reload(db, old)
    assert probe.snapshot_matches(old)
    assert not probe.pending_directory().exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('replaced', [0, 2, 4, 5])
async def test_process_stop_recovers_or_finishes_by_registry(isolated_files, publication_helpers, replaced):
    config, db, old = isolated_files
    new = probe.build_candidate(db, config, old)
    probe.prepare_pending(old, new, 123)
    for index in range(replaced):
        probe.atomic_write(probe.snapshot_paths()[index], new[index]['content'], new[index]['mode'])
    await probe.recover_pending()
    assert probe.snapshot_matches(new if replaced == 5 else old)
    assert not probe.pending_directory().exists()


@pytest.mark.asyncio
async def test_external_conflict_is_not_overwritten(isolated_files, publication_helpers):
    config, db, old = isolated_files
    probe.prepare_pending(old, probe.build_candidate(db, config, old), 123)
    probe.ENV_PATH.write_text('OTHER=human-change\n', encoding='utf-8')
    with pytest.raises(probe.ProbeError, match='external_conflict'):
        await probe.recover_pending()
    assert probe.ENV_PATH.read_text(encoding='utf-8') == 'OTHER=human-change\n'
    assert (probe.pending_directory() / 'pending.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['validation', 'reject', 'timeout', 'listener'])
async def test_failed_load_does_not_publish(isolated_files, publication_helpers, monkeypatch, fault):
    _, db, old = isolated_files
    calls = 0

    async def load(snapshot, pid):
        nonlocal calls
        calls += 1
        if calls == 1:
            if fault == 'timeout':
                raise httpx.ReadTimeout('injected')
            raise probe.ProbeError(fault)

    async def validate(_):
        raise probe.ProbeError('validation')

    monkeypatch.setattr(probe, 'load_and_verify', load)
    if fault == 'validation':
        monkeypatch.setattr(probe, 'validate_config', validate)
    with pytest.raises((probe.ProbeError, httpx.ReadTimeout)):
        await probe.apply_assembly_and_reload(db, old)
    assert probe.snapshot_matches(old)
    assert not (probe.pending_directory() / 'pending.json').exists()


@pytest.mark.skipif(sys.platform != 'linux', reason='生产 Linux 的 rename 与打开读者语义')
def test_atomic_json_never_exposes_partial_content(tmp_path):
    path = tmp_path / 'registry.json'
    probe.atomic_write(path, b'{"version": 0}')
    done, errors = threading.Event(), []

    def read():
        while not done.is_set():
            try:
                json.loads(path.read_bytes())
            except Exception as err:
                errors.append(type(err).__name__)

    thread = threading.Thread(target=read)
    thread.start()
    try:
        for i in range(20):
            probe.atomic_write(path, json.dumps({'version': i, 'ports': list(range(500))}).encode())
    finally:
        done.set()
        thread.join(5)
    assert errors == []


@pytest.mark.asyncio
async def test_stop_child_escalates_and_reaps(tmp_path, monkeypatch):
    if sys.platform != 'linux':
        pytest.skip('进程信号由 Linux 实测')
    ready = tmp_path / 'ready'
    code = 'import signal,time,pathlib,sys; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(sys.argv[1]).touch(); time.sleep(30)'
    child = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(ready),
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                await asyncio.sleep(0.01)
        monkeypatch.setattr(probe, 'PROCESS_STOP_SECONDS', 0.05)
        await probe.stop_child(child)
        assert child.returncode == -9
        assert not Path(f'/proc/{child.pid}').exists()
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()


def test_lock_rejects_second_process_before_work(isolated_files):
    if sys.platform != 'linux':
        pytest.skip('flock 在 Linux 实测')
    with probe.update_lock() as first:
        assert first
        with probe.update_lock() as second:
            assert not second


@pytest.mark.asyncio
async def test_main_overlap_exits_before_external_work(isolated_files, monkeypatch):
    if sys.platform != 'linux':
        pytest.skip('完整入口互斥在 Linux 实测')
    calls = []
    monkeypatch.setattr(probe, 'get_sub_urls', lambda: calls.append('subscription'))
    with probe.update_lock() as acquired:
        assert acquired
        assert await probe.main() == 0
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['account_missing', 'candidates_missing', 'scan_timeout', 'controller_mid_scan'])
async def test_main_failure_keeps_all_five_files(isolated_files, monkeypatch, failure):
    if sys.platform != 'linux':
        pytest.skip('完整 Linux 入口的信号和锁')
    from contextlib import asynccontextmanager

    from app import store as store_module

    config, db, old = isolated_files
    registry = probe.build_candidate(db, config, old)[-1]["content"]
    probe.atomic_write(probe.REGISTRY_PATH, registry)
    old = probe.read_snapshot()
    accounts = [] if failure == 'account_missing' else [probe_account_stub()]
    monkeypatch.setattr(store_module, 'store', SimpleNamespace(list_accounts=lambda _: accounts))
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    monkeypatch.setattr(probe, 'current_consumers', lambda: {})
    monkeypatch.setattr(probe, 'get_sub_urls', lambda: ['isolated-source'])

    async def fetch(_):
        return ({'proxies': [] if failure == 'candidates_missing' else config['proxies']}, None)

    @asynccontextmanager
    async def setup(_):
        yield 'http://127.0.0.1:32002', None, SimpleNamespace(returncode=None)

    async def scan(*_):
        if failure == 'scan_timeout':
            raise TimeoutError
        raise probe.ProbeError('probe_controller_unavailable')

    monkeypatch.setattr(probe, 'fetch_subscription', fetch)
    monkeypatch.setattr(probe, 'setup_probe_listener', setup)
    monkeypatch.setattr(probe, 'probe_all_candidates', scan)
    assert await probe.main() == 1
    assert probe.snapshot_matches(old)


@pytest.mark.skipif(sys.platform != 'linux', reason='完整入口信号和锁')
@pytest.mark.parametrize('removed', [False, True])
async def test_all_sources_failed_preserves_or_explicit_removal_updates_members(isolated_files, monkeypatch, removed):
    from contextlib import asynccontextmanager

    from app import store as store_module

    config, db, old = isolated_files
    fp, node = next(iter(db['nodes'].items()))
    db['sources'] = {'configured': {'nodes': {fp: node['proxy']}}}
    probe.atomic_write(probe.LIFECYCLE_PATH, json.dumps(db).encode())
    probe.atomic_write(probe.REGISTRY_PATH, probe.build_candidate(db, config, probe.read_snapshot())[-1]['content'])
    old = probe.read_snapshot()
    monkeypatch.setattr(store_module, 'store', SimpleNamespace(list_accounts=lambda _: [probe_account_stub()]))
    monkeypatch.setattr(probe, 'current_consumers', lambda: {})
    monkeypatch.setattr(probe, 'get_sub_urls', lambda: [] if removed else ['failed-source'])
    monkeypatch.delenv('INVOCATION_ID', raising=False)
    published = []

    async def fetch(_):
        return None, None

    @asynccontextmanager
    async def setup(_):
        yield 'http://isolated.test', None, SimpleNamespace(returncode=None)

    async def scan(proxies, nodes, *_):
        assert nodes[fp]['sources'] == []
        return nodes

    async def publish(candidate, previous):
        published.append(candidate)

    async def retire():
        pass

    monkeypatch.setattr(probe, 'fetch_subscription', fetch)
    monkeypatch.setattr(probe, 'setup_probe_listener', setup)
    monkeypatch.setattr(probe, 'probe_all_candidates', scan)
    monkeypatch.setattr(probe, 'apply_assembly_and_reload', publish)
    monkeypatch.setattr(probe, 'retire_nodes', retire)
    assert await probe.main() == (0 if removed else 1)
    assert len(published) == int(removed)
    assert probe.snapshot_matches(old)


@pytest.mark.asyncio
async def test_explicit_busy_probe_port_rejected(isolated_files, monkeypatch):
    if sys.platform != 'linux':
        pytest.skip('Linux 探测入口')
    import socket

    _, _, old = isolated_files
    with socket.socket() as holder:
        holder.bind(('127.0.0.1', 0))
        monkeypatch.setenv('MIHOMO_PROBE_PORT', str(holder.getsockname()[1]))
        monkeypatch.setenv('MIHOMO_BINARY', sys.executable)
        with pytest.raises(probe.ProbeError, match='port_busy'):
            async with probe.setup_probe_listener([{'name': 'HK-test', 'type': 'direct'}]):
                pytest.fail('占用端口不应进入探测')
    assert probe.snapshot_matches(old)


@pytest.mark.asyncio
async def test_child_early_exit_is_reaped(isolated_files, monkeypatch, tmp_path):
    if sys.platform != 'linux':
        pytest.skip('Linux 子进程回收')
    binary = tmp_path / 'early-mihomo'
    binary.write_text('#!/bin/sh\ncase " $* " in *" -t "*) exit 0;; esac\nexit 1\n', encoding='utf-8')
    binary.chmod(0o700)
    monkeypatch.setenv('MIHOMO_BINARY', str(binary))
    _, _, old = isolated_files
    with pytest.raises(probe.ProbeError, match='child_exited'):
        async with probe.setup_probe_listener([{'name': 'HK-test', 'type': 'direct'}]):
            pytest.fail('早退子进程不应就绪')
    assert probe.snapshot_matches(old)
