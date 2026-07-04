<div align="center">

# ZingClaw

### 面向本地代码仓库维护的透明可控 Coding Agent Runtime

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Runtime](https://img.shields.io/badge/Agent-Runtime-black.svg)](#系统架构)
[![Trace](https://img.shields.io/badge/Trace-Replay-green.svg)](#行为追踪与回归验证)
[![MCP](https://img.shields.io/badge/MCP-Ready-orange.svg)](#工具系统)
[![Eval](https://img.shields.io/badge/Regression-11%2F11-brightgreen.svg)](#回归基线)

当 AI 能读文件、改代码、执行命令、调用外部工具时，最重要的问题不再只是“它能不能回答”，而是：

**它做了什么、为什么这么做、出错后怎么定位、修改后怎么证明没有退化。**

</div>

---

## 项目简介

ZingClaw 是一个面向本地开发场景的 Agent Runtime。它参考了 nanobot 的模块化思想，但核心实现独立位于 `zingclaw/`，目标是构建一个可调用本地工具、可持久化状态、可接入 MCP、可观察运行过程、可回放验证行为的本地 Coding Agent。

它不是一个单纯的聊天机器人，而是一个围绕“本地代码仓库维护”设计的可控执行系统：

- 用户可以让 Agent 查看项目结构、读取文件、修改文件、运行命令和总结结果。
- Runtime 会记录上下文构建、模型调用、工具调用、工具结果和最终回答。
- 运行结果可以沉淀为 replay case，并通过 regression suite 验证后续修改是否造成退化。
- CLI、WebUI、Trace 共享同一套 runtime event 事件源，避免展示层和执行层割裂。

## 核心价值

| 方向 | ZingClaw 做了什么 | 价值 |
| --- | --- | --- |
| 透明可控 | Runtime events + trace.jsonl + suite_report.md | 能看到 Agent 每一步行为，而不是只看最终回答 |
| 本地工具执行 | 文件、Shell、搜索、消息、Cron、Spawn、Memory、MCP 工具 | Agent 可以真正完成本地开发任务 |
| 安全边界 | workspace 限制、敏感路径拦截、命令风险控制、工具错误记录 | 降低误操作和越界访问风险 |
| 长期状态 | Session、Goal、Continuation、MEMORY.md、HISTORY.md | 支持长对话、长期任务和跨会话偏好 |
| 回归验证 | trace -> replay case -> scorecard -> run-suite | 修改 prompt / tool / memory 后可验证是否退化 |

## 适用场景

ZingClaw 当前最适合定位为：

> 面向个人开发者和本地代码仓库维护的透明 Coding Agent Runtime。

典型任务包括：

- 查看项目结构并解释目录用途。
- 读取某个文件并总结代码行为。
- 创建或修改本地文件。
- 执行安全范围内的 shell 命令。
- 运行测试或脚本并解释输出。
- 将长期偏好写入 memory。
- 对 Agent 行为生成 trace，并构建 replay case 做回归验证。

## 当前能力概览

### Agent Runtime

- `AgentLoop` 负责从消息入口接收任务、维护 session、发布 runtime events。
- `AgentRunner` 负责多轮模型调用和工具调用循环。
- `ContextBuilder` 负责组装系统提示词、引导文件、记忆、历史、技能摘要和会话消息。
- `RuntimeEventBus` 统一输出运行阶段、工具调用、流式输出和完成事件。

### 工具系统

ZingClaw 使用统一的 `ToolRegistry` 管理内置工具和 MCP 工具。

当前工具覆盖：

- 文件工具：`list_dir`、`read_file`、`write_file`、`edit_file`、`find_files`、`grep`
- Shell 工具：`exec`
- Web 工具：`web_search`、`web_fetch`
- 消息工具：`message`
- 任务工具：`cron`、`spawn`、`long_task`
- Memory 工具：`save_memory`
- MCP 工具：通过 `mcp_{server}_{tool}` 命名规则统一注册

### Memory 与 Session

ZingClaw 将运行状态分为三层：

- `sessions/`：会话消息、metadata、goal state、continuation。
- `memory/MEMORY.md`：长期事实、偏好和稳定约束。
- `memory/history.jsonl` / `memory/HISTORY.md`：归档历史和压缩摘要。

这避免了把所有历史都堆进 prompt，也让 session recovery 和 long-term memory 的边界更清晰。

### MCP 扩展

ZingClaw 支持通过配置接入 MCP server：

- 支持 stdio / HTTP 类传输配置。
- 能加载 MCP capability。
- 将 MCP tool 包装为本地工具。
- 提供 MCP 状态查看、reload、reconnect。
- 使用工具名前缀避免不同 server 的工具重名。

### WebUI / Gateway

项目提供本地 HTTP gateway 和 WebUI：

- Session 列表。
- Transcript 查看。
- Runtime Feed。
- Active Tools。
- MCP Runtime 状态。
- 通过 runtime events 展示 Agent 实时执行过程。

## 系统架构

完整运行链路：

```text
用户输入
  -> Channel / CLI / WebUI
  -> InboundMessage
  -> MessageBus
  -> AgentLoop
  -> ContextBuilder
  -> AgentRunner
  -> LLM Provider
  -> ToolRegistry
  -> 内置工具 / MCP 工具
  -> Runtime Events
  -> OutboundMessage
  -> Session / Memory / Trace
```

核心模块：

```text
zingclaw/
  agent/        # context、runner、loop、autocompact
  api/          # HTTP API
  bus/          # InboundMessage、OutboundMessage、RuntimeEventBus
  channels/     # Channel 抽象和 CLI Channel
  cli/          # 命令行入口
  config/       # Pydantic 配置模型
  evaluation/   # trace、replay、scorecard、suite、curation、dataset
  gateway/      # Web runtime gateway
  memory/       # MEMORY / HISTORY / Dream consolidation
  providers/    # OpenAI-compatible provider
  session/      # SessionManager、goal state、continuation
  templates/    # AGENTS.md、SOUL.md、USER.md、agent prompt 模板
  tools/        # 内置工具、MCP wrapper、工具注册表
  webui/        # 前端页面
```

## 行为追踪与回归验证

ZingClaw 的一个核心能力是把 Agent 行为变成可追踪、可回放、可验证的证据链。

```text
trace seed
  -> 生成代表性问题集
trace run
  -> 批量运行问题并生成 trace
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

### Trace

每次运行会生成：

```text
traces/<trace_id>/
  metadata.json
  trace.jsonl
  scorecard.json
```

trace 记录关键事件：

- `user_input`
- `status`
- `tool_started`
- `tool_completed`
- `turn_completed`
- `turn_result`

### Replay Case

replay case 用 JSON 描述一个可重复任务及其预期行为：

```json
{
  "name": "read_hello_py",
  "input": "读取 test/hello.py 的内容，并解释它做了什么。",
  "expected_status": "completed",
  "expected_tools": ["read_file"],
  "expected_files_touched": ["test/hello.py"],
  "allow_tool_errors": false
}
```

### Scorecard

Scorecard 不只判断“有没有回答”，还会检查：

- 是否完成。
- 是否调用预期工具。
- 是否没有调用禁用工具。
- 是否没有额外危险工具。
- 是否出现工具错误。
- 是否触发安全拒绝。
- 是否触碰或写入预期文件。
- 是否生成预期产物。
- 最终回答是否包含关键语义。

### 回归基线

当前本地 regression baseline：

```text
Regression cases: 11
Candidate cases: 9
Dataset issues: 0
Eval suite: 11/11 passed
```

生成的报告：

```text
replay_cases/regression/suite_report.json
replay_cases/regression/suite_report.md
```

## 快速开始

### 1. 安装

```powershell
git clone <your-repo-url>
cd ZingClaw

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e .
```

### 2. 配置模型

ZingClaw 使用 OpenAI-compatible provider。以 Kimi / Moonshot 为例：

```powershell
$env:OPENAI_API_KEY="你的 API Key"
$env:OPENAI_API_BASE="https://api.moonshot.cn/v1"
$env:MODEL_NAME="kimi-k2.5"
$env:PROVIDER_NAME="openai_compat"
```

也可以写入 `.env`：

```env
OPENAI_API_KEY=your-api-key
OPENAI_API_BASE=https://api.moonshot.cn/v1
MODEL_NAME=kimi-k2.5
PROVIDER_NAME=openai_compat
```

查看当前配置：

```powershell
python -m zingclaw status
```

### 3. 启动 CLI

```powershell
python -m zingclaw chat
```

指定 session：

```powershell
python -m zingclaw chat --session demo
```

一次性发送消息：

```powershell
python -m zingclaw chat --message "查看 workspace 文件结构"
```

### 4. 启动 WebUI / Gateway

```powershell
python -m zingclaw gateway
```

默认地址：

```text
http://127.0.0.1:8000
```

## 常用命令

### Chat

```powershell
python -m zingclaw chat
python -m zingclaw chat --session demo
python -m zingclaw chat --message "运行 test/hello.py"
```

### MCP

```powershell
python -m zingclaw mcp status
python -m zingclaw mcp reload
python -m zingclaw mcp reconnect
```

### Memory

```powershell
python -m zingclaw memory dream --max-entries 20
```

### Trace

```powershell
python -m zingclaw trace seed
python -m zingclaw trace run eval_questions/starter.json --limit 3
python -m zingclaw trace inspect traces/<trace_id>
```

### Eval

```powershell
python -m zingclaw eval curate traces --output replay_cases
python -m zingclaw eval organize replay_cases --normalize
python -m zingclaw eval run replay_cases/regression/read_hello_py.json
python -m zingclaw eval run-suite replay_cases/regression
```

如果只是想查看报告，不希望失败时返回非零退出码：

```powershell
python -m zingclaw eval run-suite replay_cases/regression --no-fail-on-regression
```

## 目录说明

```text
ZingClaw/
  zingclaw/       # 真实产品代码
  tests/          # ZingClaw 测试
  docs/           # 设计文档和项目复盘
  workspace/      # 文件工具可操作的工作区
  sessions/       # 会话状态
  memory/         # MEMORY.md、HISTORY.md、history.jsonl
  traces/         # Agent 运行证据
  replay_cases/   # regression / candidates / manifest / suite report
  eval_questions/ # trace seed 生成的问题集
  reference/      # nanobot 参考代码，不作为运行依赖
```

## 安全边界

ZingClaw 不是为了让模型无限制操作本机，而是强调可控执行：

- 文件工具默认限制在 `workspace/`。
- 敏感路径访问会被拒绝或记录为安全事件。
- Shell 命令有风险控制和超时限制。
- trace 会脱敏常见 secret 字段。
- MCP 工具通过注册表统一暴露，避免绕过工具层。

安全策略的目标不是声称“绝对安全”，而是让风险可见、边界明确、失败可追踪。

## 设计取舍

### 为什么不直接只做聊天？

因为本地代码维护任务需要读文件、改文件、执行命令和验证结果。ZingClaw 把这些行为放进统一 runtime，而不是让模型只输出建议。

### 为什么要有 trace / replay？

Agent 的失败往往不是最终回答一句话能解释的，可能发生在上下文、工具选择、工具参数、文件边界或多轮执行中。trace 记录“发生了什么”，replay case 负责“怎么重放”，scorecard 负责“是否退化”。

### 为什么 candidates 不直接进入 regression？

Web、MCP、memory、复杂上下文任务可能受外部环境或模型策略影响，不适合一开始就要求 100% 稳定。ZingClaw 将它们放入 `candidates/`，等拆小、稳定后再晋级 regression。

## 当前状态

ZingClaw 目前已经跑通核心闭环：

- CLI / WebUI 基本可用。
- 内置工具系统可用。
- Session / Memory 可用。
- MCP 基础集成可用。
- Trace / Replay / Scorecard / Suite Report 可用。
- 当前 regression suite 达到 `11/11 passed`。

仍在继续完善的方向：

- WebUI 成熟度。
- candidates 晋级机制。
- 更细粒度的 memory eval。
- 更清晰的安全可视化。
- README 和项目文档持续更新。

## 相关文档

- [项目复盘材料](docs/project-review.md)
- [Nanobot 实施路线](docs/zingclaw-nanobot-implementation-roadmap.md)
- [核心组件说明](docs/zingclaw-core-components.md)
- [增强规划](docs/enhance.md)

## 项目边界

`reference/` 目录只是学习和迁移参考，不是 ZingClaw 的运行依赖。后续即使删除 `reference/`，ZingClaw 的核心功能也应该继续工作。

