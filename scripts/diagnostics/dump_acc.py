"""从 accounts.db 导出 JWT/device 到 /tmp/acc.json，供诊断脚本使用。

用法（在项目根目录执行，路径随部署位置而定）：
    .venv/bin/python scripts/diagnostics/dump_acc.py [账号名子串]

默认取第一个 `mode=jwt` 且 name 含 "akbgpryk" 的账号；可传子串指定。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "accounts.db"
OUT = pathlib.Path("/tmp/acc.json")


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else "akbgpryk"
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute("SELECT name, data FROM accounts WHERE mode='jwt'").fetchall()

    picked = None
    for name, raw in rows:
        if needle.lower() in (name or "").lower():
            picked = (name, json.loads(raw) if raw else {})
            break
    if picked is None:
        print(f"未找到匹配 '{needle}' 的 jwt 账号；现有：")
        for name, _ in rows:
            print("  -", name)
        return 1

    name, data = picked
    payload = {
        "jwt": data.get("jwt_token") or "",
        "device_mid": (data.get("fingerprint") or {}).get("device_mid") or "",
        "install_id": data.get("install_id") or "",
    }
    OUT.write_text(json.dumps(payload), encoding="utf-8")
    OUT.chmod(0o600)
    print(f"已写入 {OUT}（账号 {name}）")
    print(f"  jwt 长度 = {len(payload['jwt'])}")
    print(f"  device_mid = {payload['device_mid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
