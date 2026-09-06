"""
AppController.py
------------------
File "lắp ráp" cuối cùng: tạo tất cả service (`ChatDB`, `KeyStorage`,
`GeminiClient`, `ScreenshotService`, `ImageService`), tạo `ChatLogic`
từ 5 service đó, tạo tất cả view (`HeaderView`, `SideBarView`,
`ChatArea`, `InputBarView`), rồi NỐI tín hiệu của các view vào đúng
hàm xử lý — đây là nơi DUY NHẤT trong cả app "biết" về cả 2 phía UI
lẫn nghiệp vụ; mọi file khác chỉ biết 1 phía.

TẠI SAO PHẢI CHẠY `ChatLogic.send_message()` TRONG 1 THREAD RIÊNG
(`QThread`), KHÔNG GỌI TRỰC TIẾP TỪ MAIN THREAD?
- PyQt6 (giống mọi GUI framework) chạy trên ĐÚNG 1 thread chính duy
  nhất để vẽ giao diện và xử lý sự kiện (click, gõ phím...). Đây
  chính là "event loop" mà `main.py` khởi động bằng `app.exec()`.
- `ChatLogic.send_message()` gọi tới `GeminiClient`, mà bên trong đó
  là 1 network call THẬT SỰ (gửi HTTP request, đợi Gemini trả lời) —
  việc này có thể mất VÀI GIÂY. Nếu gọi hàm này trực tiếp từ main
  thread, toàn bộ app bị "đứng hình" trong lúc đó — không click được
  gì, không kéo cửa sổ được, hệ điều hành có thể báo "Python is
  not responding" (ĐÚNG bug đã từng gặp lúc đổi model trong Settings
  trước đây — nguyên nhân gốc chính là gọi network call trên main
  thread).
- Giải pháp: chạy `send_message()` trong 1 `QThread` RIÊNG. Main
  thread vẫn rảnh để vẽ UI, xử lý click bình thường trong lúc thread
  kia đang đợi Gemini trả lời.

TẠI SAO KHÔNG GỌI THẲNG `on_chunk` (CALLBACK) ĐỂ CẬP NHẬT UI TỪ TRONG
WORKER THREAD, MÀ PHẢI QUA SIGNAL (`chunk_received`)?
- QUY TẮC BẤT BIẾN của mọi GUI framework: KHÔNG BAO GIỜ được sửa
  widget (setText, thêm bubble...) từ 1 thread khác ngoài main thread
  — làm vậy có thể gây crash ngẫu nhiên, khó debug (widget nội bộ
  dùng cấu trúc dữ liệu không an toàn khi 2 thread cùng đụng vào).
- Cơ chế Signal/Slot của Qt tự động XỬ LÝ đúng việc này: khi 1 signal
  được `emit()` từ thread A nhưng có slot đang lắng nghe thuộc về
  thread B (ở đây là main thread), Qt tự chuyển lời gọi đó thành 1 sự
  kiện xếp vào hàng đợi của thread B (`QueuedConnection`), rồi thread
  B tự xử lý nó ở lượt event loop tiếp theo — AN TOÀN tuyệt đối,
  không cần tự viết mutex/lock gì cả.
- Vì vậy: worker (chạy trong thread phụ) chỉ được phép `emit()` tín
  hiệu; các slot NHẬN tín hiệu đó và thực sự gọi `bubble.append_chunk()`
  luôn nằm trong `AppController` (chạy ở main thread).

TẠI SAO "DỪNG" (STOP) LẠI DÙNG CỜ + RAISE EXCEPTION TỪ TRONG CALLBACK,
KHÔNG "GIẾT" THREAD TRỰC TIẾP?
- Python (và hầu hết ngôn ngữ) KHÔNG cho phép ép buộc dừng ngay lập
  tức 1 thread đang chạy — làm vậy rất nguy hiểm (thread có thể đang
  giữa chừng ghi file/DB, ép dừng giữa chừng gây hỏng dữ liệu).
- Cách AN TOÀN ("cooperative cancellation" — huỷ theo kiểu hợp tác):
  đặt 1 cờ `_stop_requested`; ngay trong `on_chunk` callback (được
  `GeminiClient`/`ChatLogic` gọi liên tục mỗi khi có chunk mới), kiểm
  tra cờ này — nếu True thì chủ động `raise` 1 exception riêng
  (`_GenerationStopped`). Exception này tự bay lên xuyên qua vòng lặp
  `for chunk in ...` bên trong `ChatLogic.send_message()`, khiến hàm
  đó dừng NGAY LẬP TỨC 1 cách sạch sẽ (không lưu câu trả lời dở dang —
  đúng hành vi đã thiết kế cho MỌI trường hợp lỗi giữa chừng, xem
  `ChatLogic`).
"""

from __future__ import annotations

from pathlib import Path
import os

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from app.logic.ChatLogic import ChatLogic, MissingApiKeyError, SendMessageResult
from app.services.ChatDB import ChatDB
from app.services.GeminiClient import (
    DEFAULT_MODEL,
    GeminiAuthError,
    GeminiClient,
    GeminiClientError,
    GeminiQuotaExceededError,
    GeminiServerError,
)
from app.services.ImageService import ImageService
from app.services.KeyStorage import KeyStorage, KeyStorageError, Provider
from app.services.ScreenshotService import ScreenshotService
from app.views.ChatArea import ChatArea
from app.views.HeaderView import HeaderView
from app.views.InputBarView import InputBarView
from app.views.SettingView import SettingsView
from app.views.SideBarView import SideBarView

import sys
from pathlib import Path


def _get_default_db_path() -> Path:
    if getattr(sys, "frozen", False):
        # Khi chạy file .exe
        base_dir = Path(sys.executable).resolve().parent
    else:
        # Khi chạy python main.py
        base_dir = Path(__file__).resolve().parent.parent

    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    return data_dir / "app.db"


DEFAULT_DB_PATH = _get_default_db_path()


class _GenerationStopped(Exception):
    """Tín hiệu nội bộ báo user chủ động bấm Dừng — xem giải thích 'cooperative cancellation' ở đầu file."""


class _SendMessageWorker(QObject):
    """
    Chạy TRONG 1 `QThread` riêng — bọc quanh đúng 1 lần gọi
    `ChatLogic.send_message()`. Tách riêng thành 1 class `QObject` độc
    lập (thay vì viết logic threading thẳng trong `AppController`) để
    có thể test hành vi của nó (chunk/finished/failed) mà không cần
    khởi động 1 `QThread` thật — gọi `worker.run()` trực tiếp trong
    cùng thread lúc test vẫn hoạt động đúng logic.
    """

    chunk_received = pyqtSignal(str)
    finished = pyqtSignal(object)  # SendMessageResult, hoặc None nếu bị dừng giữa chừng
    failed = pyqtSignal(Exception)

    def __init__(
        self,
        chat_logic: ChatLogic,
        conversation_id: int,
        user_message: str,
        include_screenshot: bool,
        model: str,
    ):
        super().__init__()
        self._chat_logic = chat_logic
        self._conversation_id = conversation_id
        self._user_message = user_message
        self._include_screenshot = include_screenshot
        self._model = model
        self._stop_requested = False

    def request_stop(self) -> None:
        """
        Gọi từ MAIN THREAD khi user bấm nút Dừng. Chỉ đặt 1 cờ boolean
        — thao tác đọc/ghi 1 biến `bool` là an toàn giữa các thread
        trong Python (GIL đảm bảo không bị xé lệnh giữa chừng), nên
        KHÔNG cần thêm lock cho riêng việc này.
        """
        self._stop_requested = True

    def run(self) -> None:
        """Hàm thực thi chính — được gọi khi `QThread.started` bắn ra."""

        def on_chunk(chunk: str) -> None:
            if self._stop_requested:
                raise _GenerationStopped()
            self.chunk_received.emit(chunk)

        try:
            result = self._chat_logic.send_message(
                conversation_id=self._conversation_id,
                user_message=self._user_message,
                include_screenshot=self._include_screenshot,
                model=self._model,
                on_chunk=on_chunk,
            )
            self.finished.emit(result)
        except _GenerationStopped:
            self.finished.emit(None)
        except Exception as exc:  # noqa: BLE001 - cố ý bắt rộng để KHÔNG làm crash cả thread nền
            self.failed.emit(exc)


class _ListModelsWorker(QObject):
    """
    Giống hệt tinh thần `_SendMessageWorker` nhưng cho 1 việc đơn giản
    hơn: gọi `ChatLogic.list_available_models()` trong thread nền.
    Đây CŨNG LÀ network call (gọi Gemini API để lấy danh sách model),
    nên áp dụng ĐÚNG quy tắc đã đặt ra cho toàn app: mọi network call
    đều chạy nền, không có ngoại lệ dù chỉ là màn hình Settings.
    """

    finished = pyqtSignal(list)
    failed = pyqtSignal(Exception)

    def __init__(self, chat_logic: ChatLogic, provider: Provider):
        super().__init__()
        self._chat_logic = chat_logic
        self._provider = provider

    def run(self) -> None:
        try:
            models = self._chat_logic.list_available_models(self._provider)
            self.finished.emit(models)
        except Exception as exc:  # noqa: BLE001 - cố ý bắt rộng, không làm crash thread nền
            self.failed.emit(exc)


class AppController(QWidget):
    """
    Cửa sổ chính của app. `main.py` chỉ cần tạo 1 instance class này
    và gọi `.show()`.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Screen Assistant")
        self.resize(960, 720)

        # --- Tầng service + logic (đã code + test ở các bước trước) ---
        self._chat_db = ChatDB(db_path)
        self._key_storage = KeyStorage()
        self._gemini_client = GeminiClient()
        self._screenshot_service = ScreenshotService()
        self._image_service = ImageService()
        self._chat_logic = ChatLogic(
            chat_db=self._chat_db,
            key_storage=self._key_storage,
            gemini_client=self._gemini_client,
            screenshot_service=self._screenshot_service,
            image_service=self._image_service,
        )

        # --- State runtime (không thuộc về service nào, chỉ AppController giữ) ---
        self._current_conversation_id: int | None = None
        self._current_model: str = DEFAULT_MODEL
        self._pending_ai_bubble = None  # bubble AI đang stream dở, None nếu không có
        self._active_thread: QThread | None = None
        self._active_worker: _SendMessageWorker | None = None
        self._models_thread: QThread | None = None
        self._models_worker: _ListModelsWorker | None = None

        # --- Tầng view ---
        self._header = HeaderView()
        self._sidebar = SideBarView()
        self._chat_area = ChatArea()
        self._input_bar = InputBarView()
        # SettingsView tạo lười (lazy) khi user bấm mở Settings lần
        # đầu — không cần thiết phải tồn tại ngay lúc mở app, vì phần
        # lớn thời gian sử dụng user không đụng tới màn hình này.
        self._settings_view: SettingsView | None = None

        self._build_layout()
        self._connect_signals()
        self._refresh_conversation_list()

    def _build_layout(self) -> None:
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._header)
        content_layout.addWidget(self._chat_area, stretch=1)
        content_layout.addWidget(self._input_bar)

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._sidebar)
        root_layout.addLayout(content_layout, stretch=1)

    def _connect_signals(self) -> None:
        self._header.sidebar_toggle_requested.connect(self._toggle_sidebar)
        self._header.model_changed.connect(self._handle_model_changed)
        self._header.settings_requested.connect(self._handle_settings_requested)

        self._sidebar.new_chat_requested.connect(self._handle_new_chat)
        self._sidebar.conversation_selected.connect(self._handle_conversation_selected)
        self._sidebar.conversation_rename_requested.connect(self._handle_rename)
        self._sidebar.conversation_delete_requested.connect(self._handle_delete)
        self._sidebar.search_query_changed.connect(self._handle_search)

        self._input_bar.message_submitted.connect(self._handle_message_submitted)
        self._input_bar.stop_requested.connect(self._handle_stop_requested)

    # ------------------------------------------------------------------
    # Header: sidebar toggle / đổi model / settings
    # ------------------------------------------------------------------
    def _toggle_sidebar(self) -> None:
        self._sidebar.setVisible(not self._sidebar.isVisible())

    def _handle_model_changed(self, model_name: str) -> None:
        # Chỉ đổi state trong bộ nhớ — KHÔNG lưu vào đâu cả ở bước này.
        # (Nếu muốn nhớ lựa chọn qua lần mở app sau, đó là việc của 1
        # bảng settings riêng trong ChatDB/KeyStorage, chưa nằm trong
        # phạm vi AppController.)
        self._current_model = model_name

    def _handle_settings_requested(self) -> None:
        if self._settings_view is None:
            self._settings_view = SettingsView(self)
            self._settings_view.provider_selected.connect(
                self._handle_settings_provider_changed
            )
            self._settings_view.save_key_requested.connect(
                self._handle_settings_save_key
            )
            self._settings_view.delete_key_requested.connect(
                self._handle_settings_delete_key
            )
            self._settings_view.default_model_changed.connect(
                self._handle_model_changed
            )

        # Mỗi lần MỞ Settings, làm mới trạng thái theo đúng provider
        # đang được chọn trong chính SettingsView (không giả định lại
        # từ đầu là Gemini — user có thể đã chọn dở trước đó rồi đóng
        # cửa sổ, mở lại vẫn giữ nguyên lựa chọn cũ).
        self._refresh_settings_key_status(self._settings_view.current_provider())
        self._settings_view.show()
        self._settings_view.raise_()
        self._settings_view.activateWindow()

    def _handle_settings_provider_changed(self, provider_value: str) -> None:
        self._refresh_settings_key_status(provider_value)

    def _refresh_settings_key_status(self, provider_value: str) -> None:
        provider = Provider(provider_value)
        is_configured = self._key_storage.has_key(provider)
        self._settings_view.set_key_configured(is_configured)

        if is_configured:
            self._start_list_models(provider)
        else:
            self._settings_view.set_models([])

    def _handle_settings_save_key(self, provider_value: str, api_key: str) -> None:
        provider = Provider(provider_value)
        try:
            self._key_storage.save_key(provider, api_key)
        except KeyStorageError as exc:
            QMessageBox.warning(self._settings_view, "Không thể lưu key", str(exc))
            return

        self._settings_view.clear_key_input()
        self._settings_view.set_key_configured(True)
        self._start_list_models(provider)

    def _handle_settings_delete_key(self, provider_value: str) -> None:
        provider = Provider(provider_value)
        self._key_storage.delete_key(provider)
        self._settings_view.set_key_configured(False)
        self._settings_view.set_models([])

    def _start_list_models(self, provider: Provider) -> None:
        if self._models_thread is not None:
            # Đã có 1 lần fetch model đang chạy dở -> bỏ qua yêu cầu
            # mới thay vì chồng thêm thread thứ 2 (vd user bấm lưu key
            # liên tiếp rất nhanh) — đơn giản và đủ an toàn cho 1 thao
            # tác không thường xuyên như mở Settings.
            return

        worker = _ListModelsWorker(self._chat_logic, provider)
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_models_fetched)
        worker.failed.connect(self._handle_models_fetch_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._models_thread = thread
        self._models_worker = worker
        thread.start()

    def _handle_models_fetched(self, models: list[str]) -> None:
        self._models_thread = None
        self._models_worker = None
        if self._settings_view is not None:
            current = self._current_model if self._current_model in models else None
            self._settings_view.set_models(models, current=current)
        self._header.set_models(models, current=self._current_model)

    def _handle_models_fetch_failed(self, error: Exception) -> None:
        self._models_thread = None
        self._models_worker = None
        if self._settings_view is not None:
            QMessageBox.warning(
                self._settings_view,
                "Không lấy được danh sách model",
                self._describe_error(error),
            )

    # ------------------------------------------------------------------
    # Sidebar: tạo mới / chọn / đổi tên / xoá / tìm kiếm
    # ------------------------------------------------------------------
    def _handle_new_chat(self) -> None:
        conversation_id = self._chat_db.create_conversation()
        self._current_conversation_id = conversation_id
        self._chat_area.clear()
        self._refresh_conversation_list()
        self._sidebar.select_conversation(conversation_id)

    def _handle_conversation_selected(self, conversation_id: int) -> None:
        self._current_conversation_id = conversation_id
        messages = self._chat_db.get_messages(conversation_id)
        self._chat_area.load_history(messages)

    def _handle_rename(self, conversation_id: int, new_title: str) -> None:
        self._chat_db.rename_conversation(conversation_id, new_title)
        self._refresh_conversation_list()

    def _handle_delete(self, conversation_id: int) -> None:
        self._chat_db.delete_conversation(conversation_id)
        if conversation_id == self._current_conversation_id:
            # Vừa xoá đúng conversation đang mở -> không còn gì để
            # hiển thị, quay về trạng thái "chưa mở conversation nào".
            self._current_conversation_id = None
            self._chat_area.clear()
        self._refresh_conversation_list()

    def _handle_search(self, keyword: str) -> None:
        results = (
            self._chat_db.list_conversations()
            if not keyword
            else self._chat_db.search_conversations(keyword)
        )
        self._sidebar.set_conversations(results, selected_id=self._current_conversation_id)

    def _refresh_conversation_list(self) -> None:
        conversations = self._chat_db.list_conversations()
        self._sidebar.set_conversations(
            conversations, selected_id=self._current_conversation_id
        )

    # ------------------------------------------------------------------
    # Gửi câu hỏi — luồng chính, chạy nền qua QThread
    # ------------------------------------------------------------------
    def _handle_message_submitted(self, text: str, include_screenshot: bool) -> None:
        if self._current_conversation_id is None:
            # Chưa mở/tạo conversation nào (vd vừa mở app lần đầu) ->
            # tự tạo 1 conversation mới thay vì bắt user phải tự bấm
            # "New chat" trước khi gõ được câu hỏi đầu tiên.
            self._current_conversation_id = self._chat_db.create_conversation()
            self._refresh_conversation_list()
            self._sidebar.select_conversation(self._current_conversation_id)

        self._chat_area.add_message("user", text)
        self._chat_area.show_typing_indicator()
        self._input_bar.clear_input()
        self._input_bar.set_generating(True)
        self._pending_ai_bubble = None

        self._start_generation(text, include_screenshot)

    def _start_generation(self, text: str, include_screenshot: bool) -> None:
        worker = _SendMessageWorker(
            chat_logic=self._chat_logic,
            conversation_id=self._current_conversation_id,
            user_message=text,
            include_screenshot=include_screenshot,
            model=self._current_model,
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.chunk_received.connect(self._handle_chunk_received)
        worker.finished.connect(self._handle_generation_finished)
        worker.failed.connect(self._handle_generation_failed)

        # Dọn dẹp thread đúng cách sau khi xong (dù thành công, bị
        # dừng, hay lỗi): worker báo finished/failed -> yêu cầu thread
        # thoát event loop của nó -> đợi thoát hẳn -> tự huỷ cả 2 object.
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._active_thread = thread
        self._active_worker = worker
        thread.start()

    def _handle_chunk_received(self, chunk: str) -> None:
        if self._pending_ai_bubble is None:
            # Chunk ĐẦU TIÊN nhận được -> ẩn "đang gõ", tạo bubble AI
            # thật để bắt đầu stream vào đó (xem thiết kế ở ChatArea).
            self._chat_area.hide_typing_indicator()
            self._pending_ai_bubble = self._chat_area.add_message("model", "")
        self._pending_ai_bubble.append_chunk(chunk)

    def _handle_generation_finished(self, result: SendMessageResult | None) -> None:
        self._input_bar.set_generating(False)
        self._pending_ai_bubble = None
        self._active_thread = None
        self._active_worker = None

        if result is None:
            # Bị dừng giữa chừng bởi user -> không có gì thêm để làm,
            # ChatLogic đã đảm bảo không lưu câu trả lời dở dang.
            self._chat_area.hide_typing_indicator()
            return

        # Gửi tin nhắn thành công làm `updated_at` của conversation đổi
        # (ChatDB.add_message tự cập nhật) -> refresh lại Sidebar để
        # conversation này nhảy lên đầu danh sách đúng thứ tự.
        self._refresh_conversation_list()

    def _handle_generation_failed(self, error: Exception) -> None:
        self._input_bar.set_generating(False)
        self._chat_area.hide_typing_indicator()
        self._pending_ai_bubble = None
        self._active_thread = None
        self._active_worker = None

        message = self._describe_error(error)
        QMessageBox.warning(self, "Không thể lấy câu trả lời", message)

    def _handle_stop_requested(self) -> None:
        if self._active_worker is not None:
            self._active_worker.request_stop()

    @staticmethod
    def _describe_error(error: Exception) -> str:
        """
        Dịch từng loại lỗi cụ thể sang thông báo dễ hiểu cho user —
        đúng lý do `ChatLogic`/`GeminiClient` cố tình KHÔNG bọc chung
        các lỗi lại: tới tận đây, tầng UI mới là nơi quyết định hiển
        thị gì cho từng loại.
        """
        if isinstance(error, MissingApiKeyError):
            return str(error)
        if isinstance(error, GeminiAuthError):
            return "API key không hợp lệ. Vui lòng kiểm tra lại trong Settings."
        if isinstance(error, GeminiQuotaExceededError):
            return "Đã hết hạn mức sử dụng (quota) cho key này. Vui lòng thử lại sau."
        if isinstance(error, GeminiServerError):
            return "Gemini đang gặp sự cố. Vui lòng thử lại sau ít phút."
        if isinstance(error, GeminiClientError):
            return f"Không thể kết nối tới Gemini: {error}"
        return f"Đã có lỗi không xác định: {error}"

    def closeEvent(self, event) -> None:  # noqa: N802 (tên hàm theo quy ước Qt override)
        """Đóng connection SQLite khi tắt app, tránh rò rỉ file handle."""
        self._chat_db.close()
        super().closeEvent(event)