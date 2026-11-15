# M1 验收记录

日期：2026-11-15（Asia/Shanghai）。

## 环境与范围

- 仓库原先不存在 `eda_common.py`、`synthesis.py`、`verifier.py`。
- 实际环境：Python 3.14.4；Yosys 0.52，git sha1 `fee39a3284c90249e1d9684cf6944ffbbcbb8f90`。
- `/usr/bin/yosys` 可执行。
- Nangate45：`/home/zhiyan/edatools/NangateOpenCellLibrary_typical.lib`，6,692,032 字节。
- 原 Demo、RTL、`tools.env`、`.env` 均未修改。新增代码及文档仅位于 `rtl_agent/`，运行产物位于已忽略的 `work/rtl_agent/`。
- 没有调用模型 API，没有安装 Claude CLI、LangGraph 或其他依赖，没有执行 Git 提交。

## 自动化测试

执行 `python3 -B -m unittest discover -s rtl_agent/tests -v`：

```text
Ran 14 tests in 1.785s
OK
```

本次完整测试产物：`work/rtl_agent/m1-tests-7t902xnz/`。
该目录由 `.gitignore` 排除；复现测试会产生新的唯一目录。

| 检查 | 验收结果 |
|---|---|
| opt_top 基线及重复综合 | 两次 OK，Area=764.218，Depth=19，配置指纹一致 |
| opt_top 自比对 | PASS |
| 资源共享等价改写，双方具有同名辅助模块 | PASS |
| 加法改为减法 | FAIL，保存 SAT 反例 |
| 端口位宽不一致 | ERROR |
| 语法错误、缺失文件、错误顶层 | ERROR，未崩溃 |
| 缺失 Yosys / Liberty | ERROR |
| 含寄存器 RTL 自比对 | UNKNOWN，明确提示 `$dff` 超出范围 |
| 显式 X 值 | UNKNOWN |
| 实际超时进程 | 验证 UNKNOWN；综合 TIMEOUT（测试用慢工具模拟） |
| 纯连线电路 | Area=0，Depth=0；自比对 PASS |
| 外部黑盒 | 拒绝不完整面积；验证 UNKNOWN |
| 多文件与含空格路径 | 综合 OK；验证 PASS |
| signedness、参数默认值不一致；组合环路 | ERROR |

额外检查了 Python CLI：

```bash
python3 -B -m rtl_agent synth case/rtl/opt_top.v --top opt_top
python3 -B -m rtl_agent verify case/rtl/opt_top.v case/rtl/opt_top.v --top opt_top
```

结果分别为 `OK`（764.218 / 19）和 `PASS`。
对应产物为 `work/rtl_agent/synth-7f5vba43/` 和 `work/rtl_agent/verify-wuri4eu1/`。

## 阶段结论

M1 在文档声明的组合电路范围内通过验收。已提供稳定接口、正反向验证、错误与超时处理及可追溯产物。
不代表八个公开案例或 Hidden Case 已完成优化，也不代表时序电路形式验证已完成。
逻辑深度仍为可替换代理指标，不是实际延时或已确认的官方 Delay 定义。

下一阶段是 M2 的 LangGraph + Mock LLM 状态闭环，设计与验收项见 `README.md`。
