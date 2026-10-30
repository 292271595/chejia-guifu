# -*- coding: utf-8 -*-
"""验证智谱 GLM-4-Flash 免费模型是否可用（Key 从项目根 .env 读取，避免明文）"""
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
    print("NO_KEY_FOUND_IN_ENV")
    sys.exit(1)

print("key_prefix=%s... len=%d" % (key[:8], len(key)))

MODELS = ["glm-4-flash", "glm-4.5-flash", "glm-4-flash-250414"]


def call(model, prompt, max_tokens=64):
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            return True, content, usage
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        return False, "HTTP %s: %s" % (e.code, detail), None
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e), None


for m in MODELS:
    ok, content, usage = call(m, "只回答两个字：你好")
    print("\n[%s] ok=%s" % (m, ok))
    print("  ->", content.replace("\n", " ")[:200])
    if usage:
        print("  usage:", usage)
