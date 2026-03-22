"""SQLite-backed queue manager with WAL mode for concurrent reads."""

import json
import sqlite3
import threading
from datetime import datetime, date

from config import DB_PATH


class QueueManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._post_id_lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS queue (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id     TEXT NOT NULL,
                    category    TEXT NOT NULL,
                    post_type   TEXT NOT NULL,
                    theme       TEXT NOT NULL,
                    prompt      TEXT NOT NULL,
                    caption     TEXT,
                    hashtags    TEXT,
                    image_index INTEGER DEFAULT 0,
                    status      TEXT DEFAULT 'pending',
                    priority    INTEGER DEFAULT 0,
                    error       TEXT,
                    output_path TEXT,
                    seed        INTEGER,
                    steps       INTEGER DEFAULT 40,
                    guidance    REAL DEFAULT 4.5,
                    width       INTEGER DEFAULT 1024,
                    height      INTEGER DEFAULT 1024,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at  TIMESTAMP,
                    completed_at TIMESTAMP,
                    brief_json  TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
                CREATE INDEX IF NOT EXISTS idx_queue_post_id ON queue(post_id);
                CREATE INDEX IF NOT EXISTS idx_queue_priority_created
                    ON queue(priority DESC, created_at ASC);

                CREATE TABLE IF NOT EXISTS trends (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    source      TEXT NOT NULL,
                    topic       TEXT NOT NULL,
                    description TEXT,
                    score       REAL,
                    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_trends_source ON trends(source);

                CREATE TABLE IF NOT EXISTS stats (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id     TEXT NOT NULL,
                    category    TEXT NOT NULL,
                    gen_time    REAL,
                    steps       INTEGER,
                    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def _generate_post_id(self, conn: sqlite3.Connection) -> str:
        """Generate a unique post_id like 20260321_001. Must be called under _post_id_lock."""
        today = date.today().strftime("%Y%m%d")
        row = conn.execute(
            "SELECT COUNT(DISTINCT post_id) FROM queue WHERE post_id LIKE ?",
            (f"{today}_%",),
        ).fetchone()
        count = row[0] + 1
        return f"{today}_{count:03d}"

    def add_brief(self, brief: dict) -> list[int]:
        """Add a content brief to the queue. Returns list of inserted row IDs."""
        ids = []
        prompts = brief.get("image_prompts", [])
        if not prompts:
            return ids

        with self._post_id_lock, self._get_conn() as conn:
            post_id = self._generate_post_id(conn)
            for i, prompt in enumerate(prompts):
                cursor = conn.execute(
                    """INSERT INTO queue
                       (post_id, category, post_type, theme, prompt, caption,
                        hashtags, image_index, brief_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        post_id,
                        brief["category"],
                        brief["post_type"],
                        brief["theme"],
                        prompt,
                        brief.get("caption", ""),
                        ",".join(brief.get("hashtags", [])),
                        i,
                        json.dumps(brief),
                    ),
                )
                ids.append(cursor.lastrowid)
        return ids

    def add_custom_item(self, prompt: str, theme: str = "Custom",
                        caption: str = "", hashtags: str = "",
                        width: int = 1024, height: int = 1024,
                        steps: int = 40, guidance: float = 4.5,
                        seed: int | None = None, priority: int = 100) -> int:
        """Add a single custom image to the queue with high priority."""
        with self._post_id_lock, self._get_conn() as conn:
            post_id = self._generate_post_id(conn)
            cursor = conn.execute(
                """INSERT INTO queue
                   (post_id, category, post_type, theme, prompt, caption,
                    hashtags, image_index, priority, steps, guidance, width, height, seed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (post_id, "custom", "single", theme, prompt, caption,
                 hashtags, 0, priority, steps, guidance, width, height, seed),
            )
            return cursor.lastrowid

    def recover_stale_rendering(self) -> int:
        """Reset any items stuck in 'rendering' back to 'pending' (crash recovery)."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE queue SET status = 'pending', started_at = NULL WHERE status = 'rendering'"
            )
            return cursor.rowcount

    def get_next_pending(self) -> dict | None:
        """Get the highest-priority pending item."""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM queue
                   WHERE status = 'pending'
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1""",
            ).fetchone()
        return dict(row) if row else None

    def update_status(self, item_id: int, status: str, **kwargs):
        """Update an item's status and optional fields."""
        sets = ["status = ?"]
        vals = [status]

        if status == "rendering":
            sets.append("started_at = CURRENT_TIMESTAMP")
        elif status in ("complete", "failed"):
            sets.append("completed_at = CURRENT_TIMESTAMP")

        for key in ("output_path", "error", "seed"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                vals.append(kwargs[key])

        vals.append(item_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE queue SET {', '.join(sets)} WHERE id = ?", vals
            )

    def get_queue(self, status: str | None = None) -> list[dict]:
        """Get queue items, optionally filtered by status."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM queue WHERE status = ? ORDER BY priority DESC, created_at ASC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM queue ORDER BY priority DESC, created_at ASC"
                ).fetchall()
        return [dict(r) for r in rows]

    def update_prompt(self, item_id: int, prompt: str) -> bool:
        """Edit a pending item's prompt. Returns False if not pending."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE queue SET prompt = ? WHERE id = ? AND status = 'pending'",
                (prompt, item_id),
            )
        return cursor.rowcount > 0

    def update_item(self, item_id: int, updates: dict) -> bool:
        """Edit multiple fields of a pending item."""
        allowed = {"prompt", "caption", "hashtags", "priority"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False

        sets = [f"{k} = ?" for k in filtered]
        vals = list(filtered.values())
        vals.append(item_id)

        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE queue SET {', '.join(sets)} WHERE id = ? AND status = 'pending'",
                vals,
            )
        return cursor.rowcount > 0

    def delete_item(self, item_id: int) -> bool:
        """Delete a pending item. Returns False if not pending."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM queue WHERE id = ? AND status = 'pending'",
                (item_id,),
            )
        return cursor.rowcount > 0

    def get_completed(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Get completed items grouped by post_id."""
        with self._get_conn() as conn:
            # Get distinct completed post_ids
            post_rows = conn.execute(
                """SELECT DISTINCT post_id FROM queue
                   WHERE status = 'complete'
                   ORDER BY completed_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()

            posts = []
            for pr in post_rows:
                items = conn.execute(
                    "SELECT * FROM queue WHERE post_id = ? ORDER BY image_index",
                    (pr["post_id"],),
                ).fetchall()
                items = [dict(i) for i in items]
                if items:
                    first = items[0]
                    posts.append({
                        "post_id": first["post_id"],
                        "category": first["category"],
                        "post_type": first["post_type"],
                        "theme": first["theme"],
                        "caption": first["caption"],
                        "hashtags": first["hashtags"],
                        "images": [
                            {"path": i["output_path"], "index": i["image_index"]}
                            for i in items
                        ],
                        "completed_at": first["completed_at"],
                    })
        return posts

    def get_post_items(self, post_id: str) -> list[dict]:
        """Get all items for a post_id."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM queue WHERE post_id = ? ORDER BY image_index",
                (post_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def pending_count(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM queue WHERE status = 'pending'"
            ).fetchone()
        return row[0]

    def completed_today_count(self) -> int:
        today = date.today().isoformat()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT post_id) FROM queue WHERE status = 'complete' AND date(completed_at) = ?",
                (today,),
            ).fetchone()
        return row[0]

    def total_count(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
        return row[0]

    def add_stat(self, post_id: str, category: str, gen_time: float, steps: int):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO stats (post_id, category, gen_time, steps) VALUES (?, ?, ?, ?)",
                (post_id, category, gen_time, steps),
            )

    # --- Trends ---

    def save_trends(self, source: str, topics: list[dict]):
        """Replace trends for a source with fresh data."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM trends WHERE source = ?", (source,))
            for t in topics:
                conn.execute(
                    "INSERT INTO trends (source, topic, description, score) VALUES (?, ?, ?, ?)",
                    (source, t["topic"], t.get("description", ""), t.get("score", 0)),
                )

    def get_trends(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trends ORDER BY score DESC, fetched_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trends_last_updated(self) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(fetched_at) FROM trends"
            ).fetchone()
        return row[0] if row else None
