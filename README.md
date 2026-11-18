# RTL 自动优化 Agent

使用 Python、LangGraph 和 Yosys，在功能等价、满足可配置逻辑深度约束的前提下，搜索低面积 RTL 实现。

支持 Mock 和 DeepSeek 客户端驱动真实 EDA 流程，交互入口使用真实 DeepSeek（有 API 费用），完整命令行默认仍为 Mock。形式验证仅支持组合电路；逻辑深度是代理指标，不是 ns 延时。

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

简短菜单入口：

```bash
./run_agent.sh
```

启动后直接输入路径和顶层模块名，即使用真实 DeepSeek。例如：

```text
文件路径：case/bench/popcount3/popcount3_v1_behavioral.v
顶层模块：TopModule
```

文件路径支持相对项目根目录的路径、绝对路径、`~/` 和空格，不加引号。自选文件的逻辑深度上限自动设为原始 RTL 的综合深度；最多 5 个候选、5 次请求、50,000 Token，搜索上限 300 秒（另有一次基线深度预分析）。
入口只运行用户指定的 RTL 文件，固定使用真实 DeepSeek（有费用）。Mock 仍可通过完整命令行使用，用于离线回归。
DeepSeek 自动读取项目根目录 `.env` 中的密钥，也支持当前终端的 `DEEPSEEK_API_KEY`；菜单不会显示密钥。每次运行都保留有限请求和 Token 预算。
需要自定义更多参数时仍可使用 [完整命令行](rtl_agent/agent/README.md)。

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

DeepSeek 配置可按 [.env.example](.env.example) 写入本地 `.env`，也支持环境变量 `DEEPSEEK_API_KEY`。不要覆盖已有 `.env`，在本地编辑需要的字段即可。支持请求、Token 和运行时间预算；详细说明见 [模型接入说明](rtl_agent/llm/README.md)。初版提示词仍需结合真实实验结果继续精进。

项目起源于 [alex00153/opti_demo](https://github.com/alex00153/opti_demo)。原 Demo 的单次优化脚本、旧提示词和 UI 文案已清理；保留 `case/` 和原始 LICENSE。阶段验收文件保留其完成时的历史记录。
