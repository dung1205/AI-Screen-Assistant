"""
ScreenshotService.py
---------------------
Service duy nhất được phép chụp màn hình. ChatLogic gọi vào đây khi user
tick checkbox "kèm ảnh" trong InputBarView, KHÔNG tự import `mss` ở nơi
khác — nếu sau này đổi thư viện chụp màn hình, chỉ sửa 1 file này.

TẠI SAO DÙNG `mss` MÀ KHÔNG DÙNG `PIL.ImageGrab`?
- `PIL.ImageGrab.grab()` trên Windows có lúc gọi API chụp màn hình kiểu
  cũ, đôi khi kích hoạt hiệu ứng nhấp nháy/animation của hệ điều hành
  (giống khi bấm PrintScreen) — VI PHẠM yêu cầu "chụp không hiệu ứng
  nhấp nháy" đã thống nhất trước đó.
- `mss` đọc trực tiếp dữ liệu pixel từ framebuffer của hệ điều hành
  (dùng API cấp thấp: GDI trên Windows, Quartz trên macOS, XGetImage
  trên Linux) — không mở cửa sổ, không có animation, không có
  "camera shutter" nào cả. Đây là lý do chính project này chọn `mss`.
- Lợi ích phụ: `mss` nhanh hơn đáng kể so với `ImageGrab` (quan trọng
  nếu sau này muốn chụp liên tục/preview).

KHÁI NIỆM "MONITOR INDEX" TRONG `mss`:
- `sct.monitors` là 1 danh sách các vùng màn hình mà `mss` nhìn thấy:
    * `monitors[0]`  = vùng ảo bao trọn TẤT CẢ màn hình gộp lại
                        (nếu máy có 2 màn hình, monitor 0 là hình chữ
                        nhật lớn nhất bao trùm cả 2)
    * `monitors[1]`  = màn hình vật lý thứ nhất
    * `monitors[2]`  = màn hình vật lý thứ hai (nếu có)
    * ...
- Yêu cầu ban đầu của app là "chụp toàn màn hình" (không cho chọn vùng)
  -> mặc định dùng `monitors[0]` để chụp được TẤT CẢ màn hình đang có,
  không chỉ màn hình chính. Nếu máy chỉ có 1 màn hình, `monitors[0]`
  và `monitors[1]` cho kết quả giống hệt nhau.

TẠI SAO TRẢ VỀ `bytes` (PNG) THAY VÌ TRẢ VỀ ĐỐI TƯỢNG ẢNH CỦA `mss`?
- Đối tượng `sct.grab()` trả về là kiểu riêng của `mss` (`ScreenShot`),
  chỉ tầng này biết dùng. Nếu trả thẳng object đó ra ngoài, `ChatLogic`
  và `ImageService` buộc phải import `mss` để hiểu nó -> rò rỉ
  (leak) chi tiết cài đặt (implementation detail) ra ngoài tầng service.
- Trả về `bytes` của file PNG chuẩn là 1 "hợp đồng" (interface) trung
  lập: bất kỳ thư viện ảnh nào (Pillow, v.v.) cũng đọc được PNG bytes
  mà không cần biết nó được chụp bằng `mss` hay công cụ nào khác.
  `ImageService` (bước tiếp theo) sẽ nhận đúng `bytes` này để nén/resize.
"""

from __future__ import annotations

import mss
import mss.tools
from mss.exception import ScreenShotError


class ScreenshotServiceError(Exception):
    """Lỗi khi chụp màn hình (không có quyền truy cập màn hình, không có display, v.v.)."""


class ScreenshotService:
    """API công khai mà `ChatLogic` gọi vào."""

    def capture_full_screen(self) -> bytes:
        """
        Chụp toàn bộ màn hình (gộp tất cả monitor nếu có nhiều màn hình),
        trả về dữ liệu ảnh dạng PNG bytes.

        Dùng `with mss.mss() as sct:` (context manager) thay vì tạo
        `mss.mss()` rồi giữ mãi trong `__init__`, vì:
        - Mỗi lần chụp là 1 tác vụ ngắn, độc lập -> mở/đóng ngay trong
          hàm giúp giải phóng tài nguyên hệ thống (screen handle) ngay
          sau khi dùng xong, thay vì giữ 1 handle sống suốt vòng đời app.
        - Nếu giữ 1 instance dùng chung, khi user cắm/rút thêm màn hình
          giữa chừng, danh sách `monitors` cũ có thể không cập nhật;
          mở mới mỗi lần đảm bảo luôn thấy đúng cấu hình màn hình hiện tại.

        Returns:
            bytes: nội dung 1 file .png hợp lệ, sẵn sàng để
            `ImageService` mở ra và nén/resize.

        Raises:
            ScreenshotServiceError: khi không chụp được (vd chạy trên
                môi trường không có màn hình/display, hoặc hệ điều hành
                từ chối quyền chụp màn hình).
        """
        try:
            with mss.mss() as sct:
                # monitors[0] = vùng ảo bao trọn tất cả màn hình
                # (xem giải thích monitor index ở đầu file).
                full_screen_region = sct.monitors[0]
                raw_shot = sct.grab(full_screen_region)

                # `raw_shot` là dữ liệu pixel thô (BGRA). Chuyển sang
                # PNG bytes chuẩn để bất kỳ thư viện ảnh nào cũng đọc
                # được, không phụ thuộc vào kiểu dữ liệu riêng của mss.
                png_bytes = mss.tools.to_png(raw_shot.rgb, raw_shot.size)
                return png_bytes
        except ScreenShotError as exc:
            raise ScreenshotServiceError(
                f"Không thể chụp màn hình: {exc}"
            ) from exc

    def list_monitors(self) -> list[dict]:
        """
        Liệt kê thông tin các màn hình đang có (vị trí, kích thước),
        chủ yếu để debug/log — app hiện tại luôn chụp full screen
        (monitor 0) nên không dùng hàm này trong luồng chính, nhưng
        hữu ích nếu sau này muốn cho user chọn chụp riêng 1 màn hình.
        """
        try:
            with mss.mss() as sct:
                # Bỏ index 0 (vùng gộp), chỉ liệt kê từng màn hình vật lý.
                return list(sct.monitors[1:])
        except ScreenShotError as exc:
            raise ScreenshotServiceError(
                f"Không thể liệt kê màn hình: {exc}"
            ) from exc