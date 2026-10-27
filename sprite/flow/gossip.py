"""花边播报素材：HN头榜 + Reddit热帖 + HF博客 → 按领域关键词过滤，TTL缓存防刷。"""
import json
import re
import time
from pathlib import Path

import requests

UA = {"User-Agent": "sprite-bot/0.1 (github.com/wind12335/sprite)"}
CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "gossip_cache.json"

BROADCAST_PROMPT = (
    "以下是技术圈今天的几条真实热门资讯。请用你的人设口吻向群友播报：挑最有趣的2-3条，"
    "逐条口语化转述（不改变事实、不编造列表外的信息），每条带一句短评，结尾一句总结今天的圈内氛围。\n\n"
)


def _domain_keywords():
    """领域关键词默认跟随 [watch].keywords——知识库/关注是什么领域，花边就播什么领域。"""
    try:
        from ..config import load_config
        return [k for k in load_config()["watch"]["keywords"] if isinstance(k, str)]
    except Exception:
        return ["AI", "LLM", "大模型"]


def _match(title, kws):
    t = title.lower()
    return any(k.lower() in t for k in kws)


def _hn(kws, limit):
    try:
        r = requests.get("https://hn.algolia.com/api/v1/front_page", headers=UA, timeout=15)
        hits = r.json().get("hits") or []
    except Exception:
        return []
    out = []
    for h in hits:
        t = h.get("title") or ""
        if _match(t, kws):
            out.append(f"- [HN {h.get('points', 0)}赞/{h.get('num_comments', 0)}评] {t}"
                       f" — https://news.ycombinator.com/item?id={h.get('objectID')}")
    return out[:limit]


def _reddit(kws, sub, limit):
    try:
        r = requests.get(f"https://www.reddit.com/r/{sub}/top.json",
                         params={"t": "day", "limit": 25}, headers=UA, timeout=15)
        ch = r.json().get("data", {}).get("children") or []
    except Exception:
        return []
    out = []
    for c in ch:
        d = c.get("data") or {}
        t = d.get("title") or ""
        if _match(t, kws):
            out.append(f"- [Reddit {d.get('score', 0)}赞] {t} — https://reddit.com{d.get('permalink', '')}")
    return out[:limit]


def _hf_blog(kws, limit):
    try:
        r = requests.get("https://huggingface.co/blog/feed.xml", headers=UA, timeout=15)
    except Exception:
        return []
    out = []
    for blk in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blk, re.S)
        l = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", blk, re.S)
        if t and l and _match(t.group(1), kws):
            out.append(f"- [HF博客] {' '.join(t.group(1).split())} — {l.group(1).strip()}")
        if len(out) >= limit:
            break
    return out


def build_gossip(kws=None, sub="MachineLearning", max_items=6, ttl_min=30):
    """返回播报素材文本（无匹配返回None）；空结果同样缓存，防止频繁打API。"""
    if kws is None:
        kws = _domain_keywords()
    if CACHE.exists():
        try:
            c = json.loads(CACHE.read_text())
            if time.time() - c.get("ts", 0) < ttl_min * 60:
                return c.get("text") or None
        except Exception:
            pass
    items = (_hn(kws, max_items) + _reddit(kws, sub, max_items) + _hf_blog(kws, 3))[:max_items]
    text = "\n".join(items) or None
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"ts": time.time(), "text": text}, ensure_ascii=False))
    except Exception:
        pass
    return text
