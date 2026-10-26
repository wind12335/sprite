"""日志与运行时工具：时间戳日志、token 记账、进程锁。"""

import builtins
import datetime
import fcntl
import json
import os
import sys
from pathlib import Path

from .config import DATA_DIR

USAGE_FILE = DATA_DIR / "token_usage.json"
LOCK_FILE = DATA_DIR / "sprite.lock"

_orig_print = builtins.print


def install_timestamped_print() -> None:
    """给所有 print 加 [时间] 前缀（含第三方库输出）；FISHBOT_RAW_LOG=1 可关闭。"""
    if os.environ.get("FISHBOT_RAW_LOG"):
        return

    def _print(*args, **kwargs):
        ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
        _orig_print(f"[{ts}]", *args, **kwargs)

    builtins.print = _print


# ---------- token 记账 ----------

def track_usage(model: str, usage: dict) -> None:
    """记录一次 LLM 调用的 token 消耗，按天/按模型累计到 data/token_usage.json。"""
    if not isinstance(usage, dict):
        return
    today = str(datetime.date.today())
    data = {}
    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    if data.get("date") != today:
        data = {"date": today, "models": {}}
    m = data["models"].setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    m["calls"] += 1
    m["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    m["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def today_usage() -> dict:
    if not USAGE_FILE.exists():
        return {"date": str(datetime.date.today()), "models": {}}
    try:
        d = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        return d if d.get("date") == str(datetime.date.today()) else {"date": str(datetime.date.today()), "models": {}}
    except Exception:
        return {"date": str(datetime.date.today()), "models": {}}


def usage_summary() -> str:
    d = today_usage()
    if not d["models"]:
        return "今日 token 消耗：暂无记录"
    parts = [
        f"{m}：{v['calls']}次 输入{v['prompt_tokens']:,} 输出{v['completion_tokens']:,}"
        for m, v in d["models"].items()
    ]
    return "今日 token 消耗 → " + "  |  ".join(parts)


# ---------- 文本清洗 ----------

import re as _re

_AT_PATTERN = _re.compile(r"(?<![\w@.])@[0-9A-Za-z_\u4e00-\u9fff\-·]+")


def strip_mentions(text: str) -> str:
    """剥掉 LLM 正文里自行写入的 @昵称（防止误@UP主/陌生人触发通知）。

    发送方拼接的受控@前缀应在清洗之后再拼。
    """
    cleaned = _AT_PATTERN.sub("", text)
    return _re.sub(r"\s{2,}", " ", cleaned).strip()


# ---------- 进程锁 ----------

_lock_handle = None  # 必须持有文件对象引用：fd 关闭（含GC回收）的瞬间锁就释放


def acquire_lock(name: str = "sprite") -> None:
    """独占文件锁：防止双开导致重复发布。不同入口可用不同 name 互不阻塞。"""
    global _lock_handle
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_FILE if name == "sprite" else DATA_DIR / f"{name}.lock"
    f = open(lock_path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        _orig_print(f"⛔ 已有 {name} 实例在运行（{lock_path.name} 被占用）。")
        _orig_print("   若确认没有实例在跑，可删除该文件后重试。")
        sys.exit(1)
    f.write(f"{os.getpid()} {datetime.datetime.now().isoformat()}\n")
    f.flush()
    _lock_handle = f  # 模块级引用保活，进程退出时自动释放锁
