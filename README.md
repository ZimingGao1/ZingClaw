<div align="center">

# ZingClaw

**面向本地工作区的可控 Agent Runtime**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![AsyncIO](https://img.shields.io/badge/Runtime-AsyncIO-2f7f64.svg)](#系统架构)
[![MCP](https://img.shields.io/badge/MCP-Extensible-d1603d.svg)](#工具调用与安全边界)
[![Trace](https://img.shields.io/badge/Trace-Replay-1f2833.svg)](#追踪与回归验证)
[![Eval](https://img.shields.io/badge/Eval-Task%20Contract-7b5e3b.svg)](#端到端评测)

面向本地工作区中的多轮任务与工具协作场景，构建轻量稳定的 Agent Runtime，重点解决**工具调用可控**、**上下文可持续**和**系统能力退化**的问题。

ZingClaw 让 Agent 在受控边界内读取文件、调用工具、执行命令、维护会话上下文，并通过 trace 与回归评测检查后续迭代是否破坏已有能力。

</div>

## 目录

- [项目概述](#项目概述)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [模块实现](#模块实现)
- [端到端评测](#端到端评测)
- [快速开始](#快速开始)
- [项目结构](#项目结构)

## 项目概述

ZingClaw 不是单纯的聊天界面，而是一个围绕本地工作区设计的 Agent 执行框架。它把用户输入、上下文构建、模型调用、工具执行、状态记录和结果返回收敛到同一条任务链路，让多轮任务可以持续推进，也让每次工具调用都能被追踪和验证。

适合的任务场景包括：

- 查看本地项目结构，解释文件和目录用途。
- 读取、创建、修改 workspace 内文件。
- 执行安全范围内的 Shell 命令并解释输出。
- 通过 WebUI 观察工具调用、运行状态和会话记录。
- 将稳定任务沉淀为 replay case，用于后续回归验证。

## 核心特性

| 方向 | 实现方式 | 解决的问题 |
| --- | --- | --- |
| 多轮执行 | `MessageBus` + `AgentLoop` + `AgentRunner` | 让对话推进、工具执行和结果回填进入统一任务链路 |
| 工具可控 | `ToolRegistry` 统一管理内置工具和 MCP 扩展工具 | 避免工具调用散落在不同路径，方便参数校验和错误回填 |
| 安全边界 | workspace 限制、敏感路径拒绝、危险命令拦截 | 降低越权访问、误执行和本地高风险操作 |
| 分层记忆 | `session` / `HISTORY` / `MEMORY` 分层保存 | 避免长对话上下文膨胀和关键信息遗忘 |
| 回归验证 | trace / replay case / scorecard / verifier | 防止模型、工具或安全策略迭代引入能力回归 |

## 系统架构

```text
用户输入
  -> CLI / WebUI / Channel
  -> MessageBus
  -> AgentLoop
  -> ContextBuilder
  -> AgentRunner
  -> LLM Provider
  -> ToolRegistry
  -> 内置工具 / MCP 工具
  -> Session / Memory / Trace
  -> 用户回复
```

这条链路分成五个核心层次：

| 层次 | 主要模块 | 作用 |
| --- | --- | --- |
| 交互层 | CLI、WebUI、Channel | 接收用户输入，展示会话、工具状态和运行事件 |
| 消息层 | `MessageBus`、`InboundMessage`、`OutboundMessage` | 解耦输入来源和 Agent 核心 |
| Agent 层 | `AgentLoop`、`AgentRunner`、`ContextBuilder` | 组织会话、构建上下文、驱动模型和工具循环 |
| 工具层 | `ToolRegistry`、内置工具、MCP wrapper | 统一工具注册、参数解析、执行和结果回填 |
| 状态层 | `sessions/`、`memory/`、`traces/`、`eval_runs/` | 保存会话、记忆、执行证据和评测结果 |

## 模块实现

### Agent 执行循环

用户消息会先被转换为 `InboundMessage`，通过 `MessageBus` 进入 Agent 核心。`AgentLoop` 按 session 获取会话锁，并通过全局并发闸门限制同时运行的任务数量，避免同一会话并发处理导致上下文混乱，也避免请求堆积压垮模型调用。

进入单轮任务后，`ContextBuilder` 负责组装系统提示词、引导文件、记忆、技能摘要和当前会话消息；`AgentRunner` 负责驱动模型与工具的多轮交互：模型返回普通文本时直接结束，返回工具调用时交给统一工具入口执行，再把工具结果回填给模型继续判断。CLI、WebUI 和评测任务都复用这条链路。

相关目录：

```text
zingclaw/agent/
zingclaw/bus/
zingclaw/providers/
```

### 工具调用与安全边界

每个工具都实现统一接口，提供名称、描述、参数 schema 和执行函数。`ToolRegistry` 负责把这些工具转换成模型可见的 function definitions，并在模型请求调用时统一完成工具查找、参数解析、错误捕获和结果返回。

内置工具和 MCP 扩展工具共用同一套执行入口。MCP 工具会被包装成本地工具形态，并使用 `mcp_{server}_{tool}` 命名避免重名；对模型来说，它们和内置工具一样被注册、选择和调用，对 Runtime 来说仍然可以保留来源、状态和错误信息。

安全边界主要落在本地高风险操作上：文件工具通过 workspace policy 解析路径，防止路径穿越；敏感路径会被单独识别并拒绝；Shell 工具在执行前检查危险命令和可疑绝对路径，并在 workspace 目录下运行。工具失败不会被吞掉，而是以明确错误回填给模型和 trace，方便后续恢复和复盘。

相关目录：

```text
zingclaw/tools/
zingclaw/security/
```

### 上下文压缩和记忆

ZingClaw 不把全部历史直接塞进 prompt，而是把上下文拆成三层：`session` 保存当前会话的完整交互和运行状态，`HISTORY` 归档历史摘要，`MEMORY` 保存用户偏好、项目事实和长期约束。

每轮构建上下文时，`ContextBuilder` 会读取模板引导文件、长期记忆、近期历史摘要、技能摘要和当前会话消息，组合成发给模型的 messages。这样可以保留当前任务需要的细节，又避免旧对话无限膨胀。

当模型判断某些信息需要长期保留时，可以通过 `save_memory` 虚拟工具写入 `MEMORY.md`；历史内容也可以通过 consolidation / dream 流程归档和压缩。这个设计让 session 负责“接着聊”，memory 负责“长期记住”，history 负责“可追溯”。

相关目录：

```text
zingclaw/session/
zingclaw/memory/
zingclaw/templates/
```

### 追踪与回归验证

Trace 会跟随一次 Agent 任务记录关键执行证据，包括运行状态、工具开始与结束、错误、最终回复和任务结果。它的作用不是参与下一轮上下文，而是给开发者和评测系统复盘：这次任务到底怎么完成，失败发生在哪一步。

当某个任务已经稳定完成，可以从 trace 中抽取 replay case，作为之后可复跑的回归用例。后续修改模型配置、工具描述、记忆注入或安全策略后，`run-suite` 会重新驱动当前 Runtime 生成 fresh trace，再用 scorecard / verifier 检查工具使用、产物状态和最终结果是否仍符合预期。

这套链路的目标是防止迭代引入能力回归：不是只看 Agent 有没有回答，而是看它是否仍然按原来的能力完成任务。

相关目录：

```text
zingclaw/evaluation/
traces/
replay_cases/
eval_tasks/
eval_runs/
```

### WebUI 与 Gateway

WebUI 提供本地可视化入口，用于查看 session、Transcript、Runtime Feed、Active Tools 和 MCP Runtime 状态。Gateway 负责把浏览器请求接入同一套 Agent Runtime，前端不单独实现执行逻辑。

相关目录：

```text
zingclaw/gateway/
zingclaw/webui/
```

## 端到端评测

ZingClaw 的端到端评测采用任务合同结构：把一条 Agent 任务定义为可执行契约，明确用户输入、fixture 工作区、允许工具、预期产物和 verifier。这样做是为了避免只凭最终回答判断任务是否完成，而是把“任务该怎么开始、允许怎么行动、最后应该留下什么结果”提前固定下来。

运行时会复制隔离 workspace，真实驱动 Agent Runtime 完成任务，并采集三类证据：

| 证据 | 判断内容 | 为什么需要 |
| --- | --- | --- |
| 最终回复 | 用户侧交付是否合理 | 防止只执行不解释，或回答偏离任务 |
| 工具轨迹 | 是否真实执行必要动作，有无错误或越权 | 防止模型只口头声称完成 |
| 工作区产物 | 文件、目录、脚本输出是否达到预期 | 用真实环境状态验证任务结果 |

对于文件修改、命令执行和安全拒绝这类可客观判断的任务，优先使用 deterministic verifier，而不是依赖 LLM-as-Judge。评测失败后，通过 trace、workspace diff 和 verifier 报告定位失败环节，再针对任务链路、工具描述、安全规则或校验条件做迭代优化。

运行端到端任务评测：

```powershell
python -m zingclaw eval task-run eval_tasks/stable --no-fail-on-failed
```

运行回归回放套件：

```powershell
python -m zingclaw eval run-suite replay_cases/regression --no-fail-on-regression
```

## 快速开始

启动 CLI：

```powershell
python -m zingclaw chat
```

启动 WebUI / Gateway：

```powershell
python -m zingclaw gateway
```

默认访问地址：

```text
http://127.0.0.1:8000
```

## 项目结构

```text
ZingClaw/
  zingclaw/        # 核心代码
  workspace/       # Agent 可操作的本地工作区
  sessions/        # 会话状态与消息记录
  memory/          # MEMORY.md、HISTORY.md、history.jsonl
  replay_cases/    # 回归回放用例
  eval_tasks/      # 端到端任务评测用例
  eval_runs/       # 评测运行结果
  docs/            # 设计说明与项目复盘
  tests/           # 测试用例
```
