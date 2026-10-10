# -*- coding: utf-8 -*-
r"""API 端到端测试：用一份合成的输出目录（真实 PNG 的副本）跑通全部接口。

绝不触碰 D:\ComfyUI\output 里的原始文件；删除 / 回收站 / 另存为都在合成目录里演练。
  python tools\api_test.py
"""
import asyncio
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

CACHE = os.path.join(ROOT, "cache", "apitest")
WORK = os.path.join(CACHE, "output")
TRASH = os.path.join(CACHE, "trash")
ARCHIVE = os.path.join(CACHE, "archive")
TEMP = os.path.join(CACHE, "temp")
SOURCE = os.environ.get("IG_SOURCE_DIR", r"D:\ComfyUI\output")

os.environ["IG_CACHE_DIR"] = CACHE
os.environ["IG_DB_PATH"] = os.path.join(CACHE, "index.db")
os.environ["IG_OUTPUT_DIR"] = WORK
os.environ["IG_TEMP_DIR"] = TEMP
os.environ["IG_ARCHIVE_DIR"] = ARCHIVE

PORT = int(os.environ.get("IG_API_PORT", "39411"))
FAILED = []
BASE = "http://127.0.0.1:%d" % PORT


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("   [" + str(detail) + "]") if detail != "" else ""))
    if not ok:
        FAILED.append(name)


def call(path, method="GET", body=None):
    url = BASE + path
    data = None
    headers = {"accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        ctype = response.headers.get("content-type", "")
        if "json" in ctype:
            return response.status, json.loads(raw.decode("utf-8"))
        return response.status, raw


def call_raw(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return response.status, response.headers.get("content-type", ""), response.read()


def build_workspace():
    if os.path.isdir(CACHE):
        shutil.rmtree(CACHE, ignore_errors=True)
    target = os.path.join(WORK, "2026-01-01")
    os.makedirs(target, exist_ok=True)
    os.makedirs(TEMP, exist_ok=True)
    sources = []
    for dirpath, _dirnames, filenames in os.walk(SOURCE):
        for name in filenames:
            if name.lower().endswith(".png"):
                sources.append(os.path.join(dirpath, name))
        if len(sources) > 20:
            break
    sources.sort(key=lambda path: os.path.getsize(path))
    picked = sources[:6]
    now = time.time()
    for index, source in enumerate(picked):
        destination = os.path.join(target, "session%s_%02d.png" % ("A" if index < 3 else "B", index))
        shutil.copy2(source, destination)
        offset = (120 - index * 20) if index < 3 else (11 * 3600 + 120 - (index - 3) * 20)
        stamp = now - offset
        os.utime(destination, (stamp, stamp))
    draft_dir = os.path.join(target, "Anima-草稿")
    os.makedirs(draft_dir, exist_ok=True)
    for index, source in enumerate(picked[:2]):
        destination = os.path.join(draft_dir, "draft_%02d.png" % index)
        shutil.copy2(source, destination)
        stamp = now - 90 + index * 10
        os.utime(destination, (stamp, stamp))
    return target, picked


def serve(holder):
    from aiohttp import web

    from py.routes import registry

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = web.Application()
    registry.register_all(app)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    loop.run_until_complete(site.start())
    holder["loop"] = loop
    holder["runner"] = runner
    loop.run_forever()
    loop.run_until_complete(runner.cleanup())
    loop.close()


def main():
    target_dir, picked = build_workspace()
    from py.runtime import get as runtime

    rt = runtime()
    rt.set_setting("trash_dir", TRASH)
    rt.set_setting("session_hours", "6")
    rt.set_setting("archive_dir", ARCHIVE)

    started = time.perf_counter()
    counts = rt.scanner.scan_roots()
    result = rt.scanner.index_directory("output", target_dir)
    draft_result = rt.scanner.index_directory("output", os.path.join(target_dir, "Anima-草稿"))
    seconds = time.perf_counter() - started
    print("synthetic workspace: %d files, index %s + draft %s (%.2fs)" % (
        counts["checked"], result, draft_result, seconds))
    check("合成目录已解析", result["parsed"] == 6 and draft_result["parsed"] == 2, (result, draft_result))
    check("跨会话切成 2 个族", result["families"] == 2, result["families"])

    holder = {}
    thread = threading.Thread(target=serve, args=(holder,), daemon=True)
    thread.start()
    for _ in range(60):
        try:
            call("/api/gallery/hello")
            break
        except Exception:
            time.sleep(0.2)

    status, hello = call("/api/gallery/hello")
    check("hello", status == 200 and hello.get("plugin") == "ComfyUI-ImageGallery", hello.get("version"))

    status, stats = call("/api/gallery/stats")
    check("stats 统计 8 个文件（含 2 张草稿）", status == 200 and stats["files"] == 8, stats.get("files"))

    status, dirs = call("/api/gallery/dirs?root=output")
    check("dirs 列出目录", status == 200 and len(dirs["dirs"]) == 1 and dirs["dirs"][0]["count"] == 6, dirs["dirs"])

    status, page = call("/api/gallery/query", "POST", {"root": "output", "limit": 50})
    items = page["items"]
    check("query 返回全部 6 项", status == 200 and len(items) == 6, page["total"])
    check("列表带 diff 摘要", any(item.get("diff_summary") for item in items), items[0].get("diff_verdict"))
    check("时间倒序", items[0]["mtime"] >= items[-1]["mtime"])

    newest = items[0]
    status, ctype, blob = call_raw("/api/gallery/thumb?id=%d&tier=256" % newest["id"])
    check("缩略图接口", status == 200 and len(blob) > 500 and ("webp" in ctype or "image" in ctype),
          "%s %d bytes" % (ctype, len(blob)))

    status, ctype, blob = call_raw("/api/gallery/raw?id=%d" % newest["id"])
    check("原图接口", status == 200 and blob[:8] == b"\x89PNG\r\n\x1a\n", len(blob))

    status, detail = call("/api/gallery/detail?id=%d" % newest["id"])
    check("详情含模型与文本", status == 200 and len(detail["meta"]["models"]) > 0 and len(detail["meta"]["search_text"]) > 50,
          detail["meta"]["models"][:2])
    check("详情含相邻跳转", detail["next_id"] is not None or detail["prev_id"] is not None,
          (detail["prev_id"], detail["next_id"]))

    token = ""
    for candidate in detail["meta"]["search_text"].split("\n"):
        candidate = candidate.strip()
        if 4 <= len(candidate) <= 24 and candidate.isascii() and candidate.replace("_", "").isalpha():
            token = candidate
            break
    if token:
        status, hit = call("/api/gallery/query", "POST", {"root": "output", "q": token, "limit": 20})
        check("全文检索命中", len(hit["items"]) >= 1, "token=%s n=%d" % (token, len(hit["items"])))
    else:
        check("全文检索命中", False, "样例文本里没找到可用 token")

    model = detail["meta"]["models"][0]
    status, filtered = call("/api/gallery/query", "POST", {"root": "output", "models": [model], "model_mode": "any"})
    check("模型筛选命中", len(filtered["items"]) >= 1, "%s -> %d" % (model, len(filtered["items"])))

    status, wf = call("/api/gallery/workflow", "POST", {"id": newest["id"]})
    graph_ok = False
    if wf.get("workflow"):
        try:
            parsed = json.loads(wf["workflow"])
            graph_ok = isinstance(parsed, dict) and ("nodes" in parsed or "last_node_id" in parsed)
        except Exception:
            graph_ok = False
    check("工作流原文可取且可解析", status == 200 and wf.get("ok") and graph_ok, wf.get("kind"))

    status, exported = call("/api/gallery/export", "POST", {"ids": [items[0]["id"], items[1]["id"]]})
    copied_ok = len(exported["copied"]) == 2 and all(os.path.exists(path) for path in exported["copied"])
    check("批量另存为落盘", status == 200 and copied_ok, exported["archive"])
    check("另存为写出清单", bool(exported.get("manifest")) and os.path.exists(exported["manifest"]))

    victim = items[2]
    status, deleted = call("/api/gallery/delete", "POST", {"ids": [victim["id"]], "permanent": False})
    moved_ok = os.path.exists(deleted["results"][0].get("target", ""))
    check("删除移入回收站", status == 200 and moved_ok, deleted["batch"])

    status, trash = call("/api/gallery/trash")
    check("回收站列出批次", status == 200 and len(trash["batches"]) == 1 and trash["batches"][0]["count"] == 1, trash["batches"])

    status, restored = call("/api/gallery/trash/restore", "POST", {"batch": deleted["batch"]})
    check("回收站还原", status == 200 and len(restored["restored"]) == 1 and os.path.exists(restored["restored"][0]),
          restored.get("failed"))

    status, beat = call("/api/gallery/heartbeat", "POST", {"viewer": "test"})
    check("心跳登记在线浏览", status == 200 and beat["viewers"] >= 1, beat.get("viewers"))

    status, settings = call("/api/gallery/settings", "POST", {"session_hours": 6})
    check("设置可写入", status == 200 and settings["session_hours"] == 6)

    # ---------- 草稿过滤 / 收藏 / 标签 / 缩略图配额 ----------
    status, visible = call("/api/gallery/query", "POST", {"root": "output", "limit": 50})
    check("草稿默认隐藏", len(visible["items"]) == 6, "可见 %d 张" % len(visible["items"]))
    status, dirs_hidden = call("/api/gallery/dirs?root=output")
    names = [entry["name"] for entry in dirs_hidden["dirs"]]
    check("草稿目录默认不出现在目录树", len(names) == 1, names)
    check("回报隐藏数量", dirs_hidden["hidden_drafts"] == 2, dirs_hidden["hidden_drafts"])

    status, shown = call("/api/gallery/query", "POST", {"root": "output", "limit": 50, "show_drafts": True})
    check("开关打开后草稿可见", len(shown["items"]) == 8, "可见 %d 张" % len(shown["items"]))
    status, dirs_shown = call("/api/gallery/dirs?root=output&show_drafts=1")
    check("开关打开后目录树含草稿", len(dirs_shown["dirs"]) == 2, [entry["name"] for entry in dirs_shown["dirs"]])

    draft_item = [item for item in shown["items"] if "草稿" in item["dir"]][0]
    status, favorited = call("/api/gallery/favorite", "POST", {"id": draft_item["id"], "value": True})
    status, fav_only = call("/api/gallery/query", "POST", {"root": "output", "favorite_only": True, "show_drafts": True, "limit": 50})
    check("收藏后可筛出", favorited.get("favorite") is True and len(fav_only["items"]) == 1, len(fav_only["items"]))

    status, tagged = call("/api/gallery/tags", "POST", {"id": draft_item["id"], "tags": ["草稿", "待修"]})
    status, tag_list = call("/api/gallery/tags")
    check("标签写入并在列表出现", len(tag_list["tags"]) == 2, [entry["name"] for entry in tag_list["tags"]])
    status, tag_filtered = call("/api/gallery/query", "POST", {"root": "output", "tags": ["待修"], "show_drafts": True, "limit": 50})
    check("按标签筛选命中", len(tag_filtered["items"]) == 1, len(tag_filtered["items"]))

    status, detail_draft = call("/api/gallery/detail?id=%d" % draft_item["id"])
    check("详情带收藏与标签", detail_draft["favorite"] is True and "待修" in detail_draft["tags"], detail_draft["tags"])

    status, usage = call("/api/gallery/thumbs")
    check("缩略图用量接口", status == 200 and usage.get("quota", 0) > 0, usage)
    status, cleaned = call("/api/gallery/thumbs/cleanup", "POST", {})
    check("未超限时清理直接跳过", cleaned.get("skipped") is True, cleaned.get("removed"))

    status, saved = call("/api/gallery/settings", "POST", {"draft_keywords": "草稿, draft", "thumb_quota_gb": 1})
    check("草稿关键词与配额可保存", status == 200 and len(saved["draft_keywords"]) == 2, saved["draft_keywords"])

    # ---------- 翻页方向与标签备份 ----------
    status, middle = call("/api/gallery/detail?id=%d" % items[1]["id"])
    check("详情返回网格方向邻居", middle["older_id"] is not None and middle["newer_id"] is not None,
          (middle["newer_id"], middle["older_id"]))
    status, older_detail = call("/api/gallery/detail?id=%d" % middle["older_id"])
    status, newer_detail = call("/api/gallery/detail?id=%d" % middle["newer_id"])
    check("older 确实更早（网格右侧）", older_detail["file"]["mtime"] <= middle["file"]["mtime"],
          (older_detail["file"]["mtime"], middle["file"]["mtime"]))
    check("newer 确实更新（网格左侧）", newer_detail["file"]["mtime"] >= middle["file"]["mtime"],
          (newer_detail["file"]["mtime"], middle["file"]["mtime"]))

    status, exported = call("/api/gallery/tags/export")
    check("标签与收藏可导出", len(exported["tags"]) == 2 and len(exported["favorites"]) == 1,
          (list(exported["tags"].keys()), len(exported["favorites"])))
    call("/api/gallery/tags", "POST", {"id": draft_item["id"], "tags": []})
    call("/api/gallery/favorite", "POST", {"id": draft_item["id"], "value": False})
    status, imported = call("/api/gallery/tags/import", "POST", exported)
    check("导入后恢复标签与收藏", imported["favorites"] == 1 and imported["tag_links"] >= 2, imported)

    # ---------- 阶段四：正则搜索 / 相关度 / 重命名 / 移动 / 回收站增强 ----------
    if token:
        status, regex_hits = call("/api/gallery/query", "POST", {"root": "output", "q": token[:6] + ".*", "mode": "regex", "show_drafts": True})
        check("正则搜索可用", len(regex_hits["items"]) >= 1, "pattern=%s hits=%d" % (token[:6] + ".*", len(regex_hits["items"])))
        status, ranked = call("/api/gallery/query", "POST", {"root": "output", "q": token, "sort": "relevance", "show_drafts": True})
        check("相关度排序可用", len(ranked["items"]) >= 1, "ranked=%d" % len(ranked["items"]))

    victim = items[3]
    status, renamed = call("/api/gallery/rename", "POST", {"ids": [victim["id"]], "template": "{name}_renamed", "pad": 0})
    renamed_ok = bool(renamed["results"]) and not renamed["results"][0].get("error") and os.path.exists(renamed["results"][0].get("path", ""))
    check("批量重命名落盘", renamed_ok, renamed["results"][0].get("name"))
    status, page_after = call("/api/gallery/query", "POST", {"root": "output", "q": "_renamed", "show_drafts": True})
    check("重命名后可按新名检索", len(page_after["items"]) == 1, len(page_after["items"]))

    status, moved = call("/api/gallery/move", "POST", {"ids": [victim["id"]], "target": "2026-01-01/moved", "root": "output"})
    moved_ok = bool(moved["results"]) and not moved["results"][0].get("error") and os.path.exists(moved["results"][0].get("path", ""))
    check("移动到子目录", moved_ok, moved.get("target"))

    status, purged = call("/api/gallery/trash/purge", "POST", {"batch": "no-such-batch"})
    check("清空批次接口可用", status == 200 and purged.get("removed") == 0, purged)

    # ---------- 目录变化探测：新图落地后不必等 10 分钟 ----------
    import py.jobs as jobs_mod
    probe_interval, hot_interval = jobs_mod.SCAN_PROBE_INTERVAL, jobs_mod.SCAN_HOT_INTERVAL
    jobs_mod.SCAN_PROBE_INTERVAL = 0.0
    jobs_mod.SCAN_HOT_INTERVAL = 0.0
    runner = rt.runner
    runner._last_probe = 0.0
    rt.store.set_setting("last_scan_at", str(time.time()))
    rt.store.set_setting("roots_signature", runner._roots_signature())
    idle_again = runner._needs_scan()
    probe_dir = os.path.join(WORK, "2026-03-03")
    os.makedirs(probe_dir, exist_ok=True)
    with open(os.path.join(probe_dir, "probe_00001_.png"), "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    runner._last_probe = 0.0
    detected = runner._needs_scan()
    jobs_mod.SCAN_PROBE_INTERVAL = probe_interval
    jobs_mod.SCAN_HOT_INTERVAL = hot_interval
    check("无变化时不重复扫描", idle_again is False, idle_again)
    check("新图落地后立刻触发扫描", detected is True, detected)

    # ---------- 队列由忙转闲：事件驱动的那一次扫描 ----------
    real_generating = jobs_mod.generating
    runner._was_busy = False
    runner._scan_due = False
    jobs_mod.generating = lambda: True
    runner._tick()
    busy_no_scan = runner._scan_due
    jobs_mod.generating = lambda: False
    idle_result = runner._tick()
    queued = runner._scan_due
    jobs_mod.generating = real_generating
    # 这一跳要么已经扫完（返回 True），要么把这次事件排进下一跳 —— 两者都算"立刻响应"
    check("生成中不额外安排扫描", busy_no_scan is False, busy_no_scan)
    check("队列转闲后立即安排扫描", bool(idle_result) or queued is True,
          {"tick": idle_result, "queued": queued})

    started = time.perf_counter()
    rt.runner.note_request(120.0)
    rt.runner.sleep_for()
    rt.runner.heartbeat("probe")
    rt.runner.viewer_count()
    rt.runner.p95_ms()
    lock_seconds = time.perf_counter() - started
    check("限速器无自锁", lock_seconds < 1.0, "%.3fs" % lock_seconds)

    started = time.perf_counter()
    for _ in range(30):
        call("/api/gallery/stats")
    tail_seconds = time.perf_counter() - started
    check("连续请求不挂起", tail_seconds < 5.0, "30 次 stats 用时 %.2fs" % tail_seconds)

    rt.runner.stop()
    loop = holder.get("loop")
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)

    print("")
    print("API %s  (%d 项失败)" % ("OK" if not FAILED else "FAILED", len(FAILED)))
    for name in FAILED:
        print("  - " + name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
