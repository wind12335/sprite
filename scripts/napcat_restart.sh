#!/usr/bin/env bash
# NapCat 单实例重启（脚本内含QQ路径字样没关系：本进程命令行只有本脚本路径）
for s in $(sudo screen -ls 2>/dev/null | grep -oE "[0-9]+\.napcat"); do
  sudo screen -S "$s" -X quit 2>/dev/null
done
sleep 1
NAPCAT_DIR="${NAPCAT_DIR:-/root/Napcat}"
sudo pkill -9 -f "$NAPCAT_DIR/opt/QQ/qq" 2>/dev/null
sleep 2
BOT_QQ="${BOT_QQ:-}"
QARG=""; [ -n "$BOT_QQ" ] && QARG="-q $BOT_QQ"
sudo screen -dmS napcat xvfb-run -a "$NAPCAT_DIR/opt/QQ/qq" --no-sandbox $QARG
echo "已重启（带账号快速登录）"
for i in $(seq 1 45); do
  if sudo ss -tln | grep -q "127.0.0.1:3001"; then echo "OK_3001_UP after $((i*2))s"; exit 0; fi
  sleep 2
done
echo "LOGIN_FAILED"
exit 1
