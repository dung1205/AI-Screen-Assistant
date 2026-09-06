"""
GeminiClient.py
----------------
Service duy nhất trong app được phép "nói chuyện" trực tiếp với Gemini API.
ChatLogic sẽ gọi vào đây, KHÔNG gọi thẳng thư viện `google-genai` ở nơi khác
-> nếu sau này Google đổi SDK/API, chỉ cần sửa 1 file này.

THƯ VIỆN DÙNG: `google-genai` (import là `from google import genai`)
Đây là SDK chính thức, thay thế cho SDK cũ `google-generativeai` đã deprecated.

KHÁI NIỆM QUAN TRỌNG TRƯỚC KHI ĐỌC CODE:

1) STREAMING là gì và tại sao dùng `yield` (generator)?
   - Bình thường, gọi API xong phải đợi Gemini viết XONG TOÀN BỘ câu trả lời
     rồi mới nhận được 1 cục text -> user nhìn màn hình đứng im vài giây.
   - Streaming: Gemini vừa nghĩ ra chữ nào, gửi ngay chữ đó về, giống hiệu ứng
     "gõ chữ" của ChatGPT/Gemini web.
   - Trong Python, cách tự nhiên để trả về "nhiều giá trị theo thời gian"
     (thay vì 1 giá trị duy nhất) là dùng `yield` thay vì `return`.
     Hàm có `yield` gọi là generator: mỗi lần vòng lặp UI hỏi "còn gì nữa
     không", hàm sẽ chạy tiếp tới `yield` kế tiếp rồi tạm dừng ở đó,
     KHÔNG chạy hết hàm rồi mới trả 1 lần.
   - Nhờ vậy, `ChatArea` (UI) có thể vừa nhận từng đoạn chữ vừa vẽ lên
     màn hình ngay lập tức, tạo hiệu ứng streaming.

2) Tại sao tạo `genai.Client(api_key=...)` MỚI trong mỗi lần gọi,
   thay vì tạo 1 lần rồi dùng mãi?
   - API key của user có thể đổi bất cứ lúc nào (user vào Settings đổi
     key hoặc đổi provider). Nếu giữ 1 client cố định từ đầu, đổi key
     xong vẫn phải "khởi tạo lại" mới nhận key mới.
   - Khởi tạo `Client` chỉ là tạo object Python trong bộ nhớ (không tốn
     network call), nên tạo mới mỗi lần gọi gần như không tốn chi phí gì
     -> đơn giản hơn nhiều so với việc quản lý vòng đời (lifecycle) của
     1 client dùng chung.

3) Lịch sử hội thoại (multi-turn) được Gemini hiểu như thế nào?
   - Gemini không tự nhớ các câu hỏi trước. Mỗi lần gọi API, mình phải
     tự gửi kèm các lượt hỏi-đáp trước đó trong tham số `contents`.
   - `contents` là 1 danh sách các "lượt nói", mỗi lượt có `role`
     ("user" hoặc "model") và `parts` (nội dung: text, hoặc ảnh...).
   - Ở app này, ChatLogic đã quyết định chỉ gửi 3 lượt Q&A gần nhất
     (xem AppController/ChatLogic) để tiết kiệm token, không gửi toàn
     bộ lịch sử.

4) Ảnh (screenshot) được gửi kèm như thế nào?
   - Trong `parts` của lượt nói hiện tại, ngoài phần `text` (câu hỏi),
     mình thêm 1 `Part` chứa dữ liệu ảnh dạng bytes + mime_type
     (vd "image/jpeg"). Gemini đọc được cả 2 phần cùng lúc (multimodal).

5) Phân biệt các loại lỗi để hiển thị đúng thông báo cho user:
   - Lỗi 401/403 (key sai/không có quyền)  -> GeminiAuthError
   - Lỗi 429 (hết quota / vượt rate limit) -> GeminiQuotaExceededError
   - Lỗi 5xx (Google đang gặp sự cố)       -> GeminiServerError
   - Các lỗi khác (mất mạng, timeout...)   -> GeminiClientError (chung)
   Việc tách lỗi giúp UI hiện đúng cảnh báo, thay vì 1 câu chung chung
   "Đã có lỗi xảy ra" (đúng như phần "quota exhaustion -> hiện warning"
   đã thống nhất trước đó).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal

from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError


# Model mặc định khi user chưa chọn gì trong Settings.
# Đây là bản "flash-lite": rẻ và nhanh nhất, phù hợp làm mặc định cho
# 1 app chat thông thường; user vẫn có thể đổi sang model khác qua
# dropdown (xem list_models() bên dưới).
DEFAULT_MODEL = "gemini-3.5-flash-lite"


@dataclass(frozen=True)
class ChatTurn:
    """
    1 lượt nói trong lịch sử hội thoại, dùng để build `contents` gửi
    cho Gemini. `frozen=True` nghĩa là object này không đổi được sau
    khi tạo (immutable) -> tránh bug do vô tình sửa nhầm 1 turn cũ.
    """
    role: Literal["user", "model"]
    text: str


class GeminiClientError(Exception):
    """Lỗi chung khi gọi Gemini API (mất mạng, timeout, lỗi không xác định...)."""


class GeminiAuthError(GeminiClientError):
    """API key sai, bị thu hồi, hoặc không có quyền dùng model này (401/403)."""


class GeminiQuotaExceededError(GeminiClientError):
    """Hết hạn mức miễn phí / vượt rate limit (HTTP 429)."""


class GeminiServerError(GeminiClientError):
    """Lỗi phía Google (HTTP 5xx) — không phải lỗi của user, nên thử lại sau."""


class GeminiClient:
    """
    API công khai mà `ChatLogic` sẽ gọi vào.
    Không lưu state (api_key, model) bên trong object này — mọi thông
    tin cần thiết được truyền vào qua tham số mỗi lần gọi, vì key/model
    có thể đổi giữa các lần gọi (user đổi trong Settings).
    """

    def list_models(self, api_key: str) -> list[str]:
        return [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        ]

    def stream_message(
        self,
        api_key: str,
        model: str,
        history: list[ChatTurn],
        user_message: str,
        image_bytes: bytes | None = None,
        image_mime_type: str = "image/jpeg",
    ) -> Iterator[str]:
        """
        Gửi câu hỏi mới (kèm lịch sử + ảnh optional) và STREAM câu trả lời về
        theo từng đoạn nhỏ, để UI vẽ hiệu ứng gõ chữ.

        Đây là generator (có `yield`), nên cách dùng ở ChatLogic sẽ là:

            full_text = ""
            for chunk in gemini_client.stream_message(...):
                full_text += chunk
                ui_callback(chunk)   # vẽ ngay từng đoạn lên UI
            chat_db.save_message(conversation_id, "model", full_text)

        Args:
            api_key: key của user (lấy từ KeyStorage ở tầng trên).
            model: tên model, vd "gemini-2.5-flash-lite" (từ Settings).
            history: các lượt hỏi-đáp trước đó (ChatLogic đã cắt sẵn
                     còn 3 turn gần nhất trước khi truyền vào đây —
                     GeminiClient không tự cắt, chỉ gửi đúng những gì
                     nhận được).
            user_message: câu hỏi hiện tại của user.
            image_bytes: dữ liệu ảnh đã nén (từ ImageService), hoặc
                         None nếu user không đính kèm ảnh.
            image_mime_type: kiểu ảnh, mặc định "image/jpeg".

        Yields:
            Từng đoạn text (str) khi Gemini sinh ra.

        Raises:
            GeminiAuthError: key sai/hết quyền.
            GeminiQuotaExceededError: hết quota/rate limit.
            GeminiServerError: lỗi phía Google.
            GeminiClientError: lỗi khác (mạng, timeout...).
        """
        client = genai.Client(api_key=api_key)

        contents = self._build_contents(history, user_message, image_bytes, image_mime_type)

        try:
            stream = client.models.generate_content_stream(
                model=model,
                contents=contents,
            )
            for chunk in stream:
                # Một số chunk chỉ chứa metadata (vd finish_reason) mà
                # không có text -> phải check trước khi yield, tránh
                # yield chuỗi None làm UI lỗi khi nối chuỗi.
                if chunk.text:
                    yield chunk.text
        except ClientError as exc:
            raise self._translate_client_error(exc) from exc
        except ServerError as exc:
            raise GeminiServerError(f"Gemini server đang gặp sự cố: {exc}") from exc
        except APIError as exc:
            raise GeminiClientError(f"Lỗi không xác định khi gọi Gemini: {exc}") from exc

    @staticmethod
    def _build_contents(
        history: list[ChatTurn],
        user_message: str,
        image_bytes: bytes | None,
        image_mime_type: str,
    ) -> list[types.Content]:
        """
        Ghép lịch sử + câu hỏi hiện tại thành đúng định dạng `contents`
        mà Gemini API yêu cầu: list các `types.Content(role=..., parts=[...])`.
        """
        contents: list[types.Content] = [
            types.Content(role=turn.role, parts=[types.Part.from_text(text=turn.text)])
            for turn in history
        ]

        # Lượt hiện tại: luôn có text; nếu có ảnh thì thêm 1 Part ảnh nữa
        # trong CÙNG 1 Content (Gemini đọc đồng thời text + ảnh, không
        # tách thành 2 lượt nói riêng).
        current_parts: list[types.Part] = [types.Part.from_text(text=user_message)]
        if image_bytes is not None:
            current_parts.append(
                types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type)
            )

        contents.append(types.Content(role="user", parts=current_parts))
        return contents

    @staticmethod
    def _translate_client_error(exc: ClientError) -> GeminiClientError:
        """
        `google-genai` ném chung 1 loại `ClientError` cho mọi lỗi 4xx,
        kèm `exc.code` là mã HTTP thật (401, 403, 429...). Hàm này "dịch"
        mã đó sang đúng loại lỗi riêng của app để tầng trên xử lý khác
        nhau (vd 429 thì hiện "Hết quota, thử lại sau", còn 401 thì hiện
        "Key không hợp lệ, vào Settings kiểm tra lại").
        """
        code = getattr(exc, "code", None)
        if code in (401, 403):
            return GeminiAuthError(
                "API key không hợp lệ hoặc không có quyền dùng model này."
            )
        if code == 429:
            return GeminiQuotaExceededError(
                "Đã hết hạn mức sử dụng (quota) cho key này. Vui lòng thử lại sau."
            )
        return GeminiClientError(f"Gemini báo lỗi: {exc}")