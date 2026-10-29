"""智谱娘 QQ 适配层：把 sprite 的人设/知识库/限流大脑接到 OneBot 消息上。

与 B 站侧完全隔离：独立数据文件（qq_sessions.json）、独立配额、独立进程锁，
不触碰 credential / memory / comment_history。
"""

import asyncio
import json
import random
import re
import time
from pathlib import Path

from ..config import DATA_DIR
from ..flow.websearch import clean_query, format_results, looks_like_question, search_web
from ..mind.guard import (
    detect_injection, frame_data, frame_owner, frame_user, guard_output,
    sanitize_user_text,
)
from ..llm import LLM

SESSION_FILE = DATA_DIR / "qq_sessions.json"

QQ_SYSTEM_SUFFIX = """
场景：QQ群/私聊。像群友说话：一两句，口语化，可用少量颜文字，禁列表标题。技术问题优先依据「知识库参考」回答，不确定就直说。"""

_CQ_AT = re.compile(r"\[CQ:at,qq=(\d+)[^\]]*\]")
_CQ_ANY = re.compile(r"\[CQ:[^\]]+\]")
_CQ_IMG_URL = re.compile(r"\[CQ:image,[^\]]*url=([^,\]]+)[^\]]*\]")

VISION_PROMPT = """你是图片解读助手。根据用户的问题解读图片：
- 报错/日志截图：完整转写关键错误信息（报错正文、文件名、行号），给出初步诊断方向
- 代码/文档截图：转写关键内容
- 其他图片：客观描述关键信息
输出纯文本，≤200字，信息优先。用户的问题：{question}"""


def extract_message(message) -> tuple[str, list[str], list[str], int | None]:
    """OneBot message → (纯文本, 被@的qq号列表, 图片URL列表, 引用回复的目标消息id)。"""
    ats: list[str] = []
    images: list[str] = []
    reply_id: int | None = None
    if isinstance(message, list):
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
            elif seg.get("type") == "at":
                ats.append(str(seg.get("data", {}).get("qq", "")))
            elif seg.get("type") == "image":
                u = (seg.get("data") or {}).get("url") or ""
                if u:
                    images.append(u)
            elif seg.get("type") == "reply":
                try:
                    reply_id = int((seg.get("data") or {}).get("id"))
                except (TypeError, ValueError):
                    pass
        return "".join(parts).strip(), [a for a in ats if a], images, reply_id
    raw = str(message or "")
    ats = _CQ_AT.findall(raw)
    images = _CQ_IMG_URL.findall(raw)
    m = re.search(r"\[CQ:reply,id=(-?\d+)\]", raw)
    reply_id = int(m.group(1)) if m else None
    return _CQ_ANY.sub("", raw).strip(), ats, [u for u in images if u], reply_id


# 兼容旧调用（仅取文本和@）
def extract_text_and_ats(message) -> tuple[str, list[str]]:
    text, ats, _, _ = extract_message(message)
    return text, ats


def to_data_url(url: str, max_bytes: int = 8 * 1024 * 1024) -> str:
    """下载QQ图床图片 → base64 data URL（绕开腾讯CDN对外链的鉴权，视觉模型能直接吃）。"""
    import base64

    import requests

    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    if len(r.content) > max_bytes:
        raise ValueError(f"图片过大 {len(r.content)}")
    mime = r.headers.get("content-type", "image/png").split(";")[0]
    if not mime.startswith("image/"):
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(r.content).decode()


class QqStore:
    """会话历史 + 冷却 + 日配额，独立于 B 站侧的 memory.json。"""

    def __init__(self, path: Path = SESSION_FILE, rounds: int = 12):
        self.path = path
        self.rounds = rounds
        d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        today = time.strftime("%Y-%m-%d")
        if d.get("counters", {}).get("date") != today:
            d["counters"] = {"date": today, "replies": 0}
        self.sessions: dict = d.get("sessions", {})
        self.cooldowns: dict = d.get("cooldowns", {})
        self.counters: dict = d.get("counters", {"date": today, "replies": 0})
        # 闭嘴：按会话粒度；"*"=全局。兼容旧版布尔 muted
        self.mutes: dict = d.get("mutes") or ({"*": True} if d.get("muted") else {})
        self._d = d

    def save(self) -> None:
        self._d = {
            "sessions": self.sessions,
            "cooldowns": self.cooldowns,
            "counters": self.counters,
            "mutes": self.mutes,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._d, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_muted(self, chat_key: str) -> bool:
        return bool(self.mutes.get("*") or self.mutes.get(chat_key))

    def set_mute(self, chat_key: str, flag: bool) -> None:
        if flag:
            self.mutes[chat_key] = True
        else:
            self.mutes.pop(chat_key, None)
        self.save()

    def clear_all_mutes(self) -> int:
        n = len(self.mutes)
        self.mutes = {}
        self.save()
        return n

    # ---------- 配额/冷却 ----------

    def quota_ok(self, limit: int) -> bool:
        return self.counters.get("replies", 0) < limit

    def bump(self) -> None:
        self.counters["replies"] = self.counters.get("replies", 0) + 1
        self.save()

    def cooldown_ok(self, chat_key: str, seconds: int) -> bool:
        return time.time() - self.cooldowns.get(chat_key, 0) >= seconds

    def touch(self, chat_key: str) -> None:
        self.cooldowns[chat_key] = time.time()
        self.save()

    # ---------- 会话 ----------

    def history(self, chat_key: str) -> list[dict]:
        return self.sessions.get(chat_key, [])

    def append(self, chat_key: str, role: str, content: str) -> None:
        sess = self.sessions.setdefault(chat_key, [])
        sess.append({"role": role, "content": content, "ts": int(time.time())})
        self.sessions[chat_key] = sess[-(self.rounds * 2):]
        self.save()

    def reset(self, chat_key: str) -> None:
        self.sessions.pop(chat_key, None)
        self.save()


class QqAgent:
    """消息事件 → 是否回应 / 生成回复 / 发送。"""

    def __init__(self, client, llm: LLM, persona: dict, qq_cfg: dict, kb=None, store: QqStore | None = None,
                 vision_llm: LLM | None = None, screen_llm=None):
        self.client = client
        self.llm = llm
        self.persona = persona
        self.screen_llm = screen_llm  # flash路由器（工具决策），None=主模型兼任
        self.cfg = qq_cfg
        self.kb = kb
        self.store = store or QqStore()
        self.vision_llm = vision_llm  # glm-4.6v：看图转写，主脑仍是 llm
        self.self_id = ""  # 从事件里学自己的QQ号
        self.owner = str(qq_cfg.get("owner_qq") or "")  # 主人QQ：人设认知 + 私聊免冷却
        self._bot_nick = ""  # 自己的QQ昵称（首条群消息时缓存，触发判定用）

    # ---------- 事件入口 ----------

    async def on_message(self, ev: dict) -> None:
        if ev.get("post_type") != "message":
            return
        self.self_id = str(ev.get("self_id") or self.self_id)
        sender = ev.get("sender") or {}
        user_id = str(ev.get("user_id") or "")
        if not user_id or user_id == self.self_id:
            return

        text, ats, images, reply_id = extract_message(ev.get("message"))
        if not text and not images:
            return
        uname = sender.get("card") or sender.get("nickname") or user_id
        is_owner = bool(self.owner) and user_id == self.owner
        speaker = "主人" if is_owner else uname
        is_group = ev.get("message_type") == "group"
        chat_key = f"g{ev.get('group_id')}" if is_group else f"u{user_id}"

        # ── 主人口令（仅主人可用，免LLM免冷却免配额，群里不用@也生效）──
        if is_owner and text.startswith(("/", "／")):
            cmd_reply = self._owner_command(text.lstrip("／/ ").strip(), chat_key)
            if cmd_reply is not None:
                if is_group:
                    await self.client.send_group_text(int(ev.get("group_id")), cmd_reply, at_user=int(user_id))
                else:
                    await self.client.send_private_text(int(user_id), cmd_reply)
                print(f"  [口令] {cmd_reply[:40]}")
                return

        # 闭嘴模式：当前会话被闭嘴时不回（主人不受影响）
        if self.store.is_muted(chat_key) and not is_owner:
            return

        # ── 安全层1+2：输入消毒 + 高精度注入硬偏转（不进LLM，零token）──
        text = sanitize_user_text(text)
        if not is_owner:
            hit = detect_injection(text)
            if hit:
                import random
                from ..mind.guard import DEFLECTIONS
                deflection = random.choice(DEFLECTIONS)
                if is_group:
                    await self.client.send_group_text(int(ev.get("group_id")), deflection, at_user=int(user_id))
                else:
                    await self.client.send_private_text(int(user_id), deflection)
                print(f"  [防注入] 已偏转：{text[:30]}…（特征: {hit[:40]}）")
                return

        # ── 花边播报：/瓜 口令或触发词（素材30分钟缓存，60秒内不重复生成）──
        gcfg = self.cfg.get("gossip") or {}
        gk = gcfg.get("keywords") or ["吃瓜", "八卦", "花边"]
        if gcfg.get("enabled", True) and (text.startswith(("/瓜", "／瓜")) or any(k in text for k in gk)):
            reply = await self._gossip_reply()
            if reply:
                if is_group:
                    await self.client.send_group_text(int(ev.get("group_id")), reply)
                else:
                    await self.client.send_private_text(int(user_id), reply)
                print(f"  [花边] 已播报（{chat_key}）")
                return

        # ── 引用原文（群/私聊通用）：被引的原文进她的上下文 ──
        quote_note, reply_her = "", False
        if reply_id:
            try:
                src = await self.client.call("get_msg", {"message_id": reply_id})
                s_sender = src.get("sender") or {}
                s_mid = str(s_sender.get("user_id") or "")
                reply_her = s_mid == self.self_id
                s_name = "你自己" if reply_her else (s_sender.get("card") or s_sender.get("nickname") or "某人")
                s_text, _, s_imgs, _ = extract_message(src.get("message"))
                s_text = (s_text + (" [图]" if s_imgs else "")).strip()
                if s_text:
                    quote_note = f"[引用{s_name}的话：{s_text[:120]}]"
            except Exception:
                pass

        if is_group:
            group_id = int(ev.get("group_id"))
            if not self._group_allowed(group_id):
                return
            # 首条群消息时缓存自己的昵称（触发判定用；@昵称的纯文本形态也认）
            if not self._bot_nick and self.self_id:
                try:
                    info = await self.client.call("get_login_info", {})
                    self._bot_nick = info.get("nickname") or ""
                except Exception:
                    pass
            hit_at = self.self_id in ats
            if not hit_at and self._bot_nick and f"@{self._bot_nick}" in text:
                hit_at = True  # 有的客户端把@当纯文本发，按字面识别
            kw = self._keyword_hit(text)
            interject = (
                random.random() < float(self.cfg.get("random_interject_rate", 0) or 0)
                if self.cfg.get("random_interject_rate")
                else False
            )
            # 引用回复：目标是"她自己"的消息时视为被点名（quote已在上一步取好原文）
            # 图片触发（手机端自然姿势，不强制先@再发图）：
            # 主人裸发图 / 指定群(image_trigger_groups)全员 / 引用她的消息+图
            img_trigger = bool(images) and (
                is_owner
                or group_id in (self.cfg.get("image_trigger_groups") or [])
                or reply_her
            )
            if not (hit_at or kw or interject or img_trigger or reply_her):
                print(f"  [群{group_id}] 未触发回复：ats={ats} nick={self._bot_nick} text={text[:30]!r}")
                # 未被触发也记进会话历史，让上下文连续（但不回复）
                if text or images:
                    self.store.append(chat_key, "user", f"{speaker}: {text or '[图片]'}")
                return
            clean = self._strip_trigger(text) if text else ""
        else:
            if not self.cfg.get("reply_private", True):
                return
            clean = text

        if not self.store.quota_ok(int(self.cfg.get("daily_reply_limit", 300))):
            print(f"  QQ 每日回复配额已满，忽略 {chat_key}")
            return
        # 主人特权：免冷却（普通用户同会话20秒冷却照常）
        if not is_owner and not self.store.cooldown_ok(chat_key, int(self.cfg.get("cooldown_seconds", 20))):
            return
        if len(clean) > 600:
            clean = clean[:600]

        # 触发消息本身没图时（群聊）：回看最近群消息，捡最近一张图来识别
        # （手机端自然姿势：先发图，再 @智谱娘 "这是啥"）
        if not images and is_group:
            recent = await self._find_recent_group_image(int(ev.get("group_id")))
            if recent:
                images = [recent]
                print(f"  [视觉] 本条无图，回捞群里最近的图：{recent[:50]}…")

        # 图片理解（两步）：glm-4.6v 看图转写 → 转成文字进入主脑上下文
        image_note = ""
        if images and self.vision_llm:
            try:
                data_urls = await asyncio.to_thread(
                    lambda: [to_data_url(u) for u in images[:2]]
                )
                desc = await asyncio.to_thread(
                    self.vision_llm.chat_vision,
                    data_urls,
                    VISION_PROMPT.format(question=clean or "用户发来一张图片，没有附加问题"),
                )
                image_note = f"[图片内容：{desc}]"
                print(f"  [视觉] {len(images)}图 → {desc[:60]}…")
            except Exception as e:
                image_note = "[图片（读取失败）]"
                print(f"  [视觉] 图片处理失败：{e!r}")
        elif images:
            image_note = "[图片]"

        self.store.append(chat_key, "user", f"{speaker}: {' '.join(x for x in (image_note, quote_note, clean) if x)}")
        reply = await self._generate(chat_key, clean or "看看这条", is_owner, image_note, quote_note)
        self.store.append(chat_key, "assistant", reply)
        self.store.touch(chat_key)
        self.store.bump()

        if is_group:
            await self.client.send_group_text(group_id, reply, at_user=int(user_id))
            print(f"  [群{group_id}] 回复 {speaker}: {reply[:40]}")
        else:
            await self.client.send_private_text(int(user_id), reply)
            print(f"  [私聊{user_id}] 回复{'(主人)' if is_owner else ''}: {reply[:40]}")

    # ---------- 内部 ----------

    # ---------- 主人口令 ----------

    async def _gossip_reply(self) -> str | None:
        """抓真实圈内资讯（HN/Reddit/HF博客）→ 人设口吻播报。素材走TTL缓存。"""
        import asyncio
        import time as _t

        from ..flow.gossip import BROADCAST_PROMPT, build_gossip

        if _t.time() - getattr(self, "_gossip_ts", 0) < 60:
            return getattr(self, "_gossip_text", None)
        gcfg = self.cfg.get("gossip") or {}
        try:
            raw = await asyncio.to_thread(
                build_gossip, None, gcfg.get("subreddit", "MachineLearning"),
                6, gcfg.get("cache_minutes", 30))
        except Exception as e:
            return f"瓜田网络打不通（{e}），晚点再来～"
        if not raw:
            self._gossip_text = "今天暂时没抓到圈内的新鲜瓜，晚点再试～"
            self._gossip_ts = _t.time()
            return self._gossip_text
        out = await asyncio.to_thread(
            self.llm.chat,
            [{"role": "system", "content": self.persona.get("system_prompt", "")},
             {"role": "user", "content": BROADCAST_PROMPT + frame_data("是刚抓取的圈内资讯素材", raw)}],
            700,
        )
        self._gossip_text, self._gossip_ts = out.strip(), _t.time()
        return self._gossip_text

    def _owner_command(self, cmd: str, chat_key: str) -> str | None:
        """仅主人：/状态 /token /闭嘴 /回来 /全局闭嘴 /全局回来 /重置记忆 /帮助。"""
        head = cmd.split()[0] if cmd else ""
        if head == "帮助":
            return ("口令：/状态 /token /搜 关键词（联网搜索） /闭嘴（本会话） /回来 /全局闭嘴 /全局回来 "
                    "/重置记忆（清空当前会话上下文）")
        if head == "状态":
            import subprocess

            watch = subprocess.run(["pgrep", "-f", "main.py watch"], capture_output=True)
            kb_n = self.kb.size() if self.kb else 0
            nap = "已连接" if (self.client and self.client._ws) else "未连接"
            mutes = "全局闭嘴中" if self.store.mutes.get("*") else f"{len(self.store.mutes)}个会话闭嘴中"
            return (f"📊 sprite 状态\nB站watch：{'运行中' if watch.returncode == 0 else '未运行'}\n"
                    f"QQ适配器：运行中\nNapCat：{nap}\n"
                    f"知识库：{kb_n} 块\n回复模式：{'闭嘴中' if self.store.mutes else '正常'}（{mutes}）")
        if head == "搜":
            kw = cmd.split(maxsplit=1)[1].strip() if " " in cmd else ""
            if not kw:
                return "用法：/搜 关键词"
            items = search_web(kw, 5)
            return "🔍 " + kw + "\n" + (format_results(items)[:1200] if items else "没搜到（换个词试试）")
        if head == "token":
            from ..logutil import usage_summary

            return usage_summary()
        if head == "全局闭嘴":
            self.store.set_mute("*", True)
            return "好捏，全局闭嘴了（你的消息还是会回）。/全局回来 解除～"
        if head == "全局回来":
            n = self.store.clear_all_mutes()
            return f"全部恢复啦捏(=ﾟωﾟ)=（清了{n}条闭嘴标记）"
        if head == "闭嘴":
            self.store.set_mute(chat_key, True)
            where = "这个群" if chat_key.startswith("g") else "这个私聊"
            return f"好捏，{where}我先闭嘴了（别处照常，主人的消息也回）。/回来 解除～"
        if head == "回来":
            self.store.set_mute(chat_key, False)
            return "我回来啦捏(=ﾟωﾟ)="
        if head == "重置记忆":
            self.store.reset(chat_key)
            return "这段会话的上下文已清空捏（知识库还在，只是忘了刚才聊了啥）"
        return None  # 不是口令，走正常对话

    async def _find_recent_group_image(self, group_id: int, lookback: int = 12, within_min: int = 10) -> str | None:
        """回看群里最近 lookback 条消息，找最近一张别人发的图（within_min 分钟内），没有则 None。"""
        try:
            hist = await self.client.call(
                "get_group_msg_history", {"group_id": group_id, "count": lookback}
            )
        except Exception:
            return None
        msgs = (hist.get("messages") or []) if isinstance(hist, dict) else []
        now = __import__("time").time()
        for m in reversed(msgs):  # 从最新往回找
            sender = str((m.get("sender") or {}).get("user_id") or "")
            if sender == self.self_id:
                continue
            if now - float(m.get("time") or now) > within_min * 60:
                break  # 再往前的更旧，直接停
            _, _, imgs, _ = extract_message(m.get("message"))
            if imgs:
                return imgs[0]
        return None

    def _group_allowed(self, group_id: int) -> bool:
        bl = self.cfg.get("group_blacklist") or []
        wl = self.cfg.get("group_whitelist") or []
        if group_id in bl:
            return False
        if wl and group_id not in wl:
            return False
        return True

    def _keyword_hit(self, text: str) -> bool:
        name = self.persona.get("name", "")
        kws = [name] + [n for n in (self._bot_nick, *(self.cfg.get("trigger_keywords") or [])) if n]
        return any(k and k in text for k in kws)

    def _strip_trigger(self, text: str) -> str:
        name = self.persona.get("name", "")
        for k in [name, self._bot_nick] + [k for k in (self.cfg.get("trigger_keywords") or []) if k]:
            if k:
                text = text.replace(k, "", 1)
        return text.strip(" @，,：:!") or text

    def _kb_hint(self, query: str) -> str:
        if not self.kb:
            return ""
        hits = self.kb.search(query, k=2)
        if not hits:
            return ""
        return "\n知识库参考（你自己整理的视频笔记，优先依据它回答）：\n" + "\n".join(
            f"- 《{h['video']}》{h['section']}：{h['text'][:150]}" for h in hits
        )

    async def _generate(self, chat_key: str, text: str, is_owner: bool = False,
                        image_note: str = "", quote_note: str = "", uname: str = "群友") -> str:
        from ..mind.guard import SECURITY_PREAMBLE
        system = SECURITY_PREAMBLE.format(name=self.persona.get("name", "智谱娘")) + (
            self.persona.get("system_prompt") or "你是QQ群里的AI群友。") + QQ_SYSTEM_SUFFIX
        if self.owner:
            system += (
                f"\n你的主人（创造你的人）的QQ号是 {self.owner}，"
                f"会话记录里名字标注为「主人」。主人了解你的全部设定，"
                f"对他更亲近自然些，不用担心在他面前'演'。"
            )
        if is_owner:
            system += "\n【本次发消息的人正是你的主人本人。】"
        # MemGPT式recall：该会话若有归档档案（旧会话被清理时蒸出的摘要），注入印象
        from ..housekeep import load_profiles

        profile = load_profiles().get(chat_key)
        if profile:
            system += f"\n你对本会话的历史印象（此前对话的归档摘要）：{profile.get('summary', '')}"
        msgs = [{"role": "system", "content": system}]
        for h in self.store.history(chat_key)[:-1]:  # 最后一条已拼入 user 消息；历史单条截长防膨胀
            c = h["content"]
            msgs.append({"role": h["role"], "content": c if len(c) <= 160 else c[:159] + "…"})
        notes = " ".join(x for x in (image_note, quote_note) if x)
        if notes:
            notes = frame_data("是引用/图片等参考信息", notes)
        body = (frame_owner("主人", text) if is_owner else frame_user(uname, text))
        user_content = "\n\n".join(x for x in (notes, body) if x)

        # ── v2 工具总线：模型自主决定 kb_search/web_search/feed_news ──
        if self.cfg.get("tool_bus", True):
            from ..mind.tools import ToolBus, build_tools
            trace = []
            bus = ToolBus(self.llm, build_tools(kb=self.kb),
                          int(self.cfg.get("tool_max_iters", 3)),
                          router_llm=getattr(self, "screen_llm", None))
            out = await asyncio.to_thread(bus.run, system, user_content, 600, trace)
            for name, q, _r in trace:
                print(f"  [工具] {name}({q[:20]})")
            out = guard_output(out, self.persona.get("name", "智谱娘"))
            limit = int(self.cfg.get("max_reply_chars", 200))
            return out if len(out) <= limit else out[: limit - 1] + "…"

        kb_raw = self._kb_hint(text)
        kb = frame_data("是从本地知识库检索到的笔记片段", kb_raw) if kb_raw else ""
        web = ""
        if self.cfg.get("web_search", True) and looks_like_question(text):
            import asyncio as _aio
            try:
                items = await _aio.to_thread(search_web, clean_query(text), 5)
                if items:
                    web = frame_data(
                        "是联网搜索结果（自动搜索补充）。知识库内容若与问题无关就忽略它，"
                        "优先依据搜索结果回答并注明来源链接；两边都无关就直说不知道",
                        format_results(items))
                    print(f"  [联网] 搜索「{clean_query(text)[:24]}」命中{len(items)}条")
            except Exception as e:
                print(f"  [联网] 搜索失败: {e}")
        msgs.append({"role": "user", "content": "\n\n".join(x for x in (kb, web, user_content) if x)})
        # 同步 requests 会卡死事件循环（收不了新消息），必须放线程池跑
        out = await asyncio.to_thread(self.llm.chat, msgs, 300)
        out = guard_output(out, self.persona.get("name", "智谱娘"))
        limit = int(self.cfg.get("max_reply_chars", 200))
        return out if len(out) <= limit else out[: limit - 1] + "…"
