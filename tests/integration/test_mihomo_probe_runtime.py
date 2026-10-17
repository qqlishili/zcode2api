"""Linux 真实双 Mihomo + TCP/SSE 全周期验收，不访问生产或公网。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest
import yaml

from app import egress
from app.captcha import CaptchaManager
from app.client_pool import AccountClientPool
from scripts import mihomo_auto_probe as probe

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('mihomo'), reason='需要 Linux 已安装 Mihomo'),
]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def free_tier(size=10) -> tuple[int, int]:
    for _ in range(200):
        start = free_port()
        if start + size > 65535:
            continue
        holders = []
        try:
            for port in range(start, start + size):
                holder = socket.socket()
                holders.append(holder)
                holder.bind(('127.0.0.1', port))
            return start, start + size - 1
        except OSError:
            pass
        finally:
            for holder in holders:
                holder.close()
    raise AssertionError('无法取得隔离百位段')


@asynccontextmanager
async def tcp_upstream():
    """真实 HTTP 代理桩只响应本地请求，包括 CONNECT 和有限长 SSE。"""
    done, started = threading.Event(), asyncio.Event()
    caller = asyncio.get_running_loop()
    clients = set()
    events = []

    async def handle(reader, writer):
        task = asyncio.current_task()
        clients.add(task)
        try:
            while True:
                head = await reader.readuntil(b'\r\n\r\n')
                lines = head.decode().split('\r\n')
                method, target, _ = lines[0].split()
                events.append((time.monotonic(), 'head', method, target))
                if method == 'CONNECT':
                    writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
                    await writer.drain()
                    continue
                length = next((int(line.split(':', 1)[1]) for line in lines[1:]
                               if line.lower().startswith('content-length:')), 0)
                if length:
                    await reader.readexactly(length)
                events.append((time.monotonic(), 'body', method, target))
                path = urlparse(target).path
                if path == '/stream':
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n')
                    caller.call_soon_threadsafe(started.set)
                    index = 0
                    while not done.is_set():
                        chunk = ('data: ' + json.dumps({'text': 'token', 'index': index}) + '\n\n').encode()
                        writer.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n')
                        await writer.drain()
                        index += 1
                        await asyncio.sleep(0.05)
                    chunk = b'data: [DONE]\n\n'
                    writer.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n0\r\n\r\n')
                    await writer.drain()
                else:
                    await asyncio.sleep(0.12)
                    body = b'loc=HK\n' if path.endswith('/trace') else b'{"text":"nonempty"}'
                    writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: ' +
                                 str(len(body)).encode() + b'\r\n\r\n' + body)
                    await writer.drain()
                    events.append((time.monotonic(), 'response', method, target))
                break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            clients.discard(task)

    # 更新器同步持久化不能堵塞测试上游；生产二者在独立进程中运行。
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    server = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
        asyncio.start_server(handle, '127.0.0.1', 0), loop))
    port = server.sockets[0].getsockname()[1]
    try:
        yield port, started, done
    except BaseException:
        print('UPSTREAM_FAILURE_EVENTS=' + json.dumps(events[-30:]))
        raise
    finally:
        done.set()

        async def close():
            server.close()
            await server.wait_closed()
            tasks = list(clients)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(close(), loop))
        finally:
            loop.call_soon_threadsafe(loop.stop)
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()
            loop.close()


@asynccontextmanager
async def isolated_production(tmp_path, monkeypatch, upstream_port, *, full=False, timings=None):
    root = tmp_path / 'production'
    root.mkdir(mode=0o700)
    for name, filename in (('CONFIG_PATH', 'config.yaml'), ('LIFECYCLE_PATH', 'lifecycle.json'),
                           ('RESULTS_PATH', 'scan.json'), ('ENV_PATH', '.env'), ('REGISTRY_PATH', 'registry.json')):
        monkeypatch.setattr(probe, name, root / filename)
    low, high = free_tier(800 if full else 10)
    tiers = ({r: (low + i * 100, low + i * 100 + 99) for i, r in enumerate(probe.REGION_PORT_TIERS)}
             if full else {'HK': (low, high)})
    monkeypatch.setattr(probe, 'REGION_PORT_TIERS', tiers)
    monkeypatch.setattr(egress, 'REGION_PORT_TIERS', tiers)
    control_port = free_port()
    monkeypatch.setattr(probe, 'CONTROLLER_URL', f'http://127.0.0.1:{control_port}')
    proxy = {'name': 'HK-original', 'type': 'http', 'server': '127.0.0.1', 'port': upstream_port}
    fp = probe.compute_node_fingerprint(proxy)
    nodes = {fp: dict(name=proxy['name'], proxy=proxy, state='active', region='HK', port=low,
                     epoch=1, sources=['initial'])}
    if full:
        for region, (begin, end) in tiers.items():
            for port in range(begin, end + 1):
                if port == low:
                    continue
                definition = dict(proxy, username=str(port), name=region + '-capacity')
                nodes[probe.compute_node_fingerprint(definition)] = dict(
                    proxy=definition, state='active', region=region, port=port, epoch=1, sources=['initial'])
    config = probe.build_maintenance_structure(
        {'mode': 'rule', 'log-level': 'debug', 'ipv6': False, 'allow-lan': False,
         'external-controller': f'127.0.0.1:{control_port}', 'secret': 'isolated-secret',
         'rules': ['MATCH,REJECT']}, nodes, root / 'provider.yaml')
    probe.atomic_write(probe.CONFIG_PATH, yaml.safe_dump(config).encode())
    life = {'version': 2, 'fingerprint_version': 2, 'sources': {'initial': {'nodes': {fp: proxy}}},
            'nodes': nodes, 'epochs': {str(low): 1}, 'base_hash': probe.content_hash(probe.CONFIG_PATH.read_bytes())}
    probe.atomic_write(probe.provider_path(config), probe.build_provider(nodes))
    probe.atomic_write(probe.LIFECYCLE_PATH, json.dumps(life).encode())
    probe.atomic_write(probe.RESULTS_PATH, b'{}')
    probe.atomic_write(probe.ENV_PATH, b'OTHER=unchanged\nZCODE_PROXIES=old\n')
    probe.atomic_write(probe.REGISTRY_PATH, b'{"regions":{}}')
    initial = probe.build_candidate(life, config, probe.read_snapshot())
    for path, item in zip(probe.snapshot_paths(), initial, strict=True):
        probe.atomic_write(path, item['content'], item['mode'])
    await probe.validate_config(probe.CONFIG_PATH)
    output = root / 'mihomo-fixture.log'
    started_at = time.monotonic()
    with output.open('wb') as log:
        process = await asyncio.create_subprocess_exec(probe.mihomo_binary(), '-d', str(root), '-f', str(probe.CONFIG_PATH),
                                                       stdout=log, stderr=log)
    try:
        async with asyncio.timeout(10):
            while not (set(range(low, high + 1)) | {control_port}) <= probe.owned_tcp_ports(process.pid):
                assert process.returncode is None
                await asyncio.sleep(0.02)
        await probe.verify_listeners(process.pid, config)
        async with probe.controller_client(config) as controller:
            await probe.verify_provider(controller, probe.provider_path(config).read_bytes())
        if timings is not None:
            timings['startup'] = time.monotonic() - started_at
        try:
            yield config, json.loads(probe.LIFECYCLE_PATH.read_bytes()), process
        except BaseException:
            print('ISOLATED_STARTUP_FAILURE_LOG=' + output.read_text()[-4000:])
            raise
    finally:
        await probe.stop_child(process)


async def continuous_traffic(port, started, done, operation, pid, old_ports):
    """长流先建立，短请求与监听归属采样覆盖操作前后。"""
    stop = asyncio.Event()
    short = []
    samples = []
    chunks = []

    async def stream():
        async with httpx.AsyncClient(proxy=f'http://127.0.0.1:{port}', trust_env=False, timeout=20) as client:
            async with client.stream('GET', 'http://local.test/stream') as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line:
                        chunks.append(line)

    async def requests():
        async with httpx.AsyncClient(proxy=f'http://127.0.0.1:{port}', trust_env=False, timeout=3) as client:
            while not stop.is_set():
                began = time.monotonic()
                try:
                    response = await client.post('http://local.test/messages', json={'text': 'hi'})
                except httpx.HTTPError:
                    print('SHORT_REQUEST_FAILURE=' + json.dumps({'begin': began, 'end': time.monotonic(),
                                                                 'succeeded_before': len(short)}))
                    raise
                assert response.status_code == 200 and response.json()['text'], response.text
                short.append(response.status_code)
                await asyncio.sleep(0.01)

    async def listen():
        while not stop.is_set():
            assert old_ports <= probe.owned_tcp_ports(pid)
            samples.append(pid)
            await asyncio.sleep(0.02)

    stream_task = asyncio.create_task(stream())
    request_task = asyncio.create_task(requests())
    listen_task = asyncio.create_task(listen())
    try:
        ready = asyncio.create_task(started.wait())
        completed, _ = await asyncio.wait({ready, stream_task}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if stream_task in completed:
            await stream_task
        assert ready in completed, '真实 SSE 未建立'
        await asyncio.sleep(0.15)
        # 更新器与客户端在生产中独立运行，进程归属扫描不能阻塞测试客户端。
        result = await asyncio.to_thread(asyncio.run, operation())
        await asyncio.sleep(0.2)
        done.set()
        await stream_task
        stop.set()
        await asyncio.gather(request_task, listen_task)
        assert len(short) >= 3
        assert samples and set(samples) == {pid}
        assert len(chunks) >= 5 and chunks[-1] == 'data: [DONE]'
        assert any('token' in c for c in chunks)
        return result, {'short_requests': len(short), 'sse_events': len(chunks),
                        'listener_samples': len(samples), 'connection_failures': 0, 'normal_terminal': True}
    finally:
        stop.set()
        done.set()
        for task in (stream_task, request_task, listen_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stream_task, request_task, listen_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_full_cycle_and_consumer_fenced_retirement(tmp_path, monkeypatch):
    from app import agent as agent_module
    from app import client_pool
    from app import store as store_module
    from app.routes import gateway

    async with tcp_upstream() as (upstream, started, done):
        async with isolated_production(tmp_path, monkeypatch, upstream) as (config, life, process):
            low = min(probe.config_tcp_listeners(config))
            account = SimpleNamespace(id='consumer', assigned_region='HK', proxy=None, status='active',
                                      enabled=True, secret='isolated-probe-fixture')
            store = SimpleNamespace(list_accounts=lambda *args: [account])
            pool = AccountClientPool(registry_path=probe.REGISTRY_PATH, idle_timeout=0)
            manager = CaptchaManager()
            monkeypatch.setattr(client_pool, 'account_client_pool', pool)
            monkeypatch.setattr(gateway, 'account_client_pool', pool)
            await pool.start_coordinator(manager, store)
            consumers = {pool._consumer['boot_id']: pool._consumer}
            monkeypatch.setattr(probe, 'current_consumers', lambda: dict(consumers))
            monkeypatch.setattr(store_module, 'store', store)
            baseline = probe.CONFIG_PATH.read_bytes()
            old = await pool.acquire_operation(account)
            cm = old.client.stream('GET', 'http://local.test/stream')
            response = await cm.__aenter__()
            upstream_result = gateway._Upstream(response, cm, old.client)
            upstream_result.operation = old
            chunks = []

            async def consume():
                async for line in response.aiter_lines():
                    if line:
                        chunks.append(line)

            streaming = asyncio.create_task(consume())
            await started.wait()
            stop = asyncio.Event()
            short, bindings = [], []

            async def requests():
                while not stop.is_set():
                    async with pool.operation(account) as operation:
                        res = await operation.client.post('http://local.test/messages', json={})
                        assert res.status_code == 200 and res.json()['text']
                        short.append(res.status_code)
                        bindings.append(operation.identity)
                    await pool.prune_idle()
                    await asyncio.sleep(0.01)

            requests_task = asyncio.create_task(requests())
            relay_tasks = set()

            async def relay(reader, writer):
                task = asyncio.current_task()
                relay_tasks.add(task)
                remote_reader, remote_writer = await asyncio.open_connection('127.0.0.1', upstream)

                async def pump(source, target):
                    try:
                        while data := await source.read(8192):
                            target.write(data)
                            await target.drain()
                    finally:
                        target.close()

                try:
                    await asyncio.gather(pump(reader, remote_writer), pump(remote_reader, writer))
                finally:
                    relay_tasks.discard(task)

            relay_server = await asyncio.start_server(relay, '127.0.0.1', 0)
            new_proxy = dict(next(iter(life['nodes'].values()))['proxy'], name='HK-new',
                             port=relay_server.sockets[0].getsockname()[1])
            sub = tmp_path / 'subscription.yaml'
            sub.write_text(yaml.safe_dump({'proxies': [new_proxy]}), encoding='utf-8')
            monkeypatch.setattr(probe, 'get_sub_urls', lambda: [str(sub)])
            monkeypatch.setattr(agent_module, 'build_request', lambda *a, **kw: ('http://local.test/messages', {}, b'{}'))
            monkeypatch.setattr(probe, 'TRACE_URL', 'http://local.test/cdn-cgi/trace')
            try:
                assert await probe.main() == 0
                provider = yaml.safe_load(probe.provider_path(config).read_bytes())['proxies']
                assert any(p['name'] == probe.slot_name(low, old.identity[1]) for p in provider)
                assert not json.loads(probe.LIFECYCLE_PATH.read_bytes())['nodes'][old.identity[1]].get('reclaimed')
                async with pool.operation(account) as replacement:
                    assert replacement.proxy != old.proxy
                    assert (await replacement.client.get('http://local.test/messages')).status_code == 200
                entered, finish = asyncio.Event(), asyncio.Event()

                async def solve(*args, **kwargs):
                    entered.set()
                    await finish.wait()
                    return 'isolated-token'

                async def configs(**kwargs):
                    return {}

                monkeypatch.setattr(manager, '_run_solver', solve)
                monkeypatch.setattr(manager, 'fetch_config', configs)
                monkeypatch.setattr(manager, '_schedule_refill', lambda _: None)
                waiters = [asyncio.create_task(manager.get_verify_param(operation=old)) for _ in range(2)]
                await entered.wait()
                waiters[0].cancel()
                await asyncio.gather(waiters[0], return_exceptions=True)
                assert sum(op.kind == 'producer' for op in pool._operations) == 1
                done.set()
                await streaming
                await upstream_result.close()
                assert not old.client.is_closed
                finish.set()
                assert (await waiters[1])[0] == 'isolated-token'
                assert old.client.is_closed
                await probe.retire_nodes()
                registry = json.loads(probe.REGISTRY_PATH.read_bytes())
                assert registry['slots'][str(low)]['admission'] == 'empty'
                assert json.loads(probe.LIFECYCLE_PATH.read_bytes())['nodes'][old.identity[1]]['reclaimed']
                life = json.loads(probe.LIFECYCLE_PATH.read_bytes())
                another = dict(new_proxy, name='HK-next', username='fixture')
                fp = probe.compute_node_fingerprint(another)
                life['nodes'][fp] = dict(proxy=another, state='active', region='HK', sources=['new'])
                await probe.apply_assembly_and_reload(life, probe.read_snapshot())
                reused = json.loads(probe.REGISTRY_PATH.read_bytes())['slots'][str(low)]
                assert reused['epoch'] == 2 and reused['node_id'] == fp
                assert baseline == probe.CONFIG_PATH.read_bytes() and process.returncode is None
                assert set(probe.config_tcp_listeners(config)) <= probe.owned_tcp_ports(process.pid)
                assert any('token' in chunk for chunk in chunks) and chunks[-1] == 'data: [DONE]'
                assert len(short) >= 5 and any(identity != old.identity for identity in bindings)
                print('MIHOMO_SSE_EVIDENCE=' + json.dumps({'short_successes': len(short), 'nonempty_done': True,
                      'replacement_used': True, 'parent_producer_drained': True, 'same_region_reuse_epoch': 2,
                      'base_unchanged': True, 'pid_preserved': True}))
            finally:
                done.set()
                stop.set()
                results = await asyncio.gather(streaming, requests_task, return_exceptions=True)
                await upstream_result.close()
                await manager.close()
                await pool.stop_coordinator()
                await pool.aclose()
                relay_server.close()
                await relay_server.wait_closed()
                await asyncio.gather(*relay_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        raise result


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['probe_crash', 'cancel', 'interrupted_before', 'interrupted_after',
                                   'forced_before', 'forced_after',
                                   'invalid_config', 'file_failure', 'provider_response_lost'])
async def test_real_faults_preserve_old_traffic(tmp_path, monkeypatch, fault):
    async with tcp_upstream() as (upstream, started, done):
        async with isolated_production(tmp_path, monkeypatch, upstream) as (config, life, process):
            old = probe.read_snapshot()
            baseline = probe.CONFIG_PATH.read_bytes()
            proxy = dict(next(iter(life['nodes'].values()))['proxy'], username='another')
            life['nodes'][probe.compute_node_fingerprint(proxy)] = dict(proxy=proxy, state='active', region='HK', sources=['new'])

            async def operation():
                if fault in {'probe_crash', 'cancel'}:
                    async def scan():
                        async with probe.setup_probe_listener([proxy]) as (_, controller, child):
                            if fault == 'probe_crash':
                                child.kill()
                                await child.wait()
                                with pytest.raises(httpx.HTTPError):
                                    await controller.get('/version')
                            else:
                                await asyncio.sleep(30)
                    task = asyncio.create_task(scan())
                    if fault == 'cancel':
                        await asyncio.sleep(0.25)
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        await task
                elif fault.startswith(('interrupted', 'forced')):
                    new = probe.build_candidate(life, config, old)
                    await probe.validate_provider(new[0]['content'], config)
                    count = 1 if fault.endswith('before') else 5
                    if fault.startswith('forced'):
                        packet = tmp_path / 'interrupted.json'
                        packet.write_text(json.dumps({'config': str(probe.CONFIG_PATH),
                            'paths': [str(p) for p in probe.snapshot_paths()], 'pid': process.pid,
                            'tiers': probe.REGION_PORT_TIERS, 'count': count,
                            'new': [dict(content=v['content'].decode() if v['content'] else None,
                                         mode=v['mode']) for v in new]}))
                        ready = tmp_path / 'interrupted.ready'
                        code = """
import json,pathlib,sys,time
from scripts import mihomo_auto_probe as p
d=json.loads(pathlib.Path(sys.argv[1]).read_bytes())
p.CONFIG_PATH=pathlib.Path(d['config'])
p.LIFECYCLE_PATH,p.RESULTS_PATH,p.ENV_PATH,p.REGISTRY_PATH=map(pathlib.Path,d['paths'][1:])
p.REGION_PORT_TIERS=d['tiers']
new=[dict(content=v['content'].encode() if v['content'] is not None else None,mode=v['mode']) for v in d['new']]
p.prepare_pending(p.read_snapshot(),new,d['pid'])
for path,v in zip(p.snapshot_paths()[:d['count']],new):p.atomic_write(path,v['content'],v['mode'])
pathlib.Path(sys.argv[2]).touch()
time.sleep(30)
"""
                        child = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(packet), str(ready),
                            env=dict(os.environ, PYTHON_DOTENV_DISABLED='1', ZCODE_DATA_DIR=str(tmp_path / 'child-data')),
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                        try:
                            async with asyncio.timeout(10):
                                while not ready.exists():
                                    assert child.returncode is None
                                    await asyncio.sleep(0.02)
                            child.kill()
                            await child.wait()
                            assert child.returncode == -9
                        finally:
                            await probe.stop_child(child)
                    else:
                        probe.prepare_pending(old, new, process.pid)
                        for i in range(count):
                            probe.atomic_write(probe.snapshot_paths()[i], new[i]['content'], new[i]['mode'])
                    await probe.recover_pending()
                    assert probe.snapshot_matches(old if count == 1 else new)
                elif fault == 'invalid_config':
                    proxy['type'] = 'invalid-type'
                    with pytest.raises(probe.ProbeError):
                        await probe.apply_assembly_and_reload(life, old)
                elif fault == 'file_failure':
                    real = probe.atomic_write
                    failed = False

                    def write(path, content, mode=0o600):
                        nonlocal failed
                        if path == probe.RESULTS_PATH and not failed:
                            failed = True
                            raise OSError('isolated-injected')
                        real(path, content, mode)

                    monkeypatch.setattr(probe, 'atomic_write', write)
                    with pytest.raises(OSError):
                        await probe.apply_assembly_and_reload(life, old)
                    assert probe.snapshot_matches(old)
                else:
                    real = probe.controller_client

                    def client(config):
                        result = real(config)
                        put = result.put

                        async def lost(url, **kwargs):
                            response = await put(url, **kwargs)
                            if url.startswith('/providers/'):
                                raise httpx.ReadTimeout('isolated response lost')
                            return response

                        result.put = lost
                        return result

                    monkeypatch.setattr(probe, 'controller_client', client)
                    await probe.apply_assembly_and_reload(life, old)
                assert baseline == probe.CONFIG_PATH.read_bytes()
                assert not (probe.pending_directory() / 'pending.json').exists()
                return True

            _, evidence = await continuous_traffic(min(probe.config_tcp_listeners(config)), started, done,
                                                    operation, process.pid, set(probe.config_tcp_listeners(config)))
            print('MIHOMO_FAULT_EVIDENCE=' + json.dumps(dict(evidence, fault=fault), sort_keys=True))


async def test_all_region_slots_and_simultaneous_probe_resource_peak(tmp_path, monkeypatch):
    import resource

    timings = {}
    async with tcp_upstream() as (upstream, _, _):
        async with isolated_production(tmp_path, monkeypatch, upstream, full=True, timings=timings) as (config, life, process):
            startup = timings['startup']
            assert len(config['listeners']) == len(life['nodes']) == 800
            assert startup < probe.PROBE_READY_SECONDS
            ports = set(probe.config_tcp_listeners(config))
            assert ports <= probe.owned_tcp_ports(process.pid)
            proxies = [dict(n['proxy'], name=fp) for fp, n in life['nodes'].items()]
            peak_rss, peak_fd = 0, 0
            async with probe.setup_probe_listener(proxies) as (url, controller, child):
                for _ in range(5):
                    rss, fds = 0, 0
                    for pid in (process.pid, child.pid):
                        status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines())
                        rss += int(status['VmHWM'].split()[0])
                        fds += len(list(Path(f'/proc/{pid}/fd').iterdir()))
                    peak_rss, peak_fd = max(peak_rss, rss), max(peak_fd, fds)
                    await asyncio.sleep(0.02)
                async with httpx.AsyncClient(proxy=url, trust_env=False) as client:
                    assert (await client.get('http://local.test/messages')).json()['text']
            publish_started = time.monotonic()
            await probe.apply_assembly_and_reload(life, probe.read_snapshot())
            publish = time.monotonic() - publish_started
            assert publish < 120
            for port, _ in probe.REGION_PORT_TIERS.values():
                async with httpx.AsyncClient(proxy=f'http://127.0.0.1:{port}', trust_env=False) as routed:
                    assert (await routed.get('http://local.test/messages')).json()['text']
            assert peak_fd < resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            print('MIHOMO_CAPACITY_EVIDENCE=' + json.dumps({'slots': 800, 'assigned_nodes': 800,
                'startup_seconds': round(startup, 3), 'publish_seconds': round(publish, 3),
                'simultaneous_peak_rss_kib': peak_rss, 'simultaneous_peak_fds': peak_fd,
                'same_pid': process.returncode is None, 'listeners_complete': True}))


@pytest.mark.parametrize('stop_after', ['reject', 'lifecycle', 'registry'])
async def test_reject_interruption_reconfirms_new_consumer_before_reuse(tmp_path, monkeypatch, stop_after):
    async with tcp_upstream() as (upstream, _, _):
        async with isolated_production(tmp_path, monkeypatch, upstream) as (config, life, process):
            account = SimpleNamespace(id='consumer', assigned_region='HK', proxy=None)
            store = SimpleNamespace(list_accounts=lambda: [account])
            pool = AccountClientPool(registry_path=probe.REGISTRY_PATH)
            manager = CaptchaManager()
            await pool.start_coordinator(manager, store)
            monkeypatch.setattr(probe, 'current_consumers', lambda: {pool._consumer['boot_id']: pool._consumer})
            old_fp, old_node = next(iter(life['nodes'].items()))
            port = old_node['port']
            old_node['sources'] = []
            new_proxy = dict(old_node['proxy'], username='replacement')
            life['nodes'][probe.compute_node_fingerprint(new_proxy)] = dict(
                proxy=new_proxy, state='active', region='HK', sources=['replacement'])
            await probe.apply_assembly_and_reload(life, probe.read_snapshot())
            marker = probe.LIFECYCLE_PATH.parent / 'egress_reclaim.json'
            original, unlink = probe.atomic_write, Path.unlink
            failed = False

            def write(path, content, mode=0o600):
                nonlocal failed
                target = probe.LIFECYCLE_PATH if stop_after == 'reject' else probe.REGISTRY_PATH
                if stop_after != 'registry' and path == target and marker.exists() and not failed:
                    failed = True
                    raise OSError('isolated interruption')
                return original(path, content, mode)

            def remove(path, *args, **kwargs):
                nonlocal failed
                if stop_after == 'registry' and path == marker and not failed:
                    failed = True
                    raise OSError('isolated interruption')
                return unlink(path, *args, **kwargs)

            monkeypatch.setattr(probe, 'atomic_write', write)
            monkeypatch.setattr(Path, 'unlink', remove)
            try:
                with pytest.raises(OSError, match='isolated interruption'):
                    await probe.retire_nodes()
                record = json.loads(marker.read_bytes())
                assert marker.exists() and probe.slot_name(port, old_fp) not in {
                    p['name'] for p in yaml.safe_load(probe.provider_path(config).read_bytes())['proxies']}
                async with httpx.AsyncClient(proxy=f'http://127.0.0.1:{port}', trust_env=False) as client:
                    assert (await client.get('http://local.test/messages')).status_code != 200
                await pool.stop_coordinator()
                await pool.start_coordinator(manager, store)
                new_boot = pool._consumer['boot_id']
                assert new_boot not in record['request']['consumers']
                await probe.recover_reclaim()
                assert not marker.exists()
                registry = json.loads(probe.REGISTRY_PATH.read_bytes())
                assert registry['slots'][str(port)]['admission'] == 'empty'
                assert json.loads(probe.LIFECYCLE_PATH.read_bytes())['nodes'][old_fp]['reclaimed']
                ack = json.loads((probe.LIFECYCLE_PATH.parent / 'egress_consumers' / (new_boot + '.ack.json')).read_bytes())
                assert ack['request_id'] != record['request']['request_id']
                assert probe.CONFIG_PATH.read_bytes() == yaml.safe_dump(config).encode()
                assert process.returncode is None
                print('MIHOMO_RECLAIM_EVIDENCE=' + json.dumps({'interruption': stop_after,
                      'current_consumer_reconfirmed': True, 'old_admission_reopened': False, 'empty_verified': True}))
            finally:
                await manager.close()
                await pool.aclose()
                await pool.stop_coordinator()


@pytest.mark.asyncio
async def test_sigterm_cleans_child_and_lock(tmp_path):
    script = Path(probe.__file__).resolve()
    ready = tmp_path / 'ready'
    code = """
import asyncio
import importlib.util
import pathlib
import signal
import time
import sys

spec = importlib.util.spec_from_file_location('probe_under_test', sys.argv[1])
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
probe.LIFECYCLE_PATH = pathlib.Path(sys.argv[2]) / 'lifecycle.json'

async def run():
    with probe.update_lock() as acquired:
        assert acquired
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        async with probe.setup_probe_listener([{'name': 'HK-test', 'type': 'direct'}]) as (_, controller, child):
            pathlib.Path(sys.argv[3]).write_text(str(child.pid))
            await asyncio.sleep(30)

try:
    asyncio.run(run())
except asyncio.CancelledError:
    pass
"""
    env = dict(os.environ, PYTHON_DOTENV_DISABLED='1', ZCODE_DATA_DIR=str(tmp_path / 'import-data'))
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(script), str(tmp_path), str(ready),
                                                    env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(10):
            while not ready.exists():
                if process.returncode is not None:
                    out, err = await process.communicate()
                    pytest.fail('隔离辅助进程失败: ' + (out + err).decode()[-1000:])
                await asyncio.sleep(0.02)
        child_pid = int(ready.read_text())
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), 10)
        assert process.returncode == 0
        assert not Path(f'/proc/{child_pid}').exists()
    finally:
        await probe.stop_child(process)
