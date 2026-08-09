#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config-precheck v0.1 — 长时算力任务的提交前静态预检

针对 WRF / COAWST 耦合作业，在提交之前、零成本地检出三类会造成
"跑满整个窗口才发现失败"的配置问题：

  C1  restart 闹钟不可达      —— 重启文件恢复的闹钟覆盖 namelist 设置，或周期超出运行窗口
  C2  namelist 变量未注册      —— 变量名不在本 build 的 Registry 中，会导致读配置阶段 FATAL 全崩
  C3  跨文件残留路径          —— 从上一个实验目录复制而来、未改干净的硬编码路径

用法:
    python3 config_precheck.py --case <配置目录> --registry <WRF/Registry 目录> [--json]

退出码: 0=通过  1=有告警  2=有错误
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

# --------------------------------------------------------------------------
# namelist 解析
# --------------------------------------------------------------------------

_ASSIGN = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$')


def strip_comment(line: str) -> str:
    """去掉 Fortran namelist 的行注释（! 之后），但保留引号内的 !"""
    out, in_str, quote = [], False, ''
    for ch in line:
        if in_str:
            out.append(ch)
            if ch == quote:
                in_str = False
        elif ch in '"\'':
            in_str, quote = True, ch
            out.append(ch)
        elif ch == '!':
            break
        else:
            out.append(ch)
    return ''.join(out)


_GROUP = re.compile(r'^\s*&([A-Za-z_][A-Za-z0-9_]*)')


def parse_namelist(path: Path) -> tuple[dict, dict]:
    """
    返回 (flat, groups)：
      flat   = {变量名: (原始值, 行号)}                 —— 供 C1 使用
      groups = {组名: {变量名: (原始值, 行号)}}          —— 供 C2 使用（需要按组判定）
    只取每个变量第一次出现。
    """
    flat, groups, cur = {}, {}, None
    for lineno, raw in enumerate(path.read_text(errors='ignore').splitlines(), 1):
        line = strip_comment(raw)
        s = line.strip()
        if not s:
            continue
        g = _GROUP.match(line)
        if g:
            cur = g.group(1).lower()
            groups.setdefault(cur, {})
            continue
        if s.startswith('/'):
            cur = None
            continue
        m = _ASSIGN.match(line)
        if m:
            name, val = m.group(1).lower(), m.group(2).rstrip(',').strip()
            flat.setdefault(name, (val, lineno))
            if cur is not None:
                groups[cur].setdefault(name, (val, lineno))
    return flat, groups


def as_int(v: str, default=None):
    m = re.match(r'^\s*(-?\d+)', v)
    return int(m.group(1)) if m else default


def as_bool(v: str):
    s = v.strip().lower().strip('.')
    if s.startswith('t'):
        return True
    if s.startswith('f'):
        return False
    return None


# --------------------------------------------------------------------------
# C1 · restart 闹钟可达性
# --------------------------------------------------------------------------

def check_restart_alarm(nml: dict) -> list[dict]:
    """
    WRF 从 restart 起跑时，会从 restart 文件里恢复"写重启闹钟"的剩余秒数，
    该闹钟带的是上一次运行时的 restart_interval。namelist 里新设的值被忽略，
    除非显式打开 override_restart_timers。
    参见 WRF/share/input_wrf.F L291。
    """
    out = []
    window = (as_int(nml.get('run_days', ('0', 0))[0], 0) * 1440
              + as_int(nml.get('run_hours', ('0', 0))[0], 0) * 60
              + as_int(nml.get('run_minutes', ('0', 0))[0], 0)
              + as_int(nml.get('run_seconds', ('0', 0))[0], 0) / 60.0)

    if 'restart_interval' not in nml:
        return [dict(id='C1.0', level='WARN', title='未设置 restart_interval',
                     detail='作业不会产出任何重启文件，一旦中断只能全量重跑。',
                     evidence='namelist 中无 restart_interval')]

    interval = as_int(nml['restart_interval'][0], 0)
    ln_int = nml['restart_interval'][1]
    is_restart = as_bool(nml.get('restart', ('.false.', 0))[0]) is True
    ovr_raw = nml.get('override_restart_timers')
    ovr = as_bool(ovr_raw[0]) if ovr_raw else None

    # C1.1 热重启且未 override —— namelist 的 interval 根本不生效
    if is_restart and ovr is not True:
        out.append(dict(
            id='C1.1', level='ERROR',
            title='热重启会覆盖 namelist 的 restart_interval，闹钟周期不可知',
            detail=('restart = .true. 时 WRF 从重启文件恢复写重启闹钟，'
                    f'namelist 里的 restart_interval = {interval} 分钟不生效。'
                    '若恢复的周期大于本次运行窗口，将一个重启文件都不产出。'),
            evidence=(f'namelist L{ln_int}: restart_interval = {interval}；'
                      f'override_restart_timers '
                      f'{"= " + ovr_raw[0] if ovr_raw else "未设置"}'),
            fix='在 &time_control 中加入 override_restart_timers = .true.，'
                '强制从 namelist 重算闹钟。'
                '注意：变量真名以本 build 的 Registry 为准（见 C2）。',
            source='WRF/share/input_wrf.F L291',
        ))

    # C1.2 周期超出窗口 —— 无论冷热启动都不会触发
    if interval > window:
        out.append(dict(
            id='C1.2', level='ERROR',
            title='restart_interval 超出运行窗口，闹钟永远不会触发',
            detail=f'restart_interval = {interval} 分钟 > 运行窗口 {window:g} 分钟。',
            evidence=f'namelist L{ln_int}',
            fix=f'把 restart_interval 调整到 ≤ {window:g}，或延长运行窗口。',
        ))

    # C1.3 余量过薄 —— 会触发，但离窗口末尾太近
    elif is_restart and ovr is True:
        margin = window - interval
        if 0 <= margin < 10:
            out.append(dict(
                id='C1.3', level='WARN',
                title='最后一次 restart 闹钟距窗口结束余量过薄',
                detail=(f'闹钟预计在第 {interval} 分钟触发，运行窗口 {window:g} 分钟，'
                        f'余量仅 {margin:g} 分钟。'
                        '若步长不能整除、或末段耗时略有偏差，重启文件可能写不出来。'),
                evidence=f'namelist L{ln_int}',
                fix='建议把窗口末尾留出至少一个输出间隔的余量后再确认。',
            ))
    return out


# --------------------------------------------------------------------------
# C2 · namelist 变量是否在本 build 的 Registry 中注册
# --------------------------------------------------------------------------

_RCONFIG = re.compile(r'^\s*rconfig\s+\S+\s+(\S+)\s+(\S+)', re.IGNORECASE)


def load_registry(reg_dir: Path) -> dict[str, set[str]]:
    """返回 {变量名: {它被注册到的 namelist 组}}。"""
    reg: dict[str, set[str]] = {}
    for f in reg_dir.rglob('*'):
        if not f.is_file():
            continue
        try:
            for line in f.read_text(errors='ignore').splitlines():
                m = _RCONFIG.match(line)
                if not m:
                    continue
                name, how = m.group(1).lower(), m.group(2).lower()
                grps = {p.split(',', 1)[1] for p in how.split(';')
                        if p.startswith('namelist,')}
                if grps:
                    reg.setdefault(name, set()).update(grps)
        except Exception:
            continue
    return reg


# WRF 源码里硬编码的 NAMELIST 组，不经 Registry。
# 依据: WRF/frame/module_dm.F:97  NAMELIST /namelist_quilt/ nio_tasks_per_group, nio_groups, poll_servers
_NON_REGISTRY_GROUPS = {'namelist_quilt'}


def check_registry(groups: dict, registry: dict) -> list[dict]:
    if not registry:
        return [dict(id='C2.0', level='WARN', title='Registry 为空，跳过变量注册检查',
                     detail='未能从指定目录解析出任何 rconfig 条目。', evidence='')]

    declared_groups = set().union(*registry.values()) if registry else set()
    out, skipped = [], []

    for gname, vars_ in groups.items():
        if gname in _NON_REGISTRY_GROUPS:
            skipped.append(f'{gname}(源码硬编码)')
            continue
        if gname not in declared_groups:
            skipped.append(f'{gname}(Registry 未声明该组)')
            continue
        for name, (val, ln) in sorted(vars_.items(), key=lambda kv: kv[1][1]):
            if name not in registry:
                near = sorted(r for r in registry
                              if r.startswith(name[:12]) or name.startswith(r[:12]))[:3]
                out.append(dict(
                    id='C2.1', level='ERROR',
                    title=f'namelist 变量 `{name}` 未在本 build 的 Registry 中注册',
                    detail=('WRF 在读取 namelist 阶段遇到未注册变量会直接 FATAL 并 '
                            'MPI_Abort —— 不是 warning，所有 rank 会在启动后立刻退出。'),
                    evidence=f'namelist L{ln}  &{gname}: {name} = {val}',
                    fix=(f'本 build 中最接近的已注册名: {", ".join(near)}' if near
                         else '在 Registry 中确认正确的变量名，或删除该行。'),
                ))
            elif gname not in registry[name]:
                out.append(dict(
                    id='C2.2', level='ERROR',
                    title=f'变量 `{name}` 写在了错误的 namelist 组里',
                    detail='该变量已注册，但不属于当前所在的组，读取时同样会失败。',
                    evidence=f'namelist L{ln}  实际写在 &{gname}',
                    fix=f'应放入 &{" 或 &".join(sorted(registry[name]))}',
                ))

    if skipped:
        out.append(dict(
            id='C2.0', level='INFO', title='以下 namelist 组不做注册检查',
            detail='这些组不由 Registry 驱动，其合法变量在模式源码中硬编码。',
            evidence='、'.join(skipped),
            source='WRF/frame/module_dm.F:97',
        ))
    return out


# --------------------------------------------------------------------------
# C3 · 跨文件残留路径
# --------------------------------------------------------------------------

_ABSPATH = re.compile(r'(/[A-Za-z0-9_][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.\-<>]+)+)')


_STEM = re.compile(r'^(.*?[^0-9])[0-9]+$')


def _stem_of(name: str) -> str | None:
    """`bridge_v3` -> `bridge_v`；无版本后缀则返回 None。"""
    m = _STEM.match(name)
    return m.group(1) if m else None


def check_stale_paths(case_dir: Path, files: list[Path]) -> list[dict]:
    """
    案例目录形如  <实验根>/<分支>/cwd/。从上一个分支 `cp -a` 过来但没改干净的
    绝对路径，会让本次运行把产出写进别的分支、或从别的分支读耦合子配置 ——
    **不报错，安静地污染另一个实验的结果**。

    两条判据（满足其一即判残留）：
      R1  路径落在同一实验根下、但属于另一个分支目录        —— 就地复制的情形
      R2  路径中含有与当前分支同族、但版本号不同的目录名     —— 与物理位置无关，
          例如当前分支 bridge_v3，路径里出现 bridge_v2
    """
    cwd = case_dir.resolve()
    branch = cwd.parent
    exp_root = branch.parent
    stem = _stem_of(branch.name)
    out, seen = [], set()

    for f in files:
        try:
            text = f.read_text(errors='ignore')
        except Exception:
            continue
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = strip_comment(raw) if f.name == 'namelist.input' else raw
            for p in set(_ABSPATH.findall(line)):
                other, rule = None, None
                pp = Path(p)
                # R1 同实验根下的其他分支
                try:
                    if exp_root in pp.parents:
                        rel = pp.relative_to(exp_root)
                        first = rel.parts[0] if rel.parts else ''
                        if first and first != branch.name:
                            other, rule = first, 'R1'
                except Exception:
                    pass
                # R2 同族不同版本的目录名
                if other is None and stem:
                    for comp in pp.parts:
                        if comp != branch.name and _stem_of(comp) == stem:
                            other, rule = comp, 'R2'
                            break
                if other is None:
                    continue
                key = (f.name, other)
                if key in seen:          # 同一文件同一残留分支只报一次，附计数
                    continue
                seen.add(key)
                cnt = sum(1 for _ in re.finditer(re.escape(other), text))
                out.append(dict(
                    id='C3.1', level='ERROR',
                    title=f'`{f.name}` 中残留了另一个分支 `{other}` 的路径',
                    detail=('本次运行会把产出写进另一个分支的目录，或从另一个分支读取'
                            '耦合子配置。这类问题不会报错，会安静地污染另一个实验的结果。'),
                    evidence=f'{f.name} L{lineno}: {p}（该文件中共出现 {cnt} 处 `{other}`）',
                    fix=f'把该文件中全部 `{other}` 替换为当前分支 `{branch.name}`，并核验无残留。',
                    rule=rule,
                ))
    return out


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description='长时算力任务提交前静态预检')
    ap.add_argument('--case', required=True, help='配置目录（含 namelist.input 等）')
    ap.add_argument('--registry', help='WRF/Registry 目录，用于变量注册检查')
    ap.add_argument('--json', action='store_true', help='输出结构化 JSON')
    a = ap.parse_args()

    case = Path(a.case)
    nml_path = case / 'namelist.input'
    if not nml_path.is_file():
        print(f'找不到 {nml_path}', file=sys.stderr)
        return 2

    nml, groups = parse_namelist(nml_path)
    registry = load_registry(Path(a.registry)) if a.registry else set()
    cfg_files = sorted([nml_path] + [p for p in case.glob('*.in')])

    findings = (check_restart_alarm(nml)
                + check_registry(groups, registry)
                + check_stale_paths(case, cfg_files))

    n_err = sum(1 for f in findings if f['level'] == 'ERROR')
    n_warn = sum(1 for f in findings if f['level'] == 'WARN')
    n_info = sum(1 for f in findings if f['level'] == 'INFO')

    if a.json:
        print(json.dumps(dict(case=str(case), registry_vars=len(registry),
                              files=[f.name for f in cfg_files],
                              errors=n_err, warnings=n_warn,
                              findings=findings), ensure_ascii=False, indent=2))
    else:
        print(f'config-precheck v0.1')
        print(f'  配置目录 : {case}')
        print(f'  已检文件 : {", ".join(f.name for f in cfg_files)}')
        print(f'  Registry : {len(registry)} 个已注册变量'
              if registry else '  Registry : 未提供，跳过 C2')
        print()
        if not findings:
            print('  ✅ 未发现问题')
        for f in findings:
            mark = {'ERROR': '✗', 'WARN': '!', 'INFO': 'i'}[f['level']]
            print(f'  [{mark}] {f["id"]}  {f["title"]}')
            print(f'        {f["detail"]}')
            print(f'        证据: {f["evidence"]}')
            if f.get('source'):
                print(f'        依据: {f["source"]}')
            if f.get('fix'):
                print(f'        建议: {f["fix"]}')
            print()
        print(f'  小计: {n_err} 个错误, {n_warn} 个告警, {n_info} 条提示')

    return 2 if n_err else (1 if n_warn else 0)


if __name__ == '__main__':
    sys.exit(main())
