"""
ChatDB.py
---------
Service duy nhất được phép chạm vào file SQLite (`data/app.db`).
ChatLogic / Sidebar gọi vào đây để tạo/đọc/sửa/xoá conversation và message,
KHÔNG tự viết câu SQL ở nơi khác -> đổi schema sau này chỉ sửa 1 file.

TẠI SAO DÙNG `sqlite3` (thư viện chuẩn) MÀ KHÔNG DÙNG ORM (SQLAlchemy...)?
- App chỉ chạy local, 1 user, 1 file .db -> không cần ORM lo migration
  đa database, connection pool phức tạp.
- `sqlite3` có sẵn trong Python, không cần thêm dependency, giữ đúng
  tinh thần "Python-only, không cần chạy server riêng" đã thống nhất.

2 KHÁI NIỆM BẢO MẬT/AN TOÀN DỮ LIỆU QUAN TRỌNG TRONG FILE NÀY:

1) PARAMETERIZED QUERY (tham số hoá câu lệnh SQL) — chống SQL Injection
   - SAI:  f"SELECT * FROM messages WHERE text = '{user_input}'"
     Nếu user gõ câu hỏi có chứa dấu nháy đơn (') hoặc cố tình gõ
     "'; DROP TABLE messages; --", câu SQL bị "biến dạng" và có thể
     chạy lệnh nguy hiểm ngoài ý muốn.
   - ĐÚNG: "SELECT * FROM messages WHERE text = ?", (user_input,)
     Dấu `?` là placeholder, sqlite3 tự escape giá trị truyền vào,
     value luôn được hiểu là DỮ LIỆU thuần, không bao giờ là code SQL.
   - Toàn bộ file này dùng placeholder `?`, KHÔNG bao giờ f-string
     giá trị người dùng nhập trực tiếp vào câu SQL.

2) FOREIGN KEY + ON DELETE CASCADE
   - Bảng `messages` có cột `conversation_id` trỏ về `conversations.id`.
   - Khi user xoá 1 conversation, tất cả message thuộc conversation đó
     phải tự động bị xoá theo — nếu không, chúng thành "rác mồ côi"
     (orphan rows) nằm mãi trong DB không ai xoá.
   - SQLite mặc định TẮT foreign key constraint (khác với PostgreSQL/
     MySQL bật sẵn) -> phải tự bật bằng `PRAGMA foreign_keys = ON`
     mỗi khi mở connection, nếu không ON DELETE CASCADE sẽ không chạy.

VỀ DEBOUNCE (search trong Sidebar):
   Debounce (đợi user gõ xong ~300ms rồi mới search, tránh query liên tục
   từng ký tự) là việc của TẦNG UI (SideBarView dùng QTimer), KHÔNG phải
   việc của ChatDB. ChatDB chỉ cung cấp 1 hàm `search_conversations()`
   chạy 1 lần — gọi bao nhiêu lần cũng được, UI tự quyết định khi nào gọi.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    """
    Giờ hiện tại dạng chuỗi ISO-8601 UTC, vd '2026-09-01T10:30:00+00:00'.

    Tại sao lưu string thay vì kiểu DATETIME riêng?
    SQLite không có kiểu ngày-giờ thật sự (nó chỉ có TEXT/INTEGER/REAL/BLOB).
    Lưu chuỗi ISO-8601 là cách chuẩn vì chuỗi này vẫn SẮP XẾP ĐÚNG THỨ TỰ
    THỜI GIAN khi so sánh như string (vd "2026-09-01" < "2026-09-02"),
    nên `ORDER BY updated_at DESC` chạy đúng mà không cần convert kiểu.
    """
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Conversation:
    id: int
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Message:
    id: int
    conversation_id: int
    role: str  # "user" hoặc "model"
    text: str
    has_image: bool
    created_at: str


class ChatDBError(Exception):
    """Lỗi chung khi thao tác với database, để tầng trên bắt riêng thay vì bắt sqlite3.Error trực tiếp."""


class ChatDB:
    """
    API công khai mà ChatLogic / SideBarView gọi vào.
    1 instance ChatDB giữ 1 connection SQLite mở suốt vòng đời app
    (mở 1 lần trong AppController lúc khởi động, không mở/đóng liên tục).
    """

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # row_factory=sqlite3.Row -> mỗi dòng kết quả truy cập được cả
        # theo tên cột (row["title"]) lẫn theo index (row[0]), tiện hơn
        # tuple thường khi map sang dataclass.
        self._conn.row_factory = sqlite3.Row

        # Bật foreign key constraint (xem giải thích ở đầu file).
        self._conn.execute("PRAGMA foreign_keys = ON")

        self._create_tables()

    # ------------------------------------------------------------------
    # Khởi tạo schema
    # ------------------------------------------------------------------
    def _create_tables(self) -> None:
        """
        `CREATE TABLE IF NOT EXISTS` -> chạy hàm này bao nhiêu lần cũng
        an toàn (idempotent): lần đầu tạo bảng, các lần sau thấy bảng
        đã có thì bỏ qua, không báo lỗi. Nhờ vậy có thể gọi vô tư mỗi
        lần app khởi động mà không cần logic "kiểm tra đã tạo chưa".
        """
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role            TEXT NOT NULL CHECK (role IN ('user', 'model')),
                text            TEXT NOT NULL,
                has_image       INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations (id)
                    ON DELETE CASCADE
            )
            """
        )
        # Index cho cột hay dùng để lọc/sắp xếp -> query nhanh hơn khi
        # số message tăng lên nhiều (không cần SQLite quét toàn bảng).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation_id "
            "ON messages (conversation_id, created_at)"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Conversations: create / list / search / rename / delete
    # ------------------------------------------------------------------
    def create_conversation(self, title: str = "Cuộc trò chuyện mới") -> int:
        """Tạo conversation mới (khi user bấm nút "New chat" - icon sparkle). Trả về id vừa tạo."""
        now = _now_iso()
        cur = self._conn.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_conversations(self) -> list[Conversation]:
        """
        Danh sách conversation cho Sidebar, mới nhất (vừa nhắn tin gần
        đây nhất) lên đầu -> đúng hành vi ChatGPT-style quen thuộc.
        """
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_conversation(r) for r in rows]

    def search_conversations(self, keyword: str) -> list[Conversation]:
        """
        Tìm conversation theo tiêu đề (dùng cho ô search ở Sidebar).
        UI tự lo debounce, hàm này chỉ chạy 1 query đơn giản mỗi lần gọi.

        `LIKE ? ` với giá trị `%keyword%` -> tìm chuỗi con ở bất kỳ đâu
        trong title (không phân biệt phải khớp từ đầu).
        """
        keyword = keyword.strip()
        if not keyword:
            return self.list_conversations()

        pattern = f"%{keyword}%"
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE title LIKE ? ORDER BY updated_at DESC",
            (pattern,),
        ).fetchall()
        return [self._row_to_conversation(r) for r in rows]

    def rename_conversation(self, conversation_id: int, new_title: str) -> None:
        new_title = new_title.strip()
        if not new_title:
            raise ChatDBError("Tên cuộc trò chuyện không được để trống")

        self._conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (new_title, _now_iso(), conversation_id),
        )
        self._conn.commit()

    def delete_conversation(self, conversation_id: int) -> None:
        """
        Xoá conversation. Nhờ `ON DELETE CASCADE` khai báo ở bảng messages,
        SQLite tự xoá luôn toàn bộ message thuộc conversation này —
        không cần tự viết thêm câu DELETE FROM messages riêng.
        """
        self._conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Messages: add / list / lấy N lượt gần nhất cho context gửi Gemini
    # ------------------------------------------------------------------
    def add_message(
        self, conversation_id: int, role: str, text: str, has_image: bool = False
    ) -> int:
        """
        Lưu 1 message mới, đồng thời cập nhật `updated_at` của conversation
        cha -> để `list_conversations()` sắp xếp đúng "chat mới nhắn gần
        đây nhất lên đầu".

        role phải là "user" hoặc "model" (khớp CHECK constraint trong
        schema) -> nếu gọi sai giá trị, sqlite3 tự raise IntegrityError,
        mình bọc lại thành ChatDBError cho nhất quán với các hàm khác.
        """
        if role not in ("user", "model"):
            raise ChatDBError(f"role không hợp lệ: {role!r} (chỉ nhận 'user' hoặc 'model')")

        now = _now_iso()
        try:
            cur = self._conn.execute(
                "INSERT INTO messages (conversation_id, role, text, has_image, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, text, int(has_image), now),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise ChatDBError(f"Không thể lưu message: {exc}") from exc

        return cur.lastrowid

    def get_messages(self, conversation_id: int) -> list[Message]:
        """Toàn bộ message của 1 conversation, theo thứ tự thời gian (cũ -> mới), để vẽ lại ChatArea khi mở lại conversation."""
        rows = self._conn.execute(
            "SELECT id, conversation_id, role, text, has_image, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, id ASC",
            (conversation_id,),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def get_recent_turns(self, conversation_id: int, n_pairs: int = 3) -> list[Message]:
        """
        Lấy N lượt hỏi-đáp GẦN NHẤT để làm context gửi cho Gemini
        (đúng quyết định trước đó: mỗi request chỉ gửi 3 turn gần nhất,
        không gửi toàn bộ lịch sử để tiết kiệm token).

        1 "turn" = 1 cặp (user hỏi, model đáp) = 2 message
        -> n_pairs=3 nghĩa là lấy tối đa 6 message gần nhất.

        Cách làm: lấy 2*n_pairs message MỚI NHẤT (ORDER BY ... DESC LIMIT),
        rồi đảo ngược lại thành thứ tự cũ -> mới, vì Gemini cần nhận
        lịch sử theo đúng trình tự thời gian xảy ra, không phải mới nhất
        trước.
        """
        limit = n_pairs * 2
        rows = self._conn.execute(
            "SELECT id, conversation_id, role, text, has_image, created_at "
            "FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        messages = [self._row_to_message(r) for r in rows]
        messages.reverse()  # đảo lại thành thứ tự cũ -> mới
        return messages

    def close(self) -> None:
        """Đóng connection khi app tắt (gọi trong AppController lúc cleanup)."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            text=row["text"],
            has_image=bool(row["has_image"]),
            created_at=row["created_at"],
        )