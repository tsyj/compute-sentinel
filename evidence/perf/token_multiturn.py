import json, subprocess
soul = open('soul.txt').read()
msgs = [{"role":"system","content":soul},
        {"role":"user","content":"事故 incident-002，Planner 方案：restart_interval 720->180，L2。材料在 /host-share/compute-sentinel-demo/incident-002/。请按 L2 流程处理。"}]
def call(ms, maxtok=350):
    payload = json.dumps({"model":"qwen3.7-plus","max_tokens":maxtok,"messages":ms})
    cmd = ['docker','exec','-i','agentteams-worker-executor','sh','-c',
      'curl -s -X POST "$AGENTTEAMS_AI_GATEWAY_URL/v1/chat/completions" '
      '-H "Authorization: Bearer $AGENTTEAMS_WORKER_GATEWAY_KEY" -H "Content-Type: application/json" --data-binary @-']
    r = subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=200)
    d = json.loads(r.stdout)
    return d['choices'][0]['message']['content'], d['usage']

# 模拟真实的工具往返：每轮追加助手回复 + 一条工具结果
tool_results = [
  "config-precheck v0.1\n  [✗] C1.2 restart_interval 超出运行窗口，闹钟永远不会触发\n  小计: 1 个错误, 1 个告警",
  "backup OK\nbca929612088d9afb9332ec42adef351  namelist.input",
  "16: restart_interval                    = 180,",
  "config-precheck v0.1\n  小计: 0 个错误, 1 个告警, 0 条提示",
]
cum_p = cum_c = 0
print(f"{'轮':>3}{'prompt':>9}{'completion':>12}{'其中推理':>10}{'累计':>9}")
for i in range(5):
    txt, u = call(msgs)
    cum_p += u['prompt_tokens']; cum_c += u['completion_tokens']
    rt = u.get('completion_tokens_details',{}).get('reasoning_tokens',0)
    print(f"{i+1:>3}{u['prompt_tokens']:>9}{u['completion_tokens']:>12}{rt:>10}{cum_p+cum_c:>9}")
    if i < len(tool_results):
        msgs.append({"role":"assistant","content":txt})
        msgs.append({"role":"user","content":"[工具结果] "+tool_results[i]})
print(f"\n5 轮累计: prompt {cum_p} + completion {cum_c} = {cum_p+cum_c} tokens")
