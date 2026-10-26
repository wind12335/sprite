"""Obsidian 同步：报告/综述/论文解析自动写入 vault（带 YAML frontmatter 便于搜索和关联）。"""

import shutil
from datetime import datetime
from pathlib import Path

from .config import ROOT


def sync_to_obsidian(file_path: Path, tags: list[str] | None = None, source: str = "", doc_type: str = "video-analysis") -> Path | None:
    """把报告文件同步到 Obsidian vault（加 YAML frontmatter）。

    Args:
        file_path: 源文件（.md）
        tags: Obsidian 标签列表（如 ["vLLM", "inference"]）
        source: 来源链接/ID（BV号/arXiv ID）
        doc_type: video-analysis / paper-analysis / digest
    Returns: Obsidian 里的目标路径，失败返回 None
    """
    from .config import load_config

    try:
        cfg = load_config().get("obsidian", {})
    except SystemExit:
        return None
    if not cfg.get("sync", True):
        return None

    vault = Path(cfg.get("vault_path", ""))
    subfolder = cfg.get("subfolder", "AI-Infra")
    target_dir = vault / subfolder
    if not vault.exists():
        return None

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / file_path.name

    # 读原文，插入 YAML frontmatter
    content = file_path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        today = datetime.now().strftime("%Y-%m-%d")
        tag_str = ", ".join(t for t in (tags or []) if t)
        frontmatter = f"---\ntags: [{tag_str}]\nsource: \"{source}\"\ndate: {today}\ntype: {doc_type}\n---\n\n"
        content = frontmatter + content

    target.write_text(content, encoding="utf-8")
    return target
