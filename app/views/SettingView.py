"""
SettingsView.py
-----------------
Màn hình Settings: chọn provider (Gemini/ChatGPT/Claude/Copilot/
DeepSeek), nhập + lưu API key cho provider đó, và chọn model mặc định
để dùng khi chat.

Vẫn giữ đúng nguyên tắc "view chỉ phát tín hiệu" đã áp dụng xuyên suốt
các view khác: `SettingsView` KHÔNG tự import `KeyStorage` hay
`GeminiClient` — chỉ phát tín hiệu (`save_key_requested`,
`provider_selected`...), để `AppController` lắng nghe rồi tự gọi
`KeyStorage.save_key()` / `ChatLogic.list_available_models()` giúp,
sau đó gọi ngược lại các hàm `set_...()` của view này để cập nhật UI
theo đúng kết quả thật (đã lưu thành công chưa, danh sách model gồm
những gì...).

TẠI SAO CHỈ CHO PHÉP CHỌN "Gemini" TRONG DROPDOWN PROVIDER, CÒN
ChatGPT/Claude/Copilot/DeepSeek HIỂN THỊ NHƯNG BỊ KHOÁ?
- `KeyStorage` đã hỗ trợ SẴN việc lưu key cho cả 5 provider (đúng định
  hướng mở rộng đa provider đã bàn trước đó) — nhưng `ChatLogic` hiện
  tại CHỈ có `GeminiClient` thật, các provider khác gọi vào sẽ raise
  `NotImplementedError`.
- Nếu để dropdown cho chọn tự do cả 5 provider mà chỉ 1 cái THẬT SỰ
  hoạt động, user nhập key cho ChatGPT xong tưởng dùng được, tới lúc
  chat mới biết bị lỗi "chưa hỗ trợ" — trải nghiệm rất tệ (lãng phí
  công gõ key + gây hoang mang).
- Giải pháp: vẫn HIỂN THỊ đủ 5 provider (để user biết app CÓ Ý ĐỊNH hỗ
  trợ, không phải thiếu sót), nhưng nếu chọn provider chưa hỗ trợ, tự
  động quay dropdown về lại Gemini + hiện dòng chú thích — thay vì để
  user đi tiếp vào ngõ cụt.

TẠI SAO Ô NHẬP API KEY DÙNG `QLineEdit.EchoMode.Password` + NÚT
"HIỆN/ẨN" RIÊNG, KHÔNG ĐỂ HIỆN CHỮ THẲNG?
- API key là thông tin nhạy cảm (tương đương mật khẩu) — mặc định ẩn
  đi (hiện dấu chấm tròn) để tránh lộ key khi có người khác nhìn qua
  màn hình lúc user đang gõ (vd đang chia sẻ màn hình, quay video
  hướng dẫn...).
- Vẫn cần nút "hiện" tuỳ chọn: API key thường là chuỗi dài, khó gõ
  đúng nếu không nhìn thấy gì để tự kiểm tra lại trước khi lưu — ẩn
  hoàn toàn không cho xem lại sẽ dễ gây lỗi gõ nhầm.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.services.KeyStorage import Provider

# Tên hiển thị cho user, khác với `Provider.value` (dùng nội bộ) — vd
# hiển thị "ChatGPT" đẹp hơn "chatgpt". Tách riêng để không lẫn lộn
# giữa "cái để hiển thị" và "cái để gửi tín hiệu"/lưu DB.
_PROVIDER_DISPLAY_NAMES = {
    Provider.GEMINI: "Gemini",
    Provider.CHATGPT: "ChatGPT",
    Provider.CLAUDE: "Claude",
    Provider.COPILOT: "Copilot",
    Provider.DEEPSEEK: "DeepSeek",
}

# Provider nào THỰC SỰ dùng được ngay bây giờ — xem giải thích ở đầu file.
_SUPPORTED_PROVIDERS = {Provider.GEMINI, Provider.DEEPSEEK}

_QSS = """
#SettingsRoot {
    background: #1e2025;
}

QLabel {
    color: #c9cdd3;
    font-size: 12px;
}
#SectionLabel {
    color: #f0f0f0;
    font-size: 13px;
    font-weight: bold;
}
#StatusLabelConfigured {
    color: #3dd68c;
    font-size: 11px;
}
#StatusLabelMissing {
    color: #e5484d;
    font-size: 11px;
}
#UnsupportedNotice {
    color: #e5b84d;
    font-size: 11px;
}

QComboBox, QLineEdit {
    background: #2a2d34;
    border: 1px solid #3d4149;
    border-radius: 8px;
    color: #f0f0f0;
    font-size: 13px;
    padding: 6px 10px;
}

#PrimaryButton {
    background: #2f6fed;
    border: none;
    border-radius: 8px;
    color: white;
    font-size: 13px;
    padding: 8px 14px;
}
#PrimaryButton:hover { background: #4a83f0; }
#PrimaryButton:disabled { background: #3d4149; color: #6b7078; }

#DangerButton {
    background: transparent;
    border: 1px solid #e5484d;
    border-radius: 8px;
    color: #e5484d;
    font-size: 12px;
    padding: 6px 12px;
}
#DangerButton:hover { background: #e5484d; color: white; }

#ToggleVisibilityButton {
    background: transparent;
    border: 1px solid #3d4149;
    border-radius: 8px;
    color: #c9cdd3;
    font-size: 13px;
}
"""


class SettingsView(QDialog):
    """API công khai mà `AppController` dùng."""

    # User chọn 1 provider khác trong dropdown (giá trị `Provider.value`,
    # vd "gemini") — CHỈ phát cho provider đã hỗ trợ (xem `_apply_role...`
    # tương tự cơ chế tự-quay-về-Gemini ở dưới).
    provider_selected = pyqtSignal(str)
    # User bấm Lưu key: (provider_value, api_key_thô_user_vừa_gõ).
    save_key_requested = pyqtSignal(str, str)
    # User bấm Xoá key cho provider đang chọn.
    delete_key_requested = pyqtSignal(str)
    # User chọn 1 model khác làm model mặc định.
    default_model_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Settings")
        self.setModal(False)  # không chặn thao tác ở cửa sổ chat chính
        self.resize(420, 320)

        self._provider_dropdown = QComboBox()
        for provider in Provider:
            label = _PROVIDER_DISPLAY_NAMES[provider]
            if provider not in _SUPPORTED_PROVIDERS:
                label += "  (sắp hỗ trợ)"
            self._provider_dropdown.addItem(label, userData=provider.value)
        self._provider_dropdown.currentIndexChanged.connect(
            self._handle_provider_index_changed
        )

        self._unsupported_notice = QLabel(
            "Provider này chưa được hỗ trợ, tự động quay về Gemini."
        )
        self._unsupported_notice.setObjectName("UnsupportedNotice")
        self._unsupported_notice.hide()

        self._api_key_input = QLineEdit()
        self._api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_input.setPlaceholderText("Dán API key vào đây...")
        self._get_key_link = QLabel()
        self._get_key_link.setObjectName("GetKeyLink")
        self._get_key_link.setOpenExternalLinks(True)

        self._toggle_visibility_button = QPushButton("👁")
        self._toggle_visibility_button.setObjectName("ToggleVisibilityButton")
        self._toggle_visibility_button.setFixedSize(32, 32)
        self._toggle_visibility_button.setCheckable(True)
        self._toggle_visibility_button.toggled.connect(self._toggle_key_visibility)

        self._save_button = QPushButton("Lưu")
        self._save_button.setObjectName("PrimaryButton")
        self._save_button.clicked.connect(self._handle_save_clicked)

        self._delete_button = QPushButton("Xoá key")
        self._delete_button.setObjectName("DangerButton")
        self._delete_button.clicked.connect(self._handle_delete_clicked)

        self._status_label = QLabel()
        self._status_label.hide()

        self._model_dropdown = QComboBox()
        self._model_dropdown.currentTextChanged.connect(
            self.default_model_changed.emit
        )

        self._build_layout()
        self.setStyleSheet(_QSS)
        self._update_key_link(self._provider_dropdown.currentData())

    def _update_key_link(self, provider_value: str) -> None:
        links = {
            "gemini": ("https://aistudio.google.com/apikey", "Lấy Gemini API key tại Google AI Studio"),
            "deepseek": ("https://platform.deepseek.com/api_keys", "Lấy DeepSeek API key tại DeepSeek Platform"),
        }
        if provider_value in links:
            url, text = links[provider_value]
            self._get_key_link.setText(f'<a href="{url}" style="color:#2f6fed;">{text}</a>')
            self._get_key_link.show()
        else:
            self._get_key_link.hide()

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        provider_label = QLabel("Provider")
        provider_label.setObjectName("SectionLabel")
        layout.addWidget(provider_label)
        layout.addWidget(self._provider_dropdown)
        layout.addWidget(self._unsupported_notice)

        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key_input, stretch=1)
        key_row.addWidget(self._toggle_visibility_button)
        key_row.addWidget(self._save_button)
        layout.addLayout(key_row)
        layout.addWidget(self._get_key_link)
        layout.addWidget(self._status_label)
        layout.addWidget(
            self._delete_button, alignment=Qt.AlignmentFlag.AlignLeft
        )

        model_label = QLabel("Model mặc định")
        model_label.setObjectName("SectionLabel")
        layout.addWidget(model_label)
        layout.addWidget(self._model_dropdown)

        layout.addStretch(1)

    # ------------------------------------------------------------------
    # API công khai — AppController gọi để cập nhật UI theo dữ liệu thật
    # ------------------------------------------------------------------
    def current_provider(self) -> str:
        return self._provider_dropdown.currentData()

    def set_key_configured(self, is_configured: bool) -> None:
        """
        Cập nhật dòng trạng thái dưới ô nhập key — gọi sau khi
        `AppController` tra `KeyStorage.has_key()` cho provider đang
        chọn (mỗi khi đổi provider, hoặc ngay sau khi lưu/xoá key
        thành công).
        """
        if is_configured:
            self._status_label.setText("✓ Đã cấu hình API key cho provider này")
            self._status_label.setObjectName("StatusLabelConfigured")
        else:
            self._status_label.setText("Chưa có API key cho provider này")
            self._status_label.setObjectName("StatusLabelMissing")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.show()
        self._delete_button.setEnabled(is_configured)

    def set_models(self, models: list[str], current: str | None = None) -> None:
        """
        Đổ danh sách model vào dropdown — cùng pattern `blockSignals`
        đã dùng ở `HeaderView.set_models()` (xem giải thích ở đó): nạp
        dữ liệu không được tự phát `default_model_changed`, chỉ user
        tự tay chọn mới phát.
        """
        self._model_dropdown.blockSignals(True)
        try:
            self._model_dropdown.clear()
            self._model_dropdown.addItems(models)
            if current is not None and current in models:
                self._model_dropdown.setCurrentText(current)
        finally:
            self._model_dropdown.blockSignals(False)

    def clear_key_input(self) -> None:
        """Xoá trắng ô nhập key sau khi lưu thành công — không để lộ key thô trên màn hình lâu hơn cần thiết."""
        self._api_key_input.clear()

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def _handle_provider_index_changed(self, _index: int) -> None:
        provider_value = self._provider_dropdown.currentData()
        provider = Provider(provider_value)

        if provider not in _SUPPORTED_PROVIDERS:
            self._unsupported_notice.show()
            # Tự quay dropdown về lại Gemini — bọc blockSignals để
            # KHÔNG kích hoạt lại chính hàm này 1 lần đệ quy nữa (đúng
            # bug đã gặp và sửa ở SideBarView khi tự setText() sửa lỗi).
            self._provider_dropdown.blockSignals(True)
            try:
                gemini_index = self._provider_dropdown.findData(
                    Provider.GEMINI.value
                )
                self._provider_dropdown.setCurrentIndex(gemini_index)
            finally:
                self._provider_dropdown.blockSignals(False)
            self._update_key_link(provider_value)
            self.provider_selected.emit(Provider.GEMINI.value)
            return

        self._unsupported_notice.hide()
        self._update_key_link(provider_value)
        self.provider_selected.emit(provider_value)

    def _toggle_key_visibility(self, checked: bool) -> None:
        self._api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self._toggle_visibility_button.setText("🙈" if checked else "👁")

    def _handle_save_clicked(self) -> None:
        api_key = self._api_key_input.text().strip()
        if not api_key:
            return
        provider_value = self._provider_dropdown.currentData()
        self.save_key_requested.emit(provider_value, api_key)

    def _handle_delete_clicked(self) -> None:
        provider_value = self._provider_dropdown.currentData()
        self.delete_key_requested.emit(provider_value)