# sprite 命令与参数手册

> LLM 驱动（任意 OpenAI 兼容模型）的 B 站账号机器人：视频情报流水线 / 评论自动回复 / 知识库与自我进化。
> 本文档是全量命令与参数参考；概览、部署与风险说明见 [README.md](README.md)。

## 目录

- [快速开始](#快速开始)
- [CLI 命令总表](#cli-命令总表)
- [命令详解](#命令详解)
- [config.toml 参数手册](#configtoml-参数手册)
- [环境变量](#环境变量)
- [数据文件](#数据文件)
- [常驻运行](#常驻运行)
- [开源前自检清单](#开源前自检清单)

## 快速开始

```bash
git clone <repo> && cd sprite
python3 -m venv .venv && source .venv/bin/activate   # 需 Python ≥ 3.11
pip install -r requirements.txt
cp config.example.toml config.toml                    # 编辑：填 API Key、你的B站UID
export GLM_API_KEY="..."                              # 或用环境变量代替落盘
python main.py login                                  # B站App扫码
python main.py watch --once --dry-run                 # 预演一轮，看将发布什么
python main.py watch                                  # 正式运行
```

## CLI 命令总表

| 命令 | 作用 | 关键参数 | 发布行为 |
|---|---|---|---|
| `login` | 扫码登录，凭据存 `data/credential.json` | — | 无 |
| `ingest` | 情报摄取：arXiv关键词+HF日报→PDF入`data/papers`；HF博客→资讯报告；`--absorb`直接解析入库 | 网络只读 | 低（无LLM；`--absorb`按论文数） |
| `gossip` | 花边播报预览：HN/Reddit/HF素材→人设播报（QQ端触发词同款） | 网络只读 | 一次问答 |
| `search` | 联网搜索测试（搜狗+必应双引擎，与QQ端自动搜索同款） | 网络只读 | 无 |
| `status` | 查看登录身份与最近投稿 | — | 无 |
| `watch` | 视频情报流水线（核心） | `--once` `--dry-run` | 动态@我 / 视频下评论 / 专栏(实验) |
| `ask` | 查询知识库（视频解析笔记） | `<query>` | 无 |
| `distill` | 蒸馏：多份笔记 → 一篇主题综述 | `<topic>` `--prune` `--max-files N` | 无（只写本地） |
| `reply-loop` | 自动回复**自己视频**下的评论 | — | 评论 |
| `dynamic` | 发动态（手写或按人设生成） | `--text` `--llm` `--topic` `--yes` | 动态 |
| `video` | 本地视频分片投稿 | `<path>` `--title` `--tags` … | 视频 |
| `social` | 单独触发社交清扫：回复楼中楼/动态评论 | `--dry-run` | 楼中楼/动态评论 |
| `housekeep` | 手动保洁：旧会话归档清理/日志裁尾/体量检查 | `--no-notify` | 无（QQ账单） |
| `qq` | 智谱娘上QQ：连 NapCat 常驻（独立锁，不影响B站侧） | — | QQ群/私聊 |
| `usage` | 查看今日 token 消耗（按模型） | — | 无 |

> 🔒 **进程锁**：除 `status`/`ask`/`usage` 外的命令启动时独占 `data/sprite.lock`，防止 cron 单轮与常驻进程双开导致重复发布。提示"已有实例在运行"时，确认无进程后删除该文件即可。
>
> 💬 **社交闭环**（watch 内建，`social_sweep = true` 开启）：每轮扫描尾部检查"我方评论的楼中楼"和"我方动态的评论区"，有人回复则以人设回应。防杠护栏：生成端硬规则（不接杠、不谈敏感话题、≤60字）+ 每人每线程最多回 2 次（第 2 次固定礼貌收尾话术）+ 每日总量 `reply_in_thread_daily_limit`。
>
> 👑 **主人口令**（仅 owner_qq 可用，群里不用@，免LLM免冷却）：`/状态`（系统概况）`/token`（今日消耗）`/闭嘴`（暂停回复他人，主人不受影响，持久化）`/回来` `/重置记忆`（清空当前会话上下文）`/帮助`
>
> 🖼 **图片理解**（`vision_model`）：私聊直接发图、群里 @她+图 / 引用她的消息+图 / 先发图再@她（自动回捞10分钟内最近一张群图）/ 主人裸发图 / `image_trigger_groups` 指定群全员。两步链路：glm-4.6v 看图转写 → 主脑人设回复。引用回复时被引原文自动注入上下文。

## 命令详解

### `login` — 扫码登录

```bash
python main.py login
```

终端渲染紧凑二维码（约 1/4 屏），B 站 App 扫码确认。走 B 站 passport 官方 ticket 流程，凭据（SESSDATA/bili_jct/DedeUserID/buvid3/refresh_token/全量cookie）自动保存到 `data/credential.json`。同目录会留一张 `data/qrcode.png` 备扫。

### `status` — 身份确认

```bash
python main.py status
```

显示登录账号昵称、mid、等级和最近 5 条投稿。用来验证凭据是否仍有效。

### `watch` — 视频情报流水线（核心命令）

```bash
python main.py watch              # 常驻：每 scan_interval_seconds 扫一轮（带随机抖动）
python main.py watch --once       # 单轮后退出，适合 cron
python main.py watch --dry-run    # 预演：完整走搜索/字幕/两级LLM，但只打印将发布的内容
python main.py watch --once --dry-run   # 调试人设/提示词时的标准姿势
```

| 参数 | 说明 |
|---|---|
| `--once` | 只扫一轮。cron 定时场景用这个，进程不常驻 |
| `--dry-run` | 不真发布评论/动态/专栏，其余全流程照跑（LLM 消耗照常） |

每轮流水线：关键词搜索 + 指定UP主投稿 → 合并去重（时长≥`min_duration_seconds`、发布时间≤`lookback_days`）→ 每天最多 `candidates_per_day` 个 → 拉 CC 字幕（无字幕跳过）→ `glm-5.3-flash` 快筛（卖课/资讯一律拦）→ 相关的用 `glm-5.3` 深度解析（自动注入同主题旧笔记做跨视频关联）→ 报告落盘 + 专栏(实验) + 动态@你 + 视频下人设总结评论（自动注入旧评论防重复）。

内建保护：启动凭据校验、所有间隔 ±40% 随机抖动、评论连续失败 2 次熔断本轮、动态/评论/候选三重日配额。

### `ask` — 知识库问答

```bash
python main.py ask "vLLM 的 PD 分离是怎么回事"
python main.py ask "Triton 的 autotune 怎么调"
```

检索 `data/reports/`（含综述）top-5 笔记块 → glm-5.3 基于笔记归纳回答，注明来源视频；笔记没覆盖的直说"没有"，不编造。

### `distill` — 蒸馏综述（知识库自我进化）

```bash
python main.py distill "Triton"                # 只生成综述
python main.py distill "Triton" --prune        # 生成后把被吸收的原文移入 archive/
python main.py distill "vLLM" --max-files 20   # 放宽纳入的笔记数（默认10）
```

| 参数 | 说明 |
|---|---|
| `<topic>` | 主题词，按知识库检索匹配源笔记（综述文件不会被二次蒸馏） |
| `--prune` | 原文移入 `data/reports/archive/`——索引立即瘦身（检索不扫归档），原文保留；确认综述质量后 `rm -rf data/reports/archive/` 彻底删除 |
| `--max-files N` | 最多纳入的相关笔记份数，默认 10 |

建议节奏：某主题笔记 ≥5 份、或评论出现"原地踏步"时蒸馏一次。综述会自动进入索引，成为后续评论/解析/ask 的"教科书"。

### `reply-loop` — 自己视频的评论自动回复

```bash
python main.py reply-loop
```

人设（`[persona]`）口吻自动回复**自己账号**视频下的新评论。内建：去重、同用户冷却、每日配额、每条间隔。频率参数在 `[safety]` 调。

### `dynamic` — 发动态

```bash
python main.py dynamic --text "手动发一条"             # 发布前需确认（y/N）
python main.py dynamic --llm --topic "今天摸鱼"        # 人设生成，同样需确认
python main.py dynamic --text "..." --yes              # 跳过确认（脚本/cron 用）
```

> 无论 `--text` 还是 `--llm`，默认都会展示内容并要求确认，`--yes` 才跳过。

### `video` — 投稿视频

```bash
python main.py video out/demo.mp4 \
  --title "标题（≤80字）" \
  --tags "AI,日常" \
  --desc "简介" \
  --tid 21 \
  --cover cover.jpg
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `path` | ✅ | 本地视频文件路径 |
| `--title` | ✅ | 标题，≤80 字 |
| `--tags` | ✅ | 逗号分隔，1~10 个 |
| `--desc` | | 简介 |
| `--tid` | | 分区 ID，默认 21（日常）；B 站投稿页可查全部分区 |
| `--cover` | | 封面图片路径 |

### `qq` — 智谱娘上 QQ（OneBot v11 / NapCat）

```bash
# 一次性部署 NapCat（QQ小号登录，WebUI: http://localhost:6099 扫码）：
docker run -d --name napcat -p 6099:6099 -p 3001:3001 mlikiowa/napcat-docker:latest
# WebUI → 网络配置 → 新建 → 「WebSocket 服务器」，端口 3001

python main.py qq        # 常驻：断线自动重连，Ctrl+C 退出
```

<details>
<summary>本机已用 Shell 方式部署（免 Docker），运维命令如下</summary>

```bash
# 启动（WSL 重启后需要重新执行）
sudo screen -dmS napcat xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox
# 查看控制台（Ctrl+A 再按 D 退出查看）
sudo screen -r napcat
# 停止
sudo screen -S napcat -X quit
# WebUI token 忘了时
sudo cat /root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json
```
</details>

行为：群里 **@她** 或 **说人设名/触发词** 才回（回复带@发送者）；私聊直接回。回复走智谱娘人设 + **知识库 RAG**（问题命中视频笔记时注入参考）+ 每会话 12 轮上下文记忆。保护：同会话 20s 冷却、每日 300 条上限、群黑白名单。

### `[qq]` 参数

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ws_url` | str | `ws://127.0.0.1:3001` | NapCat 正向 WS 地址 |
| `access_token` | str | 空 | NapCat 设了 accessToken 才填 |
| `group_whitelist` | [int] | `[]` | 允许的群号；空=全部（黑名单优先） |
| `group_blacklist` | [int] | `[]` | 拒绝的群号 |
| `reply_private` | bool | true | 是否回应私聊 |
| `owner_qq` | int | 0 | ★主人QQ号：人设认知（对话标注"主人"、更亲近）、私聊免冷却 |
| `trigger_keywords` | [str] | `[]` | 群里说这些词也触发（人设名与QQ昵称始终触发） |
| `random_interject_rate` | float | 0.0 | 群聊随机插话概率，默认关 |
| `cooldown_seconds` | int | 20 | 同会话两次回复最小间隔 |
| `daily_reply_limit` | int | 300 | 每日回复总量（token 保护） |
| `session_rounds` | int | 12 | 每会话保留上下文轮数 |
| `max_reply_chars` | int | 200 | 单条回复长度上限 |

### `[housekeep]` — 自动保洁（watch 每天自动跑一次，也可 `python main.py housekeep` 手动）



| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `session_max_age_days` | int | 7 | 超过N天没说话的QQ会话自动清理；**清理前用 flash 蒸成档案摘要**存 `data/qq_profiles.json`（该会话再聊时自动"召回"注入印象——删了记录但记得聊过什么，MemGPT 式归档）。主人会话豁免 |
| `log_max_kb` | int | 2048 | `run.log`/`qq.log` 超过此KB自动裁尾 |
| `reports_max_mb` | int | 50 | 知识库超过此MB时在QQ上提醒主人跑 `distill --prune` 瘦身 |
| `napcat_dir` | str | `/root/Napcat` | NapCat/QQ 目录（含图片缓存），每日账单常规监控项（root 目录用免密 sudo 测量） |
| `napcat_base_mb` / `napcat_max_mb` | int | 750 / 2000 | QQ 程序本体基线与总量阈值：总量超阈值时账单提醒"该触发缓存清理插件了"（与 NapCat 缓存清理插件联动） |
| `notify_qq` | bool | true | 保洁账单（做了什么/释放多少）通过QQ私聊发给主人 |

> QQ 侧与 B 站侧完全隔离：独立进程锁（`data/qq.lock`）、独立会话数据（`data/qq_sessions.json`）、独立配额；仅共享人设配置、知识库（只读）与 GLM 额度（token 记账合并显示在 `usage`）。
> ⚠️ 协议端方案（NapCat 挂真实QQ号）违反腾讯用户协议，有封号风险，务必用小号。

## config.toml 参数手册


完整模板见 [`config.example.toml`](config.example.toml)。未写的键取内置默认值（下表"默认"列）。

### `[llm]` — 深度模型（解析/评论/问答/蒸馏）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `base_url` | str | `https://open.bigmodel.cn/api/paas/v4` | 普通 Key 用默认；**GLM Coding Plan 的 Key 必须改** `https://open.bigmodel.cn/api/coding/paas/v4` |
| `api_key` | str | 空 | 智谱 API Key；建议用环境变量 `GLM_API_KEY` 代替落盘 |
| `model` | str | `glm-5.3` | 旗舰模型；省钱可换 `glm-5.3-flash` 等 |
| `temperature` | float | 0.8 | 生成温度 |
| `thinking` | bool | false | 深度思考开关。闲聊/评论关（快且省 token），要更深的解析可开 |

### `[llm_screen]` — 快筛模型（第一级）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `base_url` | str | 空 | 空则继承 `[llm].base_url` |
| `api_key` | str | 空 | 空则继承 `[llm].api_key` |
| `model` | str | `glm-5.3-flash` | 长上下文轻量模型，吃整篇字幕做相关性判断 |
| `temperature` | float | 0.2 | 筛选任务要稳定，低温 |
| `thinking` | bool | false | 快筛不需要思考 |

### `[watch]` — 视频情报流水线

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keywords` | [str] | 8 个基础词 | 按最新发布搜索，多词合并去重；置 `[]` 纯靠 UP 供料 |
| `watch_ups` | [int] | `[]` | 盯指定 UP 的 B 站 UID（主页地址栏 `space.bilibili.com/<UID>`） |
| `scan_interval_seconds` | int | 7200 | 常驻模式扫描间隔（实际带 ±40% 抖动） |
| `candidates_per_day` | int | 20 | 每天最多处理的新视频数 |
| `lookback_days` | int | 3 | 只看最近 N 天发布的 |
| `min_duration_seconds` | int | 300 | 短于此的跳过 |
| `master_uid` | int | 0 | ★**动态 @ 的账号** UID（收通知的主人）；0 = 不发动态@ |
| `daily_dynamic_limit` | int | 3 | 每天最多发几条动态 |
| `dynamic_max_chars` | int | 350 | 动态正文字数上限 |
| `post_comment` | bool | true | 是否在原视频下发总结评论 |
| `comment_at_uid` | int | 0 | ★**评论里 @ 的账号** UID（与 master_uid 可不同）；0 = 不 @。@ 用 `用户名+零宽空格` 格式以触发对方"提到我的"通知 |
| `comment_daily_limit` | int | 5 | 每天最多评论数 |
| `comment_gap_seconds` | int | 120 | 两条评论最小间隔（带抖动） |
| `comment_max_chars` | int | 280 | 单条评论长度上限（B 站上限 1000，宜短） |
| `publish_article` | bool | true | 尝试把完整解析发成 B 站专栏（实验性，失败自动只存本地） |
| `article_category_id` | int | 15 | 专栏分区，15=科技 |
| `report_dir` | str | `data/reports` | 深度解析/综述的 markdown 落地目录（= 知识库） |

### `[persona]` — 人设（评论/动态/reply-loop 的口吻）

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | 智谱娘 | 人设名 |
| `system_prompt` | str | (内置) | 人设 system prompt：身份+说话风格+场景要求。直接决定评论像不像真人 |

### `[safety]` — 风控与合规

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `aigc_label` | str | `🤖AI生成` | 每条评论/动态末尾追加的显式标识；**置空 = 不加**（按 2025-09 施行的《人工智能生成合成内容标识办法》，AI 内容需显式标识，去掉有合规风险，自行权衡） |
| `poll_interval_seconds` | int | 120 | reply-loop 轮询间隔 |
| `watch_video_count` | int | 5 | reply-loop 只盯最近 N 个视频的评论区 |
| `max_reply_per_poll` | int | 3 | 每轮最多回复几条 |
| `reply_gap_seconds` | int | 45 | 两条回复间隔 |
| `reply_daily_limit` | int | 30 | 每日回复上限 |
| `reply_cooldown_per_user` | int | 1800 | 同一用户两次被回复的最小间隔（秒） |
| `dynamic_daily_limit` | int | 3 | `dynamic` 命令每日上限 |
| `reply_in_thread_daily_limit` | int | 10 | 社交清扫每日回复总量（楼中楼+动态评论） |
| `per_user_per_thread` | int | 2 | 同一人同一条评论/动态下最多被回复几次（第 2 次走固定收尾话术） |

## 环境变量

| 变量 | 作用 |
|---|---|
| `GLM_API_KEY` | 覆盖 `config.toml` 的 `[llm].api_key`（优先级更高，推荐用法，避免 Key 落盘） |

## 数据文件

| 路径 | 生成者 | 内容 | 敏感性 |
|---|---|---|---|
| `data/credential.json` | `login` | B 站登录凭据（等同账号密码） | 🔴 绝不外传/提交 |
| `config.toml` | 用户 | 含 API Key | 🔴 已在 .gitignore |
| `data/memory.json` | 各流水线 | 去重记录 + 每日配额计数 | 🟡 可删（删后当天可能重复处理） |
| `data/comment_history.json` | `watch` | 全部已发评论（防重复语料） | 🟡 可删（丢失防重复能力） |
| `data/reports/*.md` | `watch`/`distill` | 深度解析与综述（= 知识库） | 🟢 正文 |
| `data/reports/archive/` | `distill --prune` | 被综述吸收的原始笔记（不参与检索） | 🟢 |
| `data/token_usage.json` | 各 LLM 调用 | 每日按模型 token 记账（`usage` 命令查看） | 🟢 |
| `data/dynamic_history.json` | `watch` | 已发动态记录（社交清扫回访用） | 🟢 |
| `data/sprite.lock` | 进程锁 | 运行时独占锁文件 | 🟢 |
| `data/qrcode.png` | `login` | 最近一次登录二维码 | 🟢 |

## 常驻运行

```bash
# 方式一：nohup 常驻（每 2 小时一轮）
nohup python -u main.py watch > run.log 2>&1 &

# 方式二：cron 单轮（更稳，崩了下一轮自动恢复）
# crontab -e 添加（每 2 小时的第 10 分，错开整点）：
10 */2 * * * cd /path/to/sprite && .venv/bin/python -u main.py watch --once >> run.log 2>&1
```

`-u` 很重要：关闭输出缓冲，日志实时可读。

## 开源前自检清单

- [ ] `sprite/config.py` 的 `DEFAULTS` 中无任何真实 Key / 个人端点（应全部为空或官方公共默认值）
- [ ] `config.toml` 与整个 `data/` 均在 `.gitignore`（**data/ 里有明文聊天记录 qq_sessions.json、凭据 credential.json、知识库 reports/**）
- [ ] `config.example.toml` 中无真实 Key（模板 `api_key = ""`）
- [ ] `run.log`/`qq.log` 未被 `git add`
- [ ] 添加 LICENSE（未选可考虑 MIT / Apache-2.0）
- [ ] README 的风险与合规章节保留：非官方接口、封号风险、AIGC 标识义务
