# sprite 启动手册（STARTUP）

> 场景：WSL 重启后 / 手动停掉后，把整套系统拉起来。
> 两个部分互相独立：**QQ 侧**（NapCat + 适配器）和 **B站侧**（watch/social），可以只启动需要的部分。

## 主人口令速查（QQ里直接发给智谱娘，群里不用@，只认主人）

| 口令 | 作用 |
|---|---|
| `/帮助` | 列出全部口令（**记不住就只记这一条**） |
| `/状态` | B站watch/NapCat/知识库/闭嘴概况 |
| `/token` | 今日 token 消耗 |
| `/闭嘴` | 当前会话闭嘴（只闭这个群/私聊） |
| `/全局闭嘴` | 全部会话闭嘴 |
| `/回来` `/全局回来` | 解除闭嘴 |
| `/重置记忆` | 清空当前会话上下文（知识库不动） |

## 一图流

```
WSL 重启
  ├── QQ侧：① 启动 NapCat（30秒自动登录）→ ② 启动 sprite qq → 完成
  └── B站侧：③ 启动 sprite watch（常驻）或已配 cron 则无需手动
```

## QQ 侧启动（两步）

### ① 启动 NapCat（协议端，挂小号）

```bash
sudo screen -dmS napcat xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox
```

- 已配置自动登录小号 你的机器人小号（config/环境变量 BOT_QQ），约 20-30 秒完成登录并监听 3001
- 确认就绪（看到 `127.0.0.1:3001` 即成功）：

```bash
sudo ss -tln | grep 3001
```

### ② 启动智谱娘 QQ 适配器

```bash
cd ~/workspace/sprite && source .venv/bin/activate
python -u main.py qq
```

- 看到 `已连接 OneBot 实现：ws://127.0.0.1:3001` 即成功
- 想退出：`Ctrl+C`；想后台挂着：`nohup python -u main.py qq > qq.log 2>&1 &`
- 防双开：已有实例在跑时会提示 `⛔ 已有 qq 实例在运行`（锁文件 `data/qq.lock`）

### 日常启停命令速查

| 操作 | 命令 |
|---|---|
| 看 NapCat 控制台 | `sudo screen -r napcat`（`Ctrl+A` 再按 `D` 退出查看，不会停掉进程） |
| 停 NapCat | `sudo screen -S napcat -X quit` |
| 查 3001 是否监听 | `sudo ss -tln \| grep 3001` |
| 查 WebUI 登录令牌 | `sudo cat /root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json \| grep token` |
| WebUI（扫码/改配置） | Windows 浏览器 `http://localhost:6099/webui` |
| 停 sprite qq（后台模式） | `kill $(cat data/qq.lock)` 或 `pkill -f "main.py qq"` |

## B 站侧启动

```bash
cd ~/workspace/sprite && source .venv/bin/activate

# 常驻模式（每2小时扫一轮，含发动态/评论/楼中楼社交清扫）
nohup python -u main.py watch > run.log 2>&1 &

# 或单轮模式（推荐配 cron，见下）
python -u main.py watch --once

# 只跑社交清扫（回复楼中楼/动态评论）
python -u main.py social [--dry-run]
```

cron 单轮方案（`crontab -e`，每 2 小时第 10 分，自带进程锁不怕与手动跑冲突）：

```
10 */2 * * * cd ~/workspace/sprite && .venv/bin/python -u main.py watch --once >> run.log 2>&1
```

## 开机自启（可选，你的 WSL 支持 systemd）

创建 `/etc/systemd/system/napcat.service`：

```ini
[Unit]
Description=NapCat QQ protocol bridge
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

创建 `/etc/systemd/system/sprite-qq.service`：

```ini
[Unit]
Description=sprite QQ adapter (智谱娘)
After=network.target napcat.service

[Service]
Type=simple
User=<你的用户名>
WorkingDirectory=~/workspace/sprite
ExecStart=~/workspace/sprite/.venv/bin/python -u main.py qq
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

启用（一次性）：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now napcat sprite-qq
systemctl status sprite-qq --no-pager   # 查看运行状态
```

启用后 WSL 重启即自动全套拉起，无需手动任何步骤；journalctl -u sprite-qq -f 可看实时日志。
（注意：若启用 systemd 自启，就不要再手动 nohup 启动，避免双开——进程锁会拦住第二个。）

## 故障排查

| 症状 | 原因与处理 |
|---|---|
| **凭据速查** | WebUI token：`sudo cat /root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json \| grep '"token"'`；WS token 与 LLM Key：`grep -E "access_token\|api_key" config.toml`（凭据只存在 gitignore 的文件里，勿写进文档/代码） |
| 3001 一直不监听 | NapCat 没登录。**隔天/隔几天首次启动大概率要重新扫码（会话过期，属正常现象）**：开 WebUI（6099）→ QQ登录页 → 小号扫码。快速登录重启法：`sudo screen -dmS napcat xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox -q <你的小号QQ>` |
| 小号掉线（登录态过期） | WebUI 里重新扫码；NapCat 会话一般能维持很久，掉线多因异地/风控 |
| sprite qq 提示连接断开 | 正常现象（自动重连）；持续失败检查 NapCat 是否还活着、3001 是否监听 |
| `⛔ 已有 qq 实例在运行` | 确认没有旧进程（`pgrep -af "main.py qq"`）后删锁：`rm data/qq.lock` |
| WebUI 打不开（Windows） | WSL 端口转发抽风，用 WSL IP 替代 localhost：`hostname -I` 查 IP |
| QQ 机器人不回消息 | ① 私聊开关/群白名单（config.toml [qq]）② 同会话 20s 冷却 ③ 当日 300 条限额 ④ 看日志是否收到消息事件 |
| WSL 里网络对 GitHub 龟速 | 更新 NapCat 时用镜像并行下载（本次部署经验：gh.acmsz.top + 8路并行） |
