"""画廊 API（aiohttp）。全部路由挂在 ComfyUI 自己的应用上，同源同端口。"""
import json
import os
import shutil
import time

from aiohttp import web

from .. import config, diff, meta, thumbs
from ..runtime import get as runtime

JPEG_HINT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
             ".bmp": "image/bmp", ".avif": "image/avif", ".mp4": "video/mp4", ".webm": "video/webm"}


def _json(data, status: int = 200):
    return web.json_response(data, status=status, dumps=lambda obj: json.dumps(obj, ensure_ascii=False))


def _localized_summaries(rt, rows, lang) -> dict:
    """按语言重渲染 diff 摘要；中文直接用库里存好的文本。"""
    if lang != "en" or not rows:
        return {}
    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" * len(ids))
    stored = rt.store.query(
        "SELECT file_id, verdict, score, detail FROM file_diff WHERE file_id IN (" + placeholders + ")",
        tuple(ids),
    )
    out = {}
    for row in stored:
        try:
            detail = json.loads(row["detail"] or "{}")
        except ValueError:
            continue
        out[row["file_id"]] = diff.summarize(row["verdict"], row["score"], detail, lang="en")
    return out


def _items(rt, rows, summaries=None) -> list:
    out = []
    for row in rows:
        keys = row.keys()
        out.append({
            "id": row["id"],
            "name": row["name"],
            "dir": row["dir"],
            "dirname": os.path.basename(row["dir"]),
            "root": row["root"],
            "size": row["size"],
            "mtime": row["mtime"],
            "width": row["width"],
            "height": row["height"],
            "kind": row["kind"],
            "meta_state": row["meta_state"],
            "node_count": row["node_count"],
            "fingerprint": row["fingerprint"] or "",
            "family_id": row["family_id"],
            "thumb": "%s/thumb?id=%d&tier=256" % (config.API_PATH, row["id"]),
            "raw": "%s/raw?id=%d" % (config.API_PATH, row["id"]),
            "diff_verdict": (row["diff_verdict"] if "diff_verdict" in keys else None),
            "diff_summary": (summaries or {}).get(row["id"], row["diff_summary"] if "diff_summary" in keys else ""),
            "favorite": bool(row["favorite_flag"]) if "favorite_flag" in keys else False,
            "duration": (row["duration"] if "duration" in keys else 0) or 0,
        })
    return out


def _row(rt, file_id: int):
    return rt.store.query_one("SELECT * FROM file WHERE id = ?", (file_id,))


def _fts_clause(q: str):
    text = (q or "").strip()
    if not text:
        return None, []
    if len(text) >= 3:
        safe = text.replace('"', " ")
        return "f.id IN (SELECT rowid FROM fts_file WHERE fts_file MATCH ?)", ['"' + safe + '"']
    return "f.name LIKE ?", ["%" + text + "%"]


def _draft_clause(rt, show_drafts: bool, column: str = "f.dir"):
    """目录路径里带草稿关键词的文件默认隐藏（实施方案 §10.3 增补）。"""
    if show_drafts:
        return None, []
    keywords = rt.draft_keywords()
    if not keywords:
        return None, []
    parts = []
    params = []
    for keyword in keywords:
        parts.append(column + " NOT LIKE ?")
        params.append("%" + keyword + "%")
    return "(" + " AND ".join(parts) + ")", params


def _draft_hidden_count(rt) -> int:
    keywords = rt.draft_keywords()
    if not keywords:
        return 0
    marks = " OR ".join("dir LIKE ?" for _ in keywords)
    row = rt.store.query_one(
        "SELECT COUNT(*) AS n FROM file WHERE state = 'ok' AND (" + marks + ")",
        tuple("%" + keyword + "%" for keyword in keywords),
    )
    return row["n"] if row else 0


def _tag_clause(names, mode: str):
    marks = ",".join("?" for _ in names)
    if mode == "all":
        return ("f.id IN (SELECT ft.file_id FROM file_tag ft JOIN tag t ON t.id = ft.tag_id "
                "WHERE t.name IN (" + marks + ") GROUP BY ft.file_id HAVING COUNT(DISTINCT t.name) = ?)", list(names) + [len(names)])
    return ("f.id IN (SELECT ft.file_id FROM file_tag ft JOIN tag t ON t.id = ft.tag_id "
            "WHERE t.name IN (" + marks + "))", list(names))


def register(app: web.Application) -> None:
    rt = runtime()
    api = config.API_PATH

    async def _timed(handler, request: web.Request):
        start = time.perf_counter()
        try:
            return await handler(request)
        finally:
            rt.runner.note_request((time.perf_counter() - start) * 1000.0)

    # ---------------- 心跳与状态 ----------------
    async def heartbeat(request: web.Request) -> web.Response:
        body = await request.json()
        viewer = str(body.get("viewer") or "anonymous")
        rt.runner.heartbeat(viewer)
        return _json({"ok": True, "viewers": rt.runner.viewer_count(), "state": rt.runner.state})

    async def stats(request: web.Request) -> web.Response:
        info = rt.store.stats()
        info["roots"] = [{"name": name, "path": path} for name, path in rt.roots]
        info["worker"] = rt.runner.state
        info["viewers"] = rt.runner.viewer_count()
        info["thumbs"] = thumbs.usage(rt.thumb_dir)
        info["session_hours"] = rt.session_hours
        info["draft_keywords"] = rt.draft_keywords()
        info["draft_hidden"] = _draft_hidden_count(rt)
        info["favorites"] = rt.store.query_one("SELECT COUNT(*) AS n FROM favorite")["n"]
        info["tags"] = rt.store.query_one("SELECT COUNT(*) AS n FROM tag WHERE kind = 'custom'")["n"]
        info["thumb_quota"] = rt.thumb_quota_bytes()
        info["archive_dir"] = rt.archive_dir()
        info["trash_dir"] = rt.trash_dir()
        info["jobs"] = [dict(row) for row in rt.store.jobs_recent(5)]
        return _json(info)

    async def scan(request: web.Request) -> web.Response:
        rt.store.set_setting("last_scan_at", "0")
        rt.store.set_setting("rescan_epoch", str(time.time()))
        return _json({"ok": True})

    async def settings(request: web.Request) -> web.Response:
        body = await request.json()
        if "session_hours" in body:
            rt.set_setting("session_hours", str(float(body["session_hours"])))
        if "archive_dir" in body:
            rt.set_setting("archive_dir", str(body["archive_dir"]))
        if "trash_dir" in body:
            rt.set_setting("trash_dir", str(body["trash_dir"]))
        if "draft_keywords" in body:
            rt.set_setting("draft_keywords", str(body["draft_keywords"]))
        if "trash_keep_days" in body:
            rt.set_setting("trash_keep_days", str(float(body["trash_keep_days"])))
        if "thumb_quota_gb" in body:
            rt.set_setting("thumb_quota_gb", str(float(body["thumb_quota_gb"])))
            rt.runner.quota_bytes = rt.thumb_quota_bytes()
        return _json({"ok": True, "session_hours": rt.session_hours, "archive_dir": rt.archive_dir(),
                      "trash_dir": rt.trash_dir(), "draft_keywords": rt.draft_keywords(),
                      "thumb_quota_gb": rt.thumb_quota_bytes() / float(1024 ** 3)})

    # ---------------- 目录与列表 ----------------
    async def dirs(request: web.Request) -> web.Response:
        root = request.query.get("root", "output")
        show_drafts = request.query.get("show_drafts") in ("1", "true", "yes")
        sql = "SELECT dir, COUNT(*) AS n, MAX(mtime) AS latest FROM file WHERE root = ? AND state = 'ok'"
        params = [root]
        clause, extra = _draft_clause(rt, show_drafts, "dir")
        if clause:
            sql += " AND " + clause
            params.extend(extra)
        sql += " GROUP BY dir ORDER BY dir DESC"
        rows = rt.store.query(sql, tuple(params))
        hidden = 0
        if not show_drafts:
            keywords = rt.draft_keywords()
            if keywords:
                marks = " OR ".join("dir LIKE ?" for _ in keywords)
                hidden = rt.store.query_one(
                    "SELECT COUNT(*) AS n FROM file WHERE root = ? AND state = 'ok' AND (" + marks + ")",
                    tuple([root] + ["%" + keyword + "%" for keyword in keywords]),
                )["n"]
        root_path = rt.roots_by_name.get(root, "")
        dirs_out = []
        for row in rows:
            label = os.path.basename(row["dir"])
            if root_path and row["dir"].lower().startswith(root_path.lower()):
                relative = row["dir"][len(root_path):].strip("\\/")
                if relative and relative != label:
                    label = relative
            dirs_out.append({"dir": row["dir"], "name": label, "count": row["n"], "latest": row["latest"]})
        return _json({"root": root, "dirs": dirs_out, "hidden_drafts": hidden, "show_drafts": show_drafts})

    async def models(request: web.Request) -> web.Response:
        rows = rt.store.query(
            "SELECT m.name AS name, COUNT(*) AS n FROM model m JOIN file_model fm ON fm.model_id = m.id "
            "GROUP BY m.name ORDER BY n DESC, m.name ASC LIMIT 300"
        )
        return _json({"models": [{"name": row["name"], "count": row["n"]} for row in rows]})

    async def query(request: web.Request) -> web.Response:
        body = await request.json()
        root = body.get("root") or "output"
        sort = body.get("sort") or "mtime_desc"
        limit = max(1, min(int(body.get("limit") or 120), 300))
        where = ["f.state = 'ok'"]
        params = []
        if root != "all":
            where.append("f.root = ?")
            params.append(root)
        if body.get("dir"):
            where.append("f.dir = ?")
            params.append(body["dir"])
        if body.get("dir_prefix"):
            where.append("f.dir LIKE ?")
            params.append(body["dir_prefix"].rstrip("\\/") + "%")
        if body.get("kind"):
            where.append("f.kind = ?")
            params.append(body["kind"])
        if body.get("family_id"):
            where.append("f.family_id = ?")
            params.append(int(body["family_id"]))
        draft_clause, draft_params = _draft_clause(rt, bool(body.get("show_drafts")), "f.dir")
        if draft_clause:
            where.append(draft_clause)
            params.extend(draft_params)
        if body.get("favorite_only"):
            where.append("f.id IN (SELECT file_id FROM favorite)")
        tag_names = [name for name in (body.get("tags") or []) if name]
        if tag_names:
            tag_sql, tag_params = _tag_clause(tag_names, body.get("tag_mode") or "any")
            where.append(tag_sql)
            params.extend(tag_params)

        search_text = (body.get("q") or "").strip()
        fts_clause, fts_params = (None, [])
        if search_text and (body.get("mode") == "regex"):
            where.append("f.id IN (SELECT file_id FROM meta WHERE search_text REGEXP ?)")
            params.append(search_text)
        elif search_text:
            fts_clause, fts_params = _fts_clause(search_text)

        base_where = list(where)
        base_params = list(params)
        if fts_clause:
            where.append(fts_clause)
            params.extend(fts_params)
        where_sql = " AND ".join(where)
        total = rt.store.query_one("SELECT COUNT(*) AS n FROM file f WHERE " + where_sql, tuple(params))["n"]

        order_map = {
            "mtime_desc": "f.mtime DESC, f.id DESC",
            "mtime_asc": "f.mtime ASC, f.id ASC",
            "size_desc": "f.size DESC, f.id DESC",
            "name_asc": "f.name ASC, f.id ASC",
        }
        order = order_map.get(sort, order_map["mtime_desc"])
        offset = int(body.get("offset") or 0)

        # 相关度排序：rank 必须和 fts_file 的 MATCH 出现在同一次查询里，因此走独立路径，
        # 并改用"不含 FTS 子句"的 where（否则会重复过滤）。
        if sort == "relevance" and search_text and body.get("mode") != "regex":
            safe = '"' + search_text.replace('"', " ") + '"'
            rows = rt.store.query(
                "SELECT f.*, d.verdict AS diff_verdict, d.summary AS diff_summary, "
                "(SELECT COUNT(*) FROM favorite fa WHERE fa.file_id = f.id) AS favorite_flag "
                "FROM fts_file JOIN file f ON f.id = fts_file.rowid "
                "LEFT JOIN file_diff d ON d.file_id = f.id WHERE fts_file MATCH ? AND "
                + " AND ".join(base_where) + " ORDER BY rank LIMIT ? OFFSET ?",
                tuple([safe] + base_params + [limit, offset]),
            )
            lang = str(body.get("lang") or request.query.get("lang") or "zh")
            ranked = _items(rt, rows, _localized_summaries(rt, rows, lang))
            return _json({"items": ranked, "total": total, "next_cursor": "",
                          "has_more": offset + len(ranked) < total})

        page_params = list(params)
        if sort.startswith("mtime") and body.get("cursor"):
            raw = str(body["cursor"])
            parts = raw.rsplit("|", 1)
            if len(parts) == 2:
                try:
                    ts, last_id = float(parts[0]), int(parts[1])
                except ValueError:
                    ts, last_id = None, None
                if ts is not None:
                    if sort == "mtime_desc":
                        where_sql += " AND (f.mtime < ? OR (f.mtime = ? AND f.id < ?))"
                    else:
                        where_sql += " AND (f.mtime > ? OR (f.mtime = ? AND f.id > ?))"
                    page_params.extend([ts, ts, last_id])
        elif body.get("offset"):
            offset = max(0, int(body["offset"]))

        rows = rt.store.query(
            "SELECT f.*, d.verdict AS diff_verdict, d.summary AS diff_summary, "
            "(SELECT COUNT(*) FROM favorite fa WHERE fa.file_id = f.id) AS favorite_flag "
            "FROM file f LEFT JOIN file_diff d ON d.file_id = f.id WHERE " + where_sql +
            " ORDER BY " + order + " LIMIT ? OFFSET ?",
            tuple(page_params + [limit, offset]),
        )
        lang = str(body.get("lang") or request.query.get("lang") or "zh")
        items = _items(rt, rows, _localized_summaries(rt, rows, lang))
        next_cursor = ""
        if sort.startswith("mtime") and len(rows) == limit:
            next_cursor = "%.3f|%d" % (rows[-1]["mtime"], rows[-1]["id"])
        return _json({"items": items, "total": total, "next_cursor": next_cursor,
                      "has_more": bool(next_cursor) or (offset + len(rows) < total)})

    # ---------------- 媒体 ----------------
    async def thumb(request: web.Request) -> web.Response:
        file_id = int(request.query.get("id", 0))
        tier = int(request.query.get("tier", 256))
        if tier not in thumbs.TIERS:
            tier = 256
        row = _row(rt, file_id)
        if row is None:
            raise web.HTTPNotFound()
        if not rt.is_allowed(row["path"]) or not os.path.exists(row["path"]):
            raise web.HTTPNotFound()
        try:
            served = thumbs.ensure(rt.thumb_dir, row["path"], row["mtime"], tier)
        except Exception:
            raise web.HTTPNotFound(text="thumbnail unavailable")
        headers = {"Cache-Control": "public, max-age=31536000, immutable"}
        try:
            rt.note_thumb_hit(file_id, tier, os.path.getsize(served) if os.path.exists(served) else 0)
        except OSError:
            pass
        if served.endswith(".webp"):
            return web.FileResponse(served, headers=headers)
        content_type = JPEG_HINT.get(os.path.splitext(served)[1].lower(), "application/octet-stream")
        return web.FileResponse(served, headers=headers, content_type=content_type)

    async def raw(request: web.Request) -> web.Response:
        file_id = int(request.query.get("id", 0))
        row = _row(rt, file_id)
        if row is None or not os.path.exists(row["path"]) or not rt.is_allowed(row["path"]):
            raise web.HTTPNotFound()
        return web.FileResponse(row["path"])

    async def download(request: web.Request) -> web.Response:
        file_id = int(request.query.get("id", 0))
        row = _row(rt, file_id)
        if row is None or not os.path.exists(row["path"]) or not rt.is_allowed(row["path"]):
            raise web.HTTPNotFound()
        return web.FileResponse(row["path"], headers={
            "Content-Disposition": 'attachment; filename="%s"' % row["name"].replace('"', "")
        })

    # ---------------- 详情与 diff ----------------
    async def detail(request: web.Request) -> web.Response:
        file_id = int(request.query.get("id", 0))
        row = _row(rt, file_id)
        if row is None:
            raise web.HTTPNotFound()
        meta_row = rt.store.query_one("SELECT * FROM meta WHERE file_id = ?", (file_id,))
        model_rows = rt.store.query(
            "SELECT m.name AS name FROM model m JOIN file_model fm ON fm.model_id = m.id WHERE fm.file_id = ? ORDER BY m.name",
            (file_id,),
        )
        diff_row = rt.store.query_one("SELECT * FROM file_diff WHERE file_id = ?", (file_id,))
        favorite_row = rt.store.query_one("SELECT file_id FROM favorite WHERE file_id = ?", (file_id,))
        tag_rows = rt.store.query(
            "SELECT t.name AS name FROM tag t JOIN file_tag ft ON ft.tag_id = t.id "
            "WHERE ft.file_id = ? AND t.kind = 'custom' ORDER BY t.name",
            (file_id,),
        )
        family = None
        if row["family_id"]:
            fam = rt.store.query_one("SELECT * FROM family WHERE id = ?", (row["family_id"],))
            if fam:
                family = {"id": fam["id"], "count": fam["file_count"], "start": fam["start_file_id"]}
        neighbors = rt.store.query(
            "SELECT id, name, mtime FROM file WHERE dir = ? AND state = 'ok' ORDER BY mtime ASC, id ASC",
            (row["dir"],),
        )
        prev_id = next_id = None
        ids = [item["id"] for item in neighbors]
        # 网格按时间倒序排列，所以：older（更早）在右侧，newer（更新）在左侧。
        # 翻页方向必须与网格一致，diff 的对照对象始终是更早的那一张。
        older_id = newer_id = None
        if file_id in ids:
            index = ids.index(file_id)
            older_id = ids[index - 1] if index > 0 else None
            newer_id = ids[index + 1] if index + 1 < len(ids) else None

        # 走到目录边界后继续沿全局时间序翻页（允许跨 diff 链），仍遵循当前草稿开关
        if older_id is None or newer_id is None:
            show_drafts = request.query.get("show_drafts") in ("1", "true", "yes")
            clause, extra = _draft_clause(rt, show_drafts, "dir")
            suffix = (" AND " + clause) if clause else ""
            common = [row["root"]] + list(extra)
            if older_id is None:
                found = rt.store.query_one(
                    "SELECT id FROM file WHERE root = ? AND state = 'ok'" + suffix +
                    " AND (mtime < ? OR (mtime = ? AND id < ?)) ORDER BY mtime DESC, id DESC LIMIT 1",
                    tuple(common + [row["mtime"], row["mtime"], file_id]),
                )
                older_id = found["id"] if found else None
            if newer_id is None:
                found = rt.store.query_one(
                    "SELECT id FROM file WHERE root = ? AND state = 'ok'" + suffix +
                    " AND (mtime > ? OR (mtime = ? AND id > ?)) ORDER BY mtime ASC, id ASC LIMIT 1",
                    tuple(common + [row["mtime"], row["mtime"], file_id]),
                )
                newer_id = found["id"] if found else None
        return _json({
            "file": _items(rt, [row], _localized_summaries(rt, [row], str(request.query.get("lang") or "zh")))[0],
            "path": row["path"],
            "meta": {
                "models": [item["name"] for item in model_rows],
                "search_text": (meta_row["search_text"] if meta_row else ""),
                "source": (meta_row["source"] if meta_row else "unknown"),
                "node_types": json.loads(meta_row["node_types"]) if meta_row and meta_row["node_types"] else [],
                "error": (meta_row["error"] if meta_row else ""),
            },
            "diff": ({"summary": diff_row["summary"], "verdict": diff_row["verdict"], "score": diff_row["score"],
                      "s1": diff_row["s1"], "s2": diff_row["s2"], "s3": diff_row["s3"], "s4": diff_row["s4"],
                      "detail": json.loads(diff_row["detail"] or "{}")} if diff_row else None),
            "favorite": favorite_row is not None,
            "tags": [row["name"] for row in tag_rows],
            "family": family,
            "older_id": older_id,
            "newer_id": newer_id,
            "prev_id": older_id,
            "next_id": newer_id,
            "has_workflow": bool(row["wf_len"] or row["prompt_len"]),
        })

    async def workflow(request: web.Request) -> web.Response:
        body = await request.json()
        file_id = int(body.get("id") or 0)
        row = _row(rt, file_id)
        if row is None or not os.path.exists(row["path"]) or not rt.is_allowed(row["path"]):
            raise web.HTTPNotFound()
        if row["wf_len"]:
            text = meta.read_chunk_text(row["path"], row["wf_off"], row["wf_len"])
            return _json({"ok": True, "kind": "workflow", "name": row["name"], "workflow": text})
        if row["prompt_len"]:
            text = meta.read_chunk_text(row["path"], row["prompt_off"], row["prompt_len"])
            return _json({"ok": True, "kind": "prompt", "name": row["name"], "workflow": ""})
        return _json({"ok": False, "error": "该文件没有可用的工作流元数据"}, status=200)

    # ---------------- 写操作 ----------------
    async def export(request: web.Request) -> web.Response:
        body = await request.json()
        ids = [int(value) for value in (body.get("ids") or [])]
        flat = bool(body.get("flat"))
        archive = rt.archive_dir()
        os.makedirs(archive, exist_ok=True)
        copied, failed = [], []
        for file_id in ids:
            row = _row(rt, file_id)
            if row is None or not os.path.exists(row["path"]):
                failed.append({"id": file_id, "error": "文件不存在"})
                continue
            date_name = os.path.basename(row["dir"])
            if not (len(date_name) == 10 and date_name[4] == "-"):
                date_name = time.strftime("%Y-%m-%d")
            target_dir = archive if flat else os.path.join(archive, date_name)
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, row["name"])
            if os.path.exists(target):
                stem, ext = os.path.splitext(row["name"])
                for index in range(1, 1000):
                    candidate = os.path.join(target_dir, "%s (%d)%s" % (stem, index, ext))
                    if not os.path.exists(candidate):
                        target = candidate
                        break
            try:
                shutil.copy2(row["path"], target)
                if os.path.getsize(target) != os.path.getsize(row["path"]):
                    failed.append({"id": file_id, "error": "复制后大小不一致"})
                    continue
                copied.append(target)
            except Exception as exc:
                failed.append({"id": file_id, "error": str(exc)[:120]})
        manifest = os.path.join(archive, "manifest-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
        try:
            with open(manifest, "w", encoding="utf-8") as handle:
                json.dump({"at": time.time(), "archive": archive, "copied": copied, "failed": failed}, handle,
                          ensure_ascii=False, indent=2)
        except OSError:
            manifest = ""
        return _json({"ok": True, "archive": archive, "copied": copied, "failed": failed, "manifest": manifest})

    async def delete(request: web.Request) -> web.Response:
        body = await request.json()
        ids = [int(value) for value in (body.get("ids") or [])]
        permanent = bool(body.get("permanent"))
        batch = time.strftime("%Y%m%d-%H%M%S")
        trash_root = os.path.join(rt.trash_dir(), batch)
        entries = []
        done = []
        for file_id in ids:
            row = _row(rt, file_id)
            if row is None or not os.path.exists(row["path"]):
                continue
            path = row["path"]
            try:
                if permanent:
                    os.remove(path)
                    rt.store.execute("DELETE FROM file WHERE id = ?", (file_id,))
                    rt.store.execute("DELETE FROM meta WHERE file_id = ?", (file_id,))
                    rt.store.execute("DELETE FROM file_model WHERE file_id = ?", (file_id,))
                    rt.store.execute("DELETE FROM fts_file WHERE rowid = ?", (file_id,))
                    done.append({"id": file_id, "path": path, "permanent": True})
                    continue
                relative = os.path.relpath(path, rt.roots_by_name.get(row["root"], os.path.dirname(path)))
                target = os.path.join(trash_root, relative)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(path, target)
                rt.store.execute("UPDATE file SET state = 'missing' WHERE id = ?", (file_id,))
                rt.store.execute(
                    "INSERT INTO trash_entry(batch, original_path, trash_path, size, deleted_at) VALUES(?,?,?,?,?)",
                    (batch, path, target, row["size"], time.time()),
                )
                entries.append({"original": path, "trash": target, "size": row["size"], "name": row["name"]})
                done.append({"id": file_id, "path": path, "target": target})
            except Exception as exc:
                done.append({"id": file_id, "path": path, "error": str(exc)[:120]})
        manifest_path = ""
        if entries:
            try:
                manifest_path = os.path.join(trash_root, "manifest.json")
                with open(manifest_path, "w", encoding="utf-8") as handle:
                    json.dump({"batch": batch, "at": time.time(), "entries": entries}, handle,
                              ensure_ascii=False, indent=2)
            except OSError:
                manifest_path = ""
        return _json({"ok": True, "batch": batch, "permanent": permanent, "results": done,
                      "manifest": manifest_path})

    async def trash_list(request: web.Request) -> web.Response:
        rows = rt.store.query(
            "SELECT batch, COUNT(*) AS n, SUM(size) AS bytes, MIN(deleted_at) AS at FROM trash_entry "
            "WHERE restored_at IS NULL GROUP BY batch ORDER BY batch DESC LIMIT 50"
        )
        return _json({"batches": [
            {"batch": row["batch"], "count": row["n"], "bytes": row["bytes"] or 0, "at": row["at"]}
            for row in rows
        ]})

    async def trash_restore(request: web.Request) -> web.Response:
        body = await request.json()
        batch = str(body.get("batch") or "")
        if not batch:
            raise web.HTTPBadRequest(text="batch required")
        only = [int(value) for value in (body.get("ids") or [])]
        rows = rt.store.query("SELECT * FROM trash_entry WHERE batch = ? AND restored_at IS NULL", (batch,))
        if only:
            rows = [row for row in rows if row["id"] in only]
        restored, failed = [], []
        for row in rows:
            target = row["original_path"]
            try:
                if os.path.exists(target):
                    failed.append({"original": target, "error": "目标已存在"})
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(row["trash_path"], target)
                rt.store.execute("UPDATE trash_entry SET restored_at = ? WHERE id = ?", (time.time(), row["id"]))
                rt.store.execute(
                    "UPDATE file SET state = 'ok', meta_state = 'pending' WHERE path = ?", (target,)
                )
                restored.append(target)
            except Exception as exc:
                failed.append({"original": target, "error": str(exc)[:120]})
        return _json({"ok": True, "restored": restored, "failed": failed})

    async def diff_recompute(request: web.Request) -> web.Response:
        file_id = int(request.query.get("id", 0))
        row = _row(rt, file_id)
        if row is None:
            raise web.HTTPNotFound()
        diff_row = rt.store.query_one("SELECT * FROM file_diff WHERE file_id = ?", (file_id,))
        return _json({"diff": ({"summary": diff_row["summary"], "verdict": diff_row["verdict"],
                                "score": diff_row["score"], "detail": json.loads(diff_row["detail"] or "{}")}
                               if diff_row else None)})

    async def favorite_toggle(request: web.Request) -> web.Response:
        body = await request.json()
        file_id = int(body.get("id") or 0)
        value = bool(body.get("value"))
        if value:
            rt.store.execute("INSERT OR REPLACE INTO favorite(file_id, at) VALUES(?, ?)", (file_id, time.time()))
        else:
            rt.store.execute("DELETE FROM favorite WHERE file_id = ?", (file_id,))
        return _json({"ok": True, "favorite": value})

    async def tags_get(request: web.Request) -> web.Response:
        rows = rt.store.query(
            "SELECT t.name AS name, COUNT(ft.file_id) AS n FROM tag t "
            "LEFT JOIN file_tag ft ON ft.tag_id = t.id WHERE t.kind = 'custom' "
            "GROUP BY t.name ORDER BY n DESC, t.name LIMIT 300"
        )
        return _json({"tags": [{"name": row["name"], "count": row["n"]} for row in rows]})

    async def tags_set(request: web.Request) -> web.Response:
        body = await request.json()
        file_id = int(body.get("id") or 0)
        names = [str(name).strip() for name in (body.get("tags") or []) if str(name).strip()]
        with rt.store.write() as write:
            write.execute(
                "DELETE FROM file_tag WHERE file_id = ? AND tag_id IN (SELECT id FROM tag WHERE kind = 'custom')",
                (file_id,),
            )
            for name in names:
                write.execute("INSERT OR IGNORE INTO tag(name, kind, count) VALUES(?, 'custom', 0)", (name,))
            for name in names:
                write.execute(
                    "INSERT OR IGNORE INTO file_tag(file_id, tag_id) SELECT ?, id FROM tag WHERE name = ?",
                    (file_id, name),
                )
        rt.store.execute("UPDATE tag SET count = (SELECT COUNT(*) FROM file_tag ft WHERE ft.tag_id = tag.id)")
        return _json({"ok": True, "tags": names})

    async def thumbs_usage(request: web.Request) -> web.Response:
        info = thumbs.usage(rt.thumb_dir)
        info["quota"] = rt.thumb_quota_bytes()
        stats_rows = rt.store.query("SELECT COUNT(*) AS n, SUM(hit_count) AS hits FROM thumb_stat")
        info["tracked"] = stats_rows[0]["n"] or 0
        info["hits"] = stats_rows[0]["hits"] or 0
        return _json(info)

    async def thumbs_cleanup(request: web.Request) -> web.Response:
        result = thumbs.cleanup_scored(rt.store, rt.thumb_dir, rt.thumb_quota_bytes())
        return _json(result)

    async def tags_export(request: web.Request) -> web.Response:
        tags = {}
        for row in rt.store.query(
            "SELECT t.name AS name, f.path AS path FROM tag t JOIN file_tag ft ON ft.tag_id = t.id "
            "JOIN file f ON f.id = ft.file_id WHERE t.kind = 'custom'"
        ):
            tags.setdefault(row["name"], []).append(row["path"])
        favorites = [row["path"] for row in rt.store.query(
            "SELECT f.path AS path FROM favorite fa JOIN file f ON f.id = fa.file_id")]
        return _json({"ok": True, "exported_at": time.time(), "tags": tags, "favorites": favorites})

    async def tags_import(request: web.Request) -> web.Response:
        body = await request.json()
        tags = body.get("tags") or {}
        favorites = body.get("favorites") or []
        with rt.store.write() as write:
            for name, paths in tags.items():
                write.execute("INSERT OR IGNORE INTO tag(name, kind, count) VALUES(?, 'custom', 0)", (str(name),))
                for path in paths or []:
                    write.execute(
                        "INSERT OR IGNORE INTO file_tag(file_id, tag_id) "
                        "SELECT id, (SELECT id FROM tag WHERE name = ?) FROM file WHERE path = ?",
                        (str(name), str(path)),
                    )
            for path in favorites:
                write.execute(
                    "INSERT OR IGNORE INTO favorite(file_id, at) SELECT id, ? FROM file WHERE path = ?",
                    (time.time(), str(path)),
                )
        rt.store.execute("UPDATE tag SET count = (SELECT COUNT(*) FROM file_tag ft WHERE ft.tag_id = tag.id)")
        return _json({
            "ok": True,
            "tag_links": rt.store.query_one("SELECT COUNT(*) AS n FROM file_tag")["n"],
            "favorites": rt.store.query_one("SELECT COUNT(*) AS n FROM favorite")["n"],
        })

    async def rename_files(request: web.Request) -> web.Response:
        body = await request.json()
        ids = [int(value) for value in (body.get("ids") or [])]
        template = str(body.get("template") or "{name}")
        pad = max(0, min(6, int(body.get("pad") or 0)))
        results = []
        for index, file_id in enumerate(ids, start=1):
            row = _row(rt, file_id)
            if row is None or not os.path.exists(row["path"]):
                results.append({"id": file_id, "error": "文件不存在"})
                continue
            stem, ext = os.path.splitext(row["name"])
            number = str(index).zfill(pad) if pad else str(index)
            new_name = (template.replace("{name}", stem)
                        .replace("{n}", number)
                        .replace("{ext}", ext.lstrip(".")))
            if not new_name.lower().endswith(ext.lower()):
                new_name += ext
            target = os.path.join(row["dir"], new_name)
            if os.path.normcase(target) == os.path.normcase(row["path"]):
                results.append({"id": file_id, "path": row["path"], "skipped": True})
                continue
            if os.path.exists(target):
                results.append({"id": file_id, "error": "目标已存在：" + new_name})
                continue
            try:
                os.rename(row["path"], target)
                rt.store.execute("UPDATE file SET path = ?, name = ? WHERE id = ?", (target, new_name, file_id))
                rt.store.execute("DELETE FROM fts_file WHERE rowid = ?", (file_id,))
                rt.store.execute("INSERT INTO fts_file(rowid, name, search_text) "
                                 "SELECT ?, ?, COALESCE((SELECT search_text FROM meta WHERE file_id = ?), '')",
                                 (file_id, new_name, file_id))
                results.append({"id": file_id, "path": target, "name": new_name})
            except OSError as exc:
                results.append({"id": file_id, "error": str(exc)[:120]})
        return _json({"ok": True, "results": results})

    async def move_files(request: web.Request) -> web.Response:
        body = await request.json()
        ids = [int(value) for value in (body.get("ids") or [])]
        target_dir = str(body.get("target") or "").strip()
        if not target_dir:
            raise web.HTTPBadRequest(text="target required")
        if not os.path.isabs(target_dir):
            base = rt.roots_by_name.get(str(body.get("root") or "output"), rt.roots[0][1])
            target_dir = os.path.normpath(os.path.join(base, target_dir))
        if not rt.is_allowed(target_dir):
            raise web.HTTPForbidden(text="target outside allowed roots")
        os.makedirs(target_dir, exist_ok=True)
        results = []
        for file_id in ids:
            row = _row(rt, file_id)
            if row is None or not os.path.exists(row["path"]):
                results.append({"id": file_id, "error": "文件不存在"})
                continue
            target = os.path.join(target_dir, row["name"])
            if os.path.exists(target):
                results.append({"id": file_id, "error": "目标已存在"})
                continue
            try:
                shutil.move(row["path"], target)
                rt.store.execute("UPDATE file SET path = ?, dir = ? WHERE id = ?", (target, target_dir, file_id))
                results.append({"id": file_id, "path": target})
            except OSError as exc:
                results.append({"id": file_id, "error": str(exc)[:120]})
        return _json({"ok": True, "target": target_dir, "results": results})

    async def trash_detail(request: web.Request) -> web.Response:
        batch = request.query.get("batch", "")
        rows = rt.store.query("SELECT * FROM trash_entry WHERE batch = ? ORDER BY id", (batch,))
        return _json({"batch": batch, "entries": [
            {"id": row["id"], "name": os.path.basename(row["original_path"]),
             "original": row["original_path"], "size": row["size"], "restored": bool(row["restored_at"])}
            for row in rows
        ]})

    async def trash_purge(request: web.Request) -> web.Response:
        body = await request.json()
        batch = str(body.get("batch") or "")
        rows = rt.store.query("SELECT * FROM trash_entry WHERE batch = ? AND restored_at IS NULL", (batch,))
        removed = 0
        for row in rows:
            try:
                if os.path.exists(row["trash_path"]):
                    os.remove(row["trash_path"])
                    removed += 1
            except OSError:
                pass
            rt.store.execute("DELETE FROM trash_entry WHERE id = ?", (row["id"],))
        batch_dir = os.path.join(rt.trash_dir(), batch)
        if os.path.isdir(batch_dir):
            shutil.rmtree(batch_dir, ignore_errors=True)
        return _json({"ok": True, "removed": removed})

    routes = [
        ("POST", api + "/heartbeat", heartbeat),
        ("POST", api + "/rename", rename_files),
        ("POST", api + "/move", move_files),
        ("GET", api + "/trash/detail", trash_detail),
        ("POST", api + "/trash/purge", trash_purge),
        ("GET", api + "/tags/export", tags_export),
        ("POST", api + "/tags/import", tags_import),
        ("POST", api + "/favorite", favorite_toggle),
        ("GET", api + "/tags", tags_get),
        ("POST", api + "/tags", tags_set),
        ("GET", api + "/thumbs", thumbs_usage),
        ("POST", api + "/thumbs/cleanup", thumbs_cleanup),
        ("GET", api + "/stats", stats),
        ("POST", api + "/scan", scan),
        ("POST", api + "/settings", settings),
        ("GET", api + "/dirs", dirs),
        ("GET", api + "/models", models),
        ("POST", api + "/query", query),
        ("GET", api + "/thumb", thumb),
        ("GET", api + "/raw", raw),
        ("GET", api + "/download", download),
        ("GET", api + "/detail", detail),
        ("GET", api + "/diff", diff_recompute),
        ("POST", api + "/workflow", workflow),
        ("POST", api + "/export", export),
        ("POST", api + "/delete", delete),
        ("GET", api + "/trash", trash_list),
        ("POST", api + "/trash/restore", trash_restore),
    ]
    for method, path, handler in routes:
        wrapped = (lambda inner: (lambda request: _timed(inner, request)))(handler)
        wrapped.__name__ = handler.__name__
        app.router.add_route(method, path, wrapped)
