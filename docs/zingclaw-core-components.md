# ZingClaw 核心组件详解（当前实现版）

这份文档模仿 `nanobot` 核心组件讲解的结构，但描述的是 **当前 ZingClaw 仓库里已经存在的实现**，而不是目标态。

阅读这份文档时请始终区分三件事：

- `已实现`：代码已经存在并可运行
- `部分实现`：骨架或简化版已经存在，但还没有达到 nanobot 完整能力
- `未实现`：目前仓库里还没有对应组件

---

## 2.2 核心组件详解

### 2.2.1 CLI Layer（命令行入口层）

位置: `zingclaw/cli/main.py`

职责: 负责当前项目的本地交互入口。

当前已实现能力:

- `status`：打印当前运行配置
- `chat --message "..."`：执行单轮对话
- `chat`：进入交互式会话
- 交互命令：
  - `/help`
  - `/exit`
  - `/model`
  - `/session`
  - `/clear`
- 对最终模型回答支持流式输出

当前状态: `已实现（P0 版本）`

和 nanobot 的差异:

- 目前只有 CLI，没有 Channel 层
- 没有接入 WebUI / API / 第三方聊天平台
- 没有复杂的异步交互控件和流式进度事件体系

---

### 2.2.2 Agent Loop（Agent 外层编排）

位置: `zingclaw/agent/loop.py`

职责: 把一次用户输入真正串成完整执行链路。

当前已实现职责:

- 接收一条用户消息
- 获取或创建 session
- 先把用户消息写入 session
- 调用 `ContextBuilder` 生成本轮上下文
- 调用 `AgentRunner` 执行 LLM / tool loop
- 把 assistant / tool 结果回写到 session
- 返回本轮结果给 CLI

当前状态: `已实现（P0 版本）`

当前运行方式:

```text
CLI -> AgentLoop -> Session -> ContextBuilder -> AgentRunner
    -> Provider / Tools -> Session 持久化 -> CLI 输出
```

和 nanobot 的差异:

- 没有 `MessageBus`
- 没有 `InboundMessage / OutboundMessage`
- 没有多 channel 调度
- 没有 runtime event bus
- 没有 command router / hook / cron / subagent 等外层调度机制

---

### 2.2.3 Context Builder（上下文构建器）

位置: `zingclaw/agent/context.py`

职责: 组装发送给 LLM 的完整上下文。

当前实际放入上下文的内容:

- system prompt
  - agent 身份
  - 当前模型名
  - workspace 路径
  - 当前运行环境
  - channel / session 信息
  - 基础工具使用约束
- 会话历史
  - `user / assistant / tool`
  - `tool_calls`
  - `reasoning_content`
  - `partial`
- 当前轮用户输入
- 当前轮 runtime metadata
  - 当前时间
  - workspace
  - session
  - channel
  - chat id
  - `message_id`
  - `sender_id`
- 工具摘要
- tool schema

代码里已预留但当前通常未生效的内容:

- `AGENTS.md`
- `SOUL.md`
- `USER.md`

原因:

- `ContextBuilder` 支持加载这些文件
- 但当前仓库根目录并没有这些文件

当前状态: `已实现（P0 版本）`

和 nanobot 的差异:

- 还没有 `TOOLS.md`
- 还没有 memory context
- 还没有 skills summary
- 还没有 MCP runtime lines
- 还没有 media/image 注入
- 还没有 summary / consolidation / recent history 记忆层

---

### 2.2.4 Agent Runner（LLM 工具循环执行器）

位置: `zingclaw/agent/runner.py`

职责: 执行单轮里的 `LLM -> 工具 -> LLM` 循环。

当前已实现逻辑:

- 调 `provider.chat_with_retry(...)`
- 接收普通回答或 `tool_calls`
- 把 assistant 消息回放到消息列表
- 调用 `ToolRegistry.execute(...)`
- 把 tool result 作为 `role="tool"` 消息回放
- 循环直到：
  - 得到最终回答
  - provider 返回错误
  - 达到 `max_iterations`
- 累加 token usage
- 支持把文本 delta 透传给上层做流式输出

当前状态: `已实现（P0 版本）`

和 nanobot 的差异:

- 没有 hook 生命周期
- 没有 progress callback
- 没有 injection / checkpoint
- 没有复杂错误恢复
- 没有 tool result offload
- 没有 micro-compact / context governance

---

### 2.2.5 Provider Layer（模型提供层）

位置:

- `zingclaw/providers/base.py`
- `zingclaw/providers/openai_compat.py`

职责: 屏蔽具体模型服务差异，向上提供统一的聊天接口。

当前已实现能力:

- `LLMProvider` 抽象基类
- `OpenAICompatProvider`
- OpenAI-compatible Chat Completions 调用
- tool schema 传入
- tool call 解析
- reasoning content 保留
- retry 机制
- Kimi / Moonshot 兼容处理
- 流式文本输出
- 流式 tool call 组装

当前状态: `已实现（P0 版本）`

和 nanobot 的差异:

- 目前只有一个 provider 实现
- 没有 provider registry / factory
- 没有多 provider 路由
- 没有 image generation / transcription provider

---

### 2.2.6 Session Manager（会话管理器）

位置: `zingclaw/session/manager.py`

职责: 管理每个会话的历史记录与本地持久化。

当前已实现能力:

- `Session`
- `SessionManager`
- `get_or_create`
- `save`
- `flush_all`
- `delete_session`
- `read_session_file`
- `read_session_metadata`
- `list_sessions`
- 损坏 JSONL 的轻量修复
- `get_history(...)` 历史回放

存储方式:

- 每个 session 一份本地 JSONL 文件
- 目录默认是 `sessions/`

当前状态: `已实现（P0 版本）`

和 nanobot 的差异:

- 没有 auto compact
- 没有 consolidation
- 没有 fork session
- 没有 goal state
- 没有更复杂的多层历史管理

---

### 2.2.7 Tool System（工具系统）

位置:

- `zingclaw/tools/base.py`
- `zingclaw/tools/registry.py`
- `zingclaw/tools/loader.py`
- `zingclaw/tools/context.py`
- `zingclaw/tools/filesystem.py`
- `zingclaw/tools/shell.py`
- `zingclaw/tools/mcp.py`

职责: 管理可调用工具的定义、装载、参数校验和执行。

当前已实现内置工具:

| 工具 | 作用 |
| --- | --- |
| `list_dir` | 列出目录 |
| `read_file` | 读取文件 |
| `write_file` | 写入文件 |
| `edit_file` | 编辑文件 |
| `exec` | 执行 shell 命令 |

当前已实现的工具基础设施:

- `Tool` 抽象基类
- JSON Schema 参数定义
- 参数类型转换和校验
- `ToolRegistry`
- `ToolLoader`
- built-in / MCP wrapper 分层设计
- `ToolContext`

当前状态: `已实现（P0 + 部分 P1 骨架）`

和 nanobot 的差异:

- MCP 目前只有 wrapper/sanitizer，没有真实连接和会话控制
- 没有 web search / web fetch
- 没有 message / spawn / cron / self / image 等工具
- 没有 exec session 长任务体系

---

### 2.2.8 Config System（配置系统）

位置: `zingclaw/config/settings.py`

职责: 管理运行时配置和目录布局。

当前已实现配置项:

- `home_dir`
- `workspace_dir`
- `sessions_dir`
- `memory_dir`
- `logs_dir`
- `openai_api_key`
- `openai_api_base`
- `model_name`
- `max_iterations`
- `max_tool_result_chars`

当前状态: `已实现（最小配置版）`

和 nanobot 的差异:

- 还没有完整 schema 分层
- 还没有 provider registry 相关配置
- 还没有 tools/channels/bus/webui 的大配置树

---

### 2.2.9 Memory System（记忆系统）

位置: `zingclaw/memory/`

职责: 目标上应负责跨会话长期记忆与摘要压缩。

当前实际情况:

- 包目录存在
- 还没有真正实现 `memory.py`
- 还没有长期记忆文件或摘要逻辑

当前状态: `未实现`

这部分如果往 nanobot 靠，需要补上:

- `MEMORY.md` 或结构化 profile
- 最近摘要
- 历史压缩 / consolidation
- 上下文裁剪策略

---

### 2.2.10 Skills / MCP System（技能与 MCP 扩展）

位置:

- `zingclaw/tools/mcp.py`
- 未来应新增 `zingclaw/skills/` 或 `zingclaw/agent/skills.py`

职责: 目标上应负责按需加载扩展能力。

当前实际情况:

- MCP 只有命名包装层
- skills 体系还没有开始

当前状态:

- MCP：`部分实现（骨架）`
- Skills：`未实现`

---

### 2.2.11 Channel Layer / Message Bus（通道层 / 消息总线）

目标位置:

- `zingclaw/channels/`
- `zingclaw/bus/`

职责: 在完整 nanobot 方向里，这两层负责把“平台消息”与“agent 核心”解耦。

当前实际情况:

- 这两层都还没有实现
- 当前只有 CLI 直接调用 `AgentLoop`

当前状态: `未实现`

这也是当前项目和 nanobot 最大的结构差异之一。

---

## 2.3 当前数据流分析

### 2.3.1 当前可运行的完整流程

当前 ZingClaw 的真实数据流是：

```text
用户在 CLI 输入消息
    ↓
CLI 调用 AgentLoop.process_message(...)
    ↓
SessionManager.get_or_create(session_key)
    ↓
session.add_message("user", ...)
    ↓
ContextBuilder.build_messages(...)
    ↓
AgentRunner.run(...)
    ↓
Provider.chat_with_retry(...)
    ↓
如果有 tool_calls:
    ToolRegistry.execute(...)
    ↓
    tool result 追加回 messages
    ↓
    继续调用 Provider
否则:
    得到 final_content
    ↓
AgentLoop 把 assistant / tool 消息写回 session
    ↓
CLI 输出最终结果（现在支持流式文本）
```

### 2.3.2 当前和 nanobot 数据流的主要差异

nanobot 的完整消息流是：

```text
Channel -> MessageBus -> AgentLoop -> MessageBus -> Channel
```

当前 ZingClaw 是：

```text
CLI -> AgentLoop -> CLI
```

所以现在少掉的是：

- 平台接入层
- inbound/outbound 标准消息模型
- 消息总线
- 多消费者异步解耦

---

## 2.4 当前能力完成度

| 组件 | 当前状态 | 说明 |
| --- | --- | --- |
| CLI | 已实现 | 支持单轮与交互式会话 |
| Provider | 已实现 | OpenAI-compatible + Kimi 兼容 + 流式 |
| Tool Registry | 已实现 | 内置工具已可注册和执行 |
| Filesystem Tools | 已实现 | 含 `edit_file` |
| Shell Tool | 已实现 | 工作区限制、超时、截断 |
| Session | 已实现 | JSONL 持久化与历史回放 |
| Context Builder | 已实现 | P0 版本，尚无 memory/skills |
| Runner | 已实现 | P0 工具循环 |
| Loop | 已实现 | 单轮编排与持久化 |
| MCP | 部分实现 | 只有 wrapper 骨架 |
| Memory | 未实现 | 目录存在，逻辑未做 |
| Skills | 未实现 | 尚未开始 |
| Message Bus | 未实现 | 尚未开始 |
| Channels | 未实现 | 尚未开始 |
| WebUI/API | 未实现 | 尚未开始 |

---

## 2.5 如果要往“完整 nanobot 方向”继续做，接下来该补什么

建议把后续工作拆成三层，不要一口气平铺。

### 第一层：先补 nanobot 的结构骨架

优先级最高：

1. `bus/`
2. `channels/`
3. 标准消息模型 `InboundMessage / OutboundMessage`
4. provider registry / factory

原因:

- 这是从“CLI agent”变成“agent framework”的关键一步
- 不补这层，后面技能、平台接入、WebUI 都没有稳定挂点

### 第二层：补 nanobot 的能力增强层

优先级中等：

1. `memory`
2. `skills`
3. 真实 MCP 接入
4. 更多工具

原因:

- 这些决定 agent 是否像一个“长期工作的助手”
- 但它们依赖前面的主结构已经稳定

### 第三层：补产品化层

优先级后置：

1. WebUI / API
2. runtime events / JSONL trace
3. command router
4. 更复杂的交互和可观测性

---

## 2.6 推荐的下一步实现顺序

如果你现在明确要往“完整 nanobot 方向”靠，而不是继续只做单机 CLI demo，我建议顺序是：

1. 先实现 `bus/`
   - 目标: 让消息进入和消息输出有统一接口

2. 再实现 `message schema`
   - 目标: 定义 `InboundMessage / OutboundMessage`

3. 再实现 `channels/base.py` 和 `channels/manager.py`
   - 目标: 即使暂时只有 CLI channel，也先把结构搭出来

4. 把当前 CLI 改造成一个真正的 `CLI Channel`
   - 目标: 让 CLI 也走 `Channel -> Bus -> AgentLoop`

5. 再补 `provider registry / factory`
   - 目标: 为多 provider 扩展打底

6. 然后开始做 `memory`
   - 目标: 让 context builder 真正接上记忆层

7. 再做 `skills`
   - 目标: 让“按需扩展能力”成立

这个顺序的好处是：

- 先补结构，再补能力
- 以后迁移到 Telegram / Discord / WebUI 时不会重写主链路
- 代码会越来越像 nanobot，而不是越来越像“单机 CLI 脚本”

---

## 2.7 一句话总结当前项目

当前 ZingClaw 已经不是骨架了，而是一个 **能跑通 CLI -> Session -> Context -> Runner -> Tools -> Provider 的本地单用户 Agent 原型**。

如果继续朝 nanobot 方向演进，真正的分水岭不是再多加一个工具，而是：

**把当前“CLI 直连 AgentLoop”的结构，升级成“Channel / Bus / AgentLoop 解耦”的结构。**
