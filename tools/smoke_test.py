# -*- coding: utf-8 -*-
r"""独立冒烟测试：不需要 ComfyUI 也能验证路由、静态资源与入口注册。

  python tools\smoke_test.py

做两件事：
1) 起一个真实的 aiohttp 服务器，逐个 HTTP 请求校验状态码与关键内容；
2) 用一个假的 server.PromptServer 复刻 ComfyUI 的加载方式（spec_from_file_location 指向 __init__.py），
   验证入口确实把路由挂上了、WEB_DIRECTORY 正确。
"""
import asyncio
import importlib.util
import json
import os
import sys
import threading
import time
import types
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

PORT = int(os.environ.get("IG_SMOKE_PORT", "39391"))

CACHE = os.path.join(ROOT, "cache", "smoke")
os.environ["IG_CACHE_DIR"] = CACHE
os.environ["IG_DB_PATH"] = os.path.join(CACHE, "index.db")
os.environ["IG_OUTPUT_DIR"] = os.path.join(CACHE, "output")
os.environ["IG_TEMP_DIR"] = os.path.join(CACHE, "temp")
os.makedirs(os.path.join(CACHE, "output"), exist_ok=True)
os.makedirs(os.path.join(CACHE, "temp"), exist_ok=True)

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    prefix = "PASS " if ok else "FAIL "
    print(prefix + name + (("   [" + detail + "]") if detail else ""))


def http_get(path):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    req = urllib.request.Request(url, headers={"accept": "*/*"})
    with urllib.request.urlopen(req, timeout=8) as res:
        return res.status, res.read().decode("utf-8", "replace")


def http_status(path):
    try:
        http_get(path)
        return 200
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return -1


def wait_ready(timeout=15.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            http_get("/api/gallery/hello")
            return True
        except Exception:
            time.sleep(0.15)
    return False


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


def check_http():
    status, body = http_get("/api/gallery/hello")
    data = {}
    try:
        data = json.loads(body)
    except ValueError:
        pass
    record("GET /api/gallery/hello", status == 200 and data.get("success") is True, body[:110].replace("\n", " "))
    record("hello 内容", data.get("plugin") == "ComfyUI-ImageGallery", str(data.get("routes")))

    status, body = http_get("/gallery")
    ok = status == 200 and 'id="ig-grid"' in body and "/gallery_static/js/gallery.js" in body
    record("GET /gallery", ok, "%d 字符" % len(body))
    record("页面注入缓存号", "?v=" in body)

    status, body = http_get("/gallery_static/js/gallery.js")
    record("GET gallery.js", status == 200 and "ig-grid" in body and "fetch(" in body, "%d 字符" % len(body))

    status, body = http_get("/gallery_static/css/gallery.css")
    record("GET gallery.css", status == 200 and "--ig-bg" in body, "%d 字符" % len(body))

    record("404 兜底", http_status("/gallery_static/not-exist.txt") == 404)


def check_entry_with_stub():
    from aiohttp import web

    app = web.Application()

    class PromptServer:
        instance = types.SimpleNamespace(app=app)

    server_mod = types.ModuleType("server")
    server_mod.PromptServer = PromptServer
    sys.modules["server"] = server_mod

    spec = importlib.util.spec_from_file_location(
        "ComfyUI_ImageGallery_smoke",
        os.path.join(ROOT, "__init__.py"),
        submodule_search_locations=[ROOT],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    paths = sorted(
        {route.resource.canonical for route in app.router.routes() if hasattr(route, "resource")}
    )
    record("入口 REGISTERED", getattr(module, "REGISTERED", False) is True)
    record("入口 WEB_DIRECTORY", getattr(module, "WEB_DIRECTORY", None) == "./web")
    record("入口挂载路由", "/gallery" in paths and "/api/gallery/hello" in paths, ", ".join(paths))
    record("入口不抛异常", True)


def main():
    holder = {}
    thread = threading.Thread(target=serve, args=(holder,), daemon=True)
    thread.start()
    if wait_ready():
        check_http()
    else:
        record("服务器就绪", False, "等待超时")

    loop = holder.get("loop")
    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)

    check_entry_with_stub()

    failed = [item for item in RESULTS if not item[1]]
    print("")
    print("SMOKE %s  (%d 项检查，%d 项失败)" % ("OK" if not failed else "FAILED", len(RESULTS), len(failed)))
    for name, _ok, detail in failed:
        print("  - " + name + "  " + detail)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
