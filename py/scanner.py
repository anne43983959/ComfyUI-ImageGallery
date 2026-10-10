"""扫描、解析、族链与 diff 落库。

一次目录级流水线（对应实施方案 §6 + §7）：
  walk 目录 -> 增量写入 file 表 -> 按 mtime 顺序解析未完成项 -> 相邻比对 -> 族链 -> file_diff
按目录切片执行，方便限速线程逐片让出 CPU。
"""
import json
import os
import time

from . import diff, meta

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}

JOB_SCAN = "scan"


def media_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "other"


class Scanner:
    def __init__(self, store, roots, session_hours: float = diff.SESSION_GAP_HOURS) -> None:
        self.store = store
        self.roots = roots
        self.session_hours = session_hours
        self.th_same = diff.TH_SAME
        self.th_weak = diff.TH_WEAK

    # ---------------- 阶段一：遍历与登记 ----------------
    def scan_roots(self, stop=None, progress=None) -> dict:
        seen_paths = set()
        added = 0
        updated = 0
        checked = 0
        for root_name, root_path in self.roots:
            if not root_path or not os.path.isdir(root_path):
                continue
            for dirpath, _dirnames, filenames in os.walk(root_path):
                for name in filenames:
                    if stop is not None and stop.is_set():
                        break
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
                        continue
                    full = os.path.normpath(os.path.join(dirpath, name))
                    seen_paths.add(full)
                    checked += 1
                    try:
                        stat = os.stat(full)
                    except OSError:
                        continue
                    row = self.store.query_one("SELECT id, size, mtime FROM file WHERE path = ?", (full,))
                    if row is None:
                        self._insert_file(root_name, full, stat)
                        added += 1
                    elif abs(row["mtime"] - stat.st_mtime) > 0.001 or row["size"] != stat.st_size:
                        self._update_file(row["id"], full, stat)
                        updated += 1
                    if progress and checked % 500 == 0:
                        progress(checked, 0)
        missing = self._mark_missing(seen_paths)
        return {"checked": checked, "added": added, "updated": updated, "missing": missing}

    def _insert_file(self, root_name: str, path: str, stat) -> None:
        self.store.execute(
            "INSERT OR IGNORE INTO file(root, path, dir, name, ext, size, mtime, ctime, kind, state, meta_state, scanned_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'ok','pending',?)",
            (
                root_name, path, os.path.dirname(path), os.path.basename(path),
                os.path.splitext(path)[1].lower(), stat.st_size, stat.st_mtime,
                getattr(stat, "st_ctime", stat.st_mtime), media_kind(path), time.time(),
            ),
        )

    def _update_file(self, file_id: int, path: str, stat) -> None:
        self.store.execute(
            "UPDATE file SET size = ?, mtime = ?, meta_state = 'pending', state = 'ok', scanned_at = ? WHERE id = ?",
            (stat.st_size, stat.st_mtime, time.time(), file_id),
        )

    def _mark_missing(self, seen_paths: set) -> int:
        count = 0
        for row in self.store.query("SELECT id, path FROM file WHERE state = 'ok'"):
            if row["path"] not in seen_paths and not os.path.exists(row["path"]):
                self.store.execute("UPDATE file SET state = 'missing' WHERE id = ?", (row["id"],))
                count += 1
        return count

    # ---------------- 阶段二：目录级解析 + 族链 ----------------
    def pending_directories(self, limit: int = 50) -> list:
        rows = self.store.query(
            "SELECT root, dir, MAX(mtime) AS latest FROM file WHERE state = 'ok' AND dir IN "
            "(SELECT DISTINCT dir FROM file WHERE state = 'ok' AND meta_state = 'pending') "
            "GROUP BY root, dir ORDER BY latest DESC LIMIT ?",
            (limit,),
        )
        return [(row["root"], row["dir"]) for row in rows]

    def index_directory(self, root_name: str, directory: str, stop=None) -> dict:
        rows = self.store.query(
            "SELECT * FROM file WHERE root = ? AND dir = ? AND state = 'ok' ORDER BY mtime ASC, id ASC",
            (root_name, directory),
        )
        if not rows:
            return {"dir": directory, "files": 0, "parsed": 0, "families": 0}

        # 该目录的既有族信息：找到最后一个族作为延续起点
        last_family = self.store.query_one(
            "SELECT id, rep_file_id, file_count FROM family WHERE root = ? AND dir = ? ORDER BY id DESC LIMIT 1",
            (root_name, directory),
        )
        family_id = last_family["id"] if last_family else None
        rep_struct = None
        rep_file_id = last_family["rep_file_id"] if last_family else None
        if rep_file_id:
            rep_struct = self._structure_of_file(rep_file_id)
        since_rep = last_family["file_count"] if last_family else 0

        prev_struct = None
        prev_row = None
        parsed = 0
        families = 0
        for row in rows:
            if stop is not None and stop.is_set():
                break
            if row["meta_state"] == "pending":
                ok = self._parse_and_store(row["id"], row["path"], row["mtime"])
                if not ok:
                    self.store.execute(
                        "UPDATE file SET meta_state = 'failed', family_id = NULL WHERE id = ?", (row["id"],)
                    )
                    prev_struct, prev_row = None, None
                    continue
                parsed += 1
                row = self.store.query_one("SELECT * FROM file WHERE id = ?", (row["id"],))
            struct = self._structure_of_file(row["id"])
            if struct is None:
                # 视频或非 ComfyUI 产出的图：没有可比较结构，既不参与 diff，
                # 也不能把前后两条链断开（prev 保持不变，链路继续）。
                self._store_diff(row["id"], None, None, {
                    "s1": 0.0, "s2": 0.0, "s3": 0.0, "s4": 0.0, "score": 0.0,
                    "verdict": "na", "reason": "no-structure",
                    "summary": "无工作流元数据（视频或非 ComfyUI 产出），不参与差异比对",
                    "detail": {}, "algo_ver": diff.ALGO_VER,
                })
                self.store.execute("UPDATE file SET family_id = NULL WHERE id = ?", (row["id"],))
                continue
            if prev_struct is None:
                family_id = self._new_family(root_name, directory, row["id"])
                families += 1
                rep_struct, rep_file_id, since_rep = struct, row["id"], 0
                self._store_diff(row["id"], None, family_id, {
                    "s1": 1.0, "s2": 1.0, "s3": 1.0, "s4": 1.0, "score": 1.0,
                    "verdict": "start", "reason": "first",
                    "summary": "工作流起点（本目录第一张可比对图）", "detail": {}, "algo_ver": diff.ALGO_VER,
                })
            else:
                gap_hours = (row["mtime"] - prev_row["mtime"]) / 3600.0
                result = diff.compare(
                    prev_struct, struct, gap_hours, family_id, prev_row["id"],
                    session_hours=self.session_hours, th_same=self.th_same, th_weak=self.th_weak,
                )
                if (result["verdict"] == "new" and result.get("reason") is None
                        and rep_struct is not None and rep_file_id != prev_row["id"]):
                    rep_result = diff.compare(
                        rep_struct, struct, 0.0, family_id, rep_file_id,
                        session_hours=0.0, th_same=self.th_same, th_weak=self.th_weak,
                    )
                    if rep_result["verdict"] in ("same", "weak"):
                        result["verdict"] = "weak"
                        result["summary"] = "与上一张差异较大，但仍与本次会话代表图同源；" + result["summary"]
                if result["verdict"] == "new":
                    family_id = self._new_family(root_name, directory, row["id"])
                    families += 1
                    rep_struct, rep_file_id, since_rep = struct, row["id"], 0
                else:
                    since_rep += 1
                    if since_rep >= 20 or result["score"] < 0.85:
                        rep_struct, rep_file_id, since_rep = struct, row["id"], 0
                    self._touch_family(family_id, row["id"])
                self._store_diff(row["id"], prev_row["id"], family_id, result)
            self.store.execute("UPDATE file SET family_id = ? WHERE id = ?", (family_id, row["id"]))
            prev_struct, prev_row = struct, row

        return {"dir": directory, "files": len(rows), "parsed": parsed, "families": families}

    def _structure_of_file(self, file_id: int):
        row = self.store.query_one("SELECT path, prompt_off, prompt_len FROM file WHERE id = ?", (file_id,))
        if row is None or not row["prompt_len"]:
            return None
        try:
            raw = meta.read_chunk_text(row["path"], row["prompt_off"], row["prompt_len"])
            return diff.structure(raw)
        except Exception:
            return None

    def _parse_and_store(self, file_id: int, path: str, mtime: float) -> bool:
        width, height = 0, 0
        duration = 0.0
        kind = media_kind(path)
        if kind == "image":
            try:
                from PIL import Image
                with Image.open(path) as image:
                    width, height = image.size
            except Exception:
                width, height = 0, 0
        elif kind == "video":
            from . import video
            width, height, duration = video.probe(path)
        try:
            chunks = meta.read_text_chunks(path)
        except Exception:
            return False
        prompt_entry = chunks.get("prompt")
        workflow_entry = chunks.get("workflow")
        prompt_text = prompt_entry[0] if prompt_entry else ""
        workflow_text = workflow_entry[0] if workflow_entry else ""
        if not prompt_text:
            # 视频或非 ComfyUI 产出的图：登记但无可抽取内容，仍要能在画廊里看到
            self.store.execute(
                "UPDATE file SET meta_state = 'done', node_count = 0, fingerprint = '', width = ?, height = ?, "
                "duration = ? WHERE id = ?",
                (width, height, duration, file_id),
            )
            return True  # 已处理完毕，只是没有可抽取的元数据（视频 / 非 ComfyUI 产出的图）
        info = meta.extract(prompt_text, workflow_text)
        now = time.time()
        with self.store.write() as write:
            write.execute(
                "INSERT INTO meta(file_id, search_text, text_hash, models, source, node_types, error, parsed_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET search_text=excluded.search_text, "
                "text_hash=excluded.text_hash, models=excluded.models, source=excluded.source, "
                "node_types=excluded.node_types, error=excluded.error, parsed_at=excluded.parsed_at",
                (
                    file_id, info["search_text"], "", json.dumps(info["models"], ensure_ascii=False),
                    info["source"], json.dumps(info["node_types"], ensure_ascii=False), info["error"], now,
                ),
            )
            write.execute("DELETE FROM file_model WHERE file_id = ?", (file_id,))
            for name in info["models"]:
                write.execute("INSERT OR IGNORE INTO model(name) VALUES(?)", (name,))
            for name in info["models"]:
                write.execute(
                    "INSERT OR IGNORE INTO file_model(file_id, model_id) "
                    "SELECT ?, id FROM model WHERE name = ?",
                    (file_id, name),
                )
            if prompt_entry:
                write.execute(
                    "UPDATE file SET meta_state = 'done', node_count = ?, fingerprint = ?, "
                    "prompt_off = ?, prompt_len = ?, width = ?, height = ?, duration = ? WHERE id = ?",
                    (info["node_count"], info["fingerprint"], prompt_entry[1], prompt_entry[2], width, height, duration, file_id),
                )
            else:
                write.execute("UPDATE file SET meta_state = 'done', width = ?, height = ?, duration = ? WHERE id = ?",
                              (width, height, duration, file_id))
            if workflow_entry:
                write.execute("UPDATE file SET wf_off = ?, wf_len = ? WHERE id = ?", (workflow_entry[1], workflow_entry[2], file_id))
            # FTS 同步
            name = os.path.basename(path)
            write.execute("DELETE FROM fts_file WHERE rowid = ?", (file_id,))
            write.execute("INSERT INTO fts_file(rowid, name, search_text) VALUES(?,?,?)", (file_id, name, info["search_text"]))
        return True

    # ---------------- 族与 diff 落库 ----------------
    def _new_family(self, root_name: str, directory: str, file_id: int) -> int:
        with self.store.write() as write:
            write.execute(
                "INSERT INTO family(root, dir, start_file_id, end_file_id, rep_file_id, file_count, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (root_name, directory, file_id, file_id, file_id, 1, time.time()),
            )
        row = self.store.query_one("SELECT id FROM family WHERE root = ? AND dir = ? ORDER BY id DESC LIMIT 1", (root_name, directory))
        return row["id"]

    def _touch_family(self, family_id, file_id: int) -> None:
        if family_id is None:
            return
        self.store.execute(
            "UPDATE family SET end_file_id = ?, file_count = file_count + 1 WHERE id = ?",
            (file_id, family_id),
        )

    def _store_diff(self, file_id: int, prev_file_id, family_id, result: dict) -> None:
        self.store.execute(
            "INSERT INTO file_diff(file_id, prev_file_id, family_id, s1, s2, s3, s4, score, verdict, summary, detail, algo_ver, computed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET "
            "prev_file_id=excluded.prev_file_id, family_id=excluded.family_id, s1=excluded.s1, s2=excluded.s2, "
            "s3=excluded.s3, s4=excluded.s4, score=excluded.score, verdict=excluded.verdict, "
            "summary=excluded.summary, detail=excluded.detail, algo_ver=excluded.algo_ver, computed_at=excluded.computed_at",
            (
                file_id, prev_file_id, family_id,
                result.get("s1"), result.get("s2"), result.get("s3"), result.get("s4"), result.get("score"),
                result.get("verdict"), result.get("summary", ""),
                json.dumps(result.get("detail") or {}, ensure_ascii=False),
                result.get("algo_ver", diff.ALGO_VER), time.time(),
            ),
        )
