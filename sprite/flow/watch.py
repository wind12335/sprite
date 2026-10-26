"""watch 流水线：找视频 → CC字幕 → flash 快筛 → glm-5.3 深度解析 → 动态@我 + 专栏 + 本地报告。"""

import asyncio
import html
import json
import random
import re
from pathlib import Path

from ..bili.account import Bili
from ..mind.brain import Memory
from ..config import ROOT
from ..bili.discover import fetch_subtitle_text, latest_from_up, pubdate_str, search_new_videos
from ..mind.knowledge import CommentHistory, Knowledge
from ..llm import LLM
from ..logutil import usage_summary
from .social import DynamicHistory, SocialReplier, extract_rpid


def _jitter(base: float, ratio: float = 0.4) -> float:
    """固定间隔本身是机器人指纹，全部加 ±40% 随机抖动。"""
    return base * random.uniform(1 - ratio, 1 + ratio)

PRESCREEN_PROMPT = """只根据标题和简介判断这个视频是否值得进一步处理（服务对象：关注AI基础设施/大模型/算子优化/AI编译器的工程师）。
以下情况判 relevant=false（只输出JSON，无其他内容）：
- 明显是卖课/训练营/招生/就业导向（标题含"课程/训练营/涨薪/就业/大纲"等强信号）
- 明显与AI技术无关（生活/游戏/娱乐等）
- 拿不准时判 true（交给后续字幕级精筛）
{{"relevant": true 或 false, "reason": "一句话"}}

标题：{title}
UP：{author}
简介：{description}"""

SCREEN_PROMPT = """你是视频内容筛选器，服务对象是一位关注「AI 基础设施 / 大模型技术 / AI 论文解读」的工程师。
判断下面这个视频是否真的在讲技术内容（原理/论文/工程实现），排除以下全部类型：
- 付费课程/训练营/培训班/招生宣讲——凡是目的是卖课、引流报名、讲"就业/涨薪/简历"的视频一律不相关，即使其中夹了技术内容
- 纯新闻资讯、带货、营销号缝合、纯体验测评

输出严格 JSON（不要输出任何其他内容、不要用 markdown 代码块包裹）：
{{"relevant": true 或 false, "reason": "一句话判断依据", "digest": "100字以内的内容概括"}}

视频标题：{title}
UP主：{author}
简介:{description}
字幕全文（带时间戳）：
{subtitle}
"""

COMPRESS_PROMPT = """把这段视频字幕压缩成结构化要点笔记，供后续深度解析使用。
要求：
1. 保留全部技术细节：概念定义、原理推导、数字/参数、架构组件名、结论
2. 保留关键时间戳（每要点前标 [mm:ss]）
3. 删掉：寒暄、重复、口水话、求三连、广告
4. 输出≤3000字的要点列表，信息密度拉满，宁可短不可漏技术点

字幕：
{subtitle}"""

DEEP_PROMPT = """你是资深 AI 基础设施工程师。基于下面这个视频的字幕，写一篇完整的中文解析报告，用 markdown 输出。

结构要求：
# 《{title}》解析
## 一句话结论
## 背景与问题
## 核心原理（逐步讲清楚讲对，涉及算法/架构/公式/伪代码就写出来；字幕没讲清的地方注明"视频未展开"）
## 关键要点（列表，5条以内）
## 值得看的片段（结合字幕时间戳，给出 [mm:ss] 起点 + 该段在讲什么，3~6段）
## 适合谁看

要求：忠实于视频内容，不要编造字幕里没有的信息；技术名词首次出现给出一句解释。
相关旧解析笔记（你之前整理过的同类内容；有联系就对比关联，重点讲这个视频新增/不同/更深入的地方，不要复述旧内容）：
{related}
UP主：{author}，发布于 {pubdate}，视频时长约 {duration} 分钟。
视频链接：https://www.bilibili.com/video/{bvid}
字幕全文（带时间戳）：
{subtitle}
"""

COMMENT_PROMPT = """你刚看完一个视频并写了深度解析。现在要用你的日常口吻，在这个视频的评论区发一条总结评论。

要求：
1. 以你的口吻（{name}的人设）写，像真人随手写的观后总结，口语化、自然，禁止"首先/其次/总之/综上"这类AI腔
2. 100~200字，讲清楚这期视频的核心内容和一个你觉得最有价值的点
3. 不加表情指令、不加话题标签、不用列表和标题，就是一段大白话
4. 结尾自然收，不用引导关注不用客套
5. 绝对不要在评论里@任何人（包括UP主）——提到UP主就用"UP主"三个字称呼
6. 参考下面你的旧笔记和旧评论：这次不要重复以前的观点和句式；如果话题有关联，要体现出认知在递进（比如"上次XX视频里讲过A，这期把B补上了"这种感觉）

你以前的相关笔记（避免重复、体现积累，别照抄）：
{related}
你以前发过的评论（这次别再说类似的话）：
{old_comments}

视频：《{title}》（UP：{author}）
快筛摘要：{digest}
你的深度解析（节选）：
{report_head}
"""


def extract_json(text: str) -> dict | None:
    """从模型输出里抠 JSON（容忍 ```json 围栏和前后废话）。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def md_to_html(md: str) -> str:
    """极简 markdown → HTML（专栏接口吃 HTML）。只处理标题/列表/粗体/行内代码/代码块/段落。"""
    lines = md.splitlines()
    out: list[str] = []
    in_code, in_ul = False, False
    buf: list[str] = []
    code_lines: list[str] = []

    def esc(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)

    def flush_para() -> None:
        nonlocal buf
        if buf:
            out.append(f"<p>{esc(' '.join(buf))}</p>")
            buf = []

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for ln in lines:
        if ln.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
            else:
                flush_para()
                close_ul()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(ln)
            continue
        s = ln.strip()
        if not s:
            flush_para()
            close_ul()
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush_para()
            close_ul()
            lvl = min(len(m.group(1)) + 1, 5)  # 专栏正文从 h2 起更稳
            out.append(f"<h{lvl}>{esc(m.group(2))}</h{lvl}>")
            continue
        if s.startswith("- ") or s.startswith("* "):
            flush_para()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{esc(s[2:])}</li>")
            continue
        if re.match(r"^\d+\.\s+", s):
            flush_para()
            close_ul()
            out.append(f"<p>{esc(s)}</p>")
            continue
        close_ul()
        buf.append(s)
    if in_code and code_lines:
        out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    flush_para()
    close_ul()
    return "\n".join(out)


class Watcher:
    def __init__(
        self,
        bili: Bili,
        deep_llm: LLM,
        screen_llm: LLM,
        watch_cfg: dict,
        mem: Memory,
        persona_cfg: dict | None = None,
        aigc_label: str = "🤖AI生成",
        kb: Knowledge | None = None,
        history: CommentHistory | None = None,
        safety_cfg: dict | None = None,
        hk_cfg: dict | None = None,
        qq_cfg: dict | None = None,
        dry_run: bool = False,
    ):
        self.bili = bili
        self.deep = deep_llm
        self.screen = screen_llm
        self.cfg = watch_cfg
        self.mem = mem
        self.persona = persona_cfg or {}
        self.aigc_label = aigc_label
        self.kb = kb
        self.history = history
        self.safety_cfg = safety_cfg or {}
        self.hk_cfg = hk_cfg
        self.qq_cfg = qq_cfg
        self.dry_run = dry_run
        self.report_dir = ROOT / self.cfg.get("report_dir", "data/reports")
        self._master_uname: str | None = None
        self._comment_at_uname: str | None = None
        self._last_comment_ts: float = 0.0
        self._comment_fail_streak = 0
        self.dyn_hist = DynamicHistory()
        self.social: SocialReplier | None = None

    # ---------- 流程 ----------

    async def run(self, once: bool = False) -> None:
        if self.cfg.get("master_uid"):
            self._master_uname = await self.bili.get_uname(self.cfg["master_uid"])
        else:
            print("提示：未填 master_uid，动态@通知将跳过")
        if self.cfg.get("post_comment") and self.cfg.get("comment_at_uid"):
            self._comment_at_uname = await self.bili.get_uname(self.cfg["comment_at_uid"])
            print(f"评论将 @：{self._comment_at_uname}")
        print(
            f"watch 启动：@{self._master_uname or '（未设置）'}，"
            f"每天最多看 {self.cfg['candidates_per_day']} 个视频"
            + ("，【DRY-RUN：只预览不发布】" if self.dry_run else "")
        )
        if self.kb:
            print(f"知识库：{self.kb.size()} 个笔记块（报告目录 {self.report_dir}）")
        try:
            me = await self.bili.my_info()
            print(f"凭据有效：{me['name']} (mid={me['mid']})")
        except Exception as e:
            raise SystemExit(f"凭据校验失败（{e}），请重新执行: python main.py login")

        # 社交闭环：楼中楼 + 自己动态评论区回复（防杠护栏在 SocialReplier 内）
        if self.cfg.get("social_sweep", True):
            self.social = SocialReplier(
                bili=self.bili,
                llm=self.deep,
                mem=self.mem,
                persona=self.persona,
                safety=self.safety_cfg or {},
                history=self.history,
                dynamic_history=self.dyn_hist,
                my_mid=me.get("mid"),
                dry_run=self.dry_run,
            )

        while True:
            try:
                await self._scan()
                if self.social:
                    await self.social.sweep()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"本轮扫描出错：{e}")
            print(usage_summary())
            if self.hk_cfg:
                from ..housekeep import maybe_daily_housekeep

                maybe_daily_housekeep(self.hk_cfg, self.qq_cfg or {}, llm=self.screen)
            if once:
                return
            await asyncio.sleep(_jitter(self.cfg["scan_interval_seconds"]))

    async def _scan(self) -> None:
        budget = self.cfg["candidates_per_day"]
        if not self.mem.quota_ok("candidates", budget):
            print("今日候选配额已用完，等下一轮。")
            return

        items = await self._candidates()
        print(f"发现 {len(items)} 个待处理候选（去重/时长/时间窗过滤后）")

        for item in items:
            if not self.mem.quota_ok("candidates", budget):
                print("今日候选配额用完。")
                return
            key = f"watch:{item['bvid']}"
            if self.mem.is_seen(key):
                continue
            # 先标记再处理：即使中途出错也不重复烧配额（每天候选足够多）
            self.mem.mark_seen(key)
            self.mem.bump("candidates")
            try:
                await self._process(item)
            except Exception as e:
                print(f"  处理 {item['bvid']} 出错：{e}")
            await asyncio.sleep(_jitter(5))

    async def _candidates(self) -> list[dict]:
        items = await search_new_videos(
            self.cfg.get("keywords", []),
            self.cfg["lookback_days"],
            self.cfg["min_duration_seconds"],
        )
        for up_mid in self.cfg.get("watch_ups") or []:
            try:
                items += await latest_from_up(
                    up_mid, self.bili.cred, self.cfg["lookback_days"], self.cfg["min_duration_seconds"]
                )
            except Exception as e:
                print(f"  拉取 UP({up_mid}) 投稿失败：{e}")
        # 去重 + 按发布时间倒序，多取一倍给筛选留余量
        dedup = {it["bvid"]: it for it in items}
        return sorted(dedup.values(), key=lambda x: x["pubdate"], reverse=True)[: self.cfg["candidates_per_day"] * 2]

    async def _process(self, item: dict) -> None:
        tag = f"[{item['bvid']}]《{item['title']}》"

        # 刀1：标题/简介预筛（LLMLingua粗到细思想：先用~200token的标题简介
        # 拒掉明显不相关/卖课的候选，省掉拉字幕+1.4万字幕快筛的整条成本）
        pre = extract_json(
            self.screen.chat(
                [{
                    "role": "user",
                    "content": PRESCREEN_PROMPT.format(
                        title=item["title"], author=item["author"],
                        description=item["description"][:200]),
                }],
                max_tokens=120,
            )
        )
        if pre and not pre.get("relevant"):
            print(f"  {tag} 预筛拒（{pre.get('reason', '')[:30]}），未拉字幕")
            return

        info, subtitle = await fetch_subtitle_text(item["bvid"], self.bili.cred)
        if not subtitle:
            print(f"  {tag} 无CC字幕，跳过")
            return
        # 原始字幕全文落盘（压缩审计链：报告若疑丢细节，随时回查原文/关压缩重跑）
        sub_dir = ROOT / "data" / "subtitles"
        sub_dir.mkdir(parents=True, exist_ok=True)
        sub_path = sub_dir / f"{pubdate_str(item['pubdate'])[:10]}_{item['bvid']}.txt"
        sub_path.write_text(subtitle, encoding="utf-8")
        print(f"  {tag} 字幕 {len(subtitle)} 字（原文存 {sub_path.name}），快筛中…")

        verdict = extract_json(
            self.screen.chat(
                [
                    {
                        "role": "user",
                        "content": SCREEN_PROMPT.format(
                            title=item["title"],
                            author=item["author"],
                            description=item["description"][:300],
                            subtitle=subtitle,
                        ),
                    }
                ],
                max_tokens=300,
            )
        )
        if not verdict or not verdict.get("relevant"):
            print(f"  {tag} 不相关（{(verdict or {}).get('reason', '模型输出解析失败')}），丢弃")
            return
        print(f"  {tag} 相关：{verdict.get('digest', '')[:60]}")

        # 刀2：字幕预压缩（flash压成要点再喂5.3，输入省75-85%；关闭开关则用原始字幕）
        deep_input = subtitle
        if self.cfg.get("subtitle_compress", True) and len(subtitle) > 3000:
            deep_input = self.screen.chat(
                [{"role": "user", "content": COMPRESS_PROMPT.format(subtitle=subtitle)}],
                max_tokens=3500,
            )
            print(f"  {tag} 字幕压缩 {len(subtitle)}→{len(deep_input)} 字")

        # 刀4：日token预算护栏——超标则降级（不跑深度解析，只留快筛摘要当报告）
        from ..logutil import today_usage

        budget = int(self.cfg.get("daily_token_budget", 0) or 0)
        if budget and sum(
            v.get("prompt_tokens", 0) + v.get("completion_tokens", 0)
            for v in today_usage().get("models", {}).values()
        ) > budget:
            print(f"  {tag} ⚠️今日token已超预算{budget}，降级：跳过深度解析")
            self._save_report(item, f"> 快筛摘要（预算降级，未做深度解析）\n\n{verdict.get('digest','')}\n",
                              verdict.get("digest", ""))
            return

        report_md = self.deep.chat(
            [
                {
                    "role": "user",
                    "content": DEEP_PROMPT.format(
                        title=item["title"],
                        author=item["author"],
                        pubdate=pubdate_str(item["pubdate"]),
                        duration=round(item["duration"] / 60),
                        bvid=item["bvid"],
                        subtitle=deep_input,
                        related=self._related_notes(item),
                    ),
                }
            ],
            max_tokens=4000,
        )
        self._save_report(item, report_md, verdict.get("digest", ""))

        cv_url = ""
        if self.cfg.get("publish_article"):
            if self.dry_run:
                print(f"  【DRY-RUN】将发布专栏：《【解析】{item['title']}》[:95]（正文略）")
            else:
                try:
                    aid = await self.bili.publish_article(
                        title=f"【解析】{item['title']}"[:95],
                        content_html=md_to_html(report_md),
                        category_id=self.cfg.get("article_category_id", 15),
                    )
                    cv_url = f"https://www.bilibili.com/read/cv{aid}"
                    print(f"  {tag} 专栏已提交：{cv_url}")
                except Exception as e:
                    print(f"  {tag} 专栏发布失败（{e}），完整解析已存本地")

        if self.mem.quota_ok("watch_dynamic", self.cfg["daily_dynamic_limit"]):
            text = self._compose_dynamic(item, verdict.get("digest", ""), cv_url)
            if self.dry_run:
                print(f"  【DRY-RUN】将发动态@{self._master_uname}：\n    {text[:100]}…")
            else:
                result = await self.bili.post_dynamic_at(text, self.cfg["master_uid"], self._master_uname)
                self.mem.bump("watch_dynamic")
                self.dyn_hist.add(result.get("dyn_id") if isinstance(result, dict) else None, text)
                print(f"  {tag} 已发动态@{self._master_uname}")
        else:
            print("  今日动态配额已满，仅存报告/专栏。")

        await self._comment_on_video(tag, item, info, verdict.get("digest", ""), report_md)

    async def _comment_on_video(
        self, tag: str, item: dict, info: dict, digest: str, report_md: str
    ) -> None:
        """在被解析的视频下发一条人设口吻的总结评论（公开行为，配额与间隔都要保守）。"""
        if not self.cfg.get("post_comment"):
            return
        if self._comment_fail_streak >= 2:
            print("  ⚠️ 评论连续失败已达2次（疑似触发风控），本轮剩余视频跳过评论。")
            return
        limit = self.cfg.get("comment_daily_limit", 5)
        if not self.mem.quota_ok("watch_comment", limit):
            print("  今日评论配额已满，跳过评论。")
            return

        gap = self.cfg.get("comment_gap_seconds", 120)
        now = asyncio.get_event_loop().time()
        wait = gap - (now - self._last_comment_ts) if self._last_comment_ts else 0
        if wait > 0:
            wait = _jitter(wait)
            print(f"  为评论间隔风控等待 {int(wait)}s…")
            await asyncio.sleep(wait)

        body = self._comment_text(item, digest, report_md)
        text = self._compose_comment(body)

        if self.dry_run:
            print(f"  【DRY-RUN】将在 {tag} 下发布评论：\n    {text}")
            return

        try:
            result = await self.bili.post_video_comment(info["aid"], text)
        except Exception as e:
            self._comment_fail_streak += 1
            print(f"  ⚠️ 评论发布失败（{e}），连续失败 {self._comment_fail_streak}/2")
            return
        self._comment_fail_streak = 0
        self._last_comment_ts = asyncio.get_event_loop().time()
        self.mem.bump("watch_comment")
        if self.history:
            rpid = extract_rpid(result)
            if not rpid:
                print("  ⚠️ 未能从返回中解析评论rpid，该评论将不参与楼中楼自动回复")
            self.history.add(item["title"], item["bvid"], text, aid=info["aid"], rpid=rpid)
        at = f"（@{self._comment_at_uname}）" if self._comment_at_uname else ""
        print(f"  {tag} 已在视频下发布总结评论{at}")

    # ---------- 纯函数 ----------

    def _related_notes(self, item: dict, k: int = 2) -> str:
        """从知识库找同主题旧解析（排除当前视频自己的报告）。"""
        if not self.kb:
            return "（无）"
        exclude = f"{pubdate_str(item['pubdate'])[:10]}_{item['bvid']}.md"
        hits = self.kb.search(item["title"], k=k, exclude_file=exclude)
        if not hits:
            return "（无）"
        return "\n".join(
            f"- 《{h['video']}》的「{h['section']}」节：{h['text'][:200]}…" for h in hits
        )

    def _comment_text(self, item: dict, digest: str, report_md: str) -> str:
        """用深度模型 + 人设 system prompt 写评论（输出短，成本可控）。"""
        system = self.persona.get(
            "system_prompt", "你是B站用户，说话口语化自然，像真人。"
        )
        prompt = COMMENT_PROMPT.format(
            name=self.persona.get("name", "助手"),
            title=item["title"],
            author=item["author"],
            digest=digest,
            report_head=report_md[:1500],
            related=self._related_notes(item),
            old_comments=self._old_comments(item, digest),
        )
        return self.deep.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
        )

    def _compose_comment(self, body: str) -> str:
        """拼装最终评论：受控@前缀（零宽字符格式触发通知）+ 清洗后正文 + AIGC标识。"""
        # B站评论@的生效格式：@用户名 + 两个零宽空格(U+200B) + 普通空格
        prefix = f"@{self._comment_at_uname}\u200b\u200b " if self._comment_at_uname else ""
        label = self.aigc_label
        limit = self.cfg.get("comment_max_chars", 280)
        budget = max(limit - len(prefix) - len(label) - 1, 30)
        # 先剥掉 LLM 自己写的任何@（防止误@UP主/陌生人），受控前缀随后拼
        from ..logutil import strip_mentions

        body = " ".join(strip_mentions(body).split())
        if len(body) > budget:
            body = body[: budget - 1] + "…"
        return f"{prefix}{body} {label}".strip()

    def _old_comments(self, item: dict, digest: str, k: int = 3) -> str:
        """取相似旧评论，供提示词里做"别重复"约束。"""
        if not self.history:
            return "（无）"
        olds = self.history.similar(f"{item['title']} {digest}", k=k)
        return "\n".join(f"- {o}" for o in olds) if olds else "（无）"

    def _compose_dynamic(self, item: dict, digest: str, cv_url: str) -> str:
        parts = [
            f"今日AI精选《{item['title']}》UP:{item['author']}",
            digest.strip() or "（详见专栏）",
        ]
        if cv_url:
            parts.append(f"完整解析：{cv_url}")
        parts.append("视频：https://www.bilibili.com/video/" + item["bvid"])
        text = "\n".join(parts)
        limit = self.cfg["dynamic_max_chars"]
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _save_report(self, item: dict, report_md: str, digest: str) -> Path:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / f"{pubdate_str(item['pubdate'])[:10]}_{item['bvid']}.md"
        head = (
            f"> 《{item['title']}》 UP:{item['author']} {pubdate_str(item['pubdate'])}\n"
            f"> https://www.bilibili.com/video/{item['bvid']}\n"
            f"> 快筛摘要：{digest}\n\n"
        )
        path.write_text(head + report_md + "\n", encoding="utf-8")
        print(f"  报告已存：{path}")
        # 同步到 Obsidian
        from ..obsidian_sync import sync_to_obsidian

        obs = sync_to_obsidian(path, tags=self._extract_tags(item["title"]),
                               source=item["bvid"], doc_type="video-analysis")
        if obs:
            print(f"  → Obsidian：{obs}")
        return path

    @staticmethod
    def _extract_tags(title: str) -> list[str]:
        """从标题提取简单标签（英文词+已知技术词）。"""
        import re as _re

        words = _re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", title)
        known = {"vllm", "cuda", "triton", "cutlass", "mlir", "llvm", "kv", "gpu",
                 "sglang", "flashattention", "pd", "lora", "moe", "mla", "deepseek"}
        tags = [w.lower() for w in words if len(w) >= 3]
        tags += [k for k in known if k in title.lower()]
        return list(set(tags))[:8]
