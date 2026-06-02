import requests
import os
from dotenv import load_dotenv
from app.utils.vector_service import EmbeddingModel, VectorStore
from app.services.reranker_service import RerankerService
from config import Config

load_dotenv()


class QAModule:
    _initialized = False
    _instances = {}  # 存储不同知识库的实例
    
    def __new__(cls, knowledge_base_name="default", model_type="ollama"):
        """创建或获取知识库实例
        
        Args:
            knowledge_base_name: 知识库名称
            model_type: 模型类型 (api 或 ollama)
        """
        if knowledge_base_name not in cls._instances:
            cls._instances[knowledge_base_name] = super(QAModule, cls).__new__(cls)
        return cls._instances[knowledge_base_name]
    
    def __init__(self, knowledge_base_name="default", model_type="ollama"):
        """初始化问答模块
        
        Args:
            knowledge_base_name: 知识库名称
            model_type: 模型类型 (api 或 ollama)
        """
        if not hasattr(self, 'initialized'):
            self.knowledge_base_name = knowledge_base_name
            self.model_type = model_type
            self.embedding_model = EmbeddingModel()
            self.vector_store = VectorStore(collection_name=f"knowledge_base_{knowledge_base_name}")
            self.llm_model = Config.OLLAMA_MODEL  # 使用配置中的生成模型
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

        # 生成问题的嵌入向量
        query_embedding = self.embedding_model.get_embedding(question)

        # 搜索相关文档
        search_results = self.vector_store.search(query_embedding, n_results=5)

        # 构建上下文
        context = self._build_context(search_results)

        # 构建提示词
        prompt = self._build_prompt(question, context)

        # 调用LLM生成回答
        answer = self._call_llm(prompt)

        # 更新对话历史
        self.chat_history.append({"role": "user", "content": question})
        self.chat_history.append({"role": "assistant", "content": answer})

        return answer

    def query_with_knowledge(self, question, history=None, model_type=None):
        """处理用户查询，返回带知识库来源的回答

        Args:
            question: 用户问题
            history: 对话历史
            model_type: 模型类型 (api 或 ollama)

        Returns:
            dict: 包含 answer 和 sources 的字典
        """
        # 生成问题的嵌入向量
        query_embedding = self.embedding_model.get_embedding(question)

        # 搜索相关文档（召回阶段）
        search_results = self.vector_store.search(query_embedding, n_results=10)  # 先召回更多文档

        # 重排阶段（如果启用）
        if self.use_reranker and self.reranker:
            search_results = self.reranker.rerank_with_metadata(
                question, 
                search_results, 
                top_k=5  # 重排后保留 top 5
            )

        # 构建上下文
        context = self._build_context(search_results)

        # 构建提示词
        prompt = self._build_prompt(question, context)

        # 调用LLM生成回答
        answer = self._call_llm(prompt, model_type)

        # 构建来源信息
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

        return {
            'answer': answer,
            'sources': sources,
            'context_used': bool(context.strip())
        }

    def _build_context(self, search_results):
        """构建上下文

        Args:
            search_results: 搜索结果

        Returns:
            上下文文本
        """
        context = []
        if search_results and 'documents' in search_results:
            for doc in search_results['documents'][0]:
                if doc:
                    context.append(doc)
        return '\n\n'.join(context)

    def _build_prompt(self, question, context):
        """构建提示词

        Args:
            question: 用户问题
            context: 上下文

        Returns:
            提示词
        """
        if context.strip():
            prompt = f"""你是一个智能知识库助手，根据提供的上下文回答用户问题。

上下文：
{context}

用户问题：
{question}

请仔细阅读上下文，提取与用户问题相关的所有信息。
如果上下文包含用户问题的答案，请直接提供答案，不要添加任何额外信息。
如果上下文没有相关信息，请如实告知。

请确保回答准确、完整，包括所有相关的细节。"""
        else:
            prompt = f"""你是一个智能学习助手，请回答用户的问题。

用户问题：
{question}

请提供准确、有帮助的回答。"""

        return prompt

    def _call_llm(self, prompt, model_type=None):
        """调用LLM

        Args:
            prompt: 提示词
            model_type: 模型类型 (api 或 ollama)

        Returns:
            回答
        """
        # 确定使用的模型类型
        use_model_type = model_type or self.model_type
        
        try:
            if use_model_type == 'api':
                # 使用AI服务调用API模型
                from app.services.ai_service import AIService
                ai_service = AIService()
                result = ai_service.ask_question(prompt, model_type='api')
                return result['answer']
            else:
                # 使用Ollama API
                # 使用/api/chat端点，与ai_service.py保持一致
                response = requests.post(
                    f"{self.ollama_url}/api/chat",
                    json={
                        "model": self.llm_model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "stream": False,
                        "options": {
                            "temperature": 0.7,
                            "num_predict": 1000
                        }
                    },
                    timeout=120
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return result['message']['content']
                else:
                    return f"Ollama API 调用失败: {response.status_code}"
            
        except requests.exceptions.ConnectionError as e:
            return f"无法连接到 Ollama，请检查 Ollama 服务是否正常运行"
        except Exception as e:
            return f"API 调用失败: {str(e)}"

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
            # 先删除该文件的旧向量（如果存在）
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

            # 生成嵌入向量
            embeddings = self.embedding_model.get_batch_embeddings(chunks)

            # 准备元数据
            metadatas = []
            for i, chunk in enumerate(chunks):
                chunk_metadata = metadata.copy() if metadata else {}
                chunk_metadata['chunk_index'] = i
                chunk_metadata['file_path'] = file_path
                metadatas.append(chunk_metadata)

            # 添加到向量存储
            self.vector_store.add_embeddings(chunks, embeddings, metadatas)

            return True
        except Exception as e:
            print(f"添加文档时出错: {str(e)}")
            return False

    def get_stats(self):
        """获取知识库统计信息"""
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
        """删除知识库"""
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

    def rename_knowledge_base(self, new_name):
        """重命名知识库"""
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

        # 更新实例字典
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
        
        # 按来源筛选
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
        
        # 按文件路径分组并统计数量
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
        
        # 找出该文件来源的所有向量ID
        ids_to_delete = []
        # 统一路径分隔符，处理Windows和Unix路径的差异
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
