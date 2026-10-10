"""视频探测与封面抽帧（复用 ComfyUI 自带的 imageio_ffmpeg，无新增依赖）。"""
import os
import subprocess

from PIL import Image


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe() or ""
    except Exception:
        return ""


def probe(path: str) -> tuple:
    """返回 (宽, 高, 时长秒)；失败给全 0，绝不抛异常。"""
    try:
        import imageio_ffmpeg
        reader = imageio_ffmpeg.read_frames(path)
        meta = next(reader)
        size = meta.get("size") or (0, 0)
        duration = float(meta.get("duration") or 0)
        try:
            reader.close()
        except Exception:
            pass
        return int(size[0]), int(size[1]), duration
    except Exception:
        return 0, 0, 0.0


def cover(path: str, target: str, at: float = 1.0, size: int = 512) -> bool:
    """抽一帧写成 webp 缩略图；成功返回 True。"""
    exe = ffmpeg_exe()
    if not exe or not os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temp = target + ".frame.png"
    command = [exe, "-y", "-loglevel", "error", "-ss", str(at), "-i", path,
               "-frames:v", "1", "-vf", "scale=%d:-2" % size, temp]
    try:
        result = subprocess.run(command, capture_output=True, timeout=40)
        if result.returncode != 0 or not os.path.exists(temp):
            return False
        with Image.open(temp) as image:
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.save(target, "WEBP", quality=82, method=4)
        return True
    except Exception:
        return False
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass


def human_duration(seconds: float) -> str:
    total = int(seconds or 0)
    if total <= 0:
        return ""
    return "%d:%02d" % (total // 60, total % 60)
