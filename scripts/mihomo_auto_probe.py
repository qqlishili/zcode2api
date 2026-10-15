#!/usr/bin/env python3
"""隔离探测、来源差分、固定大区槽提供器发布与消费者排空。

完整连接定义决定节点身份；来源成员与健康、准入、物理槽分别记账。
日常只刷新提供器，旧身份经当前消费者确认及实际连接排空后才能回收。
基础结构由一次维护转换，周期更新和恢复均不加载整份配置或重启生产。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.constants import CAPTCHA_DEFAULTS, MODEL_NAME_MAP, REGION_PORT_TIERS  # noqa: E402
from app.egress import (  # noqa: E402
    PROTOCOL_VERSION,
    PROVIDER_NAME,
    node_fingerprint,
    process_identity,
    registry_generation,
    validate_registry,
    write_private_json,
)
from app.notify import send_bark_notification  # noqa: E402
from scripts.region_classifier import parse_cf_trace, resolve_region  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("auto_probe")
logging.getLogger('httpx').setLevel(logging.WARNING)

CONFIG_PATH = Path(os.getenv('MIHOMO_CONFIG_PATH', '/etc/mihomo/config.yaml'))
ENV_PATH = REPO_ROOT / '.env'
DATA_PATH = Path(os.getenv('ZCODE_DATA_DIR', str(REPO_ROOT / 'data')))
LIFECYCLE_PATH = DATA_PATH / 'node_lifecycle.json'
REGISTRY_PATH = DATA_PATH / 'egress_registry.json'
RESULTS_PATH = DATA_PATH / 'node_scan_results.json'

CONTROLLER_URL = os.getenv('MIHOMO_CONTROLLER_URL', 'http://127.0.0.1:9090').rstrip('/')
PROBE_READY_SECONDS = 10.0
PROCESS_STOP_SECONDS = 5.0
TRACE_URL = 'http://cloudflare.com/cdn-cgi/trace'
# 单节点业务请求超时：跨境线路 TLS 握手与上游受理需充足余量，旧值 3.5s 会误杀可用节点。
PROBE_REQUEST_SECONDS = float(os.getenv('MIHOMO_PROBE_TIMEOUT', '10.0'))
# trace 定位为旁路证据，超时独立且远小于业务请求。
PROBE_TRACE_SECONDS = 3.0
# 单节点失败前允许的网络重试次数（首次抖动不直接判死节点）。
PROBE_NETWORK_RETRIES = 1
_TZ_BEIJING = timezone(timedelta(hours=8))

def compute_node_fingerprint(proxy: dict) -> str:
    return node_fingerprint(proxy)


def legacy_fingerprint(p: dict) -> str:
    raw = ':'.join((str(p.get('server') or '').strip(), str(p.get('port') or '').strip(),
                    str(p.get('type') or '').strip().lower(), str(p.get('cipher') or '').strip().lower(),
                    str(p.get('uuid') or p.get('password') or '').strip(),
                    str(p.get('sni') or p.get('servername') or '').strip().lower()))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def provider_path(config: dict) -> Path:
    provider = config.get('proxy-providers', {}).get(PROVIDER_NAME, {})
    if provider.get('type') != 'file' or not provider.get('path'):
        raise ProbeError('maintenance_migration_required')
    path = Path(provider['path'])
    return path if path.is_absolute() else CONFIG_PATH.parent / path


def source_snapshot(data: object) -> dict[str, dict] | None:
    if not isinstance(data, dict) or not isinstance(data.get('proxies'), list) or not data['proxies']:
        return None
    nodes = {}
    for proxy in data['proxies']:
        if (not isinstance(proxy, dict) or not isinstance(proxy.get('name'), str) or
                not isinstance(proxy.get('type'), str) or not proxy.get('server') or not proxy.get('port')):
            return None
        fp = compute_node_fingerprint(proxy)
        nodes.setdefault(fp, proxy)
    return nodes


def merge_sources(lifecycle: dict, results: dict) -> dict:
    candidate = copy.deepcopy(lifecycle)
    sources = candidate.setdefault('sources', {})
    for removed in set(sources) - set(results):
        sources.pop(removed)
    for source, snapshot in results.items():
        if snapshot is not None:
            sources[source] = {'nodes': snapshot}
    members = {}
    for source, snapshot in sources.items():
        for fp, proxy in snapshot['nodes'].items():
            members.setdefault(fp, {'proxy': proxy, 'sources': []})['sources'].append(source)
    nodes = candidate.setdefault('nodes', {})
    for fp, member in members.items():
        entry = nodes.setdefault(fp, {'state': 'stale', 'region': 'OTHER', 'last_healthy': 0})
        if entry.get('reclaimed'):
            for field in ('port', 'epoch', 'reclaimed', 'retiring', 'admission'):
                entry.pop(field, None)
        entry.update(proxy=member['proxy'], name=member['proxy']['name'], sources=sorted(member['sources']))
    for fp, entry in nodes.items():
        entry['sources'] = sorted(members.get(fp, {}).get('sources', []))
    return candidate


def get_sub_urls() -> list[str]:
    urls = []
    if ENV_PATH.exists():
        with open(ENV_PATH, encoding='utf-8') as f:
            for line in f:
                if line.startswith('AIRPORT_SUB_URLS='):
                    val = line.split('=', 1)[1].strip().strip('"').strip("'")
                    urls.extend([u.strip() for u in val.split(',') if u.strip()])
    return urls


async def fetch_subscription(url: str) -> tuple[dict | None, dict | None]:
    clean_target = url.strip().strip('"').strip("'")
    if urlparse(clean_target).scheme not in {'http', 'https'}:
        file_path = Path(clean_target.replace("file://", ""))
        if not file_path.is_absolute():
            file_path = REPO_ROOT / file_path
        if file_path.is_file():
            try:
                content = yaml.safe_load(file_path.read_text(encoding="utf-8"))
                logger.info('phase=subscription_loaded source=local')
                return content, None
            except Exception as e:
                logger.error('phase=subscription_failed source=local error=%s', type(e).__name__)
                return None, None
        else:
            logger.warning('phase=subscription_failed source=local reason=missing')
            return None, None

    headers = {
        'User-Agent': 'clash-verge/v1.7.7',
        'Accept': '*/*'
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=False) as client:
            res = await client.get(clean_target, headers=headers)
            if res.status_code != 200:
                logger.warning('phase=subscription_failed source=remote status=%s', res.status_code)
                return None, None

            info_header = res.headers.get('subscription-userinfo', '')
            user_info = {}
            if info_header:
                for part in info_header.split(';'):
                    if '=' in part:
                        k, v = part.strip().split('=', 1)
                        try:
                            user_info[k.lower()] = int(v)
                        except ValueError:
                            pass

            content = yaml.safe_load(res.text)
            return content, user_info
    except Exception as e:
        logger.error('phase=subscription_failed source=remote error=%s', type(e).__name__)
        return None, None


async def check_subscription_expiration(source_name: str, user_info: dict | None) -> None:
    if not user_info:
        return

    expire_ts = user_info.get('expire')
    upload = user_info.get('upload', 0)
    download = user_info.get('download', 0)
    total = user_info.get('total', 0)
    used = upload + download

    now_ts = time.time()
    warnings = []

    if expire_ts and expire_ts > 0:
        expire_dt = datetime.fromtimestamp(expire_ts, tz=_TZ_BEIJING)
        days_left = (expire_ts - now_ts) / 86400.0
        expire_str = expire_dt.strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"[{source_name}] 到期时间: {expire_str} (剩余 {days_left:.1f} 天)")
        if days_left <= 7.0:
            warnings.append(f"[{source_name}] 订阅即将于 {int(days_left)} 天后到期（{expire_str}）！")

    if total > 0:
        used_gb = used / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        pct = (used / total) * 100
        logger.info(f"[{source_name}] 流量用量: {used_gb:.1f}GB / {total_gb:.1f}GB ({pct:.1f}%)")
        if (total - used) / (1024 ** 3) < 10.0 or pct > 95.0:
            warnings.append(f"[{source_name}] 剩余流量不足 10GB (已用 {pct:.1f}%)！")

    if warnings:
        warn_msg = "；".join(warnings)
        try:
            await send_bark_notification("⚠️ 机场订阅临期/告罄预警", warn_msg)
        except Exception as e:
            logger.warning('phase=notification_failed error=%s', type(e).__name__)


class ProbeError(RuntimeError):
    """本轮未提交；消息仅包含可公开的阶段码。"""


def mihomo_binary() -> str:
    binary = os.getenv('MIHOMO_BINARY') or shutil.which('mihomo')
    if not binary:
        raise ProbeError('binary_missing')
    return binary


async def stop_child(process: asyncio.subprocess.Process) -> None:
    """所有退出路径先终止，再升级 kill 并回收。"""
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), PROCESS_STOP_SECONDS)
        except TimeoutError:
            process.kill()
    await asyncio.wait_for(process.wait(), PROCESS_STOP_SECONDS)


async def validate_config(path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix='mp-check-') as directory:
        process = await asyncio.create_subprocess_exec(
            mihomo_binary(), '-t', '-d', directory, '-f', str(path),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), PROBE_READY_SECONDS)
            if process.returncode:
                raise ProbeError('config_invalid')
        finally:
            await stop_child(process)


def owned_tcp_ports(pid: int) -> set[int]:
    """按 socket inode 核验监听归属，不借用其它进程的开放端口。"""
    directory = Path('/proc') / str(pid)
    inodes = set()
    try:
        for fd in (directory / 'fd').iterdir():
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            if target.startswith('socket:['):
                inodes.add(target[8:-1])
        ports = set()
        for table in ('tcp', 'tcp6'):
            for line in (directory / 'net' / table).read_text().splitlines()[1:]:
                parts = line.split()
                if parts[3] == '0A' and parts[9] in inodes:
                    ports.add(int(parts[1].rsplit(':', 1)[1], 16))
        return ports
    except (FileNotFoundError, PermissionError) as err:
        raise ProbeError('listener_owner_unavailable') from err


def config_tcp_listeners(config: dict) -> dict[int, str]:
    result = {}
    for listener in config.get('listeners', []):
        if listener.get('port') and listener.get('type') in {'mixed', 'http', 'socks', 'redir', 'tproxy'}:
            host = listener.get('listen') or '127.0.0.1'
            result[int(listener['port'])] = {'0.0.0.0': '127.0.0.1', '::': '::1'}.get(host, host)
    for key in ('port', 'socks-port', 'mixed-port', 'redir-port', 'tproxy-port'):
        if config.get(key):
            result[int(config[key])] = '127.0.0.1'
    return result


def production_pid(config: dict) -> int:
    ports = set(config_tcp_listeners(config))
    controller = urlparse(CONTROLLER_URL)
    if controller.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ProbeError('controller_not_loopback')
    ports.add(controller.port or (443 if controller.scheme == 'https' else 80))
    for entry in Path('/proc').iterdir():
        if entry.name.isdecimal():
            try:
                if ports <= owned_tcp_ports(int(entry.name)):
                    return int(entry.name)
            except ProbeError:
                continue
    raise ProbeError('production_owner_missing')


async def verify_listeners(pid: int, config: dict) -> None:
    listeners = config_tcp_listeners(config)
    if not set(listeners) <= owned_tcp_ports(pid):
        raise ProbeError('listener_missing')
    for port, host in listeners.items():
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 1.0)
            writer.close()
            await writer.wait_closed()
        except (OSError, TimeoutError) as err:
            raise ProbeError('listener_unreachable') from err


@contextmanager
def update_lock():
    """独立 inode 锁覆盖恢复、外呼、发布和清理。"""
    import fcntl

    path = LIFECYCLE_PATH.parent / '.mihomo-probe.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def probe_account_candidate(accounts: list):
    """探针身份：**优先未被风控的 active 账号**，退而选其它 enabled 且有凭证的。

    为什么优先 active（而非任意有凭证的账号）：
      额度耗尽（exhausted）不等于可安全用作探针身份 —— 其中混杂着**已被上游
      风控**的账号。用被风控的账号探测，**任何出口都会返回 3012**（账号级拒绝，
      与出口无关），会把整池出口误判为不可用。实测（2026-10-15）：

          账号          status      21200    21100    21304     21501
          账号A     exhausted   ❌3012   ❌3012   ❌3012    ❌3012   ← 全灭
          akbgpryk      active      ✅200    ❌3012   ❌网络错  ❌3012

      当时 probe_account_candidate 恰好选中 5735，导致 143 个节点全判 False。

    为什么仍保留 exhausted 兜底：
      池内可能一个 active 都没有（全部额度耗尽但未被风控）。此时用 exhausted
      账号探测仍优于放弃整轮 —— 但结论需结合上面的风险理解。
      disabled 账号不入选：其凭证可能已被吊销。
    """
    usable = [
        a for a in accounts
        if getattr(a, 'enabled', False) and (getattr(a, 'secret', '') or '').strip()
    ]
    # status 是软信号（仅用于优选），缺失时按非 active 处理 —— 不让缺字段的对象崩，
    # 也让只为验证「候选资格」而构造的最小替身保持可用。
    for account in usable:
        if getattr(account, 'status', None) == 'active':
            return account
    return usable[0] if usable else None


def node_reachable(response: httpx.Response, text: str) -> tuple[bool, str | None]:
    """节点可达性判定：复用网关业务错误归一化，区分业务受理与上游故障。

    判据**复用 app/egress_probe._verdict**，不在此另立一套 —— 两处若各写一份，
    就会出现「同一份响应、两种结论」的分裂（本次修复的直接起因）。

    三类判定：
      A 上游受理了业务（200 / 402 额度 / 403 验证码 / 429 限流 / 404 plan-not-found …）
        → **可达** —— 链路端到端打通，障碍在账号或业务层
      B 上游边缘拒绝（业务码 3012 / 归一后 405）
        → **不可达** —— 请求根本没到业务层
      C 上游或链路故障（5xx）
        → **不可达**
    连接失败、TLS 失败与超时在调用点单独处理，同样不计入业务判定。

    为什么必须拒 405/3012：旧实现只拒 `effective >= 500`，把 405 放行，
    导致一轮探活 143 个节点里 124 个被判 active，而它们对 zcode 全部返回
    3012 —— 下游据此装配的出口池大面积失真（实测 2026-10-15）。
    """
    from app import constants
    from app.routes.gateway import _classify_business_error

    effective, code, _concurrency = _classify_business_error(response.status_code, response, text)

    # B 类：上游在边缘层就拒绝，请求根本没到业务层 —— 该出口不可用。
    #   业务码 3012 = 风控拉黑；归一后的 405 = constants.RISK_CONTROL_HTTP_STATUSES。
    #   判据来源与网关路由、egress_probe 同源（constants），不在此另立一套。
    if code == "3012" or effective in constants.RISK_CONTROL_HTTP_STATUSES:
        return False, code or str(effective)

    # C 类：上游或链路自身故障，无法证明出口可用。
    if effective >= 500:
        return False, code or str(effective)

    # A 类：其余一律视为「上游已受理业务」→ 出口可用。
    #   含 200、402（额度耗尽）、403（验证码挑战）、429（限流）、
    #   404（如 plan not found）等业务级拒绝 —— 它们都证明链路端到端打通。
    return True, code or str(effective)


async def probe_request(client: httpx.AsyncClient, url: str, headers: dict, payload: bytes):
    """发送一次业务探测请求；网络层失败返回 None，由调用方决定是否重试。"""
    try:
        return await client.post(url, headers=headers, content=payload)
    except httpx.HTTPError:
        return None


@asynccontextmanager
async def setup_probe_listener(proxies: list[dict]):
    """用短生命周期独立实例探测，绝不写入生产配置。"""
    if sys.platform != 'linux' or not proxies:
        raise ProbeError('probe_requires_linux_candidates')
    binary = mihomo_binary()
    explicit = os.getenv('MIHOMO_PROBE_PORT')
    if explicit and not (explicit.isdecimal() and 1 <= int(explicit) <= 65535):
        raise ProbeError('probe_port_invalid')
    for attempt in range(3):
        with socket.socket() as reservation:
            try:
                reservation.bind(('127.0.0.1', int(explicit) if explicit else 0))
            except OSError as err:
                raise ProbeError('probe_port_busy') from err
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='mp-') as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            path = root / 'config.yaml'
            controller_socket = root / 'ctl.sock'
            if len(os.fsencode(controller_socket)) > 100:
                raise ProbeError('controller_path_too_long')
            names = [p['name'] for p in proxies]
            if len(names) != len(set(names)):
                raise ProbeError('probe_names_duplicate')
            config = {
                'mode': 'rule', 'log-level': 'silent', 'ipv6': False,
                'allow-lan': False, 'bind-address': '127.0.0.1',
                'listeners': [{'name': 'probe-listener', 'type': 'mixed',
                               'port': port, 'listen': '127.0.0.1', 'proxy': 'PROBE_GROUP'}],
                'proxies': proxies,
                'proxy-groups': [{'name': 'PROBE_GROUP', 'type': 'select', 'proxies': names}],
                'rules': ['MATCH,DIRECT'],
            }
            path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            os.chmod(path, 0o600)
            await validate_config(path)
            process = await asyncio.create_subprocess_exec(
                binary, '-d', str(root), '-f', str(path), '-ext-ctl-unix', str(controller_socket),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            retry = False
            try:
                transport = httpx.AsyncHTTPTransport(uds=str(controller_socket), trust_env=False)
                async with httpx.AsyncClient(
                    transport=transport, base_url='http://probe', trust_env=False, timeout=1.0,
                ) as controller:
                    deadline = asyncio.get_running_loop().time() + PROBE_READY_SECONDS
                    while True:
                        if process.returncode is not None:
                            retry = not explicit and attempt < 2
                            if retry:
                                break
                            raise ProbeError('probe_child_exited')
                        try:
                            response = await controller.get('/version')
                            if response.status_code == 200 and port in owned_tcp_ports(process.pid):
                                break
                        except httpx.HTTPError:
                            pass
                        if asyncio.get_running_loop().time() >= deadline:
                            raise ProbeError('probe_readiness_timeout')
                        await asyncio.sleep(0.05)
                    if not retry:
                        yield f'http://127.0.0.1:{port}', controller, process
                        return
            finally:
                await stop_child(process)
    raise ProbeError('probe_bind_race')


async def probe_all_candidates(
    proxies: list[dict],
    lifecycle_db: dict[str, dict],
    proxy_url: str,
    controller: httpx.AsyncClient,
) -> dict[str, dict]:
    """全量串行探测；控制设施失败与单节点失败分开。"""
    from app.agent import build_request
    from app.store import store

    acc = probe_account_candidate(store.list_accounts('zai'))
    if acc is None:
        raise ProbeError('probe_account_missing')
    url, headers, payload = build_request(
        acc, {'model': MODEL_NAME_MAP['glm-5.3-flash'], 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1},
        'fake_param', verify_region=CAPTCHA_DEFAULTS.get('region', 'cn'),
    )
    lifecycle_db = copy.deepcopy(lifecycle_db)
    now = time.time()
    seen = set()
    active = 0
    logger.info('phase=scan_started candidates=%s timeout_s=%s', len(proxies), PROBE_REQUEST_SECONDS)
    for idx, proxy in enumerate(proxies, 1):
        name = proxy['name']
        fp = compute_node_fingerprint(proxy)
        seen.add(fp)
        entry = lifecycle_db.get(fp, {
            'state': 'stale', 'first_seen': now, 'last_healthy': 0.0,
            'consecutive_fails': 0, 'region': 'OTHER', 'latency': 9999,
        })
        entry.update(last_seen=now, name=name, proxy=proxy)
        try:
            response = await controller.put('/proxies/PROBE_GROUP', json={'name': name}, timeout=1.5)
            if response.status_code not in (200, 204):
                raise ProbeError('probe_controller_rejected')
        except httpx.HTTPError as err:
            raise ProbeError('probe_controller_unavailable') from err
        await asyncio.sleep(0.05)
        clean = False
        latency = 9999
        status = 0
        outcome = 'network_fail'
        real_loc = None
        async with httpx.AsyncClient(proxy=proxy_url, timeout=PROBE_REQUEST_SECONDS, trust_env=False) as client:
            # 首次网络抖动不直接判死：有界重试后再计入失败计数。
            for _attempt in range(PROBE_NETWORK_RETRIES + 1):
                started = time.monotonic()
                response = await probe_request(client, url, headers, payload)
                if response is None:
                    continue
                latency = int((time.monotonic() - started) * 1000)
                status = response.status_code
                clean, outcome = node_reachable(response, response.text)
                break
            if clean:
                try:
                    trace = await client.get(TRACE_URL, timeout=PROBE_TRACE_SECONDS)
                    if trace.status_code == 200:
                        real_loc = parse_cf_trace(trace.text)
                except httpx.HTTPError:
                    pass
        if clean:
            entry.update(state='active', consecutive_fails=0, last_healthy=now,
                         latency=latency, region=resolve_region(name, real_loc))
            active += 1
        else:
            entry['consecutive_fails'] = entry.get('consecutive_fails', 0) + 1
            entry['state'] = 'stale'
        lifecycle_db[fp] = entry
        logger.info('phase=node_result index=%s total=%s status=%s reachable=%s outcome=%s latency_ms=%s',
                    idx, len(proxies), status, clean, outcome, latency)
    try:
        response = await controller.get('/version')
        if response.status_code != 200:
            raise ProbeError('probe_controller_rejected')
    except httpx.HTTPError as err:
        raise ProbeError('probe_controller_unavailable') from err
    if not active:
        raise ProbeError('no_active_egress')
    return lifecycle_db


def allocate_region_ports(lifecycle_db: dict[str, dict], baseline: dict | None = None) -> dict[str, dict]:
    """物理旧身份只在已持久回收后释放，同区满槽拒绝追加。"""
    fixed = PROVIDER_NAME in (baseline or {}).get('proxy-providers', {})
    reserved = set() if fixed else set(config_tcp_listeners(baseline or {}))
    owners = {}
    for listener in (baseline or {}).get('listeners', []):
        for proxy in (baseline or {}).get('proxies', []):
            if proxy.get('name') == listener.get('proxy'):
                owners[int(listener['port'])] = compute_node_fingerprint(proxy)
    used = reserved | {n['port'] for n in lifecycle_db.values() if n.get('port') and not n.get('reclaimed')}
    accepted = set()
    for fp, node in lifecycle_db.items():
        if node.get('reclaimed') or (node.get('state') != 'active' and not node.get('port')):
            continue
        region = node.get('region', 'OTHER')
        if region not in REGION_PORT_TIERS:
            region = node['region'] = 'OTHER'
        low, high = REGION_PORT_TIERS[region]
        port = node.get('port')
        if port and not low <= port <= high:
            # 旧槽身份不能因一次地域校正直接覆写，留待明确迁移。
            node['state'] = 'stale'
            continue
        if port:
            if port in accepted or (port in reserved and owners.get(port) != fp):
                raise ProbeError('port_identity_conflict')
            accepted.add(port)
            continue
        if node.get('state') != 'active' or not node.get('sources', ['legacy']):
            continue
        allocated = next((p for p in range(low, high + 1) if p not in used), None)
        if allocated is None:
            logger.warning('phase=allocation_rejected region=%s reason=port_tier_full', region)
            continue
        node['port'] = allocated
        used.add(allocated)
        accepted.add(allocated)
    return lifecycle_db


def slot_name(port: int, identity: str | None) -> str:
    return f'zcode-slot-{port}-' + (identity or 'empty')


def build_provider(nodes: dict) -> bytes:
    occupied = {}
    for fp, node in nodes.items():
        if node.get('port') and not node.get('reclaimed'):
            if node['port'] in occupied:
                raise ProbeError('port_identity_conflict')
            if compute_node_fingerprint(node['proxy']) != fp:
                raise ProbeError('node_identity_invalid')
            occupied[node['port']] = (fp, node)
    proxies = []
    for low, high in REGION_PORT_TIERS.values():
        for port in range(low, high + 1):
            if port in occupied:
                fp, node = occupied[port]
                proxies.append(dict(node['proxy'], name=slot_name(port, fp)))
            else:
                proxies.append({'name': slot_name(port, None), 'type': 'reject'})
    return yaml.safe_dump({'proxies': proxies}, allow_unicode=True, sort_keys=True).encode()


def build_maintenance_structure(baseline: dict, nodes: dict, path: Path) -> dict:
    """仅生成一次维护候选，不加载或改写生产；未知配置完整保留。"""
    config = copy.deepcopy(baseline)
    listeners = config.setdefault('listeners', [])
    groups = config.setdefault('proxy-groups', [])
    by_port = {int(v['port']): v for v in listeners if v.get('port')}
    if len(by_port) != len([v for v in listeners if v.get('port')]):
        raise ProbeError('duplicate_listener_port')
    primary = {int(baseline[k]) for k in ('port', 'mixed-port', 'socks-port', 'redir-port', 'tproxy-port')
               if baseline.get(k)}
    if any(low <= p <= high for p in primary for low, high in REGION_PORT_TIERS.values()):
        raise ProbeError('primary_listener_slot_conflict')
    by_name = {p['name']: p for p in config.get('proxies', [])}
    occupied = {n['port']: fp for fp, n in nodes.items() if n.get('port') and not n.get('reclaimed')}
    if len(occupied) != sum(bool(n.get('port')) and not n.get('reclaimed') for n in nodes.values()):
        raise ProbeError('port_identity_conflict')
    providers = config.setdefault('proxy-providers', {})
    if PROVIDER_NAME in providers:
        raise ProbeError('provider_name_conflict')
    providers[PROVIDER_NAME] = {'type': 'file', 'path': str(path), 'interval': 0}
    for low, high in REGION_PORT_TIERS.values():
        for port in range(low, high + 1):
            group = f'zcode-slot-{port}'
            if group in by_name or any(g['name'] == group for g in groups):
                raise ProbeError('slot_name_conflict')
            if port in by_port:
                listener = by_port[port]
                definition = by_name.get(listener.get('proxy'))
                if (listener.get('type') != 'mixed' or listener.get('listen') != '127.0.0.1' or
                        definition is None or compute_node_fingerprint(definition) != occupied.get(port)):
                    raise ProbeError('managed_listener_conflict')
                listener['proxy'] = group
            else:
                listeners.append({'name': f'mixed-{port}', 'type': 'mixed', 'listen': '127.0.0.1',
                                  'port': port, 'proxy': group})
            groups.append({'name': group, 'type': 'select', 'use': [PROVIDER_NAME],
                           'filter': f'^zcode-slot-{port}-', 'proxies': []})
    return config


def migrate_lifecycle(old: dict, baseline: dict) -> dict:
    if old.get('version') == 2:
        return copy.deepcopy(old)
    nodes = {}
    partial = {}
    for key, node in old.items():
        proxy = node.get('proxy')
        if not proxy or legacy_fingerprint(proxy) != key:
            raise ProbeError('legacy_identity_invalid')
        fp = compute_node_fingerprint(proxy)
        if key in partial and partial[key] != fp or fp in nodes:
            raise ProbeError('legacy_identity_ambiguous')
        partial[key] = fp
        entry = copy.deepcopy(node)
        entry.update(epoch=1, sources=[])
        nodes[fp] = entry
    ports = [n['port'] for n in nodes.values() if n.get('port') and not n.get('reclaimed')]
    if len(ports) != len(set(ports)):
        raise ProbeError('port_identity_conflict')
    by_name = {p['name']: p for p in baseline.get('proxies', [])}
    for listener in baseline.get('listeners', []):
        port = int(listener.get('port', 0))
        if not any(low <= port <= high for low, high in REGION_PORT_TIERS.values()):
            continue
        proxy = by_name.get(listener.get('proxy'))
        if proxy is None:
            raise ProbeError('legacy_listener_unknown')
        fp = compute_node_fingerprint(proxy)
        key = legacy_fingerprint(proxy)
        if key in partial and partial[key] != fp:
            raise ProbeError('legacy_identity_ambiguous')
        if fp not in nodes or nodes[fp].get('port') != port:
            raise ProbeError('legacy_listener_unmatched')
    return {'version': 2, 'fingerprint_version': 2, 'sources': {}, 'nodes': nodes,
            'epochs': {str(n['port']): 1 for n in nodes.values() if n.get('port')}}


def snapshot_paths() -> tuple[Path, ...]:
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    return provider_path(config), LIFECYCLE_PATH, RESULTS_PATH, ENV_PATH, REGISTRY_PATH


def read_snapshot() -> list[dict]:
    result = []
    for path in snapshot_paths():
        if path.is_symlink():
            raise ProbeError('snapshot_symlink')
        exists = path.exists()
        result.append({
            'content': path.read_bytes() if exists else None,
            'mode': stat.S_IMODE(path.stat().st_mode) if exists else 0o600,
        })
    return result


def content_hash(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def sync_directory(path: Path) -> None:
    if sys.platform == 'linux':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write(path: Path, content: bytes | None, mode: int = 0o600) -> None:
    """单文件完整替换，不声称多文件事务原子性。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        path.unlink(missing_ok=True)
        sync_directory(path.parent)
        return
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as file:
            os.chmod(temporary, mode)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        # 注册表缓存按 mtime 观察，避免快速同内容替换保持旧时间。
        if path.exists() and temporary.stat().st_mtime_ns == path.stat().st_mtime_ns:
            os.utime(temporary, ns=(time.time_ns(), path.stat().st_mtime_ns + 1_000_000))
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def snapshot_matches(snapshot: list[dict]) -> bool:
    current = read_snapshot()
    return all(content_hash(a['content']) == content_hash(b['content'])
               and (a['content'] is None or a['mode'] == b['mode'])
               for a, b in zip(current, snapshot, strict=True))


def build_candidate(lifecycle: dict, baseline: dict, old: list[dict]) -> list[dict]:
    lifecycle = copy.deepcopy(lifecycle)
    nodes = lifecycle['nodes']
    allocate_region_ports(nodes, baseline)
    regions = {r: [] for r in REGION_PORT_TIERS}
    slots = {str(p): {'node_id': None, 'epoch': lifecycle.get('epochs', {}).get(str(p), 0),
                     'region': r, 'admission': 'empty'}
             for r, (low, high) in REGION_PORT_TIERS.items() for p in range(low, high + 1)}
    for fp, node in nodes.items():
        if not node.get('port') or node.get('reclaimed'):
            continue
        region = next(r for r, (low, high) in REGION_PORT_TIERS.items() if low <= node['port'] <= high)
        if slots[str(node['port'])]['node_id']:
            raise ProbeError('port_identity_conflict')
        if not node.get('epoch'):
            node['epoch'] = lifecycle.setdefault('epochs', {}).get(str(node['port']), 0) + 1
        lifecycle.setdefault('epochs', {})[str(node['port'])] = node['epoch']
        healthy = node.get('state') == 'active' and region == node.get('region')
        if node.get('retiring'):
            admission = 'draining'
        elif healthy and node.get('sources'):
            admission = 'active'
        elif healthy and not any(n.get('sources') and n.get('state') == 'active' and n.get('port') and
                                  n.get('region') == region and not n.get('reclaimed') for n in nodes.values()):
            admission = 'fallback'
        else:
            admission = 'draining'
        if admission == 'draining' and not node.get('sources'):
            node['retiring'] = True
        node['admission'] = admission
        slots[str(node['port'])] = {'node_id': fp, 'epoch': node['epoch'], 'region': region,
                                    'admission': admission}
        if admission in {'active', 'fallback'}:
            regions[region].append(f'http://127.0.0.1:{node["port"]}')
    regions = {r: sorted(v) for r, v in regions.items()}
    ports = [p for urls in regions.values() for p in urls]
    if not ports:
        raise ProbeError('no_unique_active_egress')
    previous = json.loads(old[-1]['content']) if old[-1]['content'] else {}
    registry = {'regions': regions, 'slots': slots}
    registry['generation'] = registry_generation(registry)
    registry['updated_at'] = (previous.get('updated_at', time.time())
                              if previous.get('generation') == registry['generation'] else time.time())
    validate_registry(registry)
    scan = {'updated_at': registry['updated_at'], 'total_active': len(ports),
            'regions': {r: len(v) for r, v in regions.items()}, 'ports': ports}
    env = old[3]['content']
    if env is not None:
        replacement = ('ZCODE_PROXIES=' + ','.join(ports)).encode()
        result, found = [], False
        for line in env.splitlines(keepends=True):
            if re.match(rb'^(?:export[ \t]+)?ZCODE_PROXIES=', line):
                if not found:
                    newline = b'\r\n' if line.endswith(b'\r\n') else b'\n' if line.endswith(b'\n') else b''
                    result.append(replacement + newline)
                    found = True
            else:
                result.append(line)
        if not found:
            if env and not env.endswith(b'\n'):
                result.append(b'\n')
            result.append(replacement + b'\n')
        env = b''.join(result)
    values = [build_provider(nodes), json.dumps(lifecycle, ensure_ascii=False, indent=2).encode(),
              json.dumps(scan, ensure_ascii=False, indent=2).encode(), env,
              json.dumps(registry, ensure_ascii=False, indent=2).encode()]
    # 相同语义不重写路由或提供器；来源/健康记账仍可更新。
    if previous.get('generation') == registry['generation']:
        values[-1] = old[-1]['content']
    if old[0]['content'] and yaml.safe_load(old[0]['content']) == yaml.safe_load(values[0]):
        values[0] = old[0]['content']
    return [dict(content=value, mode=before['mode']) for value, before in zip(values, old, strict=True)]


def pending_directory() -> Path:
    return LIFECYCLE_PATH.parent / '.mihomo-publish'


def prepare_pending(old: list[dict], new: list[dict], pid: int) -> dict:
    directory = pending_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if (directory / 'pending.json').exists():
        raise ProbeError('pending_already_exists')
    record = {'version': 2, 'pid': pid, 'start': process_identity(pid),
              'base_hash': content_hash(CONFIG_PATH.read_bytes()),
              'paths': [str(p) for p in snapshot_paths()], 'old': [], 'new': []}
    for version, snapshot in (('old', old), ('new', new)):
        for index, item in enumerate(snapshot):
            record[version].append({'hash': content_hash(item['content']), 'mode': item['mode']})
            atomic_write(directory / f'{version}-{index}', item['content'])
    atomic_write(directory / 'pending.json', json.dumps(record).encode())
    return record


def read_pending(record: dict, version: str) -> list[dict]:
    result = []
    for index, item in enumerate(record[version]):
        content = (pending_directory() / f'{version}-{index}').read_bytes() if item['hash'] is not None else None
        if content_hash(content) != item['hash']:
            raise ProbeError('pending_copy_corrupt')
        result.append({'content': content, 'mode': item['mode']})
    return result


def clear_pending() -> None:
    directory = pending_directory()
    # 先移除提交标记；清理中断留下的副本不可误判为未完成发布。
    (directory / 'pending.json').unlink()
    sync_directory(directory)
    for version in ('old', 'new'):
        for index in range(5):
            (directory / f'{version}-{index}').unlink(missing_ok=True)
    directory.rmdir()
    sync_directory(directory.parent)


def check_base(record: dict, config: dict) -> None:
    if (content_hash(CONFIG_PATH.read_bytes()) != record['base_hash'] or
            production_pid(config) != record['pid'] or process_identity(record['pid']) != record['start']):
        raise ProbeError('production_baseline_conflict')


def controller_client(config: dict) -> httpx.AsyncClient:
    secret = config.get('secret')
    return httpx.AsyncClient(base_url=CONTROLLER_URL, trust_env=False, timeout=5,
                             headers={'Authorization': 'Bearer ' + str(secret)} if secret else {})


async def validate_provider(content: bytes, config: dict) -> None:
    with tempfile.TemporaryDirectory(prefix='mp-provider-') as directory:
        root = Path(directory)
        atomic_write(root / 'provider.yaml', content)
        candidate = copy.deepcopy(config)
        candidate['proxy-providers'][PROVIDER_NAME]['path'] = str(root / 'provider.yaml')
        atomic_write(root / 'config.yaml', yaml.safe_dump(candidate, allow_unicode=True).encode())
        await validate_config(root / 'config.yaml')


async def verify_provider(client, content: bytes) -> None:
    expected = {p['name'] for p in yaml.safe_load(content)['proxies']}
    response = await client.get('/providers/proxies/' + PROVIDER_NAME)
    response.raise_for_status()
    actual = {p['name'] for p in response.json().get('proxies', [])}
    if actual != expected:
        raise ProbeError('provider_identity_mismatch')
    response = await client.get('/proxies')
    response.raise_for_status()
    groups = response.json()['proxies']
    for low, high in REGION_PORT_TIERS.values():
        for port in range(low, high + 1):
            name = next((n for n in expected if n.startswith(f'zcode-slot-{port}-')), None)
            group = groups.get(f'zcode-slot-{port}', {})
            if name is None or group.get('all') != [name]:
                raise ProbeError('slot_identity_mismatch')
            if group.get('now') != name:
                response = await client.put('/proxies/' + f'zcode-slot-{port}', json={'name': name})
                response.raise_for_status()
    response = await client.get('/proxies')
    response.raise_for_status()
    groups = response.json()['proxies']
    for low, high in REGION_PORT_TIERS.values():
        for port in range(low, high + 1):
            names = [n for n in expected if n.startswith(f'zcode-slot-{port}-')]
            group = groups.get(f'zcode-slot-{port}', {})
            if group.get('all') != names or group.get('now') != names[0]:
                raise ProbeError('slot_selection_mismatch')


async def load_and_verify(snapshot: list[dict], pid: int) -> None:
    """日常只刷新 file provider；响应丢失以实际身份核验。"""
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    provider_path(config)
    async with controller_client(config) as client:
        try:
            response = await client.put('/providers/proxies/' + PROVIDER_NAME)
            response.raise_for_status()
        except httpx.HTTPError:
            # watcher 可能已生效；只有完整实际核验能确认成功。
            pass
        await verify_provider(client, snapshot[0]['content'])
    await verify_listeners(pid, config)


async def recover_pending() -> None:
    marker = pending_directory() / 'pending.json'
    if not marker.exists():
        return
    record = json.loads(marker.read_bytes())
    if record.get('version') != 2 or record.get('paths') != [str(p) for p in snapshot_paths()]:
        raise ProbeError('pending_paths_conflict')
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    check_base(record, config)
    old, new = read_pending(record, 'old'), read_pending(record, 'new')
    current = read_snapshot()
    for now, before, after in zip(current, old, new, strict=True):
        if not any(content_hash(now['content']) == content_hash(v['content']) and
                   (now['content'] is None or now['mode'] == v['mode']) for v in (before, after)):
            raise ProbeError('snapshot_external_conflict')
    committed = content_hash(current[-1]['content']) == content_hash(new[-1]['content'])
    target = new if committed else old
    await validate_provider(target[0]['content'], config)
    for index, (path, item) in enumerate(zip(snapshot_paths(), target, strict=True)):
        check_base(record, config)
        if not snapshot_matches(current):
            raise ProbeError('snapshot_external_conflict')
        if current[index] != item:
            atomic_write(path, item['content'], item['mode'])
            current[index] = item
        if index == 0:
            await load_and_verify(target, record['pid'])
    check_base(record, config)
    if not snapshot_matches(target):
        raise ProbeError('recovery_incomplete')
    clear_pending()
    logger.info('phase=recovered outcome=%s', 'committed' if committed else 'rolled_back')


async def apply_assembly_and_reload(lifecycle: dict, old: list[dict]) -> None:
    """提供器先准备，注册表最后提交；旧物理身份始终保留。"""
    baseline = yaml.safe_load(CONFIG_PATH.read_bytes())
    if lifecycle.get('base_hash') != content_hash(CONFIG_PATH.read_bytes()):
        raise ProbeError('maintenance_baseline_missing_or_changed')
    new = build_candidate(lifecycle, baseline, old)
    await validate_provider(new[0]['content'], baseline)
    old_nodes = {p['name']: p for p in yaml.safe_load(old[0]['content'])['proxies'] if p['type'] != 'reject'}
    new_nodes = {p['name']: p for p in yaml.safe_load(new[0]['content'])['proxies'] if p['type'] != 'reject'}
    if any(new_nodes.get(name) != proxy for name, proxy in old_nodes.items()):
        raise ProbeError('publication_removed_old_identity')
    if not snapshot_matches(old):
        raise ProbeError('snapshot_external_conflict')
    pid = production_pid(baseline)
    if all(a == b for a, b in zip(old, new, strict=True)):
        async with controller_client(baseline) as client:
            await verify_provider(client, old[0]['content'])
        await verify_listeners(pid, baseline)
        logger.info('phase=committed outcome=unchanged')
        return
    record = prepare_pending(old, new, pid)
    try:
        expected = copy.deepcopy(old)
        for index, (path, item) in enumerate(zip(snapshot_paths(), new, strict=True)):
            check_base(record, baseline)
            if not snapshot_matches(expected):
                raise ProbeError('snapshot_external_conflict')
            if item != expected[index]:
                atomic_write(path, item['content'], item['mode'])
                expected[index] = item
            if index == 0 and item['content'] != old[0]['content']:
                await load_and_verify(new, pid)
            elif index == 0:
                async with controller_client(baseline) as client:
                    await verify_provider(client, item['content'])
        check_base(record, baseline)
        if not snapshot_matches(new):
            raise ProbeError('commit_incomplete')
        clear_pending()
    except (Exception, asyncio.CancelledError):
        await asyncio.shield(recover_pending())
        raise
    logger.info('phase=committed active=%s', sum(len(v) for v in json.loads(new[-1]['content'])['regions'].values()))


def check_service_budget(seconds: float) -> None:
    """受 systemd 调度时读取有效截止；手工执行由脚本有限期限约束。"""
    if not os.getenv('INVOCATION_ID'):
        return
    match = re.search(r'/([^/]+[.]service)(?:/|$)', Path('/proc/self/cgroup').read_text())
    if not match:
        raise ProbeError('unit_budget_unavailable')
    response = subprocess.run(
        ['systemctl', 'show', match[1], '--value', '--property=TimeoutStartUSec'],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout.strip()
    units = {'h': 3600, 'min': 60, 's': 1, 'ms': 0.001, 'us': 0.000001}
    pieces = re.findall(r'([0-9]+(?:[.][0-9]+)?)\s*(min|ms|us|h|s)', response)
    if not pieces or sum(float(value) * units[unit] for value, unit in pieces) < seconds:
        raise ProbeError('unit_budget_insufficient')


def current_consumers() -> dict:
    """只接受当前实际网关/worker 的注册，不把文件心跳当作存活证明。"""
    directory = LIFECYCLE_PATH.parent / 'egress_consumers'
    records = {}
    for path in directory.glob('*.json'):
        if path.name.endswith('.ack.json'):
            continue
        record = json.loads(path.read_bytes())
        try:
            if process_identity(record['pid']) != record['start']:
                continue
        except (FileNotFoundError, ProcessLookupError):
            continue
        if record.get('protocol') != PROTOCOL_VERSION:
            raise ProbeError('consumer_protocol_mismatch')
        records[record['boot_id']] = {k: record[k] for k in ('protocol', 'pid', 'start', 'boot_id')}
    expected = set()
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            cwd = Path(os.readlink(path / 'cwd')).resolve()
            argv = (path / 'cmdline').read_bytes().split(b'\0')
            if cwd == REPO_ROOT and any(Path(os.fsdecode(v)).name in {'cli.py', 'uvicorn', 'gunicorn'} for v in argv):
                expected.add(int(path.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    if not expected or expected != {v['pid'] for v in records.values()}:
        raise ProbeError('consumer_set_unconfirmed')
    return records


def connection_owners(ports: set[int], consumers: dict) -> bool:
    """未知本地消费者阻止回收；Node 子进程由已注册 producer 引用保护。"""
    path = LIFECYCLE_PATH.parent / 'egress_unknown_consumers.json'
    previous = json.loads(path.read_bytes()) if path.is_file() else {}
    unknown = {}
    for pid, record in previous.items():
        try:
            if process_identity(int(pid)) == record['start']:
                unknown[pid] = record
        except (FileNotFoundError, ProcessLookupError):
            continue
    if unknown != previous:
        write_private_json(path, unknown)
    # 曾使用旧槽的未知进程仍存活时，瞬间零连接不能证明其已关闭准入。
    if any(ports.intersection(record['ports']) for record in unknown.values()):
        return False
    inodes = set()
    for table in ('tcp', 'tcp6'):
        for line in (Path('/proc/net') / table).read_text().splitlines()[1:]:
            fields = line.split()
            if int(fields[2].split(':')[1], 16) in ports and fields[3] == '01':
                inodes.add(fields[9])
    if not inodes:
        return True
    allowed = {v['pid'] for v in consumers.values()}
    found = set()
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            owned = set()
            for fd in (path / 'fd').iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                if target.startswith('socket:[') and target[8:-1] in inodes:
                    owned.add(target[8:-1])
            if owned and int(path.name) not in allowed:
                unknown[path.name] = {'start': process_identity(int(path.name)), 'ports': sorted(ports)}
                write_private_json(LIFECYCLE_PATH.parent / 'egress_unknown_consumers.json', unknown)
                return False
            found.update(owned)
        except FileNotFoundError:
            continue
        except PermissionError:
            return False
    return found == inodes


async def retirement_ready(request: dict, client) -> tuple[bool, str]:
    registry = json.loads(REGISTRY_PATH.read_bytes())
    if (registry.get('generation') != request['generation'] or
            current_consumers() != request['consumers']):
        return False, 'consumer_or_generation_changed'
    for boot, consumer in request['consumers'].items():
        path = LIFECYCLE_PATH.parent / 'egress_consumers' / (boot + '.ack.json')
        if not path.is_file():
            return False, 'confirmation_missing'
        ack = json.loads(path.read_bytes())
        if (any(ack.get(k) != v for k, v in consumer.items()) or
                ack.get('request_id') != request['request_id'] or ack.get('generation') != request['generation']):
            return False, 'confirmation_stale'
        for port, slot in request['slots'].items():
            count = ack.get('slots', {}).get(port, {})
            if (any(count.get(k) != v for k, v in slot.items()) or not count.get('admission_closed') or
                    any(count.get(k) != 0 for k in ('operations', 'producers', 'caches', 'tokens', 'fixed'))):
                return False, 'references_pending'
    ports = {int(p) for p in request['slots']}
    if not connection_owners(ports, request['consumers']):
        return False, 'unknown_consumer'
    response = await client.get('/connections')
    response.raise_for_status()
    data = response.json()
    # 当前 Mihomo 对空连接集合序列化为 null；缺字段仍是未知状态。
    connections = data.get('connections')
    if 'connections' in data and connections is None:
        connections = []
    if not isinstance(connections, list):
        return False, 'connections_unavailable'
    for connection in connections:
        metadata = connection.get('metadata', {})
        port = metadata.get('inboundPort')
        if port is None:
            return False, 'connection_unattributed'
        if int(port) in ports:
            return False, 'connections_pending'
        if any(name.startswith(tuple(f'zcode-slot-{p}-' for p in ports))
               for name in connection.get('chains', [])):
            return False, 'connections_pending'
    return True, 'drained'


async def retire_nodes() -> None:
    """独立后置清理；有界等待不回滚路由，不强杀仍在工作的消费者。"""
    marker = LIFECYCLE_PATH.parent / 'egress_reclaim.json'
    if marker.is_file():
        await recover_reclaim()
    registry = json.loads(REGISTRY_PATH.read_bytes())
    lifecycle = json.loads(LIFECYCLE_PATH.read_bytes())
    slots = {p: s for p, s in registry['slots'].items() if s['admission'] == 'draining' and
             lifecycle['nodes'][s['node_id']].get('retiring')}
    if not slots:
        return
    consumers = current_consumers()
    import secrets

    request = {'request_id': secrets.token_hex(16), 'generation': registry['generation'],
               'slots': slots, 'consumers': consumers}
    write_private_json(LIFECYCLE_PATH.parent / 'egress_retirement.json', request)
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    async with controller_client(config) as client:
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            ready, reason = await retirement_ready(request, client)
            if ready:
                break
            if asyncio.get_running_loop().time() >= deadline:
                logger.info('phase=pending_retirement slots=%s reason=%s', len(slots), reason)
                return
            await asyncio.sleep(0.25)
        # 删除前重新确认当前集合与真实连接；准入已永久关闭。
        if not (await retirement_ready(request, client))[0]:
            raise ProbeError('retirement_changed_before_remove')
        updated = copy.deepcopy(lifecycle)
        for slot in slots.values():
            updated['nodes'][slot['node_id']]['reclaimed'] = True
        content = build_provider(updated['nodes'])
        await validate_provider(content, config)
        record = {'request': request, 'pid': production_pid(config),
                  'start': process_identity(production_pid(config)), 'base_hash': content_hash(CONFIG_PATH.read_bytes()),
                  'old_provider': content_hash(provider_path(config).read_bytes()),
                  'new_provider': content_hash(content), 'lifecycle': updated,
                  'old_lifecycle': content_hash(LIFECYCLE_PATH.read_bytes()),
                  'new_lifecycle': content_hash(json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True).encode())}
        atomic_write(LIFECYCLE_PATH.parent / '.egress-reclaim-provider', content)
        write_private_json(marker, record)
        atomic_write(provider_path(config), content)
        await recover_reclaim()


async def recover_reclaim() -> None:
    marker = LIFECYCLE_PATH.parent / 'egress_reclaim.json'
    record = json.loads(marker.read_bytes())
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    check_base(record, config)
    actual = content_hash(provider_path(config).read_bytes())
    if actual not in {record['old_provider'], record['new_provider']}:
        raise ProbeError('reclaim_external_conflict')
    registry = json.loads(REGISTRY_PATH.read_bytes())
    request = record['request']
    target_registry = dict(registry, slots=dict(registry['slots']))
    for port, slot in request['slots'].items():
        current = registry['slots'].get(port)
        empty = dict(slot, node_id=None, admission='empty')
        if current not in (slot, empty):
            raise ProbeError('reclaim_registry_conflict')
        target_registry['slots'][port] = empty
    target_registry['generation'] = registry_generation(target_registry)
    if registry.get('generation') not in {request['generation'], target_registry['generation']}:
        raise ProbeError('reclaim_registry_generation_conflict')
    consumers = current_consumers()
    if consumers != request['consumers'] or request['generation'] != registry['generation']:
        import secrets

        # reject 后重启/提交中断按当前进程重新确认，永不重新开放旧准入。
        request = dict(request, consumers=consumers, generation=registry['generation'],
                       request_id=secrets.token_hex(16))
        record['request'] = request
        write_private_json(LIFECYCLE_PATH.parent / 'egress_retirement.json', request)
        write_private_json(marker, record)
    current_lifecycle = json.loads(LIFECYCLE_PATH.read_bytes())
    if content_hash(LIFECYCLE_PATH.read_bytes()) not in {record['old_lifecycle'], record['new_lifecycle']}:
        raise ProbeError('reclaim_lifecycle_external_conflict')
    if any(current_lifecycle['nodes'].get(slot['node_id'], {}).get('epoch') != slot['epoch']
           for slot in request['slots'].values()):
        raise ProbeError('reclaim_lifecycle_conflict')
    async with controller_client(config) as client:
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            ready, reason = await retirement_ready(record['request'], client)
            if ready:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise ProbeError('reclaim_frozen_' + reason)
            await asyncio.sleep(0.25)
        content = (LIFECYCLE_PATH.parent / '.egress-reclaim-provider').read_bytes()
        if content_hash(content) != record['new_provider']:
            raise ProbeError('reclaim_copy_corrupt')
        atomic_write(provider_path(config), content)
        snapshot = read_snapshot()
        await load_and_verify(snapshot, record['pid'])
        # 监听核验的短连接也会出现在连接表中，等实际关闭后再确认。
        while True:
            ready, reason = await retirement_ready(record['request'], client)
            if ready:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise ProbeError('reclaim_frozen_after_remove_' + reason)
            await asyncio.sleep(0.25)
    # 先持久回收记账，再把槽设 empty；中断恢复仍向前，不打开旧身份。
    atomic_write(LIFECYCLE_PATH, json.dumps(record['lifecycle'], ensure_ascii=False, indent=2, sort_keys=True).encode())
    validate_registry(target_registry)
    atomic_write(REGISTRY_PATH, json.dumps(target_registry, ensure_ascii=False, indent=2).encode())
    marker.unlink()
    (LIFECYCLE_PATH.parent / '.egress-reclaim-provider').unlink()
    sync_directory(marker.parent)
    logger.info('phase=reclaimed slots=%s', len(record['request']['slots']))


async def main() -> int:
    """完整更新单写者；迁移前拒绝日常执行，不自动触碰生产结构。"""
    if sys.platform != 'linux':
        logger.error('phase=not_committed reason=linux_required')
        return 1
    with update_lock() as acquired:
        if not acquired:
            logger.info('phase=skipped reason=already_running')
            return 0
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await recover_pending()
            if (LIFECYCLE_PATH.parent / 'egress_reclaim.json').is_file():
                await recover_reclaim()
            old = read_snapshot()
            lifecycle = json.loads(old[1]['content']) if old[1]['content'] else {}
            if lifecycle.get('version') != 2 or lifecycle.get('base_hash') != content_hash(CONFIG_PATH.read_bytes()):
                raise ProbeError('maintenance_migration_required')
            registry = json.loads(old[-1]['content'])
            validate_registry(registry)
            if not registry.get('slots'):
                raise ProbeError('consumer_protocol_required')
            current_consumers()  # 检查实际进程及当前协议，在订阅/探测外呼之前。
            from app.store import store

            if probe_account_candidate(store.list_accounts('zai')) is None:
                raise ProbeError('probe_account_missing')
            urls = get_sub_urls()
            results = {}
            succeeded = 0
            async with asyncio.timeout(max(15.0, 16.0 * len(urls))):
                for index, url in enumerate(urls, 1):
                    data, info = await fetch_subscription(url)
                    await check_subscription_expiration(f'source-{index}', info)
                    snapshot = source_snapshot(data)
                    results[hashlib.sha256(url.encode()).hexdigest()] = snapshot
                    succeeded += snapshot is not None
            if not succeeded and (urls or not ENV_PATH.is_file()):
                raise ProbeError('all_subscriptions_unverified')
            candidate = merge_sources(lifecycle, results)
            proxies = [dict(n['proxy'], name=f"{n.get('name', 'node')}-{fp}")
                       for fp, n in candidate['nodes'].items() if not n.get('reclaimed')]
            if not proxies:
                raise ProbeError('no_candidates')
            budget = PROBE_READY_SECONDS + 12 * len(proxies)
            check_service_budget(budget + 16 * len(urls) + 120)
            async with asyncio.timeout(budget):
                async with setup_probe_listener(proxies) as (proxy_url, controller, process):
                    candidate['nodes'] = await probe_all_candidates(proxies, candidate['nodes'], proxy_url, controller)
                    if process.returncode is not None:
                        raise ProbeError('probe_child_exited')
            async with asyncio.timeout(120):
                await apply_assembly_and_reload(candidate, old)
                await retire_nodes()
            return 0
        except asyncio.CancelledError:
            logger.error('phase=not_committed reason=cancelled')
            return 1
        except Exception as err:
            reason = str(err) if isinstance(err, ProbeError) else type(err).__name__
            logger.error('phase=not_committed reason=%s old_snapshot_not_revalidated=true', reason)
            return 1
        finally:
            loop.remove_signal_handler(signal.SIGTERM)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
