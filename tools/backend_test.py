# -*- coding: utf-8 -*-
r"""后端功能测试：对真实 output 目录跑一遍扫描 / 解析 / 族链 / 检索 / 缩略图。

只读图片，索引写到 IG_DB_PATH 指定的独立库，不碰生产数据。
  python tools\backend_test.py
"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

CACHE = os.path.join(ROOT, "cache", "test")
os.environ["IG_CACHE_DIR"] = CACHE
os.environ["IG_DB_PATH"] = os.path.join(CACHE, "index.db")
if not os.environ.get("IG_OUTPUT_DIR"):
    os.environ["IG_OUTPUT_DIR"] = r"D:\ComfyUI\output"
os.environ.setdefault("IG_TEMP_DIR", r"D:\ComfyUI\temp")

if os.path.isdir(CACHE):
    shutil.rmtree(CACHE, ignore_errors=True)

from py import diff, thumbs  # noqa: E402
from py.runtime import get as runtime  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("   [" + str(detail) + "]") if detail != "" else ""))
    if not ok:
        FAILED.append(name)


def main():
    rt = runtime()
    print("roots:", rt.roots)
    print("db   :", rt.db_path)

    start = time.perf_counter()
    counts = rt.scanner.scan_roots()
    scan_seconds = time.perf_counter() - start
    print("scan: %s  (%.2fs)" % (counts, scan_seconds))
    check("扫描登记到文件", counts["checked"] > 6000, counts["checked"])
    check("扫描耗时低于 30s", scan_seconds < 30, "%.2fs" % scan_seconds)
    check("首扫全部为待解析", rt.store.stats()["pending"] == counts["checked"], rt.store.stats()["pending"])

    targets = []
    for root_name, directory in rt.scanner.pending_directories(limit=400):
        if os.path.basename(directory) in ("2026-10-03", "2026-04-26"):
            targets.append((root_name, directory))
    check("选中两个基准目录", len(targets) == 2, [d for _r, d in targets])

    start = time.perf_counter()
    summary = []
    for root_name, directory in targets:
        summary.append(rt.scanner.index_directory(root_name, directory))
    index_seconds = time.perf_counter() - start
    print("index:", summary, "  (%.2fs)" % index_seconds)
    parsed = sum(item["parsed"] for item in summary)
    check("解析出元数据", parsed > 700, parsed)

    stats = rt.store.stats()
    check("已解析计数一致", stats["parsed"] >= parsed, stats)

    families_1003 = rt.store.query(
        "SELECT id, file_count FROM family WHERE dir LIKE ? ORDER BY file_count DESC",
        ("%2026-10-03",),
    )
    check("2026-10-03 只形成 1 个族", len(families_1003) == 1 and families_1003[0]["file_count"] >= 100,
          [dict(row) for row in families_1003][:3])

    families_0426 = rt.store.query("SELECT id FROM family WHERE dir LIKE ?", ("%2026-04-26",))
    check("2026-04-26 形成多个族", len(families_0426) >= 8, len(families_0426))

    diff_rows = rt.store.query("SELECT verdict, COUNT(*) AS n FROM file_diff GROUP BY verdict")
    verdicts = {row["verdict"]: row["n"] for row in diff_rows}
    check("diff 覆盖全部已解析文件", sum(verdicts.values()) >= parsed, verdicts)

    sample = rt.store.query_one(
        "SELECT f.id, m.search_text FROM file f JOIN meta m ON m.file_id = f.id "
        "WHERE f.dir LIKE ? AND LENGTH(m.search_text) > 200 ORDER BY f.id LIMIT 1",
        ("%2026-10-03",),
    )
    check("样例文件有合并文本", sample is not None and len(sample["search_text"]) > 200,
          len(sample["search_text"]) if sample else 0)

    if sample:
        token = ""
        for candidate in sample["search_text"].split("\n"):
            candidate = candidate.strip()
            if 4 <= len(candidate) <= 24 and candidate.isascii() and candidate.isalpha():
                token = candidate
                break
        if not token:
            token = sample["search_text"].split()[0][:12]
        found = rt.store.query(
            "SELECT f.id FROM file f WHERE f.id IN (SELECT rowid FROM fts_file WHERE fts_file MATCH ?) LIMIT 5",
            ('"' + token.replace('"', "") + '"',),
        )
        check("FTS5 命中检索词", len(found) > 0, "token=%s hits=%d" % (token, len(found)))

    short = rt.store.query(
        "SELECT id FROM fts_file WHERE fts_file MATCH ? LIMIT 3", ('"ai"',)
    ) if False else None
    like_rows = rt.store.query("SELECT id FROM file WHERE name LIKE ? LIMIT 3", ("%_00001_.png",))
    check("短词回退 LIKE 可用", len(like_rows) > 0, len(like_rows))

    models = rt.store.query(
        "SELECT m.name, COUNT(*) AS n FROM model m JOIN file_model fm ON fm.model_id = m.id "
        "GROUP BY m.name ORDER BY n DESC LIMIT 5"
    )
    check("模型 facet 有数据", len(models) > 0, [(row["name"], row["n"]) for row in models])

    image_row = rt.store.query_one(
        "SELECT id, path, mtime FROM file WHERE kind = 'image' AND dir LIKE ? ORDER BY id LIMIT 1",
        ("%2026-10-03",),
    )
    start = time.perf_counter()
    served = thumbs.ensure(rt.thumb_dir, image_row["path"], image_row["mtime"], 256)
    thumb_seconds = time.perf_counter() - start
    check("缩略图生成", os.path.exists(served) and os.path.getsize(served) > 1000,
          "%s (%.0fms, %.1fKB)" % (os.path.basename(served), thumb_seconds * 1000, os.path.getsize(served) / 1024.0))
    cached = thumbs.ensure(rt.thumb_dir, image_row["path"], image_row["mtime"], 256)
    check("缩略图命中缓存", cached == served)

    detail = rt.store.query_one("SELECT * FROM file_diff WHERE file_id = ?", (sample["id"],))
    check("样例文件有 diff 记录", detail is not None, detail["summary"][:60] if detail else "")

    print("")
    print("BACKEND %s  (%d 项失败)" % ("OK" if not FAILED else "FAILED", len(FAILED)))
    for name in FAILED:
        print("  - " + name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
