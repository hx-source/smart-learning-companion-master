"""AI 问答服务。

封装本地 Ollama 和云端 API 的调用细节。上层路由只关心“传入问题、
得到回答”，不需要知道当前用户使用的是本地模型还是 API Key。
"""
import os
import requests
from flask import current_app

class AIService:
    """AI 服务类。

    配置优先级为：用户个人配置 > 请求指定模型类型 > 系统默认配置。
    """
    _initialized = False
    
    def __init__(self):
        # 各模型的默认连接信息；API Key 会在 init_config 或用户配置中补齐。
        self.deepssek_config = {
            'api_key': None,
            'base_url': 'https://api.deepseek.com',
            'model': 'deepseek-chat'
        }
        self.kimi_config = {
            'api_key': None,
            'base_url': 'https://api.moonshot.cn',
            'model': 'kimi-chat'
        }
        self.zhipu_config = {
            'api_key': None,
            'base_url': 'https://open.bigmodel.cn/api/paas/v4',
            'model': 'glm-4'
        }
        self.ollama_config = {
            'base_url': 'http://localhost:11434',
            'model': 'ollama',
            'enabled': False
        }
    
    def init_config(self):
        """从 Flask 配置初始化服务级默认值。"""
        from flask import current_app
        self.deepssek_config['api_key'] = current_app.config.get('DEEPSEEK_API_KEY')
        self.kimi_config['api_key'] = current_app.config.get('KIMI_API_KEY')
        self.zhipu_config['api_key'] = current_app.config.get('ZHIPU_API_KEY')
        self.ollama_config['base_url'] = current_app.config.get('OLLAMA_BASE_URL', 'http://localhost:11434')
        # 使用qwen2.5:7b作为生成模型，而不是使用bge-m3嵌入模型
        self.ollama_config['model'] = 'qwen2.5:7b'
        self.ollama_config['enabled'] = current_app.config.get('USE_OLLAMA', False)
        if not AIService._initialized:
            pass
        AIService._initialized = True
    
    def ask_question(self, question, user=None, user_id=None, context=None, model_type=None):
        """
        智能问答核心方法
        
        Args:
            question: 用户问题
            user: 用户对象（可选）
            user_id: 用户ID（可选）
            context: 上下文（可选）
            model_type: 模型类型 ("ollama" 或 "api")，None则使用默认
        
        Returns:
            dict: {'answer': str, 'sources': list, 'model_used': str}
        """
        self.init_config()
        
        system_prompt = """你是一个学习助手，名为"智学伴"。请回答学生的问题，回答要准确、简洁、有条理，适当使用代码示例或列表。"""
        
        user_prompt = f"""学生问题：{question}

请回答："""
        
        answer = ""
        model_used = "unknown"
        
        # 优先使用用户的配置：支持不同用户分别选择本地模型或自己的 API Key。
        use_ollama = self.ollama_config['enabled']
        deepseek_api_key = self.deepssek_config['api_key']
        
        if user:
            use_ollama = user.use_ollama
            deepseek_api_key = user.deepseek_api_key
            # 可以在这里添加其他模型的API密钥使用
        
        if model_type == "ollama":
            # 路由显式指定本地模型时，覆盖默认/用户配置。
            use_ollama = True
        elif model_type == "api":
            # 路由显式指定云端 API 时，关闭 Ollama 分支。
            use_ollama = False
        
        if use_ollama:
            # 使用用户配置的Ollama模型
            original_model = self.ollama_config['model']
            if user and user.ollama_model:
                self.ollama_config['model'] = user.ollama_model
            
            answer = self._call_ollama(system_prompt, user_prompt)
            model_used = f"Ollama ({self.ollama_config['model']})"
            
            # 恢复原始模型
            self.ollama_config['model'] = original_model
        elif deepseek_api_key and deepseek_api_key != 'your-api-key-here':
            # 使用用户的DeepSeek API密钥
            original_api_key = self.deepssek_config['api_key']
            self.deepssek_config['api_key'] = deepseek_api_key
            
            answer = self._call_deepseek(system_prompt, user_prompt)
            model_used = "DeepSeek API"
            
            # 恢复原始API密钥
            self.deepssek_config['api_key'] = original_api_key
        else:
            answer = "请配置 AI 服务 API 密钥或启用 Ollama 本地模型"
            model_used = "No Configuration"
        
        return {
            'answer': answer,
            'sources': [],
            'model_used': model_used
        }
    
    def _call_ollama(self, system_prompt, user_prompt):
        """调用 Ollama 本地模型。

        使用 `/api/chat`，消息格式与常见 Chat Completions 接近。
        """
        try:
            url = f"{self.ollama_config['base_url']}/api/chat"
            data = {
                "model": self.ollama_config['model'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 1000
                }
            }
            
            # 禁用代理，避免本地 Ollama 请求被系统代理转发导致连接失败。
            proxies = {"http": None, "https": None}
            response = requests.post(url, json=data, timeout=120, proxies=proxies)
            
            if response.status_code == 200:
                result = response.json()
                return result['message']['content']
            else:
                return f"Ollama API 调用失败: {response.status_code}"
            
        except requests.exceptions.ConnectionError as e:
            return f"无法连接到 Ollama，请检查 Ollama 服务是否正常运行"
        except Exception as e:
            return f"Ollama API 调用失败: {str(e)}"
    
    def _call_deepseek(self, system_prompt, user_prompt):
        """调用 DeepSeek API。

        这里直接使用 requests，方便显式清理代理环境变量并控制超时。
        """
        try:
            # 清除所有可能的代理环境变量，减少校园网/本机代理对 API 调用的影响。
            for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 
                        'HTTP', 'HTTPS', 'ALL_PROXY', 'all_proxy', 'NO_PROXY']:
                os.environ.pop(var, None)
                os.environ.pop(var.lower(), None)
            
            url = "https://api.deepseek.com/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.deepssek_config['api_key']}"
            }
            data = {
                "model": self.deepssek_config['model'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1000
            }
            
            # 使用requests直接发送，禁用代理
            response = requests.post(
                url, 
                headers=headers, 
                json=data, 
                timeout=30,
                proxies={"http": None, "https": None}  # 禁用代理
            )
            
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content']
            else:
                print(f"DeepSeek API错误: {response.status_code} - {response.text}")
                return "DeepSeek API 调用失败，请检查 API 密钥是否正确"
            
        except Exception as e:
            print(f"DeepSeek API调用失败: {e}")
            return "DeepSeek API 调用失败"
