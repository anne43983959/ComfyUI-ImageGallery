"""画廊页面：服务端渲染 HTML 壳。"""
import jinja2
from aiohttp import web

from .. import config

_env = None


def _environment() -> jinja2.Environment:
    global _env
    if _env is None:
        _env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(config.TEMPLATE_DIR),
            autoescape=True,
        )
    return _env


def register(app: web.Application) -> None:
    async def gallery_page(request: web.Request) -> web.Response:
        html = _environment().get_template("gallery.html").render(
            version=config.build_version(),
            api_base=config.API_PATH,
            static_base=config.STATIC_PATH,
        )
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    app.router.add_get(config.PAGE_PATH, gallery_page)
    app.router.add_get(config.PAGE_PATH + "/", gallery_page)
