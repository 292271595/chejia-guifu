"""
文档加载器 —— 把知识库 Markdown 切成语义完整、可溯源的知识块

分块策略（按文件形态自动选择）：

  1) 条文型文档（法规原文，含"第X条"）
     一条一块。块的标题就是**这一条自己的条号**，正文前缀补上
     "《法规名》第X条（第X章）"，保证块自带出处、脱离原文也能被正确引用。

     为什么必须这样：早期实现按 "##/### 小标题" 切分，而法规文件里的小标题
     是编者加的段落标签，会跨条。例如 driving_license.md 的
     "### 第十四条 身体条件" 之下一直写到第十五条，于是第十五条被贴上
     "第十四条"的标题；"### 增驾路径汇总表" 之下藏着第十八条。检索时标题与
     内容错位，用户问"致人死亡怎么处理"就永远召不回第十八条。

  2) 标题型文档（标准、规范、问答）
     按标题层级切分，标题取"法规名 + 二级标题 + 三级标题"完整路径，
     不再只取最近一级。

  3) 无标题文档（GB 行业标准等）
     按自然段聚合到目标长度。

任何情况下都不把一个"条"拦腰截断；超长条才按款拆，且每段都保留条号。

用法：
    from document_loader import DocumentLoader
    chunks = DocumentLoader("data/knowledge_base").load_all()
"""
import os
import re
from typing import Dict, List

# 单块目标长度与上限（字符）。法规单条多在 100~800 字，1200 足以覆盖绝大多数条文。
MAX_CHUNK = 1200
HARD_MAX = 1600

_CN = "零一二三四五六七八九十百"
_CN_VAL = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 条：行首"第X条"，后面可以是空格、行尾，或直接接正文（部分文件写作"第三条车辆管理所…"）
ARTICLE_RE = re.compile(rf"^第([{_CN}]+)条(之[{_CN}]+)?(?=[\s　]|$|[^\d])")
# 章 / 节：允许带 # 前缀，也允许"第一章　总　则"这种全角空格
CHAPTER_RE = re.compile(rf"^#*\s*第([{_CN}]+)([章节])[\s　]*(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
TOC_WORDS = {"目录", "目次"}

# 无标题文档用来猜法规名的关键词（只在找不到一级标题时兜底）
_DOC_NAME_HINTS = ("规定", "规范", "办法", "标准", "规则", "细则", "指南", "方法", "要求")


def cn2int(s: str) -> int:
    total = cur = 0
    for ch in s:
        if ch in _CN_VAL:
            cur = _CN_VAL[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


_TOC_TAIL_RE = re.compile(r"[\s\.…·:：]*(\d+|[IVXLCivxlc]+)\s*$")


def _is_toc_entry(line: str) -> bool:
    """判断一行是不是目录条目：章/节名，或以页码结尾的短行。"""
    s = line.strip()
    if not s:
        return False
    if CHAPTER_RE.match(s) and len(s) <= 40:
        return True
    return len(s) <= 60 and bool(_TOC_TAIL_RE.search(s))


def _squash(s: str) -> str:
    return re.sub(r"[\s\u3000]+", "", s)


class DocumentChunk:
    """文档分块数据结构"""

    def __init__(self, content: str, metadata: Dict[str, str]):
        self.content = content
        self.metadata = metadata

    def __repr__(self):
        return (f"DocumentChunk(source={self.metadata.get('source')}, "
                f"title={self.metadata.get('title')}, len={len(self.content)})")


class DocumentLoader:
    """加载 knowledge_base 目录下的 .md 文件并分块"""

    def __init__(self, knowledge_dir: str):
        self.knowledge_dir = knowledge_dir
        self._chunks: List[DocumentChunk] = []

    # ── 对外入口 ──────────────────────────────────────────────
    def load_all(self) -> List[DocumentChunk]:
        all_chunks = []
        for filename in sorted(os.listdir(self.knowledge_dir)):
            if not filename.endswith(".md"):
                continue
            path = os.path.join(self.knowledge_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            source = filename[:-3]
            self._chunks = []
            doc_title = self._doc_title(text, source)
            units = self._parse(text, doc_title)
            for u in units:
                self._emit(u, source, doc_title)
            chunks, seen, dropped = [], set(), 0
            for c in self._chunks:
                # 同一文件内完全重复的块（多为空白表格模板）只留一份；
                # 跨文件不去重——不同法规出现相同句子是正常的，各自都要能溯源。
                key = _squash(c.content)
                if key in seen:
                    dropped += 1
                    continue
                seen.add(key)
                c.metadata["chunk_index"] = str(len(chunks))
                chunks.append(c)
            n_art = sum(1 for c in chunks if c.metadata.get("article"))
            note = f"，去重 {dropped}" if dropped else ""
            print(f"[DocumentLoader] {filename}: {len(chunks)} 个分块"
                  f"（条文块 {n_art}）{note}")
            all_chunks.extend(chunks)
        print(f"[DocumentLoader] 总计加载 {len(all_chunks)} 个知识块")
        return all_chunks

    # ── 文档级信息 ────────────────────────────────────────────
    def _doc_title(self, text: str, source: str) -> str:
        """取法规名：优先一级标题；否则从开头找带"规定/规范/办法"等字样的短行。"""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for ln in lines[:5]:
            if ln.startswith("# "):
                return self._clean_doc_title(ln[2:].strip()) or source
        # 没有一级标题的旧文件（如工作规范），从开头若干行里找最像法规名的一行。
        # 扫描窗口要留够：这类文件开头往往是"**发布机关**"、文号、引用块，真正的
        # 名称要到第 20 行往后才出现。优先取"以名称后缀结尾"的那行，
        # 否则容易抓到"关于印发《X》"这种通知标题。
        best = None
        for ln in lines[:40]:
            s = ln.strip().strip("*").strip()
            if not s or s.startswith(("#", ">", "|")):
                continue
            wrapped = re.match(r"^关于(?:印发|发布|公布)《(.+)》", s)
            if wrapped:
                s = wrapped.group(1)
            if not (4 <= len(s) <= 40 and any(h in s for h in _DOC_NAME_HINTS)):
                continue
            if s.endswith(_DOC_NAME_HINTS):
                return self._clean_doc_title(s)
            if best is None:
                best = s
        return self._clean_doc_title(best) if best else source

    @staticmethod
    def _clean_doc_title(title: str) -> str:
        """去掉末尾"（公安部令第163号）"这类括注，溯源条更短更好读。"""
        t = re.sub(r"[（(][^（）()]*[）)]\s*$", "", title).strip()
        return t or title.strip()

    # ── 解析成单元 ────────────────────────────────────────────
    def _parse(self, text: str, doc_title: str) -> List[Dict]:
        """
        把文档解析成有序单元：
          {"kind": "article", "label": "第一百零一条", "text": "...", "context": "第七章 法律责任"}
          {"kind": "block",   "label": "增驾路径汇总表", "text": "...", "context": "..."}
        """
        lines = text.split("\n")

        # 跳过一级标题所在行
        start = 0
        for i, ln in enumerate(lines):
            if ln.startswith("# "):
                start = i + 1
                break

        units: List[Dict] = []
        buf: List[str] = []          # 标题块缓冲
        art_lines: List[str] = []    # 当前条文缓冲
        art_label = ""               # 当前条号，如 "第一百零一条"
        art_num = -1                 # 当前条号数值，用于识别引用
        art_ctx = ""                 # 条文所属章节
        chapter = section = ""
        label = ""                   # 最近一个非章/节标题
        in_toc = False
        toc_seen = set()
        toc_lines = 0

        def flush_art():
            nonlocal art_lines, art_label
            if art_label and art_lines:
                units.append({
                    "kind": "article",
                    "label": art_label,
                    "text": "\n".join(art_lines).strip(),
                    "context": art_ctx,
                })
            art_lines, art_label = [], ""

        def flush_buf():
            nonlocal buf
            body = "\n".join(buf).strip()
            buf = []
            if not body:
                return
            # 纯引用块（以 > 开头）是文档元信息，不进知识库
            if all((not l.strip()) or l.strip().startswith(">") for l in body.split("\n")):
                return
            units.append({
                "kind": "block",
                "label": label,
                "text": body,
                "context": section or chapter,
            })

        for raw in lines[start:]:
            s = raw.strip()

            if not s:
                if art_label:
                    art_lines.append("")
                elif buf:
                    buf.append("")
                continue

            # 1) Markdown 标题
            mh = HEADING_RE.match(s)
            if mh:
                title = mh.group(2).strip()
                flush_art()
                flush_buf()
                mc = CHAPTER_RE.match(title)
                if mc:
                    if mc.group(2) == "章":
                        chapter, section = self._chapter_text(mc, title), ""
                    else:
                        section = self._chapter_text(mc, title)
                    label = ""
                    in_toc = False
                elif _squash(title) in TOC_WORDS:
                    in_toc = True
                    label = ""
                else:
                    in_toc = False
                    label = title
                continue

            # 2) 目录区标记（有的文件写成"目  录"/"目　次"，且不带 #）
            if _squash(s) in TOC_WORDS:
                flush_art()
                flush_buf()
                in_toc = True
                continue

            # 3) 无 # 的章 / 节行（部分文件正文就长这样）
            mc = CHAPTER_RE.match(s)
            if mc and len(s) <= 30 and not re.search(r"[。；，]", s):
                key = _squash(s)
                if in_toc and key in toc_seen:
                    # 同一个章名出现两次：目录结束，正文开始
                    in_toc = False
                if in_toc:
                    toc_seen.add(key)
                    continue
                flush_art()
                flush_buf()
                if mc.group(2) == "章":
                    chapter, section = self._chapter_text(mc, s), ""
                else:
                    section = self._chapter_text(mc, s)
                label = ""
                continue

            if in_toc:
                # 目录条目：章/节名，或以页码（阿拉伯数字 / 罗马数字）结尾的短行。
                # 一旦出现不像条目的行，说明正文已经开始，必须立刻退出目录区——
                # 早期版本用"行长 > 45"判断，遇到 GB 标准那种被硬换行切碎的正文
                # （每行都不长）就会一路吞掉整篇，实测丢了 36% 的内容。
                if _is_toc_entry(s):
                    toc_lines += 1
                    if toc_lines > 80:
                        in_toc = False
                    continue
                in_toc = False

            # 4) 条文
            ma = ARTICLE_RE.match(s)
            if ma:
                num = cn2int(ma.group(1))
                suffix = ma.group(2) or ""
                # 单调递增才算新条；否则是正文里对别的条的引用
                if art_label == "" or num > art_num or (num == art_num and suffix):
                    flush_art()
                    flush_buf()
                    art_num = num
                    art_label = f"第{ma.group(1)}条{suffix}"
                    art_ctx = section or chapter
                    art_lines = [s]
                    continue

            # 5) 普通正文
            if art_label:
                art_lines.append(s)
            else:
                buf.append(s)

        flush_art()
        flush_buf()
        return units

    @staticmethod
    def _chapter_text(mc, raw: str) -> str:
        num, kind, rest = mc.group(1), mc.group(2), mc.group(3)
        rest = _squash(rest)
        return f"第{num}{kind} {rest}".strip()

    # ── 单元 → 知识块 ─────────────────────────────────────────
    def _emit(self, unit: Dict, source: str, doc_title: str):
        text = unit["text"]
        if not text:
            return

        if unit["kind"] == "article":
            label = unit["label"]
            header = f"《{doc_title}》{label}"
            if unit["context"]:
                header += f"（{unit['context']}）"
            title = f"{doc_title} {label}"
            meta = {
                "source": source,
                "title": title,
                "article": label,
                "chapter": unit["context"],
            }
            self._append(header, text, meta, split_title=True)
        else:
            title = " ".join(x for x in (doc_title, unit["label"]) if x)
            meta = {
                "source": source,
                "title": title,
                "chapter": unit["context"],
            }
            self._append("", text, meta, split_title=False)

    def _append(self, header: str, body: str, meta: Dict, split_title: bool):
        """把一段正文（可带出处头）写入块列表；超长时按款拆分并保留出处头。"""
        full = f"{header}\n{body}" if header else body
        if len(full) <= MAX_CHUNK:
            self._push(full, meta)
            return

        parts = self._split_paragraphs(body)
        for i, part in enumerate(parts):
            if i == 0:
                content = f"{header}\n{part}" if header else part
                m = dict(meta)
            else:
                lead = f"{header}（接上）" if header else ""
                content = f"{lead}\n{part}" if lead else part
                m = dict(meta)
                if split_title:
                    m["title"] = meta["title"] + "（续）"
            m["part"] = str(i)
            self._push(content, m)

    @staticmethod
    def _split_paragraphs(body: str) -> List[str]:
        """按自然段聚合到 MAX_CHUNK，不切断段落；单段超长再按句、行、字符逐级降级。"""
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        if len(paras) == 1:
            paras = [p for p in body.split("\n") if p.strip()]

        chunks, cur = [], ""
        for p in paras:
            if len(p) > HARD_MAX:
                if cur:
                    chunks.append(cur)
                    cur = ""
                chunks.extend(DocumentLoader._split_oversize(p))
                continue
            if len(cur) + len(p) + 2 > MAX_CHUNK and cur:
                chunks.append(cur)
                cur = p
            else:
                cur = f"{cur}\n{p}" if cur else p
        if cur:
            chunks.append(cur)
        return chunks or [body]

    @staticmethod
    def _split_oversize(text: str) -> List[str]:
        """超长段落：先按句切，再按行切，最后按字符硬切。

        表格行没有任何句末标点，前两级都切不动——早期版本到这一步就原样返回，
        于是一个 7500 字的表格整块进了向量库，检索精度和上下文预算双输。
        """
        pieces = DocumentLoader._split_sentences(text)
        out = []
        for piece in pieces:
            if len(piece) <= HARD_MAX:
                out.append(piece)
                continue
            # 按行聚合成接近 MAX_CHUNK 的块，避免一行一块（表格会被切得极碎）
            cur = ""
            for ln in piece.split("\n"):
                ln = ln.strip()
                if not ln:
                    continue
                if len(ln) > HARD_MAX:
                    if cur:
                        out.append(cur)
                        cur = ""
                    out.extend(DocumentLoader._hard_split(ln))
                    continue
                if len(cur) + len(ln) + 1 > MAX_CHUNK and cur:
                    out.append(cur)
                    cur = ln
                else:
                    cur = f"{cur}\n{ln}" if cur else ln
            if cur:
                out.append(cur)
        return out or [text]

    @staticmethod
    def _hard_split(text: str, limit: int = MAX_CHUNK) -> List[str]:
        """按字符硬切，尽量落在标点上，保证不产生超长块。"""
        parts, i, n = [], 0, len(text)
        while n - i > limit:
            cut = i + limit
            window = text[i:cut]
            pos = max(window.rfind(p) for p in "。；，、！？,;")
            if pos > limit * 0.5:
                cut = i + pos + 1
            parts.append(text[i:cut])
            i = cut
        tail = text[i:].strip()
        if tail:
            parts.append(tail)
        return [p for p in parts if p.strip()]

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        out, cur = [], ""
        for sent in re.split(r"(?<=[。；！？])", text):
            if not sent:
                continue
            if len(cur) + len(sent) > MAX_CHUNK and cur:
                out.append(cur)
                cur = sent
            else:
                cur += sent
        if cur:
            out.append(cur)
        return out or [text]

    def _push(self, content: str, meta: Dict):
        content = content.strip()
        if not content:
            return
        # 表格分隔符之类的纯符号块（如单个 "|"）没有任何检索价值，直接丢弃
        if len(content) < 15 and not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", content):
            return
        m = dict(meta)
        m["chunk_index"] = str(len(self._chunks))
        self._chunks.append(DocumentChunk(content=content, metadata=m))


def chunks_to_texts_metadatas(chunks: List[DocumentChunk]):
    texts = [c.content for c in chunks]
    metadatas = [c.metadata for c in chunks]
    ids = [f"{m.get('source','')}_{m.get('chunk_index','')}_{m.get('part','0')}"
           for m in metadatas]
    return texts, metadatas, ids
