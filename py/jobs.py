"""后台任务线程与生成期 QoS（实施方案 §18 / §19）。

一个守护线程承载全部重活：扫描、解析、族链、缩略图回收。
节奏按四档阶梯调整；请求时延超过阈值自动降档（AIMD），生成期间不停机。
"""
import os
import shutil
import threading
import time

from . import thumbs

DUTY_IDLE_NO_VIEWER = 0.060
DUTY_IDLE_VIEWER = 0.010
DUTY_GENERATING_VIEWER = 0.060
DUTY_GENERATING_NO_VIEWER = 0.200
VIEWER_TIMEOUT = 30.0
LATENCY_WINDOW = 40
LATENCY_HIGH_MS = 30.0
DEGRADE_STEP = 0.04
RECOVER_AFTER = 60.0
CLEANUP_INTERVAL = 3600.0


def generating() -> bool:
    """ComfyUI 是否正在执行（生成线程独立于事件循环）。"""
    try:
        from server import PromptServer
        server = getattr(PromptServer, "instance", None)
        if server is None:
            return False
        running, queued = server.prompt_queue.get_current_queue_volatile()
        return bool(running) or bool(queued)
    except Exception:
        return False


class JobRunner(threading.Thread):
    def __init__(self, store, scanner, cache_dir: str, quota_bytes: int = thumbs.DEFAULT_QUOTA_BYTES) -> None:
        super().__init__(name="image-gallery-worker", daemon=True)
        self.store = store
        self.scanner = scanner
        self.cache_dir = cache_dir
        self.quota_bytes = quota_bytes
        self._stop = threading.Event()
        self._viewers = {}
        self._latencies = []
        self._extra_sleep = 0.0
        self._last_degrade = 0.0
        self._last_cleanup = 0.0
        self.state = {"phase": "idle", "dir": "", "parsed": 0, "families": 0, "duty": 0.0, "sleep": 0.0}
        # 必须可重入：sleep_for 持锁期间会调用 p95_ms()，普通 Lock 会自死锁并冻结事件循环
        self._lock = threading.RLock()

    # ---------- 外部信号 ----------
    def stop(self) -> None:
        self._stop.set()

    def heartbeat(self, viewer_id: str) -> None:
        with self._lock:
            self._viewers[viewer_id] = time.time()

    def viewer_count(self) -> int:
        now = time.time()
        with self._lock:
            for key in [k for k, ts in self._viewers.items() if now - ts > VIEWER_TIMEOUT]:
                self._viewers.pop(key, None)
            return len(self._viewers)

    def note_request(self, duration_ms: float) -> None:
        with self._lock:
            self._latencies.append(duration_ms)
            if len(self._latencies) > LATENCY_WINDOW:
                self._latencies.pop(0)

    def p95_ms(self) -> float:
        with self._lock:
            if not self._latencies:
                return 0.0
            ordered = sorted(self._latencies)
            return ordered[int(len(ordered) * 0.95) - 1 if len(ordered) > 1 else 0]

    def sleep_for(self) -> float:
        busy = generating()
        viewers = self.viewer_count()
        if busy:
            base = DUTY_GENERATING_VIEWER if viewers else DUTY_GENERATING_NO_VIEWER
        else:
            base = DUTY_IDLE_VIEWER if viewers else DUTY_IDLE_NO_VIEWER
        with self._lock:
            if self.p95_ms() > LATENCY_HIGH_MS:
                self._extra_sleep = min(self._extra_sleep + DEGRADE_STEP, 0.5)
                self._last_degrade = time.time()
            elif self._extra_sleep > 0 and time.time() - self._last_degrade > RECOVER_AFTER:
                self._extra_sleep = max(0.0, self._extra_sleep - DEGRADE_STEP)
                self._last_degrade = time.time()
            extra = self._extra_sleep
        return base + extra

    # ---------- 主循环 ----------
    def run(self) -> None:
        time.sleep(2.0)  # 让 ComfyUI 先把端口与界面准备好
        while not self._stop.is_set():
            try:
                did_work = self._tick()
            except Exception as exc:  # 后台线程绝不能把异常抛出去
                self.state["phase"] = "error"
                self.state["error"] = str(exc)[:200]
                did_work = False
            if not did_work:
                self._idle_sleep(1.0)
            else:
                self._idle_sleep(self.sleep_for())

    def _idle_sleep(self, seconds: float) -> None:
        self.state["sleep"] = round(seconds, 3)
        self._stop.wait(seconds)

    def _tick(self) -> bool:
        now = time.time()
        if now - self._last_cleanup > CLEANUP_INTERVAL:
            self._last_cleanup = now
            threading.Thread(
                target=thumbs.cleanup_scored,
                args=(self.store, self.cache_dir, self.quota_bytes),
                daemon=True,
            ).start()
            threading.Thread(target=self._purge_old_trash, daemon=True).start()

        pending_dirs = self.scanner.pending_directories(limit=1)
        if pending_dirs:
            root_name, directory = pending_dirs[0]
            self.state["phase"] = "index"
            self.state["dir"] = directory
            result = self.scanner.index_directory(root_name, directory, stop=self._stop)
            self.state["parsed"] = result.get("parsed", 0)
            self.state["families"] = result.get("families", 0)
            return result.get("files", 0) > 0

        if self.state.get("phase") != "idle":
            self.state["phase"] = "idle"
            self.state["dir"] = ""
        if self._needs_scan():
            self.state["phase"] = "scan"
            self.store.job_start("scan", "scan", 0, "遍历目录")
            counts = self.scanner.scan_roots(stop=self._stop)
            self.store.job_update("scan", done=counts["checked"], total=counts["checked"], state="done",
                                  message="新增 %d，更新 %d，缺失 %d" % (counts["added"], counts["updated"], counts["missing"]))
            self.state["scan"] = counts
            return counts["checked"] > 0
        return False

    def _purge_old_trash(self) -> None:
        """回收站里超过保留天数的批次自动清理（默认 30 天，可在设置里改）。"""
        try:
            keep_days = float(self.store.get_setting("trash_keep_days", "30") or 30)
        except ValueError:
            keep_days = 30.0
        if keep_days <= 0:
            return
        cutoff = time.time() - keep_days * 86400
        rows = self.store.query(
            "SELECT batch, MAX(deleted_at) AS at FROM trash_entry WHERE restored_at IS NULL GROUP BY batch"
        )
        trash_root = self.store.get_setting("trash_dir", "") or ""
        for row in rows:
            if not row["at"] or row["at"] > cutoff:
                continue
            entries = self.store.query(
                "SELECT * FROM trash_entry WHERE batch = ? AND restored_at IS NULL", (row["batch"],)
            )
            for entry in entries:
                try:
                    if os.path.exists(entry["trash_path"]):
                        os.remove(entry["trash_path"])
                except OSError:
                    pass
                self.store.execute("DELETE FROM trash_entry WHERE id = ?", (entry["id"],))
            if trash_root:
                batch_dir = os.path.join(trash_root, row["batch"])
                if os.path.isdir(batch_dir):
                    shutil.rmtree(batch_dir, ignore_errors=True)

    def _needs_scan(self) -> bool:
        last = float(self.store.get_setting("last_scan_at", "0") or 0)
        if time.time() - last > 600:
            self.store.set_setting("last_scan_at", str(time.time()))
            return True
        return False
