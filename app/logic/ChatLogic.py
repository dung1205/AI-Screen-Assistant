"""
ChatLogic.py
-------------
"Bộ não" của app: nối `KeyStorage`, `ChatDB`, `GeminiClient`,
`ScreenshotService`, `ImageService` lại thành 1 luồng nghiệp vụ hoàn
chỉnh (đúng sơ đồ đã thống nhất: InputBar -> AppController -> ChatLogic
-> các service -> ChatArea).

NGUYÊN TẮC QUAN TRỌNG NHẤT CỦA FILE NÀY: KHÔNG BIẾT GÌ VỀ PyQt6.
- `ChatLogic` chỉ nhận/trả về kiểu dữ liệu thuần Python (str, list,
  callback function) — KHÔNG import `PyQt6`, không biết `QWidget` là gì.
- Lý do: nếu sau này đổi UI framework (vd sang Flet, hoặc thậm chí làm
  bản web), toàn bộ nghiệp vụ trong file này vẫn dùng lại được nguyên
  vẹn, chỉ cần viết lại tầng `views/` gọi vào nó theo cách khác.
- `ChatLogic` cũng không tự quản lý luồng (thread) — việc chạy hàm này
  trong background thread (để không đứng hình UI khi đợi Gemini trả
  lời) là trách nhiệm của `AppController`/`views` (PyQt6 có
  `QThread`/`QRunnable` riêng cho việc đó).

TẠI SAO DÙNG CALLBACK `on_chunk` THAY VÌ TỰ LÀM GENERATOR (`yield`)
Ở TẦNG NÀY?
- `GeminiClient.stream_message()` đã là generator (`yield` từng đoạn
  chữ). `ChatLogic` có 2 việc phải làm với MỖI đoạn chữ đó:
    1. Gộp lại thành câu trả lời đầy đủ để lưu vào `ChatDB` sau khi
       stream xong.
    2. Đẩy ngay đoạn chữ đó ra UI để vẽ hiệu ứng gõ chữ.
  Nếu `ChatLogic.send_message()` cũng chỉ là 1 generator thuần và bắt
  UI tự vòng `for chunk in ...`, thì UI code phải tự lo luôn việc gộp
  chuỗi để biết khi nào lưu DB — trộn lẫn trách nhiệm hiển thị và lưu
  trữ vào chung 1 chỗ.
  Dùng callback `on_chunk(text)`: `ChatLogic` vừa gọi callback (để UI
  vẽ ngay) vừa tự gộp chuỗi nội bộ (để biết khi nào lưu DB) — 2 việc
  tách bạch, mỗi tầng lo đúng phần việc của mình.

THỨ TỰ LƯU DB QUAN TRỌNG: LƯU CÂU HỎI TRƯỚC, LƯU CÂU TRẢ LỜI SAU KHI
STREAM XONG THÀNH CÔNG.
- Câu hỏi của user được lưu vào `ChatDB` NGAY LẬP TỨC, trước khi gọi
  Gemini. Vì vậy nếu Gemini lỗi (hết quota, mất mạng...) giữa chừng,
  câu hỏi của user vẫn còn trong lịch sử — user không bị "mất câu hỏi
  vừa gõ" chỉ vì Gemini lỗi.
- Ngược lại, câu trả lời của AI CHỈ được lưu sau khi stream hoàn tất
  KHÔNG lỗi. Nếu lỗi giữa chừng, phần chữ đã nhận được (dở dang, có thể
  cụt câu) sẽ KHÔNG được lưu vào lịch sử — tránh lưu 1 câu trả lời sai/
  không đầy đủ mà lần sau gửi lên Gemini làm context lại bị hiểu nhầm
  là "đây là câu trả lời hoàn chỉnh trước đó".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.services.ChatDB import ChatDB, Message
from app.services.GeminiClient import (
    DEFAULT_MODEL,
    ChatTurn,
    GeminiClient,
    GeminiClientError,
)
from app.services.ImageService import ImageService
from app.services.KeyStorage import KeyStorage, Provider
from app.services.ScreenshotService import ScreenshotService
from app.services.DeepSeekClient import DeepSeekClient, DEFAULT_DEEPSEEK_MODEL

CONTEXT_TURNS = 3


class ChatLogicError(Exception):
    pass


class MissingApiKeyError(ChatLogicError):
    def __init__(self, provider: Provider):
        self.provider = provider
        super().__init__(
            f"Chưa có API key cho {provider.value}. Vui lòng vào Settings để nhập key."
        )


@dataclass(frozen=True)
class SendMessageResult:
    reply_text: str
    used_screenshot: bool


class ChatLogic:

    def __init__(
        self,
        chat_db: ChatDB,
        key_storage: KeyStorage,
        gemini_client: GeminiClient,
        screenshot_service: ScreenshotService,
        image_service: ImageService,
        deepseek_client=None,
    ):
        self._chat_db = chat_db
        self._key_storage = key_storage
        self._gemini_client = gemini_client
        self._screenshot_service = screenshot_service
        self._image_service = image_service
        self._deepseek_client = deepseek_client or DeepSeekClient()

    def send_message(
        self,
        conversation_id: int,
        user_message: str,
        include_screenshot: bool = False,
        provider: Provider = Provider.GEMINI,
        model: str | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> SendMessageResult:

        # Chụp + nén ảnh nếu cần (chỉ Gemini hỗ trợ ảnh)
        image_bytes = None
        if include_screenshot and provider == Provider.GEMINI:
            raw_screenshot = self._screenshot_service.capture_full_screen()
            image_bytes = self._image_service.compress_screenshot(raw_screenshot)

        # Lấy context TRƯỚC khi lưu câu hỏi hiện tại
        recent_messages = self._chat_db.get_recent_turns(
            conversation_id, n_pairs=CONTEXT_TURNS
        )
        history = self._to_chat_turns(recent_messages)

        # Lưu câu hỏi của user vào DB
        self._chat_db.add_message(
            conversation_id, "user", user_message, has_image=image_bytes is not None
        )

        if provider == Provider.GEMINI:
            api_key = self._key_storage.get_key(provider)
            if api_key is None:
                raise MissingApiKeyError(provider)

            resolved_model = model or DEFAULT_MODEL
            full_reply = ""
            try:
                for chunk in self._gemini_client.stream_message(
                    api_key=api_key,
                    model=resolved_model,
                    history=history,
                    user_message=user_message,
                    image_bytes=image_bytes,
                ):
                    full_reply += chunk
                    if on_chunk is not None:
                        on_chunk(chunk)
            except GeminiClientError:
                raise

            self._chat_db.add_message(conversation_id, "model", full_reply, has_image=False)
            return SendMessageResult(reply_text=full_reply, used_screenshot=image_bytes is not None)

        elif provider == Provider.DEEPSEEK:
            api_key = self._key_storage.get_key(provider)
            if api_key is None:
                raise MissingApiKeyError(provider)

            resolved_model = model or DEFAULT_DEEPSEEK_MODEL
            full_reply = ""
            try:
                for chunk in self._deepseek_client.stream_message(
                    api_key=api_key,
                    model=resolved_model,
                    history=history,
                    user_message=user_message,
                ):
                    full_reply += chunk
                    if on_chunk is not None:
                        on_chunk(chunk)
            except Exception:
                raise

            self._chat_db.add_message(conversation_id, "model", full_reply, has_image=False)
            return SendMessageResult(reply_text=full_reply, used_screenshot=False)

        else:
            raise NotImplementedError(
                f"Provider {provider.value} chưa được hỗ trợ trong ChatLogic."
            )

    def list_available_models(self, provider: Provider = Provider.GEMINI) -> list[str]:
        if provider == Provider.DEEPSEEK:
            return self._deepseek_client.list_models()

        if provider is not Provider.GEMINI:
            raise NotImplementedError(
                f"Provider {provider.value} chưa được hỗ trợ trong ChatLogic."
            )

        api_key = self._key_storage.get_key(provider)
        if api_key is None:
            raise MissingApiKeyError(provider)

        return self._gemini_client.list_models(api_key)

    @staticmethod
    def _to_chat_turns(messages: list[Message]) -> list[ChatTurn]:
        return [ChatTurn(role=m.role, text=m.text) for m in messages]