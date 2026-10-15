"""available_models 单测：并集语义、-Trial 剥离、官方名归一、缺失回退。"""

from __future__ import annotations

import pytest

from app.usage_shadow import UsageShadow, available_models


def _patch_snapshot(monkeypatch: pytest.MonkeyPatch, accounts: dict) -> None:
    monkeypatch.setattr(UsageShadow, "snapshot",
                        staticmethod(lambda: {"at": 1.0, "accounts": accounts}))


def test_union_across_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """取并集而不是取某一个账号：实测同 plan 下不同账号的桶不同。"""
    _patch_snapshot(monkeypatch, {
        "甲": {"plan": "P", "error": "",
               "windows": [{"name": "GLM-5.3-Flash", "models": ["glm-5.3-flash"]}]},
        "乙": {"plan": "P", "error": "",
               "windows": [{"name": "GLM-5.3", "models": ["glm-5.3"]}]},
    })
    assert sorted(available_models()) == ["GLM-5.3", "GLM-5.3-Flash"]


def test_trial_alias_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """-Trial 是同桶的另一个名字，不是独立模型。"""
    _patch_snapshot(monkeypatch, {
        "甲": {"plan": "P", "error": "",
               "windows": [{"name": "GLM-5.3-Flash",
                            "models": ["glm-5.3-flash", "GLM-5.3-Flash-Trial"]}]},
    })
    got = available_models()
    assert got == ["GLM-5.3-Flash"]
    assert not any(m.endswith("-Trial") for m in got)


def test_names_normalized_to_official_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """usage 报小写，客户端认官方名（evidence 里 108 次都用 GLM-5.3-Flash）。"""
    _patch_snapshot(monkeypatch, {
        "甲": {"plan": "P", "error": "",
               "windows": [{"name": "w", "models": ["glm-5.3-flash"]}]},
    })
    assert available_models() == ["GLM-5.3-Flash"]


def test_unknown_model_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_NAME_MAP 未收录的名字原样保留（不丢信息）。"""
    _patch_snapshot(monkeypatch, {
        "甲": {"plan": "P", "error": "",
               "windows": [{"name": "w", "models": ["glm-5.9-hypothetical"]}]},
    })
    assert available_models() == ["glm-5.9-hypothetical"]


def test_dedupes_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    """glm-5.3-flash 与 GLM-5.3-Flash 归一后是同一个，不能重复列出。"""
    _patch_snapshot(monkeypatch, {
        "甲": {"plan": "P", "error": "",
               "windows": [{"name": "w", "models": ["glm-5.3-flash", "GLM-5.3-Flash"]}]},
    })
    assert available_models() == ["GLM-5.3-Flash"]


def test_failed_account_contributes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """读取失败的账号不贡献模型（它的 windows 本来就不可信）。"""
    _patch_snapshot(monkeypatch, {
        "坏": {"error": "this account has no GLM Coding Plan"},
        "好": {"plan": "P", "error": "",
               "windows": [{"name": "w", "models": ["glm-5.3"]}]},
    })
    assert available_models() == ["GLM-5.3"]


def test_falls_back_when_no_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """/v1/models 是握手接口，没读数时回退硬编码而不是返回空列表。"""
    from app import constants

    _patch_snapshot(monkeypatch, {})
    assert available_models() == list(constants.AVAILABLE_MODELS)


def test_empty_windows_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import constants

    _patch_snapshot(monkeypatch, {"甲": {"plan": "P", "error": "", "windows": []}})
    assert available_models() == list(constants.AVAILABLE_MODELS)


def test_tolerates_malformed_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """结构异常不得导致 500 —— 旁路数据来自外部，做防御性跳过。"""
    _patch_snapshot(monkeypatch, {
        "甲": "不是 dict",
        "乙": {"plan": "P", "error": "", "windows": [
            "不是 dict",
            {"models": "不是 list"},
            {"models": [None, "", "  ", "glm-5.3"]},
        ]},
    })
    assert available_models() == ["GLM-5.3"]
