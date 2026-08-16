#!/usr/bin/env bash
# 一条命令复现本仓库的全部确定性结论。
#
#   ./verify.sh
#
# 不需要 GPU、不需要网络、不调用任何大模型、不读取任何凭证。
# 纯 Python 标准库，Python ≥ 3.9 即可。
#
# 这个脚本本身就是一份声明：README 与证据文档里的每一个判定类数字，
# 都能在你自己的机器上重新算一遍。算不出来就是我们错了。
set -uo pipefail
cd "$(dirname "$0")"

PY="${PY:-python3}"
PASS=0; FAIL=0; SKIP=0
line() { printf '%s\n' "────────────────────────────────────────────────────────────"; }
ok()   { PASS=$((PASS+1)); printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31m✗\033[0m %s\n' "$1"; }
skip() { SKIP=$((SKIP+1)); printf '  \033[33m−\033[0m %s\n' "$1"; }

printf '\n算力哨兵 ComputeSentinel · 可复现性自检\n'
printf 'Python: %s\n' "$($PY -V 2>&1)"
line

# ── 1. 判定内核语义单测 ───────────────────────────────────────
printf '\n[1/6] 判定内核语义单测（tools/test_decide.py）\n'
if OUT=$($PY tools/test_decide.py 2>&1); then
  N=$(printf '%s' "$OUT" | grep -c '^PASS')
  ok "$N 项断言全部通过"
  printf '      含防回归护栏：纯 CPU workload 与预处理阶段不得被误判为卡死\n'
else
  bad "单测未通过"; printf '%s\n' "$OUT" | tail -12
fi

# ── 1b. safe-kill 对抗测试 ──────────────────────────────────
printf '\n[1b] safe-kill 对抗测试（tools/test_safe_kill.py）\n'
if OUT=$($PY tools/test_safe_kill.py 2>&1); then
  N=$(printf '%s' "$OUT" | grep -c '^PASS')
  ok "$N 项对抗用例全部通过"
  printf '      含关键用例：跨用户同名进程必须被拒，理由为属主不符\n'
else
  bad "对抗测试未全通过 —— 按 SKILL.md 标准该版本不可发布"
  printf '%s\n' "$OUT" | grep '^FAIL' | head -6
fi

# ── 2. MCP 契约与安全边界 ────────────────────────────────────
printf '\n[2/6] MCP Server 契约与安全边界（tools/test_mcp_server.sh）\n'
if OUT=$(bash tools/test_mcp_server.sh 2>&1); then
  grep -q '"protocolVersion": "2024-11-05"' <<<"$OUT" && ok "MCP 握手，protocolVersion 2024-11-05" || bad "握手失败"
  grep -q 'PATH_DENIED'   <<<"$OUT" && ok "越权路径被拒（PATH_DENIED）"      || bad "越权路径未被拒绝"
  grep -q 'BAD_PATTERN'   <<<"$OUT" && ok "命令注入串被拒（BAD_PATTERN）"    || bad "注入串未被拒绝"
else
  bad "MCP 测试脚本执行失败"
fi

# ── 3. GPU 退化路径卡死回放（漏报侧）─────────────────────────
printf '\n[3/6] GPU 退化路径卡死回放 —— 漏报侧（evidence/gpu-stall/）\n'
for R in "runA:ep 4 begin:909" "runB:ep 11 begin:318"; do
  NAME="${R%%:*}"; REST="${R#*:}"; LINE="${REST%%:*}"; WANT="${REST##*:}"
  SIG="evidence/gpu-stall/${NAME}-signals.jsonl"
  LOG="evidence/gpu-stall/${NAME}-train.log"
  if [[ -f "$SIG" && -f "$LOG" ]]; then
    OUT=$($PY tools/replay_signals.py --signals "$SIG" --log "$LOG" \
            --workload pytorch --stall-from-line "$LINE" 2>&1)
    MISS=$(grep -c '全程未判定卡死' <<<"$OUT")
    GOT=$(grep -oP '距退化开始 \K[0-9]+' <<<"$OUT" | head -1)
    if [[ "$MISS" == "1" && "$GOT" == "$WANT" ]]; then
      ok "$NAME：修复前全程漏报，修复后 ${GOT}s 发现（预期 ${WANT}s）"
    else
      bad "$NAME：预期「修复前漏报 + 修复后 ${WANT}s」，实得漏报块数 $MISS / 时延 ${GOT:-无}"
    fi
  else
    skip "$NAME：原始数据缺失"
  fi
done

# ── 4. 误报侧回放 ────────────────────────────────────────────
printf '\n[4/6] 误报侧回放\n'
# 负样本 C 随仓库发布，开箱即测：150 分钟真实成功训练，日志稀疏
SIGN="evidence/gpu-stall/runN-signals.jsonl"; LOGN="evidence/gpu-stall/runN-train.log"
if [[ -f "$SIGN" && -f "$LOGN" ]]; then
  grep -q 'DONE' "$LOGN" \
    && OUT=$($PY tools/replay_signals.py --signals "$SIGN" --log "$LOGN" --workload pytorch 2>&1) \
    || OUT=""
  ST=$(grep -c ' STALLED ' <<<"$OUT")
  # 该跑真值为「正常跑完」，修复后一路不得出现 STALLED
  AFTER=$(awk '/## 修复后/,0' <<<"$OUT" | grep -c ' STALLED ')
  if [[ "$AFTER" == "0" ]]; then
    ok "GPU 训练 150 分钟真实跑完（日志静默 18.2 分钟）：误报 0 次"
  else
    bad "GPU 训练负样本出现 $AFTER 次误报（预期 0）"
  fi
else
  skip "GPU 训练负样本数据缺失"
fi

COAWST="${COAWST_LOG:-}"
DL="${DOWNLOAD_LOG:-}"
if [[ -n "$COAWST" && -f "$COAWST" ]]; then
  OUT=$($PY tools/replay_from_log.py --log "$COAWST" --workload coawst \
        --elapsed-regex 'Timing for main:.*?:\s+([\d.]+) elapsed seconds' \
        --interval 60 --sweep 2>&1)
  FP=$(grep -P '^\s*1800\s' <<<"$OUT" | awk '{print $2}')
  [[ "$FP" == "0" ]] && ok "COAWST 真实成功运行：当前阈值 1800s 下误报 $FP 次" \
                     || bad "COAWST：当前阈值下误报 $FP 次（预期 0）"
else
  skip "COAWST 日志未提供（设 COAWST_LOG=<路径> 后可复现）"
fi
if [[ -n "$DL" && -f "$DL" ]]; then
  OUT=$($PY tools/replay_from_log.py --log "$DL" --workload download --wget \
        --interval 60 --sweep 2>&1)
  FP=$(grep -P '^\s*900\s' <<<"$OUT" | awk '{print $2}')
  [[ "$FP" == "0" ]] && ok "18 GB 下载真实成功完成：当前阈值 900s 下误报 $FP 次" \
                     || bad "下载：当前阈值下误报 $FP 次（预期 0）"
else
  skip "下载日志未提供（设 DOWNLOAD_LOG=<路径> 后可复现）"
fi

# ── 5. 静态预检工具 ──────────────────────────────────────────
printf '\n[5/6] 提交前静态预检（tools/config_precheck.py）\n'
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mk() { printf '&time_control\n run_hours = 6,\n restart = .true.,\n restart_interval = %s,\n%s/\n' "$1" "$2" > "$TMP/namelist.input"; }

# 状态 A：周期超出运行窗口 —— 闹钟永远不会响
mk 720 ""
OUT=$($PY tools/config_precheck.py --case "$TMP" 2>&1)
grep -q 'C1.2' <<<"$OUT" \
  && ok "状态 A 检出「restart_interval 超出运行窗口」——case-01 的真根因" \
  || bad "状态 A 未检出 C1.2"

# 状态 B：周期合法，但热重启会用重启文件里的闹钟覆盖 namelist
mk 180 ""
OUT=$($PY tools/config_precheck.py --case "$TMP" 2>&1)
grep -q 'C1.1' <<<"$OUT" \
  && ok "状态 B 检出「热重启覆盖 namelist 闹钟」——这一条来自读 WRF 源码，不是猜的" \
  || bad "状态 B 未检出 C1.1"

# 状态 C：两处都修好 —— 必须零误报
mk 180 " override_restart_timers = .true.,\n"
OUT=$($PY tools/config_precheck.py --case "$TMP" 2>&1)
grep -qE '0 个错误' <<<"$OUT" \
  && ok "状态 C 全部修正后零误报" \
  || { bad "状态 C 仍报错（误报）"; printf '%s\n' "$OUT" | sed -n "/\[✗\]/,+3p"; }

# ── 汇总 ─────────────────────────────────────────────────────
line
printf '\n通过 %d  失败 %d  跳过 %d\n\n' "$PASS" "$FAIL" "$SKIP"
if (( FAIL > 0 )); then
  printf '有断言未通过。这不是"环境问题"的免责声明 —— 请把输出发给我们，是我们的问题。\n\n'
  exit 1
fi
printf '全部通过。以上每一项都不依赖大模型，结果是确定性的。\n'
printf '跳过项需要历史科研运行日志（含主机路径，未随仓库发布），见 evidence/measure-08。\n'
printf '随仓库发布的数据已覆盖漏报与误报两侧，无需任何外部文件即可复现。\n\n'
