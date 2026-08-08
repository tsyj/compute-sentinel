# Workload Adapters

把领域差异从 Skill 逻辑里剥出来。接入一个新场景 = 写一个 adapter 配置，
不改 Skill、不改 Agent。

每个 adapter 声明：

- 进度行的识别规则
- 主输出文件的路径模式（用于 mtime / size 信号）
- 该 workload 的静止阈值（首个进度信号超时、中途静止阈值）
- restart / checkpoint 的语义（文件在哪、断点怎么读、续跑要改什么）
- 常见故障模式与判据

计划覆盖：`wrf` `roms` `coawst` `mitgcm` `pytorch` `download` `generic`。

规范定稿后，考虑作为通用能力向上游 AgentTeams 社区提交。
