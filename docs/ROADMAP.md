# sprite 更新计划（ROADMAP）

原则：**架构由真实需求塑造，不提前造。** 每项都写明重启条件，条件不满足就不动手。

## 人设包演进（优先级已排）

当前状态：`personas/*.toml` 平铺文件，仅 `name` + `system_prompt` 两键，包内非空键覆盖内联值。

### 目标结构（完全体）

```toml
# personas/大肥鱼/persona.toml —— 包升级为目录：toml + 资源整套
schema = 1

[identity]
name = "大肥鱼"
system_prompt = """……"""

[voice]                           # 说话风格：程序性约束 > 提示词约束
suffix = "🌊"                     # 口癖结尾，每条回复自动缀上
max_chars = 120                   # 人设级回复长度硬上限
emoji_pool = ["🐋", "🌊"]         # 表情池随机点缀

[behavior]                        # 行为参数：人设级覆盖全局
trigger_keywords = ["大肥鱼", "肥鱼"]   # 唤醒词（从[qq]全局下放）
greetings = ["又见面啦～"]               # 被@时的开场白池
interject_rate = 0.05

[samples]                         # 杀手锏：少样本对话
dialogue = [
  { user = "什么是KV Cache", reply = "潜水员的氧气瓶！……🌊" },
]

[assets]                          # 换装：人设+形象整套切
avatar_set = "."                  # 指向包内 pet_idle/blink/working/error 帧
accent = "#2196F3"                # 桌宠主题色跟随人设

[meta]
author = ""
version = "1.0"
description = ""
```

### 分期与重启条件

| 期 | 内容 | 价值 | 重启条件 |
|---|---|---|---|
| **① 声音层**（下一期就做） | `[samples]` 少样本注入 + `[voice]` 口癖/长度 + loader 白名单化 | 少样本是角色一致性的最强杠杆，当天可感知 | 无条件，随时可做（约半小时工作量） |
| ② 触发词下放 | `[behavior].trigger_keywords` 从 `[qq]` 全局段搬进包 | 逻辑自洽：唤醒词本就是角色属性 | 想给不同人设配不同唤醒词时 |
| ③ 换装 | `[assets]` 表情帧目录 + 桌宠主题色传递 | 人设包 = 完整角色 | 出现第二个真实使用者，或需要成套角色形象 |
| ④ 包管理 | `[meta]` + 目录包格式 + 平铺/目录双兼容 | 社区投稿基础设施 | 有人开始投稿人设包 |

### 实现约束（做的时候必须守住）

- **白名单合并**：loader 只认白名单键，禁止盲写（现在是 `for k,v in pack.items()` 直塞，键多后是脏配置口子）
- **覆盖语义三层**：`defaults < pack < [persona.overrides]`（用户微调放独立层，杜绝"内联残留顶回新角色名"）
- **未知键静默忽略 + `schema` 版本号**：新旧版本包与程序互相兼容
- samples 注入位置：brain.py 组装消息时拼在 system 之后、对话历史之前

## 功能路线（讨论已定，未排期）

| 功能 | 说明 | 前置 |
|---|---|---|
| arXiv RSS 订阅 | 关键词抓新论文 → paper 管线自动入库 → 早报进 Obsidian | 无，paper 命令已是现成下游 |
| 周报自动蒸馏 | cron 定时 distill + 周综述，知识库自我进化 | 无，distill/daily-digest 已有 |
| 平台适配器接口 | `Platform` 协议（收/发/轮询/凭据），B站与QQ改造为实现，新平台=新适配器 | 第三个平台需求出现时再固化接口 |
| 动态配图 | GLM 文生图为动态自动配封面 | 依赖文生图 API 接入 |
| Web 仪表盘 | 单页看 token 用量/报告产出/配额（usage 数据已在记账） | 低优先 |
| 语音 | TTS 语癖 / QQ 语音回复 | 锦上添花，最后 |

## 已完成（2026-10）

### 第二批（10-27/28 晚）
- 防提示词注入四层防御（guard.py：消毒/隔离/硬偏转/输出审计）
- 联网搜索：搜狗+必应双引擎无Key，QQ自动搜索 + `/搜` 口令 + `search` 命令
- 论文情报摄取 `ingest`：arXiv+HF日报→PDF队列→自动精读→归档（papers/absorbed）
- QQ花边播报（HN/Reddit/HF真实资讯+人设口吻，30min缓存）
- 锁语义修正：只读命令不再与watch互斥；paper扫仓模式+跨天防重


- v20 桌宠：状态表情四态 + 点击反馈 + 人设包取名 + 单实例锁 + 飞行记录仪
- 人设包最小版（name + system_prompt）、官方小精灵四态套图、自定义形象姊妹帧约定
- 模型厂商解耦（OpenAI 兼容 + thinking 参数条件发送）、仓库结构规范化（scripts/assets/docs）
