# ZingClaw 增强规划

本文档记录后续增强方向。重点不是堆功能，而是把 ZingClaw 从“能聊天、能调用工具”的 agent，继续推进到“行为透明、可回放、可验证、可演进”的本地 agent runtime。

## 增强一：行为追踪与回归验证

### 命名建议

不建议直接使用“评测与回放层”作为最终名称，因为这个说法来自别人的项目表达，容易显得像照搬。ZingClaw 可以把这部分命名为：

- 推荐名称：行为追踪与回归验证
- 备选名称：运行轨迹与稳定性验证
- 备选名称：Agent 行为证据链
- 备选名称：可回放运行证据系统

我更建议使用“行为追踪与回归验证”。这个名字更贴合 ZingClaw 当前方向：不是单纯做测试，而是把 agent 每次运行时发生了什么记录下来，并用历史任务持续验证新版本有没有退化。

### 这部分解决什么问题

普通测试通常验证函数是否返回预期值，但 agent 的问题更复杂：它会调用模型、选择工具、读写文件、触发安全策略、经历多轮执行。因此只看最终回答是不够的。

“行为追踪与回归验证”的目标是让每次 agent 执行都有证据链：

- 用户输入是什么。
- Agent 进入了哪些运行阶段。
- 调用了哪些工具。
- 工具参数和结果是什么。
- 有没有工具错误。
- 有没有触发安全边界。
- 最终回答是什么。
- 这次行为是否满足预期。

这样做的好处是：

- 调试更容易：失败时不用靠猜，可以直接看 trace。
- 回归更稳定：升级 provider、runner、tool、memory、skill 后，可以重放旧任务验证有没有坏。
- 展示更有说服力：WebUI 或文档里可以展示 agent 的运行过程，而不是只展示一段回答。
- 安全更可审计：危险命令、越界路径、工具错误都能进入记录。

### 和 capability eval / regression eval 的关系

这部分不是单纯为了“多写几个测试”，而是为了给 agent 建立一套持续改进的证据系统。

可以把 eval 分成两类：

- Capability eval：问“这个 agent 现在能不能做更难的事”。这类测试一开始通过率可以比较低，因为它的作用是暴露能力边界，给后续优化提供爬坡目标。
- Regression eval：问“这个 agent 以前能做的事，现在还能不能稳定做到”。这类测试应该接近 100% 通过，因为它的作用是防止改动造成倒退。

trace 本身不是 regression eval，但它是构建 regression eval 的基础。更准确的闭环是：

```text
一次真实运行
  -> 生成 trace
  -> 从 trace 中抽取 replay case
  -> 新版本重新运行 replay case
  -> 生成 fresh trace
  -> verifier / scorecard 判断是否退化
  -> 稳定 case 进入 regression suite
```

也就是说，trace 解决“发生了什么”，replay case 解决“如何重放”，scorecard 解决“是否符合预期”，regression suite 解决“长期防退化”。

Capability eval 和 regression eval 可以互相转化。早期某个任务可能是 capability eval，用来验证“ZingClaw 能不能做到”；当这个任务已经稳定高通过率后，就可以毕业成 regression eval，用来验证“ZingClaw 以后还能不能做到”。

这也是 ZingClaw 选择“行为追踪与回归验证”这个名字的原因：核心不是借鉴某个外部叫法，而是把 agent 的行为变成可观察、可回放、可长期验证的系统。

### 当前已经实现的最小版本

当前项目已经有第一版最小实现，主要文件是：

- `zingclaw/evaluation/trace.py`
- `zingclaw/evaluation/replay.py`
- `zingclaw/evaluation/scorecard.py`
- `zingclaw/evaluation/__init__.py`
- `zingclaw/cli/main.py`
- `tests/unit/test_evaluation_trace.py`
- `tests/unit/test_evaluation_scorecard.py`
- `tests/unit/test_evaluation_replay.py`
- `tests/integration/test_cli_eval.py`

当前能力包括：

- `TraceStore` / `TraceRecorder`：把一次运行写成 `metadata.json`、`trace.jsonl`、`scorecard.json`。
- `RuntimeTraceCapture`：订阅当前 turn 的 runtime events，把正常聊天 / WebUI / bus 触发的一轮 agent 运行自动写成 trace。
- `trace.jsonl`：使用语义化事件格式，直接记录 `user_input`、`status`、`tool_started`、`tool_completed`、`turn_completed`、`turn_result`，避免把重复的 runtime context 嵌入每一行。
- `TraceQuestionSet`：普通问题数据集，只用于批量生成 trace，不包含 grader，也不是 replay case。
- `ReplayCase`：用 JSON 定义一个可重放任务。
- `run_replay_case`：把 replay case 送入当前 `AgentLoop`，生成 fresh trace。
- `evaluate_trace`：基于规则生成 scorecard。
- CLI 命令：`zingclaw eval run CASE`。
- CLI 命令：`zingclaw trace seed` 生成代表性普通问题；`zingclaw trace run QUESTIONS` 使用这些问题批量生成 trace。
- 敏感字段脱敏：例如 `api_key`、`authorization`、`cookie`、`password`、`secret`、`token`。
- trace 配置：默认开启，输出到 `traces/`，可通过 `tracing.enabled` 关闭；默认不记录流式 text delta，避免 trace 文件过大。

当前 scorecard 已经支持检查：

- 最终状态是否符合预期。
- 是否必须有最终回答。
- 是否调用了期望工具。
- 是否调用了禁用工具。
- 是否出现工具错误。
- 是否出现安全拦截。
- 是否执行了包含指定片段的命令。
- 是否触碰了期望文件。

当前 metrics 已经包含：

- `event_count`
- `tool_call_count`
- `tool_error_count`
- `safety_block_count`
- `tools_used`
- `stop_reason`

### 当前 replay case 示例

可以先用一个简单 JSON 文件定义任务：

```json
{
  "name": "readme_summary",
  "input": "请总结这个项目的 README",
  "session_key": "eval:readme_summary",
  "expected_status": "completed",
  "require_final_answer": true,
  "expected_tools": ["read_file"],
  "forbidden_tools": ["write_file"],
  "allow_tool_errors": false,
  "allow_safety_blocks": false
}
```

运行方式：

```bash
zingclaw eval run replay_cases/readme_summary.json
```

如果想指定 trace 输出目录：

```bash
zingclaw eval run replay_cases/readme_summary.json --trace-root .tmp_traces
```

运行后会生成类似结构：

```text
.tmp_traces/
  <trace_id>/
    metadata.json
    trace.jsonl
    scorecard.json
```

### 普通问题数据集与 trace 生成

在正式构建 replay case 之前，可以先用普通问题数据集观察 trace。这个阶段不打分、不判断通过失败，只回答一个问题：ZingClaw 在不同类型问题下会产生什么样的运行轨迹。

生成 starter 问题集：

```bash
zingclaw trace seed
```

默认写入：

```text
eval_questions/starter.json
```

这个文件包含 20+ 个代表性普通问题，覆盖聊天、文件、shell、安全、错误恢复、memory、skill、MCP、cron、web、context 等类别。

使用问题集生成 trace：

```bash
zingclaw trace run eval_questions/starter.json
```

为了避免一次跑太多真实 LLM 调用，可以先限制数量：

```bash
zingclaw trace run eval_questions/starter.json --limit 3
```

也可以只跑某一类：

```bash
zingclaw trace run eval_questions/starter.json --category filesystem
```

如果只想先跑不写文件、不联网的观察性问题：

```bash
zingclaw trace run eval_questions/starter.json --tag read_only --limit 5
```

每条问题都会使用独立 session，例如：

```text
traceq:list_workspace
traceq:python_version
```

每条运行都会生成一个 trace，输出到：

```text
traces/<trace_id>/trace.jsonl
```

这一步和 replay case 的区别是：

- 普通问题数据集：只负责“问什么”，用于观察 trace。
- replay case：负责“问什么 + 期望什么 + 怎么评分”，用于回归验证。

### 完整实现路线

第一阶段：完善 trace 证据链。

需要记录更多关键事件：

- 用户输入。
- session 信息。
- context 构建摘要。
- provider 请求摘要，不记录完整密钥。
- provider 响应摘要。
- runtime events。
- 工具开始、工具完成、工具错误。
- 文件产物。
- 安全拦截。
- 最终回答。

这一阶段的重点是“能看清楚 agent 为什么这样做”。

同时，trace 结构后续应该从当前 JSONL 事件流，逐渐升级为更接近 Trace / Span 的层级结构：

- Trace：一次端到端 agent workflow，例如一次用户请求、一次 replay run、一次长任务恢复。
- Span：workflow 内部的一个子操作，例如 context build、model generation、tool call、guardrail check、memory write、skill activation。
- Trace metadata：记录 `trace_id`、`session_key`、`turn_id`、`goal_id`、`workflow_name`、运行配置、模型名等信息。
- Span timing：记录每个 span 的开始和结束时间，用于分析耗时、卡点和异常。
- Parent/child 关系：让 WebUI 可以展示“模型为什么调用这个工具”“工具结果又怎样影响下一轮模型调用”。

这样做比单纯平铺事件更适合后续可视化，也更接近成熟 agent runtime 的 tracing 设计。

第二阶段：完善 replay case。

当前 replay case 是手写 JSON，后续应该支持两种来源：

- 手动编写 case，用于明确测试某个能力。
- 从历史 trace 中抽取 case，用真实用户任务沉淀回归样本。

建议增加字段：

- `expected_answer_contains`
- `expected_artifacts`
- `max_tool_calls`
- `max_tool_errors`
- `max_runtime_seconds`
- `expected_safety_blocks`
- `expected_skills`
- `expected_memory_updates`

第三阶段：实现 trace 到 case 的抽取。

新增命令建议：

```bash
zingclaw eval extract TRACE_ID --output replay_cases/generated_case.json
```

抽取逻辑：

- 从 trace 中读取用户输入。
- 提取本次使用过的工具。
- 提取最终状态。
- 提取最终回答摘要。
- 提取关键文件产物。
- 生成一个可人工修改的 replay case。

这一步的价值是把日常使用自动沉淀成回归资产。

第四阶段：实现批量回归套件。

新增命令建议：

```bash
zingclaw eval run-suite replay_cases/
```

需要输出：

- 总 case 数。
- 通过数。
- 失败数。
- 平均分。
- 失败 check 列表。
- 每个 case 的 trace 路径。

这一步让 ZingClaw 可以在每次大改后做稳定性验收。

建议把 case 目录分成两类：

```text
replay_cases/
  capability/
    hard_refactor.json
    multi_tool_debug.json
  regression/
    read_file_basic.json
    shell_exec_basic.json
    memory_recall_basic.json
```

`capability/` 允许通过率较低，用来探索能力上限；`regression/` 应该接近全通过，用来守住已经实现过的能力。

第五阶段：加入 baseline 对比。

只看这一次得分还不够，最好能和上一次稳定版本对比。

建议支持：

```bash
zingclaw eval compare baseline.json latest.json
```

对比内容：

- pass rate 是否下降。
- tool error rate 是否上升。
- safety block 是否异常减少或异常增加。
- 平均工具调用次数是否异常变多。
- 平均耗时是否明显变长。
- 哪些 case 从通过变失败。

这一步可以把“感觉变差了”变成“哪里退化了”。

第六阶段：WebUI 可视化。

后续 WebUI 可以增加一个 Trace / Replay 页面：

- trace 列表。
- 单次 trace 时间线。
- 工具调用详情。
- scorecard 结果。
- 失败 check 高亮。
- 一键 replay。
- 一键从 trace 生成 case。

这会让 ZingClaw 的“透明可控”变成用户可见能力。

### 完整测试方案

#### 单元测试

测试 `TraceRecorder`：

- 能创建 trace 目录。
- 能写入 `metadata.json`。
- 能逐行写入 `trace.jsonl`。
- 能写入 `scorecard.json`。
- 敏感字段会被脱敏。

测试 `ReplayCase`：

- 能从 JSON 文件加载。
- `input` 和 `user_input` 都能识别。
- 默认 `session_key` 能自动生成。
- camelCase 和 snake_case 字段都能识别。
- 缺少输入时会报错。

测试 `evaluate_trace`：

- 期望工具被调用时通过。
- 禁用工具被调用时失败。
- 没有最终回答时失败。
- 工具错误不允许时失败。
- 安全拦截不允许时失败。
- 文件触碰和命令片段能被识别。

#### 集成测试

使用 fake provider + fake tool 跑真实 `AgentLoop`：

- 输入 replay case。
- AgentLoop 生成 runtime events。
- TraceRecorder 记录事件。
- Scorecard 生成结果。
- 检查 trace 目录存在。
- 检查 `trace.jsonl` 中确实有工具调用事件。
- 检查 `scorecard.json` 中分数和 checks 正确。

测试 CLI：

```bash
python -m pytest tests/integration/test_cli_eval.py -v
```

应该能看到类似输出：

```text
Replay cli_eval: score 100/100
```

#### 手动验收测试

准备一个 replay case：

```json
{
  "name": "manual_echo",
  "input": "请创建一个 hello.py，并运行它",
  "session_key": "eval:manual_echo",
  "expected_status": "completed",
  "require_final_answer": true,
  "expected_tools": ["write_file", "exec"],
  "expected_files_touched": ["hello.py"],
  "expected_command_substrings": ["python"],
  "allow_tool_errors": false,
  "allow_safety_blocks": false
}
```

运行：

```bash
zingclaw eval run replay_cases/manual_echo.json --trace-root .tmp_traces
```

人工检查：

- 控制台是否显示 score。
- `.tmp_traces/<trace_id>/trace.jsonl` 是否有用户输入、工具调用、最终结果。
- `.tmp_traces/<trace_id>/scorecard.json` 是否列出每个 check。
- 如果故意把 `expected_tools` 改成不存在的工具，scorecard 是否失败。
- 如果故意要求越界文件，是否能在 trace 或 scorecard 中看到安全相关结果。

#### 长期回归指标

这些指标不是为了显得复杂，而是为了从不同角度衡量 agent 是否稳定：

- Replay 通过率：历史任务是否还能完成。
- 平均得分：即使未完全失败，也能看到质量波动。
- 工具错误率：工具调用链是否变脆。
- 安全拦截数：安全策略是否正常生效。
- 禁用工具违规次数：模型是否调用了不该调用的工具。
- 平均工具调用次数：是否出现绕路、重复调用、效率下降。
- 平均运行耗时：改动后是否明显变慢。
- 文件产物命中率：编码任务是否真的产生了预期文件。

指标应该按 eval 类型区分使用：

- Capability eval 更关注能力上限，例如复杂任务通过率、平均得分提升、失败原因分布、需要人工介入的比例。
- Regression eval 更关注稳定性，例如通过率是否接近 100%、是否出现新工具错误、是否出现安全策略退化、旧任务是否从通过变失败。

因此这些指标不是随便设计的，而是服务于两个问题：

- Capability eval：ZingClaw 变强了吗？
- Regression eval：ZingClaw 变强的同时，有没有把以前能做的事情弄坏？

### 当前版本的不足

当前只是最小实现，还不是完整体系：

- 还不能从历史 trace 自动生成 replay case。
- 还没有 `run-suite` 批量回归命令。
- 还没有 baseline 对比。
- 还没有 WebUI trace 查看页面。
- 还没有记录完整文件 diff 或产物快照。
- 还没有 LLM-as-judge，只做了确定性规则检查。
- trace 已经是简化事件轨迹，但还没有正式升级成 Trace / Span 层级模型。
- 还没有区分 capability suite 和 regression suite。

后续优先级建议：

1. 先补 Trace / Span 数据模型，让 trace 结构更适合长期演进。
2. 再做 `run-suite`，并区分 capability suite 和 regression suite。
3. 再做 trace 到 case 的抽取，让真实使用沉淀成测试资产。
4. 然后做 WebUI trace 页面，把透明可控展示出来。
5. 最后再考虑 LLM judge，避免过早引入不稳定评判。

## 增强二：两段式技能调用

这一部分先只记录方向，后续再详细讨论。

### 初步目标

现在 skill 更像是上下文增强材料。后续可以把它改成“两段式技能调用”：

- 第一段：技能发现与选择。
- 第二段：技能展开与执行。

第一段只给模型看 skill 摘要，让模型判断当前任务需要哪个 skill。第二段只把被选中的 skill 的完整说明注入上下文，或者通过内部机制激活。

### 为什么要做两段式

这样设计有几个好处：

- 避免一次性把所有 `SKILL.md` 塞进上下文。
- 让 skill 是否被使用变得可追踪。
- 减少 prompt 噪声。
- 更适合后续 WebUI 展示。
- 可以和“行为追踪与回归验证”联动，检查某个任务是否激活了预期 skill。

### 可能的实现方向

后续可以考虑实现：

- `SkillSummaryIndex`：只加载 skill 名称、描述、适用场景。
- `SkillSelector`：根据用户任务选择候选 skill。
- `activate_skill` 虚拟工具：让模型显式请求启用某个 skill。
- `SkillActivationTrace`：把 skill 激活写入 runtime event 和 trace。
- Context Builder 改造：只注入已激活 skill 的完整内容。
- Skill policy：支持启用、禁用、内置、用户自定义等来源管理。

### 和当前 ZingClaw 的关系

这部分可以放在行为追踪之后做。原因是 skill 是否激活，本身也应该被 trace 记录下来。先有 trace，再做 skill 激活，后续更容易证明 skill 系统真的生效。
