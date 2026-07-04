# ZingClaw 项目复盘材料

本文用于整理 ZingClaw 的项目价值点、技术方案和实现过程中遇到的问题。它不是简历正文，而是后续提炼简历 bullet、项目介绍和面试讲解的素材库。

## 1. 项目定位

ZingClaw 是一个面向本地开发场景的 Agent Runtime。它参考了 nanobot 的模块化思想，但核心代码在 `zingclaw/` 中独立实现，目标不是只做一个聊天机器人，而是构建一个可使用工具、可保持状态、可扩展能力、可追踪行为、可回归验证的本地 Agent 系统。

当前项目已经形成了比较完整的闭环：

```text
用户输入
  -> AgentLoop / AgentRunner
  -> Context Builder
  -> LLM Provider
  -> Tool Registry
  -> 内置工具 / MCP 工具
  -> Runtime Events
  -> Session / Memory
  -> Trace / Replay / Scorecard / Suite Report
```

项目更适合强调为“透明可控的本地 Agent Runtime”，而不是简单的“AI 聊天助手”。

## 2. 项目价值点

### 2.1 从聊天程序升级为 Agent Runtime

ZingClaw 不只保存用户消息和模型回复，而是把 Agent 的运行过程拆成多个可观察模块：消息入口、上下文构建、模型调用、工具调用、运行事件、会话状态、长期记忆和回归评估。

这使得系统具备以下价值：

- 能解释 Agent 为什么调用某个工具。
- 能观察一轮任务经历了哪些阶段。
- 能复用同一套 runtime 支撑 CLI、WebUI 和未来更多入口。
- 能把普通对话能力扩展成可执行任务的本地 Agent 能力。

### 2.2 工具系统可扩展

项目实现了统一的工具抽象和注册表，内置工具和未来 MCP 工具都可以通过统一接口暴露给 LLM。

当前工具能力覆盖：

- 文件操作：读取、写入、编辑、列目录、搜索等。
- Shell 执行：安全边界内执行命令。
- Web：搜索和网页抓取。
- 消息工具：向用户发送消息。
- Cron：定时任务管理。
- Spawn / Subagent：后台子任务能力。
- Memory 虚拟工具：`save_memory` 写入长期记忆。
- MCP：支持外部 MCP server 工具包装、状态查看、reload / reconnect。

这个设计让工具不再是写死在 runner 里的逻辑，而是可以独立注册、统一描述、统一执行、统一记录。

### 2.3 Session / Goal / Memory 让 Agent 具备长期运行状态

项目区分了几层状态：

- `sessions/`：保存会话级消息、metadata、goal state、continuation 等运行状态。
- `memory/MEMORY.md`：长期记忆，保存稳定事实、偏好和重要约束。
- `memory/history.jsonl` / `HISTORY.md`：归档历史，用于后续 Dream / consolidation。

这样做的价值是：

- session 负责“当前会话怎么恢复”。
- memory 负责“长期事实和偏好怎么保留”。
- history 负责“过去交互怎么归档和压缩”。

相比只把所有消息塞进 prompt，这种分层更适合长任务和长期使用。

### 2.4 Runtime Events 支撑 CLI / WebUI / Trace

项目设计了运行时事件流，用来描述 Agent 执行过程中的关键状态：

- 构建上下文。
- 调用模型。
- 开始工具调用。
- 工具调用完成或失败。
- 输出流式增量。
- 一轮任务结束。

这些事件既能被 CLI 消费，也能被 WebUI 消费，还能写入 trace。这样前端展示和回归评估不是两套系统，而是共享同一份 runtime 事件源。

### 2.5 行为追踪与回归验证闭环

这是当前项目中最有区分度的价值点之一。

ZingClaw 已经实现了从真实运行到回归验证的闭环：

```text
trace run
  -> 生成 trace
eval curate
  -> 从 trace 抽取 replay case
eval organize
  -> 整理 regression / candidates
eval run-suite
  -> 重新驱动当前 runtime
scorecard
  -> 判断是否退化
suite_report.md
  -> 生成人可读报告
```

当前数据集状态：

```text
regression: 11
candidate: 9
issues: 0
run-suite: 11/11 passed
```

这个能力解决的问题是：每次修改 prompt、context、tool、memory 或安全策略后，可以判断“原来能做的事情，现在还能不能稳定做到”。

### 2.6 评估器和修改器分离

项目当前没有实现自动修改器，这是一个有意选择。

当前边界是：

- 修改由开发者手动完成。
- 评估由 `eval run-suite + scorecard` 完成。
- 系统生成 trace 和 suite report 作为证据。

这种设计避免让优化逻辑和评分逻辑互相污染。后续如果要做 prompt 变体或策略变体，也应该只负责“生成或应用变体”，不能参与评分。

## 3. 核心技术方案

### 3.1 分层架构

项目大致分为以下模块：

```text
config/
  运行配置、provider 配置、通道配置、MCP 配置、memory 配置

providers/
  OpenAI-compatible provider，支持 Kimi / Moonshot API

bus/
  InboundMessage、OutboundMessage、RuntimeEventBus

channels/
  Channel 抽象，CLI Channel，未来可扩展其他入口

agent/
  context builder、runner、loop、autocompact

tools/
  BaseTool、ToolRegistry、内置工具、MCP wrapper

session/
  SessionManager、goal state、turn continuation

memory/
  MemoryStore、DreamConsolidator、MEMORY / HISTORY 管理

evaluation/
  trace、replay、scorecard、suite、curation、dataset

api/ gateway/ webui/
  HTTP gateway、SSE runtime event、WebUI 展示
```

### 3.2 Agent 执行流程

一轮任务大致流程：

```text
用户消息
  -> InboundMessage
  -> AgentLoop
  -> 保存 session message
  -> ContextBuilder 组装上下文
  -> AgentRunner 调用 provider
  -> 如果有 tool_calls，ToolRegistry 执行工具
  -> 工具结果回填给模型
  -> 直到完成或达到迭代限制
  -> OutboundMessage / RuntimeEvent
  -> session / memory / trace 更新
```

这个流程把“模型推理”和“系统执行”分开，工具执行、状态记录、事件发布都在 runtime 层完成。

### 3.3 Context Builder 方案

上下文构建参考 nanobot 的方向，主要包括：

- 系统身份和基础指令。
- 引导文件：`AGENTS.md`、`SOUL.md`、`USER.md`。
- 长期记忆：`MEMORY.md`。
- 历史摘要。
- 当前 session 最近消息。
- Skill 摘要。
- 工具描述。

项目中已经修正过一个重要边界：引导文件应该来自 ZingClaw 自己的 `zingclaw/templates/`，不能依赖 `reference/nanobot/`，因为 reference 目录未来可能删除。

### 3.4 内置工具与 MCP 工具统一

内置工具和 MCP 工具都注册到 `ToolRegistry`，AgentRunner 只面向统一工具 schema 和统一执行接口。

MCP 集成思路：

```text
MCPConfig
  -> stdio / HTTP transport
  -> 获取 MCP capability
  -> MCPToolWrapper
  -> 注册到 ToolRegistry
  -> LLM 通过统一工具名调用
```

MCP 工具命名使用类似：

```text
mcp_{server}_{tool}
```

这样可以避免不同 MCP server 工具重名。

### 3.5 记忆系统方案

项目实现了 `MemoryStore` 和 `DreamConsolidator`，采用类似 nanobot 的方向：

- `save_memory` 虚拟工具：由 LLM 主动决定是否保存长期记忆。
- `append_history`：归档会话摘要到 history。
- `DreamConsolidator`：从归档历史中抽取长期事实，写入 `MEMORY.md`。
- 自动 compaction：会话过长时归档历史，避免上下文无限膨胀。

这里的关键设计是把 session memory 和 long-term memory 分开：

- session 保留当前会话运行状态。
- long-term memory 保留跨会话重要信息。

### 3.6 Trace / Replay / Scorecard / Suite

评估模块是后期重点增强的部分。

#### Trace

每次运行记录为：

```text
traces/<trace_id>/
  metadata.json
  trace.jsonl
  scorecard.json
```

trace 记录：

- user input
- status
- tool_started
- tool_completed
- turn_completed
- turn_result

并做了敏感字段脱敏，避免 API key、token、secret 出现在 trace 中。

#### Replay Case

replay case 是从 trace 或手工构造的 JSON，用来描述一个可重复任务：

```json
{
  "name": "read_hello_py",
  "input": "读取 test/hello.py 的内容，并解释它做了什么。",
  "expected_tools": ["read_file"],
  "expected_files_touched": ["test/hello.py"],
  "expected_status": "completed"
}
```

#### Scorecard

scorecard 判断不是只看“有没有回答”，而是检查：

- 是否完成。
- 是否调用了预期工具。
- 是否没有调用 forbidden tool。
- 是否没有额外危险工具。
- 是否有工具错误。
- 是否有安全拒绝。
- 是否触碰或写入预期文件。
- 是否生成预期产物。
- 最终回答是否包含关键语义。

#### Suite Report

`eval run-suite` 会批量运行 regression case，并生成：

```text
suite_report.json
suite_report.md
```

Markdown 报告用于人工审查，JSON 报告用于后续自动化处理。

## 4. 遇到的问题与解决方式

### 4.1 一开始测试路径跑错

早期曾经混用了 nanobot 参考项目的测试路径，导致用户在 ZingClaw 中运行不存在的测试文件。

解决方式：

- 明确 `reference/` 只是参考。
- 真实实现和测试都必须在 `zingclaw/` 与 `tests/` 下。
- 后续修改时优先检查当前工作目录和真实项目路径。

### 4.2 reference 代码不能成为运行依赖

曾经讨论过引导文件、skill、template 时，容易把 nanobot 的 reference 文件当成可依赖资源。

解决方式：

- 明确 ZingClaw 不能依赖 `reference/nanobot/`。
- 把需要的模板迁移到 `zingclaw/templates/`。
- Context Builder 只读取 ZingClaw 自己的模板和配置。

### 4.3 文件系统和 skill 路径边界混淆

早期回答里曾经把 workspace 沙箱和 skill/template 文件读取混在一起。

实际边界应当是：

- 文件工具访问 workspace，受安全策略限制。
- context builder 读取模板和 skill，是 runtime 内部行为，不等同于让 LLM 自主访问任意路径。
- skill summary 不应该暴露真实敏感路径。

解决方式：

- 调整 skill summary，不暴露真实文件路径。
- 明确 context 注入和文件工具访问是两套权限边界。

### 4.4 WebUI 初期功能不成熟

WebUI 初期只是一个较小的前端页面，和 nanobot 的成熟前端相比差距明显。

遇到的问题：

- Transcript 太长时显示不友好。
- 放大后背景模糊、滚动和关闭行为异常。
- Runtime Feed 展示信息不够清晰。
- 页面功能缺少明确主线。

解决方式：

- 增加前端 store/state 层。
- Transcript 支持独立滚动和放大查看。
- CLI / WebUI 开始消费 runtime events，而不只依赖最终消息。
- 后续仍需要继续增强前端信息架构。

### 4.5 Trace 早期记录过多重复信息

最早的 trace 里每个 runtime event 都记录完整 context，导致文件冗余，人工审查困难。

解决方式：

- 把 trace 改成更紧凑的事件记录。
- `turn_result` 不再重复保存完整 `final_content`。
- 最终回答主要由 `turn_completed` 保存。
- 增加 `trace inspect` 用于可读化查看单条 trace。

### 4.6 Session 和 Trace 内容看起来重合

用户曾经疑惑：`sessions/` 和 `traces/` 都有对话内容，是不是重复。

最终边界：

- `sessions/` 是运行状态，用于恢复会话。
- `traces/` 是证据链，用于调试、审计、回放和回归验证。

它们可能包含相似文本，但目的不同。

### 4.7 Replay Case 规则一开始过严

早期从 trace 自动抽取的 replay case 过度绑定历史工具路径，例如：

- `safe_shell_listing` 强制必须用 `exec dir`。
- `list_workspace` 强制必须调用 `list_dir`。
- `edit_trace_note` 强制必须调用 `apply_patch`。
- `project_orientation` 太宽泛却被放进 regression。

这导致 regression suite 一开始通过率较低。

解决方式：

- `safe_shell_listing` 改成以更安全的 `list_dir` 为主。
- `list_workspace` 改成语义回归 case，不强制工具路径。
- `edit_trace_note` 改成“读取确认，必要时编辑”。
- `project_orientation` 移到 candidates。
- `suite_report.json` / `manifest.json` 不再被当成 replay case。

调整后：

```text
regression: 11
candidate: 9
issues: 0
run-suite: 11/11 passed
```

### 4.8 PowerShell 编码导致中文显示异常

在 PowerShell 里查看 JSON / Markdown 时，有时中文显示为乱码。

确认后发现：

- 文件本身是 UTF-8。
- Python 读取后 Unicode 正常。
- 问题主要来自终端显示编码。

解决方式：

- 文档和 JSON 继续使用 UTF-8。
- 检查中文时优先用 VS Code / Notepad / Python 读取。
- 避免通过 PowerShell 管道写入中文内容，必要时使用 `apply_patch`。

### 4.9 报告初版可读性差

`suite_report.md` 最初用超宽 Markdown 表格展示失败原因和路径，导致阅读困难。

解决方式：

- 失败 case 改成分段展示。
- 总览、失败项、case 明细、metadata 分区展示。
- trace 路径改成相对路径，降低噪声。

## 5. 当前可展示证据

当前项目可以展示的证据包括：

- `replay_cases/regression/`：稳定回归 case。
- `replay_cases/candidates/`：候选能力池。
- `replay_cases/manifest.json`：数据集清单。
- `traces/`：运行证据。
- `suite_report.md`：人可读回归报告。
- `scorecard.json`：单 case 评分结果。

当前关键结果：

```text
Regression cases: 11
Candidate cases: 9
Dataset issues: 0
Eval suite: 11/11 passed
```

## 6. 后续优化方向

### 6.1 文档和 README 更新

当前 README 仍然停留在早期 P0 阶段，已经不能反映项目真实完成度。

建议后续更新：

- 当前架构图。
- 快速启动命令。
- CLI / WebUI 使用方式。
- Tool / Memory / MCP / Eval 能力说明。

### 6.2 Candidates 晋级机制

`candidates/` 中的 case 不是废弃，而是观察池。

后续可以：

- 把稳定的 candidate 拆小后晋级 regression。
- 对 web / MCP / memory 这类不稳定任务设计更合适的 scorecard。
- 保留 capability eval 和 regression eval 的边界。

### 6.3 WebUI 展示 trace / replay

当前 WebUI 已经能展示 runtime，但还可以继续增强：

- trace 列表。
- 单条 trace 时间线。
- 工具调用详情。
- suite report 展示。
- failed checks 高亮。

### 6.4 更细粒度的安全可视化

安全策略已经存在，但后续可以增强：

- 安全拦截事件展示。
- 敏感路径访问说明。
- 命令风险等级展示。
- WebUI 中提示当前 workspace boundary。

### 6.5 更强的长期记忆评估

Memory 已经有 `save_memory` 和 Dream consolidation，但 memory eval 还可以更细：

- 记忆写入是否发生。
- 记忆是否被注入上下文。
- 记忆是否影响后续回答。
- 历史压缩是否保留关键信息。

## 7. 简历提炼时可以围绕的主线

后续写简历时，不建议把所有模块都堆上去。可以优先围绕三条主线：

### 主线一：本地 Agent Runtime

强调从 provider、context、runner、tool、session、memory 到 CLI/WebUI 的完整 runtime。

### 主线二：可扩展工具与 MCP

强调统一工具注册表、内置工具、MCP wrapper、运行时状态查看和 reload。

### 主线三：行为追踪与回归验证

强调 trace、replay case、scorecard、suite report，以及 `11/11` regression baseline。

这三条主线比单纯列“实现了很多功能”更清晰，也更适合面试时展开讲。
