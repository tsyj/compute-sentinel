#!/usr/bin/env bash
# cluster-mcp-server 端到端验证：握手 / 列工具 / 正常调用 / 安全边界 / 审计
set -uo pipefail
PY="${PY:-python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
AUDIT="$(mktemp)"
CASE="${CASE:-/data/xinyuan/crown_ab_v2_20260717}"
{
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}'
echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
echo '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"tail_log","arguments":{"path":"'$CASE'/orchestrate_v3.log","lines":3}}}'
echo '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"stat_outputs","arguments":{"run_dir":"'$CASE'/bridge_v3/out","patterns":"wrfout_*|wrfrst_*"}}}'
echo '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"tail_log","arguments":{"path":"/etc/shadow"}}}'
echo '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"sample_resources","arguments":{"pattern":"; rm -rf /"}}}'
} | "$PY" "$HERE/cluster_mcp_server.py" --allow /data/xinyuan --allow /home/xinyuan --audit "$AUDIT"
echo "--- 审计 ---"; cat "$AUDIT"; rm -f "$AUDIT"
