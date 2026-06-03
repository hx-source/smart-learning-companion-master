"""文本切分工具。

向量检索通常不直接把整篇文档做成一个向量，而是按页、段落、句子拆成
较小片段。这样召回结果更精确，也能减少传给大模型的上下文长度。
"""

import re


class TextSplitter:
    """将长文本切成适合向量化和检索的小块。"""

    def __init__(self, chunk_size=500, chunk_overlap=100):
        """初始化文本分块器

        Args:
            chunk_size: 每个分块的最大长度
            chunk_overlap: 分块之间的重叠长度
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text):
        """将文本分割成多个语义完整的分块

        Args:
            text: 待分割的文本

        Returns:
            分块后的文本列表
        """
        if not text:
            return []

        # 预处理文本，规范化换行符和空格，减少无意义空白对切块的影响。
        text = self._preprocess_text(text)

        # 按页面分割（基于 "=== 第 X 页 ===" 标记）。
        # FileProcessor 在 PDF 解析时会插入该标记，便于保留页级边界。
        pages = self._split_by_pages(text)
        chunks = []

        # 对每页文本进行分块
        for page_content in pages:
            page_chunks = self._split_page_content(page_content)
            chunks.extend(page_chunks)

        return chunks

    def _split_by_pages(self, text):
        """按页面标记分割文本

        Args:
            text: 完整文本

        Returns:
            按页面分割后的文本列表
        """
        # 查找页面标记
        page_pattern = r'=== 第 \d+ 页 ==='
        pages = re.split(page_pattern, text)
        
        # 过滤空页面
        return [page.strip() for page in pages if page.strip()]

    def _split_page_content(self, page_content):
        """分割单页内容

        Args:
            page_content: 单页文本内容

        Returns:
            分块后的文本列表
        """
        # 按段落分割
        paragraphs = page_content.split('\n')
        chunks = []
        current_chunk = []
        current_length = 0

        for paragraph in paragraphs:
            paragraph_length = len(paragraph)

            # 跳过空段落
            if not paragraph.strip():
                continue

            # 如果当前段落长度超过分块大小，单独按句子/固定窗口处理。
            if paragraph_length > self.chunk_size:
                # 处理超长段落
                sub_chunks = self._split_long_paragraph(paragraph)
                chunks.extend(sub_chunks)
                continue

            # 检查添加当前段落后是否超过分块大小
            if current_length + paragraph_length + len(current_chunk) <= self.chunk_size:
                current_chunk.append(paragraph)
                current_length += paragraph_length
            else:
                # 保存当前分块
                chunks.append('\n'.join(current_chunk))

                # 计算重叠部分。
                # 相邻块保留少量共同文本，能减少答案跨块时的信息断裂。
                overlap = []
                if current_chunk and self.chunk_overlap > 0:
                    overlap_length = 0
                    for i in range(len(current_chunk)-1, -1, -1):
                        overlap_length += len(current_chunk[i]) + 1  # +1 是换行符
                        if overlap_length <= self.chunk_overlap:
                            overlap.insert(0, current_chunk[i])
                        else:
                            break

                # 开始新分块
                current_chunk = overlap + [paragraph]
                current_length = sum(len(p) for p in current_chunk)

        # 添加最后一个分块
        if current_chunk:
            chunks.append('\n'.join(current_chunk))

        return chunks

    def _preprocess_text(self, text):
        """预处理文本，规范化换行符和空格

        Args:
            text: 原始文本

        Returns:
            预处理后的文本
        """
        # 替换连续的换行符为单个换行符。
        text = '\n'.join([line.strip() for line in text.split('\n') if line.strip()])
        # 替换连续的空格为单个空格。
        text = re.sub(r'\s+', ' ', text)
        return text

    def _split_long_paragraph(self, paragraph):
        """处理超长段落

        Args:
            paragraph: 超长段落

        Returns:
            分割后的子段落列表
        """
        chunks = []
        # 保留中英文句末标点，让子块尽量在自然句边界结束。
        sentences = re.split(r'([。！？.!?])', paragraph)

        current_chunk = ""
        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            if i + 1 < len(sentences):
                sentence += sentences[i + 1]  # 添加标点符号

            if len(current_chunk) + len(sentence) <= self.chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                # 如果单个句子超过分块大小，强制分割
                if len(sentence) > self.chunk_size:
                    for j in range(0, len(sentence), self.chunk_size - self.chunk_overlap):
                        chunk = sentence[j:j + self.chunk_size]
                        if j > 0:
                            # 添加重叠部分
                            start = max(0, j - self.chunk_overlap)
                            chunk = sentence[start:j + self.chunk_size]
                        chunks.append(chunk)
                    current_chunk = ""
                else:
                    current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks
