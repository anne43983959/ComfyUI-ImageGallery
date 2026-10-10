"""阶段五验收：QoS 四档阶梯、缩略图配额回收、十万张规模、双语摘要。

只在本文件的沙箱目录（cache/accept）里造数据，不碰真实图库。
"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WORK = os.path.join(ROOT, "cache", "accept")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)

from py import diff, jobs, thumbs  # noqa: E402
from py.store import Store  # noqa: E402

FAILED = []
PASSED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS " if ok else "FAIL ") + name + ("   [" + str(detail) + "]" if detail != "" else ""))


# ---------------- 1. QoS 四档阶梯 + AIMD ----------------
def test_qos():
    store = Store(os.path.join(WORK, "qos.db"))
    runner = jobs.JobRunner(store, None, WORK)
    original = jobs.generating
    cases = [
        (False, 0, jobs.DUTY_IDLE_NO_VIEWER, "空闲无观众"),
        (False, 1, jobs.DUTY_IDLE_VIEWER, "空闲有观众"),
        (True, 1, jobs.DUTY_GENERATING_VIEWER, "生成中有观众"),
        (True, 0, jobs.DUTY_GENERATING_NO_VIEWER, "生成中无观众"),
    ]
    for busy, viewers, expected, label in cases:
        jobs.generating = (lambda flag: (lambda: flag))(busy)
        runner._viewers.clear()
        if viewers:
            runner.heartbeat("viewer-1")
        runner._latencies = []
        runner._extra_sleep = 0.0
        value = runner.sleep_for()
        check("QoS " + label, abs(value - expected) < 1e-6, "%.3f 期望 %.3f" % (value, expected))

    jobs.generating = lambda: False
    runner._viewers.clear()
    runner._extra_sleep = 0.0
    runner._latencies = [50.0] * jobs.LATENCY_WINDOW
    first = runner.sleep_for()
    second = runner.sleep_for()
    check("AIMD 高延迟降档", abs(first - (jobs.DUTY_IDLE_NO_VIEWER + jobs.DEGRADE_STEP)) < 1e-6
          and abs(second - (jobs.DUTY_IDLE_NO_VIEWER + 2 * jobs.DEGRADE_STEP)) < 1e-6, "%.3f -> %.3f" % (first, second))
    runner._latencies = []
    for _ in range(40):
        runner.sleep_for()
    check("AIMD 不上溢", runner._extra_sleep <= 0.5, runner._extra_sleep)
    jobs.generating = original


# ---------------- 2. 缩略图配额回收（价值分 + 保护） ----------------
def test_thumb_quota():
    store = Store(os.path.join(WORK, "thumb.db"))
    cache = os.path.join(WORK, "thumbs")
    os.makedirs(cache, exist_ok=True)
    now = time.time()
    total = 300
    protected_fav = set(range(0, 20))
    protected_recent = set(range(20, 40))
    hit_counts = {}
    with store.write() as write:
        for index in range(total):
            path = os.path.join(WORK, "img", "img_%03d.png" % index)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(b"\x00" * 1024)
            mtime = now - 40 * 86400
            write.execute("INSERT INTO file(id, root, dir, name, path, size, mtime, state, kind) "
                          "VALUES(?, 'output', ?, ?, ?, ?, ?, 'ok', 'image')",
                          (index + 1, os.path.dirname(path), os.path.basename(path), path, 1024, mtime))
            if index in protected_fav:
                write.execute("INSERT INTO favorite(file_id, at) VALUES(?, ?)", (index + 1, now))
            hits = 0 if index < 200 else 12
            hit_counts[index] = hits
            last_hit = now - 60 if index in protected_recent else now - 40 * 86400
            key = thumbs.cache_key(path, mtime, 256)
            thumb_path = thumbs.cache_path(cache, key, 256)
            os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
            with open(thumb_path, "wb") as handle:
                handle.write(b"\x00" * 40000)
            write.execute("INSERT INTO thumb_stat(file_id, tier, bytes, created_at, last_hit_at, hit_count) "
                          "VALUES(?, 256, 40000, ?, ?, ?)", (index + 1, now - 40 * 86400, last_hit, hits))

    def alive(index):
        path = os.path.join(WORK, "img", "img_%03d.png" % index)
        mtime = now - 40 * 86400
        return os.path.exists(thumbs.cache_path(cache, thumbs.cache_key(path, mtime, 256), 256))

    before = thumbs.usage(cache)
    # 第一阶段：部分超限（12.0MB -> 水位 6.0MB），检查"低价值分先走"
    result = thumbs.cleanup_scored(store, cache, quota_bytes=8_000_000, watermark_bytes=6_000_000,
                                   quota_files=10 ** 9, watermark_files=10 ** 9)
    after = thumbs.usage(cache)
    check("配额压力触发回收", result["removed"] > 0, "removed=%d %.1fMB -> %.1fMB"
          % (result["removed"], before["bytes"] / 1048576.0, after["bytes"] / 1048576.0))
    check("回收到水位以下", after["bytes"] <= 6_000_000, after["bytes"])
    low_kept = sum(1 for index in range(40, 200) if alive(index))
    high_kept = sum(1 for index in range(200, total) if alive(index))
    check("低价值分先被回收", low_kept < high_kept,
          "低分存活 %d / 高分存活 %d" % (low_kept, high_kept))

    # 第二阶段：水位低于"受保护数据总量"，必须删到只剩受保护项
    thumbs.cleanup_scored(store, cache, quota_bytes=500_000, watermark_bytes=300_000,
                          quota_files=10 ** 9, watermark_files=10 ** 9)
    final = thumbs.usage(cache)
    check("收藏项豁免", all(alive(index) for index in protected_fav), "20 项")
    check("最近 14 天豁免", all(alive(index) for index in protected_recent), "20 项")
    check("无保护数据可删时停手", final["files"] == 40 and final["bytes"] == 1_600_000, final)
    skipped = thumbs.cleanup_scored(store, cache, quota_bytes=before["bytes"] * 4,
                                   watermark_bytes=before["bytes"] * 3)
    check("未超限时不动手", skipped["skipped"] and skipped["removed"] == 0, skipped)


# ---------------- 3. 十万张规模 ----------------
def test_scale():
    db = os.path.join(WORK, "scale.db")
    store = Store(db)
    rows = []
    fts = []
    base = time.time() - 200 * 86400
    for index in range(100000):
        day = index // 1000
        directory = "D:\\ComfyUI\\output\\2026-%02d-%02d" % (day // 28 % 12 + 1, day % 28 + 1)
        name = "scale_%06d.png" % index
        rows.append((index + 1, "output", directory, name, directory + "\\" + name, 2 * 1024 * 1024,
                     base + index * 60, "ok", "image"))
        fts.append((index + 1, name, "masterpiece, 1girl, seed %d, model cyclegan_%d" % (index, index % 50)))
    started = time.perf_counter()
    conn = store.conn()
    conn.execute("BEGIN")
    conn.executemany("INSERT INTO file(id, root, dir, name, path, size, mtime, state, kind) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO fts_file(rowid, name, search_text) VALUES(?,?,?)", fts)
    conn.commit()
    insert_seconds = time.perf_counter() - started

    def timed(label, sql, args, budget_ms, expect=None):
        started = time.perf_counter()
        result = store.query(sql, args)
        elapsed = (time.perf_counter() - started) * 1000
        ok = elapsed < budget_ms and (expect is None or len(result) == expect)
        check("规模 " + label, ok, "%.1f ms / %d 行" % (elapsed, len(result)))
        return elapsed

    timed("首页 120 条", "SELECT * FROM file WHERE state='ok' ORDER BY mtime DESC LIMIT 120", (), 150, 120)
    timed("目录内分页", "SELECT * FROM file WHERE state='ok' AND dir = ? ORDER BY mtime DESC LIMIT 120",
          ("D:\\ComfyUI\\output\\2026-01-01",), 150)
    timed("FTS 子串搜索", "SELECT f.id FROM fts_file JOIN file f ON f.id = fts_file.rowid "
          "WHERE fts_file MATCH ? LIMIT 120", ('"cyclegan_7"',), 400)
    timed("目录聚合", "SELECT dir, COUNT(*) AS n FROM file WHERE root='output' GROUP BY dir ORDER BY n DESC LIMIT 200",
          (), 800)
    timed("按 id 取详情", "SELECT * FROM file WHERE id = ?", (99999,), 30, 1)
    timed("总数统计", "SELECT COUNT(*) AS n FROM file WHERE state='ok'", (), 300)
    check("十万行写入耗时", insert_seconds < 60, "%.1f s" % insert_seconds)


# ---------------- 4. 双语摘要 ----------------
def test_i18n():
    detail = {
        "added": [["KSampler", 3], ["LoraLoader", 1]],
        "removed": [["EmptyLatentImage", 1]],
        "changed": [{"kind": "number", "key": "steps", "from": 20, "to": 28},
                    {"kind": "text", "key": "positive", "from_len": 100, "to_len": 132,
                     "fragments": [{"added": "golden hour"}]}],
        "random_fields": 2,
    }
    zh = diff.summarize("weak", 0.8, detail)
    en = diff.summarize("weak", 0.8, detail, lang="en")
    has_cjk = lambda text: any("\u4e00" <= ch <= "\u9fff" for ch in text)
    check("中文摘要含中文", has_cjk(zh), zh[:40])
    check("英文摘要无中文", not has_cjk(en), en[:60])
    check("英文摘要有关键信息", "added 4 nodes" in en and "steps 20->28" in en, en[:80])
    check("起点摘要双语", not has_cjk(diff.summarize("new", 0.4, {}, lang="en")))


test_qos()
test_thumb_quota()
test_scale()
test_i18n()

print("")
if FAILED:
    print("ACCEPT FAILED  (%d 项失败: %s)" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("ACCEPT OK  (%d 项检查，0 项失败)" % len(PASSED))
