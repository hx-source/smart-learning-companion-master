"""LangChain 文本切分工具。

向量检索通常不直接把整篇文档做成一个向量，而是按页、段落、句子拆成
较小片段。这里使用 LangChain 的 RecursiveCharacterTextSplitter，
让切分逻辑更标准，也方便后续替换为 Markdown/HTML/代码等专用 splitter。
"""

import re

from langchain_text_splitters import RecursiveCharacterTextSplitter


class TextSplitter:
    """将长文本切成适合向量化和检索的小块。"""

    def __init__(self, chunk_size=500, chunk_overlap=100):
        """初始化文本分块器。

        Args:
            chunk_size: 每个分块的最大长度。
            chunk_overlap: 分块之间的重叠长度。
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=[
                "\n\n",
                "\n",
                "。",
                "！",
                "？",
                ". ",
                "! ",
                "? ",
                " ",
                "",
            ],
        )

    def split_text(self, text):
        """将文本分割成多个语义相对完整的分块。

        Args:
            text: 待分割的文本。

        Returns:
            list[str]: 分块后的文本列表。
        """
        if not text:
            return []

        # FileProcessor 会在 PDF 文本里插入页码标记；先按页切开，可以减少跨页拼接。
        pages = self._split_by_pages(self._preprocess_text(text))
        chunks = []
        for page_content in pages:
            chunks.extend(self.splitter.split_text(page_content))
        return [chunk.strip() for chunk in chunks if chunk.strip()]

    def _split_by_pages(self, text):
        """按页面标记分割文本。"""
        page_pattern = r"=== 第 \d+ 页(?: \(OCR识别\))? ==="
        pages = re.split(page_pattern, text)
        return [page.strip() for page in pages if page.strip()]

    def _preprocess_text(self, text):
        """预处理文本，规范化换行符和空格。"""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)
