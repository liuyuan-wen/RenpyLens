# -*- coding: utf-8 -*-
"""翻译引擎封装 - 支持 Gemini、智谱AI、Ollama（本地/远程）"""

import re
import time
import httpx
import threading


class KeyExpiredError(Exception):
    """试用 Key 已过期"""
    pass


class BaseTranslator:
    """翻译器基类"""

    def __init__(self, config: dict):
        self.config = config
        self.source_lang = config.get("source_lang", "English")
        self.target_lang = config.get("target_lang", "Chinese")
        self.system_prompt = config.get("system_prompt", "You are a professional game dialogue translator. Translate the user's message into {target_lang}. Keep it natural and concise for a visual novel. Output ONLY the translated text. No numbering, no quotes, no explanations.")
        self.batch_prompt = config.get("batch_prompt", "You are a professional game dialogue translator. Translate ALL numbered dialogues into {target_lang}. Keep translations natural and concise. Output ONLY translations in the same numbered format [1]...[2]... No extra text.")
        self.temperature = float(config.get("temperature", 0.3))
        self._last_call_time = 0
        self._timing_enabled = config.get("enable_timing_log", False)
        self._keep_names = config.get("keep_original_names", True)
        self._name_instruction = "\nRule:\nALL character names MUST remain in {source_lang} exactly as they appear in the original text. (e.g. Eileen -> Eileen, Kaelen -> Kaelen)."
        # 线程锁与延迟初始化的客户端
        self._client = None
        self._client_lock = threading.Lock()
        # 最近一次 API 调用的计时数据（供上层读取）
        self.last_timing = {}

    def _rate_limit(self, min_interval=0.5):
        """简单速率限制"""
        now = time.time()
        elapsed = now - self._last_call_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call_time = time.time()

    def _clean_result(self, text: str) -> str:
        """清理 LLM 输出中的编号前缀"""
        text = text.strip()
        text = re.sub(r'^\d+[.)\-]\s*', '', text)
        return text.strip('"\'')

    def _get_client(self) -> httpx.Client:
        """延迟获取/创建客户端"""
        with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = self._create_client()
            return self._client

    def _create_client(self) -> httpx.Client:
        """由子类实现具体的客户端创建逻辑"""
        raise NotImplementedError

    def _call_api(self, system_prompt: str, user_content: str) -> str:
        """子类必须实现此 API 调用逻辑"""
        raise NotImplementedError

    def translate(self, text: str, source_lang=None, target_lang=None) -> str:
        tl = target_lang or self.target_lang
        sl = source_lang or self.source_lang
        system_prompt = self.system_prompt.format(target_lang=tl)
        if self._keep_names:
            system_prompt += self._name_instruction.format(target_lang=tl, source_lang=sl)
        result = self._call_api(system_prompt, text)
        return self._clean_result(result)

    def translate_batch(self, texts: list, source_lang=None, target_lang=None) -> list:
        if len(texts) == 1:
            return [self.translate(texts[0], source_lang=source_lang, target_lang=target_lang)]
        tl = target_lang or self.target_lang
        sl = source_lang or self.source_lang
        numbered = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(texts))
        system_prompt = self.batch_prompt.format(target_lang=tl)
        if self._keep_names:
            system_prompt += self._name_instruction.format(target_lang=tl, source_lang=sl)
        
        max_retries = 3
        for attempt in range(max_retries):
            result_text = self._call_api(system_prompt, numbered)
            try:
                # 尝试严格解析
                return self._parse_batch(result_text, len(texts), strict=True)
            except ValueError as e:
                # 格式错误，重试
                if attempt < max_retries - 1:
                    print(f"[Translator] Batch parse failed ({e}), retrying {attempt+1}/{max_retries}...")
                    continue
                # 重试机会耗尽，启用降级解析
                print(f"[Translator] Batch parse final failure, fallback parsing enabled ({e})")
                return self._parse_batch(result_text, len(texts), strict=False)
        return []

    def _parse_batch(self, result_text: str, expected_count: int, strict: bool = False) -> list:
        """解析编号格式的批量翻译结果"""
        matches = re.findall(r'\[(\d+)\]\s*(.+?)(?=\[\d+\]|$)', result_text, re.DOTALL)
        if len(matches) >= expected_count:
            results = {}
            for num, text in matches:
                results[int(num)] = self._clean_result(text)
            return [results.get(i+1, "") for i in range(expected_count)]
        
        if strict:
            raise ValueError(f"Expected {expected_count} items, parsed {len(matches)}")

        # 降级：按行分割
        lines = [l.strip() for l in result_text.strip().split('\n') if l.strip()]
        while len(lines) < expected_count:
            lines.append("")
        return [self._clean_result(l) for l in lines[:expected_count]]

    def close(self):
        """关闭连接池，释放 TCP 连接"""
        with self._client_lock:
            if self._client and not self._client.is_closed:
                self._client.close()
                print(f"[Translator] Connection pool closed")


class GeminiTranslator(BaseTranslator):
    """Gemini API 翻译"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get("gemini_api_key", "")
        self.model = config.get("gemini_model", "gemini-2.5-flash-lite")
        self.base_url = config.get("gemini_url", "https://generativelanguage.googleapis.com").rstrip('/')
        # 兼容旧版本或直接提供完整端点的情况
        if "/v1beta/models/" not in self.base_url:
            self.api_url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        else:
            self.api_url = self.base_url
        self.max_retries = 3
    def _create_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=30,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=300,
            ),
        )

    def _call_api(self, system_prompt: str, user_content: str) -> str:
        """调用 Gemini API"""
        client = self._get_client()
        prompt = system_prompt + "\n\n" + user_content
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature}
        }
        for attempt in range(self.max_retries):
            try:
                self._rate_limit()
                sep = "&" if "?" in self.api_url else "?"
                url = f"{self.api_url}{sep}key={self.api_key}"
                resp = client.post(url, json=payload)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[Gemini] 429 rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                print(f"[Gemini] Retry {attempt + 1}/{self.max_retries}: {e}")
                time.sleep(1)
        raise RuntimeError("Gemini API max retries exhausted")

    # Translation logic inherited from BaseTranslator

    def close(self):
        """关闭连接池"""
        super().close()


class ZhipuTranslator(BaseTranslator):
    """智谱AI (GLM) 翻译"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get("zhipu_api_key", "")
        self.model = config.get("zhipu_model", "glm-4.7-flash")
        self.base_url = config.get("zhipu_url", "https://open.bigmodel.cn").rstrip('/')
        if "/v4/chat/completions" not in self.base_url:
            self.api_url = f"{self.base_url}/api/paas/v4/chat/completions"
        else:
            self.api_url = self.base_url
        self.max_retries = 3
    def _create_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=30,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=300,
            ),
        )

    def _call_api(self, system_prompt: str, user_content: str) -> str:
        """调用智谱 API"""
        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
        }
        for attempt in range(self.max_retries):
            try:
                self._rate_limit()
                resp = client.post(self.api_url, json=payload, headers=headers)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[Zhipu] 429 rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                print(f"[Zhipu] Retry {attempt + 1}/{self.max_retries}: {e}")
                time.sleep(1)
        raise RuntimeError("Zhipu API max retries exhausted")

    # Translation logic inherited from BaseTranslator

    def close(self):
        """关闭连接池"""
        super().close()


class OllamaTranslator(BaseTranslator):
    """Ollama 本地/远程 LLM 翻译（OpenAI 兼容接口）"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = config.get("ollama_url", "http://localhost:11434")
        self.model = config.get("ollama_model", "gemma3:4b")
        self.api_url = self.base_url.rstrip('/') + "/v1/chat/completions"
        self.max_retries = 3
    def _create_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=120,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=300,
            ),
        )

    def warmup(self):
        """预加载模型到 GPU 显存，避免首次翻译时冷启动"""
        client = self._get_client()
        try:
            warmup_url = self.base_url.rstrip('/') + "/api/generate"
            payload = {
                "model": self.model,
                "prompt": "Hello",
                "keep_alive": "30m",
                "stream": False,
            }
            print(f"[Ollama] Model {self.model} warming up...")
            resp = client.post(warmup_url, json=payload)
            print(f"[Ollama] Warmup response: {resp.status_code}")
        except Exception as e:
            print(f"[Ollama] Warmup failed (can be ignored): {e}")

    def _call_api(self, system_prompt: str, user_content: str) -> str:
        """调用 Ollama OpenAI 兼容 API"""
        client = self._get_client()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "stream": False,
        }
        for attempt in range(self.max_retries):
            try:
                self._rate_limit(0.1)
                resp = client.post(self.api_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except httpx.ConnectError:
                raise ConnectionError(
                    "Cannot connect to Ollama service. Please verify:\n"
                    "1. Ollama is running on the target machine\n"
                    "2. The API URL is correctly configured"
                )
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                print(f"[Ollama] Retry {attempt + 1}/{self.max_retries}: {e}")
                time.sleep(1)
        raise RuntimeError("Ollama max retries exhausted")

    # Translation logic inherited from BaseTranslator

    def close(self):
        """关闭连接池"""
        super().close()


class BuiltinTranslator(BaseTranslator):
    """内置通道推理框架（OpenAI 兼容接口，支持 continuous batching）"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = config.get("builtin_url", "http://localhost:8000")
        
        # 兼容旧配置：如果保存成了显示名，映射回真实模型名
        raw_model = config.get("builtin_model", "Qwen2.5-7B-Instruct")
        if raw_model == "模型1":
            raw_model = "Qwen3-8B-FP8"
        self.model = raw_model
        
        self.api_key = config.get("builtin_api_key", "")
        # 如果 URL 已经以 /v1 结尾，则直接拼 /chat/completions，避免重复
        base = self.base_url.rstrip('/')
        if base.endswith('/v1'):
            self.api_url = base + "/chat/completions"
        else:
            self.api_url = base + "/v1/chat/completions"
        self.max_retries = 3
        # models 端点 URL（用于预热连接）
        self._models_url = self.api_url.replace("/chat/completions", "/models")
    def _create_client(self) -> httpx.Client:
        """创建 httpx 客户端（支持关闭后重建）"""
        return httpx.Client(
            timeout=120,
            verify=False,  # 忽略 SSL 证书校验（SakuraFRP 等自签名证书）
            trust_env=False,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=300,
            ),
        )

    def warmup(self):
        """预热连接：提前建立 TCP + TLS 连接，避免首次翻译时的握手延迟"""
        print(f"[Builtin] Using model: {self.model}")
        client = self._get_client()
        try:
            t0 = time.perf_counter()
            headers = self._build_headers()
            resp = client.get(self._models_url, headers=headers)
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[Builtin] Connection warmup done: {elapsed:.0f}ms (TCP+TLS handshake established, status={resp.status_code})")
        except Exception as e:
            print(f"[Builtin] Connection warmup failed (will retry on first translation): {e}")

    def _build_headers(self):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"
        return headers

    def _build_payload(self, system_prompt: str, user_content: str, stream: bool = False):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "stream": stream,
        }
        if "qwen3" in self.model.lower() or "Qwen3" in self.model:
            # 兼容 OpenAI 的 extra_body，在 json 里直接放在顶层
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def _call_api(self, system_prompt: str, user_content: str) -> str:
        """调用内置通道 OpenAI 兼容 API"""
        client = self._get_client()
        headers = self._build_headers()
        payload = self._build_payload(system_prompt, user_content, stream=False)

        for attempt in range(self.max_retries):
            try:
                self._rate_limit(0.1)
                t_api_start = time.perf_counter()
                resp = client.post(self.api_url, json=payload, headers=headers)
                t_api_end = time.perf_counter()
                if resp.status_code == 403:
                    raise PermissionError("[Builtin] 403 Forbidden. Please check API Key or server allowed origins.")
                if resp.status_code == 400:
                    try:
                        err_data = resp.json().get("error", {})
                        if err_data.get("type") == "expired_key":
                            raise KeyExpiredError("试用 API Key 已到期，请联系微信 renpytrans 获取正式授权。")
                    except KeyExpiredError:
                        raise
                    except Exception:
                        pass
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                if "<think>" in content:
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
                content = content.strip()

                # 记录 usage 信息和计时
                usage = data.get("usage", {})
                api_ms = (t_api_end - t_api_start) * 1000
                self.last_timing = {
                    "api_ms": api_ms,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                if self._timing_enabled:
                    comp_tokens = usage.get("completion_tokens", 0)
                    tps = comp_tokens / (api_ms / 1000) if api_ms > 0 else 0
                    print(f"[Timing][Builtin] API latency: {api_ms:.0f}ms | "
                          f"prompt_tokens={usage.get('prompt_tokens', '?')} "
                          f"completion_tokens={comp_tokens} | "
                          f"throughput(incl. network): {tps:.1f} tok/s")
                return content
            except httpx.ConnectError:
                raise ConnectionError(
                    "Cannot connect to Builtin channel service. Please verify:\n"
                    "1. Builtin channel server is running\n"
                    "2. API URL is correctly configured\n"
                    "3. NAT traversal / tunnel service is running normally"
                )
            except PermissionError:
                raise
            except KeyExpiredError:
                raise
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Builtin channel max retries exhausted: {e}\n\nPlease check server logs or configuration."
                    )
                print(f"[Builtin] Retry {attempt + 1}/{self.max_retries}: {e}")
                time.sleep(1)
        raise RuntimeError("Builtin channel max retries exhausted")

    def call_api_streaming(self, system_prompt: str, user_content: str):
        """流式调用内置通道 API，返回详细计时数据
        Returns: (content, timing_dict)
        timing_dict 包含: ttft_ms, generation_ms, total_ms, tokens, tokens_per_sec,
                         chunk_timestamps
        """
        import json as _json
        headers = self._build_headers()
        payload = self._build_payload(system_prompt, user_content, stream=True)
        # 请求 usage 信息
        payload["stream_options"] = {"include_usage": True}

        t0 = time.perf_counter()
        chunks_content = []
        chunk_timestamps = []  # (timestamp_ms, token_text)
        t_first_token = None
        usage_info = {}

        client = self._get_client()
        with client.stream("POST", self.api_url, json=payload,
                                headers=headers) as resp:
            if resp.status_code == 403:
                raise PermissionError("[Builtin] 403 Forbidden.")
            resp.raise_for_status()
            t_headers = time.perf_counter()  # HTTP 响应头到达

            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]  # 去掉 "data: "
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = _json.loads(data_str)
                except Exception:
                    continue

                # 提取 usage（内置通道会在最后一个 chunk 中包含 usage）
                if "usage" in chunk and chunk["usage"]:
                    usage_info = chunk["usage"]

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                token_text = delta.get("content", "")
                if token_text:
                    t_now = time.perf_counter()
                    if t_first_token is None:
                        t_first_token = t_now
                    chunks_content.append(token_text)
                    chunk_timestamps.append(((t_now - t0) * 1000, token_text))

        t_end = time.perf_counter()
        content = "".join(chunks_content)
        if "<think>" in content:
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        content = content.strip()

        total_ms = (t_end - t0) * 1000
        ttft_ms = (t_first_token - t0) * 1000 if t_first_token else total_ms
        headers_ms = (t_headers - t0) * 1000
        generation_ms = (t_end - t_first_token) * 1000 if t_first_token else 0
        n_tokens = len(chunks_content)
        tps = n_tokens / (generation_ms / 1000) if generation_ms > 0 else 0

        timing = {
            "total_ms": total_ms,
            "headers_ms": headers_ms,
            "ttft_ms": ttft_ms,
            "generation_ms": generation_ms,
            "tokens": n_tokens,
            "tokens_per_sec": tps,
            "chunk_timestamps": chunk_timestamps,
            "prompt_tokens": usage_info.get("prompt_tokens", 0),
            "completion_tokens": usage_info.get("completion_tokens", 0),
        }
        return content, timing

    # Translation logic inherited from BaseTranslator

    def close(self):
        """关闭连接池，释放 TCP 连接"""
        with self._client_lock:
            if self._client and not self._client.is_closed:
                self._client.close()
                print(f"[Builtin] Connection pool closed")


class OpenAICompatibleTranslator(BaseTranslator):
    """通用的 OpenAI 兼容格式 API 翻译器
    (适用于 OpenAI, DeepSeek, 硅基流动, 月之暗面, xAI, 阿里通义, 火山引擎, 自定义等)
    """

    def __init__(self, engine: str, config: dict):
        super().__init__(config)
        self.engine = engine
        self.api_key = config.get(f"{engine}_api_key", "")
        self.model = config.get(f"{engine}_model", "")
        self.base_url = config.get(f"{engine}_url", "").rstrip('/')
        
        # 处理可能的 base_url 未以 /v1 结尾的情况
        if not self.base_url.rstrip('/').endswith('/v1') and "api.openai.com" not in self.base_url:
            # 阿里 / 火山 等特殊后缀不强加 /v1，根据常见情况智能识别
            if "dashscope.aliyuncs.com" in self.base_url or "volces.com" in self.base_url or "api.x.ai" in self.base_url or "api.deepseek.com" in self.base_url or "api.siliconflow.cn" in self.base_url or "api.moonshot.cn" in self.base_url:
                self.api_url = f"{self.base_url}/chat/completions" # 很多直接拼
                if "api.x.ai" in self.base_url or "api.moonshot.cn" in self.base_url or "api.siliconflow.cn" in self.base_url:
                    self.api_url = f"{self.base_url}/v1/chat/completions"
            else:
                 # 默认 openai 系或者自定义加上 v1
                 self.api_url = f"{self.base_url}/v1/chat/completions"
        else:
            self.api_url = f"{self.base_url}/chat/completions"
            
        # 强制修正常见已知端点
        if "api.openai.com" in self.base_url:
             self.api_url = "https://api.openai.com/v1/chat/completions"
        if "api.deepseek.com" in self.base_url:
             self.api_url = "https://api.deepseek.com/chat/completions"

        self.max_retries = 3

    def _create_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=120,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=300,
            ),
        )

    def _call_api(self, system_prompt: str, user_content: str) -> str:
        """调用兼容 OpenAI 的 API"""
        client = self._get_client()
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
        }
        
        # Deepseek-R1 或包含 think 的模型不需要 thinking 开关，会自动包含 <think>
        for attempt in range(self.max_retries):
            try:
                self._rate_limit()
                resp = client.post(self.api_url, json=payload, headers=headers)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[{self.engine.capitalize()}] 429 rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                if "<think>" in content:
                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                return content
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                print(f"[{self.engine.capitalize()}] Retry {attempt + 1}/{self.max_retries}: {e}")
                time.sleep(1)
        raise RuntimeError(f"{self.engine.capitalize()} API max retries exhausted")

    def close(self):
        super().close()


class AnthropicTranslator(BaseTranslator):
    """Anthropic Claude API 翻译器"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get("anthropic_api_key", "")
        self.model = config.get("anthropic_model", "claude-3-5-haiku-20241022")
        self.base_url = config.get("anthropic_url", "https://api.anthropic.com").rstrip('/')
        self.api_url = f"{self.base_url}/v1/messages"
        self.max_retries = 3

    def _create_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=120,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=5,
                max_keepalive_connections=2,
                keepalive_expiry=300,
            ),
        )

    def _call_api(self, system_prompt: str, user_content: str) -> str:
        """调用 Anthropic Messages API"""
        client = self._get_client()
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_content}
            ],
            "temperature": self.temperature,
        }

        for attempt in range(self.max_retries):
            try:
                self._rate_limit()
                resp = client.post(self.api_url, json=payload, headers=headers)
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    print(f"[Anthropic] 429 rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["content"][0]["text"].strip()
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                print(f"[Anthropic] Retry {attempt + 1}/{self.max_retries}: {e}")
                time.sleep(1)
        raise RuntimeError("Anthropic API max retries exhausted")

    def close(self):
        super().close()


def create_translator(engine: str, config: dict) -> BaseTranslator:
    """工厂函数：根据引擎名创建对应的翻译器"""
    if engine == "ollama":
        url = config.get("ollama_url", "http://localhost:11435")
        model = config.get("ollama_model", "gemma3:4b")
        print(f"[Translator] Ollama: {url}, model={model}")
        return OllamaTranslator(config)
    elif engine == "builtin":
        url = config.get("builtin_url", "http://localhost:8000")
        model = config.get("builtin_model", "Qwen2.5-7B-Instruct")
        key = config.get("builtin_api_key", "")
        print(f"[Translator] Builtin: {url}, model={model}, key={'***' if key else '(none)'}")
        return BuiltinTranslator(config)
    elif engine == "zhipu":
        key = config.get("zhipu_api_key", "")
        model = config.get("zhipu_model", "glm-4.7-flash")
        print(f"[Translator] ZhipuAI: model={model}, key={'***' + key[-4:] if len(key) > 4 else '(empty)'}")
        return ZhipuTranslator(config)
    elif engine == "gemini":
        key = config.get("gemini_api_key", "")
        model = config.get("gemini_model", "gemini-2.5-flash-lite")
        print(f"[Translator] Gemini: model={model}, key={'***' + key[-4:] if len(key) > 4 else '(empty)'}")
        return GeminiTranslator(config)
    elif engine == "anthropic":
        key = config.get("anthropic_api_key", "")
        model = config.get("anthropic_model", "claude-3-5-haiku-20241022")
        print(f"[Translator] Anthropic: model={model}, key={'***' + key[-4:] if len(key) > 4 else '(empty)'}")
        return AnthropicTranslator(config)
    elif engine in ["openai", "deepseek", "siliconflow", "moonshot", "xai", "alibaba", "volcengine", "custom"]:
        key = config.get(f"{engine}_api_key", "")
        model = config.get(f"{engine}_model", "")
        url = config.get(f"{engine}_url", "")
        print(f"[Translator] {engine.capitalize()}: url={url}, model={model}, key={'***' + key[-4:] if len(key) > 4 else '(empty)'}")
        return OpenAICompatibleTranslator(engine, config)
    else:
        print(f"[Translator] Unknown engine '{engine}', falling back to Ollama")
        return OllamaTranslator(config)
