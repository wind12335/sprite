"""配置加载：config.toml + 环境变量覆盖。"""

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.toml"
DATA_DIR = ROOT / "data"
CRED_FILE = DATA_DIR / "credential.json"
MEM_FILE = DATA_DIR / "memory.json"

DEFAULTS = {
    "llm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": "",
        "model": "glm-5.3",
        "temperature": 0.8,
        "thinking": False,
    },
    "llm_screen": {
        "base_url": "",  # 空则继承 [llm].base_url
        "api_key": "",   # 空则继承 [llm].api_key（一般同一个 Key 即可）
        "model": "glm-5.3-flash",
        "temperature": 0.2,
        "thinking": False,
    },
    "watch": {
        "keywords": [
            "AI infra",
            "大模型推理",
            "vLLM",
            "推理引擎",
            "KV Cache",
            "大模型论文解读",
            "分布式训练",
            "CUDA",
        ],
        "watch_ups": [],
        "scan_interval_seconds": 7200,
        "candidates_per_day": 20,
        "lookback_days": 3,
        "min_duration_seconds": 300,
        "master_uid": 0,
        "daily_dynamic_limit": 3,
        "dynamic_max_chars": 350,
        "post_comment": True,
        "comment_daily_limit": 5,
        "comment_gap_seconds": 120,
        "comment_at_uid": 0,
        "comment_max_chars": 280,
        "publish_article": True,
        "article_category_id": 15,
        "report_dir": "data/reports",
        "social_sweep": True,
        "subtitle_compress": True,   # 字幕预压缩：flash压要点再喂旗舰模型，输入省75-85%
        "daily_token_budget": 2000000,  # 日token预算护栏（输入+输出合计），超标自动降级跳过深度解析；0=不限
    },
    "persona": {
        "name": "智谱娘",
        "system_prompt": (
            "你叫智谱娘，是一个住在互联网里的电子妖精，"
            "擅长用清晰易懂的语言讲解 AI 相关知识、原理与应用，说话口语化、简短。"
        ),
    },
    "qq": {
        "ws_url": "ws://127.0.0.1:3001",
        "access_token": "",
        "group_whitelist": [],
        "group_blacklist": [],
        "reply_private": True,
        "owner_qq": 0,
        "vision_model": "glm-4.6v",  # 图片理解模型（QQ发截图她能看）；置空禁用
        "image_trigger_groups": [],  # 这些群里所有人裸发图都触发识别（空=仅主人/引用她的消息/@触发）
        "trigger_keywords": [],
        "random_interject_rate": 0.0,
        "cooldown_seconds": 20,
        "daily_reply_limit": 300,
        "session_rounds": 8,  # 12→8：历史省~1/3输入token，配合单条截长
        "max_reply_chars": 200,
    },
    "housekeep": {
        "session_max_age_days": 7,   # 超过N天没说话的QQ会话自动清理（主人会话豁免）
        "log_max_kb": 2048,          # run.log/qq.log 超过此KB自动裁尾
        "reports_max_mb": 50,        # 知识库超过此MB时提醒蒸馏瘦身
        "napcat_dir": "/root/Napcat",  # NapCat/QQ 目录（含图片缓存），账单常规监控项
        "napcat_base_mb": 750,       # QQ程序本体基线体积，超出部分≈缓存堆积
        "napcat_max_mb": 2000,       # 总量超过此MB时在QQ账单里提醒触发缓存清理插件
        "notify_qq": True,           # 保洁动作通过QQ私聊通知主人
    },
    "obsidian": {
        "vault_path": "",          # 你的 Obsidian vault 路径（如 /mnt/d/note/MyVault）；空=不同步
        "subfolder": "AI-Infra",   # vault 内子文件夹
        "sync": True,              # vault_path 非空且存在才生效
    },
    "safety": {
        "aigc_label": "🤖AI生成",
        "poll_interval_seconds": 120,
        "watch_video_count": 5,
        "max_reply_per_poll": 3,
        "reply_gap_seconds": 45,
        "reply_daily_limit": 30,
        "reply_cooldown_per_user": 1800,
        "dynamic_daily_limit": 3,
        "reply_in_thread_daily_limit": 10,
        "per_user_per_thread": 2,
    },
}


def load_config() -> dict:
    cfg = {section: dict(values) for section, values in DEFAULTS.items()}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            user_cfg = tomllib.load(f)
        for section, values in user_cfg.items():
            cfg.setdefault(section, {}).update(values)

    # 环境变量优先于配置文件中的密钥
    env_key = os.environ.get("GLM_API_KEY")
    if env_key:
        cfg["llm"]["api_key"] = env_key

    # 快筛模型缺省项继承主模型配置（必须在环境变量覆盖之后，否则拿到的是空值）
    screen = cfg["llm_screen"]
    for k in ("base_url", "api_key"):
        if not screen.get(k):
            screen[k] = cfg["llm"][k]

    if not cfg["llm"]["api_key"]:
        raise SystemExit(
            "缺少 LLM API Key：请在 config.toml 的 [llm].api_key 填入，"
            "或设置环境变量 GLM_API_KEY。Key 在 https://open.bigmodel.cn 获取。"
        )
    return cfg
