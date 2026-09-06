"""
MessageBubble.py
------------------
Component NHỎ NHẤT trong tầng views — 1 bong bóng tin nhắn duy nhất.
`ChatArea.py` sẽ tạo nhiều `MessageBubble` xếp chồng lên nhau để tạo
thành cả cuộc trò chuyện. Đây là lý do phải code component này TRƯỚC
`ChatArea` — không thể xếp chồng thứ chưa tồn tại.

2 QUY TẮC HIỂN THỊ ĐÃ THỐNG NHẤT TRƯỚC ĐÓ (yêu cầu gốc của bug fix):
1. Bong bóng AI (role="model"): chiếm TOÀN BỘ chiều rộng khung chat.
2. Bong bóng user (role="user"): chỉ chiếm TỐI ĐA 50% chiều rộng, chữ
   xuống dòng tự nhiên theo từ (không phải kiểu "một chữ một dòng" như
   bug cũ).

TẠI SAO BUG CŨ BỊ "MỘT CHỮ MỘT DÒNG"?
Lỗi kinh điển khi giới hạn chiều rộng 1 QLabel trong PyQt: đặt
`setMaximumWidth()` bằng 1 SỐ PIXEL CỐ ĐỊNH quá nhỏ (vd 80px — đủ chứa
1-2 từ ngắn), hoặc dùng `QSizePolicy.Policy.Fixed` khiến widget bị ép
xuống kích thước tối thiểu. `QLabel` có `wordWrap(True)` vẫn xuống dòng
đúng theo TỪ, nhưng nếu khung chứa quá hẹp, mỗi dòng chỉ vừa 1 từ ->
nhìn giống lỗi "xuống dòng theo ký tự".
Cách sửa ĐÚNG: không hardcode 1 số pixel cụ thể, mà tính max-width
TƯƠNG ĐỐI theo chiều rộng khung chat hiện tại (vd 50%) — xem
`set_container_width()` bên dưới. `ChatArea` sẽ gọi hàm này mỗi khi
kích thước cửa sổ thay đổi (resize), để tỉ lệ 50% luôn đúng dù cửa sổ
to hay nhỏ.

TẠI SAO BONG BÓNG AI DÙNG `Qt.TextFormat.MarkdownText` CÒN USER DÙNG
`Qt.TextFormat.PlainText`?
- `QLabel` của Qt6 có khả năng TỰ RENDER MARKDOWN sẵn (Qt dùng thư viện
  `md4c` tích hợp sẵn bên trong) — chỉ cần set `textFormat` là
  `MarkdownText`, không cần cài thêm thư viện markdown-to-html riêng.
  Hỗ trợ: **in đậm**, *in nghiêng*, code block, danh sách, link...
  Đúng yêu cầu "Chat UI renders markdown in AI responses" đã thống
  nhất trước đó.
- Câu hỏi của USER thì KHÔNG nên render markdown: nếu user gõ
  "2*3=6" hay "*quan trọng*", họ muốn hiển thị ĐÚNG NGUYÊN VĂN những
  gì họ gõ, không muốn Qt tự động biến `*quan trọng*` thành chữ in
  nghiêng ngoài ý muốn. Dùng `PlainText` đảm bảo WYSIWYG (what you
  type is what you see) cho phần user gõ.

TẠI SAO STREAMING (hiệu ứng gõ chữ) LẠI DÙNG `append_chunk()` SỬA TRỰC
TIẾP TEXT CỦA 1 LABEL, THAY VÌ TẠO WIDGET MỚI CHO MỖI CHỮ?
- Mỗi lần Gemini trả về 1 đoạn chữ (`on_chunk` từ ChatLogic), nếu tạo
  1 `QLabel` mới cho mỗi đoạn thì sau vài giây stream sẽ có HÀNG TRĂM
  widget con trong 1 bubble -> vừa chậm (PyQt phải layout lại toàn bộ
  mỗi lần thêm widget), vừa tốn bộ nhớ.
- Cách đúng: 1 bubble = 1 `QLabel` DUY NHẤT, khi có chữ mới thì chỉ
  `setText()` lại với chuỗi đã gộp — Qt tự vẽ lại nội dung label (thao
  tác rẻ), không phải tạo/xoá widget liên tục.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

# Bong bóng user tối đa chiếm bao nhiêu % chiều rộng khung chat.
# Đặt hằng số ở đây (không hardcode số pixel) để dễ chỉnh nếu sau này
# muốn đổi tỉ lệ, và để `set_container_width()` luôn tính tương đối.
USER_BUBBLE_MAX_WIDTH_RATIO = 0.5

# Khoảng đệm bên trong bubble (giữa viền bubble và chữ).
_BUBBLE_PADDING = "10px 14px"


class MessageBubble(QFrame):
    """
    1 bong bóng tin nhắn — dùng chung cho cả user và AI, khác nhau ở
    tham số `role` truyền vào constructor.
    """

    def __init__(self, role: str, text: str = "", parent=None):
        """
        Args:
            role: "user" hoặc "model" — quyết định style + cách render text.
            text: nội dung ban đầu. Để rỗng khi tạo bubble AI trước lúc
                stream bắt đầu (sẽ gọi `append_chunk()` dần sau).
        """
        super().__init__(parent)
        if role not in ("user", "model"):
            raise ValueError(f"role không hợp lệ: {role!r}")

        self.role = role
        self._full_text = ""

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        # Cho phép user bôi đen/copy chữ từ bubble — hữu ích nhất là
        # với câu trả lời AI (copy code, copy đáp án...).
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self._apply_role_style()
        self.set_text(text)

    def _apply_role_style(self) -> None:
        """Style khác nhau theo role — xem giải thích ở đầu file."""
        if self.role == "model":
            self._label.setTextFormat(Qt.TextFormat.MarkdownText)
            # Bong bóng AI KHÔNG có nền màu riêng (hoà vào nền khung
            # chat chung), vì nó chiếm full-width — có nền màu sẽ giống
            # 1 khối màu lớn chiếm cả màn hình, nhìn nặng nề.
            self.setStyleSheet(
                f"QFrame {{ background: transparent; }}"
                f"QLabel {{ padding: {_BUBBLE_PADDING}; }}"
            )
            # Expanding: bubble AI luôn giãn hết chiều rộng khung cha.
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )
        else:
            self._label.setTextFormat(Qt.TextFormat.PlainText)
            self.setStyleSheet(
                "QFrame {"
                "  background: #2f6fed;"
                "  border-radius: 14px;"
                "}"
                f"QLabel {{ padding: {_BUBBLE_PADDING}; color: white; }}"
            )
            # Maximum: bubble user co lại vừa đúng nội dung, KHÔNG tự
            # giãn hết cỡ — nhưng vẫn bị chặn trần bởi maximumWidth
            # (đặt qua set_container_width) để không tràn quá 50%.
            self.setSizePolicy(
                QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum
            )

    def set_text(self, text: str) -> None:
        """
        Set toàn bộ nội dung 1 lần — dùng khi load lại lịch sử cũ từ
        `ChatDB` (không cần hiệu ứng streaming, hiển thị ngay).
        """
        self._full_text = text
        self._label.setText(text)

    def append_chunk(self, chunk: str) -> None:
        """
        Nối thêm 1 đoạn chữ vào cuối — dùng khi đang stream câu trả
        lời từ Gemini. Gọi hàm này lặp lại nhiều lần (mỗi lần 1 chunk
        từ `on_chunk` callback của `ChatLogic`) sẽ tạo hiệu ứng gõ chữ,
        vì mỗi lần gọi Qt vẽ lại label với nội dung dài hơn 1 chút.
        """
        self._full_text += chunk
        self._label.setText(self._full_text)

    def text(self) -> str:
        """Lấy lại toàn bộ nội dung hiện tại (vd để debug/test)."""
        return self._full_text

    def set_container_width(self, container_width: int) -> None:
        """
        Gọi bởi `ChatArea` mỗi khi chiều rộng khung chat thay đổi
        (user resize cửa sổ). Tính lại max-width TƯƠNG ĐỐI thay vì
        dùng 1 số pixel cố định — đây chính là điểm sửa bug "một chữ
        một dòng" đã giải thích ở đầu file.
        """
        if self.role == "user":
            self.setMaximumWidth(int(container_width * USER_BUBBLE_MAX_WIDTH_RATIO))
        else:
            self.setMaximumWidth(container_width)