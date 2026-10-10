"""元数据抽取：只读文件头，取输入性文本、模型列表与结构指纹。

对应实施方案 §5 / §6：
- 不落库完整 prompt/workflow，只记录偏移量与抽取结果；
- 文本默认全收、黑名单排除；模型单独归一化；
- 不采集节点输出（workflow 块里的 outputs、展示类节点）。
"""
import hashlib
import json
import os
import re
import zlib

MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".gguf", ".sft", ".pkl", ".safetensors.index.json")
MODEL_KEYS = {
    "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name",
    "control_net_name", "style_model_name", "model_name", "model",
}
DISPLAY_NODE_RE = re.compile(r"^(showtext|showany|previewtext|displaytext|noteplus|showstring)", re.IGNORECASE)
HEX64_RE = re.compile(r"^[0-9a-fA-F]{64,}$")
B64_RE = re.compile(r"^[A-Za-z0-9+/=]{64,}$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
ENUM_NOISE = {"randomize", "fixed", "increment", "decrement", "true", "false", "none", "enable", "disable"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
MAX_TEXT = 200 * 1024
ALGO_VER = 1


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "other"


def read_text_chunks(path: str) -> dict:
    """按 PNG 块跳跃，只读 tEXt/iTXt/zTXt，返回 {关键字: (文本, 数据段偏移, 长度)}。"""
    found = {}
    with open(path, "rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            return found
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            length = int.from_bytes(header[:4], "big")
            chunk_type = header[4:8]
            data_off = handle.tell()
            if chunk_type in (b"tEXt", b"iTXt", b"zTXt"):
                raw = handle.read(length)
                try:
                    if chunk_type == b"tEXt":
                        key, _, value = raw.partition(b"\x00")
                        found[key.decode("latin1")] = (value.decode("utf-8", "replace"), data_off, length)
                    elif chunk_type == b"zTXt":
                        key, _, rest = raw.partition(b"\x00")
                        text = zlib.decompress(rest[1:]).decode("utf-8", "replace")
                        found[key.decode("latin1")] = (text, data_off, length)
                    else:
                        key, _, rest = raw.partition(b"\x00")
                        comp = rest[0] if rest else 0
                        rest = rest[2:]
                        _, _, rest = rest.partition(b"\x00")
                        _, _, text = rest.partition(b"\x00")
                        if comp == 1:
                            text = zlib.decompress(text)
                        found[key.decode("latin1")] = (text.decode("utf-8", "replace"), data_off, length)
                except Exception:
                    pass
                handle.seek(4, 1)
            else:
                handle.seek(length + 4, 1)
            if chunk_type == b"IEND":
                break
    return found


def read_chunk_text(path: str, offset: int, length: int) -> str:
    """按记录的偏移读回原始文本（发送到 ComfyUI 时用）。"""
    with open(path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(length)
    key, _, value = raw.partition(b"\x00")
    return value.decode("utf-8", "replace")


def is_model_value(value: str, key: str) -> bool:
    lowered = value.lower()
    if lowered.endswith(MODEL_EXTS):
        return True
    return key in MODEL_KEYS


def keep_text(value: str) -> bool:
    text = value.strip()
    if len(text) < 2 or len(text) > 20000:
        return False
    if text[0] in "{[" or "class_type" in text:
        return False
    if NUM_RE.match(text) or text.lower() in ENUM_NOISE:
        return False
    if HEX64_RE.match(text) or B64_RE.match(text):
        return False
    if (":" in text and ("\\" in text or "/" in text)) or ".." in text:
        return False
    return True


def _collect(inputs: dict, class_type: str, texts: list, models: set, keys: set):
    for key, value in inputs.items():
        keys.add(class_type + "." + key)
        if isinstance(value, str):
            if is_model_value(value, key):
                models.add(os.path.basename(value))
            elif keep_text(value):
                texts.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    if is_model_value(item, key):
                        models.add(os.path.basename(item))
                    elif keep_text(item):
                        texts.append(item.strip())


def extract(prompt_raw: str, workflow_raw: str = "") -> dict:
    """从 prompt 块（API 格式）抽取文本、模型、指纹。"""
    result = {
        "search_text": "",
        "models": [],
        "node_types": [],
        "node_count": 0,
        "fingerprint": "",
        "source": "comfyui",
        "error": "",
    }
    try:
        graph = json.loads(prompt_raw)
    except Exception as exc:
        result["error"] = "prompt not json: " + str(exc)[:120]
        return result

    nodes = {str(k): v for k, v in graph.items() if isinstance(v, dict) and "class_type" in v}
    if not nodes:
        result["error"] = "no nodes"
        return result

    texts = []
    models = set()
    keys = set()
    type_counter = {}
    for node in nodes.values():
        class_type = str(node.get("class_type", "?"))
        type_counter[class_type] = type_counter.get(class_type, 0) + 1
        if DISPLAY_NODE_RE.match(class_type):
            continue
        _collect(node.get("inputs") or {}, class_type, texts, models, keys)

    seen = set()
    unique = []
    for text in sorted(texts, key=len, reverse=True):
        if text not in seen:
            seen.add(text)
            unique.append(text)
    merged = "\n".join(unique)
    if len(merged) > MAX_TEXT:
        merged = merged[:MAX_TEXT]

    result["search_text"] = merged
    result["models"] = sorted(models)
    result["node_types"] = sorted(type_counter.keys())
    result["node_count"] = len(nodes)
    result["fingerprint"] = hashlib.sha1("|".join(sorted(keys)).encode("utf-8")).hexdigest()[:16]
    result["type_counter"] = type_counter
    result["keys"] = sorted(keys)
    result["texts"] = unique
    return result
