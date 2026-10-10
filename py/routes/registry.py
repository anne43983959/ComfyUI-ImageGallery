"""路由注册总入口：静态挂载 + 页面 + 健康检查 + API + 后台线程。"""
import os

from aiohttp import web

from .. import config
from ..runtime import get as runtime
from . import api, health, pages


def register_all(app: web.Application) -> None:
    rt = runtime()

    if os.path.isdir(config.STATIC_DIR):
        try:
            app.router.add_static(config.STATIC_PATH, config.STATIC_DIR, name="image_gallery_static")
        except (RuntimeError, ValueError):
            pass

    pages.register(app)
    health.register(app)
    api.register(app)

    async def _on_startup(app):
        rt.start()

    async def _on_shutdown(app):
        rt.stop()

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)
