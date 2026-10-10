"""相邻图 diff 与同源判定（实施方案 §7，阈值与规则均为本机实测修订后的版本）。

- 四路信号：s1 节点类型多重集、s2 边多重集、s3 输入键集合、s4 文本 3-gram 集合
- 硬断链：解析失败、节点数量级差、节点类型交集过低、超大会话窗口
- 族链：与前一张比 + 与族代表比，双阈值，代表滚动
- diff 结果落库；详情页按需重算时复用同一套函数
"""
import difflib
import hashlib
import json
import os
import re
from collections import Counter

from . import meta

ALGO_VER = 2
WEIGHT_TYPES = 0.40
WEIGHT_EDGES = 0.20
WEIGHT_KEYS = 0.15
WEIGHT_TEXT = 0.25
TH_SAME = 0.90
TH_WEAK = 0.75
SESSION_GAP_HOURS = 6.0
MAX_WORKFLOW_BYTES = 2 * 1024 * 1024
RANDOM_KEYS = {"seed", "noise_seed", "rand_seed"}
BIG_INT = 10 ** 9


def _multiset_jaccard(left: Counter, right: Counter) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 1.0
    inter = sum(min(left[k], right[k]) for k in keys)
    union = sum(max(left[k], right[k]) for k in keys)
    return inter / union if union else 1.0


def _set_jaccard(left: set, right: set) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def _grams(text: str) -> set:
    flat = " ".join(text.split())
    if not flat:
        return set()
    if len(flat) < 3:
        return set(flat)
    return {flat[i:i + 3] for i in range(len(flat) - 2)}


def structure(prompt_raw: str) -> dict:
    """把 prompt 块解析成可比较的结构（含类型计数、边、输入键、文本）。"""
    graph = json.loads(prompt_raw)
    nodes = {str(k): v for k, v in graph.items() if isinstance(v, dict) and "class_type" in v}
    id2cls = {nid: str(node.get("class_type", "?")) for nid, node in nodes.items()}
    types = Counter()
    edges = Counter()
    keys = set()
    texts = []
    models = set()
    for nid, node in nodes.items():
        class_type = id2cls[nid]
        types[class_type] += 1
        if meta.DISPLAY_NODE_RE.match(class_type):
            continue
        for key, value in (node.get("inputs") or {}).items():
            keys.add(class_type + "." + key)
            if isinstance(value, str):
                if meta.is_model_value(value, key):
                    models.add(os.path.basename(value))
                elif meta.keep_text(value):
                    texts.append(value.strip())
            elif isinstance(value, list):
                if len(value) == 2 and str(value[0]) in id2cls and isinstance(value[1], int) and not isinstance(value[0], bool):
                    edges[(id2cls[str(value[0])], class_type)] += 1
                else:
                    for item in value:
                        if isinstance(item, str):
                            if meta.is_model_value(item, key):
                                models.add(os.path.basename(item))
                            elif meta.keep_text(item):
                                texts.append(item.strip())
    seen = set()
    unique = []
    for text in sorted(texts, key=len, reverse=True):
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return {
        "types": types,
        "edges": edges,
        "keys": keys,
        "text": "\n".join(unique),
        "models": models,
        "node_count": len(nodes),
        "nodes": nodes,
        "id2cls": id2cls,
        "fingerprint": hashlib.sha1("|".join(sorted(keys)).encode("utf-8")).hexdigest()[:16],
    }


def signals(left: dict, right: dict) -> tuple:
    s1 = _multiset_jaccard(left["types"], right["types"])
    s2 = _multiset_jaccard(left["edges"], right["edges"])
    s3 = _set_jaccard(left["keys"], right["keys"])
    s4 = _set_jaccard(_grams(left["text"]), _grams(right["text"]))
    score = s1 * WEIGHT_TYPES + s2 * WEIGHT_EDGES + s3 * WEIGHT_KEYS + s4 * WEIGHT_TEXT
    return s1, s2, s3, s4, round(score, 4)


def hard_break(prev: dict, cur: dict, gap_hours: float, session_hours: float = SESSION_GAP_HOURS):
    """返回断链原因；无则为 None。时间窗口优先于一切相似度计算。"""
    if session_hours and gap_hours > session_hours:
        return "gap"
    left_count = prev.get("node_count") or 0
    right_count = cur.get("node_count") or 0
    if left_count == 0 or right_count == 0:
        return "empty"
    ratio = right_count / float(left_count)
    if ratio > 3 or ratio < 1 / 3.0:
        return "size"
    s1 = _multiset_jaccard(prev["types"], cur["types"])
    if s1 < 0.30:
        return "types"
    return None


def _node_similarity(left: dict, right: dict) -> float:
    left_inputs = left.get("inputs") or {}
    right_inputs = right.get("inputs") or {}
    left_keys, right_keys = set(left_inputs), set(right_inputs)
    key_score = _set_jaccard(left_keys, right_keys)
    same = 0
    total = len(left_keys | right_keys) or 1
    for key in left_keys & right_keys:
        if left_inputs[key] == right_inputs[key]:
            same += 1
    value_score = same / total
    return 0.4 * key_score + 0.4 * value_score + 0.2 * 1.0


def diff_nodes(left: dict, right: dict) -> dict:
    """节点级 diff：新增、删除、参数变化、连接变化。"""
    added = Counter((right["types"] - left["types"]).elements())
    removed = Counter((left["types"] - right["types"]).elements())
    changed = []
    edges_added = Counter((right["edges"] - left["edges"]).elements())
    edges_removed = Counter((left["edges"] - right["edges"]).elements())

    buckets = {}
    for nid, node in left["nodes"].items():
        buckets.setdefault(str(node.get("class_type")), []).append(("L", nid, node))
    for nid, node in right["nodes"].items():
        buckets.setdefault(str(node.get("class_type")), []).append(("R", nid, node))

    for class_type, items in buckets.items():
        lefts = [item for item in items if item[0] == "L"]
        rights = [item for item in items if item[0] == "R"]
        if not lefts or not rights:
            continue
        # 桶内贪心匹配：先按相似度降序配对，避免多实例节点错位
        pairs = []
        for i, (_, lid, lnode) in enumerate(lefts):
            for j, (_, rid, rnode) in enumerate(rights):
                pairs.append((_node_similarity(lnode, rnode), i, j))
        pairs.sort(reverse=True, key=lambda item: item[0])
        used_left, used_right = set(), set()
        matched = []
        for score, i, j in pairs:
            if i in used_left or j in used_right:
                continue
            used_left.add(i)
            used_right.add(j)
            matched.append((lefts[i][2], rights[j][2]))
        for lnode, rnode in matched:
            lin = lnode.get("inputs") or {}
            rin = rnode.get("inputs") or {}
            for key in sorted(set(lin) | set(rin)):
                lv, rv = lin.get(key), rin.get(key)
                if lv == rv:
                    continue
                if key in RANDOM_KEYS:
                    continue
                if isinstance(lv, list) or isinstance(rv, list):
                    continue
                if isinstance(lv, int) and abs(lv) > BIG_INT:
                    continue
                if isinstance(rv, int) and abs(rv) > BIG_INT:
                    continue
                changed.append(_describe_change(class_type, key, lv, rv))
    big_random = _count_random_shifts(left, right)
    return {
        "added": _flatten(added),
        "removed": _flatten(removed),
        "changed": changed[:80],
        "changed_count": len(changed),
        "edges_added": _edges(edges_added),
        "edges_removed": _edges(edges_removed),
        "random_fields": big_random,
    }


def _edges(counter: Counter) -> list:
    return [[a, b, n] for (a, b), n in counter.most_common(20)]


def _flatten(counter: Counter) -> list:
    out = []
    for name, count in counter.most_common(40):
        out.append([name, count])
    return out


def _count_random_shifts(left: dict, right: dict) -> int:
    count = 0
    left_nodes = list(left["nodes"].values())
    right_nodes = list(right["nodes"].values())
    for index, lnode in enumerate(left_nodes):
        if index >= len(right_nodes):
            break
        rnode = right_nodes[index]
        if lnode.get("class_type") != rnode.get("class_type"):
            continue
        for key, value in (lnode.get("inputs") or {}).items():
            other = (rnode.get("inputs") or {}).get(key)
            if key in RANDOM_KEYS or (isinstance(value, int) and abs(value) > BIG_INT):
                if value != other:
                    count += 1
    return count


def _describe_change(class_type: str, key: str, left_value, right_value) -> dict:
    item = {"node": class_type, "key": key, "kind": "value"}
    if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
        item["kind"] = "number"
        item["from"] = left_value
        item["to"] = right_value
        item["delta"] = round(right_value - left_value, 4)
        if left_value:
            item["percent"] = round((right_value - left_value) / abs(left_value) * 100, 1)
        return item
    left_text = "" if left_value is None else str(left_value)
    right_text = "" if right_value is None else str(right_value)
    if len(left_text) > 200 or len(right_text) > 200:
        item["kind"] = "text"
        item["from_len"] = len(left_text)
        item["to_len"] = len(right_text)
        item["fragments"] = _text_fragments(left_text, right_text)
        return item
    item["from"] = left_text
    item["to"] = right_text
    return item


def _text_fragments(left_text: str, right_text: str, limit: int = 3) -> list:
    matcher = difflib.SequenceMatcher(None, left_text, right_text)
    ops = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    ops.sort(key=lambda op: (op[2] - op[1]) + (op[4] - op[3]), reverse=True)
    fragments = []
    for tag, i1, i2, j1, j2 in ops[:limit]:
        fragments.append({
            "op": tag,
            "removed": left_text[i1:i2][:120],
            "added": right_text[j1:j2][:120],
        })
    return fragments


def summarize(verdict: str, score: float, detail: dict, prev_name: str = "", lang: str = "zh") -> str:
    en = lang == "en"
    if verdict == "new":
        if en:
            return "Workflow start (different origin from the previous image, similarity %.2f)" % score
        return "工作流起点（与上一张不同源，相似度 %.2f）" % score
    parts = []
    if detail["added"]:
        names = ", ".join(item[0] for item in detail["added"][:4]) if en else "、".join(item[0] for item in detail["added"][:4])
        total = sum(item[1] for item in detail["added"])
        parts.append("added %d nodes (%s)" % (total, names) if en else "新增 %d 个节点（%s）" % (total, names))
    if detail["removed"]:
        names = ", ".join(item[0] for item in detail["removed"][:4]) if en else "、".join(item[0] for item in detail["removed"][:4])
        total = sum(item[1] for item in detail["removed"])
        parts.append("removed %d nodes (%s)" % (total, names) if en else "移除 %d 个节点（%s）" % (total, names))
    numbers = [item for item in detail["changed"] if item["kind"] == "number"]
    texts = [item for item in detail["changed"] if item["kind"] == "text"]
    values = [item for item in detail["changed"] if item["kind"] == "value"]
    if numbers:
        joiner = "; " if en else "；"
        shown = joiner.join("%s %s->%s" % (item["key"], _fmt(item["from"]), _fmt(item["to"])) for item in numbers[:3])
        parts.append(shown)
    if texts:
        fragments = texts[0].get("fragments") or []
        added = fragments[0]["added"] if fragments else ""
        delta = texts[0]["to_len"] - texts[0]["from_len"]
        if en:
            parts.append("text changed in %d places (longest %+d chars%s)" % (
                len(texts), delta, (", new fragment: " + added[:40]) if added else ""))
        else:
            parts.append("文本变化 %d 处（最长一段 %+d 字符%s）" % (
                len(texts), delta, ("，新增片段：" + added[:40]) if added else ""))
    if values:
        joiner = "; " if en else "；"
        shown = joiner.join("%s %s->%s" % (item["key"], str(item["from"])[:20], str(item["to"])[:20]) for item in values[:3])
        parts.append(shown)
    if detail.get("random_fields"):
        parts.append("%d random field changes" % detail["random_fields"] if en
                     else "另有 %d 处随机项变化" % detail["random_fields"])
    if not parts:
        return "Structurally identical to the previous image" if en else "与上一张结构一致，无可报告的参数变化"
    prefix = "" if verdict == "same" else ("large structural difference: " if en else "结构差异较大：")
    return prefix + ("; ".join(parts) if en else "；".join(parts))

def _fmt(value) -> str:
    if isinstance(value, float):
        return ("%.4g" % value)
    return str(value)


def compare(prev: dict, cur: dict, gap_hours: float, family_id, prev_file_id: int,
            session_hours: float = SESSION_GAP_HOURS, th_same: float = TH_SAME,
            th_weak: float = TH_WEAK) -> dict:
    """给一对（前一张、当前张）算出完整判定与 diff 记录。"""
    reason = hard_break(prev, cur, gap_hours, session_hours)
    s1, s2, s3, s4, score = signals(prev, cur)
    if reason:
        verdict = "new"
    elif score >= th_same:
        verdict = "same"
    elif score >= th_weak:
        verdict = "weak"
    else:
        verdict = "new"
    detail = diff_nodes(prev, cur) if verdict != "new" else {}
    summary = summarize(verdict, score, detail)
    return {
        "s1": round(s1, 4), "s2": round(s2, 4), "s3": round(s3, 4), "s4": round(s4, 4),
        "score": score, "verdict": verdict, "reason": reason,
        "summary": summary, "detail": detail,
        "fingerprint": cur.get("fingerprint", ""),
        "node_count": cur.get("node_count", 0),
        "algo_ver": ALGO_VER,
    }
