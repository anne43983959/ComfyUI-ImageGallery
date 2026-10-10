"""ComfyUI-ImageGallery 入口。

只做三件事：声明 WEB_DIRECTORY、注册路由、打印一行状态。
所有动作都包在 try 里：注册失败也绝不允许影响 ComfyUI 启动。
"""
import logging
import os
import sys

logger = logging.getLogger("image_gallery")

ROOT = os.path.dirname(os.path.realpath(__file__))
WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

REGISTERED = False


def _load_register_all():
    try:
        from .py.routes.registry import register_all
        return register_all
    except ImportError:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import importlib
        return importlib.import_module("py.routes.registry").register_all


def _register() -> bool:
    try:
        register_all = _load_register_all()
    except Exception as exc:
        logger.warning("[ImageGallery] cannot load routes module: %s", exc)
        return False

    try:
        from server import PromptServer
    except ImportError:
        # 独立运行（冒烟测试、pytest）时没有 server 模块，静默跳过
        return False

    server = getattr(PromptServer, "instance", None)
    if server is None:
        return False

    try:
        register_all(server.app)
    except Exception as exc:
        logger.exception("[ImageGallery] route registration failed: %s", exc)
        return False

    logger.info("[ImageGallery] routes ready at /gallery")
    return True


REGISTERED = _register()
