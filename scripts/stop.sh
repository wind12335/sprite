#!/usr/bin/env bash
# sprite 一键停止
# 用法：./stop.sh            —— 全部停止（QQ + B站 + NapCat）
#       ./stop.sh qq | bili  —— 只停对应端（qq 含 NapCat，bili 只停 watch）
cd "$(dirname "$0")/.."
TARGET="${1:-all}"

stop_by_pidfile() {  # $1=pidfile名 $2=显示名
  if [ -f "data/$1.pid" ]; then
    kill "$(cat "data/$1.pid")" 2>/dev/null && echo "[$2] 已停止 (PID $(cat "data/$1.pid"))"
    rm -f "data/$1.pid"
  fi
}

stop_qq() {
  stop_by_pidfile qq.pid qq
  pkill -f "main.py qq" 2>/dev/null && echo "[qq] 兜底清理完成"
  # NapCat：清所有同名screen + 孤儿QQ进程（拼串防pkill自伤）
  NAPCAT_SCREENS=$(sudo screen -ls 2>/dev/null | grep -oE "[0-9]+\.napcat")
  if [ -n "$NAPCAT_SCREENS" ]; then
    for s in $NAPCAT_SCREENS; do sudo screen -S "$s" -X quit 2>/dev/null && echo "[NapCat] 已停止会话 $s"; done
  else
    echo "[NapCat] 无screen会话"
  fi
  sleep 1
  QQBIN="${NAPCAT_DIR:-/root/Napcat}/opt/QQ"/qq
  if pgrep -f "$QQBIN" > /dev/null 2>&1; then
    sudo kill -9 $(pgrep -f "$QQBIN") 2>/dev/null && echo "[NapCat] 已清理残留QQ进程"
    sleep 2
  fi
}

stop_bili() {
  stop_by_pidfile watch.pid watch
  pkill -f "main.py watch" 2>/dev/null && echo "[watch] 兜底清理完成"
}

case "$TARGET" in
  qq)     stop_qq ;;
  bili)   stop_bili ;;
  all)
    stop_qq; stop_bili
    rm -f data/qq.lock data/sprite.lock
    REMAIN=$(pgrep -f "main.py (qq|watch)" 2>/dev/null | wc -l)
    echo "✅ 全部停止（残留检查：${REMAIN}个应为0）"
    ;;
  *) echo "用法: ./stop.sh [all|qq|bili]"; exit 1 ;;
esac
[ "$TARGET" != "all" ] && rm -f "data/${TARGET}.lock" 2>/dev/null
echo "✅ 完成（目标：$TARGET）"
