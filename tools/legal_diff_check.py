# -*- coding: utf-8 -*-
"""将本地知识库文档与官方法规全文做条款级比对"""
import re, os, sys

CN = {'零':0,'〇':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}

def cn2num(s):
    """中文数字串转阿拉伯数字，处理 一百一十一 / 二十一 等"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total = 0; section = 0; num = 0
    units = {'十':10, '百':100, '千':1000}
    for ch in s:
        if ch in CN:
            num = CN[ch]
        elif ch in units:
            u = units[ch]
            if num == 0:
                num = 1
            section += num * u
            num = 0
        else:
            return None
    return total + section + num

ART_RE = re.compile(r'第\s*([一二三四五六七八九十百千零〇\d]{1,6})\s*条')

def extract(text):
    """返回 {条号(int): 正文(str)}"""
    positions = []
    for m in ART_RE.finditer(text):
        n = cn2num(m.group(1))
        if n is None:
            continue
        positions.append((n, m.end()))
    out = {}
    for i, (n, end) in enumerate(positions):
        stop = positions[i+1][1] - len(repr(0)) if False else None
        # 下一条的起点需回溯“第X条”这几个字的长度
        if i + 1 < len(positions):
            nxt = positions[i+1][0]
            # 找到下一条 "第X条" 的起始位置
            start_next = len(text)
            for m2 in ART_RE.finditer(text, end):
                if cn2num(m2.group(1)) == nxt and m2.start() >= end:
                    start_next = m2.start()
                    break
            body = text[end:start_next]
        else:
            body = text[end:]
        body = body.strip()
        if n not in out or len(body) > len(out[n]):
            out[n] = body
    return out

def norm(s):
    """归一化：去空白/标点，全角转半角"""
    s = s.replace('\u3000', ' ').replace('\xa0', ' ')
    s = ''.join(ch for ch in s if not ch.isspace())
    # 全角标点转半角
    trans = str.maketrans('，。；：（）、""''《》〈〉%－—', ',.;:()\'""《》<>%-_')
    s = s.translate(trans)
    s = re.sub(r'[,.。、;:]', '', s)
    return s

def num_map(s):
    """把中文数字统一折算，便于比对：把阿拉伯数字也转出来做成元组比较成本高，
    这里退而求其次：把常见数量词统一"""
    for cn, ar in [('三年','3年'),('三倍','3倍'),('三日','3日'),('百分之二十','20%'),
                   ('百分之五十','50%'),('百分之三十','30%'),('百分之百','100%')]:
        s = s.replace(cn, ar)
    return s

def compare(official_path, local_path, label):
    o = open(official_path, encoding='utf-8').read()
    l = open(local_path, encoding='utf-8').read()
    oa, la = extract(o), extract(l)
    print(f'\n===== {label} =====')
    print(f'官方条款数: {len(oa)}  本地条款数: {len(la)}')
    missing = sorted(set(oa) - set(la))
    extra   = sorted(set(la) - set(oa))
    if missing:
        print(f'本地缺失条款: {missing[:40]}{"..." if len(missing)>40 else ""}')
    if extra:
        print(f'本地多出/异常条款: {extra[:40]}')
    diff = []
    for n in sorted(set(oa) & set(la)):
        a, b = norm(oa[n]), norm(la[n])
        a, b = num_map(a), num_map(b)
        if a != b:
            diff.append((n, len(a), len(b)))
    print(f'内容存在差异的条款数: {len(diff)}')
    if diff:
        print('差异清单(条号, 官方正文长度, 本地正文长度):')
        for n, x, y in diff:
            print(f'  第{n}条  official={x}  local={y}')
    return oa, la, diff

if __name__ == '__main__':
    base = r'C:\RAG\rag-vehicle-system\data\knowledge_base'
    compare(r'C:\RAG\_172_official.txt',
            os.path.join(base, 'driving_license.md'),
            'driving_license.md  vs  公安部令第172号原文')
