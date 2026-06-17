"""Local Ollama AI question-answering service."""
import json
import requests


class AIService:
    """AI service backed only by the local Ollama runtime."""

    _initialized = False

    def __init__(self):
        self.ollama_config = {
            'base_url': 'http://localhost:11434',
            'model': 'qwen2.5:7b',
            'enabled': True
        }

    def init_config(self):
        """Load Ollama settings from Flask config."""
        from flask import current_app

        self.ollama_config['base_url'] = current_app.config.get('OLLAMA_BASE_URL', 'http://localhost:11434')
        self.ollama_config['model'] = current_app.config.get('OLLAMA_MODEL', 'qwen2.5:7b')
        self.ollama_config['enabled'] = current_app.config.get('USE_OLLAMA', True)
        AIService._initialized = True

    def ask_question(self, question, user=None, user_id=None, context=None, model_type=None):
        """Return one complete answer from local Ollama."""
        self.init_config()

        system_prompt = """你是一个学习助手，名为"智学伴"。请回答学生的问题，回答要准确、简洁、有条理，适当使用代码示例或列表。"""
        user_prompt = f"""学生问题：{question}

请回答："""

        original_model = self.ollama_config['model']
        if user and user.ollama_model:
            self.ollama_config['model'] = user.ollama_model

        try:
            answer = self._call_ollama(system_prompt, user_prompt)
            model_used = f"Ollama ({self.ollama_config['model']})"
        finally:
            self.ollama_config['model'] = original_model

        return {
            'answer': answer,
            'sources': [],
            'model_used': model_used
        }

    def stream_question(self, question, user=None, user_id=None, context=None, model_type=None):
        """Stream an answer from local Ollama."""
        self.init_config()

        system_prompt = """你是一个学习助手，名为"智学伴"。请回答学生的问题，回答要准确、简洁、有条理，适当使用代码示例或列表。"""
        user_prompt = f"""学生问题：{question}

请回答："""

        original_model = self.ollama_config['model']
        if user and user.ollama_model:
            self.ollama_config['model'] = user.ollama_model
        model_used = f"Ollama ({self.ollama_config['model']})"

        def generate():
            try:
                yield from self._stream_ollama(system_prompt, user_prompt)
            finally:
                self.ollama_config['model'] = original_model

        return generate(), model_used

    def _call_ollama(self, system_prompt, user_prompt):
        """Call Ollama `/api/chat` without streaming."""
        if not self.ollama_config['enabled']:
            return "本地 Ollama 未启用，请检查 USE_OLLAMA 配置"

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
            response = requests.post(
                url,
                json=data,
                timeout=120,
                proxies={"http": None, "https": None}
            )

            if response.status_code == 200:
                result = response.json()
                return result['message']['content']
            return f"Ollama 调用失败: {response.status_code}"

        except requests.exceptions.ConnectionError:
            return "无法连接到 Ollama，请检查本地 Ollama 服务是否正常运行"
        except Exception as e:
            return f"Ollama 调用失败: {str(e)}"

    def _stream_ollama(self, system_prompt, user_prompt):
        """Stream from Ollama `/api/chat`."""
        if not self.ollama_config['enabled']:
            yield "本地 Ollama 未启用，请检查 USE_OLLAMA 配置"
            return

        try:
            url = f"{self.ollama_config['base_url']}/api/chat"
            data = {
                "model": self.ollama_config['model'],
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "stream": True,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 1000
                }
            }
            response = requests.post(
                url,
                json=data,
                timeout=120,
                proxies={"http": None, "https": None},
                stream=True
            )

            if response.status_code != 200:
                yield f"Ollama 调用失败: {response.status_code}"
                return

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                event = json.loads(line)
                content = event.get('message', {}).get('content')
                if content:
                    yield content
                if event.get('done'):
                    break
        except requests.exceptions.ConnectionError:
            yield "无法连接到 Ollama，请检查本地 Ollama 服务是否正常运行"
        except Exception as e:
            yield f"Ollama 调用失败: {str(e)}"
