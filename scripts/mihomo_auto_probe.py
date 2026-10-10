#!/usr/bin/env python3
"""VPS 多机场原生订阅周期拉取、百位大区段物理装配、风控探针清洗与热重载服务。

核心架构与契约：
1. 市场供给驱动的大区百位段隔离：
   - HK: 21100~21199, TW: 21200~21299, JP: 21300~21399, SG: 21400~21499
   - US: 21500~21599, KR: 21600~21699, EU: 21700~21799, OTHER: 21800~21899
2. 全凭证物理指纹 (server:port:type:uuid/secret:sni) 结合 active/stale/tombstone 增量三态机。
3. 槽位占位与在线路由严格解耦：
   - Mihomo 监听表保留 active + stale 节点（12 小时缓冲期，消除换机场断崖）；
   - data/egress_registry.json 仅对外发布 active 干净节点（杜绝流量进入 stale 黑洞）。
4. 真机 Trace 出口 IP 校正：调用 cdn-cgi/trace 识别真实 loc 国家代码，击穿假地名欺骗。
5. Mihomo Controller API PUT /configs 零断流平滑热重载，保护 SSE 流式长连接。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.constants import CAPTCHA_DEFAULTS, DEFAULT_MODEL, REGION_PORT_TIERS
from app.store import store
from app.agent import build_request
from app.notify import send_bark_notification
from scripts.region_classifier import resolve_region, parse_cf_trace

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("auto_probe")

CONFIG_PATH = Path(os.getenv('MIHOMO_CONFIG_PATH', '/etc/mihomo/config.yaml'))
ENV_PATH = REPO_ROOT / '.env'
LIFECYCLE_PATH = REPO_ROOT / 'data' / 'node_lifecycle.json'
REGISTRY_PATH = REPO_ROOT / 'data' / 'egress_registry.json'
RESULTS_PATH = REPO_ROOT / 'data' / 'node_scan_results.json'

PROBE_PORT = int(os.getenv('MIHOMO_PROBE_PORT', '21080'))
CONTROLLER_URL = os.getenv('MIHOMO_CONTROLLER_URL', 'http://127.0.0.1:9090').rstrip('/')
_TZ_BEIJING = timezone(timedelta(hours=8))

STALE_EXPIRY_SECONDS = 12 * 3600  # 12 小时观察期


def compute_node_fingerprint(p: dict) -> str:
    """提取节点全凭证物理指纹（避免同名不同节点混淆）。"""
    server = str(p.get('server') or '').strip()
    port = str(p.get('port') or '').strip()
    ntype = str(p.get('type') or '').strip().lower()
    cipher = str(p.get('cipher') or '').strip().lower()
    secret = str(p.get('uuid') or p.get('password') or '').strip()
    sni = str(p.get('sni') or p.get('servername') or '').strip().lower()
    raw = f"{server}:{port}:{ntype}:{cipher}:{secret}:{sni}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def get_sub_urls() -> list[str]:
    urls = []
    if ENV_PATH.exists():
        with open(ENV_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('AIRPORT_SUB_URLS='):
                    val = line.split('=', 1)[1].strip().strip('"').strip("'")
                    urls.extend([u.strip() for u in val.split(',') if u.strip()])
    return urls


async def fetch_subscription(url: str) -> tuple[dict | None, dict | None]:
    headers = {
        'User-Agent': 'clash-verge/v1.7.7',
        'Accept': '*/*'
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            res = await client.get(url, headers=headers)
            if res.status_code != 200:
                logger.warning(f"订阅拉取失败 ({url.split('?')[0][:45]}...): HTTP {res.status_code}")
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
        logger.error(f"订阅拉取异常 ({url.split('?')[0][:45]}...): {e}")
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
            logger.warning(f"Bark 推送异常: {e}")


def load_lifecycle_db() -> dict[str, dict]:
    """读取存量节点生命周期状态库。"""
    if not LIFECYCLE_PATH.exists():
        return {}
    try:
        with open(LIFECYCLE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取生命周期库异常: {e}")
        return {}


def save_lifecycle_db(db: dict[str, dict]) -> None:
    """持久化节点生命周期状态库。"""
    LIFECYCLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LIFECYCLE_PATH, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


async def setup_probe_listener(proxies: list[dict]) -> bool:
    """为 Mihomo 准备单端口动态切换探活环境。"""
    if not proxies:
        return False
    proxy_names = [p['name'] for p in proxies]
    probe_cfg = {
        'mode': 'rule',
        'log-level': 'warning',
        'ipv6': False,
        'allow-lan': False,
        'bind-address': '127.0.0.1',
        'external-controller': '127.0.0.1:9090',
        'secret': '',
        'listeners': [{
            'name': 'probe-listener',
            'type': 'mixed',
            'port': PROBE_PORT,
            'listen': '127.0.0.1',
            'proxy': 'PROBE_GROUP'
        }],
        'proxies': proxies,
        'proxy-groups': [{
            'name': 'PROBE_GROUP',
            'type': 'select',
            'proxies': proxy_names
        }],
        'rules': ['MATCH,DIRECT']
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.safe_dump(probe_cfg, f, allow_unicode=True)

    # 首次探活初始化重启一次
    os.system('systemctl restart mihomo')
    await asyncio.sleep(2.0)
    return True


async def probe_all_candidates(
    proxies: list[dict],
    lifecycle_db: dict[str, dict],
) -> dict[str, dict]:
    """全量真机探活并结合 cdn-cgi/trace 进行真实大区分类。"""
    acc = next((a for a in store.list_accounts('zai') if a.status == 'active'), None)
    if not acc:
        logger.error("数据库中未找到 active 账号，无法构建风控探活请求！")
        return lifecycle_db

    url, headers, payload = build_request(
        acc,
        {'model': DEFAULT_MODEL, 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1},
        'fake_param',
        verify_region=CAPTCHA_DEFAULTS.get('region', 'cn'),
    )

    now = time.time()
    total = len(proxies)
    logger.info(f"开始执行全量 {total} 节点真机风控探活 (POST /messages + cdn-cgi/trace)...")

    async with httpx.AsyncClient(timeout=4.0) as ctrl:
        for idx, p in enumerate(proxies, 1):
            pname = p['name']
            fp = compute_node_fingerprint(p)
            node_entry = lifecycle_db.get(fp, {
                'name': pname,
                'proxy': p,
                'state': 'stale',
                'first_seen': now,
                'last_seen': now,
                'last_healthy': 0.0,
                'consecutive_fails': 0,
                'region': 'OTHER',
                'latency': 9999,
            })
            node_entry['last_seen'] = now
            node_entry['name'] = pname
            node_entry['proxy'] = p

            # 1. 切换探针出口
            try:
                s_res = await ctrl.put(f"{CONTROLLER_URL}/proxies/PROBE_GROUP", json={"name": pname}, timeout=1.5)
                if s_res.status_code not in (200, 204):
                    logger.debug(f"[{idx}/{total}] 切换代理组失败: {pname}")
                    node_entry['consecutive_fails'] = node_entry.get('consecutive_fails', 0) + 1
                    lifecycle_db[fp] = node_entry
                    continue
            except Exception:
                node_entry['consecutive_fails'] = node_entry.get('consecutive_fails', 0) + 1
                lifecycle_db[fp] = node_entry
                continue

            await asyncio.sleep(0.05)

            # 2. 执行模型出词真机探活
            is_clean = False
            lat = 9999
            try:
                async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{PROBE_PORT}", timeout=3.5) as probe:
                    t0 = time.time()
                    res = await probe.post(url, headers=headers, content=payload)
                    lat = int((time.time() - t0) * 1000)
                    if res.status_code == 200:
                        is_clean = True
                    elif res.status_code == 405:
                        logger.warning(f"[{idx}/{total}] 🚫 405 WAF 拦截: {pname}")
                    else:
                        logger.warning(f"[{idx}/{total}] ⚠️ HTTP {res.status_code}: {pname}")
            except Exception:
                logger.debug(f"[{idx}/{total}] ⏱️ TIMEOUT/ERR: {pname}")

            # 3. 若模型探测成功，进一步核验真实出网国家代码 (Ground-Truth Geo-IP)
            real_loc = None
            if is_clean:
                try:
                    async with httpx.AsyncClient(proxy=f"http://127.0.0.1:{PROBE_PORT}", timeout=3.0) as probe:
                        trace_res = await probe.get("http://cloudflare.com/cdn-cgi/trace")
                        if trace_res.status_code == 200:
                            real_loc = parse_cf_trace(trace_res.text)
                except Exception:
                    pass

                region = resolve_region(pname, real_loc)
                logger.info(f"[{idx}/{total}] ✅ CLEAN ({lat}ms) [{region} | loc={real_loc or 'regex'}]: {pname}")
                node_entry['state'] = 'active'
                node_entry['consecutive_fails'] = 0
                node_entry['last_healthy'] = now
                node_entry['latency'] = lat
                node_entry['region'] = region
            else:
                node_entry['consecutive_fails'] = node_entry.get('consecutive_fails', 0) + 1
                # 状态退化：原 active 变为 stale；超期变为 tombstone
                if node_entry.get('state') == 'active':
                    node_entry['state'] = 'stale'
                elif now - node_entry.get('last_healthy', 0) > STALE_EXPIRY_SECONDS:
                    node_entry['state'] = 'tombstone'

            lifecycle_db[fp] = node_entry

    return lifecycle_db


def allocate_region_ports(lifecycle_db: dict[str, dict]) -> dict[str, dict]:
    """为各大区 active 与 stale 节点装配稳定的百位端口段。"""
    # 统计各大区当前已占用的端口映射
    used_ports_by_region: dict[str, set[int]] = {r: set() for r in REGION_PORT_TIERS}

    for fp, node in lifecycle_db.items():
        if node.get('state') == 'tombstone':
            continue
        reg = node.get('region', 'OTHER')
        port = node.get('port')
        if reg in REGION_PORT_TIERS and port:
            tier_min, tier_max = REGION_PORT_TIERS[reg]
            if tier_min <= port <= tier_max:
                used_ports_by_region[reg].add(port)

    # 为尚未分配端口的节点分配端口
    for fp, node in lifecycle_db.items():
        if node.get('state') == 'tombstone':
            continue
        reg = node.get('region', 'OTHER')
        if reg not in REGION_PORT_TIERS:
            reg = 'OTHER'
            node['region'] = 'OTHER'

        tier_min, tier_max = REGION_PORT_TIERS[reg]
        port = node.get('port')
        if not port or not (tier_min <= port <= tier_max):
            # 找到首个可用端口
            allocated = None
            for p in range(tier_min, tier_max + 1):
                if p not in used_ports_by_region[reg]:
                    allocated = p
                    used_ports_by_region[reg].add(p)
                    break
            if allocated is not None:
                node['port'] = allocated
            else:
                logger.error(f"大区 [{reg}] 100 个端口段已耗尽，无法为节点分配端口: {node.get('name')}")

    return lifecycle_db


async def apply_assembly_and_reload(lifecycle_db: dict[str, dict]) -> None:
    """生成百位段生产配置，解耦发布活跃出口，并平滑热重载。"""
    # 1. 过滤 Mihomo 装配集（active + stale）与 网关路由集（仅 active）
    mihomo_nodes = []
    active_by_region: dict[str, list[str]] = {r: [] for r in REGION_PORT_TIERS}
    all_active_ports: list[str] = []

    for fp, node in lifecycle_db.items():
        state = node.get('state')
        port = node.get('port')
        reg = node.get('region', 'OTHER')
        proxy = node.get('proxy')
        if not port or not proxy or state == 'tombstone':
            continue

        # Mihomo 监听包含 active 与 stale（槽位占位，去断崖）
        mihomo_nodes.append((port, proxy))

        # 网关路由仅包含 active（绝对零黑洞）
        if state == 'active':
            port_url = f"http://127.0.0.1:{port}"
            active_by_region.setdefault(reg, []).append(port_url)
            all_active_ports.append(port_url)

    logger.info(f"Mihomo 物理装配节点数: {len(mihomo_nodes)} (active + stale)")
    logger.info(f"网关动态路由出口统计: { {r: len(ports) for r, ports in active_by_region.items() if ports} }")

    # 2. 生成 Mihomo /etc/mihomo/config.yaml
    listeners = []
    proxies_list = []
    seen_names = set()

    for port, p in mihomo_nodes:
        pname = p['name']
        # 防止重名冲突
        if pname in seen_names:
            pname = f"{pname}-{port}"
            p = dict(p)
            p['name'] = pname
        seen_names.add(pname)
        proxies_list.append(p)

        listeners.append({
            'name': f'mixed-{port}',
            'type': 'mixed',
            'port': port,
            'listen': '127.0.0.1',
            'proxy': pname,
        })

    prod_cfg = {
        'mode': 'rule',
        'log-level': 'warning',
        'ipv6': False,
        'allow-lan': False,
        'bind-address': '127.0.0.1',
        'external-controller': '127.0.0.1:9090',
        'secret': '',
        'listeners': listeners,
        'proxies': proxies_list,
        'rules': ['MATCH,DIRECT'],
    }

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.safe_dump(prod_cfg, f, allow_unicode=True)

    # 3. 写入网关路由动态注册表 data/egress_registry.json
    registry_data = {
        'updated_at': time.time(),
        'regions': active_by_region,
    }
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, 'w', encoding='utf-8') as f:
        json.dump(registry_data, f, ensure_ascii=False, indent=2)

    # 4. 同步更新 .env 中的 ZCODE_PROXIES
    if ENV_PATH.exists() and all_active_ports:
        proxy_env_val = ",".join(all_active_ports)
        os.system(f"sed -i '/^ZCODE_PROXIES=/d' {ENV_PATH}")
        with open(ENV_PATH, 'a', encoding='utf-8') as f:
            f.write(f"\nZCODE_PROXIES={proxy_env_val}\n")

    # 5. Mihomo Controller API 平滑热重载（零断流）
    reloaded = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.put(
                f"{CONTROLLER_URL}/configs?force=true",
                json={"path": str(CONFIG_PATH)},
            )
            if resp.status_code in (200, 204):
                logger.info("[✓] Mihomo Controller API 热重载成功 (SSE 零断流)")
                reloaded = True
    except Exception as e:
        logger.warning(f"Controller API 热重载异常: {e}")

    if not reloaded:
        logger.warning("Controller API 无法响应，回退 systemctl restart mihomo")
        os.system('systemctl restart mihomo')

    # 6. 保存扫描记录
    record = {
        'updated_at': time.time(),
        'total_active': len(all_active_ports),
        'regions': {r: len(ports) for r, ports in active_by_region.items()},
        'ports': all_active_ports,
    }
    with open(RESULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    logger.info(f"[✓] 生产环境已生效大区百位段干净代理出口，共计 {len(all_active_ports)} 个在线活跃端口")


async def main():
    logger.info("=" * 60)
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 启动多机场全量探活与百位大区段装配...")
    logger.info("=" * 60)

    all_proxies = []
    seen_fps = set()
    supported = {'ss', 'ssr', 'vmess', 'vless', 'trojan', 'hysteria', 'hysteria2', 'wireguard', 'tuic', 'snell', 'http', 'socks5'}
    dummy = ('剩余流量', '距离下次', '套餐到期', '官网', '通知', '提示', '公告', 'Traffic', 'Expire', 'Reset')

    urls = get_sub_urls()
    for u in urls:
        sub_data, user_info = await fetch_subscription(u)
        await check_subscription_expiration(u.split('?')[0][:30], user_info)
        if sub_data and 'proxies' in sub_data:
            added = 0
            for p in sub_data['proxies']:
                pt = str(p.get('type') or '').lower()
                pname = p.get('name', '')
                if pt in supported and not any(k in pname for k in dummy):
                    fp = compute_node_fingerprint(p)
                    if fp not in seen_fps:
                        seen_fps.add(fp)
                        all_proxies.append(p)
                        added += 1
            logger.info(f"[+] 订阅源解析出 {added} 个有效代理节点: {u.split('?')[0][:45]}...")

    logger.info(f"多机场聚合候选池总计: {len(all_proxies)} 个物理节点")
    if not all_proxies:
        logger.error("未获取到任何候选节点，退出。")
        return

    # 加载存量生命周期数据库
    lifecycle_db = load_lifecycle_db()

    # 1. 装配探活监听
    await setup_probe_listener(all_proxies)

    # 2. 全量真机探活
    lifecycle_db = await probe_all_candidates(all_proxies, lifecycle_db)

    # 3. 按大区百位端口段装配
    lifecycle_db = allocate_region_ports(lifecycle_db)

    # 4. 保存生命周期状态
    save_lifecycle_db(lifecycle_db)

    # 5. 应用配置并热重载
    await apply_assembly_and_reload(lifecycle_db)


if __name__ == '__main__':
    asyncio.run(main())
