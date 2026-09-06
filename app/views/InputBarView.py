"""
InputBarView.py
-----------------
Thanh nhập liệu ở dưới cùng màn hình chat: ô nhập câu hỏi (tự giãn
chiều cao theo số dòng), checkbox "kèm ảnh màn hình" + cảnh báo quyền
riêng tư, và 1 nút tròn đổi giữa "Gửi" / "Dừng" tuỳ trạng thái.

NGUYÊN TẮC "VIEW CHỈ PHÁT TÍN HIỆU, KHÔNG TỰ GỌI NGHIỆP VỤ":
- `InputBarView` KHÔNG import `ChatLogic`, không tự gọi Gemini hay lưu
  DB gì cả. Nó chỉ làm 1 việc: khi user bấm Gửi (hoặc Enter), PHÁT RA
  1 tín hiệu (`message_submitted`) kèm nội dung câu hỏi.
- `AppController` là nơi LẮNG NGHE tín hiệu đó rồi mới gọi vào
  `ChatLogic.send_message()`. Nhờ tách bạch thế này, muốn test
  `InputBarView` không cần chạy `ChatLogic`/`GeminiClient` thật —
  chỉ cần kiểm tra tín hiệu có phát đúng nội dung hay không.
- Đây chính là mô hình Signal/Slot của Qt — cách "chuẩn" để 2 widget
  không cần biết trực tiếp về nhau vẫn giao tiếp được.

TẠI SAO PHẢI TỰ VIẾT 1 CLASS CON KẾ THỪA `QTextEdit`
(`_EnterToSendTextEdit`) THAY VÌ DÙNG THẲNG `QTextEdit`/`QLineEdit`?
- `QLineEdit` chỉ nhập được 1 DÒNG — không phù hợp vì câu hỏi có thể
  dài, cần xuống dòng (vd dán 1 đoạn code hỏi debug).
- `QTextEdit` mặc định thì phím Enter LUÔN xuống dòng mới, không có
  khái niệm "Enter để gửi" — đúng hành vi 1 ô soạn thảo văn bản thông
  thường, nhưng KHÔNG phải hành vi mong muốn của 1 ô chat (ChatGPT,
  Messenger... đều dùng Enter để gửi, Shift+Enter mới xuống dòng).
- Cách duy nhất để đổi hành vi phím Enter trong Qt là "chặn" sự kiện
  bàn phím TRƯỚC khi `QTextEdit` xử lý mặc định — bằng cách override
  `keyPressEvent()` trong 1 class con. Nếu Enter (không giữ Shift) ->
  phát tín hiệu riêng, KHÔNG gọi hàm gốc (không xuống dòng). Nếu
  Shift+Enter hoặc phím khác -> gọi hàm gốc của `QTextEdit` như bình
  thường (xuống dòng / gõ chữ như thường).

TẠI SAO Ô NHẬP TỰ GIÃN CHIỀU CAO (AUTO-RESIZE)?
- Mặc định `QTextEdit` có chiều cao CỐ ĐỊNH — nếu để cố định 1 dòng,
  gõ câu dài sẽ bị cuộn ẩn bên trong ô nhỏ xíu, khó nhìn lại những gì
  vừa gõ. Nếu để cố định cao (vd 5 dòng) thì lúc chỉ gõ 1 câu ngắn, ô
  nhập trống trải quá nhiều khoảng trắng vô ích.
- Giải pháp: đo chiều cao THẬT SỰ cần thiết để hiển thị hết nội dung
  hiện tại (`document().size().height()`), rồi set chiều cao ô nhập
  bằng đúng số đó — nhưng giới hạn trong khoảng [min, max] dòng, để ô
  nhập không co về 0 (mất luôn chỗ gõ) và không phình to chiếm hết màn
  hình nếu user dán 1 đoạn văn bản khổng lồ.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QTextOption
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Số dòng tối thiểu/tối đa mà ô nhập tự giãn tới — xem giải thích
# "auto-resize" ở đầu file.
_MIN_LINES = 1
_MAX_LINES = 6

_QSS = """
#InputBarRoot {
    background: transparent;
}

#InputPill {
    background: #2a2d34;
    border: 1px solid #3d4149;
    border-radius: 22px;
}

#InputPill:focus-within {
    border: 1px solid #2f6fed;
}

#QuestionEdit {
    background: transparent;
    border: none;
    color: #f0f0f0;
    font-size: 14px;
    selection-background-color: #2f6fed;
}

#PrivacyLabel {
    color: #8a8f98;
    font-size: 11px;
}

#SendButton {
    background: #2f6fed;
    border: none;
    border-radius: 20px;
    color: white;
    font-size: 16px;
    font-weight: bold;
}
#SendButton:hover {
    background: #4a83f0;
    padding: 1px 0px 0px 1px;
}

#SendButton:pressed {
    background: #1a5fdf;
    padding: 2px 0px 0px 2px;
}
#SendButton:disabled {
    background: #3d4149;
    color: #6b7078;
}

#StopButton {
    background: #e5484d;
    border: none;
    border-radius: 20px;
    color: white;
    font-size: 14px;
    font-weight: bold;
}
#StopButton:hover {
    background: #ef5d62;
}

#ScreenshotCheckbox {
    color: #c9cdd3;
    font-size: 12px;
}
"""


class _EnterToSendTextEdit(QTextEdit):
    """
    `QTextEdit` phiên bản riêng cho ô chat: Enter để gửi, Shift+Enter
    để xuống dòng. Class nội bộ (không export) — chi tiết cài đặt
    riêng của `InputBarView`, không phải component dùng chung.
    """

    submit_requested = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        shift_held = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if is_enter and not shift_held:
            # Enter thường -> yêu cầu gửi, KHÔNG gọi super() nên
            # QTextEdit không tự chèn ký tự xuống dòng.
            self.submit_requested.emit()
            return

        # Mọi phím khác (bao gồm Shift+Enter) -> hành vi mặc định của
        # QTextEdit như bình thường.
        super().keyPressEvent(event)


class InputBarView(QWidget):
    """
    API công khai mà `AppController` dùng: kết nối 2 tín hiệu
    (`message_submitted`, `stop_requested`) và gọi `set_generating()`
    khi trạng thái sinh câu trả lời thay đổi.
    """

    # Phát khi user muốn gửi câu hỏi: (nội dung câu hỏi, có kèm ảnh không).
    message_submitted = pyqtSignal(str, bool)
    # Phát khi user bấm nút Dừng lúc đang generating.
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InputBarRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._is_generating = False

        self._text_edit = _EnterToSendTextEdit()
        self._text_edit.setObjectName("QuestionEdit")
        self._text_edit.setPlaceholderText("Hỏi Gemini...")
        self._text_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._text_edit.submit_requested.connect(self._handle_submit)
        self._text_edit.textChanged.connect(self._update_auto_height)

        self._send_button = QPushButton("➤")
        self._send_button.setObjectName("SendButton")
        self._send_button.setFixedSize(40, 40)
        self._send_button.clicked.connect(self._handle_submit)

        self._screenshot_checkbox = QCheckBox("Kèm ảnh màn hình")
        self._screenshot_checkbox.setObjectName("ScreenshotCheckbox")

        self._privacy_label = QLabel("Ảnh toàn màn hình sẽ được gửi tới Google Gemini")
        self._privacy_label.setObjectName("PrivacyLabel")

        self._build_layout()
        self.setStyleSheet(_QSS)
        self._update_auto_height()

    def _build_layout(self) -> None:
        # Hàng trên: ô nhập (pill) + nút gửi/dừng, nằm cạnh nhau.
        pill = QWidget()
        pill.setObjectName("InputPill")
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(16, 4, 6, 4)
        pill_layout.setSpacing(8)
        pill_layout.addWidget(self._text_edit, stretch=1)
        pill_layout.addWidget(
            self._send_button, alignment=Qt.AlignmentFlag.AlignBottom
        )

        # Hàng dưới: checkbox + label cảnh báo quyền riêng tư, canh trái.
        options_row = QHBoxLayout()
        options_row.setContentsMargins(16, 0, 0, 0)
        options_row.setSpacing(8)
        options_row.addWidget(self._screenshot_checkbox)
        options_row.addWidget(self._privacy_label)
        options_row.addStretch(1)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 8, 12, 12)
        root_layout.setSpacing(6)
        root_layout.addWidget(pill)
        root_layout.addLayout(options_row)

    # ------------------------------------------------------------------
    # API công khai
    # ------------------------------------------------------------------
    def set_generating(self, is_generating: bool) -> None:
        """
        Chuyển đổi UI giữa 2 trạng thái:
        - Đang generating: nút đổi thành "Dừng" (đỏ), ô nhập bị khoá
          (không cho gõ/gửi câu hỏi mới trong lúc câu trước chưa xong,
          tránh user bấm gửi 2 lần liên tiếp gây lẫn lộn thứ tự).
        - Không generating: nút trở lại "Gửi" (xanh), ô nhập mở khoá.

        `AppController` gọi hàm này ở 2 thời điểm: `True` ngay trước
        khi gọi `ChatLogic.send_message()`, `False` ngay sau khi
        stream xong (hoặc lỗi/bị dừng).
        """
        self._is_generating = is_generating
        self._text_edit.setEnabled(not is_generating)
        self._screenshot_checkbox.setEnabled(not is_generating)

        if is_generating:
            self._send_button.setObjectName("StopButton")
            self._send_button.setText("■")
            self._send_button.clicked.disconnect()
            self._send_button.clicked.connect(self.stop_requested.emit)
        else:
            self._send_button.setObjectName("SendButton")
            self._send_button.setText("➤")
            self._send_button.clicked.disconnect()
            self._send_button.clicked.connect(self._handle_submit)

        # Đổi objectName xong phải "refresh" lại stylesheet của riêng
        # widget này — Qt không tự áp style mới nếu chỉ đổi objectName
        # lúc runtime, cần unpolish/polish lại theo đúng API của Qt.
        self._send_button.style().unpolish(self._send_button)
        self._send_button.style().polish(self._send_button)

    def clear_input(self) -> None:
        """Xoá trắng ô nhập — gọi sau khi câu hỏi đã được gửi đi thành công."""
        self._text_edit.clear()

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def _handle_submit(self) -> None:
        if self._is_generating:
            # Đang generating thì nút đã đổi công dụng thành Dừng rồi,
            # nhưng phòng hờ Enter vẫn gọi tới đây (vd race condition
            # lúc chuyển trạng thái) -> không làm gì cả, an toàn.
            return

        text = self._text_edit.toPlainText().strip()
        if not text:
            # Không gửi câu hỏi rỗng/chỉ toàn khoảng trắng.
            return

        include_screenshot = self._screenshot_checkbox.isChecked()
        self.message_submitted.emit(text, include_screenshot)

    def _update_auto_height(self) -> None:
        """Xem giải thích 'auto-resize' ở đầu file."""
        document = self._text_edit.document()
        # Đặt trước độ rộng của "trang" tài liệu bằng đúng độ rộng
        # hiện tại của ô nhập, để document tính word-wrap đúng TRƯỚC
        # khi đo chiều cao — nếu không, document nghĩ nó rộng vô hạn,
        # tính chiều cao sai (không wrap dòng nào cả).
        document.setTextWidth(self._text_edit.viewport().width())

        line_height = self._text_edit.fontMetrics().lineSpacing()
        content_height = document.size().height()
        margins = self._text_edit.contentsMargins()
        frame = 2 * self._text_edit.frameWidth()

        min_height = line_height * _MIN_LINES + margins.top() + margins.bottom() + frame
        max_height = line_height * _MAX_LINES + margins.top() + margins.bottom() + frame

        target_height = int(content_height + margins.top() + margins.bottom() + frame)
        target_height = max(min_height, min(target_height, max_height))

        self._text_edit.setFixedHeight(target_height)