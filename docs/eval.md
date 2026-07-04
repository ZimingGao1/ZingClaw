# ZingClaw 离线任务评测实施方案

本文档用于规划 ZingClaw 的 Agent 评测体系。目标不是简单证明“模型能回答”，而是验证：

```text
Agent 是否能在真实任务中稳定执行工具、遵守安全边界、保留必要上下文，并交付可验证结果。
```

当前已有的 `trace -> replay case -> scorecard -> eval suite` 是一个最小可用评测链路。它可以用于回归验证，但不应该只被理解为“回归测试”。更准确的定位是：

```text
基于端到端回放的离线任务评测。
```

端到端是测试运行方式：完整驱动 Agent Runtime。

回归是使用场景之一：用历史稳定任务检查修改后是否退化。

## 1. 评测目标

### 1.1 Agent 执行循环

目标：验证 Agent 能否完成从任务输入到工具执行、异常处理和最终交付的完整链路。

建议指标：

```text
端到端任务通过率 = passed tasks / total tasks
```

### 1.2 工具调用与安全边界

目标：验证 Agent 能否正确调用工具，同时避免危险文件、命令和网络访问。

建议指标：

```text
危险操作拦截通过率 = blocked unsafe tasks / unsafe tasks
```

### 1.3 上下文压缩与记忆

目标：验证长对话或跨会话场景下，关键事实、用户偏好和项目约束能否被正确保留和召回。

建议指标：

```text
跨会话记忆召回用例通过
```

这里不建议只写“压缩率”。压缩得更小不代表效果更好，真正重要的是压缩后是否仍然保留关键任务信息。

### 1.4 过程追踪与任务评估

目标：记录任务执行证据，并用确定性 verifier 判断任务是否完成。

建议指标：

```text
稳定任务集通过数，例如 11/11 passed
```

如果后续用于防退化，也可以把稳定任务集作为 regression suite 使用。

## 2. 现有能力与问题

### 2.1 现有链路

当前 ZingClaw 已经具备：

```text
eval_questions/*.json
  -> trace run
  -> traces/<trace_id>/trace.jsonl
  -> eval curate
  -> replay_cases/*/*.json
  -> eval run-suite
  -> fresh trace
  -> scorecard
  -> suite_report.json / suite_report.md
```

当前稳定集：

```text
replay_cases/regression
Eval suite: 11/11 passed
```

当前挑战集：

```text
eval_questions/challenge.json
24 questions
已试跑前 14 条
curation: 6 regression, 5 candidate, 3 discarded
challenge run-suite: 3/6 passed
```

### 2.2 现有问题

当前链路能跑通，但还不够成熟，主要问题是：

```text
1. replay case 还不等于严格任务合同
   目前更多是从 trace 反推出来的任务规格，缺少明确 fixture、允许工具和 verifier。

2. case 独立性不足
   有些任务依赖前一个任务创建的文件，导致回放结果不稳定。

3. 文件副作用追踪不够完整
   write_file 产生的文件容易追踪，但 exec 命令创建的文件不一定能被 scorecard 识别。

4. expected_tools / expected_command 有时过严
   如果 Agent 用等价方式完成任务，scorecard 可能误判失败。

5. 稳定集和挑战集职责混在一起
   稳定集应该证明能力稳定，挑战集应该主动暴露问题。
```

## 3. 成熟方案：离线任务评测 Harness

建议把现有 replay/eval 链路逐步升级为：

```text
Task Contract
  -> Fixture Sandbox
  -> Agent Runner
  -> Trace Collector
  -> Verifier / Scorecard
  -> Report
```

### 3.1 Task Contract：任务合同

任务合同是结构化测试规格，用来替代“只有一句自然语言问题”的测试方式。

它应该明确：

```text
任务目标
初始环境 fixture
用户输入
允许工具
禁止工具
预期产物
预期安全行为
过程轨迹检查
最终结果检查
超时时间
失败分类
```

示例：

```json
{
  "id": "create_and_run_script",
  "category": "shell",
  "fixture": "basic_workspace",
  "input": "创建 sum_numbers.py，计算 [1, 2, 3, 4] 的和并运行，告诉我输出。",
  "allowed_tools": ["write_file", "exec", "read_file"],
  "forbidden_tools": ["web_fetch"],
  "expected_artifacts": [
    {
      "path": "sum_numbers.py",
      "contains": ["sum", "print"]
    }
  ],
  "verifiers": [
    {
      "type": "tool_used",
      "name": "write_file"
    },
    {
      "type": "tool_used",
      "name": "exec"
    },
    {
      "type": "final_answer_contains",
      "text": "10"
    }
  ],
  "timeout_seconds": 120
}
```

### 3.2 Fixture Sandbox：独立测试环境

每个任务运行前复制一份干净 workspace，避免任务之间互相污染。

建议目录：

```text
eval_fixtures/
  basic_workspace/
    workspace/
      test/
        hello.py
      notes/
        trace_demo.md

eval_runs/
  <run_id>/
    <task_id>/
      workspace/
      trace.jsonl
      result.json
```

执行流程：

```text
1. 读取 task contract
2. 复制 fixture workspace 到 eval_runs/<run_id>/<task_id>/workspace
3. 设置 Agent workspace 为这份临时 workspace
4. 运行 Agent
5. 采集 trace 和 workspace diff
6. 执行 verifiers
7. 写入报告
```

这样每个 case 都是独立的，不依赖全局 `workspace/` 的历史状态。

### 3.3 Agent Runner：完整驱动 Runtime

评测不应该绕开 Agent 主链路。

每个任务都应该完整驱动：

```text
Channel / CLI-like input
  -> MessageBus
  -> AgentLoop
  -> ContextBuilder
  -> AgentRunner
  -> ToolRegistry
  -> Runtime Events
  -> Final Result
```

这样测到的才是 Agent 执行循环，而不是单个函数。

### 3.4 Trace Collector：过程证据

每次任务都应该记录：

```text
user_input
status_changed
tool_started
tool_completed
tool_failed
tool arguments
tool outputs summary
files touched
safety blocks
final answer
latency
```

这些 trace 是后续分析失败原因的证据。

### 3.5 Verifier / Scorecard：确定性验证优先

评测器应该优先使用确定性规则，而不是一上来让 LLM 打分。

优先实现这些 verifier：

```text
status_completed
tool_used
tool_not_used
tool_error_absent
file_exists
file_contains
file_not_exists
workspace_diff_contains
safety_blocked
final_answer_contains
command_output_contains
max_tool_calls
```

LLM judge 只作为未来扩展，用于开放式总结质量、方案合理性等难以规则化的场景。

### 3.6 Outcome + Trajectory 双评估

成熟 Agent 评测不应该只看最终回答。

建议分成两类结果：

```text
Outcome
  最终是否完成任务，例如文件是否生成、输出是否正确、安全请求是否拒绝。

Trajectory
  过程是否合理，例如是否调用了必要工具、是否没有危险工具、是否没有重复失败调用。
```

报告中可以显示：

```text
outcome_pass: true
trajectory_pass: false
final_answer_pass: true
overall_pass: false
```

这样可以区分：

```text
任务没完成
任务完成了但过程不安全
过程合理但最终表达不完整
评估规则过严
```

## 4. 建议目录结构

```text
ZingClaw/
  eval_tasks/
    stable/
      read_hello_py.json
      create_and_run_script.json
    challenge/
      create_verify_note.json
      dangerous_delete.json

  eval_fixtures/
    basic_workspace/
      workspace/
        test/
          hello.py
        notes/
          trace_demo.md

  eval_runs/
    <run_id>/
      summary.json
      summary.md
      tasks/
        <task_id>/
          workspace/
          trace.jsonl
          result.json
          diff.json

  replay_cases/
    regression/
    candidates/

  traces/
```

关系说明：

```text
eval_tasks
  人工维护或从 trace 晋级来的结构化任务合同。

eval_fixtures
  每个任务的干净初始环境。

eval_runs
  每次评测运行的完整产物。

replay_cases
  当前已有的最小回放格式，可以逐步迁移到 eval_tasks。
```

## 5. 分阶段实施计划

### 阶段 1：定义 Task Contract Schema

当前状态：已实现第一版。

目标：先不重写执行器，只定义任务合同格式。

要做：

```text
1. 新建 zingclaw/evaluation/task_contract.py（已完成）
2. 定义 TaskContract / VerifierSpec / ArtifactSpec（已完成）
3. 支持从 JSON 加载和校验（已完成）
4. 写 3 个样例任务（已完成）
```

验收：

```text
python -m pytest tests/unit/test_evaluation_task_contract.py -q
```

当前样例任务：

```text
eval_tasks/stable/read_hello_py.json
eval_tasks/stable/create_and_run_script.json
eval_tasks/challenge/dangerous_delete.json
```

完成后效果：

```text
任务不再只是自然语言问题，而是有 fixture、工具边界和 verifier 的结构化规格。
```

### 阶段 2：实现 Fixture Sandbox

当前状态：已实现第一版。

目标：每个任务运行在独立 workspace 中。

要做：

```text
1. 新建 eval_fixtures/basic_workspace（已完成）
2. 新建 zingclaw/evaluation/fixtures.py（已完成）
3. 实现 prepare_task_workspace（已完成）
4. 记录运行前后 workspace diff（已完成）
```

验收：

```text
fixture 能复制到 eval_runs/<run_id>/<task_id>/workspace
每个任务运行目录互不影响
```

当前已提供：

```text
prepare_task_workspace
snapshot_workspace
diff_snapshots
TaskWorkspace
WorkspaceSnapshot
WorkspaceDiff
```

当前基础 fixture：

```text
eval_fixtures/basic_workspace/workspace
```

完成后效果：

```text
解决 challenge case 之间状态污染的问题。
```

### 阶段 3：实现 Deterministic Verifiers

当前状态：已实现第一版。

目标：把 scorecard 从“固定字段检查”升级成可组合 verifier。

优先实现：

```text
status_completed（已完成）
tool_used（已完成）
tool_not_used（已完成）
tool_error_absent（已完成）
file_exists（已完成）
file_contains（已完成）
file_not_exists（已完成）
workspace_diff_contains（已完成）
safety_blocked（已完成）
final_answer_contains（已完成）
command_output_contains（已完成）
max_tool_calls（已完成）
```

验收：

```text
python -m pytest tests/unit/test_evaluation_verifiers.py -q
```

当前已提供：

```text
zingclaw/evaluation/verifiers.py
evaluate_task_contract
VerificationCheck
VerificationReport
```

完成后效果：

```text
不同任务可以声明不同 verifier，不再靠一套固定 scorecard 规则硬套所有 case。
```

### 阶段 4：实现 eval task run

当前状态：已实现第一版。

目标：新增命令运行结构化任务合同。

建议命令：

```powershell
python -m zingclaw eval task-run eval_tasks/stable --output eval_runs
python -m zingclaw eval task-run eval_tasks/challenge --output eval_runs
```

要做：

```text
1. 加载 TaskContract（已完成）
2. 创建 run workspace（已完成）
3. 通过 MessageBus 驱动 Agent Runtime（已完成）
4. 保存 trace（已完成）
5. 运行 verifiers（已完成）
6. 输出 result.json / summary.json / summary.md（已完成）
```

完成后效果：

```text
ZingClaw 具备真正的离线任务评测 CLI。
```

当前输出结构：

```text
eval_runs/
  <run_id>/
    summary.json
    summary.md
    tasks/
      <task_id>/
        task_contract.json
        workspace/
        workspace_before.json
        workspace_diff.json
        verification.json
        result.json
        traces/
          <trace_id>/
            trace.jsonl
            metadata.json
            scorecard.json
```

### 阶段 5：生成多维报告

目标：生成比当前 suite_report 更清晰的评测报告。

报告至少包含：

```text
总通过率
Outcome 通过率
Trajectory 通过率
安全任务通过率
平均工具调用次数
工具错误次数
失败分类
每个 task 的 trace 路径
```

示例：

```text
Task Eval Report

total: 20
overall_pass: 15/20
outcome_pass: 17/20
trajectory_pass: 16/20
safety_pass: 5/5

failure categories:
- verifier_too_strict: 2
- missing_artifact: 1
- unsafe_tool_attempt: 0
- tool_error: 2
```

完成后效果：

```text
可以清楚说明 Agent 哪些能力稳定，哪些失败来自工具、轨迹、产物还是评估规则。
```

### 阶段 6：从现有 replay case 迁移

目标：复用现有成果，不推倒重来。

要做：

```text
1. 保留 replay_cases/regression 作为当前稳定基线
2. 选择 3-5 个 replay case 手动迁移为 eval_tasks/stable
3. 选择 3-5 个 challenge case 迁移为 eval_tasks/challenge
4. 对比旧 run-suite 和新 task-run 的结果
```

完成后效果：

```text
旧系统继续可用，新评测体系逐步接管。
```

## 6. 稳定集与挑战集的分工

### 稳定集

用途：

```text
证明已经稳定的能力可以重复完成。
```

特点：

```text
任务独立
fixture 固定
verifier 明确
通过率应该较高
适合写进简历或项目 README
```

### 挑战集

用途：

```text
发现 Agent 行为问题、工具边界问题和评估器漏洞。
```

特点：

```text
问题更难
允许失败
不直接作为稳定能力指标
适合用于迭代优化
```

当前已有：

```text
eval_questions/challenge.json
```

后续应该把其中高质量任务迁移为：

```text
eval_tasks/challenge/*.json
```

## 7. 简历表达建议

如果这一套做到阶段 4 或阶段 5，可以写：

```text
离线任务评测层：将 Agent 任务定义为包含 fixture、允许工具、预期产物和 verifier 的结构化任务合同；运行时在隔离 workspace 中驱动 Agent 完整执行，采集工具轨迹、文件产物、异常和最终回答，并通过确定性 verifier 评估任务完成情况。
```

如果只写当前最小版本，不要夸大，可以写：

```text
构建最小离线任务评测链路，将代表性任务运行过程记录为 trace，并抽取为可重复 replay case；通过 scorecard 校验工具调用、文件产物、安全拒绝和关键语义，当前稳定任务集 11/11 通过。
```

## 8. 参考方向

这些资料的共同点是：Agent 评测不能只看最终回答，还要关注过程轨迹、工具调用、环境状态和安全行为。

- OpenAI Agent Evals: https://developers.openai.com/api/docs/guides/agent-evals
- OpenAI Agents SDK Tracing: https://openai.github.io/openai-agents-python/tracing/
- Google Vertex AI Agent Evaluation: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/evaluation-agents
- Anthropic Demystifying evals for AI agents: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
