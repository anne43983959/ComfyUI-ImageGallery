"""健康检查与版本信息。"""
from aiohttp import web

from .. import config
from ..runtime import get as runtime


def register(app: web.Application) -> None:
    async def hello(request: web.Request) -> web.Response:
        rt = runtime()
        return web.json_response(
            {
                "success": True,
                "plugin": config.PLUGIN_NAME,
                "version": config.VERSION,
                "milestone": config.MILESTONE,
                "routes": {
                    "page": config.PAGE_PATH,
                    "static": config.STATIC_PATH,
                    "api": config.API_PATH,
                },
                "roots": [{"name": name, "path": path} for name, path in rt.roots],
                "session_hours": rt.session_hours,
            },
            dumps=lambda obj: __import__("json").dumps(obj, ensure_ascii=False),
        )

    app.router.add_get(config.API_PATH + "/hello", hello)
