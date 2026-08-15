#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cluster-mcp-server v0.1 — 长时算力任务集群适配器的 MCP Server

把 Agent 需要的集群观测能力封装为 MCP 工具，**全部只读**：

  probe_job_progress   多信号联合的进度判定（progress-probe 的后端）
  tail_log             读日志尾部
  stat_outputs         按 glob 统计产物文件的 mtime / size
  sample_resources     采样进程的 CPU / 内存 / 线程数

协议：MCP over stdio，JSON-RPC 2.0，protocolVersion 2024-11-05
实现：**纯标准库**，不引入 mcp SDK —— 便于在任意受限环境部署，也便于审计

设计约束（与 docs/integration-contract.md 一致）：
  · 只读：不提供任何写、删、终止类工具
  · 路径白名单：只允许访问 --allow 指定的根目录之下
  · 全量审计：每次调用写一条 JSON Lines 审计记录
  · 错误不抛栈：统一返回结构化错误，避免把宿主机路径细节泄露给模型

用法:
    python3 cluster_mcp_server.py --allow /data/xinyuan --allow /home/xinyuan --audit audit.jsonl
"""
from __future__ import annotations
import argparse, glob as globlib, json, os, re, subprocess, sys, time
from pathlib import Path

PROTOCOL = "2024-11-05"
SERVER = {"name": "cluster-mcp-server", "version": "0.1.0"}

ALLOW: list[Path] = []
AUDIT: Path | None = None


# ---------------------------------------------------------------- 安全与审计

class ToolError(Exception):
    def __init__(self, code: str, msg: str):
        self.code, self.msg = code, msg
        super().__init__(msg)


def check_path(p: str) -> Path:
    """路径白名单。越界一律拒绝，且不回显白名单内容。"""
    try:
        rp = Path(p).resolve()
    except Exception:
        raise ToolError("BAD_PATH", "路径无法解析")
    for root in ALLOW:
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    raise ToolError("PATH_DENIED", "路径不在允许范围内")


def audit(tool: str, args: dict, decision: str, reason: str = "") -> None:
    if AUDIT is None:
        return
    rec = dict(ts=int(time.time() * 1000), tool=tool, args=args,
               decision=decision, reason=reason)
    try:
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass          # 审计失败不能影响主流程，但也不静默改变行为


# ---------------------------------------------------------------- workload 适配

ADAPTER_DIR = Path(__file__).resolve().parent.parent / "adapters"
_ADAPTER_CACHE: dict[str, dict] = {}

_FALLBACK = dict(workload="generic", progress_line_regex=".", output_globs=["*"],
                 first_progress_sec=900, stall_sec=1200,
                 notes="适配器文件缺失，已回落到内置兜底值")


def load_adapter(workload: str) -> dict:
    """从 adapters/<workload>.json 加载。文件缺失时回落到兜底并显式标注。"""
    if workload in _ADAPTER_CACHE:
        return _ADAPTER_CACHE[workload]
    f = ADAPTER_DIR / f"{workload}.json"
    try:
        ad = json.loads(f.read_text(encoding="utf-8"))
        ad.setdefault("source", str(f.name))
    except Exception:
        ad = dict(_FALLBACK, source="fallback(内置)", requested=workload)
    _ADAPTER_CACHE[workload] = ad
    return ad


def available_workloads() -> list[str]:
    try:
        return sorted(f.stem for f in ADAPTER_DIR.glob("*.json"))
    except Exception:
        return ["generic"]


def _stat_many(run_dir: Path, patterns: str) -> list[dict]:
    out = []
    for pat in patterns.split("|"):
        for f in sorted(globlib.glob(str(run_dir / pat)))[:200]:
            try:
                st = os.stat(f)
                out.append(dict(path=os.path.basename(f), size=st.st_size,
                                mtime=int(st.st_mtime)))
            except OSError:
                continue
    return out


# ---------------------------------------------------------------- 工具实现

def t_tail_log(path: str, lines: int = 50) -> dict:
    p = check_path(path)
    if not p.is_file():
        raise ToolError("NOT_FOUND", "文件不存在")
    st = p.stat()
    with p.open("rb") as f:
        f.seek(max(0, st.st_size - 64 * 1024))
        tail = f.read().decode("utf-8", "replace").splitlines()[-lines:]
    return dict(path=str(p), size=st.st_size, mtime=int(st.st_mtime), lines=tail)


def t_stat_outputs(run_dir: str, patterns: str = "*") -> dict:
    d = check_path(run_dir)
    if not d.is_dir():
        raise ToolError("NOT_FOUND", "目录不存在")
    files = _stat_many(d, patterns)
    return dict(run_dir=str(d), count=len(files),
                newest_mtime=max((f["mtime"] for f in files), default=0),
                total_size=sum(f["size"] for f in files), files=files[:50])


_PS_CACHE: tuple[float, str] | None = None
PS_TTL = 2.0          # 秒。同一轮巡检里多个作业共用一次扫描


def _ps_snapshot() -> str:
    """
    全机进程表快照，带 TTL 缓存。

    `ps -eo` 扫全机进程要 ~120 ms，比其余三个工具慢两个数量级。
    原来每判定一个作业就扫一次 —— 盯 N 个作业就是每轮 N 次全机扫描，
    这是整套判定里唯一随作业数线性劣化的地方。同一轮巡检内进程表几乎不变，
    因此缓存 2 秒、让同轮的所有作业共用一次扫描。

    TTL 取 2 秒而不是更长：巡检周期是分钟级，2 秒足够覆盖一轮扇出，
    又不会让两轮之间读到陈旧数据。
    """
    global _PS_CACHE
    now = time.monotonic()
    if _PS_CACHE and now - _PS_CACHE[0] < PS_TTL:
        return _PS_CACHE[1]
    try:
        out = subprocess.run(["ps", "-eo", "user,pid,pgid,pcpu,rss,nlwp,args"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        raise ToolError("PS_FAILED", str(e)[:120])
    _PS_CACHE = (now, out)
    return out


def t_sample_resources(pattern: str) -> dict:
    """按命令行片段匹配进程，返回 CPU/内存/线程。只读，不发送任何信号。"""
    if not re.fullmatch(r"[\w./=-]{1,120}", pattern or ""):
        raise ToolError("BAD_PATTERN", "匹配串只允许字母数字与 . / _ - = 字符")
    out = _ps_snapshot()
    procs = []
    for line in out.splitlines()[1:]:
        if pattern in line:
            f = line.split(None, 6)
            if len(f) == 7:
                procs.append(dict(user=f[0], pid=int(f[1]), pgid=int(f[2]),
                                  cpu_pct=float(f[3]), rss_kb=int(f[4]),
                                  threads=int(f[5]), args=f[6][:160]))
    return dict(pattern=pattern, matched=len(procs),
                total_cpu_pct=round(sum(p["cpu_pct"] for p in procs), 1),
                procs=procs[:20])


def t_sample_gpu() -> dict:
    """采样 GPU 利用率。无 GPU 或无 nvidia-smi 时返回 available=False，不报错。"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        rows = [r for r in out.splitlines() if r.strip()]
        if not rows:
            return dict(available=False, detail="nvidia-smi 无输出")
        utils, mems = [], []
        for r in rows:
            u, m = [int(x.strip()) for x in r.split(",")]
            utils.append(u); mems.append(m)
        return dict(available=True, util_pct=max(utils), mem_mb=max(mems), gpus=len(rows))
    except Exception as e:
        return dict(available=False, detail=str(e)[:80])


def decide(sig: dict, prev: dict, first_to: int, stall_to: int,
           job_start_ts: int, interval_sec: int, gpu_required: bool,
           degraded_stall_to: int = 0) -> dict:
    """
    判定内核 —— 纯函数，不做任何 IO。
    抽出来的目的有二：一是可以拿历史快照序列离线回放（tools/replay_signals.py），
    二是判定规则可被单独审计，不必连带读采集代码。

    **进展信号与存活信号是两回事**（2026-08-15 实测修正，见 evidence/measure-06）：

      · 进展信号 = 日志进度行、产物文件 —— 是"在往前走"的正面证据
      · 存活信号 = CPU / GPU 占用 —— 只能区分"卡住"和"死了"，不等于有进展

    原实现把 CPU 占用当作与前两者对等的 OR 票，于是 GPU 训练掉进退化路径时
    （日志静默、无新产物、CPU 100%、GPU 0%）会被投成 RUNNING —— 恰好漏报了
    本项目的头号故障。现在的规则：CPU 只有在**资源画像符合该 workload 的预期**时
    才算进展票；对声明了 gpu_signal.required 的 workload，GPU 闲置而 CPU 打满
    不仅不投 RUNNING，还是退化路径的正面指征。

    对 ROMS / WRF 这类纯 CPU workload 行为不变：CPU 忙依然算进展票，
    因为它们的资源画像本就该是 CPU 忙。

    另外，「GPU 闲置」本身并不总是异常 —— 数据预处理阶段 CPU 忙、GPU 闲是正常的。
    区分办法是看**本次运行里 GPU 是否曾经忙过**：忙过说明预处理已结束，
    此后再出现 CPU 忙 + GPU 闲就是退化，可用更短的 degraded_stall_sec 提前告警；
    没忙过则可能还在预处理，仍走通用的 stall_sec。这个状态位随 snapshot 传递。
    """
    # 不修改调用方传进来的 sig —— 判定是纯函数，降级后的信号随返回值给出。
    # （曾经原地改 sig["resource"]，导致同一个字典被连续判定两次时第二次不再降级。）
    sig = {k: dict(v) for k, v in sig.items()}
    note, degraded = "", False
    gpu_busy_now = sig.get("gpu", {}).get("changed") is True
    gpu_ever_busy = bool(prev.get("gpu_ever_busy")) or gpu_busy_now

    # 资源票的降级：GPU 型 workload 上「CPU 忙 + GPU 闲」不是进展
    if gpu_required and sig.get("resource", {}).get("changed") is True:
        g = sig.get("gpu", {})
        if g.get("changed") is False and g.get("available") is True:
            sig["resource"]["changed"] = False
            sig["resource"]["demoted"] = True
            sig["resource"]["detail"] = sig["resource"].get("detail", "") + \
                "（GPU 闲置，资源画像不符预期，不计进展票）"
            degraded = True
            note = "CPU 打满而 GPU 闲置 —— 符合退化路径特征"

    def out(status, conf, stalled_for, note):
        return dict(status=status, confidence=conf, stalled_for_sec=stalled_for,
                    degradation_suspected=degraded, gpu_ever_busy=gpu_ever_busy,
                    signals=sig, note=note)

    first_round = not prev
    votes = [v["changed"] for v in sig.values() if v.get("changed") is not None]
    stalled_for = prev.get("stalled_for_sec", 0)

    if first_round:
        age = int(time.time()) - job_start_ts if job_start_ts else 0
        if job_start_ts and age > first_to and sig["file"].get("count", 0) == 0:
            return out("STALLED", 0.8, stalled_for,
                       f"启动后 {age}s 未见任何产物，超过首个进度信号阈值 {first_to}s")
        return out("UNKNOWN", 0.0, stalled_for, "首轮采集，已建立基线；判定需下一轮比对")

    if not votes:
        return out("UNKNOWN", 0.0, stalled_for, "全部信号不可采集")

    if any(votes):
        # 每多一个独立信号佐证就加一点，但封顶 0.95 —— 判定永远不宣称确定。
        # （原式 0.6+0.2×票数 在三票时算出 1.2，置信度越界。）
        return out("RUNNING", round(min(0.95, 0.6 + 0.1 * sum(votes)), 2), 0, note)

    stalled_for += int(interval_sec)
    # 退化画像 + GPU 曾经忙过 ⇒ 预处理已结束，可用更短阈值
    eff_to = stall_to
    if degraded and gpu_ever_busy and degraded_stall_to:
        eff_to = min(stall_to, degraded_stall_to)
        note += f"；GPU 曾达工作负载后再度闲置，阈值收紧至 {eff_to}s"
    status = "STALLED" if stalled_for >= eff_to else "RUNNING"
    conf = 0.9 if status == "STALLED" else 0.5
    if degraded:
        conf = round(min(0.98, conf + 0.05), 2)   # 有退化画像佐证，判定更有把握
    if status == "RUNNING":
        note = (note + "；" if note else "") + \
               f"全部信号静止 {stalled_for}s，未达阈值 {eff_to}s，暂不判定卡死"
    return out(status, conf, stalled_for, note)


def t_probe_job_progress(run_dir: str, workload_type: str = "generic",
                         log_path: str = "", prev_snapshot: dict | None = None,
                         proc_pattern: str = "", job_start_ts: int = 0,
                         interval_sec: int = 0) -> dict:
    """
    多信号联合判定。采集失败降级 UNKNOWN，绝不误判 DEAD。
    判定规则见 decide() 的说明。
    """
    d = check_path(run_dir)
    ad = load_adapter(workload_type)
    prog_re = ad.get("progress_line_regex", ".")
    patterns = "|".join(ad.get("output_globs", ["*"]))
    first_to = int(ad.get("first_progress_sec", 900))
    stall_to = int(ad.get("stall_sec", 1200))
    prev = prev_snapshot or {}
    sig, unavailable = {}, []

    # 信号 A · 日志
    try:
        if log_path:
            lp = check_path(log_path)
            st = lp.stat()
            cur = dict(size=st.st_size, mtime=int(st.st_mtime))
            prev_size = prev.get("log", {}).get("size", -1)
            grew = cur["size"] > prev_size
            # 关键：日志"在长"不等于"有进度"。只统计新增片段里匹配进度行的条数，
            # 一个反复刷同一行错误的作业，size 一直涨但进度行数为 0 —— 那不是 RUNNING。
            prog_hits, tail_txt = 0, ""
            if grew and prev_size >= 0:
                with lp.open("rb") as fh:
                    fh.seek(max(0, prev_size))
                    tail_txt = fh.read(512 * 1024).decode("utf-8", "replace")
                try:
                    prog_hits = len(re.findall(prog_re, tail_txt, re.M))
                except re.error:
                    prog_hits = -1        # 正则无效，退化为只看 size
            has_progress = (prog_hits != 0) if (grew and prev_size >= 0) else grew
            sig["log"] = dict(changed=bool(has_progress), size=cur["size"], mtime=cur["mtime"],
                              grew=bool(grew), progress_lines=prog_hits,
                              detail=(f'size={cur["size"]}'
                                      + (f", 新增 {len(tail_txt)}B 内含进度行 {prog_hits} 条"
                                         if grew and prev_size >= 0 else "")
                                      + (" ← 日志在长但无进度行" if grew and prog_hits == 0 else "")))
        else:
            unavailable.append("log")
            sig["log"] = dict(changed=None, detail="未提供 log_path")
    except ToolError as e:
        unavailable.append("log"); sig["log"] = dict(changed=None, detail=f"{e.code}")

    # 信号 B · 产物文件
    try:
        files = _stat_many(d, patterns)
        newest = max((f["mtime"] for f in files), default=0)
        total = sum(f["size"] for f in files)
        p = prev.get("file", {})
        changed = newest > p.get("newest_mtime", -1) or total > p.get("total_size", -1)
        sig["file"] = dict(changed=bool(changed), newest_mtime=newest, total_size=total,
                           count=len(files),
                           detail=f"{len(files)} 个产物, 最新 mtime={newest}, 合计 {total} B")
    except Exception as e:
        unavailable.append("file"); sig["file"] = dict(changed=None, detail=str(e)[:80])

    # 信号 C · 资源
    try:
        if proc_pattern:
            r = t_sample_resources(proc_pattern)
            active = r["total_cpu_pct"] > 50
            sig["resource"] = dict(changed=bool(active), detail=
                                   f'{r["matched"]} 进程, CPU 合计 {r["total_cpu_pct"]}%')
        else:
            unavailable.append("resource")
            sig["resource"] = dict(changed=None, detail="未提供 proc_pattern")
    except Exception as e:
        unavailable.append("resource"); sig["resource"] = dict(changed=None, detail=str(e)[:80])

    # 信号 D · GPU（仅当 adapter 声明该 workload 必须用 GPU 时才采）
    gcfg = ad.get("gpu_signal", {}) or {}
    gpu_required = bool(gcfg.get("required"))
    if gpu_required:
        idle_pct = int(gcfg.get("idle_util_pct", 5))
        g = t_sample_gpu()
        if g.get("available"):
            busy = g["util_pct"] >= idle_pct
            sig["gpu"] = dict(changed=bool(busy), available=True, util_pct=g["util_pct"],
                              mem_mb=g["mem_mb"],
                              detail=f'GPU util {g["util_pct"]}%（闲置阈值 {idle_pct}%）')
        else:
            unavailable.append("gpu")
            sig["gpu"] = dict(changed=None, available=False, detail=g.get("detail", "不可采"))

    # 轮询间隔：优先用调用方给的真实值。原实现在 snapshot 里写死 300，
    # 导致每轮无脑给 stalled_for 加 300s —— 20s 轮询时 3 轮就误报卡死。
    iv = int(interval_sec or prev.get("interval_sec") or 300)

    v = decide(sig, prev, first_to, stall_to, job_start_ts, iv, gpu_required,
               int(gcfg.get("degraded_stall_sec", 0)))

    return dict(status=v["status"], workload_type=workload_type,
                adapter=ad.get("source", "?"), signals=v["signals"],
                confidence=v["confidence"], stalled_for_sec=v["stalled_for_sec"],
                degradation_suspected=v["degradation_suspected"],
                thresholds=dict(first_progress_sec=first_to, stall_sec=stall_to,
                                degraded_stall_sec=int(gcfg.get("degraded_stall_sec", 0)) or None),
                unavailable_signals=unavailable,
                next_poll_sec=150 if unavailable else 300,
                note="；".join(x for x in (v["note"],
                     ("部分信号不可用，判定精度下降" if unavailable else "")) if x),
                snapshot=dict(log=v["signals"]["log"], file=v["signals"]["file"],
                              stalled_for_sec=v["stalled_for_sec"], interval_sec=iv,
                              gpu_ever_busy=v["gpu_ever_busy"]))


TOOLS = {
    "probe_job_progress": (t_probe_job_progress, "多信号联合判定长时任务是否仍在推进；区分进展信号与存活信号（只读）", {
        "type": "object",
        "properties": {
            "run_dir": {"type": "string", "description": "作业运行目录"},
            "workload_type": {"type": "string", "enum": available_workloads(),
                              "description": "workload 类型，决定进度判据与阈值"},
            "log_path": {"type": "string", "description": "日志文件路径（可选）"},
            "proc_pattern": {"type": "string", "description": "进程命令行匹配片段（可选）"},
            "prev_snapshot": {"type": "object", "description": "上一轮返回的 snapshot；缺省表示首轮，只建基线不判定"},
            "job_start_ts": {"type": "integer", "description": "作业启动的 Unix 秒；提供后可在首轮检出'启动后长时间零产物'"},
            "interval_sec": {"type": "integer", "description": "本次与上一轮的实际间隔秒数；不给则沿用上轮 snapshot，再缺省 300"},
        }, "required": ["run_dir"]}),
    "tail_log": (t_tail_log, "读取日志文件尾部（只读）", {
        "type": "object",
        "properties": {"path": {"type": "string"},
                       "lines": {"type": "integer", "minimum": 1, "maximum": 500}},
        "required": ["path"]}),
    "stat_outputs": (t_stat_outputs, "统计产物文件的 mtime 与 size（只读）", {
        "type": "object",
        "properties": {"run_dir": {"type": "string"},
                       "patterns": {"type": "string", "description": "glob，多个用 | 分隔"}},
        "required": ["run_dir"]}),
    "sample_resources": (t_sample_resources, "采样匹配进程的 CPU/内存/线程（只读，不发信号）", {
        "type": "object",
        "properties": {"pattern": {"type": "string", "description": "命令行匹配片段"}},
        "required": ["pattern"]}),
}


# ---------------------------------------------------------------- JSON-RPC

def handle(req: dict) -> dict | None:
    mid, method, params = req.get("id"), req.get("method"), req.get("params") or {}

    if method == "initialize":
        return dict(jsonrpc="2.0", id=mid, result=dict(
            protocolVersion=PROTOCOL, serverInfo=SERVER,
            capabilities=dict(tools=dict(listChanged=False))))

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return dict(jsonrpc="2.0", id=mid, result=dict(tools=[
            dict(name=n, description=d, inputSchema=s) for n, (_, d, s) in TOOLS.items()]))

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS:
            audit(name or "?", args, "deny", "unknown tool")
            return dict(jsonrpc="2.0", id=mid,
                        error=dict(code=-32601, message=f"未知工具 {name}"))
        try:
            out = TOOLS[name][0](**args)
            audit(name, args, "allow", "")
            return dict(jsonrpc="2.0", id=mid, result=dict(
                content=[dict(type="text", text=json.dumps(out, ensure_ascii=False))],
                isError=False))
        except ToolError as e:
            audit(name, args, "deny", e.code)
            return dict(jsonrpc="2.0", id=mid, result=dict(
                content=[dict(type="text",
                              text=json.dumps(dict(error=e.code, message=e.msg),
                                              ensure_ascii=False))], isError=True))
        except TypeError as e:
            audit(name, args, "deny", "bad arguments")
            return dict(jsonrpc="2.0", id=mid, result=dict(
                content=[dict(type="text",
                              text=json.dumps(dict(error="BAD_ARGS", message=str(e)[:160]),
                                              ensure_ascii=False))], isError=True))

    return dict(jsonrpc="2.0", id=mid, error=dict(code=-32601, message="方法不支持"))


def main() -> int:
    global ALLOW, AUDIT
    ap = argparse.ArgumentParser(description="长时算力任务集群适配器 MCP Server")
    ap.add_argument("--allow", action="append", default=[], help="允许访问的根目录，可多次指定")
    ap.add_argument("--audit", help="审计记录输出路径（JSON Lines）")
    a = ap.parse_args()
    ALLOW = [Path(p).resolve() for p in (a.allow or [os.getcwd()])]
    AUDIT = Path(a.audit) if a.audit else None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            resp = dict(jsonrpc="2.0", id=None,
                        error=dict(code=-32700, message="JSON 解析失败"))
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
