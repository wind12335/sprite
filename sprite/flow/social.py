"""社交闭环：回复别人对我们评论/动态的评论（楼中楼 + 动态评论区），带防杠护栏。"""

import asyncio
import json
import random
import time
from pathlib import Path

from bilibili_api import comment

from ..bili.account import Bili, _rpid
from ..mind.brain import Memory
from ..config import DATA_DIR
from ..llm import LLM

DYNAMIC_HIST_FILE = DATA_DIR / "dynamic_history.json"

GUARD_RULES = """
回复硬规则（优先级高于人设）：
- 只聊技术，≤60字，不反问挑起争论
- 对方是质疑/抬杠：礼貌承认视角不同就收，最多一句，绝不连续争辩
- 不讨论政治、敏感事件、人身攻击
- 不确定的技术点直说"这个我还没弄明白"，不硬编
- 不引导关注、不带链接
"""


def extract_rpid(obj) -> int | None:
    """从 send_comment 返回结构里递归找 rpid（B站返回结构变过多次，BFS 最稳）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "rpid":
                if isinstance(v, dict):
                    v = v.get("rpid")
                try:
                    return int(v)
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            r = extract_rpid(v)
            if r:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = extract_rpid(it)
            if r:
                return r
    return None


class DynamicHistory:
    """已发动态记录（用于回来回复动态下的评论）。"""

    def __init__(self, path: Path = DYNAMIC_HIST_FILE):
        self.path = path
        self._items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def add(self, dyn_id, text: str) -> None:
        if dyn_id is None:
            return
        self._items.append({"dyn_id": int(dyn_id), "text": text[:200], "ts": int(time.time())})
        self._items = self._items[-100:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), encoding="utf-8")

    def recent(self, n: int = 20) -> list[dict]:
        return self._items[-n:]


class SocialReplier:
    """扫描"我们发的评论/动态"下面别人对我们的回复并回应。

    防杠护栏三件套：每线程每用户回复上限、每日总量、生成端硬规则。
    """

    def __init__(
        self,
        bili: Bili,
        llm: LLM,
        mem: Memory,
        persona: dict,
        safety: dict,
        history,  # knowledge.CommentHistory
        dynamic_history: DynamicHistory,
        my_mid: int | None = None,
        dry_run: bool = False,
    ):
        self.bili = bili
        self.llm = llm
        self.mem = mem
        self.persona = persona
        self.safety = safety
        self.history = history
        self.dyn_hist = dynamic_history
        self.my_mid = str(my_mid) if my_mid else ""
        self.dry_run = dry_run

    # ---------- 主流程 ----------

    async def sweep(self) -> None:
        """一轮社交清扫：先评论区楼中楼，再动态评论区。"""
        await self._sweep_comment_threads()
        await self._sweep_dynamics()

    async def _sweep_comment_threads(self) -> None:
        entries = [e for e in getattr(self.history, "_items", []) if e.get("aid") and e.get("rpid")]
        if not entries:
            return
        print(f"社交清扫：检查 {len(entries[-30:])} 条我方评论的楼中楼…")
        for e in entries[-30:]:
            try:
                await self._handle_one_thread(e)
                await asyncio.sleep(random.uniform(3, 8))
            except KeyboardInterrupt:
                raise
            except Exception as ex:
                print(f"  楼中楼检查出错（{e.get('bvid')}）：{ex}")

    async def _handle_one_thread(self, e: dict) -> None:
        aid, root = int(e["aid"]), int(e["rpid"])
        data = await self.bili.video_comments(aid)
        root_c = next((c for c in (data.get("replies") or []) if _rpid(c) == root), None)
        if root_c is None:
            return  # 我们的评论不在第一页，跳过（或已被删）
        replies = root_c.get("replies") or []
        if not replies:
            return
        for sub in replies:
            rpid = _rpid(sub)
            key = f"sr:{rpid}"
            if self.mem.is_seen(key):
                continue
            mid = str(sub["member"]["mid"])
            if self.my_mid and mid == self.my_mid:
                self.mem.mark_seen(key)
                continue
            uname = sub["member"]["uname"]
            text = (sub.get("content") or {}).get("message", "").strip()
            if not text:
                self.mem.mark_seen(key)
                continue
            # 防杠：每线程每用户上限
            count = e.setdefault("replied", {}).get(mid, 0)
            if count >= int(self.safety.get("per_user_per_thread", 2)):
                self.mem.mark_seen(key)
                print(f"  @{uname} 已达每线程回复上限，沉默处理")
                continue
            # 防杠：被杠第二次直接礼貌收尾话术（不再走LLM自由发挥）
            if count == int(self.safety.get("per_user_per_thread", 2)) - 1:
                reply = "有道理，你觉得对就好捏，这块我再去补补课～"
            else:
                if not self.mem.quota_ok("thread_reply", int(self.safety.get("reply_in_thread_daily_limit", 10))):
                    print("  今日楼中楼回复配额已满。")
                    return
                reply = self._gen_reply(e.get("video", ""), e.get("text", ""), uname, text)
            if self.dry_run:
                print(f"  【DRY-RUN】回复 @{uname}（楼中楼）：{reply}")
                continue  # 预演不留副作用：不写去重标记
            await self._send_thread_reply(aid, root, rpid, reply)
            self.mem.bump("thread_reply")
            e.setdefault("replied", {})[mid] = count + 1
            self.history.save()
            print(f"  已回复 @{uname} 的楼中楼：{reply[:40]}")
            self.mem.mark_seen(key)
            await asyncio.sleep(random.uniform(20, 45))

    async def _send_thread_reply(self, aid: int, root: int, parent: int, text: str) -> None:
        await comment.send_comment(
            text=text,
            oid=aid,
            type_=comment.CommentResourceType.VIDEO,
            root=root,
            parent=parent,
            credential=self.bili.cred,
        )

    async def _sweep_dynamics(self) -> None:
        items = self.dyn_hist.recent(10)
        if not items:
            return
        print(f"社交清扫：检查 {len(items)} 条我方动态的评论区…")
        for d in items:
            try:
                data = await comment.get_comments(
                    oid=int(d["dyn_id"]),
                    type_=comment.CommentResourceType.DYNAMIC,
                    page_index=1,
                    credential=self.bili.cred,
                )
                for c in data.get("replies") or []:
                    rpid = _rpid(c)
                    key = f"sr:{rpid}"
                    if self.mem.is_seen(key):
                        continue
                    mid = str(c["member"]["mid"])
                    uname = c["member"]["uname"]
                    text = (c.get("content") or {}).get("message", "").strip()
                    if not text:
                        self.mem.mark_seen(key)
                        continue
                    if not self.mem.quota_ok("thread_reply", int(self.safety.get("reply_in_thread_daily_limit", 10))):
                        return
                    reply = self._gen_reply("我的动态", d.get("text", ""), uname, text)
                    if self.dry_run:
                        print(f"  【DRY-RUN】回复动态评论 @{uname}：{reply}")
                        continue  # 预演不留副作用
                    await comment.send_comment(
                        text=reply,
                        oid=int(d["dyn_id"]),
                        type_=comment.CommentResourceType.DYNAMIC,
                        root=rpid,
                        credential=self.bili.cred,
                    )
                    self.mem.bump("thread_reply")
                    print(f"  已回复动态评论 @{uname}：{reply[:40]}")
                    self.mem.mark_seen(key)
                    await asyncio.sleep(random.uniform(20, 45))
            except KeyboardInterrupt:
                raise
            except Exception as ex:
                print(f"  动态({d.get('dyn_id')})评论检查出错：{ex}")

    # ---------- 生成 ----------

    def _gen_reply(self, context: str, our_text: str, uname: str, their_text: str) -> str:
        from ..logutil import strip_mentions

        system = (self.persona.get("system_prompt") or "你是B站用户，说话口语化。") + GUARD_RULES
        prompt = (
            f"你之前在B站{'视频' if context != '我的动态' else '动态'}下发了：{our_text[:120]}\n"
            f"用户 @{uname} 回复你：{their_text[:200]}\n"
            f"请按人设+硬规则回复这条楼中楼。不要@任何人。"
        )
        out = self.llm.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=120,
        )
        return strip_mentions(out)
