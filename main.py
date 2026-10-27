#!/usr/bin/env python3
"""sprite 命令行入口。

用法：
  python main.py login                     # 扫码登录，保存凭据
  python main.py status                    # 查看登录身份与最近视频
  python main.py reply-loop                # 常驻：自动回复自己视频下的评论
  python main.py dynamic --llm --topic "今天摸鱼的一天"   # LLM 写动态并发布
  python main.py dynamic --text "手动内容"               # 直接发布指定文本
  python main.py video a.mp4 --title "标题" --tags "日常,记录" [--tid 21] [--cover c.jpg]
  python main.py watch [--once]            # AI infra 视频情报：搜索→字幕→两级解析→动态@我
  python main.py paper <pdf> [pdf...]      # 论文入库：PDF→LLM解析→知识库+Obsidian
  python main.py daily-digest [--date 2026-10-25]  # 日期综述：该日报告→综述→Obsidian（--prune 归档原文）
  python main.py housekeep                 # 手动触发保洁（旧会话清理/日志裁尾/知识库体量检查）
"""

import argparse
import asyncio
import inspect
import sys
from pathlib import Path

from sprite.bili import Bili, load_credential, qr_login
from sprite.config import ROOT, load_config
from sprite.flow import Watcher, reply_loop
from sprite.flow.runner import _label
from sprite.llm import LLM
from sprite.logutil import acquire_lock, install_timestamped_print, usage_summary
from sprite.mind import Brain, CommentHistory, Knowledge, Memory, archive_reports


def build_brain(cfg: dict) -> Brain:
    llm_cfg = cfg["llm"]
    return Brain(
        LLM(
            llm_cfg["base_url"],
            llm_cfg["api_key"],
            llm_cfg["model"],
            llm_cfg["temperature"],
            llm_cfg.get("thinking", False),
        ),
        cfg["persona"]["name"],
        cfg["persona"]["system_prompt"],
    )


def cmd_login(_args) -> None:
    qr_login()


async def cmd_status(_args) -> None:
    bili = Bili(load_credential())
    info = await bili.my_info()
    print(f"登录身份：{info['name']} (mid={info['mid']}, 等级 {info.get('level')})")
    for v in await bili.my_videos(5):
        print(f"  av{v['aid']} 《{v['title']}》 播放 {v.get('play', '?')}")


async def cmd_reply_loop(_args) -> None:
    cfg = load_config()
    await reply_loop(Bili(load_credential()), build_brain(cfg), Memory(), cfg["safety"])


async def cmd_dynamic(args) -> None:
    cfg = load_config()
    safety = cfg["safety"]
    mem = Memory()
    if not mem.quota_ok("dynamic", safety["dynamic_daily_limit"]):
        sys.exit("今日动态配额已用完（见 config.toml 的 dynamic_daily_limit）。")

    if args.llm:
        text = build_brain(cfg).write_dynamic(args.topic or "随便碎碎念点什么")
        print(f"LLM 生成动态：{text}")
    else:
        if not args.text:
            sys.exit("需要 --text 或 --llm 其一。")
        text = args.text
    if not args.yes:  # 无论哪条路径，发布前都要人工确认（除非显式 --yes）
        preview = text if len(text) <= 80 else text[:79] + "…"
        if input(f"将发布动态：「{preview}」 确认？(y/N) ").strip().lower() != "y":
            sys.exit("已取消。")

    result = await Bili(load_credential()).post_text_dynamic(
        _label(text, safety["aigc_label"])
    )
    mem.bump("dynamic")
    print(f"动态已发布：{result.get('dyn_id', result)}")


async def cmd_watch(args) -> None:
    cfg = load_config()
    llm_cfg = cfg["llm"]
    sc = cfg["llm_screen"]
    deep = LLM(
        llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
        llm_cfg["temperature"], llm_cfg.get("thinking", False),
    )
    screen = LLM(
        sc["base_url"], sc["api_key"], sc["model"], sc["temperature"], sc["thinking"]
    )
    watcher = Watcher(
        Bili(load_credential()),
        deep,
        screen,
        cfg["watch"],
        Memory(),
        persona_cfg=cfg["persona"],
        aigc_label=cfg["safety"].get("aigc_label", ""),
        kb=Knowledge(ROOT / cfg["watch"]["report_dir"]),
        history=CommentHistory(),
        safety_cfg=cfg["safety"],
        hk_cfg=cfg.get("housekeep"),
        qq_cfg=cfg.get("qq"),
        dry_run=args.dry_run,
    )
    await watcher.run(once=args.once)


def cmd_ask(args) -> None:
    """查询本地知识库（data/reports 里的视频解析笔记）：检索 + glm-5.3 归纳回答。"""
    cfg = load_config()
    llm_cfg = cfg["llm"]
    kb = Knowledge(ROOT / cfg["watch"]["report_dir"])
    hits = kb.search(args.query, k=5)
    if not hits:
        sys.exit("知识库为空或没有相关笔记（先跑几轮 watch 积累报告）。")
    print(f"命中 {len(hits)} 个笔记块：")
    for h in hits:
        print(f"  [{h['score']}] 《{h['video']}》{h['section']}  ({h['file']})")
    context = "\n\n---\n\n".join(
        f"【来自《{h['video']}》/{h['section']}】\n{h['text']}" for h in hits
    )
    deep = LLM(
        llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
        llm_cfg["temperature"], llm_cfg.get("thinking", False),
    )
    answer = deep.chat(
        [
            {
                "role": "system",
                "content": "你是知识库问答助手。只依据提供的笔记内容回答，注明信息来自哪个视频；笔记没覆盖的部分直说'笔记里没有'，不要编造。",
            },
            {"role": "user", "content": f"问题：{args.query}\n\n相关笔记：\n{context}"},
        ],
        max_tokens=2000,
    )
    print("\n" + answer)


async def cmd_video(args) -> None:
    bili = Bili(load_credential())
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    print(f"开始上传 {args.path}（大文件分片上传可能需要几分钟）…")
    result = await bili.upload_video(
        path=args.path,
        title=args.title,
        desc=args.desc or "",
        tags=tags,
        tid=args.tid,
        cover=args.cover,
    )
    print(f"投稿完成：{result}")


DISTILL_PROMPT = """你是一名整理知识库的工程师。下面是同一主题的多份视频解析笔记，请把它们蒸馏成一篇体系化综述。

要求：
1. markdown 输出，结构：
# 综述：{topic}
## 概览
## 核心概念体系（这个主题的知识地图，讲清概念之间的关系）
## 分主题深入（每个小主题整合所有来源，标注来源视频名；同一件事被多个视频讲过就合并讲一次）
## 不同来源的对比与互补（哪些视频讲得深、哪些互相矛盾、哪些互补）
## 空白与未覆盖（笔记里没有、但按这个主题本应覆盖的内容）
2. 浓缩但不失真：保留具体的数字、伪代码、结论和时间戳要点——综述的价值是"读完不用再翻原文"
3. 只基于笔记内容，不编造；笔记间冲突时如实并列，不擅自裁决

笔记全文：
{corpus}
"""


def cmd_distill(args) -> None:
    """蒸馏：把某主题的多份笔记总结成一篇综述；--prune 把原文归档出索引。"""
    import datetime
    import re as _re

    cfg = load_config()
    llm_cfg = cfg["llm"]
    report_dir = ROOT / cfg["watch"]["report_dir"]
    kb = Knowledge(report_dir)

    scored = kb.matched_files(args.topic)
    if not scored:
        sys.exit(f"知识库里没有和「{args.topic}」相关的笔记（先跑几轮 watch 积累）。")
    picked = [f for _, f in scored[: args.max_files]]
    print(f"「{args.topic}」匹配到 {len(scored)} 份笔记，取相关度最高的 {len(picked)} 份蒸馏：")
    for s, f in scored[: len(picked)]:
        print(f"  [{s:.1f}] {f}")
    if len(scored) > len(picked):
        print(f"  （另有 {len(scored) - len(picked)} 份相关度较低未纳入）")

    corpus = "\n\n=====\n\n".join(
        f"【文件：{f}】\n{(report_dir / f).read_text(encoding='utf-8')}" for f in picked
    )
    deep = LLM(
        llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
        llm_cfg["temperature"], llm_cfg.get("thinking", False),
        timeout=300,  # 大语料+长输出，给足生成时间
    )
    print(f"语料 {len(corpus)} 字，glm-5.3 生成综述中（可能要1-2分钟）…")
    out = deep.chat(
        [{"role": "user", "content": DISTILL_PROMPT.format(topic=args.topic, corpus=corpus)}],
        max_tokens=6000,
    )

    topic_tag = _re.sub(r"[^\w\u4e00-\u9fff-]+", "_", args.topic)[:30]
    date = datetime.date.today()
    header = f"> 《综述：{args.topic}》（由 {len(picked)} 份笔记蒸馏，{date}）\n\n"
    path = report_dir / f"distill_{date}_{topic_tag}.md"
    report_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(header + out + "\n", encoding="utf-8")
    print(f"\n✅ 综述已生成：{path}")

    if args.prune:
        moved = archive_reports(report_dir, picked)
        print(f"已归档 {len(moved)} 份原始笔记 → data/reports/archive/（索引自动瘦身，原文保留）")
        print("确认综述质量没问题后，想彻底删除：rm -rf data/reports/archive/")


async def cmd_social(args) -> None:
    """单独触发社交清扫（楼中楼/动态评论回复），测试或补救用，不必跑完整 watch。"""
    from sprite.flow.social import DynamicHistory, SocialReplier

    cfg = load_config()
    llm_cfg = cfg["llm"]
    bili = Bili(load_credential())
    me = await bili.my_info()
    print(f"凭据有效：{me['name']} (mid={me['mid']})")
    replier = SocialReplier(
        bili=bili,
        llm=LLM(llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
                llm_cfg["temperature"], llm_cfg.get("thinking", False)),
        mem=Memory(),
        persona=cfg["persona"],
        safety=cfg["safety"],
        history=CommentHistory(),
        dynamic_history=DynamicHistory(),
        my_mid=me.get("mid"),
        dry_run=args.dry_run,
    )
    await replier.sweep()
    print(usage_summary())


def _read_napcat_ws_token() -> str:
    """从 NapCat 的 OneBot 配置文件自动读 WebSocket Token（root 目录需 sudo）。"""
    import json as _json
    import os as _os
    import subprocess as _sp

    r = _sp.run(
        ["sudo", "find", f"{_os.environ.get('NAPCAT_DIR', '/root/Napcat')}/opt/QQ/resources/app/app_launcher/napcat/config/",
         "-name", "onebot11_*.json"],
        capture_output=True, text=True, timeout=10,
    )
    for f in (r.stdout or "").strip().split("\n"):
        if not f:
            continue
        r2 = _sp.run(["sudo", "cat", f], capture_output=True, text=True, timeout=10)
        try:
            data = _json.loads(r2.stdout)
            for s in data.get("network", {}).get("websocketServers", []):
                if s.get("enable") and s.get("token"):
                    return str(s["token"])
        except Exception:
            continue
    return ""


async def cmd_qq(_args) -> None:
    """智谱娘上QQ：连接 NapCat(OneBot v11)，人设+知识库大脑，群聊@/关键词触发。"""
    from sprite.qq import OneBotClient, QqAgent
    from sprite.mind import Knowledge

    cfg = load_config()
    qq_cfg = cfg["qq"]
    llm_cfg = cfg["llm"]
    llm = LLM(
        llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
        llm_cfg["temperature"], llm_cfg.get("thinking", False),
    )

    # 自动读取 NapCat WebSocket Token（不填 config 也行）
    token = qq_cfg.get("access_token", "")
    if not token:
        token = _read_napcat_ws_token()
        if token:
            print(f"✓ 自动读取到 NapCat WebSocket Token: {token[:6]}…")
    qq_cfg["access_token"] = token
    kb = Knowledge(ROOT / cfg["watch"]["report_dir"])
    client = OneBotClient(qq_cfg["ws_url"], None, access_token=qq_cfg.get("access_token", ""))
    vision_llm = None
    if qq_cfg.get("vision_model"):
        vision_llm = LLM(llm_cfg["base_url"], llm_cfg["api_key"], qq_cfg["vision_model"],
                         temperature=0.3, thinking=False)
    agent = QqAgent(client, llm, cfg["persona"], qq_cfg, kb=kb, vision_llm=vision_llm)
    client.on_message = agent.on_message
    print(f"智谱娘 QQ 模式：{qq_cfg['ws_url']}（Ctrl+C 退出，断线自动重连）")
    print(f"触发方式：群里@她 / 说「{cfg['persona'].get('name', '')}」/ 私聊直接说；知识库 {kb.size()} 块已挂载")
    if vision_llm:
        print(f"图片理解：已启用（{qq_cfg['vision_model']}）——发截图她能看")
    # 启动时顺手做一次当日保洁（含旧会话归档清理，结果会QQ通知主人）
    from sprite.housekeep import maybe_daily_housekeep

    maybe_daily_housekeep(cfg["housekeep"], qq_cfg, llm=llm)
    await client.run()



PAPER_PROMPT = """你是资深AI研究员。对下面这篇论文写一份深度解析报告（markdown），要求：

# 论文解析：{title}
## 一句话贡献
## 核心创新点（与已有方法的对比）
## 方法详解（架构/公式/伪代码，逐步讲清楚）
## 实验结论（关键数字，用表格呈现主要结果）
## 与知识库已有内容的关联（如有）
## 局限性与后续工作方向

要求：忠实于论文内容，不编造；术语首次出现给一句解释；具体数字保留精度。

论文全文（可能截断）：
{text}"""


def cmd_paper(args) -> None:
    """论文入库：PDF → pdftotext提取 → LLM解析 → 知识库 + Obsidian。
    不带参数=吸收 data/papers 整个目录（已解析过的自动跳过）；也可显式指定PDF路径。"""
    import subprocess as _sp

    from sprite.obsidian_sync import sync_to_obsidian

    cfg = load_config()
    if not args.files:
        papers_dir = ROOT / (cfg.get("ingest") or {}).get("papers_dir", "data/papers")
        args.files = sorted(str(f) for f in papers_dir.glob("*.pdf")) if papers_dir.exists() else []
        if not args.files:
            sys.exit(f"{papers_dir} 里没有PDF。先点「📥 抓新论文」，或把PDF放进去。")
    llm_cfg = cfg["llm"]
    deep = LLM(llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
               llm_cfg["temperature"], llm_cfg.get("thinking", False), timeout=300)
    report_dir = ROOT / cfg["watch"]["report_dir"]

    for pdf_path in args.files:
        pdf = Path(pdf_path)
        if not pdf.exists():
            print(f"❌ 找不到 {pdf_path}")
            continue
        # 防重复解析：任何日期下已有同编号的论文笔记就跳过（跨天重复抓取的场景）
        if list(report_dir.glob(f"*paper_{pdf.stem[:40]}.md")):
            print(f"  ↷ {pdf.name} 已有解析笔记，跳过")
            continue
        print(f"\n═══ 解析 {pdf.name} ═══")
        try:
            r = _sp.run(["pdftotext", "-l", "100", str(pdf), "-"],
                        capture_output=True, text=True, timeout=60)
            text = r.stdout.strip()
            title = pdf.stem.replace("_", " ").replace("-", " ").title()
            if not text:
                print("  ❌ PDF无文本层（可能是扫描件），跳过")
                continue
        except Exception as e:
            print(f"  ❌ PDF读取失败: {e}")
            continue

        # 截断保护（大上下文模型也够呛，400k字符以上截断）
        if len(text) > 400_000:
            text = text[:400_000] + "\n…（过长截断）"
        print(f"  提取 {len(text)} 字，LLM解析中…")

        report = deep.chat(
            [{"role": "user", "content": PAPER_PROMPT.format(title=title, text=text)}],
            max_tokens=6000,
        )

        # 保存到知识库
        today = __import__("datetime").date.today()
        out = report_dir / f"{today}_paper_{pdf.stem[:40]}.md"
        report_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(
            f"> 论文：{title}\n> 文件：{pdf.name}\n\n{report}\n", encoding="utf-8"
        )
        print(f"  ✅ 知识库：{out}")

        # 同步到 Obsidian
        obs = sync_to_obsidian(out, tags=["paper"] + title.lower().split()[:5],
                               source=pdf.stem, doc_type="paper-analysis")
        if obs:
            print(f"  ✅ Obsidian：{obs}")

    print(f"\n今日 token：{usage_summary()}")


def cmd_ingest(args) -> None:
    """情报摄取：arXiv检索+HF日报 → PDF入 data/papers；HF博客 → 资讯报告；--absorb 直接入库。"""
    import datetime
    import json as _json

    from sprite.flow.ingest import download_pdfs, fetch_arxiv, fetch_hf_blog, fetch_hf_daily

    cfg = load_config()
    ing = cfg.get("ingest") or {}
    keywords = ing.get("keywords") or cfg["watch"]["keywords"]
    days = ing.get("lookback_days", 3)
    papers_dir = ROOT / ing.get("papers_dir", "data/papers")
    seen_f = ROOT / "data/ingest_seen.json"

    seen = set()
    if seen_f.exists():
        try:
            seen = set(_json.loads(seen_f.read_text()))
        except Exception:
            pass

    print(f"[ingest] arXiv 检索（近{days}天）：{' / '.join(keywords[:6])}")
    papers = fetch_arxiv(keywords, days=days)
    print(f"  命中 {len(papers)} 篇")
    hf = fetch_hf_daily(ing.get("hf_daily_limit", 10))
    print(f"[ingest] HF 日报 {len(hf)} 篇（社区热度优先并入）")
    merged = {p["id"]: p for p in papers}
    for p in sorted(hf, key=lambda x: -(x.get("upvotes") or 0)):
        merged.setdefault(p["id"], p)

    got = download_pdfs(list(merged.values()), papers_dir, seen, ing.get("max_download", 5))
    seen_f.write_text(_json.dumps(sorted(seen)[-2000:]))
    print(f"[ingest] ✅ 新下载 {len(got)} 篇 → {papers_dir}")

    if ing.get("hf_blog", True):
        posts = fetch_hf_blog(5)
        if posts:
            today = datetime.date.today()
            out = ROOT / cfg["watch"]["report_dir"] / f"{today}_hf资讯.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(f"- [{p['title']}]({p['link']})" for p in posts)
            out.write_text(f"> HF 官方博客近5篇（{today}，ingest 自动抓取）\n\n{body}\n", encoding="utf-8")
            print(f"[ingest] ✅ 资讯报告：{out}")

    if args.absorb and got:
        print("[ingest] --absorb：转入论文解析管线…")
        cmd_paper(argparse.Namespace(files=[str(f) for _, f in got]))
    elif got:
        print("[ingest] 加 --absorb 可直接解析入库（耗token）")


def cmd_gossip(args) -> None:
    """花边播报测试：抓素材 → 人设口吻播报预览（QQ端触发词同款逻辑）。"""
    from sprite.flow.gossip import BROADCAST_PROMPT, build_gossip

    cfg = load_config()
    raw = build_gossip()
    if not raw:
        sys.exit("暂无匹配资讯（领域关键词太窄或数据源都空了），缓存30分钟。")
    print("═══ 原始素材 ═══\n" + raw + "\n═══ 人设播报 ═══")
    llm_cfg = cfg["llm"]
    deep = LLM(llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
               llm_cfg["temperature"], llm_cfg.get("thinking", False), timeout=120)
    out = deep.chat(
        [{"role": "system", "content": cfg["persona"].get("system_prompt", "")},
         {"role": "user", "content": BROADCAST_PROMPT + raw}],
        max_tokens=700,
    )
    print(out)
    print(f"\n今日 token：{usage_summary()}")


def _undigested_dates(report_dir, skip=(), days=7):
    """近N天内：有报告但还没生成综述的日期 → [(日期, 报告数), ...]。"""
    import datetime
    found = []
    today = datetime.date.today()
    for i in range(days):
        ds = str(today - datetime.timedelta(days=i))
        if ds in skip:
            continue
        n = [f for f in report_dir.glob(f"{ds}_*.md") if not f.name.startswith("distill_")]
        if n and not (report_dir / f"distill_{ds}_daily.md").exists():
            found.append((ds, len(n)))
    return found


def cmd_daily_digest(args) -> None:
    """日期综述：指定日期（默认今天）的所有报告→一篇综述→Obsidian→（可选）清理原文。"""
    import datetime

    from sprite.mind import archive_reports
    from sprite.obsidian_sync import sync_to_obsidian

    cfg = load_config()
    llm_cfg = cfg["llm"]
    deep = LLM(llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
               llm_cfg["temperature"], llm_cfg.get("thinking", False), timeout=300)
    report_dir = ROOT / cfg["watch"]["report_dir"]

    if args.date:
        try:
            day = datetime.date.fromisoformat(args.date)
        except ValueError:
            sys.exit(f"--date 格式应为 YYYY-MM-DD，收到：{args.date}")
        if day > datetime.date.today():
            sys.exit("不能综述未来日期。")
        date_str = str(day)
    else:
        date_str = str(datetime.date.today())

    reports = [f for f in sorted(report_dir.glob(f"{date_str}_*.md"))
               if not f.name.startswith("distill_")]
    if not reports:
        msg = f"{date_str} 没有报告，无需综述。"
        missed = _undigested_dates(report_dir, skip=(date_str,))
        if missed:
            msg += "\n但发现漏综述的日期：\n" + "\n".join(
                f"  · {d}（{n}份）→ python main.py daily-digest --date {d} --prune"
                for d, n in missed)
        sys.exit(msg)

    print(f"{date_str} 共 {len(reports)} 份报告，合成综述中…")
    corpus = "\n\n=====\n\n".join(
        f"【{f.name}】\n{f.read_text(encoding='utf-8')[:3000]}" for f in reports
    )
    digest = deep.chat(
        [{"role": "user", "content":
          f"把{date_str}这{len(reports)}份AI学习笔记合成一篇当日综述（markdown）。"
          f"结构：# {date_str} 综述\n## 当日核心收获（3-5条要点）\n"
          f"## 主题深入（每个主题整合多来源，标注来源文件名）\n"
          f"## 新概念速查表\n## 值得深入的方向\n\n笔记：\n{corpus}"}],
        max_tokens=4000,
    )

    out = report_dir / f"distill_{date_str}_daily.md"
    if out.exists():
        print(f"⚠️ {out.name} 已存在，将覆盖重建（会包含该日全部报告）。")
    out.write_text(digest + "\n", encoding="utf-8")
    print(f"✅ 综述已存：{out}")

    obs = sync_to_obsidian(out, tags=["daily-digest"], source=date_str, doc_type="digest")
    if obs:
        print(f"✅ Obsidian：{obs}")

    if args.prune:
        moved = archive_reports(report_dir, [f.name for f in reports])
        print(f"✅ 已归档 {len(moved)} 份原始报告 → archive/")
    else:
        print("（加 --prune 可同时归档原始报告）")

    missed = _undigested_dates(report_dir, skip=(date_str,))
    if missed:
        print("\n📌 还有漏综述的日期（忘了点的话可补）：")
        for d, n in missed:
            print(f"  · {d}（{n}份）→ python main.py daily-digest --date {d} --prune")

    print(f"\n今日 token：{usage_summary()}")


def cmd_usage(_args) -> None:
    print(usage_summary())


def cmd_housekeep(_args) -> None:
    """手动触发一轮保洁（watch/qq 运行时每天也会自动跑一次）。"""
    from sprite.housekeep import run_housekeep

    cfg = load_config()
    sc = cfg["llm_screen"]
    llm = LLM(sc["base_url"], sc["api_key"], sc["model"], sc["temperature"], sc["thinking"])
    run_housekeep(cfg["housekeep"], cfg["qq"], llm=llm, notify=bool(_args.notify))


def main() -> None:
    install_timestamped_print()
    parser = argparse.ArgumentParser(description="sprite：LLM 驱动（GLM/DeepSeek/Qwen…）的 B站+QQ 机器人")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login").set_defaults(func=cmd_login)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("reply-loop").set_defaults(func=cmd_reply_loop)

    p_watch = sub.add_parser("watch", help="AI infra 视频情报流水线")
    p_watch.add_argument("--once", action="store_true", help="只扫一轮就退出（适合 cron）")
    p_watch.add_argument("--dry-run", action="store_true", help="预演：走全流程但不真发布评论/动态/专栏")
    p_watch.set_defaults(func=cmd_watch)

    p_ask = sub.add_parser("ask", help="查询知识库：问视频解析笔记里的问题")
    p_ask.add_argument("query", help="问题，如 'vLLM的PD分离是怎么回事'")
    p_ask.set_defaults(func=cmd_ask)

    p_dis = sub.add_parser("distill", help="蒸馏：把某主题的多份笔记浓缩成一篇综述")
    p_dis.add_argument("topic", help="主题，如 'Triton'、'vLLM'")
    p_dis.add_argument("--prune", action="store_true",
                       help="综述生成后把被吸收的原始笔记移入 archive/（索引瘦身，原文保留）")
    p_dis.add_argument("--max-files", type=int, default=10, help="最多纳入多少份笔记（默认10）")
    p_dis.set_defaults(func=cmd_distill)

    sub.add_parser("usage", help="查看今日 token 消耗").set_defaults(func=cmd_usage)

    p_paper = sub.add_parser("paper", help="论文入库（不带参数=吸收整个 data/papers 目录）")
    p_paper.add_argument("files", nargs="*", help="PDF文件路径（可选；缺省扫 papers 目录）")
    p_paper.set_defaults(func=cmd_paper)

    p_dd = sub.add_parser("daily-digest", help="日期综述：该日所有报告→一篇综述→Obsidian")
    p_dd.add_argument("--date", help="指定日期 YYYY-MM-DD（默认今天；忘点可补任何一天）")
    p_dd.add_argument("--prune", action="store_true", help="综述后归档该日原始报告")
    p_dd.set_defaults(func=cmd_daily_digest)

    p_ing = sub.add_parser("ingest", help="情报摄取：arXiv+HF论文→PDF入库，HF博客→资讯报告")
    p_ing.add_argument("--absorb", action="store_true", help="下载后直接走论文解析入库（耗token）")
    p_ing.set_defaults(func=cmd_ingest)

    p_go = sub.add_parser("gossip", help="花边播报测试：抓HN/Reddit/HF素材+人设播报预览")
    p_go.set_defaults(func=cmd_gossip)

    p_hk = sub.add_parser("housekeep", help="手动保洁：旧会话清理/日志裁尾/知识库体量检查")
    p_hk.add_argument("--no-notify", dest="notify", action="store_false",
                      help="不通过QQ通知主人")
    p_hk.set_defaults(func=cmd_housekeep, notify=True)

    p_soc = sub.add_parser("social", help="单独触发社交清扫：回复楼中楼/动态评论")
    p_soc.add_argument("--dry-run", action="store_true", help="只预览回复内容，不发布")
    p_soc.set_defaults(func=cmd_social)


    sub.add_parser("qq", help="智谱娘上QQ：连接 NapCat(OneBot v11) 常驻").set_defaults(func=cmd_qq)

    p_dyn = sub.add_parser("dynamic")
    p_dyn.add_argument("--text", help="直接发布这段文本")
    p_dyn.add_argument("--llm", action="store_true", help="让 LLM 按人设写动态")
    p_dyn.add_argument("--topic", help="LLM 写动态的主题提示")
    p_dyn.add_argument("--yes", "-y", action="store_true", help="跳过发布确认")
    p_dyn.set_defaults(func=cmd_dynamic)

    p_video = sub.add_parser("video")
    p_video.add_argument("path", help="本地视频文件路径")
    p_video.add_argument("--title", required=True, help="标题（<=80字）")
    p_video.add_argument("--desc", default="", help="简介")
    p_video.add_argument("--tags", required=True, help="标签，逗号分隔，1~10 个")
    p_video.add_argument("--tid", type=int, default=21, help="分区ID，默认21（日常）")
    p_video.add_argument("--cover", default=None, help="封面图片路径")
    p_video.set_defaults(func=cmd_video)

    args = parser.parse_args()
    # 会发布内容或改写状态的命令加进程锁；QQ 用独立锁，绝不阻塞 B 站侧的 watch/social
    # 只读/分析类命令（抓取、解析、综述、问答、保洁）不抢锁，可随时与 watch 并行
    READONLY = ("status", "ask", "usage", "login", "paper", "daily-digest",
                "distill", "ingest", "gossip", "housekeep")
    if args.cmd == "qq":
        acquire_lock("qq")
    elif args.cmd not in READONLY:
        acquire_lock()
    if inspect.iscoroutinefunction(args.func):
        asyncio.run(args.func(args))
    else:
        args.func(args)


if __name__ == "__main__":
    main()
