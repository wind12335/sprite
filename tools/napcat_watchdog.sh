#!/usr/bin/env bash
# NapCat 看门狗：3001掉线时自动快速登录拉起；失败则记录并留待人工扫码
# 用法（每5分钟一次，crontab -e 添加）：
#   */5 * * * * $(dirname $0)/napcat_watchdog.sh >> $(dirname $0)/../data/watchdog.log 2>&1
LOG_PREFIX="[$(date '+%m-%d %H:%M:%S')]"

if sudo ss -tln | grep -q "127.0.0.1:3001"; then
    exit 0  # 一切正常，静默
fi

echo "$LOG_PREFIX ⚠️ 3001掉线，尝试快速登录拉起"
bash "$(cd "$(dirname "$0")" && pwd)/napcat_restart.sh" 2>/dev/null | tail -1
if sudo ss -tln | grep -q "127.0.0.1:3001"; then
    echo "$LOG_PREFIX ✅ 自动恢复成功"
else
    echo "$LOG_PREFIX ❌ 快速登录失败——需要人工扫码（WebUI: http://localhost:6099/webui）"
fi
