"""本地知识库：对 data/reports 的解析报告做检索；评论历史用于防重复。

设计：报告即数据库（.md 文件），无外部服务。加载时按 `## ` 分节切块，
jieba 分词后做 TF-IDF 式加权检索——报告量在千份以内时内存暴力打分足够快。
"""

import json
import math
import re
from collections import Counter
from pathlib import Path

import jieba

from ..config import DATA_DIR

HIST_FILE = DATA_DIR / "comment_history.json"


def _tokens(text: str) -> list[str]:
    # 只要实词：过滤单字符和纯数字/字母噪声
    return [
        t
        for t in jieba.lcut(text)
        if len(t) > 1 and not re.fullmatch(r"[\dA-Za-z,.;:!?'\"()-]+", t)
    ]


class Knowledge:
    """对报告目录建立内存索引；文件变更时自动重建。"""

    def __init__(self, report_dir: Path):
        self.report_dir = Path(report_dir)
        self._chunks: list[dict] = []  # {file, video, section, text, tf}
        self._df: Counter = Counter()
        self._stamp: tuple | None = None

    # ---------- 索引 ----------

    def _current_stamp(self) -> tuple:
        files = sorted(self.report_dir.glob("*.md"))
        return tuple((f.name, f.stat().st_mtime) for f in files if f.exists())

    def _refresh(self) -> None:
        stamp = self._current_stamp()
        if stamp == self._stamp:
            return
        chunks, df_counter = [], Counter()
        for f in sorted(self.report_dir.glob("*.md")):
            try:
                raw = f.read_text(encoding="utf-8")
            except Exception:
                continue
            video = ""
            m = re.match(r"> 《(.+?)》", raw)
            if m:
                video = m.group(1)[:50]
            for sec_title, body in self._split_sections(raw):
                toks = _tokens(f"{sec_title} {body}")
                if not toks:
                    continue
                chunks.append(
                    {
                        "file": f.name,
                        "video": video,
                        "section": sec_title,
                        "text": body,
                        "tf": Counter(toks),
                    }
                )
                df_counter.update(set(toks))
        self._chunks = chunks
        self._df = df_counter
        self._stamp = stamp

    @staticmethod
    def _split_sections(raw: str) -> list[tuple[str, str]]:
        parts = re.split(r"\n(?=## )", raw)
        out = []
        for p in parts:
            m = re.match(r"## (.+)", p)
            title = m.group(1).strip() if m else "开头"
            out.append((title, p.strip()[:1200]))
        return out

    # ---------- 检索 ----------

    def _score(self, chunk: dict, q_tokens: set[str]) -> float:
        n = len(self._chunks)
        score = 0.0
        for t in q_tokens:
            tf = chunk["tf"].get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + n / max(self._df.get(t, 0), 1))
            in_title = t in chunk["section"] or t in chunk["video"]
            score += (1 + math.log(tf)) * idf * (2.0 if in_title else 1.0)
        return score

    def maybe_refresh(self):
        """热更新：目录文件数变化时自动重建索引（新报告/综述无需重启即可被检索）。"""
        n = len(list(self.report_dir.glob("*.md")))
        if n != getattr(self, "_n_files", -1):
            self.__init__(self.report_dir)
            self._n_files = n

    def search(self, query: str, k: int = 3, exclude_file: str | None = None) -> list[dict]:
        self._refresh()
        q_tokens = set(_tokens(query))
        if not q_tokens or not self._chunks:
            return []
        scored = []
        for c in self._chunks:
            if exclude_file and c["file"] == exclude_file:
                continue
            score = self._score(c, q_tokens)
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        return [
            {
                "file": c["file"],
                "video": c["video"],
                "section": c["section"],
                "text": c["text"][:600],
                "score": round(s, 2),
            }
            for s, c in scored[:k]
        ]

    def matched_files(self, query: str) -> list[tuple[float, str]]:
        """与主题相关的原始报告文件（按累计相关度降序）；综述文件(distill_*)不算源文件。"""
        self._refresh()
        q_tokens = set(_tokens(query))
        if not q_tokens or not self._chunks:
            return []
        file_scores: dict[str, float] = {}
        for c in self._chunks:
            if c["file"].startswith("distill_"):
                continue
            s = self._score(c, q_tokens)
            if s > 0:
                file_scores[c["file"]] = file_scores.get(c["file"], 0.0) + s
        return sorted(((v, k) for k, v in file_scores.items()), reverse=True)

    def size(self) -> int:
        self._refresh()
        return len(self._chunks)


def archive_reports(report_dir: Path, files: list[str]) -> list[str]:
    """把原始报告移入 archive/ 子目录：知识库索引(glob不递归)自动瘦身，原文保留可找回。"""
    arch = Path(report_dir) / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in files:
        src = Path(report_dir) / f
        if src.exists():
            src.rename(arch / f)
            moved.append(f)
    return moved


class CommentHistory:
    """已发评论历史：检索相似旧评论，防止观点/句式重复。"""

    def __init__(self, path: Path = HIST_FILE):
        self.path = path
        self._items: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._items = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._items = []

    def add(
        self,
        video_title: str,
        bvid: str,
        text: str,
        aid: int | None = None,
        rpid: int | None = None,
    ) -> None:
        self._items.append(
            {
                "video": video_title[:60],
                "bvid": bvid,
                "aid": aid,
                "rpid": rpid,
                "text": text,
                "tokens": _tokens(text)[:60],
                "ts": __import__("time").time(),
            }
        )
        self.save()

    def save(self) -> None:
        self._items = self._items[-200:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")

    def similar(self, query: str, k: int = 3) -> list[str]:
        q = set(_tokens(query))
        if not q or not self._items:
            return []
        scored = []
        for it in self._items:
            its = set(it.get("tokens") or _tokens(it["text"]))
            overlap = len(q & its)
            if overlap:
                scored.append((overlap, it))
        scored.sort(key=lambda x: -x[0])
        return [f"《{it['video']}》下: {it['text']}" for _, it in scored[:k]]
