# ZingClaw Agent Runtime 四个核心方向实现说明

本文不是简历正文，而是给后续写简历、准备面试和补测试用的实现底稿。目标是把 ZingClaw 目前最值得讲的四个方向讲清楚：它们解决什么问题，项目里怎么实现，怎么验证。

四个方向分别是：

- Agent 执行循环
- 工具调用与安全边界
- 上下文压缩与记忆
- 追踪与回归验证

## 1. Agent 执行循环

### 要解决的问题

普通聊天程序通常只需要“用户输入 -> 模型回复”。但本地编码 Agent 不一样，它需要在一次任务里连续完成多步动作，例如读取文件、分析结果、修改文件、运行命令、根据错误继续修复。这里真正要解决的是长任务稳定执行问题：

- 模型不能只给最终回答，中间状态要能被观察。
- 工具调用失败后不能静默结束，要把错误反馈给模型继续决策。
- 多个会话不能互相污染状态。
- CLI、WebUI、未来 Channel 都应该共用同一条后端执行链路。

### 当前实现方式

ZingClaw 把一次用户请求拆成两层：

- `AgentLoop` 负责接收消息、确定 session、构建上下文、持久化会话、发布运行状态。
- `AgentRunner` 负责真正的多轮模型调用：调用 LLM、接收 tool call、执行工具、把工具结果放回 messages，再继续下一轮。

核心链路是：

```text
InboundMessage
  -> MessageBus
  -> AgentLoop
  -> ContextBuilder
  -> AgentRunner
  -> LLM Provider
  -> ToolRegistry
  -> OutboundMessage / RuntimeEvent
```

其中 `MessageBus` 的作用是把输入来源和 Agent 核心解耦。CLI 输入、WebUI 输入、测试任务输入，本质上都可以变成 `InboundMessage`。Agent 处理完成后再发布 `OutboundMessage`，同时把中间状态通过 runtime events 暴露出来。

`AgentRunner` 的核心是一个迭代循环：

- 每轮把当前 messages 和工具 schema 发给 provider。
- 如果模型直接回复文本，任务结束。
- 如果模型返回 tool calls，就通过 `ToolRegistry` 执行工具。
- 工具结果作为 `tool` 消息追加回上下文。
- 继续下一轮，直到完成或达到最大迭代次数。

这让 Agent 能处理“先看文件，再改文件，再运行测试，再根据错误继续修”的任务，而不是只能做一次问答。

### 关键文件

- `zingclaw/bus/queue.py`
- `zingclaw/bus/events.py`
- `zingclaw/bus/runtime_events.py`
- `zingclaw/agent/loop.py`
- `zingclaw/agent/runner.py`
- `zingclaw/agent/context.py`
- `zingclaw/session/manager.py`
- `zingclaw/providers/base.py`
- `zingclaw/providers/openai_compat.py`

### 当前可验证点

可以用两类方式验证：

- 手动链路：运行 `python -m zingclaw chat`，观察流式输出、工具调用状态、session 持久化。
- 离线任务：运行 `python -m zingclaw eval task-run eval_tasks/stable --no-fail-on-failed`，观察端到端任务通过率和每个任务的 trace。

如果用于简历，只写最终结果即可，例如：

```text
在 stable 任务集中复验 x 组本地编码任务，端到端通过 x/x。
```

不要在简历里展开 `MessageBus -> AgentLoop -> AgentRunner` 这种链路细节，面试时再讲。

## 2. 工具调用与安全边界

### 要解决的问题

Agent 真正能做事，靠的是工具。但本地工具也带来风险：读错文件、写错路径、执行危险命令、访问敏感目录、把失败结果包装成成功回答。这里要解决的是“能做事”和“可控”之间的平衡。

ZingClaw 的目标不是让模型拥有无限权限，而是让它在明确边界内调用工具：

- 文件操作限制在允许的 workspace 内。
- 敏感路径读取要被拒绝。
- 高风险 Shell 命令要拦截。
- 工具调用参数要统一校验。
- 工具失败要以明确错误返回给模型和 trace。

### 当前实现方式

工具层采用统一接口：

- 每个工具继承统一 `Tool` 基类。
- 每个工具提供名称、描述、参数 schema 和 `execute()`。
- `ToolRegistry` 负责注册工具、生成模型可见的 tool schema、解析参数、执行工具并返回结果。

这意味着内置工具、未来 MCP 工具、虚拟工具都可以放在同一套调用接口下。模型看到的是统一的 function calling 形式，系统内部再决定这个工具是本地执行、MCP 转发，还是内部拦截。

当前内置工具覆盖了几类能力：

- 文件：`read_file`、`write_file`、`edit_file`、`list_dir`
- Shell：`exec`
- 搜索：`find_files`、`grep`
- Web：`web_search`、`web_fetch`
- 消息：`message`
- 子任务：`spawn`
- 定时任务：`cron`
- 长任务：`long_task`
- 记忆：`save_memory`
- MCP：MCP 工具包装与注册

安全边界主要分三层：

- 路径边界：`workspace_policy` 负责解析路径，阻止越过 workspace。
- 敏感路径：`sensitive_paths` 识别 home、ssh、环境文件等敏感位置。
- 命令边界：Shell 工具在执行前检查危险命令和绝对路径访问。

这样做的好处是，安全逻辑不依赖模型“自觉”。模型即使尝试读取敏感路径或执行危险命令，也会在工具层被拦截。

### 关键文件

- `zingclaw/tools/base.py`
- `zingclaw/tools/registry.py`
- `zingclaw/tools/filesystem.py`
- `zingclaw/tools/shell.py`
- `zingclaw/tools/search.py`
- `zingclaw/tools/web.py`
- `zingclaw/tools/message.py`
- `zingclaw/tools/spawn.py`
- `zingclaw/tools/cron.py`
- `zingclaw/tools/memory.py`
- `zingclaw/tools/mcp.py`
- `zingclaw/tools/sandbox.py`
- `zingclaw/security/workspace_policy.py`
- `zingclaw/security/sensitive_paths.py`

### 当前可验证点

可以用离线任务验证安全拒绝：

```powershell
python -m zingclaw eval task-run eval_tasks/stable --pattern "*refusal*.json" --no-fail-on-failed
```

当前稳定集中已有的拒绝类任务包括敏感路径读取和危险 Shell 请求。如果要在简历里写更强的指标，例如“6/6 blocked”，需要继续补足 6 条明确的安全任务，而不是凭感觉写“全部拦截”。

适合简历的结果写法是：

```text
覆盖敏感路径读取与危险 Shell 请求，安全拒绝用例 x/x 通过。
```

## 3. 上下文压缩与记忆

### 要解决的问题

长对话最大的问题不是“历史越多越好”，而是历史太多以后会带来三个副作用：

- 上下文不断变长，成本和延迟上升。
- 旧信息和新目标混在一起，模型更容易抓错重点。
- 用户偏好、项目事实、阶段性结论没有稳定沉淀，跨会话容易丢失。

所以 ZingClaw 没有把所有历史都简单塞回 prompt，而是把记忆拆成几层：

- Session：当前会话的详细消息和运行状态。
- HISTORY：经过归档的历史事件和摘要。
- MEMORY：长期稳定事实、用户偏好和重要结论。

### 当前实现方式

#### Session 层

Session 记录当前会话的细节，包括消息、metadata、summary、goal state、turn continuation 等状态。它更像“当前任务运行容器”，不是长期知识库。

适合放在 Session 里的内容：

- 当前会话的用户消息和助手回复。
- 当前任务的阶段性状态。
- 最近活跃时间、标签、摘要、压缩状态。
- 长任务恢复所需的 goal / continuation 信息。

#### HISTORY 层

HISTORY 记录历史交互的归档信息，承担“可搜索历史”和“压缩来源”的作用。ZingClaw 同时维护结构化 JSONL 和可读 Markdown：

- `memory/history.jsonl`
- `memory/HISTORY.md`

当 session 过长或触发归档时，旧消息会进入历史层。这样 session 不需要无限保存所有上下文细节，历史层也能作为后续 dream / consolidation 的输入。

#### MEMORY 层

MEMORY 保存长期稳定信息，例如用户偏好、项目事实、重要约束和长期决策：

- `memory/MEMORY.md`

写入 MEMORY 的入口有两种：

- 模型通过 `save_memory` 虚拟工具主动保存。
- Dream consolidation 从历史中提取值得长期保留的信息。

`save_memory` 是一个虚拟工具。它对模型表现得像普通工具，但内部不调用外部服务，而是直接写入 `MEMORY.md`。这个设计的好处是：模型可以用统一工具调用形式表达“这件事值得记住”，但系统仍然控制具体写入位置和格式。

#### Context 注入

`ContextBuilder` 负责组装发给模型的上下文。当前注入内容包括：

- 系统身份和平台策略。
- 模板引导文件：`AGENTS.md`、`SOUL.md`、`USER.md`。
- 工具契约说明。
- 长期记忆 `MEMORY.md`。
- 近期历史摘要。
- 技能摘要。
- 当前 session 相关消息。

这样做的核心原则是：稳定规则放前面，长期记忆按需注入，当前会话保留近期细节，不把所有旧对话无脑堆进 prompt。

### 关键文件

- `zingclaw/session/manager.py`
- `zingclaw/session/goal_state.py`
- `zingclaw/session/turn_continuation.py`
- `zingclaw/memory/store.py`
- `zingclaw/tools/memory.py`
- `zingclaw/agent/autocompact.py`
- `zingclaw/agent/context.py`
- `zingclaw/templates/AGENTS.md`
- `zingclaw/templates/SOUL.md`
- `zingclaw/templates/USER.md`
- `zingclaw/skills/*/SKILL.md`

### 当前可验证点

跨会话记忆可以这样验证：

1. 在 A 会话中告诉 Agent 一个偏好，并要求它记住。
2. 确认 `memory/MEMORY.md` 出现对应内容。
3. 切换到 B 会话，询问该偏好。
4. 判断是否能从长期记忆中召回。

如果走自动历史归档和 dream：

```powershell
python -m zingclaw memory dream --max-entries 20
```

适合简历的结果写法是：

```text
跨会话记忆召回用例 x/x 通过。
```

不要只写“压缩率降低多少”，除非你真的设计了专门实验。对这个项目来说，更贴近目标的指标是“长对话后是否还能召回关键偏好和项目事实”。

## 4. 追踪与回归验证

### 要解决的问题

Agent 修改 prompt、工具描述、安全策略或上下文注入方式以后，很容易出现隐性退化：以前能完成的任务，现在不能完成；以前会调用工具，现在开始胡答；以前能拒绝危险操作，现在放行了。

追踪和回归验证要解决的是“怎么证明 Agent 没有退化”。

这里不是为了做普通单元测试，而是为了记录一次 Agent 任务的完整证据：

- 用户输入是什么。
- Agent 调用了哪些工具。
- 工具参数是什么。
- 产生了哪些文件变化。
- 有没有工具错误。
- 最终回答是什么。
- 是否满足预期。

### 当前实现方式

ZingClaw 现在有两条相关链路。

#### Trace 链路

每次任务可以生成 trace：

- `trace.jsonl` 保存事件序列。
- `metadata.json` 保存任务元信息。
- `scorecard.json` 保存判断结果。

trace 里记录的是事件，而不是完整 session 复制。它更像一次任务执行的审计日志，服务于复盘、debug 和回归验证。

#### Replay / Scorecard 链路

Replay case 从历史 trace 中抽取稳定任务，重新驱动当前 runtime 生成 fresh trace。然后通过 scorecard 对比关键行为：

- 是否完成。
- 是否调用预期工具。
- 是否出现工具错误。
- 是否触碰预期文件。
- 是否生成预期产物。
- 是否触发安全拒绝。
- 最终回答是否包含关键语义。

这条链路适合做“回归能力验证”：当你改了 prompt、工具描述或安全策略后，用同一批 replay case 重新跑，观察是否退化。

#### Task Contract 链路

除了从 trace 抽 replay case，ZingClaw 还补了更适合端到端验证的 Task Contract：

- 每个任务用 JSON 描述输入、fixture、允许工具、预期产物和 verifier。
- 每次运行会复制一个干净 workspace，避免污染真实项目。
- Agent 仍然通过 MessageBus 和 AgentLoop 完整运行，不绕过主链路。
- 运行后保存 trace、workspace diff、verification 和 summary。

这条链路更适合回答“这个 Agent 在一批本地编码任务上是否能稳定完成”。

### 关键文件

- `zingclaw/evaluation/trace.py`
- `zingclaw/evaluation/replay.py`
- `zingclaw/evaluation/scorecard.py`
- `zingclaw/evaluation/suite.py`
- `zingclaw/evaluation/curation.py`
- `zingclaw/evaluation/task_contract.py`
- `zingclaw/evaluation/task_runner.py`
- `zingclaw/evaluation/verifiers.py`
- `eval_tasks/stable/*.json`
- `eval_fixtures/basic_workspace/`
- `replay_cases/regression/`
- `traces/`
- `eval_runs/`

### 当前可验证点

运行端到端任务评测：

```powershell
python -m zingclaw eval task-run eval_tasks/stable --no-fail-on-failed
```

运行 replay 回归套件：

```powershell
python -m zingclaw eval run-suite replay_cases/regression --no-fail-on-regression
```

查看输出：

- `eval_runs/<run_id>/summary.md`
- `eval_runs/<run_id>/summary.json`
- `replay_cases/regression/suite_report.md`
- `replay_cases/regression/suite_report.json`

适合简历的结果写法是：

```text
当前 stable 端到端任务集 x/x 通过。
当前 replay 回归套件 x/x 通过。
```

这里的数字必须来自实际运行结果，不要手填预期数字。

## 这四个方向怎么连成一个故事

这四块不是四个孤立模块，可以连成一条主线：

```text
让 Agent 能长期稳定做事
  -> 需要稳定的执行循环
  -> 需要可控的本地工具
  -> 需要可持续的上下文和记忆
  -> 需要 trace 和 eval 证明改动没有破坏能力
```

所以 ZingClaw 最适合讲成：

```text
面向本地编码任务的可控 Agent Runtime，重点解决长任务执行稳定性、本地工具安全边界、上下文持续性和行为可验证问题。
```

这个表述比“我做了一个 Agent 系统”更具体，也比堆功能名更像一个真实工程项目。

## 后续建议

如果继续完善，优先补三个能直接增强说服力的东西：

- 把 `eval_tasks/stable` 当前任务重新跑一遍，记录真实通过率。
- 补足 6 条左右安全拒绝任务，再写安全拦截通过率。
- 做一个跨会话记忆召回任务合同，让 memory 的效果也能被 task-run 验证。

这三件事做完后，简历里的结果就不需要“感觉上很强”，而是有本地报告可以支撑。
