<p align="center">
  <img src="assets/sprite_icon.png" width="180" alt="sprite"/>
</p>

<h1 align="center">sprite ✨</h1>

<p align="center">B站 + QQ 双端 AI 桌宠助手 · 任意 OpenAI 兼容模型（GLM / DeepSeek / Qwen / Kimi…）</p>

默认人设是智谱娘（GLM 出品），但大脑可插拔——DeepSeek、Qwen、Kimi 等任意 OpenAI 兼容模型都能换上，人设也在配置里随便改。她住进你的 WSL：白天在 B 站看 AI infra 视频写笔记、发动态、回评论；在 QQ 群里有人设地聊天、看图、认主人；晚上把一天的知识蒸馏成综述同步进 Obsidian；还能把论文 PDF 吃进知识库。Windows 桌面上有一只桌宠，一键启停所有能力。

```
                 ┌─────────────────────────────┐
                 │  任意 OpenAI 兼容 LLM 厂商     │
                 │  GLM / DeepSeek / Qwen / Kimi │
                 │  （看图需多模态视觉模型）        │
                 └──────────┬──────────────────┘
        ┌───────────────────┼───────────────────┐
   ┌────▼─────┐       ┌─────▼──────┐      ┌─────▼─────┐
   │ B站 watch │       │  QQ 侧      │      │ 知识中枢   │
   │ 搜→筛→深解 │       │ NapCat WS   │      │ TF-IDF检索 │
   │ 动态/评论  │       │ 人设/RAG/看图│      │ 记忆/蒸馏  │
   └────┬─────┘       └─────┬──────┘      └─────┬─────┘
        └───────────────────┼───────────────────┘
                    ┌───────▼────────┐
                    │ Obsidian 知识库 │  ← 日报/综述/论文笔记
                    └────────────────┘
                    ┌────────────────┐
                    │ Windows 桌宠 GUI │  ← 一键启停/综述/吸收论文
                    └────────────────┘
```

## 功能总览

| 端 | 能力 |
|---|---|
| B站 | 关键词+UP主订阅→两级筛选（便宜模型快筛省 70% token）→字幕深度解析→发动态@主人→总结评论→楼中楼追问（防杠精） |
| QQ | NapCat/OneBot v11，人设对话、@/关键词/私聊触发、图片理解（需多模态模型）、主人识别、/命令、防刷屏防杠精、两级禁言 |
| 知识 | 报告入库→TF-IDF 检索（纯本地零依赖）；`distill` 把相关报告蒸馏成综述；MemGPT 式会话归档 |
| Obsidian | 所有报告/综述/论文笔记带 YAML frontmatter 写入你的 vault |
| 论文 | PDF→LLM 解析→结构化笔记→知识库+Obsidian |
| 桌宠 | Windows tkinter 无边框置顶小部件：B站/QQ 一键启停、今日/补昨日综述、吸收论文、状态、保洁、编辑配置 |

## 安装（一步一步来）

环境要求：Windows 10/11 + WSL2 (Ubuntu)；NapCat 需以 root 安装（步骤 4）。

### 1. 拉代码 + Python 环境（WSL 内）

```bash
git clone https://github.com/wind12335/sprite.git
cd sprite
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo apt install poppler-utils   # 论文入库功能用（pdftotext 提取 PDF 文本）
```

### 2. 配置模型（任意 OpenAI 兼容厂商）

```bash
cp config.example.toml config.toml
nano config.toml
```

必填项（模板里搜 ★）：
- `[llm] base_url + api_key + model`：按你选的厂商填（下面有现成对照）
- `[watch] master_uid`：你的B站 UID（动态@你）；`comment_at_uid`：评论里@的账号
- `[qq] owner_qq`：你的主 QQ 号（人设认主人，私聊免冷却）
- `[obsidian] vault_path`：你的 Obsidian 库路径（不用可留空）

**厂商对照**（都是 OpenAI 兼容接口，填三行就换脑）：

| 厂商 | base_url | 大脑 model | 快筛 model | 看图 |
|---|---|---|---|---|
| 智谱 GLM（默认） | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.3` | `glm-5.3-flash` | `glm-4.6v` |
| 智谱 Coding Plan | `https://open.bigmodel.cn/api/coding/paas/v4` | 同上（套餐内） | 同上 | 同上（套餐内） |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` | `deepseek-chat` | 无视觉模型，`vision_model` 置空禁用看图 |
| 通义 Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | `qwen-turbo` | `qwen-vl-plus` |

Key 获取：智谱 https://open.bigmodel.cn ｜ DeepSeek https://platform.deepseek.com ｜ 阿里 https://dashscope.console.aliyun.com 。**更安全**：不落盘，`export GLM_API_KEY=xxx`（环境变量优先于配置文件）。

人设是 `[persona]` 里的提示词——默认智谱娘，你可以改成任何角色（DeepSeek 用户不妨试试大肥鱼人设 🐋）。

### 3. B站登录（一次性）

```bash
.venv/bin/python main.py login     # 浏览器扫码，凭据存 data/credential.json（不出机器）
.venv/bin/python main.py status    # 确认身份
```

### 4. 安装 NapCat（QQ 协议端，root 安装）

NapCat 是 QQ 的 OneBot v11 实现，sprite 通过 WebSocket 与它通信。按 [NapCat 文档](https://napcat.napneko.icu/) 以 root 装到 `/root/Napcat`（别处也行，见"路径变量"）。装好后：

1. 启动 NapCat，进 WebUI（默认 `http://localhost:6099/webui`）
2. 添加**正向 WebSocket** 服务，端口 `3001`（与 `config.toml [qq] ws_url` 一致）
3. QQ 登录页用**机器人小号**扫码

网络注意：Windows 侧的 NapCat 要能被 WSL 访问，推荐 WSL2 **mirrored 模式**（`.wslconfig` 里 `networkingMode=mirrored`），`ws://127.0.0.1:3001` 直接互通。

### 5. 一键启停

```bash
./scripts/start.sh bili    # B站侧常驻（每2小时：搜索→筛选→解析→动态/评论）
./scripts/start.sh qq      # QQ侧：自动拉 NapCat 并等就绪 → 智谱娘上线
./scripts/start.sh widget  # 部署并启动 Windows 桌宠（自动停旧实例）
./scripts/start.sh all     # bili + qq

./scripts/stop.sh all      # 全停；也可 ./scripts/stop.sh qq / ./scripts/stop.sh bili
./scripts/status.sh        # 各进程/端口状态
```

### 6. Windows 桌宠（可选但强烈推荐）

`./scripts/start.sh widget` 把 `tools/sprite_widget.py` 部署到 `D:\` 并用 Windows Python 启动（默认 `D:\anaconda3\python.exe` 且装有 pillow，路径不同改 `start.sh` 对应行）。

- **左键点一下头像**：展开控制面板；**长按拖动**：挪位置；**右键**：菜单；**Esc**：收起
- 换形象：放 `avatar.gif` 或 `avatar.png` 到仓库 `tools/` 下，重启 widget
- 标题栏名字取自人设（`[persona].name` 或人设包）——QQ 人设叫什么，桌宠就叫什么
- **状态表情**：官方小精灵自带四态套图（正常/眨眼/干活星眼冒汗/出错哭脸），按后端状态自动切换；收起态底部状态条 + 展开态标题栏状态点同步提示。按下头像会"陷一下"作为点击反馈
- **自定义形象也支持表情套图**：放 `tools/avatar.png` 后，再放同名 `_blink/_working/_error` 姊妹帧（如 `avatar_blink.png`）即可拥有四态；只有单张也能跑（状态条兜底）
- 排查看 `D:\widget.log`（含 400ms 粒度窗口状态飞行记录）

### 7. 定时任务（可选）

```bash
crontab -e
# 每2小时跑一轮B站情报
0 */2 * * * cd ~/workspace/sprite && .venv/bin/python -u main.py watch --once >> run.log 2>&1
# NapCat 看门狗：掉线自动拉起
*/5 * * * * ~/workspace/sprite/scripts/napcat_watchdog.sh >> /dev/null 2>&1
```

更多（systemd、启动复盘、FAQ）见 [docs/STARTUP.md](docs/STARTUP.md)；全部 CLI 命令见 [docs/USAGE.md](docs/USAGE.md)。

## 人设包（换名字换性格，一行配置）

`personas/` 目录下每个 toml 就是一套人设（name + system_prompt）：

```toml
[persona]
file = "personas/deepseek_whale.toml"   # 换成大肥鱼；留空则用内联人设
```

内置 `zhipu_girl.toml`（智谱娘）和 `deepseek_whale.toml`（大肥鱼）两个示例；自己照抄一份改两行就是新角色。桌宠标题栏的名字会自动跟着人设走。

## 路径与账号变量

默认布局是"root 装 NapCat + 仓库在 ~/workspace/sprite"，不同就环境变量覆盖：

| 变量 | 默认 | 说明 |
|---|---|---|
| `NAPCAT_DIR` | `/root/Napcat` | NapCat 安装目录 |
| `BOT_QQ` | 空 | 机器人小号QQ（填了启动带快速登录） |
| `GLM_API_KEY` | 空 | GLM Key（优先于 config.toml） |

## 快速体验

```bash
.venv/bin/python main.py ask "用一句话介绍你自己"     # 和智谱娘对话
.venv/bin/python main.py watch --once                # 跑一轮B站情报（先填 master_uid）
.venv/bin/python main.py daily-digest --prune        # 今日综述→Obsidian，归档原文
.venv/bin/python main.py daily-digest --date 2026-10-25 --prune   # 补指定日期
.venv/bin/python main.py paper ~/papers/xxx.pdf      # 吸收一篇论文
```

## 防线与默认值（config.toml `[safety]`）

| 参数 | 默认 | 作用 |
|---|---|---|
| `aigc_label` | `🤖AI生成` | 每条评论/动态追加的显式标识 |
| `reply_daily_limit` / `dynamic_daily_limit` | 30 / 3 | 每日总量上限 |
| `reply_cooldown_per_user` | 1800s | 同一用户不被连刷 |
| `reply_gap_seconds` / `max_reply_per_poll` | 45s / 3 | 降低行为密度 |
| `watch_video_count` | 5 | 只盯最近几个视频 |

## ⚠️ 风险与合规（务必读）

1. **封号风险**：非官方接口 + 自动化行为违反平台用户协议，可能触发风控（限流、冻结、封号）。社区惯例：**一律用小号**、频率宁低勿高、从"被动回复"起步，不要一上来就高频发动态/评论。
2. **AIGC 标识是法定义务**：《人工智能生成合成内容标识办法》自 2025-09-01 施行，AI 生成内容需显式标识。本框架默认给评论/动态加 `🤖AI生成` 后缀，视频投稿请手动勾选 AI 声明。
3. **凭据安全**：`data/credential.json` 等同于你的账号密码，别提交 git、别发给别人；`data/` 已默认排除在任何远端之外。
4. **内容责任**：评论/动态内容的责任在你。人设避免引导对立、引流、刷屏类指令。

## 目录结构

```
sprite/
├── main.py               # CLI 入口（login/status/watch/ask/distill/social/paper/daily-digest/qq/…）
├── config.example.toml   # 配置模板（复制为 config.toml）
├── sprite/               # 核心包
│   ├── llm.py            #   GLM 客户端（重试/token统计/视觉调用）
│   ├── bili/             #   B站：登录凭据/搜索/字幕/评论/动态
│   ├── flow/             #   流水线：watch（视频情报）social（楼中楼）
│   ├── qq/               #   QQ：onebot客户端 + 人设适配器
│   ├── mind/             #   知识库（TF-IDF）+ 记忆/人设
│   └── housekeep.py      #   MemGPT式保洁：会话归档/日志轮转/配额提醒
├── tools/                # Windows侧工具（桌宠 GUI）
├── personas/            # 人设包（一行配置换角色）
├── assets/              # 图标等资源
├── docs/                # 深入文档（STARTUP/USAGE）
├── scripts/            # start/stop/status + NapCat看门狗
└── data/                 # 运行时数据（gitignore，凭据/记忆/报告）
```

## License

MIT — 见 [LICENSE](LICENSE)。使用本项目产生的账号风险自负。
