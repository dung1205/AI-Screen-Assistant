"""
ImageService.py
-----------------
Service duy nhất được phép chỉnh sửa ảnh (resize, đổi định dạng, nén).
`ScreenshotService` chụp ảnh thô -> đưa cho `ImageService` xử lý ->
kết quả mới đưa cho `GeminiClient` gửi đi. ChatLogic chỉ điều phối thứ
tự gọi, không tự resize/convert ảnh ở đâu khác.

TẠI SAO PHẢI NÉN/RESIZE ẢNH TRƯỚC KHI GỬI GEMINI? (đây là lý do cốt lõi
của cả file này, cần hiểu trước khi đọc code)

- Gemini không tính phí theo "số byte ảnh", mà tính theo TOKEN. Cách
  Gemini quy đổi ảnh sang token: ảnh được cắt thành các "tile" (ô vuông
  nhỏ ~768x768 pixel), MỖI TILE tốn ~258 token, bất kể tile đó có nhiều
  chi tiết hay không. Ảnh càng lớn (chiều rộng/cao càng nhiều pixel)
  thì càng bị cắt ra nhiều tile -> càng tốn nhiều token -> vừa tốn tiền
  (nếu dùng model trả phí) vừa tăng độ trễ phản hồi.
- Screenshot chụp từ màn hình thật thường rất lớn (vd 2560x1440 hoặc
  3840x2160 nếu màn 4K) — lớn hơn NHIỀU so với mức cần thiết để Gemini
  đọc được chữ/giao diện trên màn hình. Gemini còn tự động giới hạn ảnh
  input ở mức tối đa khoảng 3072x3072 (ảnh lớn hơn tự bị scale xuống
  phía server) -> tự mình resize trước vừa kiểm soát được chất lượng,
  vừa không lãng phí băng thông upload ảnh full-res lên chỉ để server
  scale xuống lại.
- Kết luận thực dụng: resize ảnh xuống còn khoảng 1500-1600px ở cạnh dài
  nhất là đủ để Gemini đọc rõ chữ trên màn hình (kể cả OCR), mà giảm
  đáng kể số tile cần xử lý so với gửi nguyên bản 4K.

TẠI SAO CHUYỂN SANG JPEG THAY VÌ GIỮ PNG?
- PNG là định dạng NÉN KHÔNG MẤT DỮ LIỆU (lossless) -> giữ chi tiết
  tuyệt đối nhưng file nặng, nhất là ảnh chụp màn hình có gradient/
  hiệu ứng mờ (thường gặp trong UI hiện đại).
- JPEG là định dạng NÉN CÓ MẤT DỮ LIỆU (lossy) nhưng cho phép chỉnh mức
  nén (quality 0-100) -> ở quality ~85, mắt thường khó nhận ra khác
  biệt so với bản gốc, nhưng dung lượng giảm rất nhiều so với PNG.
- Vì Gemini tính token theo KÍCH THƯỚC ẢNH (pixel), không phải theo
  dung lượng file, nên việc đổi sang JPEG chủ yếu giúp giảm THỜI GIAN
  UPLOAD (băng thông), còn số token vẫn chủ yếu do bước resize quyết
  định. Cả 2 bước (resize + đổi JPEG) đều cần thiết, giải quyết 2 vấn
  đề khác nhau (token cost vs upload speed).

TẠI SAO PHẢI XỬ LÝ "ALPHA CHANNEL" (kênh trong suốt) TRƯỚC KHI LƯU JPEG?
- Ảnh chụp màn hình (PNG) thường ở chế độ màu RGBA (Red, Green, Blue,
  Alpha) — kênh Alpha quyết định độ trong suốt của từng pixel.
- Định dạng JPEG KHÔNG HỖ TRỢ kênh Alpha (chỉ có RGB). Nếu lưu thẳng
  ảnh RGBA sang JPEG mà không xử lý, thư viện sẽ lỗi hoặc tự động bỏ
  kênh Alpha theo cách không kiểm soát được (có thể ra pixel đen ở
  vùng lẽ ra trong suốt).
- Cách xử lý đúng: tạo 1 nền màu trắng, "dán" (paste) ảnh RGBA lên
  trên nền đó dùng chính kênh Alpha làm mask -> vùng trong suốt sẽ
  hiện ra màu trắng thay vì màu đen ngẫu nhiên. Đây là kỹ thuật gọi
  là "flatten ảnh trong suốt lên nền đặc".
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

# Cạnh dài nhất của ảnh sau khi resize, tính bằng pixel.
# 1568px là mức cân bằng: đủ để Gemini đọc rõ chữ trên màn hình (kể cả
# OCR các đoạn text nhỏ), trong khi giảm đáng kể số tile so với gửi
# nguyên bản 4K/2K (xem giải thích token/tile ở đầu file).
DEFAULT_MAX_DIMENSION = 1568

# Mức nén JPEG (0-100, càng cao càng ít mất chi tiết nhưng file càng
# nặng). 85 là mức "sweet spot" phổ biến: mắt thường khó phân biệt với
# ảnh gốc, nhưng dung lượng giảm mạnh so với quality 100.
DEFAULT_JPEG_QUALITY = 85


class ImageServiceError(Exception):
    """Lỗi khi xử lý ảnh (dữ liệu không phải ảnh hợp lệ, ảnh hỏng...)."""


class ImageService:
    """API công khai mà `ChatLogic` gọi vào."""

    def compress_screenshot(
        self,
        image_bytes: bytes,
        max_dimension: int = DEFAULT_MAX_DIMENSION,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    ) -> bytes:
        """
        Nhận ảnh thô (PNG bytes từ `ScreenshotService`), trả về ảnh đã
        resize + nén dạng JPEG bytes, sẵn sàng gửi cho `GeminiClient`
        (khớp với `image_mime_type="image/jpeg"` mặc định của
        `GeminiClient.stream_message`).

        Args:
            image_bytes: dữ liệu ảnh gốc (PNG hoặc bất kỳ định dạng
                nào Pillow đọc được).
            max_dimension: cạnh dài nhất sau khi resize (pixel).
                Ảnh nhỏ hơn mức này giữ nguyên kích thước, KHÔNG phóng
                to lên (chỉ thu nhỏ, không bao giờ làm giảm chất lượng
                của ảnh vốn đã nhỏ).
            jpeg_quality: mức nén JPEG (0-100).

        Returns:
            bytes: dữ liệu ảnh JPEG đã nén, nhỏ hơn đáng kể so với
            ảnh gốc nhưng vẫn đủ rõ để Gemini đọc nội dung màn hình.

        Raises:
            ImageServiceError: nếu `image_bytes` không phải dữ liệu
                ảnh hợp lệ (file hỏng, hoặc không phải file ảnh).
        """
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img = self._resize_if_needed(img, max_dimension)
                img = self._flatten_to_rgb(img)

                output = io.BytesIO()
                # optimize=True: Pillow thử thêm vài kỹ thuật nén phụ
                # (tối ưu bảng Huffman...) để giảm thêm vài % dung
                # lượng, đổi lại tốn thêm chút thời gian xử lý — chấp
                # nhận được vì ảnh chỉ nén 1 lần trước khi gửi đi.
                img.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
                return output.getvalue()
        except UnidentifiedImageError as exc:
            raise ImageServiceError(
                f"Dữ liệu không phải ảnh hợp lệ: {exc}"
            ) from exc

    @staticmethod
    def _resize_if_needed(img: Image.Image, max_dimension: int) -> Image.Image:
        """
        Thu nhỏ ảnh nếu cạnh dài nhất vượt quá `max_dimension`, GIỮ
        NGUYÊN tỉ lệ khung hình (aspect ratio) — không kéo méo ảnh.

        Dùng `Image.thumbnail()` thay vì `Image.resize()` vì:
        - `thumbnail()` tự tính tỉ lệ resize sao cho ảnh vừa khít
          trong khung `(max_dimension, max_dimension)` mà KHÔNG làm
          méo tỉ lệ gốc — chỉ cần truyền 1 kích thước khung hình vuông,
          không cần tự tính chiều còn lại.
        - `thumbnail()` chỉ THU NHỎ, không bao giờ phóng to — nếu ảnh
          vốn đã nhỏ hơn `max_dimension`, ảnh giữ nguyên, tránh làm
          ảnh bị vỡ nét (upscale) một cách vô ích.
        - `thumbnail()` sửa ảnh "tại chỗ" (in-place) nên cần gọi trên
          copy nếu muốn giữ ảnh gốc — ở đây không cần giữ ảnh gốc nên
          gọi thẳng.
        """
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        return img

    @staticmethod
    def _flatten_to_rgb(img: Image.Image) -> Image.Image:
        """
        Loại bỏ kênh Alpha (nếu có) bằng cách dán ảnh lên nền trắng,
        trả về ảnh chế độ RGB thuần — bắt buộc trước khi lưu JPEG
        (xem giải thích "alpha channel" ở đầu file).
        """
        if img.mode in ("RGBA", "LA", "P"):
            # Mode "P" (palette, ảnh PNG dùng bảng màu) có thể ẩn chứa
            # 1 màu trong suốt -> convert về RGBA trước để chắc chắn
            # lấy đúng kênh Alpha, rồi mới flatten.
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            # Tham số thứ 3 (mask) = chính kênh Alpha của ảnh gốc:
            # pixel càng trong suốt, càng "nhường" cho màu nền trắng.
            background.paste(rgba, mask=rgba.split()[3])
            return background

        if img.mode != "RGB":
            return img.convert("RGB")

        return img