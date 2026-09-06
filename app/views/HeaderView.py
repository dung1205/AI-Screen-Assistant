"""
HeaderView.py
--------------
Thanh header trên cùng màn hình chat: nút thu/mở Sidebar, dropdown chọn
model Gemini đang dùng, và nút mở Settings.

Vẫn giữ đúng nguyên tắc đã áp dụng cho `InputBarView`: view này KHÔNG
tự gọi `ChatLogic`/`KeyStorage` gì cả — chỉ phát tín hiệu (Signal), để
`AppController` lắng nghe rồi tự quyết định làm gì (mở/đóng Sidebar,
đổi model đang dùng, mở màn hình Settings).

TẠI SAO PHẢI "CHẶN TÍN HIỆU" (`blockSignals`) KHI ĐỔ DANH SÁCH MODEL
VÀO DROPDOWN?
- `QComboBox` tự phát tín hiệu `currentTextChanged` MỖI KHI nội dung
  đang chọn thay đổi — kể cả khi thay đổi đó là do CODE tự gọi
  `addItem()`/`setCurrentText()` (nạp dữ liệu ban đầu), không phải do
  user tự tay chọn trong dropdown.
- Nếu không chặn, lúc `set_models()` được gọi (vd sau khi
  `ChatLogic.list_available_models()` trả về danh sách, đổ vào
  dropdown lần đầu), `HeaderView` sẽ phát nhầm tín hiệu `model_changed`
  dù user chưa hề động vào dropdown -> `AppController` hiểu nhầm
  thành "user vừa đổi model", có thể gây ra hành động thừa (vd lưu lại
  model mặc định vào Settings dù chẳng ai chọn gì).
- Cách xử lý: bọc đoạn code nạp dữ liệu trong
  `blockSignals(True)` ... `blockSignals(False)` — trong khoảng đó,
  `QComboBox` vẫn đổi giá trị nội bộ bình thường, nhưng KHÔNG phát tín
  hiệu ra ngoài. Chỉ những lần đổi do CHÍNH TAY USER chọn (sau khi đã
  `blockSignals(False)`) mới phát tín hiệu `model_changed` thật sự.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget

_QSS = """
#HeaderRoot {
    background: #1e2025;
    border-bottom: 1px solid #2f333b;
}

#SidebarToggleButton, #SettingsButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    color: #c9cdd3;
    font-size: 16px;
}
#SidebarToggleButton:hover, #SettingsButton:hover {
    background: #2a2d34;
}

#ModelDropdown {
    background: #2a2d34;
    border: 1px solid #3d4149;
    border-radius: 10px;
    color: #f0f0f0;
    font-size: 13px;
    padding: 4px 10px;
    min-width: 160px;
}
#ModelDropdown:hover {
    border: 1px solid #4a4f59;
}
/* Bỏ mũi tên dropdown mặc định (thường xấu/lệch theme trên các OS
   khác nhau) -> chỉ giữ khung rỗng, để mắt người dùng tập trung vào
   chữ tên model, không có icon mũi tên lạc tông màu. */
#ModelDropdown::drop-down {
    border: none;
    width: 20px;
}
/* Style riêng cho danh sách popup xổ xuống khi bấm vào dropdown —
   PHẢI khai báo riêng vì popup này là 1 cửa sổ con độc lập của Qt,
   không tự kế thừa style của #ModelDropdown. */
#ModelDropdown QAbstractItemView {
    background: #2a2d34;
    border: 1px solid #3d4149;
    color: #f0f0f0;
    selection-background-color: #2f6fed;
    outline: none;
}
"""


class HeaderView(QWidget):
    """API công khai mà `AppController` dùng."""

    # User bấm nút thu/mở Sidebar.
    sidebar_toggle_requested = pyqtSignal()
    # User TỰ TAY chọn 1 model khác trong dropdown (tên model mới).
    model_changed = pyqtSignal(str)
    # User bấm nút mở Settings.
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeaderRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._sidebar_toggle_button = QPushButton("☰")
        self._sidebar_toggle_button.setObjectName("SidebarToggleButton")
        self._sidebar_toggle_button.setFixedSize(36, 36)
        self._sidebar_toggle_button.clicked.connect(
            self.sidebar_toggle_requested.emit
        )

        self._model_dropdown = QComboBox()
        self._model_dropdown.setObjectName("ModelDropdown")
        # `currentTextChanged` (không phải `currentIndexChanged`) vì
        # `AppController`/`ChatLogic` cần TÊN model (str) để truyền cho
        # `GeminiClient`, không cần biết nó nằm ở vị trí (index) nào
        # trong danh sách — dùng đúng kiểu tín hiệu cho đúng nhu cầu,
        # tránh phải tự tra `itemText(index)` lại ở nơi khác.
        self._model_dropdown.currentTextChanged.connect(self.model_changed.emit)

        self._settings_button = QPushButton("⚙")
        self._settings_button.setObjectName("SettingsButton")
        self._settings_button.setFixedSize(36, 36)
        self._settings_button.clicked.connect(self.settings_requested.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        layout.addWidget(self._sidebar_toggle_button)
        layout.addStretch(1)
        layout.addWidget(self._model_dropdown)
        layout.addStretch(1)
        layout.addWidget(self._settings_button)

        self.setStyleSheet(_QSS)

    # ------------------------------------------------------------------
    # API công khai
    # ------------------------------------------------------------------
    def set_models(self, models: list[str], current: str | None = None) -> None:
        """
        Đổ danh sách model vào dropdown (gọi sau khi
        `ChatLogic.list_available_models()` trả về, hoặc khi user đổi
        API key trong Settings làm danh sách model khả dụng thay đổi).

        Args:
            models: danh sách tên model, vd từ `GeminiClient.list_models()`.
            current: model đang được chọn hiện tại (nếu có trong
                `models` thì dropdown hiển thị đúng cái đó; nếu không
                truyền hoặc không có trong danh sách, mặc định chọn
                phần tử đầu tiên).
        """
        self._model_dropdown.blockSignals(True)  # xem giải thích ở đầu file
        try:
            self._model_dropdown.clear()
            self._model_dropdown.addItems(models)
            if current is not None and current in models:
                self._model_dropdown.setCurrentText(current)
        finally:
            # Luôn mở lại tín hiệu dù có lỗi giữa chừng (vd `models`
            # rỗng) — tránh dropdown bị "câm" (không phát tín hiệu gì
            # nữa) vĩnh viễn chỉ vì 1 lần gọi set_models() bị lỗi.
            self._model_dropdown.blockSignals(False)

    def current_model(self) -> str:
        """Tên model đang được chọn trong dropdown."""
        return self._model_dropdown.currentText()