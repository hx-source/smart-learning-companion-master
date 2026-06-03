"""LangChain 向量服务模块。

EmbeddingModel 实现 LangChain 的 Embeddings 接口，优先调用 Ollama 嵌入模型；
VectorStore 使用 LangChain Community 的 FAISS 向量库负责持久化和检索。
外部方法名保留原项目的接口，方便 Flask 路由和知识库服务平滑迁移。
"""

import math
import os
import pickle
import shutil
import time

import requests
from dotenv import load_dotenv
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings

from config import Config

load_dotenv()


class EmbeddingModel(Embeddings):
    """LangChain Embeddings 适配器。

    优先调用 Ollama `/api/embeddings`；如果 Ollama 不可用，则使用字符哈希生成
    fallback 向量，让知识库管理页和基础检索流程仍能运行。
    """

    _initialized = False

    def __init__(self, model_name=None):
        self.model_name = model_name or Config.OLLAMA_EMBEDDING_MODEL
        self.ollama_url = Config.OLLAMA_BASE_URL
        self.use_ollama = True
        self.vector_dimension = 1024

        if not EmbeddingModel._initialized:
            print(f"Ollama URL: {self.ollama_url}")

        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=10)
            if response.status_code != 200:
                raise RuntimeError(f"Ollama API返回错误: {response.status_code}")

            test_embedding = self.get_embedding("测试文本")
            if test_embedding:
                self.vector_dimension = len(test_embedding)
                if not EmbeddingModel._initialized:
                    print(f"向量维度: {self.vector_dimension}")
        except Exception as e:
            if not EmbeddingModel._initialized:
                print(f"Ollama连接失败: {str(e)}")
                print("使用默认的嵌入方法")
            self.use_ollama = False
            self.vector_dimension = 512

        EmbeddingModel._initialized = True

    def embed_documents(self, texts):
        """LangChain 批量嵌入入口。"""
        return self.get_batch_embeddings(texts)

    def embed_query(self, text):
        """LangChain 查询嵌入入口。"""
        return self.get_embedding(text)

    def get_embedding(self, text):
        """获取单个文本的嵌入向量。"""
        if not text:
            return [0.0] * self.vector_dimension

        if not self.use_ollama:
            return self._fallback_embedding(text)

        try:
            text = text[:2000]
            response = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.model_name, "prompt": text},
                timeout=30,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Ollama API返回错误: {response.status_code}")

            embedding = response.json().get("embedding", [])
            if len(embedding) != self.vector_dimension:
                if len(embedding) > self.vector_dimension:
                    embedding = embedding[: self.vector_dimension]
                else:
                    embedding.extend([0.0] * (self.vector_dimension - len(embedding)))
            return embedding
        except Exception as e:
            print(f"Ollama嵌入失败: {str(e)}")
            return self._fallback_embedding(text)

    def get_batch_embeddings(self, texts, batch_size=8):
        """批量获取文本嵌入。"""
        embeddings = []
        total_texts = len(texts)
        for i in range(0, total_texts, batch_size):
            batch = texts[i : i + batch_size]
            embeddings.extend(self.get_embedding(text) for text in batch)
            time.sleep(0.1)
            print(f"已处理 {min(i + batch_size, total_texts)}/{total_texts} 个文本")
        return embeddings

    def _fallback_embedding(self, text):
        """基于字符哈希的 fallback 嵌入方法。"""
        embedding = [0.0] * self.vector_dimension
        for char in text:
            embedding[ord(char) % self.vector_dimension] += 1.0

        norm = math.sqrt(sum(x**2 for x in embedding))
        if norm > 0:
            embedding = [x / norm for x in embedding]
        return embedding


class VectorStore:
    """基于 LangChain FAISS 的本地向量存储。"""

    _initialized = False

    def __init__(self, collection_name="knowledge_base"):
        self.collection_name = collection_name
        self.store_dir = f"./{collection_name}_langchain_faiss"
        self.legacy_data_file = f"./{collection_name}_vector_store.pkl"
        self.embedding_model = EmbeddingModel()
        self.store = None
        self.documents = []
        self.metadatas = []
        self.ids = []
        self.vector_dimension = self.embedding_model.vector_dimension
        self._load_data()
        VectorStore._initialized = True

    def _load_data(self):
        """加载 LangChain FAISS 持久化目录。"""
        if os.path.isdir(self.store_dir):
            try:
                self.store = FAISS.load_local(
                    self.store_dir,
                    self.embedding_model,
                    allow_dangerous_deserialization=True,
                )
                self._sync_cache_from_store()
                if not VectorStore._initialized:
                    print(f"加载 LangChain FAISS 向量库: {self.store_dir}")
            except Exception as e:
                print(f"加载 LangChain FAISS 向量库失败: {str(e)}")
                self.store = None
                self._sync_cache_from_store()
        elif os.path.exists(self.legacy_data_file):
            self._migrate_legacy_store()
        else:
            self._sync_cache_from_store()

    def _migrate_legacy_store(self):
        """把旧版 pickle/Faiss 存储迁移到 LangChain FAISS 目录。

        旧索引文件不直接复用，因为 LangChain 会维护自己的 docstore 和 id 映射。
        这里读取旧 pickle 中的原文、元数据和 ID，然后重新生成向量写入新目录。
        """
        try:
            with open(self.legacy_data_file, "rb") as f:
                data = pickle.load(f)
            documents = data.get("documents", [])
            metadatas = data.get("metadatas", [])
            ids = data.get("ids", [])

            if not documents:
                self._sync_cache_from_store()
                return

            print(f"正在迁移旧版向量库到 LangChain FAISS: {self.legacy_data_file}")
            self.add_embeddings(documents, metadatas=metadatas, ids=ids or None)
            print(f"旧版向量库迁移完成: {len(documents)} 个文档片段")
        except Exception as e:
            print(f"迁移旧版向量库失败: {str(e)}")
            self.store = None
            self._sync_cache_from_store()

    def _save_data(self):
        """保存 LangChain FAISS 向量库。"""
        if self.store is not None:
            self.store.save_local(self.store_dir)
        self._sync_cache_from_store()

    def _sync_cache_from_store(self):
        """把 LangChain docstore 同步为旧接口使用的 documents/metadatas/ids。"""
        self.documents = []
        self.metadatas = []
        self.ids = []
        if self.store is None:
            return

        for _, docstore_id in sorted(self.store.index_to_docstore_id.items()):
            doc = self.store.docstore.search(docstore_id)
            if isinstance(doc, str):
                continue
            metadata = dict(doc.metadata or {})
            metadata.setdefault("doc_id", docstore_id)
            self.documents.append(doc.page_content)
            self.metadatas.append(metadata)
            self.ids.append(docstore_id)

    def add_embeddings(self, documents, embeddings=None, metadatas=None, ids=None):
        """添加文本到 LangChain FAISS。

        `embeddings` 参数保留旧接口兼容性，实际嵌入由 LangChain 调用
        EmbeddingModel 完成。
        """
        if not documents:
            return

        if ids is None:
            start_idx = len(self.ids)
            ids = [f"doc_{start_idx + i}" for i in range(len(documents))]

        if metadatas is None:
            metadatas = [{} for _ in documents]

        normalized_metadatas = []
        for doc_id, metadata in zip(ids, metadatas):
            item = dict(metadata or {})
            item["doc_id"] = doc_id
            normalized_metadatas.append(item)

        if self.store is None:
            self.store = FAISS.from_texts(
                documents,
                self.embedding_model,
                metadatas=normalized_metadatas,
                ids=ids,
            )
        else:
            self.store.add_texts(
                documents,
                metadatas=normalized_metadatas,
                ids=ids,
            )

        self._save_data()
        print(f"已添加 {len(documents)} 个文档片段")

    def search(self, query, n_results=5, where=None):
        """搜索相似文档。

        Args:
            query: 查询文本或已经生成好的查询向量。
            n_results: 返回结果数量。
            where: 预留过滤条件，当前保持旧接口兼容。
        """
        if self.store is None or len(self.ids) == 0:
            return {"documents": [[]], "distances": [[]], "metadatas": [[]], "ids": [[]]}

        n_results = min(n_results, len(self.ids))
        if isinstance(query, str):
            docs_and_scores = self.store.similarity_search_with_score(query, k=n_results)
        else:
            docs_and_scores = self.store.similarity_search_with_score_by_vector(query, k=n_results)

        documents = []
        distances = []
        metadatas = []
        ids = []
        for doc, score in docs_and_scores:
            metadata = dict(doc.metadata or {})
            doc_id = metadata.get("doc_id")
            documents.append(doc.page_content)
            distances.append(float(score))
            metadatas.append(metadata)
            ids.append(doc_id)

        return {
            "documents": [documents],
            "distances": [distances],
            "metadatas": [metadatas],
            "ids": [ids],
        }

    def get(self, ids):
        """根据 ID 获取文档。"""
        docs = []
        metadatas = []
        found_ids = []
        if self.store is None:
            return {"documents": docs, "metadatas": metadatas, "ids": found_ids}

        for doc_id in ids:
            doc = self.store.docstore.search(doc_id)
            if isinstance(doc, str):
                continue
            docs.append(doc.page_content)
            metadatas.append(doc.metadata)
            found_ids.append(doc_id)
        return {"documents": docs, "metadatas": metadatas, "ids": found_ids}

    def update(self, ids, documents=None, embeddings=None, metadatas=None):
        """更新文档。

        FAISS 不适合原地更新，保持旧行为：提示使用删除后重新添加。
        """
        print("警告: LangChain FAISS 不支持稳定的原地更新，请删除后重新添加")

    def delete(self, ids):
        """删除文档并持久化。"""
        if self.store is None:
            return 0

        unique_ids = [doc_id for doc_id in set(ids) if doc_id]
        before = len(self.ids)
        if unique_ids:
            self.store.delete(unique_ids)
            self._save_data()
        deleted_count = before - len(self.ids)
        print(f"已删除 {deleted_count} 个文档片段")
        return deleted_count

    def clear(self):
        """清空向量库。"""
        self.store = None
        self.documents = []
        self.metadatas = []
        self.ids = []
        if os.path.isdir(self.store_dir):
            shutil.rmtree(self.store_dir)
        print("向量库已清空")

    def delete_store(self):
        """删除向量库。"""
        self.clear()
        print("向量库已删除")

    def rename_store(self, new_name):
        """重命名向量库持久化目录。"""
        new_store_dir = f"./knowledge_base_{new_name}_langchain_faiss"
        if os.path.isdir(self.store_dir):
            if os.path.isdir(new_store_dir):
                shutil.rmtree(new_store_dir)
            os.rename(self.store_dir, new_store_dir)
            print(f"已重命名向量库目录: {self.store_dir} -> {new_store_dir}")
        self.store_dir = new_store_dir
        self._load_data()

    def update_file_paths(self, old_base_path, new_base_path):
        """更新所有向量元数据中的文件路径。"""
        if self.store is None:
            return

        updated_count = 0
        for docstore_id in self.store.index_to_docstore_id.values():
            doc = self.store.docstore.search(docstore_id)
            if isinstance(doc, str):
                continue
            old_path = doc.metadata.get("file_path")
            if old_path and old_base_path.lower() in old_path.lower():
                doc.metadata["file_path"] = old_path.replace(old_base_path, new_base_path, 1)
                updated_count += 1

        if updated_count > 0:
            self._save_data()
            print(f"已更新 {updated_count} 个向量的文件路径")

    def count(self):
        """获取文档片段数量。"""
        return len(self.ids)

    def get_all_vectors(self):
        """获取所有向量对应的文本和元数据。"""
        self._sync_cache_from_store()
        return [
            {"id": doc_id, "content": doc, "metadata": metadata}
            for doc_id, doc, metadata in zip(self.ids, self.documents, self.metadatas)
        ]

    def get_embeddings(self):
        """LangChain FAISS 不直接暴露原始嵌入，保留旧接口返回空列表。"""
        return []
