#!/usr/bin/env bash
# sprite 一键启动
# 用法：./start.sh qq   —— 启动QQ侧（自动先拉 NapCat 并等它就绪）
#       ./start.sh bili —— 启动B站侧（watch 常驻：搜视频/发动态/评论/楼中楼社交）
#       ./start.sh all  —— 两侧都启动
set -e
cd "$(dirname "$0")/.."
# 可通过环境变量覆盖：NAPCAT_DIR(NapCat安装目录) BOT_QQ(机器人小号,快速登录用)
NAPCAT_DIR="${NAPCAT_DIR:-/root/Napcat}"
BOT_QQ="${BOT_QQ:-}"
PY=.venv/bin/python

kill_napcat_strays() {
  # 清掉所有同名 screen 会话与孤儿QQ进程，保证单实例
  # （拼串写法让本脚本自身命令行不含完整路径，避免 pkill 自杀式误伤）
  for s in $(sudo screen -ls 2>/dev/null | grep -oE "[0-9]+\.napcat"); do
    sudo screen -S "$s" -X quit 2>/dev/null
  done
  QQBIN="$NAPCAT_DIR/opt/QQ"/qq
  sudo kill -9 $(pgrep -f "$QQBIN") 2>/dev/null
  sleep 1
}

ensure_napcat() {
  if sudo ss -tln | grep -q ":3001"; then echo "[NapCat] 已在运行"; return 0; fi
  # 端口不在但可能有半死会话/孤儿进程 → 先清场再启动，杜绝多实例重叠
  kill_napcat_strays
  echo "[NapCat] 启动中（首次需等待自动登录）…"
  QARG=""; [ -n "$BOT_QQ" ] && QARG="-q $BOT_QQ"
  sudo screen -dmS napcat xvfb-run -a "$NAPCAT_DIR/opt/QQ/qq" --no-sandbox $QARG
  for _ in $(seq 1 40); do
    if sudo ss -tln | grep -q ":3001"; then echo "[NapCat] ✅ 就绪 (ws://127.0.0.1:3001)"; return 0; fi
    sleep 1
  done
  echo "[NapCat] ⚠️ 40秒内未就绪。可能小号掉线，开 WebUI 检查：http://localhost:6099/webui" >&2
  return 1
}

start_qq() {
  ensure_napcat || exit 1
  if pgrep -f "main.py qq" > /dev/null; then echo "[QQ] 已在运行"; return; fi
  nohup $PY -u main.py qq > qq.log 2>&1 &
  echo $! > data/qq.pid
  sleep 4
  if grep -q "已连接 OneBot" qq.log; then
    echo "[QQ] ✅ 智谱娘已上线（日志 qq.log，PID $(cat data/qq.pid)）"
  else
    echo "[QQ] ⏳ 连接中，稍后查看 qq.log"
  fi
}

start_bili() {
  if pgrep -f "main.py watch" > /dev/null; then echo "[B站] watch 已在运行"; return; fi
  nohup $PY -u main.py watch > run.log 2>&1 &
  echo $! > data/watch.pid
  sleep 2
  echo "[B站] ✅ watch 常驻已启动（每2小时一轮，日志 run.log，PID $(cat data/watch.pid)）"
}

start_widget() {
  echo "[桌宠] 停止旧实例（如有）..."
  # 只匹配python进程，powershell自身不是python，不会自杀
  powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { \$_.CommandLine -match 'sprite_widget' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" >/dev/null 2>&1
  sleep 1
  rm -f /mnt/d/widget.lock
  echo "[桌宠] 部署最新代码到 Windows..."
  cp tools/sprite_widget.py /mnt/d/sprite_widget.py
  pwd > /mnt/d/sprite_dir.txt
  echo "$NAPCAT_DIR" > /mnt/d/napcat_dir.txt
  # 桌宠标题栏名字 = config.toml [persona].name（缺省 sprite）
  grep -A5 '^\[persona\]' config.toml 2>/dev/null | grep '^name' | head -1 \
    | sed 's/.*= *"\([^"]*\)".*/\1/' > /mnt/d/widget_name.txt
  [ -s /mnt/d/widget_name.txt ] || echo "sprite" > /mnt/d/widget_name.txt
  [ -f tools/avatar.gif ] && cp tools/avatar.gif /mnt/d/avatar.gif
  [ -f tools/avatar.png ] && cp tools/avatar.png /mnt/d/avatar.png
  # 无自定义头像时用官方图标（assets/sprite_icon.png）作默认形象
  if [ ! -f tools/avatar.gif ] && [ ! -f tools/avatar.png ] && [ -f assets/sprite_icon.png ]; then
    cp assets/sprite_icon.png /mnt/d/avatar.png
  fi
  echo "[桌宠] 启动 Windows 桌宠..."
  # 用Windows原生路径启动（/mnt/d/... 会被Windows Python误解为UNC路径）
  # 输出全部落 D:\widget.log：终端不再冒GBK乱码，异常也有据可查
  PYTHONUTF8=1 /mnt/d/anaconda3/python.exe "D:\\sprite_widget.py" >>/mnt/d/widget.log 2>&1 &
  echo "[桌宠] ✅ 已启动（日志: D:\\widget.log）"
}

case "${1:-}" in
  qq)     start_qq ;;
  bili)   start_bili ;;
  widget) start_widget ;;
  all)    start_bili; start_qq ;;
  *)      echo "用法: ./start.sh qq | bili | widget | all"; exit 1 ;;
esac
