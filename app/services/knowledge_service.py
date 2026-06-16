"""LangChain 知识库问答服务。

这个模块把 RAG（检索增强生成）流程串起来：
上传文档 -> 提取文本 -> 切分片段 -> 生成嵌入 -> 写入 Faiss ->
提问时召回片段 -> 可选重排 -> 拼接上下文 -> 调用大模型生成答案。
"""

import os
from dotenv import load_dotenv
from app.utils.vector_service import EmbeddingModel, VectorStore
from app.services.reranker_service import RerankerService
from config import Config
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

load_dotenv()


class QAModule:
    """单个知识库的问答模块。

    每个知识库名称对应一个 QAModule 实例，避免不同知识库的向量文件、
    上传目录和会话状态互相混用。
    """
    _initialized = False
    _instances = {}  # 存储不同知识库的实例，实现“按知识库名称单例”。
    
    def __new__(cls, knowledge_base_name="default", model_type="ollama"):
        """创建或获取知识库实例
        
        Args:
            knowledge_base_name: 知识库名称
            model_type: 保留兼容参数，当前始终使用本地 Ollama
        """
        if knowledge_base_name not in cls._instances:
            cls._instances[knowledge_base_name] = super(QAModule, cls).__new__(cls)
        return cls._instances[knowledge_base_name]
    
    def __init__(self, knowledge_base_name="default", model_type="ollama"):
        """初始化问答模块
        
        Args:
            knowledge_base_name: 知识库名称
            model_type: 保留兼容参数，当前始终使用本地 Ollama
        """
        if not hasattr(self, 'initialized'):
            # 这些成员只在首次创建实例时初始化；后续从 _instances 取出时不重复加载。
            self.knowledge_base_name = knowledge_base_name
            self.model_type = model_type
            self.embedding_model = EmbeddingModel()
            self.vector_store = VectorStore(collection_name=f"knowledge_base_{knowledge_base_name}")
            self.llm_model = Config.OLLAMA_MODEL  # 使用配置中的生成模型
            self.prompt_template = self._create_prompt_template()
            self.chat_history = []
            self.ollama_url = Config.OLLAMA_BASE_URL
            # 初始化重排服务
            self.use_reranker = Config.USE_RERANKER
            if self.use_reranker:
                self.reranker = RerankerService(Config.OLLAMA_RERANKER_MODEL)
            else:
                self.reranker = None
            self.initialized = True
            QAModule._initialized = True
    
    @classmethod
    def get_all_instances(cls):
        """获取所有知识库实例"""
        return list(cls._instances.keys())

    def query(self, question, history=None):
        """处理用户查询

        Args:
            question: 用户问题
            history: 对话历史

        Returns:
            回答
        """
        if history:
            self.chat_history = history

        # LangChain FAISS 直接接收查询文本，并通过 EmbeddingModel 完成查询向量化。
        search_results = self.vector_store.search(question, n_results=5)

        # 将召回的文档片段拼接成上下文，交给生成模型参考。
        context = self._build_context(search_results)

        # 构建提示词
        prompt = self._build_prompt(question, context)

        # 调用LLM生成回答
        answer = self._call_llm(prompt)

        # 更新对话历史
        self.chat_history.append({"role": "user", "content": question})
        self.chat_history.append({"role": "assistant", "content": answer})

        return answer

    def query_with_knowledge(self, question, history=None, model_type=None, user=None):
        """处理用户查询，返回带知识库来源的回答

        Args:
            question: 用户问题
            history: 对话历史
            model_type: 保留兼容参数，当前始终使用本地 Ollama

        Returns:
            dict: 包含 answer 和 sources 的字典
        """
        context, sources = self.retrieve_context_and_sources(question)
        prompt = self._build_prompt(question, context)
        answer = self._call_llm(prompt, model_type, user=user)

        return {
            'answer': answer,
            'sources': sources,
            'context_used': bool(context.strip())
        }

    def retrieve_context_and_sources(self, question):
        """检索知识库上下文和来源信息。

        非流式和流式问答都会先调用这里，保证召回、重排、来源展示逻辑一致。
        """
        # 搜索相关文档（召回阶段）：先取更多候选，给重排阶段留下选择空间。
        search_results = self.vector_store.search(question, n_results=10)  # 先召回更多文档

        # 重排阶段（如果启用）：用生成/评分模型重新判断候选片段和问题的相关性。
        if self.use_reranker and self.reranker:
            search_results = self.reranker.rerank_with_metadata(
                question, 
                search_results, 
                top_k=5  # 重排后保留 top 5
            )

        # 将召回的文档片段拼接成上下文，交给生成模型参考。
        context = self._build_context(search_results)

        # 构建来源信息：前端可展示“答案参考了哪些文档片段”。
        sources = []
        if search_results and 'documents' in search_results and search_results['documents'][0]:
            # 检查是否有重排分数
            rerank_scores = search_results.get('rerank_scores', [None] * len(search_results['documents'][0]))
            
            for i, doc in enumerate(search_results['documents'][0]):
                if doc:
                    # 安全地获取元数据和距离，避免索引越界
                    metadatas_list = search_results.get('metadatas', [[]])[0]
                    distances_list = search_results.get('distances', [[]])[0]
                    
                    metadata = metadatas_list[i] if i < len(metadatas_list) else {}
                    distance = distances_list[i] if i < len(distances_list) else 0
                    rerank_score = rerank_scores[i] if i < len(rerank_scores) else None
                    
                    source_info = {
                        'content': doc[:200] + '...' if len(doc) > 200 else doc,
                        'metadata': metadata,
                        'relevance': 1 - distance  # 向量相似度
                    }
                    
                    # 如果有重排分数，添加到来源信息
                    if rerank_score is not None:
                        source_info['rerank_score'] = rerank_score
                    
                    sources.append(source_info)

        return context, sources

    def stream_with_knowledge(self, question, model_type=None, user=None):
        """流式生成知识库回答。

        Yields:
            str: 模型逐步生成的文本片段。
        """
        context, sources = self.retrieve_context_and_sources(question)
        prompt = self._build_prompt(question, context)
        return self._stream_llm(prompt, model_type, user=user), sources, bool(context.strip())

    def _build_context(self, search_results):
        """构建上下文

        Args:
            search_results: 搜索结果

        Returns:
            上下文文本
        """
        context = []
        if search_results and 'documents' in search_results:
            # search_results 的结构对齐 Chroma 风格：外层列表表示一次查询的结果集。
            for doc in search_results['documents'][0]:
                if doc:
                    context.append(doc)
        return '\n\n'.join(context)

    def _create_prompt_template(self):
        """创建 LangChain ChatPromptTemplate。"""
        return ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一个智能知识库助手。请优先根据提供的上下文回答用户问题，"
                "回答要准确、完整、有条理。如果上下文没有相关信息，请如实说明。",
            ),
            (
                "human",
                "上下文：\n{context}\n\n用户问题：\n{question}",
            ),
        ])

    def _build_prompt(self, question, context):
        """构建提示词文本。

        Args:
            question: 用户问题
            context: 上下文

        Returns:
            提示词
        """
        safe_context = context if context.strip() else "未检索到相关知识库上下文。"
        return self.prompt_template.format(context=safe_context, question=question)

    def _call_llm(self, prompt, model_type=None, user=None):
        """调用LLM

        Args:
            prompt: 提示词
            model_type: 模型类型 (api 或 ollama)

        Returns:
            回答
        """
        try:
            llm = ChatOllama(
                base_url=self.ollama_url,
                model=self.llm_model,
                temperature=0.7,
                num_predict=1000,
            )
            chain = llm | StrOutputParser()
            return chain.invoke(prompt)
            
        except Exception as e:
            return f"API 调用失败: {str(e)}"

    def _stream_llm(self, prompt, model_type=None, user=None):
        """流式调用 LLM。

        当前只使用 LangChain ChatOllama 逐 token/片段返回。
        """
        try:
            llm = ChatOllama(
                base_url=self.ollama_url,
                model=self.llm_model,
                temperature=0.7,
                num_predict=1000,
            )
            chain = llm | StrOutputParser()
            for chunk in chain.stream(prompt):
                if chunk:
                    yield chunk
        except Exception as e:
            yield f"API 调用失败: {str(e)}"

    def add_document(self, file_path, metadata=None):
        """添加文档到知识库

        Args:
            file_path: 文件路径
            metadata: 元数据

        Returns:
            成功状态
        """
        from app.utils.file_processor import FileProcessor
        from app.utils.text_splitter import TextSplitter

        try:
            self.last_error = None
            # 先删除该文件的旧向量（如果存在）。
            # 这样同名文件重复上传时不会留下旧片段，避免检索结果重复或过期。
            self.delete_by_source(file_path)
            
            # 读取文件
            processor = FileProcessor()
            text = processor.process_file(file_path)

            # 文本分块
            splitter = TextSplitter()
            chunks = splitter.split_text(text)

            # 如果文件为空，直接返回成功（用于创建知识库）
            if not chunks:
                return True

            # 准备元数据：每个文本块都记录来源文件和块序号，便于前端展示和按文件删除。
            metadatas = []
            for i, chunk in enumerate(chunks):
                chunk_metadata = metadata.copy() if metadata else {}
                chunk_metadata['chunk_index'] = i
                chunk_metadata['file_path'] = file_path
                metadatas.append(chunk_metadata)

            # 添加到向量存储
            self.vector_store.add_embeddings(chunks, metadatas=metadatas)

            return True
        except Exception as e:
            self.last_error = str(e)
            print(f"添加文档时出错: {self.last_error}")
            return False

    def add_document(self, file_path, metadata=None):
        """Add a source file and return parse/vector details."""
        from app.utils.file_processor import FileProcessor
        from app.utils.text_splitter import TextSplitter

        try:
            self.last_error = None
            document_id = (metadata or {}).get('document_id')
            if document_id:
                self.delete_by_document_id(document_id)
            else:
                self.delete_by_source(file_path)

            processor = FileProcessor()
            text = processor.process_file(file_path)

            splitter = TextSplitter()
            chunks = splitter.split_text(text)
            if not chunks:
                return {'success': True, 'vector_count': 0}

            metadatas = []
            for i, chunk in enumerate(chunks):
                chunk_metadata = metadata.copy() if metadata else {}
                chunk_metadata['chunk_index'] = i
                chunk_metadata['file_path'] = file_path
                metadatas.append(chunk_metadata)

            self.vector_store.add_embeddings(chunks, metadatas=metadatas)
            return {'success': True, 'vector_count': len(chunks)}
        except Exception as e:
            self.last_error = str(e)
            print(f"Error adding document: {self.last_error}")
            return {'success': False, 'vector_count': 0, 'error': self.last_error}

    def get_stats(self):
        """获取知识库统计信息，供管理页展示向量数量、文档数量和向量维度。"""
        total_vectors = self.vector_store.count()

        # 计算文档总数（去重）
        documents = set()
        for metadata in self.vector_store.metadatas:
            if 'file_path' in metadata:
                documents.add(metadata['file_path'])
        total_documents = len(documents)

        return {
            'total_vectors': total_vectors,
            'total_documents': total_documents,
            'vector_dimension': self.embedding_model.vector_dimension
        }

    def clear_knowledge_base(self):
        """清空知识库"""
        # 清空向量库
        self.vector_store.clear()
        
        return True

    def delete_knowledge_base(self):
        """删除知识库。

        同时移除向量持久化文件、上传文件夹和内存中的实例缓存。
        """
        # 删除向量库文件
        self.vector_store.delete_store()
        
        # 删除知识库对应的上传文件夹
        import os
        from flask import current_app
        uploads_dir = os.path.join(current_app.root_path, current_app.config['DOCUMENT_UPLOAD_FOLDER'], self.knowledge_base_name)
        if os.path.exists(uploads_dir):
            try:
                import shutil
                shutil.rmtree(uploads_dir)
                print(f'已删除知识库上传文件夹: {uploads_dir}')
            except Exception as e:
                print(f'删除知识库上传文件夹失败: {e}')
        
        # 从实例字典中移除当前知识库
        if self.knowledge_base_name in QAModule._instances:
            del QAModule._instances[self.knowledge_base_name]
        
        return True

    def clear_knowledge_base(self):
        """Clear all vectors in this knowledge base."""
        self.vector_store.clear()
        return True

    def delete_knowledge_base(self):
        """Delete vector store, uploaded files, and cached module instance."""
        self.vector_store.delete_store()

        from flask import current_app
        uploads_dir = os.path.join(current_app.root_path, current_app.config['DOCUMENT_UPLOAD_FOLDER'], self.knowledge_base_name)
        if os.path.exists(uploads_dir):
            try:
                import shutil
                shutil.rmtree(uploads_dir)
                print(f'Deleted knowledge base uploads folder: {uploads_dir}')
            except Exception as e:
                print(f'Failed to delete knowledge base uploads folder: {e}')

        if self.knowledge_base_name in QAModule._instances:
            del QAModule._instances[self.knowledge_base_name]

        return True

    def rename_knowledge_base(self, new_name):
        """重命名知识库。

        名称变化会影响向量文件名、上传目录和元数据中的文件路径，
        因此需要三处一起更新，保证后续检索和删除仍能定位到来源文件。
        """
        from flask import current_app

        # 重命名向量库
        self.vector_store.rename_store(new_name)

        # 保存旧的上传文件夹路径
        old_uploads_dir = os.path.join(current_app.root_path, current_app.config['DOCUMENT_UPLOAD_FOLDER'], self.knowledge_base_name)
        new_uploads_dir = os.path.join(current_app.root_path, current_app.config['DOCUMENT_UPLOAD_FOLDER'], new_name)

        # 重命名上传文件夹
        if os.path.exists(old_uploads_dir):
            try:
                if os.path.exists(new_uploads_dir):
                    import shutil
                    shutil.rmtree(new_uploads_dir)
                os.rename(old_uploads_dir, new_uploads_dir)
                print(f'已重命名上传文件夹: {old_uploads_dir} -> {new_uploads_dir}')
            except Exception as e:
                print(f'重命名上传文件夹失败: {e}')

        # 更新向量数据中的文件路径
        if os.path.exists(new_uploads_dir):
            self.vector_store.update_file_paths(old_uploads_dir, new_uploads_dir)

        # 更新知识库名称
        old_name = self.knowledge_base_name
        self.knowledge_base_name = new_name

        # 更新实例字典，保证之后用新名称获取到的仍是当前实例。
        if old_name in QAModule._instances:
            QAModule._instances[new_name] = QAModule._instances[old_name]
            del QAModule._instances[old_name]

        return True

    def get_vectors(self, page=1, page_size=10, source=None):
        """获取向量列表，支持分页和按来源筛选

        Args:
            page: 页码
            page_size: 每页数量
            source: 来源文件路径，可选

        Returns:
            dict: 包含向量列表和分页信息
        """
        all_vectors = self.vector_store.get_all_vectors()
        
        # 按来源筛选：来源路径来自 URL，Windows 路径分隔符需要统一后再比较。
        if source:
            # 解码URL编码的文件路径
            import urllib.parse
            try:
                decoded_source = urllib.parse.unquote(source)
            except Exception as e:
                print(f"Error decoding source: {e}")
                decoded_source = source
            # 统一路径分隔符，处理Windows和Unix路径的差异
            normalized_source = decoded_source.replace('/', '\\').replace('\\\\', '\\')
            all_vectors = [v for v in all_vectors if 
                          v.get('metadata', {}).get('file_path', '').replace('/', '\\').replace('\\\\', '\\') == normalized_source]
        
        total = len(all_vectors)
        
        # 计算分页
        start = (page - 1) * page_size
        end = start + page_size
        paginated_vectors = all_vectors[start:end]
        
        # 转换格式，添加来源和片段索引信息
        result_vectors = []
        for vector in paginated_vectors:
            metadata = vector.get('metadata', {})
            result_vectors.append({
                'id': vector.get('id'),
                'content': vector.get('content'),
                'source': metadata.get('file_path', '未知来源'),
                'chunk_index': metadata.get('chunk_index', 0),
                'metadata': metadata
            })
        
        return {
            'items': result_vectors,
            'total': total,
            'page': page,
            'page_size': page_size,
            'pages': (total + page_size - 1) // page_size
        }

    def get_file_sources(self):
        """获取所有文件来源

        Returns:
            list: 文件来源列表，每个元素包含文件路径和向量数量
        """
        all_vectors = self.vector_store.get_all_vectors()
        
        # 调试信息
        print("\n=== File sources debugging ===")
        
        # 按文件路径分组并统计数量，一个文件通常会被切成多个向量片段。
        sources = {}
        for vector in all_vectors:
            file_path = vector.get('metadata', {}).get('file_path', '未知来源')
            if file_path not in sources:
                sources[file_path] = 0
                # 调试信息
                print(f"Found file path: {file_path}")
            sources[file_path] += 1
        
        # 转换为列表格式
        result = []
        for file_path, count in sources.items():
            # 提取文件名作为显示名称
            file_name = os.path.basename(file_path)
            result.append({
                'file_path': file_path,
                'file_name': file_name,
                'vector_count': count
            })
        
        # 按文件名排序
        result.sort(key=lambda x: x['file_name'])
        
        # 调试信息
        print(f"Total sources found: {len(result)}")
        print("=============================\n")
        
        return result

    def delete_by_source(self, file_path):
        """按文件来源删除向量数据

        Args:
            file_path: 文件路径

        Returns:
            int: 删除的向量数量
        """
        all_vectors = self.vector_store.get_all_vectors()
        
        # 找出该文件来源的所有向量 ID；后续交给 VectorStore 统一删除并重建索引。
        ids_to_delete = []
        # 统一路径分隔符，处理 Windows 和 Unix 路径的差异。
        # 上传、浏览器传参、后端保存元数据时可能混用 / 和 \。
        normalized_file_path = file_path.replace('/', '\\').replace('\\\\', '\\')
        
        # 调试信息
        print(f"Delete request for file path: {file_path}")
        print(f"Normalized delete path: {normalized_file_path}")
        
        for vector in all_vectors:
            vector_file_path = vector.get('metadata', {}).get('file_path', '')
            # 统一向量文件路径的分隔符
            normalized_vector_path = vector_file_path.replace('/', '\\').replace('\\\\', '\\')
            
            # 调试信息
            if vector_file_path:
                print(f"Vector file path: {vector_file_path}")
                print(f"Normalized vector path: {normalized_vector_path}")
                print(f"Match: {normalized_vector_path == normalized_file_path}")
            
            if normalized_vector_path == normalized_file_path:
                ids_to_delete.append(vector.get('id'))
        
        # 调试信息
        print(f"Found {len(ids_to_delete)} vectors to delete")
        
        # 删除这些向量
        deleted_count = 0
        if ids_to_delete:
            deleted_count = self.vector_store.delete(ids_to_delete)
            # 重新加载数据，确保vector_store中的数据是最新的
            self.vector_store._load_data()
        
        return deleted_count

    def delete_by_document_id(self, document_id):
        """Delete vectors that belong to one tracked uploaded document."""
        all_vectors = self.vector_store.get_all_vectors()
        ids_to_delete = [
            vector.get('id')
            for vector in all_vectors
            if str(vector.get('metadata', {}).get('document_id')) == str(document_id)
        ]
        if not ids_to_delete:
            return 0
        deleted_count = self.vector_store.delete(ids_to_delete)
        self.vector_store._load_data()
        return deleted_count
