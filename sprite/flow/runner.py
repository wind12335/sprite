"""评论自动回复主循环：盯自己最近几个视频的评论区，LLM 生成回复后回复根评论。"""

import asyncio

from ..bili.account import Bili, _rpid
from ..mind.brain import Memory


def _label(text: str, aigc_label: str) -> str:
    return f"{text} {aigc_label}" if aigc_label else text


async def reply_loop(bili: Bili, brain: Brain, mem: Memory, safety: dict) -> None:
    info = await bili.my_info()
    my_mid = str(info["mid"])
    print(f"以「{info['name']}」(mid={my_mid}) 身份运行，Ctrl+C 退出。")

    label = safety["aigc_label"]
    while True:
        try:
            videos = await bili.my_videos(safety["watch_video_count"])
            print(f"本轮扫描 {len(videos)} 个视频的评论区…")

            for v in videos:
                aid = v["aid"]
                data = await bili.video_comments(aid)
                # 只处理根评论；楼中楼留在 c["replies"] 里，需要的话再扩展
                candidates = []
                for c in data.get("replies") or []:
                    rpid = _rpid(c)
                    mid = str(c["member"]["mid"])
                    if mem.is_seen(rpid):
                        continue
                    if mid == my_mid:  # 自己的评论直接标记，避免反复扫描
                        mem.mark_seen(rpid)
                        continue
                    candidates.append((rpid, mid, c))

                # 先回复最早的，新来的下一轮再说，行为更像真人
                candidates.sort(key=lambda x: x[2].get("ctime", 0))
                sent_this_video = 0

                for rpid, mid, c in candidates:
                    if sent_this_video >= safety["max_reply_per_poll"]:
                        break
                    if not mem.quota_ok("reply", safety["reply_daily_limit"]):
                        print("今日回复配额已用完，明天见。")
                        return
                    if not mem.user_cooldown_ok(mid, safety["reply_cooldown_per_user"]):
                        continue  # 该用户冷却中，先不回也不标记，下轮再试

                    text = (c.get("content") or {}).get("message", "").strip()
                    if not text:
                        mem.mark_seen(rpid)
                        continue

                    reply = brain.reply_comment(c["member"]["uname"], text)
                    await bili.reply_comment(aid, rpid, _label(reply, label))
                    mem.mark_seen(rpid)
                    mem.touch_user(mid)
                    mem.bump("reply")
                    sent_this_video += 1
                    print(f"  回复 @{c['member']['uname']} 于《{v['title']}》: {reply[:50]}…")
                    await asyncio.sleep(safety["reply_gap_seconds"])

            await asyncio.sleep(safety["poll_interval_seconds"])
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"本轮出错（{e}），{safety['poll_interval_seconds']}s 后重试…")
            await asyncio.sleep(safety["poll_interval_seconds"])
