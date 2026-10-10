"""进程内运行时：存储、扫描器、后台线程、路径解析。"""
import os
import sys
import time

from . import config, jobs, thumbs
from .scanner import Scanner
from .store import Store

_instance = None


def comfy_root() -> str:
    """插件目录的上级的上级 = custom_nodes；再上一级 = ComfyUI 根目录。"""
    return os.path.dirname(os.path.dirname(config.ROOT))


def resolve_root(name: str) -> str:
    override = os.environ.get("IG_%s_DIR" % name.upper())
    if override:
        return os.path.normpath(override)
    try:
        import folder_paths
        getter = getattr(folder_paths, "get_output_directory" if name == "output" else "get_temp_directory", None)
        if getter is not None:
            return os.path.normpath(getter())
    except Exception:
        pass
    return os.path.normpath(os.path.join(comfy_root(), name))


class Runtime:
    def __init__(self) -> None:
        os.environ.setdefault("IG_SETUP", "1")
        self.db_path = os.environ.get("IG_DB_PATH") or os.path.join(config.CACHE_DIR, "index.db")
        os.makedirs(config.CACHE_DIR, exist_ok=True)
        self.thumb_dir = os.path.join(config.CACHE_DIR, "thumbs")
        os.makedirs(self.thumb_dir, exist_ok=True)
        self.store = Store(self.db_path)
        self.roots = [("output", resolve_root("output")), ("temp", resolve_root("temp"))]
        self.roots_by_name = dict(self.roots)
        self.session_hours = float(self.store.get_setting("session_hours", str(config.SESSION_GAP_HOURS)))
        self.scanner = Scanner(self.store, self.roots, session_hours=self.session_hours)
        self.runner = jobs.JobRunner(self.store, self.scanner, self.thumb_dir)
        self.thumb_bytes_quota = thumbs.DEFAULT_QUOTA_BYTES

    # ---------- 设置 ----------
    def setting(self, key: str, default=None):
        return self.store.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.store.set_setting(key, value)
        if key == "session_hours":
            try:
                self.session_hours = float(value)
                self.scanner.session_hours = self.session_hours
            except ValueError:
                pass

    def draft_keywords(self) -> list:
        raw = self.store.get_setting("draft_keywords", "草稿")
        return [item.strip() for item in raw.split(",") if item.strip()]

    def thumb_quota_bytes(self) -> int:
        try:
            gb = float(self.store.get_setting("thumb_quota_gb", "5") or 5)
        except ValueError:
            gb = 5.0
        return int(max(0.5, gb) * 1024 * 1024 * 1024)

    def note_thumb_hit(self, file_id: int, tier: int, bytes_size: int = 0) -> None:
        try:
            self.store.execute(
                "INSERT INTO thumb_stat(file_id, tier, bytes, created_at, last_hit_at, hit_count) "
                "VALUES(?,?,?,?,?,1) ON CONFLICT(file_id, tier) DO UPDATE SET "
                "last_hit_at = excluded.last_hit_at, hit_count = hit_count + 1",
                (file_id, tier, bytes_size, time.time(), time.time()),
            )
        except Exception:
            pass

    def archive_dir(self) -> str:
        default = os.environ.get("IG_ARCHIVE_DIR") or os.path.join(os.path.dirname(comfy_root()), "ComfyUI-Archive")
        return self.store.get_setting("archive_dir", default)

    def trash_dir(self) -> str:
        return self.store.get_setting("trash_dir", os.path.join(comfy_root(), ".gallery_trash"))

    def allowed_roots(self) -> list:
        paths = [path for _name, path in self.roots]
        paths.append(self.trash_dir())
        return [os.path.realpath(path) for path in paths if path]

    def is_allowed(self, path: str) -> bool:
        if not path:
            return False
        try:
            real = os.path.realpath(path)
        except OSError:
            return False
        for root in self.allowed_roots():
            if real == root or real.startswith(root + os.sep):
                return True
        return False

    def start(self) -> None:
        if not self.runner.is_alive():
            self.runner.start()

    def stop(self) -> None:
        self.runner.stop()


def setup() -> Runtime:
    global _instance
    if _instance is None:
        _instance = Runtime()
    return _instance


def get() -> Runtime:
    return setup()
