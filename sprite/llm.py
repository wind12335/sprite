"""GLM 调用：走智谱的 OpenAI 兼容 /chat/completions 接口，只依赖 requests。"""

import time

import requests


class LLM:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.8,
        thinking: bool = False,
        timeout: int = 120,
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.thinking = thinking
        self.timeout = timeout

    def _post(self, messages: list[dict], max_tokens: int, retries: int) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            # glm-5.3 等模型支持：闲聊场景关闭深度思考，更快更省 token
            "thinking": {"type": "enabled" if self.thinking else "disabled"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_err = None
        for attempt in range(retries + 1):
            try:
                r = requests.post(self.url, headers=headers, json=payload, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503):
                    last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                body = r.json()
                from .logutil import track_usage

                track_usage(self.model, body.get("usage") or {})
                content = body["choices"][0]["message"]["content"].strip()
                return content if content else (body["choices"][0]["message"].get("reasoning_content") or "").strip()[:500]
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"GLM 请求失败（重试 {retries} 次后）: {last_err}")

    def chat(self, messages: list[dict], max_tokens: int = 400, retries: int = 2) -> str:
        return self._post(messages, max_tokens, retries)

    def chat_vision(self, image_urls: list[str], text: str, max_tokens: int = 500) -> str:
        """多模态调用：图片（data URL 或 https）+ 文本 → 文字输出。"""
        content: list[dict] = [{"type": "image_url", "image_url": {"url": u}} for u in image_urls]
        content.append({"type": "text", "text": text})
        return self._post([{"role": "user", "content": content}], max_tokens, retries=1)
