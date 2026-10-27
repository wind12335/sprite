"""情报摄取：arXiv 关键词检索 + HF 日报论文 → PDF 入库；HF 博客 → 资讯报告。"""
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

UA = {"User-Agent": "sprite-bot/0.1 (github.com/wind12335/sprite)"}


def fetch_arxiv(keywords, days=3, limit=20):
    q = " OR ".join(f'all:"{k}"' for k in keywords[:6])
    r = requests.get("https://export.arxiv.org/api/query",
                     params={"search_query": q, "sortBy": "submittedDate",
                             "sortOrder": "descending", "max_results": limit},
                     headers=UA, timeout=30)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    cutoff = time.time() - days * 86400
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", ns):
        pub = e.findtext("a:published", "", ns)[:10]
        try:
            ts = time.mktime(time.strptime(pub, "%Y-%m-%d"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", ns).split("/abs/")[-1])
        out.append({"id": aid, "title": " ".join(e.findtext("a:title", "", ns).split()),
                    "abstract": " ".join(e.findtext("a:summary", "", ns).split())[:500],
                    "url": f"https://arxiv.org/abs/{aid}", "pdf": f"https://arxiv.org/pdf/{aid}"})
    return out


def fetch_hf_daily(limit=10):
    try:
        r = requests.get("https://huggingface.co/api/daily_papers", headers=UA, timeout=20)
        data = r.json()
    except Exception:
        return []
    out = []
    for p in (data if isinstance(data, list) else [])[:40]:
        pa = p.get("paper") or {}
        aid = (pa.get("arxiv_id") or pa.get("id") or "").strip()
        if not aid:
            continue
        out.append({"id": re.sub(r"v\d+$", "", aid), "title": pa.get("title") or "",
                    "abstract": (pa.get("summary") or "")[:500],
                    "url": f"https://arxiv.org/abs/{aid}", "pdf": f"https://arxiv.org/pdf/{aid}",
                    "upvotes": pa.get("upvotes") or p.get("upvotes") or 0})
        if len(out) >= limit:
            break
    return out


def download_pdfs(items, papers_dir: Path, seen: set, max_n=5):
    papers_dir.mkdir(parents=True, exist_ok=True)
    got = []
    for it in items:
        if len(got) >= max_n:
            break
        if it["id"] in seen:
            continue
        try:
            r = requests.get(it["pdf"], headers=UA, timeout=90)
            r.raise_for_status()
            if r.content[:4] != b"%PDF":
                continue
            f = papers_dir / f'{it["id"].replace("/", "_")}.pdf'
            f.write_bytes(r.content)
            got.append((it, f))
            seen.add(it["id"])
            print(f"  ⬇ {it['id']}  {it['title'][:50]}")
            time.sleep(1)  # arXiv 礼貌限速
        except Exception as e:
            print(f"  ✗ {it['id']}: {e}")
    return got


def fetch_hf_blog(limit=5):
    try:
        r = requests.get("https://huggingface.co/blog/feed.xml", headers=UA, timeout=15)
    except Exception:
        return []
    out = []
    for blk in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blk, re.S)
        l = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", blk, re.S)
        if t and l:
            out.append({"title": " ".join(t.group(1).split()), "link": l.group(1).strip()})
        if len(out) >= limit:
            break
    return out
