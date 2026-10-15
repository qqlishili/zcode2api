"""账号凭证身份与去重：重新授权同一账号不应重复入池。

背景：JWT 每次签发都带新的 `iat`，全文比对无法识别「同一账号重新授权」。
实测 {{EMAIL_placeholder}} 在 10-14 两次授权产生两条记录（user_id 相同、JWT 不同），
各自占用大区与出口端口。去重改为按 JWT payload 的 user_id（sub 兜底）。
"""

import base64
import json

from app.models import Account


def _jwt(user_id: str, iat: int) -> str:
    """构造一个结构合法的最小 JWT（仅用于身份解析，不参与签名校验）。"""
    def seg(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg({'user_id': user_id, 'iat': iat})}.sig"


# ── Account.identity ────────────────────────────────────────────────────

def test_identity_stable_across_reauth():
    """同一 user_id 的两次签发必须得到相同身份（与 iat 无关）。"""
    first = Account.create("zai", "acct", _jwt("uid-1", 100))
    second = Account.create("zai", "acct", _jwt("uid-1", 200))
    assert first.secret != second.secret      # JWT 全文不同
    assert first.identity == second.identity  # 身份相同
    assert first.identity == "uid-1"


def test_identity_falls_back_to_sub():
    """payload 无 user_id 时使用 sub。"""
    token = f"{base64.urlsafe_b64encode(b'{}').decode().rstrip('=')}." \
            f"{base64.urlsafe_b64encode(json.dumps({'sub': 'uid-sub'}).encode()).decode().rstrip('=')}.sig"
    assert Account.create("zai", "acct", token).identity == "uid-sub"


def test_identity_falls_back_to_secret_for_opaque_jwt():
    """无法解码 payload 时回退 secret 全文，不返回 None。"""
    acc = Account.create("zai", "acct", "a.b.c")
    assert acc.identity == acc.secret


def test_identity_of_apikey_account_is_secret():
    """API Key 账号没有可解码身份，身份即 Key 全文。"""
    acc = Account.create("zai", "acct", "sk-not-a-jwt")
    assert acc.mode == "apiKey"
    assert acc.identity == "sk-not-a-jwt"


# ── Store 去重 ──────────────────────────────────────────────────────────

def test_readd_same_user_keeps_single_record(fresh_app):
    """同一账号重新授权只保留一条记录。"""
    first = fresh_app.add_account("zai", "oauth-login", _jwt("uid-1", 100))
    second = fresh_app.add_account("zai", "oauth-login", _jwt("uid-1", 200))
    assert second.id == first.id
    assert len(fresh_app.list_accounts("zai")) == 1


def test_different_users_both_persisted(fresh_app):
    """不同 user_id 必须各自入池，不得误合并。"""
    fresh_app.add_account("zai", "a", _jwt("uid-1", 100))
    fresh_app.add_account("zai", "b", _jwt("uid-2", 100))
    assert len(fresh_app.list_accounts("zai")) == 2


def test_duplicate_apikey_skipped(fresh_app):
    """同一 API Key 重复导入仍按 secret 去重。"""
    first = fresh_app.add_account("zai", "k1", "sk-same-key")
    second = fresh_app.add_account("zai", "k2", "sk-same-key")
    assert second.id == first.id
    assert len(fresh_app.list_accounts("zai")) == 1


def test_regional_binding_not_duplicated(fresh_app, monkeypatch):
    """重复授权不得重复占用大区配额。"""
    from app.client_pool import AccountClientPool

    pool = AccountClientPool()
    monkeypatch.setattr(pool, "load_registry",
                        lambda: {'HK': ['a'], 'TW': [], 'JP': [], 'SG': [], 'US': []})
    monkeypatch.setattr('app.client_pool.account_client_pool', pool)

    first = fresh_app.add_account("zai", "dup", _jwt("uid-1", 100))
    second = fresh_app.add_account("zai", "dup", _jwt("uid-1", 200))
    assert second.assigned_region == first.assigned_region
    assert len(fresh_app.list_accounts("zai")) == 1


# ── jwt_user_id 归一化 ──────────────────────────────────────────────────

def test_jwt_user_id_returns_none_for_opaque_token():
    """无法解析身份的凭证不得把全文当 user_id 上报。"""
    from app.claim import jwt_user_id

    acc = Account.create("zai", "acct", "a.b.c")
    assert jwt_user_id(acc) is None


def test_jwt_user_id_matches_identity():
    """jwt_user_id 与 Account.identity 口径一致。"""
    from app.claim import jwt_user_id

    acc = Account.create("zai", "acct", _jwt("uid-1", 100))
    assert jwt_user_id(acc) == "uid-1" == acc.identity
