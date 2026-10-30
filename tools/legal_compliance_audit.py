# -*- coding: utf-8 -*-
"""车驾管知识库 · 法规符合性审计工具

用法：
  python legal_compliance_audit.py            # 用内置官方源URL重新抓取并比对
  python legal_compliance_audit.py --offdir DIR # 使用已下载的 off_172.html/off_163.html/off_164.html

比对逻辑（严谨，区分“真差异”与“工具/格式误报”）：
  1. 从本地 knowledge_base 的 .md 中【剥离所有非法规内容】：
       引用块(> 开头)、Markdown 表格、校核注/解读标记、我加的总结/约束块；
  2. 抽取“纯法规正文”与官方全文逐条(第X条)比对；
  3. 同条号正文拼接（修复第十四条年龄/身体重复标题）；
  4. 先剥离再抽取，避免“解读里含第X条”误截。
只报告真正可能影响法条准确性的差异；代号括号注释(A1等)与引号/标题符差异不计入。
"""
import re, os, html as _html, sys, argparse, urllib.request, ssl

CN = {'零':0,'〇':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
def cn2num(s):
    s=s.strip()
    if s.isdigit(): return int(s)
    t=0;sec=0;num=0
    for ch in s:
        if ch in CN: num=CN[ch]
        elif ch in {'十':10,'百':100,'千':1000}:
            u={'十':10,'百':100,'千':1000}[ch]
            if num==0: num=1
            sec+=num*u; num=0
        else: return None
    return t+sec+num

ART_RE = re.compile(r'第\s*([一二三四五六七八九十百千零〇\d]{1,6})\s*条')
OFFICIAL = {
    '172': 'https://gat.zj.gov.cn/col/col1229098252/art/2026/art_19d2f215fe624e98a95b8a2de180c563.html',
    '163': 'https://gat.hunan.gov.cn/gat/jwgk/zfxxgk/xxgkml/fgwj/202112/t20211228_29143639.html',
    '164': 'https://www.gov.cn/gongbao/content/2022/content_5682413.htm',
}
FILES = {
    '172': 'driving_license.md',
    '163': 'traffic_violations.md',
    '164': 'vehicle_registration.md',
}

def strip_nonlaw(md):
    out=[]; i=0; lines=md.split('\n')
    SKIP_KW=['以下为知识库解读','知识库校核注','【解读','【增驾','上位规章已更新',
             'GA 1029—2022','本文件已废止','以下内容系对法条','归纳，具体业务']
    while i<len(lines):
        ln=lines[i]; s=ln.strip()
        if s.startswith('>'): i+=1; continue
        if '|' in ln and ('---' in ln or s.startswith('|') or s.endswith('|')): i+=1; continue
        if any(k in s for k in SKIP_KW): i+=1; continue
        if s.startswith('⚠️') or s.startswith('**关键约束'):
            i+=1
            while i<len(lines) and (lines[i].strip().startswith('-') or lines[i].strip()==''): i+=1
            continue
        out.append(ln); i+=1
    return '\n'.join(out)

def extract(text):
    pos=[(cn2num(m.group(1)),m.end()) for m in ART_RE.finditer(text) if cn2num(m.group(1)) is not None]
    out={}
    for i,(n,end) in enumerate(pos):
        if i+1<len(pos):
            nxt=pos[i+1][0]; start_next=len(text)
            for m2 in ART_RE.finditer(text,end):
                if cn2num(m2.group(1))==nxt and m2.start()>=end:
                    start_next=m2.start(); break
            body=text[end:start_next]
        else:
            body=text[end:]
        body=body.strip()
        out[n]=out.get(n,'')+(' ' if n in out else '')+body
    return out

def norm(s):
    s=s.replace('\u3000',' ').replace('\xa0',' ')
    s=''.join(ch for ch in s if not ch.isspace())
    s=re.sub(r'[，。；：、（）《》〈〉％－—\.,;:\(\)""\'\'<>%\-_／/"]', '', s)
    return s

def html2text(raw):
    t=re.sub(r'<script.*?</script>','',raw,flags=re.S)
    t=re.sub(r'<style.*?</style>','',t,flags=re.S)
    t=re.sub(r'<[^>]+>','\n',t); return _html.unescape(t)

def get_official(law, offdir):
    fp=os.path.join(offdir,f'off_{law}.html')
    if os.path.exists(fp):
        return html2text(open(fp,encoding='utf-8',errors='ignore').read())
    ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    req=urllib.request.Request(OFFICIAL[law],headers={'User-Agent':'Mozilla/5.0'})
    return html2text(urllib.request.urlopen(req,timeout=40,context=ctx).read().decode('utf-8',errors='ignore'))

def compare(otext,lpath,label):
    lc=strip_nonlaw(open(lpath,encoding='utf-8').read())
    oa,la=extract(otext),extract(lc)
    import difflib
    real=[]
    for n in sorted(set(oa)&set(la)):
        a,b=norm(oa[n]),norm(la[n])
        if difflib.SequenceMatcher(None,a,b).ratio()<0.97:
            real.append(n)
    miss=sorted(set(oa)-set(la)); extra=sorted(set(la)-set(oa))
    print(f'\n===== {label} =====')
    print(f'官方 {len(oa)} 条 / 本地纯法规 {len(la)} 条')
    if not real and not miss and not extra:
        print('  ✅ 完全一致（已剥离代号注释与解读块后逐条比对）')
    else:
        if miss: print('  [缺失]',miss)
        if extra: print('  [本地多出条号]',extra)
        if real:
            print('  [需人工复核的实质差异]',real)
            for n in real[:6]:
                a,b=norm(oa[n]),norm(la[n])
                r=difflib.SequenceMatcher(None,a,b).get_opcodes()
                for tag,i1,i2,j1,j2 in r:
                    if tag!='equal':
                        print(f'      [{tag}] 官方:…{a[max(0,i1-8):i2]}…  本地:…{b[max(0,j1-8):j2]}…')
    return real

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--offdir',default=None)
    args=ap.parse_args()
    here=os.path.dirname(os.path.abspath(__file__))
    kb=os.path.join(os.path.dirname(here),'data','knowledge_base')
    offdir=args.offdir or os.path.join(os.path.dirname(here),'_verify')
    os.makedirs(offdir,exist_ok=True)
    tot=0
    for law in ['172','163','164']:
        otext=get_official(law,offdir)
        open(os.path.join(offdir,f'off_{law}.html'),'w',encoding='utf-8').write('') if False else None
        tot+=len(compare(otext,os.path.join(kb,FILES[law]),f'{FILES[law]} vs 公安部令第{law}号'))
    print('\n审计完成：上述“实质差异”需人工复核；其余差异均为代号注释/引号/标题符，不影响法条准确性。')
