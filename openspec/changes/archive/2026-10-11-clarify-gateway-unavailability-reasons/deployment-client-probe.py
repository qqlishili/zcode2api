"""部署验收：复用已有客户端凭据，直连VPS，仅输出白名单响应。"""

import datetime
import json
from pathlib import Path
import urllib.error
import urllib.request

config = json.loads((Path.home() / ".pi/agent/models.json").read_text(encoding="utf-8"))
provider = config["providers"]["zcode"]
base = "http://107.174.244.167:18088"
assert provider["baseUrl"].rstrip("/") == base
key = provider["apiKey"]
assert isinstance(key, str) and key and not key.startswith(("!", "$"))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = []
for endpoint in ("messages", "chat/completions", "responses"):
    for stream in (True, False):
        body = {"model": "GLM-5.3-Flash", "stream": stream}
        if endpoint == "responses":
            body.update(input="回复OK", max_output_tokens=16, reasoning={"effort": "high"})
        else:
            body.update(messages=[{"role": "user", "content": "回复OK"}], max_tokens=16)
            body.update({"output_config": {"effort": "high"}} if endpoint == "messages" else {"reasoning_effort": "high"})
        request = urllib.request.Request(base + "/v1/" + endpoint, data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            payload = json.loads(response.read(8192))
            error = payload.get("error", {})
            fields = {k: error.get(k) for k in ("type", "code", "retryable", "retry_after", "request_id")}
            fields["causes"] = [{"code": c.get("code"), "origin": c.get("origin")} for c in error.get("causes", [])]
            row = {"at": started, "url": base + "/v1/" + endpoint, "stream": stream,
                   "status": response.status, "error": fields,
                   "headers": {k: response.headers.get(k) for k in ("Content-Type", "X-Request-Id", "Retry-After")}}
        results.append(row)
        if row["status"] in (401, 403):
            break
record = {"source": "Windows client, direct IP (no proxy)", "requests": results}
target = Path(__file__).parent / "deployment-client-results.json"
target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(record, ensure_ascii=False))
assert len(results) == 6
for row in results:
    error = row["error"]
    assert row["status"] == 503 and error["type"] == "no_available_account"
    assert error["code"] == "quota_exhausted"
    assert error["causes"] == [{"code": "quota_exhausted", "origin": "account_state"}]
    assert error["retryable"] is False and error["retry_after"] is None
    assert row["headers"]["Retry-After"] is None
    assert error["request_id"] and error["request_id"] == row["headers"]["X-Request-Id"]
