"""向量服务模块。

EmbeddingModel 负责把文本转成向量；VectorStore 负责把文本、元数据和
Faiss 索引持久化到本地文件。知识库问答时会通过这里完成相似片段召回。
"""
import requests
import os
import pickle
import numpy as np
from dotenv import load_dotenv
import time
from config import Config

load_dotenv()

class EmbeddingModel:
    """向量嵌入模型。

    优先调用 Ollama 的嵌入模型；如果 Ollama 不可用，则使用字符哈希生成
    一个可用但效果较弱的 fallback 向量，保证项目功能不会直接崩溃。
    """
    _initialized = False
    
    def __init__(self, model_name=None):
        """初始化嵌入模型

        Args:
            model_name: 模型名称
        """
        self.model_name = model_name or Config.OLLAMA_EMBEDDING_MODEL
        self.use_ollama = True
        self.vector_dimension = 1024  # bge-m3 模型的默认向量维度，首次连通后会按实际返回值修正。

        # 配置Ollama客户端
        ollama_url = Config.OLLAMA_BASE_URL
        self.ollama_url = ollama_url
        
        if not EmbeddingModel._initialized:
            print(f"Ollama URL: {ollama_url}")
            pass

        try:
            # 测试 Ollama 连接，同时用一段文本探测真实向量维度。
            response = requests.get(f"{ollama_url}/api/tags", timeout=10)
            if response.status_code == 200:
                # 移除重复的打印语句
                pass
                # 测试生成一个嵌入，确定向量维度
                test_embedding = self.get_embedding("测试文本")
                if test_embedding:
                    self.vector_dimension = len(test_embedding)
                    if not EmbeddingModel._initialized:
                        print(f"向量维度: {self.vector_dimension}")
            else:
                raise Exception(f"Ollama API返回错误: {response.status_code}")
        except Exception as e:
            if not EmbeddingModel._initialized:
                print(f"Ollama连接失败: {str(e)}")
                print("使用默认的嵌入方法")
            self.use_ollama = False
            # 设置默认向量维度，后续 fallback_embedding 会按这个维度生成向量。
            self.vector_dimension = 512
        
        EmbeddingModel._initialized = True

    def get_embedding(self, text):
        """获取单个文本的嵌入向量

        Args:
            text: 文本

        Returns:
            嵌入向量
        """
        if not text:
            return [0.0] * self.vector_dimension  # 返回零向量

        if self.use_ollama:
            try:
                # 限制文本长度，避免API超时
                text = text[:2000]  # 限制为2000个字符
                response = requests.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={
                        "model": self.model_name,
                        "prompt": text
                    },
                    timeout=30  # 设置超时时间
                )
                if response.status_code == 200:
                    result = response.json()
                    embedding = result.get('embedding', [])
                    # 确保返回的向量维度一致。
                    # Faiss 索引要求所有向量维度完全相同，过长截断，过短补零。
                    if len(embedding) != self.vector_dimension:
                        # 调整向量维度
                        if len(embedding) > self.vector_dimension:
                            embedding = embedding[:self.vector_dimension]
                        else:
                            embedding.extend([0.0] * (self.vector_dimension - len(embedding)))
                    return embedding
                else:
                    raise Exception(f"Ollama API返回错误: {response.status_code}")
            except Exception as e:
                print(f"Ollama嵌入失败: {str(e)}")
                # 使用基于字符的哈希作为 fallback，确保向量维度一致。
                return self._fallback_embedding(text)
        else:
            # 使用基于字符的哈希作为 fallback。
            return self._fallback_embedding(text)

    def get_batch_embeddings(self, texts, batch_size=8):
        """批量获取文本的嵌入向量

        Args:
            texts: 文本列表
            batch_size: 批处理大小

        Returns:
            嵌入向量列表
        """
        embeddings = []
        total_texts = len(texts)

        for i in range(0, total_texts, batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = []

            for text in batch:
                embedding = self.get_embedding(text)
                batch_embeddings.append(embedding)
                # 添加小延迟以避免API限制
                time.sleep(0.1)

            embeddings.extend(batch_embeddings)
            print(f"已处理 {min(i + batch_size, total_texts)}/{total_texts} 个文本")

        return embeddings

    def _fallback_embedding(self, text):
        """基于字符哈希的fallback嵌入方法

        Args:
            text: 文本

        Returns:
            嵌入向量
        """
        # 使用字符哈希生成固定维度的向量。
        # 这种方式不能替代语义嵌入，但能在本地模型不可用时维持检索流程可运行。
        embedding = [0.0] * self.vector_dimension
        for i, char in enumerate(text):
            # 使用字符的Unicode码点作为种子
            char_code = ord(char) % self.vector_dimension
            embedding[char_code] += 1.0

        # 归一化
        import math
        norm = math.sqrt(sum(x ** 2 for x in embedding))
        if norm > 0:
            embedding = [x / norm for x in embedding]

        return embedding

class VectorStore:
    """本地向量存储。

    文档内容、元数据、ID 存在 pickle 文件中；相似度索引存在 Faiss index 文件中。
    两者必须保持同步，因此新增/删除/重命名后都会立即保存。
    """
    _initialized = False
    
    def __init__(self, collection_name="knowledge_base"):
        """初始化向量存储（使用 Faiss）

        Args:
            collection_name: 集合名称
        """
        self.collection_name = collection_name
        self.data_file = f"./{collection_name}_vector_store.pkl"
        self.index_file = f"./{collection_name}_faiss.index"

        # 文档和元数据存储：Faiss 只存向量索引，不保存原文和来源信息。
        self.documents = []
        self.metadatas = []
        self.ids = []
        self.vector_dimension = 1024  # bge-m3 模型的向量维度

        # 初始化 Faiss 索引。使用内积索引前会先归一化向量，效果等价于余弦相似度。
        self._init_faiss_index()

        # 加载已有数据
        self._load_data()
        
        VectorStore._initialized = True

    def _init_faiss_index(self):
        """初始化 Faiss 索引"""
        import faiss

        # 检查是否存在保存的索引
        if os.path.exists(self.index_file):
            self.index = faiss.read_index(self.index_file)
            if not VectorStore._initialized:
                print(f"加载 Faiss 索引: {self.index_file}")
        else:
            # 创建新的索引（使用内积，等同于余弦相似度当向量归一化后）
            self.index = faiss.IndexFlatIP(self.vector_dimension)
            if not VectorStore._initialized:
                print(f"创建新的 Faiss 索引，维度: {self.vector_dimension}")

        self.use_faiss = True

    def _load_data(self):
        """加载文档和元数据"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'rb') as f:
                    data = pickle.load(f)
                    self.documents = data.get('documents', [])
                    self.metadatas = data.get('metadatas', [])
                    self.ids = data.get('ids', [])
                if not VectorStore._initialized:
                    print(f"加载了 {len(self.documents)} 个文档")
            except Exception as e:
                if not VectorStore._initialized:
                    print(f"加载数据失败: {str(e)}")

    def _save_data(self):
        """保存文档和元数据"""
        try:
            data = {
                'documents': self.documents,
                'metadatas': self.metadatas,
                'ids': self.ids
            }
            with open(self.data_file, 'wb') as f:
                pickle.dump(data, f)

            # 保存 Faiss 索引
            if self.use_faiss:
                import faiss
                faiss.write_index(self.index, self.index_file)

            print(f"保存了 {len(self.documents)} 个文档")
        except Exception as e:
            print(f"保存数据失败: {str(e)}")

    def _normalize_vectors(self, vectors):
        """归一化向量（用于余弦相似度）。

        IndexFlatIP 计算内积；把向量归一化后，内积就可以作为余弦相似度使用。
        """
        vectors = np.array(vectors, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)  # 避免除零
        return vectors / norms

    def add_embeddings(self, documents, embeddings, metadatas=None, ids=None):
        """添加向量嵌入

        Args:
            documents: 文档列表
            embeddings: 嵌入向量列表
            metadatas: 元数据列表
            ids: 文档ID列表
        """
        if not ids:
            # 生成新的 ID
            start_idx = len(self.documents)
            ids = [f"doc_{start_idx + i}" for i in range(len(documents))]

        if not metadatas:
            metadatas = [{} for _ in range(len(documents))]

        # 归一化向量后再进入 Faiss，保证搜索分数和 query 侧处理方式一致。
        normalized_embeddings = self._normalize_vectors(embeddings)

        # 添加到 Faiss 索引
        self.index.add(normalized_embeddings)

        # 保存文档和元数据
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)
        self.ids.extend(ids)

        self._save_data()
        print(f"已添加 {len(documents)} 个文档")

    def search(self, query_embedding, n_results=5, where=None):
        """搜索相似向量

        Args:
            query_embedding: 查询向量
            n_results: 返回结果数量
            where: 过滤条件（暂不支持）

        Returns:
            搜索结果
        """
        if len(self.documents) == 0:
            return {"documents": [[]], "distances": [[]], "metadatas": [[]], "ids": [[]]}

        # 归一化查询向量
        query_vec = self._normalize_vectors([query_embedding])

        # 确保 n_results 不超过文档数量
        n_results = min(n_results, len(self.documents))

        # 使用 Faiss 搜索
        distances, indices = self.index.search(query_vec, n_results)

        # Faiss 返回的是内积相似度，这里转换为距离（1 - 相似度），兼容上层展示逻辑。
        distances = 1 - distances

        result_indices = indices[0]

        # 构建结果
        results = {
            "documents": [[self.documents[i] for i in result_indices]],
            "distances": [distances[0].tolist() if self.use_faiss else distances[0]],
            "metadatas": [[self.metadatas[i] for i in result_indices]],
            "ids": [[self.ids[i] for i in result_indices]]
        }

        return results

    def get(self, ids):
        """根据ID获取文档

        Args:
            ids: 文档ID列表

        Returns:
            文档信息
        """
        indices = [self.ids.index(id) for id in ids if id in self.ids]
        return {
            "documents": [self.documents[i] for i in indices],
            "metadatas": [self.metadatas[i] for i in indices],
            "ids": [self.ids[i] for i in indices]
        }

    def update(self, ids, documents=None, embeddings=None, metadatas=None):
        """更新文档

        Args:
            ids: 文档ID列表
            documents: 文档列表
            embeddings: 嵌入向量列表
            metadatas: 元数据列表
        """
        for i, id in enumerate(ids):
            if id in self.ids:
                index = self.ids.index(id)
                if documents:
                    self.documents[index] = documents[i]
                if metadatas:
                    self.metadatas[index] = metadatas[i]
                # 注意：Faiss 不支持原地更新向量。需要更新文本语义时，应删除后重新添加。
                if embeddings:
                    print("警告: Faiss 索引不支持直接更新向量，请删除后重新添加")

        self._save_data()

    def delete(self, ids):
        """删除文档

        Args:
            ids: 文档ID列表

        Returns:
            int: 实际删除的文档数量
        """
        # 去除重复ID，确保每个ID只处理一次
        unique_ids = list(set(ids))
        
        # 找出所有要删除的索引
        indices_to_delete = []
        for id in unique_ids:
            if id in self.ids:
                # 处理可能存在的重复ID情况
                for i, existing_id in enumerate(self.ids):
                    if existing_id == id:
                        indices_to_delete.append(i)
        
        # 按降序排序，从后往前删列表元素，避免前面的删除改变后续索引位置。
        indices_to_delete = sorted(list(set(indices_to_delete)), reverse=True)

        for index in indices_to_delete:
            del self.documents[index]
            del self.metadatas[index]
            del self.ids[index]

        # Faiss 不支持直接删除，需要用剩余文档重新生成向量并重建索引。
        self._rebuild_index()
        self._save_data()
        deleted_count = len(indices_to_delete)
        print(f"已删除 {deleted_count} 个文档")
        return deleted_count

    def _rebuild_index(self):
        """重建 Faiss 索引"""
        import faiss

        # 创建新索引
        self.index = faiss.IndexFlatIP(self.vector_dimension)

        # 如果有文档，重新添加
        if self.documents:
            # 重新生成所有文档的嵌入向量。
            # 这是删除/批量调整后的保守做法，速度慢一些但状态最可靠。
            embedding_model = EmbeddingModel()
            embeddings = embedding_model.get_batch_embeddings(self.documents)
            
            # 归一化向量
            normalized_embeddings = self._normalize_vectors(embeddings)
            
            # 添加到 Faiss 索引
            self.index.add(normalized_embeddings)
            print(f"索引已重建，添加了 {len(self.documents)} 个文档")

    def clear(self):
        """清空向量库"""
        self.documents = []
        self.metadatas = []
        self.ids = []

        # 重建索引
        import faiss
        self.index = faiss.IndexFlatIP(self.vector_dimension)

        # 删除持久化文件；下一次添加文档时会重新生成。
        if os.path.exists(self.data_file):
            os.remove(self.data_file)
        if os.path.exists(self.index_file):
            os.remove(self.index_file)

        print("向量库已清空")

    def delete_store(self):
        """删除向量库"""
        # 清空数据
        self.documents = []
        self.metadatas = []
        self.ids = []

        # 删除索引
        import faiss
        self.index = faiss.IndexFlatIP(self.vector_dimension)

        # 删除文件
        if os.path.exists(self.data_file):
            os.remove(self.data_file)
            print(f"已删除向量库文件: {self.data_file}")
        if os.path.exists(self.index_file):
            os.remove(self.index_file)
            print(f"已删除Faiss索引文件: {self.index_file}")

        print("向量库已删除")

    def rename_store(self, new_name):
        """重命名向量库"""
        # 生成新的文件名。知识库名称和向量文件名保持一致，便于管理多个知识库。
        new_data_file = f"./knowledge_base_{new_name}_vector_store.pkl"
        new_index_file = f"./knowledge_base_{new_name}_faiss.index"

        # 重命名文件
        if os.path.exists(self.data_file):
            os.rename(self.data_file, new_data_file)
            print(f"已重命名向量库文件: {self.data_file} -> {new_data_file}")
        if os.path.exists(self.index_file):
            os.rename(self.index_file, new_index_file)
            print(f"已重命名Faiss索引文件: {self.index_file} -> {new_index_file}")

        # 更新文件名
        self.data_file = new_data_file
        self.index_file = new_index_file

        print("向量库已重命名")

    def update_file_paths(self, old_base_path, new_base_path):
        """更新所有向量元数据中的文件路径
        Args:
            old_base_path: 旧的路径前缀
            new_base_path: 新的路径前缀
        """
        updated_count = 0
        for metadata in self.metadatas:
            if 'file_path' in metadata:
                old_path = metadata['file_path']
                if old_base_path.lower() in old_path.lower():
                    metadata['file_path'] = old_path.replace(old_base_path, new_base_path, 1)
                    updated_count += 1

        if updated_count > 0:
            self._save_data()
            print(f"已更新 {updated_count} 个向量的文件路径")

    def count(self):
        """获取文档数量"""
        return len(self.documents)

    def get_all_vectors(self):
        """获取所有向量信息"""
        vectors = []
        for i, doc in enumerate(self.documents):
            vectors.append({
                'id': self.ids[i],
                'content': doc,
                'metadata': self.metadatas[i]
            })
        return vectors

    def get_embeddings(self):
        """获取所有嵌入向量"""
        # 注意：Faiss 不直接存储原始向量，这里返回空列表
        # 如果需要原始向量，需要在添加时单独保存
        return []
