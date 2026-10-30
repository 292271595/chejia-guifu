# -*- coding: utf-8 -*-
"""严格归一化后核对 driving_license.md 与公安部令第172号的一致性"""
import sys, re, html, urllib.request, ssl
sys.path.insert(0, r'C:\RAG\rag-vehicle-system\tools')
from legal_diff_check import extract

ssl._create_default_https_context = ssl._create_unverified_context
URL = 'https://gat.zj.gov.cn/col/col1229098252/art/2026/art_19d2f215fe624e98a95b8a2de180c563.html'

req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
raw = urllib.request.urlopen(req, timeout=40).read().decode('utf-8', errors='ignore')

def strip_tags(s):
    s = re.sub(r'<script.*?</script>', '', s, flags=re.S)
    s = re.sub(r'<style.*?</style>', '', s, flags=re.S)
    s = re.sub(r'<[^>]+>', '\n', s)
    return html.unescape(s)

official = strip_tags(raw)
local = open(r'C:\RAG\rag-vehicle-system\data\knowledge_base\driving_license.md', encoding='utf-8').read()

CN_MAP = [('一', '1'), ('二', '2'), ('三', '3'), ('四', '4'), ('五', '5'),
          ('六', '6'), ('七', '7'), ('八', '8'), ('九', '9')]

def clean(s):
    # 去除准驾车型代号括号，如 (A1) (C1) （B2）
    s = re.sub(r'[（(]\s*[A-Z]{1,2}\d?\s*[）)]', '', s)
    # 去除 Markdown 标题符号与空白
    s = re.sub(r'[\s\u3000#\*>]', '', s)
    # 去除常见中英标点
    s = re.sub(r'[，。、；：,.;:"“”\'（）()]', '', s)
    for cn, ar in CN_MAP:
        s = s.replace(cn, ar)
    s = s.replace('十', '')
    return s

oa, la = extract(official), extract(local)
print('官方172号条款数:', len(oa), '| 本地条款数:', len(la))
print('缺失:', sorted(set(oa) - set(la)) or '无')
print('多出:', sorted(set(la) - set(oa)) or '无')

diff = [n for n in sorted(set(oa) & set(la)) if clean(oa[n]) != clean(la[n])]
print('严格归一化后仍有差异的条款数:', len(diff))
print('差异清单:', diff)
for n in diff[:6]:
    a, b = clean(oa[n]), clean(la[n])
    ratio = min(len(a), len(b)) / max(len(a), len(b))
    print(f'--- 第{n}条 长度比 {ratio:.2f} ---')
    print('  官方:', a[:110])
    print('  本地:', b[:110])
