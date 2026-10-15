"""探针节点健康判定与账号候选资格单元测试。

对应 openspec/changes/probe-health-classification
（spec: probe-node-health / REQ-PNH-01 ~ REQ-PNH-05）。
"""

import httpx
import pytest

from scripts.mihomo_auto_probe import (
    ProbeError,
    node_reachable,
    probe_account_candidate,
)


class _Account:
    """满足探针候选判据的最小账号替身。"""

    def __init__(self, secret: str = 'a.b.c', enabled: bool = True,
                 status: str = 'active', name: str | None = None) -> None:
        self._secret = secret
        self.enabled = enabled
        self.status = status
        self.name = name if name is not None else secret

    @property
    def secret(self):
        return self._secret


def _response(status_code: int, body: str, headers: dict | None = None) -> httpx.Response:
    """构造带请求上下文的上游响应（业务码判定需要 response 对象）。"""
    request = httpx.Request('POST', 'https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages')
    return httpx.Response(status_code, text=body, headers=headers or {}, request=request)


# ── REQ-PNH-01：业务响应复用既有归一化判定 ──────────────────────────────

@pytest.mark.parametrize('body,expected', [
    ('{"code":1005,"msg":"exceed quota limit"}', '1005'),
    ('{"code":3002,"msg":"rate limit reached"}', '3002'),
    ('{"code":3007,"msg":"captcha verify failed"}', '3007'),
])
def test_business_response_counts_as_reachable(body, expected):
    """额度耗尽、限流、验证码挑战等业务响应必须判为链路可达。"""
    reachable, outcome = node_reachable(_response(200, body), body)
    assert reachable is True
    assert outcome == expected


def test_quota_exhausted_normalized_through_gateway_classifier(monkeypatch):
    """1005 必须经网关归一化为 402，而非脚本自行解释状态码。"""
    from app.routes import gateway

    calls = []
    original = gateway._classify_business_error

    def _spy(status_code, resp, text):
        calls.append((status_code, text))
        return original(status_code, resp, text)

    monkeypatch.setattr(gateway, '_classify_business_error', _spy)
    body = '{"code":1005,"msg":"exceed quota limit"}'
    reachable, outcome = node_reachable(_response(200, body), body)
    assert calls and calls[0][0] == 200
    assert reachable is True and outcome == '1005'


def test_captcha_challenge_header_counts_as_reachable():
    """验证码挑战（响应头信号）同样证明出口链路打通。"""
    headers = {'X-Aliyun-Captcha-Verify-Param': 'challenge-token'}
    reachable, _ = node_reachable(_response(403, '{"code":3007}', headers), '{"code":3007}')
    assert reachable is True


def test_normal_message_payload_counts_as_reachable():
    """正常 Anthropic message 响应（非错误结构）必须判为可达。"""
    body = '{"type":"message","content":[]}'
    reachable, outcome = node_reachable(_response(200, body), body)
    assert reachable is True
    assert outcome == '200'


@pytest.mark.parametrize('status,body', [
    (500, ''),
    (500, '{}'),
    (502, '<html>bad gateway</html>'),
    (503, '{"code":500}'),
])
def test_upstream_5xx_is_not_reachable(status, body):
    """归一后仍为 5xx 说明上游故障，无法证明出口可用，不得判为可达。"""
    reachable, _ = node_reachable(_response(status, body), body)
    assert reachable is False


def test_business_4xx_still_counts_as_reachable():
    """4xx 业务拒绝证明链路已受理，必须判为可达（仅 5xx 排除）。"""
    body = '{"code":1001,"msg":"plan not found"}'
    reachable, outcome = node_reachable(_response(404, body), body)
    assert reachable is True
    assert outcome == '1001'


# ── REQ-PNH-03：账号候选资格放宽 ────────────────────────────────────────

def test_exhausted_account_is_valid_candidate():
    """额度耗尽账号凭证有效，必须可作为探针身份（不要求 active 状态）。"""
    accounts = [_Account('a.b.c', True)]
    assert probe_account_candidate(accounts) is accounts[0]


def test_active_account_is_preferred_over_exhausted():
    """有 active 账号时必须优先选它，不得因顺序先撞上被风控的 exhausted 账号。

    实测（2026-10-15）：list_accounts 把已被风控的「账号A」（exhausted）
    排在首位，旧实现直接选中它 → 任何出口都返回 3012 → 143 个节点全判不可用。
    """
    from app.models import Status

    accounts = [
        _Account('banned.one', True, status=Status.EXHAUSTED),   # 可能被风控
        _Account('healthy.one', True, status=Status.ACTIVE),
    ]
    chosen = probe_account_candidate(accounts)
    assert chosen is not None and chosen.name == 'healthy.one'


def test_disabled_account_is_not_a_candidate():
    """未启用的账号不得作为探针身份：其凭证可能已被吊销。"""
    from app.models import Status

    accounts = [_Account('disabled.one', False, status=Status.DISABLED)]
    assert probe_account_candidate(accounts) is None


def test_active_beats_disabled_even_when_both_enabled():
    """启用但 status=disabled 的账号不得压过 active 账号被优选。"""
    from app.models import Status

    accounts = [
        _Account('stale.one', True, status=Status.DISABLED),
        _Account('ok.one', True, status=Status.ACTIVE),
    ]
    chosen = probe_account_candidate(accounts)
    assert chosen is not None and chosen.name == 'ok.one'


def test_candidate_skips_disabled_or_credentialless_accounts():
    """未启用或无凭证的账号不得作为探针身份。"""
    accounts = [_Account('a.b.c', False), _Account('', True), _Account('   ', True)]
    assert probe_account_candidate(accounts) is None


def test_no_candidate_keeps_probe_account_missing_semantics():
    """池内无任何有效凭证时仍必须是整轮未提交前提。"""
    assert probe_account_candidate([]) is None
    with pytest.raises(ProbeError):
        raise ProbeError('probe_account_missing')
