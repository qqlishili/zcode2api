"""发布门槛：只核已授权文件、Git blob和原有工作区保护基线。"""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
accepted = json.loads(Path(r"D:\Temp\herdr-project\docs\evidence\zcode2api-error-contract-acceptance-20261009.json").read_text(encoding="utf-8-sig"))
mode = sys.argv[1]
assert mode in ("index", "HEAD")
def git(*args):
    return subprocess.check_output(["git", "-C", str(root), *args])

expected = {f["path"]: f["sha256"].lower() for f in accepted["finalFingerprints"]}
assert len(expected) == 11
assert git("branch", "--show-current").decode().strip() == "main"
origin = git("remote", "get-url", "origin").decode().strip()
assert origin == "git@github.com:qqlishili/zcode2api.git"
paths = git("diff", "--cached", "--name-only") if mode == "index" else git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
assert set(paths.decode().splitlines()) == set(expected), "commit scope differs"
rows = []
for path, digest in expected.items():
    blob = git("show", (":" if mode == "index" else "HEAD:") + path)
    actual = hashlib.sha256(blob).hexdigest()
    assert actual == digest, "Git blob drift: " + path
    assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest, "working file drift: " + path
    rows.append({"path": path, "sha256": actual, "matches_tested": True})
for row in accepted["protectedBaseline"]:
    path = root / row["path"]
    assert path.exists() == row["exists"], "protected existence drift"
    if path.exists():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"].lower(), "protected file drift"
record = {"mode": mode, "branch": "main", "head": git("rev-parse", "HEAD").decode().strip(),
          "files": rows, "protected_baseline": "6 matched"}
(Path(__file__).parent / ("release-" + mode.lower() + "-verification.json")).write_text(
    json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(record, ensure_ascii=False))
