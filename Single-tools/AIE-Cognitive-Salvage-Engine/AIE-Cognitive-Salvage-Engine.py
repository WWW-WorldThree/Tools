# AIE Cognitive Salvage Engine - Debug Workspace
# ============================================================
# AIE Cognitive Salvage Engine
# ------------------------------------------------------------
# 目的:
# - OpenAIエクスポートの conversations-*.json を一次ソースとして直接読む
# - current_node → parent を辿って active branch だけを復元する
# - 分岐した assistant 生成は表示しない
# - SQLite に格納し、Flet UI で検索・前後文脈参照する
# - Human Trace Mode:
#     検索ヒット → ポインター → 前後文脈を目視人力で辿る
# - Machine Mode:
#     余計な前後文脈を減らし、ヒット中心で機械処理向けに使う
#
# 依存:
#   pip install flet
#
# 備考:
# - 日本語の「単純ヒット」を重視して、主検索は instr(text, ?)>0 を使う
# - FTS5 は補助的に実装（英単語や AND 検索など向け）
# - 32M文字級でも「全文一括描画」はしない
#   -> DB化して、必要部分だけ描画する
# ============================================================

import os
import glob
import json
import sqlite3
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional
from tkinter import Tk, filedialog

import flet as ft


APP_TITLE = "AIE Cognitive Salvage Engine"
DB_NAME = "aie_cognitive_salvage.sqlite3"

# Human Trace Mode の標準前後幅
DEFAULT_CONTEXT_BEFORE = 12
DEFAULT_CONTEXT_AFTER = 18

# 検索結果の最大件数
DEFAULT_SEARCH_LIMIT = 300


# ============================================================
# ファイル選択
# ============================================================

def pick_folder() -> str:
    root = Tk()
    root.withdraw()
    path = filedialog.askdirectory(title="conversations-*.json が入っているフォルダを選択")
    root.destroy()
    return path


# ============================================================
# JSON -> Active Branch 抽出
# ============================================================

def get_conversation_sort_key(conv: Dict[str, Any]) -> float:
    ct = conv.get("create_time")
    if ct:
        return ct

    mapping = conv.get("mapping") or {}
    times = []
    for node in mapping.values():
        msg = node.get("message")
        if not msg:
            continue
        t = msg.get("create_time")
        if t:
            times.append(t)
    return min(times) if times else 0.0


def extract_text_from_message(msg: Dict[str, Any]) -> str:
    content = msg.get("content") or {}
    ctype = content.get("content_type")

    if ctype == "text":
        parts = content.get("parts") or []
        return "\n".join(str(p) for p in parts if p is not None)

    if ctype == "code":
        return str(content.get("text") or "")

    parts = content.get("parts")
    if isinstance(parts, list):
        return "\n".join(str(p) for p in parts if p is not None)

    text = content.get("text")
    if text is not None:
        return str(text)

    return ""


def build_active_branch_node_ids(conv: Dict[str, Any]) -> Tuple[set, List[str]]:
    """
    current_node から parent を辿って、表示すべき active branch を復元
    """
    mapping = conv.get("mapping") or {}
    current_node = conv.get("current_node")

    if current_node and current_node in mapping:
        branch = []
        nid = current_node
        visited = set()

        while nid and nid in mapping and nid not in visited:
            visited.add(nid)
            branch.append(nid)
            nid = mapping[nid].get("parent")

        branch.reverse()
        return set(branch), branch

    # fallback: children が空の葉ノードのうち、最新時刻のものを選ぶ
    leaf_candidates = []
    for nid, node in mapping.items():
        children = node.get("children") or []
        if children:
            continue
        msg = node.get("message")
        t = 0
        if msg:
            t = msg.get("create_time") or 0
        leaf_candidates.append((t, nid))

    if not leaf_candidates:
        return set(), []

    leaf_candidates.sort()
    nid = leaf_candidates[-1][1]

    branch = []
    visited = set()
    while nid and nid in mapping and nid not in visited:
        visited.add(nid)
        branch.append(nid)
        nid = mapping[nid].get("parent")

    branch.reverse()
    return set(branch), branch


def extract_active_messages(conv: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    active branch 上の user / assistant メッセージだけを時系列で返す
    """
    mapping = conv.get("mapping") or {}
    _, active_order = build_active_branch_node_ids(conv)

    rows = []
    seen_msg_ids = set()

    for nid in active_order:
        node = mapping.get(nid) or {}
        msg = node.get("message")
        if not msg:
            continue

        role = (msg.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue

        msg_id = msg.get("id")
        if msg_id and msg_id in seen_msg_ids:
            continue
        if msg_id:
            seen_msg_ids.add(msg_id)

        text = extract_text_from_message(msg)
        if not text.strip():
            continue

        rows.append({
            "role": role,
            "text": text,
            "time": msg.get("create_time") or 0,
        })

    rows.sort(key=lambda x: x["time"])
    return rows


# ============================================================
# SQLite 構築
# ============================================================

def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-200000;")  # 約200MB
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
        conv_id     TEXT PRIMARY KEY,
        title       TEXT,
        sort_time   REAL
    );

    CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        conv_id     TEXT NOT NULL,
        seq         INTEGER NOT NULL,
        role        TEXT NOT NULL,
        ts          REAL,
        text        TEXT NOT NULL,
        FOREIGN KEY (conv_id) REFERENCES conversations(conv_id)
    );

    CREATE INDEX IF NOT EXISTS idx_messages_conv_seq ON messages(conv_id, seq);
    CREATE INDEX IF NOT EXISTS idx_messages_ts       ON messages(ts);
    CREATE INDEX IF NOT EXISTS idx_messages_role     ON messages(role);

    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        text,
        content='messages',
        content_rowid='id'
    );
    """)


def reset_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    DELETE FROM messages_fts;
    DELETE FROM messages;
    DELETE FROM conversations;
    VACUUM;
    """)
    conn.commit()


def load_all_conversations(folder_path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    patterns = [
        os.path.join(folder_path, "conversations-*.json"),
        os.path.join(folder_path, "conversations*.json"),
    ]

    files = []
    for pattern in patterns:
        files = sorted(glob.glob(pattern))
        if files:
            break

    if not files:
        raise FileNotFoundError("conversations-*.json が見つかりませんでした。")

    all_conversations = []
    for fp in files:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(f"想定外JSON形式: {os.path.basename(fp)}")
        all_conversations.extend(data)

    all_conversations.sort(key=get_conversation_sort_key)
    return all_conversations, files


def build_database_from_folder(folder_path: str, db_path: str, progress_cb=None) -> Dict[str, Any]:
    conversations, files = load_all_conversations(folder_path)

    conn = get_db_connection(db_path)
    init_db(conn)
    reset_db(conn)

    cur = conn.cursor()

    conv_count = 0
    msg_count = 0
    char_count = 0

    for idx, conv in enumerate(conversations, start=1):
        conv_id = conv.get("id") or conv.get("conversation_id") or f"conv_{idx}"
        title = conv.get("title") or "Untitled Conversation"
        sort_time = get_conversation_sort_key(conv)

        cur.execute(
            "INSERT OR REPLACE INTO conversations (conv_id, title, sort_time) VALUES (?, ?, ?)",
            (conv_id, title, sort_time)
        )

        rows = extract_active_messages(conv)
        for seq, row in enumerate(rows, start=1):
            cur.execute(
                "INSERT INTO messages (conv_id, seq, role, ts, text) VALUES (?, ?, ?, ?, ?)",
                (conv_id, seq, row["role"], row["time"], row["text"])
            )
            rowid = cur.lastrowid
            cur.execute(
                "INSERT INTO messages_fts (rowid, text) VALUES (?, ?)",
                (rowid, row["text"])
            )

            msg_count += 1
            char_count += len(row["text"])

        conv_count += 1

        if idx % 100 == 0:
            conn.commit()
            if progress_cb:
                progress_cb(f"DB構築中... {idx}/{len(conversations)} conversations")

    conn.commit()

    stats = {
        "conv_count": conv_count,
        "msg_count": msg_count,
        "char_count": char_count,
        "files": files,
        "db_path": db_path,
    }

    conn.close()
    return stats


# ============================================================
# 検索
# ============================================================

def snippet_text(text: str, keyword: str, radius: int = 55) -> str:
    if not text:
        return ""
    if not keyword:
        return text[: radius * 2]

    pos = text.find(keyword)
    if pos < 0:
        return text[: radius * 2]

    start = max(0, pos - radius)
    end = min(len(text), pos + len(keyword) + radius)
    s = text[start:end].replace("\n", " ")
    if start > 0:
        s = "..." + s
    if end < len(text):
        s = s + "..."
    return s


def search_direct(conn: sqlite3.Connection, keyword: str, limit: int = DEFAULT_SEARCH_LIMIT) -> List[sqlite3.Row]:
    sql = """
    SELECT
        m.id,
        m.conv_id,
        c.title,
        m.seq,
        m.role,
        m.ts,
        m.text
    FROM messages m
    JOIN conversations c ON c.conv_id = m.conv_id
    WHERE instr(m.text, ?) > 0
    ORDER BY m.ts DESC
    LIMIT ?
    """
    return conn.execute(sql, (keyword, limit)).fetchall()


def search_fts(conn: sqlite3.Connection, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> List[sqlite3.Row]:
    sql = """
    SELECT
        m.id,
        m.conv_id,
        c.title,
        m.seq,
        m.role,
        m.ts,
        m.text
    FROM messages_fts f
    JOIN messages m      ON m.id = f.rowid
    JOIN conversations c ON c.conv_id = m.conv_id
    WHERE messages_fts MATCH ?
    ORDER BY bm25(messages_fts), m.ts DESC
    LIMIT ?
    """
    return conn.execute(sql, (query, limit)).fetchall()


def fetch_context(
    conn: sqlite3.Connection,
    conv_id: str,
    center_seq: int,
    before: int,
    after: int
) -> List[sqlite3.Row]:
    start_seq = max(1, center_seq - before)
    end_seq = center_seq + after

    sql = """
    SELECT
        id,
        conv_id,
        seq,
        role,
        ts,
        text
    FROM messages
    WHERE conv_id = ?
      AND seq BETWEEN ? AND ?
    ORDER BY seq ASC
    """
    return conn.execute(sql, (conv_id, start_seq, end_seq)).fetchall()


def fetch_hit_only(conn: sqlite3.Connection, msg_id: int) -> Optional[sqlite3.Row]:
    sql = """
    SELECT
        id,
        conv_id,
        seq,
        role,
        ts,
        text
    FROM messages
    WHERE id = ?
    """
    return conn.execute(sql, (msg_id,)).fetchone()


def fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


# ============================================================
# Flet UI (最新仕様 0.82.0 準拠版)
# ============================================================

class SalvageApp:
    def __init__(self, page: ft.Page, conn: sqlite3.Connection, stats: Dict[str, Any]):
        self.page = page
        self.conn = conn
        self.stats = stats

        self.page.title = APP_TITLE
        self.page.window.width = 1600
        self.page.window.height = 950
        self.page.padding = 12
        self.page.theme_mode = ft.ThemeMode.LIGHT

        # state
        self.current_results: List[sqlite3.Row] = []
        self.current_keyword: str = ""

        # widgets
        self.query_field = ft.TextField(
            label="検索ワード / クエリ",
            hint_text="例: CST / Fractal / 特許 / cue / CST AND memory",
            autofocus=True,
            expand=True,
            on_submit=self.on_search,
        )

        self.direct_mode_checkbox = ft.Checkbox(
            label="単純ヒット（instr / 文字列一致）",
            value=True,
        )

        self.human_trace_checkbox = ft.Checkbox(
            label="Human Trace Mode（前後文脈を広めに表示）",
            value=True,
            tooltip="圧縮言語・指示語・省略に備えて、ヒットの前後を人力参照するモード",
        )

        self.context_before_field = ft.TextField(
            label="前",
            value=str(DEFAULT_CONTEXT_BEFORE),
            width=80,
            text_align=ft.TextAlign.RIGHT,
        )

        self.context_after_field = ft.TextField(
            label="後",
            value=str(DEFAULT_CONTEXT_AFTER),
            width=80,
            text_align=ft.TextAlign.RIGHT,
        )

        self.limit_field = ft.TextField(
            label="上限",
            value=str(DEFAULT_SEARCH_LIMIT),
            width=100,
            text_align=ft.TextAlign.RIGHT,
        )

        self.search_btn = ft.Button("検索", on_click=self.on_search) # ElevatedButton -> Button
        self.clear_btn = ft.OutlinedButton("クリア", on_click=self.on_clear)

        self.status_text = ft.Text(
            f"DB構築済: {stats['conv_count']} conversations / {stats['msg_count']} messages / {stats['char_count']:,} chars",
            selectable=True,
            size=12,
        )

        self.results_list = ft.ListView(
            expand=True,
            spacing=6,
            auto_scroll=False,
        )

        self.chat_list = ft.ListView(
            expand=True,
            spacing=10,
            auto_scroll=False,
        )

        self.chat_header = ft.Text("右側に前後文脈が表示されます。", size=13)

        self.page.add(self.build_layout())

    def build_layout(self):
        vw = self.page.width or 1400
        title_size = 18 if vw < 900 else 20 if vw < 1200 else 24
        header_size = 16 if vw < 900 else 18
        body_size = 12 if vw < 900 else 13 if vw < 1200 else 14
        query_width = max(220, int(vw * 0.20))
        small_field_width = 50 if vw < 900 else 58 if vw < 1200 else 68
        limit_field_width = 55 if vw < 900 else 64 if vw < 1200 else 76

        self.status_text.size = body_size
        self.query_field.text_size = body_size + 2
        field_text_size = 11 if vw < 900 else 12 if vw < 1200 else 13
        self.context_before_field.text_size = field_text_size
        self.context_after_field.text_size = field_text_size
        self.limit_field.text_size = field_text_size

        left_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(APP_TITLE, size=title_size, weight=ft.FontWeight.BOLD),
                    self.status_text,
                    ft.Container(content=self.query_field, width=query_width),
                    ft.Row(
                        controls=[
                            self.search_btn,
                            self.clear_btn,
                        ],
                        spacing=12,
                        wrap=True,
                    ),
                    self.direct_mode_checkbox,
                    self.human_trace_checkbox,
                    ft.Row(
                        controls=[
                            ft.Container(content=self.context_before_field, width=small_field_width),
                            ft.Container(content=self.context_after_field, width=small_field_width),
                            ft.Container(content=self.limit_field, width=limit_field_width),
                        ],
                        spacing=12,
                        wrap=True,
                    ),
                    ft.Divider(),
                    ft.Text("検索結果", size=body_size + 2, weight=ft.FontWeight.BOLD),
                    ft.Container(content=self.results_list, expand=True),
                ],
                expand=True,
                spacing=8,
            ),
            padding=10,
            border=ft.border.all(1, ft.Colors.GREY_300),
            border_radius=10,
            expand=22,
        )

        right_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("文脈ビュー", size=header_size, weight=ft.FontWeight.BOLD),
                    self.chat_header,
                    ft.Divider(),
                    self.chat_list,
                ],
                expand=True,
                spacing=8,
            ),
            padding=10,
            border=ft.border.all(1, ft.Colors.GREY_300),
            border_radius=10,
            expand=78,
        )

        return ft.Row(
            controls=[left_panel, right_panel],
            expand=True,
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def on_clear(self, e):
        self.query_field.value = ""
        self.results_list.controls.clear()
        self.chat_list.controls.clear()
        self.current_results = []
        self.current_keyword = ""
        self.chat_header.value = "右側に前後文脈が表示されます。"
        self.page.update()

    def get_limit(self) -> int:
        try:
            n = int(self.limit_field.value)
            return max(1, min(5000, n))
        except Exception:
            return DEFAULT_SEARCH_LIMIT

    def get_context_window(self) -> Tuple[int, int]:
        if not self.human_trace_checkbox.value:
            return 0, 0
        try:
            before = max(0, min(500, int(self.context_before_field.value)))
        except Exception:
            before = DEFAULT_CONTEXT_BEFORE
        try:
            after = max(0, min(500, int(self.context_after_field.value)))
        except Exception:
            after = DEFAULT_CONTEXT_AFTER
        return before, after

    def on_search(self, e):
        query = (self.query_field.value or "").strip()
        if not query:
            return
        self.current_keyword = query
        self.results_list.controls.clear()
        self.chat_list.controls.clear()
        self.chat_header.value = "検索中..."
        self.page.update()
        limit = self.get_limit()
        try:
            if self.direct_mode_checkbox.value:
                rows = search_direct(self.conn, query, limit=limit)
            else:
                rows = search_fts(self.conn, query, limit=limit)
            self.current_results = rows
            self.populate_results(rows, query)
            self.chat_header.value = f"検索結果: {len(rows)}件"
        except Exception as ex:
            self.chat_header.value = f"検索エラー: {ex}"
        self.page.update()

    def populate_results(self, rows: List[sqlite3.Row], keyword: str):
        self.results_list.controls.clear()
        if not rows:
            self.results_list.controls.append(ft.Text("ヒットなし"))
            return
        for row in rows:
            title = row["title"] or "Untitled"
            seq = row["seq"]
            role = row["role"]
            ts = fmt_ts(row["ts"])
            snippet = snippet_text(row["text"], keyword)
            badge_color = ft.Colors.GREEN_300 if role == "user" else ft.Colors.BLUE_200
            badge_label = "YOU" if role == "user" else "AI"
            
            # カードクリックイベントを確実に登録
            item = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Container(
                                    content=ft.Text(badge_label, size=11, weight=ft.FontWeight.BOLD),
                                    bgcolor=badge_color,
                                    padding=ft.Padding(8, 3, 8, 3), # Padding.symmetric
                                    border_radius=12,
                                ),
                                ft.Text(f"{title}  |  seq:{seq}  |  {ts}", size=12, selectable=True),
                            ],
                            spacing=8,
                        ),
                        ft.Text(snippet, size=13, selectable=True),
                    ],
                    spacing=5,
                ),
                padding=10,
                border=ft.Border.all(1, ft.Colors.GREY_300),
                border_radius=10,
                on_click=lambda ev, rid=row["id"], cid=row["conv_id"], s=row["seq"], t=title: self.show_context(rid, cid, s, t),
            )
            self.results_list.controls.append(item)

    def build_bubble(self, role: str, dt: str, text: str, is_hit: bool = False):
        is_user = role == "user"
        border_color = ft.Colors.AMBER_500 if is_hit else ft.Colors.GREY_300
        label = "YOU" if is_user else "AI"

        if is_user:
            bubble = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text(f"{label} | {dt}", size=10, color=ft.Colors.BLACK54, selectable=True),
                        ft.Text(
                            text,
                            selectable=True,
                            size=14,
                            no_wrap=False,
                            max_lines=None,
                            width=320,
                        ),
                    ],
                    tight=True,
                    spacing=4,
                ),
                bgcolor=ft.Colors.LIGHT_GREEN_300,
                padding=12,
                border_radius=16,
                border=ft.Border.all(2 if is_hit else 1, border_color),
                width=360,
                expand=False,
            )
            return ft.Row(
                controls=[bubble],
                alignment=ft.MainAxisAlignment.END,
            )

        bubble = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(f"{label} | {dt}", size=10, color=ft.Colors.BLACK54, selectable=True),
                    ft.Text(
                        text,
                        selectable=True,
                        size=14,
                        no_wrap=False,
                        max_lines=None,
                    ),
                ],
                tight=True,
                spacing=4,
            ),
            bgcolor=ft.Colors.WHITE,
            padding=12,
            border_radius=12,
            border=ft.Border.all(2 if is_hit else 1, border_color),
            expand=True,
        )

        return ft.Row(
            controls=[bubble],
            alignment=ft.MainAxisAlignment.START,
            expand=True,
        )

    def show_context(self, msg_id: int, conv_id: str, seq: int, title: str):
        self.chat_list.controls.clear()
        try:
            before, after = self.get_context_window()
            if before == 0 and after == 0:
                row = fetch_hit_only(self.conn, msg_id)
                rows = [row] if row else []
                self.chat_header.value = f"{title} | hit only"
            else:
                rows = fetch_context(self.conn, conv_id, seq, before, after)
                self.chat_header.value = f"{title} | context {before} before / {after} after"

            for row in rows:
                if row is None:
                    continue
                is_hit = row["id"] == msg_id
                self.chat_list.controls.append(
                    self.build_bubble(
                        role=row["role"],
                        dt=fmt_ts(row["ts"]),
                        text=row["text"],
                        is_hit=is_hit,
                    )
                )

            if not rows:
                self.chat_list.controls.append(ft.Text("表示対象の文脈がありません。"))
        except Exception as ex:
            self.chat_header.value = f"文脈表示エラー: {ex}"
            self.chat_list.controls.append(
                ft.Text(f"文脈表示エラー: {ex}", color=ft.Colors.RED_600, selectable=True)
            )
        self.page.update()

def main(page: ft.Page):
    folder = pick_folder()
    if not folder:
        page.add(ft.Text("フォルダが選択されませんでした。"))
        return
    db_path = os.path.join(folder, DB_NAME)
    page.add(ft.Text("DB読み込み中..."))
    page.update()
    
    # DBが存在しない場合のみ構築
    if not os.path.exists(db_path):
        def progress_cb(msg: str):
            page.controls.clear()
            page.add(ft.Text(msg))
            page.update()
        stats = build_database_from_folder(folder, db_path, progress_cb=progress_cb)
    else:
        # 既存DBから統計を取得
        conn = get_db_connection(db_path)
        c = conn.cursor()
        conv_c = c.execute("SELECT count(*) FROM conversations").fetchone()[0]
        msg_c = c.execute("SELECT count(*) FROM messages").fetchone()[0]
        char_c = c.execute("SELECT sum(length(text)) FROM messages").fetchone()[0]
        stats = {"conv_count": conv_c, "msg_count": msg_c, "char_count": char_c}
        conn.close()

    page.controls.clear()
    conn = get_db_connection(db_path)
    SalvageApp(page, conn, stats)
    page.update()

if __name__ == "__main__":
    ft.run(main)
