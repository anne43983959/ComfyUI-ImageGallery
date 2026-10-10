"""缩略图管道：两档 WebP、md5(path|mtime|tier) 缓存键、按配额回收。

对应实施方案 §11：默认上限 5 GB / 25 万文件，回收到 4.2 GB / 21 万；
按"最后修改时间"做 LRU（命中时 touch 文件 mtime），保护最近 14 天的热数据。
"""
import hashlib
import os
import time

from PIL import Image

TIERS = (256, 512)
PIPELINE_VER = 1
DEFAULT_QUOTA_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_WATERMARK_BYTES = int(4.2 * 1024 * 1024 * 1024)
DEFAULT_QUOTA_FILES = 250000
DEFAULT_WATERMARK_FILES = 210000
PROTECT_SECONDS = 14 * 24 * 3600
SMALL_FILE_BYTES = 64 * 1024


def cache_key(path: str, mtime: float, tier: int) -> str:
    raw = "%s|%.3f|%d|%d" % (path, mtime, tier, PIPELINE_VER)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cache_path(cache_dir: str, key: str, tier: int) -> str:
    return os.path.join(cache_dir, key[:2], key[2:4], "%d_%s.webp" % (tier, key))


def ensure(cache_dir: str, src_path: str, mtime: float, tier: int) -> str:
    """返回可直接发送的文件路径；必要时生成。"""
    key = cache_key(src_path, mtime, tier)
    target = cache_path(cache_dir, key, tier)
    if os.path.exists(target):
        try:
            os.utime(target, None)
        except OSError:
            pass
        return target
    size = os.path.getsize(src_path)
    if size < SMALL_FILE_BYTES and os.path.splitext(src_path)[1].lower() not in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
        return src_path
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.splitext(src_path)[1].lower() in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
        from . import video
        if video.cover(src_path, target, at=1.0, size=tier):
            return target
        raise RuntimeError("video cover failed")
    temp = target + ".tmp"
    with Image.open(src_path) as image:
        image.thumbnail((tier, tier))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        image.save(temp, "WEBP", quality=82, method=4)
    os.replace(temp, target)
    return target


def usage(cache_dir: str) -> dict:
    total_bytes = 0
    total_files = 0
    if not os.path.isdir(cache_dir):
        return {"bytes": 0, "files": 0}
    for root, _dirs, names in os.walk(cache_dir):
        for name in names:
            path = os.path.join(root, name)
            try:
                total_bytes += os.path.getsize(path)
                total_files += 1
            except OSError:
                continue
    return {"bytes": total_bytes, "files": total_files}


def score(age_days: float, hits: int, favorite: bool, tagged: bool) -> float:
    """热数据价值分（实施方案 §11.2）。"""
    import math
    recency = math.exp(-max(0.0, age_days) / 14.0)
    return 0.45 * recency + 0.25 * min(hits / 10.0, 1.0) + (0.20 if favorite else 0.0) + (0.10 if tagged else 0.0)


def cleanup_scored(store, cache_dir: str, quota_bytes: int = DEFAULT_QUOTA_BYTES,
                   watermark_bytes: int = DEFAULT_WATERMARK_BYTES,
                   quota_files: int = DEFAULT_QUOTA_FILES,
                   watermark_files: int = DEFAULT_WATERMARK_FILES) -> dict:
    """按价值分从低到高回收缩略图；收藏与最近 14 天内的文件不动。"""
    current = usage(cache_dir)
    if current["bytes"] <= quota_bytes and current["files"] <= quota_files:
        return {"removed": 0, "bytes": current["bytes"], "files": current["files"], "skipped": True}

    rows = store.query(
        "SELECT t.file_id, t.tier, t.last_hit_at, t.hit_count, f.path, f.mtime, "
        "(SELECT COUNT(*) FROM favorite fa WHERE fa.file_id = t.file_id) AS fav, "
        "(SELECT COUNT(*) FROM file_tag ft WHERE ft.file_id = t.file_id) AS tags "
        "FROM thumb_stat t JOIN file f ON f.id = t.file_id"
    )
    now = time.time()
    entries = []
    for row in rows:
        if row["fav"]:
            continue
        last_touch = row["last_hit_at"] or row["mtime"] or 0
        if now - last_touch < PROTECT_SECONDS:
            continue
        key = cache_key(row["path"], row["mtime"], row["tier"])
        path = cache_path(cache_dir, key, row["tier"])
        if not os.path.exists(path):
            continue
        value = score((now - last_touch) / 86400.0, row["hit_count"] or 0, bool(row["fav"]), bool(row["tags"]))
        entries.append((value, os.path.getsize(path), path))
    entries.sort()

    removed = 0
    removed_bytes = 0
    total_bytes = current["bytes"]
    total_files = current["files"]
    for _value, size, path in entries:
        if total_bytes <= watermark_bytes and total_files <= watermark_files:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        removed += 1
        removed_bytes += size
        total_bytes -= size
        total_files -= 1
    return {"removed": removed, "removed_bytes": removed_bytes, "bytes": total_bytes,
            "files": total_files, "skipped": False}


def cleanup(cache_dir: str, quota_bytes: int = DEFAULT_QUOTA_BYTES,
            watermark_bytes: int = DEFAULT_WATERMARK_BYTES,
            quota_files: int = DEFAULT_QUOTA_FILES,
            watermark_files: int = DEFAULT_WATERMARK_FILES) -> dict:
    """超限则按最旧优先删除，直到回到水位；最近 14 天的文件不动。"""
    current = usage(cache_dir)
    if current["bytes"] <= quota_bytes and current["files"] <= quota_files:
        return {"removed": 0, "bytes": current["bytes"], "files": current["files"]}

    entries = []
    now = time.time()
    for root, _dirs, names in os.walk(cache_dir):
        for name in names:
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if now - stat.st_mtime < PROTECT_SECONDS:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
    entries.sort()

    removed = 0
    removed_bytes = 0
    total_bytes = current["bytes"]
    total_files = current["files"]
    for _mtime, size, path in entries:
        if total_bytes <= watermark_bytes and total_files <= watermark_files:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        removed += 1
        removed_bytes += size
        total_bytes -= size
        total_files -= 1
    return {"removed": removed, "removed_bytes": removed_bytes, "bytes": total_bytes, "files": total_files}
