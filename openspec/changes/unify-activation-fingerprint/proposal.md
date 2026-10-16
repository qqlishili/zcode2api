# 统一激活上报与账号指纹的设备描述

## 现象（2026-10-15 实测）

**同一账号存在两套互相矛盾的设备描述。**

激活上报（`app/install.py`）用的是**全局硬编码常量**：

```python
# app/install.py:185
DeviceProfile(
    os_version=constants.IDENTITY_OS_VERSION,
    language=constants.IDENTITY_CLIENT_LANGUAGE,
    timezone=constants.IDENTITY_CLIENT_TIMEZONE,
    screen=constants.ACTIVATION_SCREEN_RESOLUTION,     # ← "2560x1440"（constants.py:61）
    device_mid=device_mid(),
)
```

而账号自身指纹（`app/fingerprint.py` 分配、随账号持久化的 `fingerprint`）是
**多样化的**，实测 16 个账号的分布：

```
'1920x1080': 6
'1728x1117': 4
'2560x1440': 3      ← 只有这 3 个与激活上报的常量一致
'1512x982':  2
'1366x768':  1
```

即：**一个指纹为 `1920x1080` 的账号，激活时却上报 `2560x1440`**。
`os_version` / `language` / `timezone` 同样可能不一致（需逐个核对）。

## 为什么值得单独调查

账号指纹的存在意义就是**让每个账号看起来像一台独立、稳定的真实设备**。
同账号内两处设备描述不一致，等于**自己声明了两台不同的机器**，这是风控
最容易利用的信号之一：

- 激活事件说的设备与后续对话请求说的设备不同
- 同一账号在不同时间点说出的设备不同（若指纹被重新分配）

**但必须先确认影响，再决定是否改**：
1. 激活上报里是否**也**带 `device_mid`（若带，且与指纹一致，则矛盾面收窄到
   屏幕等次要字段）
2. 上游是否**实际在校验**这些字段的一致性（可用受控实验判断，例如故意用
   矛盾的字段组合，观察是否被拒）
3. `IDENTITY_*` 系列常量与指纹的字段是否**已经**同源（部分可能是）

## 待回答的问题

1. 逐字段列出「激活上报的值」与「账号指纹的值」，标注哪些同源、哪些硬编码。
2. 该不一致是否已被上游用于判定（受控实验 / 观察日志）。
3. 若需修：让 `install.py` 改为**接收并使用 account 的指纹**（而不是全局常量），
   保持"同账号同设备"。

## 范围

**只做调查与结论**。若确认需要修，另开 change 实施 —— 因为改动会触及激活链路，
需要单独评估"历史已上报数据的兼容性"（已激活账号的字段已在服务端落库，
新行为只对新激活生效）。

## 关联

- 本 change 由 `dual-channel-plugin-upstream` 的 9.9 派生。
- 账号指纹的分配与回填见 `app/fingerprint.py` 与 `app/main.py::_backfill_fingerprints`。
