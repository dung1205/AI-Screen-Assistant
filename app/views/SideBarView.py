"""
SideBarView.py
----------------
Sidebar bên trái: nút "Cuộc trò chuyện mới", ô tìm kiếm (debounce), và
danh sách các conversation — click để mở, chuột phải để đổi tên/xoá.

Vẫn giữ nguyên nguyên tắc "view chỉ phát tín hiệu": `SideBarView`
KHÔNG tự gọi `ChatDB` — mọi thao tác đọc/ghi dữ liệu thật (tạo, xoá,
đổi tên, tìm kiếm) đều đi qua tín hiệu để `AppController` gọi
`ChatDB` giúp. Ngoại lệ DUY NHẤT: hộp thoại XÁC NHẬN xoá
(`QMessageBox`) — đây là hành vi UI thuần tuý ("bạn có chắc không?"),
không phải nghiệp vụ, nên xử lý ngay trong view là hợp lý; nếu user
bấm "Có", view MỚI phát tín hiệu để AppController thực sự xoá — view
không tự xoá gì trong DB cả.

TẠI SAO CẦN "DEBOUNCE" CHO Ô TÌM KIẾM, VÀ CƠ CHẾ HOẠT ĐỘNG THẾ NÀO?
- Nếu gọi `ChatDB.search_conversations()` (1 query SQLite) SAU MỖI KÝ
  TỰ user gõ, gõ 1 từ 5 chữ sẽ chạy 5 query liên tiếp trong tích tắc —
  lãng phí, và với danh sách nhiều conversation có thể gây giật UI.
- "Debounce" = đợi user NGỪNG gõ một khoảng thời gian ngắn (ở đây
  300ms) rồi mới thực sự tìm kiếm, thay vì tìm ngay lập tức mỗi ký tự.
- Cơ chế bằng `QTimer`: mỗi lần user gõ thêm 1 ký tự, HUỶ đếm giờ cũ
  (nếu có) và bắt đầu đếm lại từ đầu (`timer.start(300)` gọi lại thì
  Qt tự reset về 300ms, không cộng dồn). Chỉ khi nào 300ms trôi qua mà
  KHÔNG có ký tự mới nào được gõ, `timeout` mới thực sự bắn ra, lúc đó
  mới phát tín hiệu `search_query_changed` để AppController query DB.
- Nhờ vậy, gõ nhanh 1 câu dài chỉ tốn ĐÚNG 1 lần query (sau khi gõ
  xong), không phải 1 lần cho mỗi ký tự.

TẠI SAO DANH SÁCH DÙNG LẠI PATTERN `blockSignals` GIỐNG HeaderView?
- `QListWidgetItem` cho phép sửa TRỰC TIẾP trên danh sách (double-click
  hoặc qua menu "Đổi tên") — mỗi lần nội dung item đổi, `QListWidget`
  phát tín hiệu `itemChanged`.
- Vấn đề giống hệt `HeaderView`: khi `set_conversations()` NẠP dữ liệu
  ban đầu (gọi `item.setText(...)` cho từng conversation), tín hiệu
  `itemChanged` cũng bị bắn ra — nếu không chặn, mỗi lần mở lại app
  hoặc tìm kiếm sẽ bị hiểu NHẦM thành "user vừa đổi tên hàng loạt
  conversation", gửi hàng loạt tín hiệu rename sai tới AppController.
- Giải pháp giống hệt `HeaderView.set_models()`: bọc toàn bộ code nạp
  dữ liệu trong `blockSignals(True)`/`blockSignals(False)`.
- LƯU Ý THÊM (phát hiện lúc test): không chỉ lúc NẠP dữ liệu ban đầu
  mới cần chặn — bất kỳ chỗ nào trong CODE tự gọi `item.setText(...)`
  (vd tự sửa lại thành tên mặc định khi user cố đặt tên rỗng, xem
  `_handle_item_renamed`) cũng phải bọc `blockSignals`, nếu không
  chính lệnh `setText()` sửa lỗi đó lại tự kích hoạt `itemChanged` lần
  nữa (đệ quy), gây phát tín hiệu 2 lần cho đúng 1 lần user thao tác.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.ChatDB import Conversation

_SEARCH_DEBOUNCE_MS = 300

_QSS = """
#SidebarRoot {
    background: #1a1c20;
    border-right: 1px solid #2f333b;
}

#NewChatButton {
    background: #2a2d34;
    border: 1px solid #3d4149;
    border-radius: 10px;
    color: #f0f0f0;
    font-size: 13px;
    padding: 8px;
    text-align: left;
    outline: none;
}
#NewChatButton:hover {
    background: #33373f;
}

#SearchEdit {
    background: #22242a;
    border: 1px solid #3d4149;
    border-radius: 10px;
    color: #f0f0f0;
    font-size: 13px;
    padding: 6px 10px;
}

#ConversationList {
    background: transparent;
    border: none;
    color: #d5d8dd;
    font-size: 13px;
}
#ConversationList::item {
    padding: 8px 10px;
    border-radius: 8px;
}
#ConversationList::item:hover {
    background: #24272d;
    border-radius: 8px;
}
#ConversationList::item:selected {
    background: #2f6fed;
    color: white;
    border-radius: 8px;
}
"""


class SideBarView(QWidget):
    """API công khai mà `AppController` dùng."""

    new_chat_requested = pyqtSignal()
    conversation_selected = pyqtSignal(int)
    conversation_rename_requested = pyqtSignal(int, str)
    conversation_delete_requested = pyqtSignal(int)
    # Phát sau khi debounce xong — chuỗi rỗng nghĩa là "xoá tìm kiếm,
    # hiện lại toàn bộ danh sách" (AppController tự quyết định gọi
    # list_conversations() hay search_conversations() tuỳ chuỗi rỗng
    # hay không).
    search_query_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SidebarRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._new_chat_button = QPushButton("＋  New chat")
        self._new_chat_button.setObjectName("NewChatButton")
        self._new_chat_button.clicked.connect(self.new_chat_requested.emit)
        self._new_chat_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._new_chat_button.setAutoDefault(False)
        self._new_chat_button.setDefault(False)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("SearchEdit")
        self._search_edit.setPlaceholderText("Tìm kiếm cuộc trò chuyện...")
        self._search_edit.textChanged.connect(self._restart_search_debounce)

        self._search_debounce_timer = QTimer(self)
        self._search_debounce_timer.setSingleShot(True)
        self._search_debounce_timer.timeout.connect(self._emit_search_query)

        self._list_widget = QListWidget()
        self._list_widget.setObjectName("ConversationList")
        self._list_widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._list_widget.customContextMenuRequested.connect(
            self._show_context_menu
        )
        self._list_widget.itemClicked.connect(self._handle_item_clicked)
        self._list_widget.itemChanged.connect(self._handle_item_renamed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self._new_chat_button)
        layout.addWidget(self._search_edit)
        layout.addWidget(self._list_widget, stretch=1)

        self.setStyleSheet(_QSS)
        self._list_widget.setStyleSheet(
    self._list_widget.styleSheet() +
    "QListWidget { show-decoration-selected: 1; }"
)

    # ------------------------------------------------------------------
    # API công khai
    # ------------------------------------------------------------------
    def set_conversations(
        self, conversations: list[Conversation], selected_id: int | None = None
    ) -> None:
        """
        Vẽ lại toàn bộ danh sách — gọi sau khi `AppController` lấy dữ
        liệu mới từ `ChatDB.list_conversations()` hoặc
        `ChatDB.search_conversations()`.

        Args:
            conversations: danh sách cần hiển thị, ĐÃ sắp xếp sẵn theo
                đúng thứ tự mong muốn (SideBarView không tự sắp xếp).
            selected_id: id conversation cần tô sáng sẵn (vd đang mở),
                None nếu không có gì được chọn.
        """
        self._list_widget.blockSignals(True)  # xem giải thích ở đầu file
        try:
            self._list_widget.clear()
            for conv in conversations:
                item = QListWidgetItem(conv.title)
                item.setData(Qt.ItemDataRole.UserRole, conv.id)
                # Cho phép sửa TRỰC TIẾP trên item (double-click hoặc
                # gọi editItem() từ menu chuột phải) -> dùng cho tính
                # năng đổi tên inline.
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self._list_widget.addItem(item)
                if selected_id is not None and conv.id == selected_id:
                    self._list_widget.setCurrentItem(item)
        finally:
            self._list_widget.blockSignals(False)

    def select_conversation(self, conversation_id: int | None) -> None:
        """
        Tô sáng 1 conversation theo id mà KHÔNG phát tín hiệu
        `conversation_selected` (khác với user tự click) — dùng khi
        `AppController` tự chuyển conversation bằng code (vd vừa tạo
        conversation mới, cần tô sáng nó trong Sidebar mà không tự
        kích hoạt lại luồng "mở conversation" 1 lần nữa cho chính nó).
        """
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == conversation_id:
                # setCurrentItem không tự phát itemClicked (tín hiệu đó
                # CHỈ bắn khi có click chuột thật), nên không cần
                # blockSignals ở đây.
                self._list_widget.setCurrentItem(item)
                return

    # ------------------------------------------------------------------
    # Nội bộ — tìm kiếm (debounce)
    # ------------------------------------------------------------------
    def _restart_search_debounce(self, _text: str) -> None:
        self._search_debounce_timer.start(_SEARCH_DEBOUNCE_MS)

    def _emit_search_query(self) -> None:
        self.search_query_changed.emit(self._search_edit.text().strip())

    # ------------------------------------------------------------------
    # Nội bộ — chọn / đổi tên / xoá
    # ------------------------------------------------------------------
    def _handle_item_clicked(self, item: QListWidgetItem) -> None:
        conversation_id = item.data(Qt.ItemDataRole.UserRole)
        self.conversation_selected.emit(conversation_id)

    def _handle_item_renamed(self, item: QListWidgetItem) -> None:
        """
        Gọi khi `itemChanged` bắn ra do user THẬT SỰ sửa xong tên (kết
        thúc chế độ edit inline) — không bắn trong lúc `set_conversations()`
        nạp dữ liệu vì đã `blockSignals` ở đó.
        """
        new_title = item.text().strip()
        if not new_title:
            # Không cho đặt tên rỗng — trả lại tên mặc định. Phải tự
            # `blockSignals` quanh `setText()` sửa lại này, nếu không
            # chính `setText()` đây lại kích hoạt `itemChanged` LẦN
            # NỮA (đệ quy), khiến hàm này chạy lại 1 lần thừa và phát
            # tín hiệu `conversation_rename_requested` những 2 LẦN cho
            # cùng 1 lần sửa của user.
            self._list_widget.blockSignals(True)
            try:
                item.setText("Cuộc trò chuyện mới")
            finally:
                self._list_widget.blockSignals(False)
            new_title = "Cuộc trò chuyện mới"
        conversation_id = item.data(Qt.ItemDataRole.UserRole)
        self.conversation_rename_requested.emit(conversation_id, new_title)

    def _show_context_menu(self, position) -> None:
        item = self._list_widget.itemAt(position)
        if item is None:
            return

        menu = QMenu(self)
        rename_action = menu.addAction("Đổi tên")
        delete_action = menu.addAction("Xoá")
        chosen_action = menu.exec(
            self._list_widget.viewport().mapToGlobal(position)
        )

        if chosen_action is rename_action:
            self._list_widget.editItem(item)
        elif chosen_action is delete_action:
            self._confirm_and_delete(item)

    def _confirm_and_delete(self, item: QListWidgetItem) -> None:
        """
        Hộp thoại xác nhận — hành vi UI thuần tuý, KHÔNG vi phạm
        nguyên tắc "view không tự gọi nghiệp vụ" (xem giải thích ở đầu
        file): view chỉ hỏi "có chắc không", việc XOÁ THẬT trong DB
        vẫn do AppController làm sau khi nhận tín hiệu
        `conversation_delete_requested`.
        """
        confirm = QMessageBox.question(
            self,
            "Xoá cuộc trò chuyện",
            f'Xoá "{item.text()}"? Hành động này không thể hoàn tác.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            conversation_id = item.data(Qt.ItemDataRole.UserRole)
            self.conversation_delete_requested.emit(conversation_id)