#!/usr/bin/env bash
# 复现证据案例 01 的预检实测
#
# 在四个配置状态上运行 config-precheck：
#   1. bridge_v2           真实配置，第一次失败（00:38 ABORT）
#   2. A_wrong_varname     重建 01:24 那次发射（用了未注册的变量名，162 rank 秒崩）
#   3. B_stale_paths       重建 cp -a 之后、sed 改路径之前的中间态
#   4. bridge_v3           真实配置，已应用修复（03:49 仍然失败）
#
# 状态 2、3 是按 worklog/2026-07-18.md 的记录重建的中间态；1、4 是原始文件，未经修改。
set -euo pipefail

PY="${PY:-/home/xinyuan/anaconda3/envs/numpy1/bin/python}"
SRC="${SRC:-/data/xinyuan/crown_ab_v2_20260717}"
REG="${REG:-/data/xinyuan/COAWST_Yagi_ERA5_WRF3km_GCE_Sep03-08/WRF/Registry}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "config-precheck 实测 · 证据案例 01"
echo "  被检对象 : $SRC"
echo "  Registry : $REG"
echo

# 重建中间态 A：把修复后的正确变量名换成交接简报给的错名
cp -r "$SRC/bridge_v3/cwd" "$WORK/A_wrong_varname"
sed -i 's/override_restart_timers/override_restart_intervals/' "$WORK/A_wrong_varname/namelist.input"

# 重建中间态 B：cp -a bridge_v2 bridge_v3，但没有 sed 改路径
mkdir -p "$WORK/B_stale/bridge_v3"
cp -r "$SRC/bridge_v2/cwd" "$WORK/B_stale/bridge_v3/cwd"

run() {  # 标题 目录
    echo "──────────────────────────────────────────────────────────────"
    echo "▸ $1"
    set +e
    "$PY" "$HERE/config_precheck.py" --case "$2" --registry "$REG"
    echo "  [退出码 $?]"
    set -e
    echo
}

run "状态 1 · bridge_v2（原始文件，第一次失败的那份）"        "$SRC/bridge_v2/cwd"
run "状态 2 · 重建 01:24 发射（错变量名）"                     "$WORK/A_wrong_varname"
run "状态 3 · 重建复制后未改路径"                              "$WORK/B_stale/bridge_v3/cwd"
run "状态 4 · bridge_v3（原始文件，已应用修复）"               "$SRC/bridge_v3/cwd"
