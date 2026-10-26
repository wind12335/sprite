"""自动保洁：旧会话清理、日志裁尾、知识库体量监控，动作结果通过 QQ 通知主人。

设计：会话记忆本来就是短期记忆（每会话12轮），长期"核心知识"住在 data/reports 知识库
（有 distill 做语义压缩）——所以旧会话直接删，不做 LLM 摘要（省 token，架构上也更干净）。
"""

import datetime
import json
import time
from pathlib import Path

from .config import DATA_DIR, ROOT

SESSION_FILE = DATA_DIR / "qq_sessions.json"
PROFILE_FILE = DATA_DIR / "qq_profiles.json"  # MemGPT式归档：旧会话的档案摘要，聊时recall
STAMP_FILE = DATA_DIR / "housekeep.date"
LOG_FILES = [ROOT / "run.log", ROOT / "qq.log"]


def _mb(p: Path) -> float:
    try:
        return p.stat().st_size / 1024 / 1024
    except OSError:
        return 0.0


def dir_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1024 / 1024


def prune_sessions(max_age_days: int, owner_qq: str = "", llm=None) -> tuple[int, int]:
    """删除超过 N 天没说话的会话（主人会话豁免）。

    MemGPT式归档：删除前若有 llm，用 flash 把会话蒸成一句档案摘要存入
    qq_profiles.json（该会话再聊时会被 recall 注入上下文——删了记录但记得印象）。
    返回 (删掉的会话数, 释放KB)。
    """
    if not SESSION_FILE.exists():
        return 0, 0
    d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    cutoff = time.time() - max_age_days * 86400
    keep, removed, freed = {}, 0, 0
    owner_key = f"u{owner_qq}" if owner_qq else ""
    profiles = json.loads(PROFILE_FILE.read_text(encoding="utf-8")) if PROFILE_FILE.exists() else {}
    for key, msgs in d.get("sessions", {}).items():
        last_ts = msgs[-1].get("ts", 0) if msgs else 0
        if last_ts >= cutoff or key == owner_key:
            keep[key] = msgs
            continue
        removed += 1
        freed += len(json.dumps(msgs, ensure_ascii=False))
        if llm is not None:
            try:
                transcript = "\n".join(f"{m['role']}: {m['content'][:80]}" for m in msgs[-12:])
                summary = llm.chat(
                    [{"role": "user", "content":
                      f"把这段QQ对话压缩成一句'档案摘要'（谁、聊了什么主题、有什么结论/偏好，≤60字）：\n{transcript}"}],
                    max_tokens=100,
                )
                profiles[key] = {"summary": summary, "archived": str(datetime.date.today())}
            except Exception:
                pass  # 摘要失败不阻塞清理
    d["sessions"] = keep
    SESSION_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    if profiles:
        PROFILE_FILE.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed, freed // 1024


def load_profiles() -> dict:
    if PROFILE_FILE.exists():
        try:
            return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def rotate_logs(max_kb: int) -> list[str]:
    """日志裁尾：只保留最后 max_kb，返回处理说明。"""
    actions = []
    for log in LOG_FILES:
        if not log.exists():
            continue
        size_kb = log.stat().st_size // 1024
        if size_kb <= max_kb:
            continue
        text = log.read_text(encoding="utf-8", errors="ignore")
        tail = text[-(max_kb * 1024):]
        log.write_text(f"[housekeep 裁尾于 {datetime.datetime.now():%m-%d %H:%M}，原{size_kb}KB]\n" + tail,
                       encoding="utf-8")
        actions.append(f"{log.name} {size_kb}KB→{max_kb}KB")
    return actions


def notify_owner(text: str, qq_cfg: dict) -> bool:
    """通过 OneBot 给主人发 QQ 私聊通知（NapCat 不在线则静默失败）。"""
    owner = qq_cfg.get("owner_qq")
    if not owner:
        return False

    async def _send() -> None:
        from .qq.onebot import OneBotClient

        client = OneBotClient(qq_cfg.get("ws_url", ""), None,
                              access_token=qq_cfg.get("access_token", ""))
        import asyncio

        async def _no_op(ev):
            return None

        client.on_message = _no_op
        async with __import__("websockets").connect(
            client.ws_url,
            additional_headers={"Authorization": f"Bearer {client.access_token}"} if client.access_token else {},
            ping_interval=15,
        ) as ws:
            client._ws = ws
            await client.send_private_text(int(owner), text)

    try:
        import asyncio

        asyncio.run(_send())
        return True
    except Exception:
        return False  # NapCat 没开/没登录，静默


def dir_size_mb(path: str) -> float | None:
    """测量目录体积（MB）；需要 root 权限时借道免密 sudo，失败返回 None。"""
    import subprocess

    for cmd in (["du", "-sm", path], ["sudo", "-n", "du", "-sm", path]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if out.returncode == 0 and out.stdout.strip():
                return float(out.stdout.split()[0])
        except Exception:
            continue
    return None


def run_housekeep(hk_cfg: dict, qq_cfg: dict, llm=None, notify: bool = True) -> str:
    """执行一轮保洁，返回人类可读的账单。"""
    lines, did_something = [], False

    removed, freed = prune_sessions(
        int(hk_cfg.get("session_max_age_days", 7)), str(qq_cfg.get("owner_qq") or ""), llm=llm
    )
    if removed:
        did_something = True
        lines.append(f"清理了 {removed} 个超过{hk_cfg.get('session_max_age_days', 7)}天的旧会话（释放{freed}KB，主人会话保留）")

    for a in rotate_logs(int(hk_cfg.get("log_max_kb", 2048))):
        did_something = True
        lines.append(f"日志裁尾：{a}")

    # 知识库 + 字幕库：账单常规项 + 超阈值提醒蒸馏
    reports = ROOT / hk_cfg.get("report_dir", "data/reports")
    kb_mb = dir_mb(reports) if reports.exists() else 0.0
    subs = ROOT / "data" / "subtitles"
    sub_mb = dir_mb(subs) if subs.exists() else 0.0
    limit = float(hk_cfg.get("reports_max_mb", 50))
    if kb_mb > limit:
        did_something = True
        lines.append(
            f"⚠️ 知识库已达 {kb_mb:.1f}MB（阈值{limit:.0f}MB）。建议蒸馏瘦身："
            f"python main.py distill \"<主题>\" --prune"
        )
    else:
        lines.append(f"知识库 {kb_mb:.1f}MB + 字幕原文 {sub_mb:.1f}MB（审计链，正常）")

    # NapCat/QQ 目录（程序本体+图片缓存）：账单常规项 + 超阈值提醒清缓存
    napcat_dir = hk_cfg.get("napcat_dir", "/root/Napcat")
    nap_mb = dir_size_mb(napcat_dir)
    if nap_mb is not None:
        nap_limit = float(hk_cfg.get("napcat_max_mb", 2000))
        base_mb = float(hk_cfg.get("napcat_base_mb", 750))  # QQ程序本体基线，超过部分≈缓存堆积
        if nap_mb > nap_limit:
            did_something = True
            lines.append(
                f"⚠️ NapCat/QQ 目录 {nap_mb:.0f}MB 超过阈值{nap_limit:.0f}MB"
                f"（较程序基线多出约{nap_mb - base_mb:.0f}MB，多为图片缓存）→ 该触发缓存清理插件了"
            )
        else:
            lines.append(f"NapCat/QQ 目录 {nap_mb:.0f}MB（正常，含程序本体~{base_mb:.0f}MB）")

    if not did_something:
        lines.append("会话/日志无需清理")

    bill = "🧹 sprite 自动保洁报告：\n" + "\n".join(lines)
    print(bill)
    if notify and hk_cfg.get("notify_qq", True):
        ok = notify_owner(bill, qq_cfg)
        print(f"QQ通知主人：{'已发送' if ok else '未发送（NapCat离线）'}")
    return bill


def maybe_daily_housekeep(hk_cfg: dict, qq_cfg: dict, llm=None) -> None:
    """每天最多自动执行一次（手动 housekeep 命令不受此限）。"""
    today = str(datetime.date.today())
    if STAMP_FILE.exists() and STAMP_FILE.read_text(encoding="utf-8").strip() == today:
        return
    run_housekeep(hk_cfg, qq_cfg, llm=llm)
    STAMP_FILE.write_text(today, encoding="utf-8")
