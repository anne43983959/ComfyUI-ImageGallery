# -*- coding: utf-8 -*-
r"""独立开发服务器：不装进 ComfyUI 也能打开画廊页面。

  python tools\dev_server.py [port] [--synthetic]

默认对真实 output 目录只读浏览（缩略图与索引写在项目 cache 下）。
--synthetic 使用 api_test 生成的合成目录，便于安全演示删除等功能。
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)

SYNTHETIC = "--synthetic" in sys.argv
PORT = 39420
for argument in sys.argv[1:]:
    if argument.isdigit():
        PORT = int(argument)

CACHE = os.path.join(ROOT, "cache", "dev")
os.environ["IG_CACHE_DIR"] = CACHE
os.environ["IG_DB_PATH"] = os.path.join(CACHE, "index.db")
if SYNTHETIC:
    os.environ["IG_OUTPUT_DIR"] = os.path.join(ROOT, "cache", "apitest", "output")
    os.environ["IG_TEMP_DIR"] = os.path.join(ROOT, "cache", "apitest", "temp")
os.environ.setdefault("IG_OUTPUT_DIR", r"D:\ComfyUI\output")
os.environ.setdefault("IG_TEMP_DIR", r"D:\ComfyUI\temp")


async def main():
    from aiohttp import web

    from py.routes import registry

    app = web.Application()
    registry.register_all(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()
    print("gallery dev server: http://127.0.0.1:%d/gallery" % PORT, flush=True)
    print("output root:", os.environ.get("IG_OUTPUT_DIR"), flush=True)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
