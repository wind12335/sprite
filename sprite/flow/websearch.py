"""联网搜索：搜狗+必应双引擎无Key抓取 → 标题/链接/摘要。
不依赖任何厂商API——GLM/DeepSeek/Qwen 用户的体验完全一致。"""
import json
import re
import time
from html import unescape
from pathlib import Path

import requests

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept-Language": "zh-CN,zh;q=0.9"}
CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "search_cache.json"
TTL = 1800

# 判定"这像不像一个需要联网的事实性问题"（避免闲聊也触发搜索）
_QWORDS = re.compile(
    r"什么|为什么|怎么|如何|是不是|有哪些|是谁|哪[个里位家]|最新|新闻|今天|最近|发布|版本|更新|"
    r"20[0-9]{2}|是什么|多少钱|区别|对比| vs |VS|干嘛|干啥|咋|能不能|有没有|多不多|好不好|"
    r"讲讲|说说|介绍(一下)?|查(一下|查)?|搜(一下)?")


def looks_like_question(text: str) -> bool:
    return ("?" in text or "？" in text or bool(_QWORDS.search(text))) and len(text) >= 6


def _strip(h: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", h)).strip()


def _sogou(query: str, max_items: int, timeout: int) -> list[dict]:
    try:
        r = requests.get("https://www.sogou.com/web", params={"query": query},
                         headers=UA, timeout=timeout)
    except Exception:
        return []
    out = []
    for blk in r.text.split('<div class="vrwrap">')[1:]:
        m = re.search(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        url = m.group(1)
        if url.startswith("/link"):
            url = "https://www.sogou.com" + url
        title = _strip(m.group(2))
        sm = re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
        if title:
            out.append({"title": title, "url": url,
                        "snippet": _strip(sm.group(1))[:200] if sm else ""})
        if len(out) >= max_items:
            break
    return out


def _bing(query: str, max_items: int, timeout: int) -> list[dict]:
    try:
        r = requests.get("https://cn.bing.com/search", params={"q": query, "count": max_items + 3},
                         headers=UA, timeout=timeout)
    except Exception:
        return []
    out = []
    for blk in r.text.split('<li class="b_algo')[1:]:
        m = re.search(r'<h2><a[^>]+href="(http[^"]+)"[^>]*>(.*?)</a></h2>', blk, re.S)
        if not m:
            continue
        url, title = m.group(1), _strip(m.group(2))
        if "bing.com" in url or not title:
            continue
        sm = re.search(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', blk, re.S) \
            or re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
        out.append({"title": title, "url": url,
                    "snippet": _strip(sm.group(1))[:200] if sm else ""})
        if len(out) >= max_items:
            break
    return out


_CHATTER = re.compile(r"我(想|要)?(搜索|搜|查)(了|一下)?|帮我|麻烦|好好|查查|看看|一下|你(知道|去|给)?|请问|到底|究竟|呗|呢|啊|吗|？|\?|。|，")

def clean_query(text: str) -> str:
    """从口语消息里提炼搜索词：剥掉对话性缀词，残骸太短就用原文。"""
    q = _CHATTER.sub("", text).strip()
    return q if len(q) >= 2 else text.strip()


def search_web(query: str, max_items: int = 5, timeout: int = 15) -> list[dict]:
    """双引擎：搜狗优先（公司/实体/长尾中文词强）+ 必应补充（技术内容强）；
    按 域名+标题前8字 去重；同query缓存30分钟。"""
    query = query.strip()[:60]
    cache = {}
    try:
        cache = json.loads(CACHE.read_text())
        c = cache.get(query)
        if c and time.time() - c["ts"] < TTL:
            return c["items"]
    except Exception:
        pass
    merged, seen = [], set()
    for item in _sogou(query, max_items, timeout) + _bing(query, max_items, timeout):
        key = re.sub(r"https?://(www\.)?", "", item["url"]).split("/")[0] + item["title"][:8]
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= max_items:
            break
    try:
        cache[query] = {"ts": time.time(), "items": merged}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False)[-200_000:])
    except Exception:
        pass
    return merged


def format_results(items: list[dict]) -> str:
    return "\n".join(f"- {it['title']}\n  {it['url']}\n  {it['snippet']}" for it in items)
