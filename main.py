"""
main.py
--------
Điểm khởi động duy nhất của app — chạy `python main.py` để mở ứng dụng.

File này CỐ TÌNH chỉ có vài dòng, không chứa logic gì cả. Đây là quy
tắc chung cho mọi app: file `main`/`entry point` chỉ làm 3 việc
- khởi tạo môi trường chạy (`QApplication`), tạo cửa sổ chính
(`AppController`, đã tự lắp ráp toàn bộ service + view bên trong nó),
hiện cửa sổ lên, và chạy vòng lặp sự kiện — KHÔNG viết thêm bất kỳ
nghiệp vụ nào ở đây. Nếu sau này cần thêm bước khởi động (vd kiểm tra
cập nhật, đọc file cấu hình...), thêm ở đây nhưng vẫn chỉ là GỌI vào
các hàm/class đã có, không viết logic mới ngay trong file này.

`sys.exit(app.exec())` — TẠI SAO PHẢI BỌC `sys.exit()` QUANH `app.exec()`?
- `app.exec()` là vòng lặp sự kiện chính của Qt — hàm này CHẠY MÃI cho
  tới khi user đóng cửa sổ (hoặc code gọi `app.quit()`), lúc đó nó mới
  return về 1 mã số nguyên (exit code: 0 nếu thoát bình thường, khác 0
  nếu có lỗi).
- Nếu chỉ gọi `app.exec()` mà không `sys.exit()`, script Python vẫn
  "coi như" chạy xong thành công (exit code luôn là 0 mặc định), DÙ
  Qt báo lỗi khác 0. Bọc `sys.exit(app.exec())` đảm bảo mã lỗi thật
  của Qt được truyền ra ngoài hệ điều hành/terminal — quan trọng khi
  đóng gói thành `.exe`: nếu app crash, công cụ theo dõi lỗi (hoặc
  đơn giản là terminal) mới biết đúng là có lỗi, không phải "chạy xong
  bình thường".

`if __name__ == "__main__":` — TẠI SAO CẦN DÒNG NÀY?
- Đảm bảo đoạn code khởi động app CHỈ chạy khi file này được chạy TRỰC
  TIẾP (`python main.py`), KHÔNG chạy khi file này bị `import` từ nơi
  khác (vd 1 file test import thử `main.py` để kiểm tra gì đó — nếu
  không có dòng này, `import main` sẽ vô tình MỞ CẢ CỬA SỔ APP ngay
  giữa lúc chạy test, rất khó chịu).
"""

import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from PyQt6.QtGui import QIcon

from app.AppController import AppController


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AI Screen Assistant")
    app.setWindowIcon(QIcon("assets/icons/app.png"))
    app.setStyle("Fusion")
    app.setFont(QFont("SF Pro Display", 10))

    window = AppController()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())