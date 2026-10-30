# -*- coding: utf-8 -*-
"""验证智谱 embedding-3 可用性与维度"""
import io
import re
import sys
import json
import urllib.request
import urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ENV = r"C:\RAG\rag-vehicle-system\.env"
key = None
for line in open(ENV, "r", encoding="utf-8", errors="ignore"):
    line = line.strip()
    if line.startswith("API_KEY=") and not line.startswith("#"):
        key = line.split("=", 1)[1].strip().strip('"').strip("'")
        break
if not key:
    print("NO_KEY")
    sys.exit(1)

for dims in (1024, 2048):
    payload = {"model": "embedding-3", "input": ["大型客车A1准驾车型年龄要求"], "dimensions": dims}
    req = urllib.request.Request(
        "https://open.bigmodel.cn/api/paas/v4/embeddings",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode("utf-8"))
            emb = body["data"][0]["embedding"]
            print(f"dims={dims}: OK, actual_len={len(emb)}, head={[round(x,4) for x in emb[:3]]}, usage={body.get('usage')}")
    except urllib.error.HTTPError as e:
        print(f"dims={dims}: HTTP {e.code} {e.read().decode('utf-8', errors='replace')[:300]}")
