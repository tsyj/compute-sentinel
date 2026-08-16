import json, subprocess, sys
soul = open('soul.txt').read()
incident = """事故 incident-002，Planner 已给出恢复方案，请按 L2 流程处理。
config_patches: restart_interval: 720 -> 180
材料: /host-share/compute-sentinel-demo/incident-002/namelist.input
预检: python3 /host-share/tools/config_precheck.py --case <目录>
审批人 @jiaoyaobin。"""
payload = json.dumps({"model":"qwen3.7-plus","max_tokens":300,
  "messages":[{"role":"system","content":soul},{"role":"user","content":incident}]})
cmd = ['docker','exec','-i','agentteams-worker-executor','sh','-c',
  'curl -s -X POST "$AGENTTEAMS_AI_GATEWAY_URL/v1/chat/completions" '
  '-H "Authorization: Bearer $AGENTTEAMS_WORKER_GATEWAY_KEY" '
  '-H "Content-Type: application/json" --data-binary @-']
r = subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=180)
try:
    d = json.loads(r.stdout)
    u = d.get('usage', {})
    print(json.dumps(u, ensure_ascii=False))
except Exception:
    print("ERR", r.stdout[:300])
