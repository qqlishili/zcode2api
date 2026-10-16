"""Git发布后只读直连验收，不发送模型请求。"""

import datetime
import json
from pathlib import Path
import urllib.request

provider = json.loads((Path.home() / '.pi/agent/models.json').read_text(encoding='utf-8'))['providers']['zcode']
base = 'http://107.174.244.167:18088'
assert provider['baseUrl'].rstrip('/') == base
key = provider['apiKey']
assert isinstance(key, str) and key and not key.startswith(('!', '$'))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
rows = []
for path in ('/meta', '/v1/models'):
    request = urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + key})
    with opener.open(request, timeout=15) as response:
        payload = json.load(response)
        row = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'url': base + path, 'status': response.status}
        if path == '/meta':
            row['version'] = payload.get('version')
        else:
            row['models'] = [item['id'] for item in payload['data']]
            assert 'GLM-5.3-Flash' in row['models']
        assert row['status'] == 200
        rows.append(row)
result = {'source': 'Windows direct IP, no proxy', 'requests': rows}
(Path(__file__).parent / 'release-client-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
