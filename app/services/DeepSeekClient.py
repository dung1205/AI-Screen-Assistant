"""
DeepSeekClient.py
------------------
Gọi DeepSeek API — họ dùng OpenAI-compatible format, nên dùng thẳng
`openai` SDK với `base_url="https://api.deepseek.com"` thay vì SDK riêng.

4 MODEL PHỔ BIẾN NHẤT HIỆN TẠI (09/2026):
- deepseek-v4-pro    : flagship, mạnh nhất, tốn token hơn
- deepseek-v4-flash  : nhanh, rẻ, phù hợp chat thông thường
- deepseek-v4-flash-reasoner: bản reasoning của v4-flash, suy luận sâu hơn
- deepseek-v3.1      : bản cũ hơn nhưng vẫn rất tốt, nhiều người dùng
"""

from __future__ import annotations

from typing import Iterator

import openai


DEEPSEEK_BASE_URL = "https://api.deepseek.com"

DEEPSEEK_MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-reasoner",
    "deepseek-v3.1",
]

DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


class DeepSeekClientError(Exception):
    pass

class DeepSeekAuthError(DeepSeekClientError):
    pass

class DeepSeekQuotaExceededError(DeepSeekClientError):
    pass

class DeepSeekServerError(DeepSeekClientError):
    pass


class DeepSeekClient:

    def list_models(self) -> list[str]:
        return DEEPSEEK_MODELS

    def stream_message(
        self,
        api_key: str,
        model: str,
        history: list,
        user_message: str,
        image_bytes: bytes | None = None,
    ) -> Iterator[str]:
        # DeepSeek chưa hỗ trợ ảnh qua API chính thức -> bỏ qua image_bytes
        client = openai.OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

        messages = [
            {"role": turn.role if turn.role != "model" else "assistant",
             "content": turn.text}
            for turn in history
        ]
        messages.append({"role": "user", "content": user_message})

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except openai.AuthenticationError as exc:
            raise DeepSeekAuthError("API key DeepSeek không hợp lệ.") from exc
        except openai.RateLimitError as exc:
            raise DeepSeekQuotaExceededError("Hết quota DeepSeek.") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise DeepSeekServerError(f"DeepSeek server lỗi: {exc}") from exc
            raise DeepSeekClientError(f"DeepSeek báo lỗi: {exc}") from exc