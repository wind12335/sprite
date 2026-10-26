#!/usr/bin/env bash
# sprite 状态一览：./status.sh
cd "$(dirname "$0")/.."

echo "── NapCat（QQ协议端）──"
sudo ss -tln 2>/dev/null | grep -q ":3001" && echo "  ✅ 运行中 (3001 监听)" || echo "  ❌ 未运行"

echo "── 智谱娘 QQ ──"
pgrep -f "main.py qq" > /dev/null && echo "  ✅ 运行中 (PID $(pgrep -f 'main.py qq' | head -1))" || echo "  ❌ 未运行"

echo "── B站 watch ──"
pgrep -f "main.py watch" > /dev/null && echo "  ✅ 运行中 (PID $(pgrep -f 'main.py watch' | head -1))" || echo "  ❌ 未运行"

echo "── 今日 token ──"
[ -x .venv/bin/python ] && .venv/bin/python main.py usage 2>/dev/null | tail -1
