"""路径、路由前缀与版本号。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "templates")
STATIC_DIR = os.path.join(ROOT, "static")
CACHE_DIR = os.environ.get("IG_CACHE_DIR") or os.path.join(ROOT, "cache")

PLUGIN_NAME = "ComfyUI-ImageGallery"
MILESTONE = "M8"
VERSION = "0.6.2"

SESSION_GAP_HOURS = 6.0

PAGE_PATH = "/gallery"
STATIC_PATH = "/gallery_static"
API_PATH = "/api/gallery"


def build_version() -> str:
    """templates 与 static 下所有文件的最大 mtime，作为静态资源缓存穿透号。"""
    newest = 0.0
    for base in (TEMPLATE_DIR, STATIC_DIR):
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
                except OSError:
                    continue
    return ("%.3f" % newest) if newest else VERSION
