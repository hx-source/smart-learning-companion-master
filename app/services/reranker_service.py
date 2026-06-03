"""重排服务模块。

向量搜索先快速召回候选片段，重排再用更“贵”但更细的相关性判断重新排序。
当前实现通过 Ollama 生成式模型打分模拟 Cross-Encoder 行为。
"""
import requests
from config import Config


class RerankerService:
    """检索结果重排服务。"""
    _initialized = False
    
    def __init__(self, model_name=None):
        """初始化重排模型
        
        Args:
            model_name: 重排模型名称
        """
        self.model_name = model_name or Config.OLLAMA_RERANKER_MODEL
        self.ollama_url = Config.OLLAMA_BASE_URL
        self.use_reranker = True
        
        if not RerankerService._initialized:
            print(f"重排模型：{self.model_name}")
        
        # 测试连接；如果 Ollama 不可用，后续直接退回原始向量检索顺序。
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code != 200:
                print("警告：无法连接到 Ollama，重排功能可能不可用")
                self.use_reranker = False
        except Exception as e:
            print(f"警告：Ollama 连接失败 - {str(e)}")
            self.use_reranker = False
        
        RerankerService._initialized = True
    
    def rerank(self, query, documents, top_k=None):
        """对文档进行重排序
        
        Args:
            query: 用户查询
            documents: 待重排的文档列表
            top_k: 返回前 K 个结果，默认为 None（返回所有）
            
        Returns:
            list: 重排后的文档列表，每个元素包含文档内容和重排分数
        """
        if not self.use_reranker or not documents:
            # 如果不使用重排器，直接返回原文档
            return [
                {'content': doc, 'score': 0.0, 'index': i}
                for i, doc in enumerate(documents)
            ][:top_k or len(documents)]
        
        try:
            # 调用 Ollama 的重排逻辑。
            # 注意：Ollama 本身不直接支持重排 API，这里使用模拟的 Cross-Encoder 方式
            # 实际部署时需要使用支持重排的模型或服务
            scores = self._compute_similarity_scores(query, documents)
            
            # 构建结果
            results = []
            for i, (doc, score) in enumerate(zip(documents, scores)):
                results.append({
                    'content': doc,
                    'score': float(score),
                    'index': i
                })
            
            # 按分数降序排序，分数越高表示模型认为越相关。
            results.sort(key=lambda x: x['score'], reverse=True)
            
            # 返回 top_k 个结果
            if top_k:
                results = results[:top_k]
            
            return results
            
        except Exception as e:
            print(f"重排失败：{str(e)}，使用原始顺序返回")
            return [
                {'content': doc, 'score': 0.0, 'index': i}
                for i, doc in enumerate(documents)
            ][:top_k or len(documents)]
    
    def _compute_similarity_scores(self, query, documents):
        """计算查询与文档的相似度分数
        
        使用 Cross-Encoder 方式计算语义相似度
        
        Args:
            query: 查询文本
            documents: 文档列表
            
        Returns:
            list: 相似度分数列表
        """
        scores = []
        
        for doc in documents:
            try:
                # 构建提示词，让模型评估相关性。
                # 文档截断到 500 字，避免重排阶段耗时过长。
                prompt = f"""请评估以下查询与文档的相关性，返回 0-1 之间的分数：
                
查询：{query}

文档：{doc[:500]}  # 限制文档长度

请直接返回一个 0-1 之间的数字，表示相关性程度（1 表示完全相关，0 表示完全不相关）。"""

                # 调用 Ollama API
                response = requests.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.model_name,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,  # 低温度以获得确定性输出
                            "num_predict": 10
                        }
                    },
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    score_text = result.get('response', '').strip()
                    
                    # 解析分数：模型可能返回“0.8”或带解释的文本，因此用正则抽取数字。
                    try:
                        # 提取数字
                        import re
                        numbers = re.findall(r'\d+\.?\d*', score_text)
                        if numbers:
                            score = float(numbers[0])
                            # 确保在 0-1 范围内
                            if score > 1:
                                score = score / 10.0
                            score = max(0.0, min(1.0, score))
                        else:
                            score = 0.5  # 默认分数
                    except:
                        score = 0.5
                else:
                    score = 0.5
                    
                scores.append(score)
                
            except Exception as e:
                print(f"计算相似度分数失败：{str(e)}")
                scores.append(0.5)
        
        return scores
    
    def rerank_with_metadata(self, query, search_results, top_k=None):
        """对带元数据的搜索结果进行重排
        
        Args:
            query: 用户查询
            search_results: 搜索结果（包含 documents, metadatas, distances）
            top_k: 返回前 K 个结果
            
        Returns:
            dict: 重排后的搜索结果
        """
        if not search_results or not search_results.get('documents'):
            return search_results
        
        # 提取文档内容
        documents = search_results['documents'][0]
        
        # 重排
        reranked_results = self.rerank(query, documents, top_k)
        
        # 构建重排后的结果
        reranked_documents = []
        reranked_metadatas = []
        reranked_distances = []
        reranked_ids = []
        
        for result in reranked_results:
            # 找到原始索引，用它同步取回文档、元数据、距离和 ID。
            original_index = result['index']
            
            reranked_documents.append(documents[original_index])
            
            if search_results.get('metadatas'):
                reranked_metadatas.append(search_results['metadatas'][0][original_index])
            else:
                reranked_metadatas.append({})
            
            if search_results.get('distances'):
                reranked_distances.append(search_results['distances'][0][original_index])
            else:
                reranked_distances.append(0.0)
            
            if search_results.get('ids'):
                reranked_ids.append(search_results['ids'][0][original_index])
            else:
                reranked_ids.append(f"doc_{original_index}")
        
        return {
            'documents': [reranked_documents],
            'metadatas': [reranked_metadatas],
            'distances': [reranked_distances],
            'ids': [reranked_ids],
            'rerank_scores': [r['score'] for r in reranked_results]
        }
