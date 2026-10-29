"""知识卡片：LLM 设计 SVG → cairosvg 渲染 PNG。人设讲解型的图文弹药。"""
import re
from pathlib import Path

CARD_PROMPT = """把下面的笔记浓缩成一张中文知识卡片，输出**一个完整的<svg>元素**（1200x900，viewBox="0 0 1200 900"）。
设计要求：
- 深色科技底(#1a1a2e)，主题紫#9c6fce点缀，正文白#eee，重点金#ffc107
- 顶部大标题（40px内），下面一个"一图看懂"式核心示意（用SVG图形/箭头/框表达机制，不是文字堆砌）
- 3-5条要点（24px，图标+短句），底部页脚：智谱娘 · sprite 知识卡片（18px，灰）
- 只用<text><rect><circle><path><line><polygon>，字体一律 font-family="Noto Sans CJK SC"（渲染环境实际字体，别用别的）
- 文字严防溢出：每行不超过18个汉字；不用<foreignObject>、不用<image>
只输出SVG，不要解释。

笔记：{notes}"""


def make_card(deep_llm, title: str, notes: str, out_dir: Path) -> Path | None:
    """生成知识卡片PNG；SVG解析失败返回None（调用方回退纯文字动态）。"""
    import cairosvg

    raw = deep_llm.chat(
        [{"role": "user", "content": CARD_PROMPT.format(notes=notes[:2500])}],
        max_tokens=3500,
    )
    m = re.search(r"<svg.*</svg>", raw, re.S)
    if not m:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"card_{re.sub(r'[\\s/:|]', '_', title)[:30]}.png"
    try:
        cairosvg.svg2png(bytestring=m.group(0).encode("utf-8"),
                         write_to=str(out), output_width=1200)
        return out
    except Exception:
        return None
