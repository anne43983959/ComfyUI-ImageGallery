"""SQLite 存储层：连接管理、建表、常用读写。

设计要点（对应实施方案 §4 / §19.3）：
- 每线程一个连接（sqlite3 默认不允许跨线程用同一连接），WAL 模式，读不阻塞写；
- 写操作统一走 write() 上下文，串行化并分批提交；
- 全部状态落库，进程内不持有权威状态，便于日后换成独立进程承载索引器。
"""
import os
import re
import sqlite3
import threading
import time


def _regexp(pattern, value) -> bool:
    if value is None:
        return False
    try:
        return re.search(pattern, value, re.IGNORECASE | re.MULTILINE | re.DOTALL) is not None
    except re.error:
        return False

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS file (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        root TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE,
        dir TEXT NOT NULL,
        name TEXT NOT NULL,
        ext TEXT,
        size INTEGER DEFAULT 0,
        mtime REAL DEFAULT 0,
        ctime REAL DEFAULT 0,
        width INTEGER DEFAULT 0,
        height INTEGER DEFAULT 0,
        duration REAL DEFAULT 0,
        kind TEXT DEFAULT 'image',
        state TEXT DEFAULT 'ok',
        meta_state TEXT DEFAULT 'pending',
        node_count INTEGER DEFAULT 0,
        fingerprint TEXT,
        prompt_off INTEGER DEFAULT 0,
        prompt_len INTEGER DEFAULT 0,
        wf_off INTEGER DEFAULT 0,
        wf_len INTEGER DEFAULT 0,
        family_id INTEGER,
        scanned_at REAL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_file_mtime ON file(mtime DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_file_dir ON file(dir, mtime DESC)",
    "CREATE INDEX IF NOT EXISTS idx_file_meta ON file(meta_state, id)",
    "CREATE INDEX IF NOT EXISTS idx_file_family ON file(family_id)",
    """CREATE TABLE IF NOT EXISTS meta (
        file_id INTEGER PRIMARY KEY,
        search_text TEXT DEFAULT '',
        text_hash TEXT DEFAULT '',
        models TEXT DEFAULT '[]',
        source TEXT DEFAULT 'unknown',
        node_types TEXT DEFAULT '[]',
        error TEXT DEFAULT '',
        parsed_at REAL DEFAULT 0
    )""",
    "CREATE TABLE IF NOT EXISTS model (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)",
    "CREATE TABLE IF NOT EXISTS file_model (file_id INTEGER, model_id INTEGER, PRIMARY KEY (file_id, model_id))",
    "CREATE INDEX IF NOT EXISTS idx_file_model_model ON file_model(model_id)",
    """CREATE TABLE IF NOT EXISTS family (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        root TEXT, dir TEXT,
        start_file_id INTEGER, end_file_id INTEGER, rep_file_id INTEGER,
        file_count INTEGER DEFAULT 0, created_at REAL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS file_diff (
        file_id INTEGER PRIMARY KEY,
        prev_file_id INTEGER,
        family_id INTEGER,
        s1 REAL, s2 REAL, s3 REAL, s4 REAL, score REAL,
        verdict TEXT,
        summary TEXT DEFAULT '',
        detail TEXT DEFAULT '{}',
        algo_ver INTEGER DEFAULT 1,
        computed_at REAL DEFAULT 0
    )""",
    "CREATE TABLE IF NOT EXISTS favorite (file_id INTEGER PRIMARY KEY, at REAL)",
    "CREATE TABLE IF NOT EXISTS tag (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, kind TEXT DEFAULT 'custom', count INTEGER DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS file_tag (file_id INTEGER, tag_id INTEGER, PRIMARY KEY (file_id, tag_id))",
    "CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS job (id TEXT PRIMARY KEY, type TEXT, state TEXT, total INTEGER DEFAULT 0, done INTEGER DEFAULT 0, message TEXT DEFAULT '', started_at REAL DEFAULT 0, updated_at REAL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS thumb_stat (file_id INTEGER, tier INTEGER, bytes INTEGER DEFAULT 0, created_at REAL DEFAULT 0, last_hit_at REAL DEFAULT 0, hit_count INTEGER DEFAULT 0, PRIMARY KEY (file_id, tier))",
    "CREATE TABLE IF NOT EXISTS trash_entry (id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT, original_path TEXT, trash_path TEXT, size INTEGER, deleted_at REAL, restored_at REAL)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS fts_file USING fts5(name, search_text, tokenize='trigram')",
]


class Store:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.RLock()
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.setup()

    # ---------- 连接 ----------
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.create_function("regexp", 2, _regexp, deterministic=True)
            self._local.conn = conn
        return conn

    def setup(self) -> None:
        with self._write_lock:
            conn = self.conn()
            for statement in SCHEMA:
                conn.execute(statement)
            for table, column, ddl in (
                ("file", "duration", "ALTER TABLE file ADD COLUMN duration REAL DEFAULT 0"),
            ):
                columns = {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")}
                if column not in columns:
                    conn.execute(ddl)
            conn.commit()

    def write(self):
        return _Write(self)

    # ---------- 查询 ----------
    def query(self, sql: str, args=()) -> list:
        return self.conn().execute(sql, args).fetchall()

    def query_one(self, sql: str, args=()):
        return self.conn().execute(sql, args).fetchone()

    def execute(self, sql: str, args=()) -> None:
        with self._write_lock:
            conn = self.conn()
            conn.execute(sql, args)
            conn.commit()

    def executemany(self, sql: str, rows) -> None:
        with self._write_lock:
            conn = self.conn()
            conn.executemany(sql, rows)
            conn.commit()

    # ---------- 设置 ----------
    def get_setting(self, key: str, default=None):
        row = self.query_one("SELECT value FROM setting WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO setting(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ---------- 任务 ----------
    def job_start(self, job_id: str, job_type: str, total: int = 0, message: str = "") -> None:
        now = time.time()
        self.execute(
            "INSERT INTO job(id, type, state, total, done, message, started_at, updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET state='running', total=excluded.total, done=0, message=excluded.message, updated_at=excluded.updated_at",
            (job_id, job_type, "running", total, 0, message, now, now),
        )

    def job_update(self, job_id: str, done=None, total=None, message=None, state=None) -> None:
        sets = ["updated_at = ?"]
        args = [time.time()]
        if done is not None:
            sets.append("done = ?")
            args.append(int(done))
        if total is not None:
            sets.append("total = ?")
            args.append(int(total))
        if message is not None:
            sets.append("message = ?")
            args.append(message)
        if state is not None:
            sets.append("state = ?")
            args.append(state)
        args.append(job_id)
        self.execute("UPDATE job SET " + ", ".join(sets) + " WHERE id = ?", tuple(args))

    def job_get(self, job_id: str):
        return self.query_one("SELECT * FROM job WHERE id = ?", (job_id,))

    def jobs_recent(self, limit: int = 10) -> list:
        return self.query("SELECT * FROM job ORDER BY updated_at DESC LIMIT ?", (limit,))

    # ---------- 统计 ----------
    def stats(self) -> dict:
        conn = self.conn()
        total = conn.execute("SELECT COUNT(*) AS n FROM file WHERE state='ok'").fetchone()["n"]
        pending = conn.execute("SELECT COUNT(*) AS n FROM file WHERE state='ok' AND meta_state='pending'").fetchone()["n"]
        parsed = conn.execute("SELECT COUNT(*) AS n FROM file WHERE meta_state='done'").fetchone()["n"]
        families = conn.execute("SELECT COUNT(*) AS n FROM family").fetchone()["n"]
        models = conn.execute("SELECT COUNT(*) AS n FROM model").fetchone()["n"]
        return {
            "files": total,
            "pending": pending,
            "parsed": parsed,
            "families": families,
            "models": models,
            "db_bytes": os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
        }

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


class _Write:
    """with store.write() as w: ...  —— 收集语句，退出时一次提交。"""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._ops = []

    def __enter__(self):
        return self

    def execute(self, sql: str, args=()) -> None:
        self._ops.append((sql, args))

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None and self._ops:
            with self._store._write_lock:
                conn = self._store.conn()
                for sql, args in self._ops:
                    conn.execute(sql, args)
                conn.commit()
        self._ops = []
        return False
