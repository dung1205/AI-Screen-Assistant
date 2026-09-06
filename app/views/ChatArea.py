"""
ChatArea.py
------------
Khung hiển thị TOÀN BỘ cuộc trò chuyện — xếp chồng nhiều `MessageBubble`
theo chiều dọc, có thanh cuộn, và 1 chỉ báo "đang gõ" khi chờ Gemini
trả lời. `AppController` gọi vào đây mỗi khi có tin nhắn mới hoặc khi
user chuyển sang xem 1 conversation khác trong Sidebar.

3 KHÁI NIỆM PyQt6 QUAN TRỌNG CẦN HIỂU TRƯỚC:

1) `QScrollArea` + `setWidgetResizable(True)`
   - `QScrollArea` tự nó KHÔNG chứa nội dung trực tiếp — nó là 1
     "khung nhìn" (viewport) bọc quanh 1 widget con (`setWidget()`).
     Nếu widget con cao hơn viewport, `QScrollArea` tự hiện thanh cuộn.
   - `setWidgetResizable(True)` là điều BẮT BUỘC phải bật: nếu không,
     widget con giữ nguyên kích thước mặc định của nó và không tự giãn
     theo chiều rộng của `QScrollArea` khi cửa sổ resize — bubble sẽ
     không đổi max-width theo cửa sổ (chính là bug "một chữ một dòng"
     đã sửa ở `MessageBubble`, nhưng ở tầng khác: nếu quên bật cờ này,
     `set_container_width()` dù đúng logic vẫn không có tác dụng vì
     toàn bộ khung chat không co giãn theo cửa sổ).

2) "SMART AUTO-SCROLL" — tự cuộn xuống cuối, NHƯNG chỉ khi hợp lý
   - Hành vi đúng của 1 app chat: khi có tin nhắn mới, tự cuộn xuống
     cuối để user thấy ngay — GIỐNG ChatGPT/Messenger.
   - NHƯNG nếu user đang cuộn lên đọc lại tin nhắn cũ (vd đọc lại câu
     trả lời từ 10 phút trước), mà lúc đó có tin nhắn mới tới (hoặc
     đang stream câu trả lời hiện tại), app KHÔNG được tự kéo màn hình
     xuống cuối — sẽ làm user đang đọc bị "giật" mất vị trí, rất khó
     chịu.
   - Giải pháp: TRƯỚC khi thêm nội dung mới, kiểm tra "user có đang ở
     gần cuối khung chat không" (`_is_near_bottom()`). Nếu có -> tự
     cuộn xuống sau khi thêm. Nếu không (user đang cuộn lên đọc cũ) ->
     giữ nguyên vị trí cuộn, không động vào.

3) TẠI SAO CUỘN XUỐNG CUỐI BẰNG CÁCH LẮNG NGHE TÍN HIỆU
   `scrollBar().rangeChanged`, THAY VÌ GỌI `setValue(maximum())` NGAY?
   - Khi vừa `addWidget()` 1 bubble mới (nhất là bubble nhiều dòng, cần
     word-wrap), Qt KHÔNG tính xong chiều cao thật của nó trong 1 lượt
     xử lý sự kiện duy nhất — với label nhiều dòng, Qt có thể cần VÀI
     lượt tính lại layout (đo độ rộng khả dụng -> word-wrap theo độ
     rộng đó -> tính chiều cao theo số dòng đã wrap -> layout cha lại
     tính lại lần nữa...) trước khi `scrollBar().maximum()` phản ánh
     ĐÚNG kích thước nội dung mới.
   - Nếu chỉ trì hoãn 1 lần cố định (vd `QTimer.singleShot(0, ...)`)
     rồi cuộn ngay, có thể vẫn cuộn tới giá trị `maximum()` CŨ (vì
     layout chưa ổn định xong) -> thiếu vài chục pixel, tin nhắn mới
     nhất bị che 1 phần.
   - Cách ĐÚNG: lắng nghe tín hiệu `rangeChanged(min, max)` — tín hiệu
     Qt tự bắn ra MỖI KHI `maximum()` thực sự đổi giá trị, bất kể mất
     bao nhiêu lượt layout để ổn định. Mỗi lần tín hiệu bắn, cuộn tới
     `max` mới nhất; sau 1 khoảng thời gian ngắn không còn thay đổi
     nữa thì ngắt kết nối lắng nghe (tránh listener sống mãi, ảnh
     hưởng những lần cuộn sau).
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.ChatDB import Message
from app.views.MessageBubble import MessageBubble

# Ngưỡng (pixel) để coi là "đang ở gần cuối" khung chat — không bắt
# buộc phải chạm ĐÚNG pixel cuối cùng, vì thanh cuộn có thể lệch vài
# pixel do làm tròn kích thước layout.
_NEAR_BOTTOM_THRESHOLD_PX = 40


class _TypingIndicator(QLabel):
    """
    Chỉ báo "đang gõ" — 1 QLabel nội bộ, KHÔNG export ra ngoài file này
    (không import được từ module khác) vì nó chỉ là chi tiết cài đặt
    riêng của `ChatArea`, không phải component dùng chung như
    `MessageBubble`.

    Hiệu ứng: text đổi giữa "." / ".." / "..." mỗi 400ms bằng QTimer,
    tạo cảm giác "đang suy nghĩ" trong lúc chờ Gemini trả về chunk đầu
    tiên (khoảng thời gian giữa lúc gửi request và lúc nhận chunk đầu
    có thể mất 1-2 giây, để trống hoàn toàn sẽ khiến user tưởng app bị
    treo).
    """

    _FRAMES = ["⬤◯◯", "◯⬤◯", "◯◯⬤"]
    _INTERVAL_MS = 400

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("color: #888; padding: 10px 14px;")
        self._frame_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_frame)

    def start(self) -> None:
        self._frame_index = 0
        self.setText(self._FRAMES[0])
        self._timer.start(self._INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()

    def _advance_frame(self) -> None:
        self._frame_index = (self._frame_index + 1) % len(self._FRAMES)
        self.setText(self._FRAMES[self._frame_index])


class ChatArea(QWidget):
    """API công khai mà `AppController` gọi vào."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._bubbles: list[MessageBubble] = []

        # --- Nội dung cuộn được: 1 widget chứa layout dọc xếp bubble ---
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 16, 16, 16)
        self._content_layout.setSpacing(12)
        # addStretch() cuối cùng: đẩy toàn bộ bubble lên phía TRÊN khi
        # số lượng tin nhắn còn ít (chưa đủ lấp đầy khung chat) — nếu
        # không có stretch, layout sẽ canh giữa theo chiều dọc, nhìn
        # rất kỳ khi mới mở 1 conversation có 1-2 tin nhắn.
        self._content_layout.addStretch(1)

        self._typing_indicator = _TypingIndicator(self._content)
        self._typing_indicator.hide()

        # --- QScrollArea bọc ngoài ---
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidget(self._content)
        # Bắt buộc bật — xem giải thích khái niệm 1 ở đầu file.
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self._scroll_area)

    # ------------------------------------------------------------------
    # API công khai
    # ------------------------------------------------------------------
    def add_message(self, role: str, text: str = "") -> MessageBubble:
        """
        Thêm 1 bubble mới vào cuối cuộc trò chuyện.

        Dùng khi:
        - User vừa gửi câu hỏi -> `add_message("user", cau_hoi)`.
        - Bắt đầu nhận câu trả lời AI -> `add_message("model", "")`
          (rỗng), rồi gọi `.append_chunk()` trên bubble TRẢ VỀ mỗi khi
          `ChatLogic` gọi `on_chunk` — đây là lý do hàm này TRẢ VỀ
          `MessageBubble` thay vì `None`: caller (AppController) cần
          giữ tham chiếu tới đúng bubble đang stream để cập nhật dần.

        Returns:
            MessageBubble vừa tạo — giữ lại tham chiếu này nếu cần
            append_chunk() sau đó (trường hợp streaming).
        """
        was_near_bottom = self._is_near_bottom()

        bubble = MessageBubble(role=role, text=text)
        bubble.set_container_width(self._container_width())

        # Bubble user canh phải, bubble AI full-width (canh trái mặc
        # định là đủ vì nó đã giãn hết chiều rộng). Dùng layout ngang
        # phụ để đẩy bubble user sang phải mà không cần chỉnh margin
        # thủ công.
        row = self._wrap_in_alignment_row(bubble)

        # Chèn TRƯỚC widget stretch cuối cùng (luôn nằm ở index cuối),
        # để bubble mới luôn xuất hiện ngay phía trên chỉ báo "đang gõ"
        # và phía trên khoảng trống co giãn.
        insert_index = self._content_layout.count() - 1
        self._content_layout.insertWidget(insert_index, row)

        self._bubbles.append(bubble)

        if was_near_bottom:
            self._scroll_to_bottom_deferred()

        return bubble

    def load_history(self, messages: list[Message]) -> None:
        """
        Xoá sạch bubble hiện tại, vẽ lại toàn bộ theo `messages` —
        dùng khi user click 1 conversation khác trong Sidebar (chuyển
        cuộc trò chuyện, không phải đang gửi tin nhắn mới).
        """
        self.clear()
        for msg in messages:
            self.add_message(role=msg.role, text=msg.text)
        # Mở 1 conversation cũ thì mặc định cuộn thẳng xuống tin nhắn
        # mới nhất (giống hành vi mở lại 1 đoạn chat quen thuộc).
        self._scroll_to_bottom_deferred()

    def clear(self) -> None:
        """Xoá toàn bộ bubble hiện tại khỏi khung chat (giữ lại chỉ báo đang gõ + stretch)."""
        for bubble in self._bubbles:
            row = bubble.parentWidget()
            self._content_layout.removeWidget(row)
            row.deleteLater()
        self._bubbles.clear()

    def show_typing_indicator(self) -> None:
        """
        Hiện chỉ báo "đang gõ" — gọi ngay SAU khi lưu câu hỏi user vào
        DB, TRƯỚC khi bắt đầu gọi `ChatLogic.send_message()`, để user
        thấy phản hồi ngay (không phải chờ trong im lặng).
        """
        was_near_bottom = self._is_near_bottom()
        insert_index = self._content_layout.count() - 1
        self._content_layout.insertWidget(insert_index, self._typing_indicator)
        self._typing_indicator.show()
        self._typing_indicator.start()
        if was_near_bottom:
            self._scroll_to_bottom_deferred()

    def hide_typing_indicator(self) -> None:
        """
        Ẩn chỉ báo "đang gõ" — gọi NGAY khi nhận được chunk ĐẦU TIÊN
        từ Gemini (lúc đó mới `add_message("model", "")` để bắt đầu
        stream thật), không phải đợi tới khi stream xong hoàn toàn.
        """
        self._typing_indicator.stop()
        self._typing_indicator.hide()
        self._content_layout.removeWidget(self._typing_indicator)

    def scroll_to_bottom(self) -> None:
        """Cuộn thẳng xuống cuối ngay lập tức, không điều kiện — dùng khi caller CHẮC CHẮN muốn cuộn (vd user vừa tự gửi câu hỏi)."""
        self._scroll_to_bottom_deferred()

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 (tên hàm theo quy ước Qt override)
        """
        Override sự kiện resize của Qt — mỗi khi cửa sổ đổi kích
        thước, tính lại `container_width` và áp dụng cho TẤT CẢ bubble
        đang có, để tỉ lệ max-width 50%/100% luôn đúng (xem giải thích
        trong `MessageBubble.set_container_width`).
        """
        super().resizeEvent(event)
        width = event.size().width()
        for bubble in self._bubbles:
            bubble.set_container_width(width)

    def _container_width(self) -> int:
        """
        Chiều rộng thực tế mà bubble có thể dùng — lấy từ viewport của
        `QScrollArea` (không phải `self.width()`), vì viewport đã trừ
        sẵn phần bị thanh cuộn dọc chiếm chỗ.
        """
        return self._scroll_area.viewport().width()

    def _is_near_bottom(self) -> bool:
        bar = self._scroll_area.verticalScrollBar()
        return bar.value() >= bar.maximum() - _NEAR_BOTTOM_THRESHOLD_PX

    # Khoảng thời gian (ms) tiếp tục lắng nghe rangeChanged sau lần gọi
    # cuộn gần nhất, trước khi tự ngắt kết nối — đủ dài để layout nhiều
    # dòng ổn định qua vài lượt, đủ ngắn để không giữ listener quá lâu.
    _SCROLL_SETTLE_WINDOW_MS = 120

    def _scroll_to_bottom_deferred(self) -> None:
        """Xem giải thích khái niệm 3 ở đầu file (lắng nghe `rangeChanged`)."""
        bar = self._scroll_area.verticalScrollBar()

        def on_range_changed(_min: int, max_: int) -> None:
            bar.setValue(max_)

        bar.rangeChanged.connect(on_range_changed)

        def stop_listening() -> None:
            try:
                bar.rangeChanged.disconnect(on_range_changed)
            except TypeError:
                # Đã bị ngắt kết nối từ trước (vd gọi cuộn liên tiếp
                # nhiều lần rất nhanh) -> bỏ qua, không phải lỗi.
                pass
            # Lần cuối, đảm bảo luôn ở đúng vị trí cuối kể cả khi
            # rangeChanged không bắn thêm lần nào trong lúc chờ.
            bar.setValue(bar.maximum())

        # Cuộn ngay 1 lần đầu (trường hợp layout đã ổn định sẵn từ
        # trước, không cần chờ rangeChanged mới thấy đúng vị trí).
        bar.setValue(bar.maximum())
        QTimer.singleShot(self._SCROLL_SETTLE_WINDOW_MS, stop_listening)

    @staticmethod
    def _wrap_in_alignment_row(bubble: MessageBubble) -> QWidget:
        """
        Bọc 1 bubble trong 1 `QWidget` + `QHBoxLayout` để canh trái/phải:
        - Bubble user: đẩy sang phải bằng 1 `addStretch()` ở BÊN TRÁI.
        - Bubble AI: không cần stretch (đã full-width sẵn), nhưng vẫn
          bọc trong row cho ĐỒNG NHẤT cấu trúc (mọi phần tử trong
          `_content_layout` đều là 1 "row"), giúp `clear()` dễ dàng
          xoá theo đúng 1 loại widget mà không cần phân biệt trường hợp.
        """
        from PyQt6.QtWidgets import QHBoxLayout

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)

        if bubble.role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            bubble.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )

        return row