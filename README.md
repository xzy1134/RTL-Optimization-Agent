# RTL 自动优化 Agent

使用 Python、LangGraph 和 Yosys，在功能等价、满足可配置逻辑深度约束的前提下，搜索低面积 RTL 实现。

当前使用 Mock LLM 驱动真实 EDA 流程，尚未接入 DeepSeek。形式验证仅支持组合电路；逻辑深度是代理指标，不是 ns 延时。

## 目录

| 路径 | 用途 |
|---|---|
| `rtl_agent/` | 当前系统的实现、测试、提示词和说明 |
| `case/` | 从参考 Demo 保留的原始 RTL 测试用例与仿真激励 |
| `tools.env` | 本机 Yosys 和 Liberty 路径，不提交 Git |
| `tools.env.example` | 工具配置模板 |
| `.agents/skills/` | 开发参考 Skill |
| `work/rtl_agent/` | 运行产物，不提交 Git |
| `.venv-m3/` | 本机 Python 环境，不提交 Git |

## 运行

先按照 `tools.env.example` 配置 `tools.env`，填写实际 Yosys 和 Nangate45 路径。
依赖及虚拟环境配置见 [Agent 使用说明](rtl_agent/agent/README.md)。

运行 Mock 搜索：

```bash
.venv-m3/bin/python -B -m rtl_agent.agent \
  rtl_agent/examples/mock_baseline.v --top t \
  --responses rtl_agent/examples/mock_sequence.json \
  --depth-limit 6 --patience 8
```

完整回归测试：

```bash
.venv-m3/bin/python -B -m unittest discover -s rtl_agent/tests -v
```

单独运行综合或验证：

```bash
.venv-m3/bin/python -B -m rtl_agent synth case/rtl/opt_top.v --top opt_top
.venv-m3/bin/python -B -m rtl_agent verify case/rtl/opt_top.v case/rtl/opt_top.v --top opt_top
```

## 开发说明

- [模块说明与阶段记录](rtl_agent/README.md)
- [策略库与特征分析](rtl_agent/strategies/README.md)
- [LangGraph Mock 闭环](rtl_agent/agent/README.md)
- [提示词框架与 M4 待办提醒](rtl_agent/llm/README.md)

接入 DeepSeek 前，需要与用户一起检查并精进初版提示词，再实现真实响应解析和 usage 统计。

项目起源于 [alex00153/opti_demo](https://github.com/alex00153/opti_demo)。原 Demo 的单次优化脚本、旧提示词和 UI 文案已清理；保留 `case/` 和原始 LICENSE。阶段验收文件保留其完成时的历史记录。
