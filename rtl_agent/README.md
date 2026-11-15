# RTL Agent：M1 EDA 工具层

Stage 2 now provides an independent strategy library and generic-netlist analysis.
See [strategy usage](strategies/README.md) and [stage acceptance](STRATEGIES_ACCEPTANCE.md).
The M1 synthesis and verification interfaces and recipes are unchanged.

M3 adds a serial LangGraph Mock search loop. See [Agent usage](agent/README.md).
See [M3 acceptance](M3_ACCEPTANCE.md) for measured candidate results and test coverage.
Use `.venv-m3/bin/python` for Agent commands and the combined test suite.

Initial model prompts now live in `llm/prompts/`; rounds save `messages.json`.
Pending reminder: review and refine these prompts with the user before real
DeepSeek integration. See [prompt scaffold and M4 reminder](llm/README.md).

本目录独立于原 Demo，不依赖 Claude CLI、模型 API 或第三方 Python 包。
当前交付范围是 M1：可复现综合、组合电路形式等价验证及错误处理。
Python 需要 3.10+；本机验收环境为 Python 3.14.4、Yosys 0.52、Nangate45。

## 运行

从仓库根目录执行：

```bash
python3 -B -m rtl_agent synth case/rtl/opt_top.v --top opt_top
python3 -B -m rtl_agent verify case/rtl/opt_top.v case/rtl/opt_top.v --top opt_top
python3 -B -m unittest discover -s rtl_agent/tests -v
```

配置读取顺序为显式参数、`tools.env`、环境变量、默认值。
`TOOLS_ENV` 或 `--tools-env` 可指定配置文件；工具名采用 `YOSYS_BIN`，库路径采用 `LIB_FILE`。
命令支持 `--yosys`、`--liberty`、`--timeout`（秒）、`--outdir`。
综合默认超时 60 秒；验证的准备及 SAT 共用一个 60 秒预算。
顶层模块必须显式提供，不依赖正则猜测。

多文件综合用重复的 `--source path.v`；验证时分别通过 `--source` 和
`--candidate-source` 指定两边的辅助源文件。默认只使用参数默认值。
M1 暂不支持 `include`、自定义宏参数、参数覆盖和非普通标识符顶层名。

Python 接口：

```python
from rtl_agent.eda import EDAConfig, synthesize_rtl, verify_rtl

cfg = EDAConfig.from_tools_env(timeout_seconds=60)
baseline = synthesize_rtl("case/rtl/opt_top.v", "opt_top", config=cfg)
proof = verify_rtl("original.v", "candidate.v", "top", config=cfg)
if proof.status == "PASS":
    candidate = synthesize_rtl("candidate.v", "top", config=cfg)
```

接口返回 dataclass，可使用 `dataclasses.asdict()` 转成可 JSON 序列化的字典。
候选语法、模块、文件或工具错误通过结果返回；输出目录无法创建等宿主文件系统错误仍可能抛出异常。
配置和结果定义在 `eda/common.py`；不导入原 Demo 的配置或模型模块。

## 综合契约

固定流程：

```text
read_liberty -lib
read_verilog -sv
hierarchy -check -top
flatten -noscopeinfo
proc; opt; techmap; opt
abc -liberty
stat -liberty
ltp （仅选择展开后的顶层）
```

展开层次是为了让 `ltp` 覆盖整个顶层。所有候选使用同一套流程，不因候选名称选择优化脚本。
输出 `area`、`logic_depth`、`timing_metric="logic_depth"` 和 `config_fingerprint`。
工艺库单位决定面积单位；当前 Nangate45 报告为 µm²。
**逻辑深度不是 ns，也不是静态时序分析结果。** 当前不设置时钟、负载或真实延时约束。

配置指纹覆盖 Yosys 版本、Liberty 内容哈希、顶层、指标和规范化综合流程。
源码另有独立 SHA256；后续缓存键必须组合配置指纹和所有源文件哈希，不能只用文件名。
每次运行复制 Liberty 和 RTL，保留完整脚本，避免运行中源文件变化影响结果。
同配置重复综合默认案例已验证结果一致。当前尚未实现跨运行缓存。

所有单元必须映射到指定 Liberty 中的单元；发现残留内部单元或其他黑盒即返回错误，
避免报告缺少寄存器或其他逻辑面积的数字。M1 不实现 `dfflibmap`，不能据此宣称时序电路 PPA 已受支持。
纯连线电路支持面积 0、逻辑深度 0，后续评价器必须单独处理零分母。

综合状态：`OK`、`TIMEOUT`、`ERROR`。指标不可用时为 `None`。

## 形式验证契约

M1 仅支持可展开、无状态的组合 RTL。两边分别解析、`proc`、展开和
`check -assert`，保存到不同设计存储，再以 `gold` / `gate` 导入；顶层及辅助模块重名不会冲突。
比较顶层端口名称、方向、位宽、signedness、索引范围和参数默认值。
该检查只证明默认参数实例，不证明所有参数取值。

通过支持范围检查后，构造全输出 miter：

```text
miter -equiv -make_outputs -flatten gold gate miter
hierarchy -top miter
select -module miter
sat -enable_undef -set-def-inputs -prove trigger 0 -show-inputs -show-outputs -dump_json counterexample.json
```

输入无约束遍历所有二值组合；SAT 启用未定义值建模。显式 X/Z 连接被保守拒绝。
不依赖内部信号同名匹配，不忽略未知单元，不施加模型生成的假设，也不使用随机仿真作为证明。
仅凭未证明的 `$equiv` 单元不能断言不等价；这也是此版选择完整 miter 获取反例的原因。
工具语义参考 [Yosys 官方形式等价命令文档](https://yosyshq.readthedocs.io/projects/yosys/en/v0.59/cmd/index_passes_equiv.html)，
具体脚本选项以本机 Yosys 0.52 的 `help miter` 和 `help sat` 为准。

| 状态 | 含义 |
|---|---|
| `PASS` | SAT 明确报告无反例，全部输出等价 |
| `FAIL` | SAT 明确找到输出差异，且生成了反例 JSON |
| `UNKNOWN` | 超时、无明确结论，或超出 M1 支持范围 |
| `ERROR` | 语法、接口、检查或工具执行错误 |

寄存器、锁存器、存储器、黑盒、双向端口和不支持的单元不会被当成组合逻辑通过。
这些情况应继续开发相应验证流程，而不是放宽通过条件。
全输出 SAT 可能在复杂算术上超时；M1 不承诺证明任意 Verilog。
`FAIL` 的反例在本轮 `counterexample.json`，详细输入/输出也记录于 `proof.log`。

## 产物与超时

默认写入仓库已忽略的 `work/rtl_agent/`。每次调用创建唯一子目录：

- 源码快照和 `manifest.json`：原路径、快照路径及 SHA256。
- `config.json`：版本、配置、验证假设或综合配置指纹。
- `*.ys` / `*.log`：实际脚本和完整日志。
- `result.json`：结构化结果及 `perf_counter()` 测得的本次接口运行时间。
- 综合额外保留 `library.lib`、库单元和映射网表 JSON。
- 验证额外保留准备阶段的 JSON/RTLIL 和不等价反例。

子进程不经过 Shell，Yosys 路径按其脚本语法引用；超时终止整个进程组，包括 ABC 子进程。
命令行退出码：`OK/PASS=0`、`FAIL=2`、`UNKNOWN=3`、`ERROR=4`、`TIMEOUT=5`。
这些运行时间是 EDA 接口成本；M2 将另行统计包含所有节点和最终报告的 Agent 总时间。

## 下一阶段 M2

M1 验收记录见 `M1_ACCEPTANCE.md`。下一步再引入 LangGraph 和 Mock LLM，保持 EDA 接口不变：

1. State 保存原始/当前/最佳 RTL 路径、基线与最佳指标、约束、迭代数、反馈摘要和历史。
2. 每轮生成独立候选；先验证，只有 `PASS` 进入综合，只有完整指标进入评价。
3. 评价器使用内部代理函数 `0.5*r_area + 0.3*r_depth`，与官方排名评分明确区分。
   延时约束可配置，零基线单独处理；合法但无收益候选不覆盖历史最佳。
4. 初始最佳是原始 RTL。无有效改进时原样返回，保留原因；不修改原始文件。
5. 历史记录包含路径、哈希、验证结果、指标、策略、状态和成本，状态路由由 Python 决定。
6. 设置迭代、连续无改善、Token 和 Runtime 停止条件，保证每条错误分支都能到达结束节点。
7. Mock 客户端与未来 DeepSeek 客户端采用相同响应契约：候选文本、usage 和调用诊断。
   未知 usage 单独标记；M1 的零 API 调用不代表未来失败请求零消耗。

M2 验收应覆盖无效候选、证明失败、合法改善、重复候选、预算停止和最终报告。
M3 再接入课程提供的真实 URL、模型名及密钥；本阶段不读取或修改密钥文件。
