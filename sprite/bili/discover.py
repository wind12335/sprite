"""发现层：按关键词/UP 主找新视频 + 提取 CC 字幕全文（带时间戳）。"""

import asyncio
import datetime
import json
import re
import time

import requests
from bilibili_api import search, video

_EM_TAG = re.compile(r"</?em[^>]*>")


def _clean_title(t: str) -> str:
    return _EM_TAG.sub("", t)


async def search_new_videos(
    keywords: list[str],
    lookback_days: int = 3,
    min_duration: int = 300,
    per_keyword: int = 20,
) -> list[dict]:
    """多关键词搜索最新发布的视频，合并去重，过滤时长和发布时间。"""
    since = time.time() - lookback_days * 86400
    merged: dict[str, dict] = {}
    for kw in keywords:
        try:
            res = await search.search_by_type(
                keyword=kw,
                search_type=search.SearchObjectType.VIDEO,
                order_type=search.OrderVideo.PUBDATE,
                page=1,
            )
        except Exception as e:
            print(f"  搜索「{kw}」失败：{e}")
            continue
        for it in res.get("result") or []:
            bvid = it.get("bvid")
            if not bvid or bvid in merged:
                continue
            if _len_to_sec(it.get("duration") or 0) < min_duration:
                continue
            if int(it.get("pubdate") or 0) < since:
                continue
            merged[bvid] = {
                "bvid": bvid,
                "title": _clean_title(it.get("title", "")),
                "description": it.get("description", ""),
                "pubdate": int(it.get("pubdate") or 0),
                "duration": _len_to_sec(it.get("duration") or 0),
                "author": it.get("author", ""),
                "mid": it.get("mid"),
            }
        await asyncio.sleep(2)  # 搜索接口礼貌间隔
    return sorted(merged.values(), key=lambda x: x["pubdate"], reverse=True)


def _fmt_ts(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def subtitle_body_to_text(body: list[dict]) -> str:
    """CC 字幕 JSON body（[{from,to,content}]）→ 带时间戳的全文。"""
    return "\n".join(f"[{_fmt_ts(l.get('from', 0))}] {l.get('content', '')}" for l in body)


def _pick_subtitle_url(data: dict) -> str | None:
    subs = data.get("subtitles") or []
    if not subs:
        return None
    zh = [s for s in subs if str(s.get("lan", "")).lower().startswith("zh")]
    url = (zh or subs)[0].get("subtitle_url") or ""
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    return url


async def fetch_subtitle_text(bvid: str, credential) -> tuple[dict, str | None]:
    """返回 (视频信息, 字幕全文或 None)。字幕是 CC 字幕（含 AI 字幕），需登录态。"""
    v = video.Video(bvid=bvid, credential=credential)
    info = await v.get_info()
    cid = await v.get_cid(page_index=0)  # 注意：分P号从0开始（库里1会报"不存在该分p"）
    sub_data = await v.get_subtitle(cid)
    url = _pick_subtitle_url(sub_data)
    if not url:
        return info, None
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    body = json.loads(r.text).get("body") or []
    if not body:
        return info, None
    text = subtitle_body_to_text(body)
    # 超长保护：flash 有 1M 上下文，一般到不了这里，防极端长视频
    if len(text) > 400_000:
        text = text[:400_000] + "\n…（字幕过长已截断）"
    return info, text


def pubdate_str(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _len_to_sec(length) -> int:
    """B站接口的时长字段两种形态都可能出现：整数秒 或 "mm:ss"/"h:mm:ss" 字符串。"""
    if isinstance(length, (int, float)):
        return int(length)
    parts = [int(p) for p in str(length).split(":") if p != ""]
    if not parts:
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


async def latest_from_up(
    up_mid: int,
    credential,
    lookback_days: int = 3,
    min_duration: int = 300,
) -> list[dict]:
    """盯指定 UP 主最近发布的视频（走空间投稿列表）。"""
    from bilibili_api import user

    since = time.time() - lookback_days * 86400
    u = user.User(uid=up_mid, credential=credential)
    res = await u.get_videos(pn=1, ps=20)
    out = []
    for it in res.get("list", {}).get("vlist") or []:
        if int(it.get("created") or 0) < since:
            continue
        if _len_to_sec(it.get("length", "")) < min_duration:
            continue
        out.append(
            {
                "bvid": it["bvid"],
                "title": it.get("title", ""),
                "description": it.get("description", ""),
                "pubdate": int(it.get("created") or 0),
                "duration": _len_to_sec(it.get("length", "")),
                "author": it.get("author", ""),
                "mid": up_mid,
            }
        )
    return out
