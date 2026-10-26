"""大脑：人设、生成评论回复 / 动态文案，以及一个小型 JSON 记忆体（去重 + 限频 + 日配额）。"""

import json
import time
from datetime import date
from pathlib import Path

from ..config import MEM_FILE
from ..llm import LLM


class Memory:
    """data/memory.json：已处理评论、每用户冷却、每日计数器。崩溃安全靠整体覆写。"""

    def __init__(self, path: Path = MEM_FILE):
        self.path = path
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
        else:
            d = {}
        self.seen: dict[str, int] = d.get("seen", {})          # rpid -> 处理时间戳
        self.user_last: dict[str, float] = d.get("user_last", {})  # mid -> 上次回复时间戳
        counters = d.get("counters", {})
        if counters.get("date") != str(date.today()):
            counters = {"date": str(date.today()), "reply": 0, "dynamic": 0}
        self.counters: dict = counters

    def is_seen(self, key) -> bool:
        return str(key) in self.seen

    def mark_seen(self, key) -> None:
        self.seen[str(key)] = int(time.time())
        # 防止无限膨胀：只留最近 5000 条
        if len(self.seen) > 5000:
            keep = sorted(self.seen.items(), key=lambda kv: kv[1])[-3000:]
            self.seen = dict(keep)
        self.save()

    def user_cooldown_ok(self, mid, cooldown: int) -> bool:
        last = self.user_last.get(str(mid), 0)
        return (time.time() - last) >= cooldown

    def touch_user(self, mid) -> None:
        self.user_last[str(mid)] = time.time()
        self.save()

    def quota_ok(self, action: str, limit: int) -> bool:
        return self.counters.get(action, 0) < limit

    def bump(self, action: str) -> None:
        self.counters[action] = self.counters.get(action, 0) + 1
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"seen": self.seen, "user_last": self.user_last, "counters": self.counters},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class Brain:
    def __init__(self, llm: LLM, persona_name: str, system_prompt: str):
        self.llm = llm
        self.persona_name = persona_name
        self.system_prompt = system_prompt

    def _chat(self, user_prompt: str, max_tokens: int = 200) -> str:
        return self.llm.chat(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
        )

    def reply_comment(self, uname: str, text: str) -> str:
        from ..logutil import strip_mentions

        out = self._chat(
            f"B站用户「{uname}」在你的视频下评论：\n{text}\n\n"
            f"请以「{self.persona_name}」的身份回复这条评论。保持简短口语化，不要解释你是AI设定。不要@任何人。"
        )
        return strip_mentions(out)

    def write_dynamic(self, topic_hint: str) -> str:
        return self._chat(
            f"请以「{self.persona_name}」的身份写一条即将发布的B站动态，主题/日常碎碎念方向：{topic_hint}。"
            f"一两句话即可，口语化，不要加话题标签和表情指令。",
            max_tokens=150,
        )
