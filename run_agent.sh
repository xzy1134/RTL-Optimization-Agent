#!/usr/bin/env bash
# Short interactive entry point for the current Agent workflows.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PYTHON="./.venv-m3/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "未找到 .venv-m3/bin/python，请先按 rtl_agent/agent/README.md 配置环境。" >&2
  exit 1
fi

read -r -p 'RTL 文件路径（相对项目根目录或绝对路径，不加引号）：' rtl || exit 0
case "$rtl" in
  '~/'*) rtl="$HOME/${rtl:2}" ;;
esac
if [ ! -f "$rtl" ]; then
  echo 'RTL 文件不存在。' >&2; exit 2
fi
# Absolute paths also let argparse accept filenames beginning with a dash.
case "$rtl" in
  /*) ;;
  *) rtl="$PWD/$rtl" ;;
esac
read -r -p '顶层模块名（填写 RTL 中的 module 名称）：' top || exit 0
if [[ ! "$top" =~ ^[A-Za-z_][A-Za-z0-9_\$]*$ ]]; then
  echo '请输入有效的顶层模块名。' >&2; exit 2
fi

args=(-B -m rtl_agent.agent "$rtl" --top "$top" --depth-limit-baseline)
echo '使用真实 DeepSeek API（有费用），自动读取本地配置。'
echo '逻辑深度上限自动设为原始 RTL 的综合深度。当前仅支持组合电路形式验证。'
echo '最多 5 个候选、5 次请求、50,000 Token 预算，搜索运行时间上限 300 秒。'
args+=(--backend deepseek --max-candidates 5 --max-rounds 5
        --patience 5 --max-api-requests 5 --max-tokens 50000 --runtime 300)
exec "$PYTHON" "${args[@]}"
