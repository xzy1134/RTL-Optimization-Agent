# 组合候选的失败诊断

默认恢复历史成功实验的策略提示、结构证据和完整跨轮记忆。完整 CLI 加 `--diagnostics` 显式启用 M5 诊断反馈和已有选路。`--legacy-feedback` 保留为历史默认模式的兼容参数。每轮先由 M1 判定，再根据 record.reason 选择读哪些工件。record.json 保存评估，diagnosis.json 保存诊断，下一轮 request.json 和 messages.json 可以核查实际反馈。成功候选沿用原记录。工具错误、格式错误、重复候选沿用已有反馈。

M1 从展平、ABC 映射后的顶层执行 ltp，读取最长拓扑路径长度。mapped.json 提供连接和方向，深度存于 result.json。P0 用位 ID 归并别名，按单元输入位到输出位建立单位权边，拓扑排序后回溯最多三条并列最长路径。路径可以止于内部线。用库端口签名和 Yosys 导出的 abc9_box 标志保守筛选单元，结果须与该次 M1 深度一致。paths(run_dir) 返回实例、输入输出引脚、两端位 ID、端口和保留下来的公开网名。位 ID 仅在本次网表内有效。ABC 可能丢失 RTL 表达式对应关系，源码位置标记 unknown。路径表示拓扑层数，实际传播时间需要 STA。

M2 的通用网表特征用于发现算术、位宽、选择结构等候选方向。ABC 映射后不能直接按通用网表的位 ID 跨网表关联。深度失败时按顶层端口识别逻辑锥，比较原始、父版和候选各一条路径。面积失败读取 M2 的 mapped_cell_counts 和本次库快照中的单元面积，仅发面积变化最显著的五类。相同直方图不能证明连线相同。形式失败读取 M1 的 WaveJSON 反例，用 gold/gate 对区分预期和实际输出。actual_edit 在同一条 feedback 中保存。诊断信息缺失时标记 unavailable。

搜索默认从合法最佳出发，保留定期从基线重启的规则。遇到反例或深度超限，可以依据上轮 diff 修复。连续两次诊断定位到同一输出或同一单元族时，优先从合法最佳换一个局部结构，并降低上次主要策略的顺序。单元族是面积搜索的粗粒度线索。失败设计不参与最佳版本晋升。只有原始的组合证明 PASS 和完整可比的面积、深度指标可以用于晋升。

真实对照运行命令为 `.venv-m3/bin/python -B -m rtl_agent.experiments.diagnostic_ab`。它会调用付费 API。两个电路各跑旧、新两组，每组最多三次 API 请求、四万个 Token、一百二十秒。comparison.json 包含每组报告位置和是否含诊断。等价通过率分母为返回的候选数。无效或重复比例按总轮数计算，包括响应格式错误和无法通过证明的候选。格式正确但面积无改善、深度超限和 NO_CHANGE 单列在 reasons 中。诊断耗时测量 record 节点的诊断计算及工件写入。

参考 [Dr.RTL Timing Analyzer](https://github.com/hkust-zhiyao/DR_RTL/blob/main/.claude/agents/rtl-timing-analyzer.md)、[Dr.RTL 评估脚本](https://github.com/hkust-zhiyao/DR_RTL/blob/main/syn_flow_eda/run_design.py) 和 [Yosys 0.52 ltp](https://github.com/YosysHQ/yosys/blob/v0.52/passes/cmds/ltp.cc)。

本次小样本实验的实际工件位于 work/rtl_agent/diagnostic-ab-taabqux3/comparison.json。两个电路各轮流运行旧版和新版，模型均为 deepseek-flash。barrel 两组的有效面积改善均为 0%，新版有三个等价候选，面积都回到 39.368。addsubz 旧版改善 0%，新版改善 0.35%，最佳面积 75.278、深度 10。每个条件仅运行一次，有效样本量不足以估计成功率变化。历史实验中的最佳设计不参与这四次独立搜索。

具体连接、历史失败复核结果分别保存在 work/rtl_agent/diagnostic-replay-nhm9f5lz/paths.json、addsubz.json 和 barrel.json。诊断不涉及额外模型请求。搜索失败后的修复采用差异节选；很长的修改可能无法从节选还原。建议下一批在相同首轮候选后分叉并重复实验，以减少首轮生成差异的影响。
