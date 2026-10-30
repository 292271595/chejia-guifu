"""
文档加载器 —— 加载 Markdown 知识库文档，按语义分块
"""
import os
import re
from typing import List, Dict


# 条款边界：第X条
_CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百零〇]+条")


def _clause_of(text: str, window: int = 240) -> str:
    """取文本开头的第一个"第X条"作为标题候选；找不到返回空串。"""
    m = _CLAUSE_RE.search(text[:window])
    return m.group(0) if m else ""


class DocumentChunk:
    """文档分块数据结构"""
    def __init__(self, content: str, metadata: Dict[str, str]):
        self.content = content
        self.metadata = metadata

    def __repr__(self):
        src = self.metadata.get("source", "unknown")
        title = self.metadata.get("title", "")
        return f"DocumentChunk(source={src}, title={title}, len={len(self.content)})"


class DocumentLoader:
    """
    加载 knowledge_base 目录下的 .md 文件，按 ## 标题分块
    每个 ## 二级标题作为一个独立的知识块，语义完整
    """

    def __init__(self, knowledge_dir: str):
        self.knowledge_dir = knowledge_dir

    def _split_by_heading(self, text: str, source: str) -> List[DocumentChunk]:
        """
        按 ## 二级标题和 ### 三级标题切分文档。
        ### 子标题独立成块（更细粒度），保证条款级语义完整；
        超长块再按条款边界（第X条 / 【】）切分。
        """
        chunks = []
        lines = text.split('\n')
        current_section_lines = []
        current_title = ""

        # 文档级标题：第一行 '# xxx'；取不到就用文件名。
        # 没有它，像 traffic_violations.md 这种**全文无 ## 标题**的法规原文，
        # 所有分块的 title 都会退化成同一个占位符，前端溯源等于没有信息。
        doc_title = source
        for ln in lines:
            if ln.startswith('# '):
                doc_title = ln.lstrip('#').strip()
                # 去掉"（公安部令第163号）"这类括注，标题只留法规简称，避免溯源条过长
                doc_title = re.sub(r"[（(][^（）()]*[）)]\s*$", "", doc_title).strip() or doc_title
                break

        def make_title(clause_text: str) -> str:
            if current_title:
                return current_title
            clause = _clause_of(clause_text)
            return " ".join(x for x in (doc_title, clause) if x)

        def flush():
            if not current_section_lines:
                return
            content = '\n'.join(current_section_lines).strip()
            if not content:
                return
            if len(content) <= 2000:
                chunks.append(DocumentChunk(
                    content=content,
                    metadata={
                        "source": source,
                        "title": make_title(content),
                        "chunk_index": str(len(chunks))
                    }
                ))
            else:
                sub_parts = self._split_long_chunk(content)
                for i, sub in enumerate(sub_parts):
                    chunks.append(DocumentChunk(
                        content=sub,
                        metadata={
                            "source": source,
                            "title": make_title(sub),
                            "chunk_index": str(len(chunks)),
                            "sub_index": str(i)
                        }
                    ))

        for line in lines:
            if line.startswith('### ') or line.startswith('## '):
                flush()
                current_title = line.lstrip('#').strip()
                current_section_lines = []
            else:
                current_section_lines.append(line)

        flush()
        return chunks

    def _split_long_chunk(self, content: str, max_len: int = 2000) -> List[str]:
        """把过长的 chunk 切分为多个子块。
        优先按法规条款边界（第X条 / 【）切分，保证条款语义完整；
        再按空行切分；仍有超大块（如 markdown 表格）则按表格行拆分。"""
        # 第一步：按条款/要点边界切分
        boundary_parts = re.split(r'\n(?=(?:第[一二三四五六七八九十百]+条|【))', content)
        segments = [bp.strip() for bp in boundary_parts if bp.strip()]
        if len(segments) <= 1:
            segments = [content]

        # 第二步：对每个 segment 按空行二次切分
        result = []
        for seg in segments:
            if len(seg) <= max_len:
                result.append(seg)
            else:
                paragraphs = seg.split('\n\n')
                current = ""
                for p in paragraphs:
                    if len(current) + len(p) > max_len and current:
                        result.append(current.strip())
                        current = p
                    else:
                        current += ('\n\n' + p) if current else p
                if current:
                    result.append(current.strip())

        if not result:
            result = [content]

        # 二次拆分：如果仍有超过 max_len 的块，按表格行（\n|）拆分
        final_result = []
        for chunk_text in result:
            if len(chunk_text) <= max_len:
                final_result.append(chunk_text)
            else:
                # 按 markdown 表格行边界拆分
                # 先按 ### 子标题拆分
                sub_parts = re.split(r'\n(?=### )', chunk_text)
                for sub in sub_parts:
                    if len(sub) <= max_len:
                        final_result.append(sub.strip())
                    else:
                        # 按表格行拆分
                        table_rows = sub.split('\n|')
                        table_current = ""
                        for row in table_rows:
                            if len(table_current) + len(row) + 1 > max_len and table_current:
                                final_result.append(table_current.strip())
                                table_current = '|' + row
                            else:
                                table_current += ('\n|' + row) if table_current else ('|' + row)
                        if table_current:
                            final_result.append(table_current.strip())

        return final_result if final_result else [content]

    def load_all(self) -> List[DocumentChunk]:
        """加载 knowledge_dir 下所有 .md 文件并分块"""
        all_chunks = []
        for filename in sorted(os.listdir(self.knowledge_dir)):
            if filename.endswith('.md'):
                filepath = os.path.join(self.knowledge_dir, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    text = f.read()
                source_name = filename.replace('.md', '')
                chunks = self._split_by_heading(text, source_name)
                all_chunks.extend(chunks)
                print(f"[DocumentLoader] {filename}: {len(chunks)} 个分块")

        print(f"[DocumentLoader] 总计加载 {len(all_chunks)} 个知识块")
        return all_chunks


# 用于获取 chunks 列表的文本和元数据
def chunks_to_texts_metadatas(chunks: List[DocumentChunk]):
    texts = [c.content for c in chunks]
    metadatas = [c.metadata for c in chunks]
    ids = [f"{m.get('source','')}_{m.get('chunk_index','')}_{m.get('sub_index','0')}"
           for m in metadatas]
    return texts, metadatas, ids