"""
探测 SiliconFlow 免费模型在当前账号（赠送额度可能已耗尽）下是否仍可调用。
从 main.py 读取真实 API_BASE / API_KEY（避免明文 key 出现在命令行被脱敏）。
"""
import re, json, urllib.request, urllib.error

MAIN = r"C:/RAG/rag-vehicle-system/backend/main.py"
src = open(MAIN, encoding="utf-8").read()
api_base = re.search(r'API_BASE\s*=\s*os\.getenv\("API_BASE",\s*"([^"]+)"\)', src).group(1)
api_key = re.search(r'API_KEY\s*=\s*os\.getenv\("API_KEY",\s*"([^"]+)"\)', src).group(1)
print("API_BASE:", api_base)
print("API_KEY prefix:", api_key[:7], "len:", len(api_key))

# 候选：9B 以下免费模型（不同命名前缀都试一下）
candidates = [
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen2.5-7B-Instruct",
    "THUDM/glm-4-9b-chat",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
    "meta-llama/Llama-3.1-8B-Instruct",
]


def call(model, msg, maxtok=60):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": msg}],
        "max_tokens": maxtok,
        "temperature": 0.05,
    }).encode("utf-8")
    req = urllib.request.Request(api_base + "/chat/completions", data=body, headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            code = resp.getcode()
            data = json.loads(resp.read().decode("utf-8"))
            return code, data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:400]
    except Exception as e:
        return -1, str(e)[:400]


print("\n===== 连通性探测（简单消息）=====")
for m in candidates:
    code, out = call(m, "你好，只回一个字")
    print(f"\n[{m}] HTTP {code}")
    print(out[:200])
