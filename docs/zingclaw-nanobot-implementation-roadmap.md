# ZingClaw 向完整 nanobot 演进的实施路线图

这份文档基于当前 `ZingClaw` 的真实实现状态整理，目标不是一次性照搬 `nanobot`，而是给出一条可以渐进落地的实施路径。

建议配合以下文档一起看：

- `docs/zingclaw-core-components.md`
- `docs/vibe-clone-implementation-plan.md`
- `reference/nanobot/`

---

## 1. 总体策略

### 1.1 实施原则

- 先补结构骨架，再补能力层，最后补产品层。
- 优先复用当前已经跑通的 `provider / tools / session / loop / cli`，不要推倒重来。
- 每完成一个模块，都要保持 `zingclaw chat` 仍然可用。
- 新模块先做“最小可用骨架”，再补 nanobot 的完整能力。
- 内置工具和未来 MCP 工具继续保持统一调用接口，不在 `runner` 里分叉逻辑。

### 1.2 推荐顺序

1. `message schema`
2. `bus`
3. `channels`
4. `CLI Bus Adapter` 改造
5. `provider registry / factory`
6. `config schema` 扩展
7. `security / workspace policy`
8. `session` 增强
9. `memory`
10. `context builder` 升级
11. `skills`
12. `MCP` 真接入
13. `tools` 扩展
14. `runner / loop` 生命周期与 runtime events
15. `command router`
16. `API / WebUI / gateway`
17. `测试与验收`

这个顺序的核心原因是：先把 `CLI -> AgentLoop` 升级成 `Channel -> Bus -> AgentLoop -> Bus -> Channel`，后面的记忆、技能、MCP、WebUI 才有稳定落点。

---

## 2. 当前项目基线

当前 `ZingClaw` 已经具备这些基础能力：

- `zingclaw/providers/`：OpenAI-compatible provider，已支持 Kimi / Moonshot、tool calls、streaming。
- `zingclaw/tools/`：统一工具接口，已接入 filesystem、shell、真实 MCP runtime。
- `zingclaw/session/manager.py`：JSONL session 持久化。
- `zingclaw/agent/context.py`：P0 上下文组装。
- `zingclaw/agent/runner.py`：P0 LLM/tool 循环。
- `zingclaw/agent/loop.py`：单轮编排。
- `zingclaw/cli/main.py`：交互式 CLI，已支持流式输出。

所以接下来的重点，不是继续补零散工具，而是补 nanobot 的结构层。

---

## 3. 模块化实施步骤

## 模块 1：消息模型

- 模块名称：`bus/events.py`
- 当前状态：未实现
- 功能概括：定义 channel、agent、tool、UI 之间共享的统一消息对象。
- 具体要实现的东西：
- 定义 `InboundMessage`，至少包含 `channel`, `chat_id`, `session_key`, `message_id`, `sender_id`, `text`, `attachments`, `metadata`, `created_at`。
- 定义 `OutboundMessage`，至少包含 `channel`, `chat_id`, `session_key`, `reply_to`, `text`, `final`, `metadata`, `created_at`。
- 把当前 `AgentLoop.process_message(...)` 依赖的零散参数，逐步收敛到统一消息对象。
- 为未来 WebUI / WebSocket / 第三方 channel 预留 `attachments`、`raw_payload`、`thread_id` 等字段。
- 对应的参考文件：
- 当前项目：`zingclaw/agent/loop.py`
- 当前项目：`zingclaw/cli/main.py`
- 参考：`reference/nanobot/bus/events.py`
- 参考：`reference/nanobot/sdk/types.py`
- 完成标志：
- `AgentLoop` 可以接收一个统一的 inbound 对象，而不是依赖 CLI 私有参数拼装。
- 说明：
- 这一模块只负责 `InboundMessage / OutboundMessage`。
- `runtime events` 不放在这里，而是在模块 14 单独实现 `bus/runtime_events.py`，这样更贴近 `nanobot` 的结构。

## 模块 2：消息总线

- 模块名称：`bus/queue.py`
- 当前状态：未实现
- 功能概括：提供一个接近 `nanobot` 的队列式消息总线，专门负责用户消息进出，不负责运行时状态订阅。
- 具体要实现的东西：
- 实现一个最小异步 `MessageBus`，内部至少有 `inbound queue` 和 `outbound queue`。
- 提供 `publish_inbound()`、`consume_inbound()`、`publish_outbound()`、`consume_outbound()` 这组基础接口。
- 可以补充 `inbound_size`、`outbound_size` 这类轻量状态读取，方便调试和观测。
- `AgentLoop` 所在运行层自己起循环去 `consume_inbound()`，而不是在 bus 里注册消费者。
- `Channel` / `ChannelManager` 自己起循环去 `consume_outbound()`，而不是让 bus 做订阅分发。
- 这一层只负责“消息搬运”，不负责命令路由、状态广播、日志订阅、WebUI 实时事件推送。
- `runtime events` 不放在这里；订阅式事件总线放到模块 14 单独实现。
- 对应的参考文件：
- 当前项目：`zingclaw/agent/loop.py`
- 参考：`reference/nanobot/bus/queue.py`
- 参考：`reference/nanobot/bus/events.py`
- 完成标志：
- CLI / Channel 可以先把 `InboundMessage` 放进 bus。
- `AgentLoop` 可以从 bus 取出 inbound 处理后，再把 `OutboundMessage` 放回 bus。
- CLI / Channel 可以再从 bus 取出 outbound 并完成实际回复。
- 整条链路已经变成 `Channel -> Bus -> AgentLoop -> Bus -> Channel`。

## 模块 3：Channel 基础设施

- 模块名称：`channels/base.py`、`channels/manager.py`
- 当前状态：未实现
- 功能概括：统一不同输入输出平台的接入方式。
- 具体要实现的东西：
- 定义 `Channel` 抽象基类，至少包含 `start()`、`stop()`、`publish_inbound()`、`deliver_outbound()`。
- 定义 channel 的基础元信息，例如 `name`、`kind`、`supports_streaming`。
- 实现 `ChannelManager`，负责加载、启动和关闭多个 channel。
- 先不做自动扫描也可以，第一版可以显式注册，第二版再模仿 nanobot 加入发现机制。
- 规定 channel 和 bus 的边界：channel 只负责平台消息收发，不负责业务逻辑。
- 对应的参考文件：
- 当前项目：`zingclaw/cli/main.py`
- 参考：`reference/nanobot/channels/base.py`
- 参考：`reference/nanobot/channels/manager.py`
- 参考：`reference/nanobot/channels/registry.py`
- 完成标志：
- `ZingClaw` 可以同时挂一个或多个 channel，而不是 CLI 硬编码唯一入口。

## 模块 4：CLI Bus Adapter 改造

- 模块名称：`cli/main.py`、`cli/chat_runtime.py` 或等价 CLI 接入层
- 当前状态：未实现，现有 CLI 直接调用 loop
- 功能概括：把 CLI 从“唯一入口”改造成“直接对接 MessageBus 的本地交互入口”，而不是硬塞进 `channels/` 体系。
- 具体要实现的东西：
- 在 CLI 里创建 `MessageBus`，并把聊天输入转换成 `InboundMessage` 发布到 bus。
- 让 `AgentLoop` 运行在一条后台消费链路里，从 bus 取 inbound，再把 `OutboundMessage` 放回 bus。
- CLI 自己维护 outbound 消费循环，读取属于当前会话的 `OutboundMessage` 并处理终端打印。
- 保持 CLI 作为“本地前端壳”的定位，不要求它实现 `BaseChannel`。
- `/help`、`/exit`、`/model`、`/session`、`/clear` 先保留在 CLI 层，后面再逐步迁到 command router。
- 保持现有用户体验不变，先做结构迁移，不先加新功能。
- 对应的参考文件：
- 当前项目：`zingclaw/cli/main.py`
- 当前项目：`zingclaw/agent/loop.py`
- 当前项目：`zingclaw/agent/runner.py`
- 参考：`reference/nanobot/cli/commands.py`
- 参考：`reference/nanobot/cli/stream.py`
- 参考：`reference/nanobot/bus/queue.py`
- 完成标志：
- `zingclaw chat` 仍然能正常跑，但调用链已经变成 `CLI -> Bus -> AgentLoop -> Bus -> CLI`。

## 模块 5：Provider Registry / Factory

- 模块名称：`providers/registry.py`、`providers/factory.py`
- 当前状态：未实现，当前只有单一 provider 构造路径
- 功能概括：把 provider 从“一个类”升级成“可扩展 provider 体系”。
- 具体要实现的东西：
- 抽出 provider 注册表，支持通过 provider 名称查找实现类。
- 实现 provider factory，根据配置创建具体 provider。
- 让当前 `OpenAICompatProvider` 通过 registry/factory 创建，不再由 CLI 或 loop 直接 new。
- 为将来引入 `OpenAI Responses`、`Anthropic`、`Azure`、`fallback provider` 留好接口。
- 统一 provider 能力声明，例如 `supports_streaming`、`supports_tools`、`supports_reasoning`。
- 对应的参考文件：
- 当前项目：`zingclaw/providers/base.py`
- 当前项目：`zingclaw/providers/openai_compat.py`
- 参考：`reference/nanobot/providers/registry.py`
- 参考：`reference/nanobot/providers/factory.py`
- 参考：`reference/nanobot/providers/base.py`
- 完成标志：
- 入口层只知道“我要一个 provider”，不再关心具体类名和构造细节。

## 模块 6：配置系统扩展

- 模块名称：`config/schema.py`、`config/loader.py` 或在 `config/settings.py` 上扩展
- 当前状态：部分实现
- 功能概括：把现在的最小配置升级成支持 channel、provider、memory、MCP、WebUI 的统一配置体系。
- 具体要实现的东西：
- 保留当前 `Settings` 的简单使用方式，但逐步拆成更清晰的配置结构。
- 增加 `provider`、`channels`、`memory`、`mcp`、`logging`、`api`、`webui` 相关配置。
- 明确 home 目录下的标准目录布局，例如 `workspace/`、`sessions/`、`memory/`、`logs/`、`skills/`。
- 如果你想更像 nanobot，可以增加 JSON 配置文件加载，再兼容环境变量覆盖。
- 统一路径构造逻辑，避免各模块自行拼路径。
- 对应的参考文件：
- 当前项目：`zingclaw/config/settings.py`
- 参考：`reference/nanobot/config/schema.py`
- 参考：`reference/nanobot/config/loader.py`
- 参考：`reference/nanobot/config/paths.py`
- 完成标志：
- 新增 channel、memory、MCP 时，不需要再在多个模块里手写路径和配置分发。

## 模块 7：安全与工作区策略

- 模块名称：`security/workspace_access.py`、`security/workspace_policy.py`
- 当前状态：未实现，现有安全逻辑散落在工具实现中
- 功能概括：把 workspace 边界和危险操作限制抽成统一安全层。
- 具体要实现的东西：
- 抽出统一路径解析器，供 filesystem、shell、future MCP tools 共用。
- 明确允许访问的根目录和禁止逃逸规则。
- 统一处理绝对路径、相对路径、Windows 路径、软链接等场景。
- 将 shell 工具的 cwd 校验、filesystem 工具的 path 校验收口到安全模块。
- 为后续更细粒度权限策略预留扩展点，例如只读模式、禁网模式、特定目录白名单。
- 对应的参考文件：
- 当前项目：`zingclaw/tools/filesystem.py`
- 当前项目：`zingclaw/tools/shell.py`
- 当前项目：`zingclaw/security/__init__.py`
- 参考：`reference/nanobot/security/workspace_access.py`
- 参考：`reference/nanobot/security/workspace_policy.py`
- 参考：`reference/nanobot/agent/tools/path_utils.py`
- 完成标志：
- 工具层不再各自重复做路径安全判断，所有工作区约束统一收敛。

## 模块 8：Session 增强

- 模块名称：`session/manager.py`、`session/goal_state.py`
- 当前状态：已实现第一版
- 功能概括：实现会话状态层（session layer），让 session 从“历史存档”升级成“长期运行状态容器”，并为后续 memory / consolidation 提供输入与状态边界。
- 具体要实现的东西：
- 在现有 JSONL session 的基础上增加更明确的 metadata 管理。
- 补充会话摘要、压缩、清理策略。
- 为未来 `goal state`、`turn continuation`、`fork session` 预留结构。
- 增加“会话最近活跃时间”、“自动 compact 时机”、“会话标签”等状态。
- 如果后续做 WebUI，需要支持 session 列表、元信息读取、删除、重命名。
- 对应的参考文件：
- 当前项目：`zingclaw/session/manager.py`
- 参考：`reference/nanobot/session/manager.py`
- 参考：`reference/nanobot/session/goal_state.py`
- 参考：`reference/nanobot/session/turn_continuation.py`
- 完成标志：
- session 不只是保存消息，还能承载长任务、摘要和恢复运行所需状态。
- 这部分负责管理近程会话状态与原始对话回放，是后续 memory consolidation 的上游输入，而不是 `memory.py` 本身。

## 模块 9：Memory 系统

- 模块名称：`memory/` 或 `agent/memory.py`
- 当前状态：未实现，目录存在但没有真正逻辑；但依赖前置已经具备，因为模块 8 已经提供了 session 状态层。
- 功能概括：补全 memory 子系统，包括长期记忆（`MEMORY.md`）、历史归档 / 近期摘要（`history.jsonl` / `HISTORY.md`）以及 consolidation / 上下文压缩。
- 具体要实现的东西：
- 先明确模块边界：
- 模块 8 负责 `sessions/*.jsonl`、session metadata、goal state、continuation 等会话状态。
- 本模块负责 `MEMORY.md`、`history.jsonl` / `HISTORY.md`、recent summary、consolidation 和 memory 注入。
- 第一版建议实现一个简化但结构正确的 memory 闭环：
- `session.messages -> consolidator -> history / MEMORY / session summary -> context injection`
- 先做 `MemoryStore`，统一管理 `MEMORY.md`、`history.jsonl`、最近处理 cursor、历史读取与裁剪等能力。
- `history.jsonl` 作为主历史归档格式，`HISTORY.md` 只保留为兼容旧资料或迁移说明，不建议作为新实现主路径。
- 定义何时触发 consolidation，建议优先按“上下文 / token 预算接近上限”触发；第一版也可以保留“手动触发”作为补充入口。
- consolidation 的输出至少分成两类：
- 一类写入长期记忆，例如用户偏好、项目事实、稳定约束。
- 一类写入历史摘要，例如本轮/本阶段做了什么、得到了什么结论。
- consolidation 的输入源应明确来自 session 的旧消息，而不是单独扫描一个历史文件凑阈值。
- consolidation 完成后要能更新 session 状态，例如 `last_consolidated` 和最近摘要 metadata。
- 为 context builder 提供统一 memory 注入接口，让后续模块 10 能把 `MEMORY.md`、recent summary、未压缩历史分层拼进去。
- 第一版不要一上来实现 `nanobot` 完整的 Dream 两阶段流水线，但要保留与它一致的总方向：先有 session 作为原始会话来源，再通过 consolidation 进入 history / memory 层。
- 对应的参考文件：
- 当前项目：`zingclaw/memory/__init__.py`
- 当前项目：`zingclaw/agent/context.py`
- 当前项目：`zingclaw/session/manager.py`
- 参考：`reference/nanobot/agent/memory.py`
- 参考：`reference/nanobot/templates/memory/MEMORY.md`
- 参考：`reference/nanobot/templates/agent/dream.md`
- 完成标志：
- 长对话不会无限堆历史；agent 能从长期偏好和近期摘要里取信息。
- roadmap 语义上要清楚区分：
- `session` 管原始会话状态与对话回放。
- `history` 管从 session 提炼出的历史轨迹 / 近期摘要，主存储格式优先 `history.jsonl`。
- `MEMORY` 管稳定长期事实。
- 本模块新增的是 memory subsystem，不是重新发明 session manager。

## 模块 10：Context Builder 升级

- 模块名称：`agent/context.py`
- 当前状态：已实现第二版（模板化 + skills 接入）
- 功能概括：把当前 P0 上下文拼装升级成更接近 nanobot 的多层上下文。
- 具体要实现的东西：
- 把当前 system prompt 拆成更清晰的模板段落。
- 正式接入 `AGENTS.md`、`SOUL.md`、`USER.md`、`MEMORY.md`、skills summary。
- 加入 memory 内容、MCP runtime lines、session 摘要、近期历史裁剪。
- 支持按 channel 注入平台策略，避免以后 Telegram / WebUI 还要改主逻辑。
- 为图片、附件、结构化输入预留扩展位。
- 当前这一版已经完成：
- identity / platform policy / channel format hint 的分层 system prompt。
- workspace 优先、home 兜底的 `AGENTS.md` / `SOUL.md` / `USER.md` 注入。
- `MEMORY.md`、recent archived history、session summary 的分层注入。
- `skills/*/SKILL.md` 的 summary 注入，以及 `always skills` 的完整内容注入。
- 当前用户消息中的 runtime context 扩展位，以及图片 `media` 输入块支持。
- system prompt 已抽成模板文件，而不是继续把主要提示词硬编码在 `context.py`。
- 后续仍可继续增强：
- 真正的 MCP runtime 状态接入放到模块 12。
- 更复杂的结构化输入和多模态策略可以继续在本模块上迭代。
- 对应的参考文件：
- 当前项目：`zingclaw/agent/context.py`
- 当前项目：`zingclaw/config/settings.py`
- 当前项目：`zingclaw/utils/prompt_templates.py`
- 当前项目：`zingclaw/templates/agent/identity.md`
- 当前项目：`zingclaw/templates/agent/platform_policy.md`
- 当前项目：`zingclaw/templates/agent/tool_contract.md`
- 当前项目：`zingclaw/templates/agent/skills_section.md`
- 参考：`reference/nanobot/agent/context.py`
- 参考：`reference/nanobot/templates/AGENTS.md`
- 参考：`reference/nanobot/templates/SOUL.md`
- 参考：`reference/nanobot/templates/USER.md`
- 参考：`reference/nanobot/templates/agent/tool_contract.md`
- 参考：`reference/nanobot/templates/agent/skills_section.md`
- 完成标志：
- 上下文不再只是“system + history + user + tools”，而是有完整层次和可裁剪策略。

## 模块 11：Skills 系统

- 模块名称：`skills/`、`agent/skills.py`
- 当前状态：已实现第二版（loader + always skills + disabled skills）
- 功能概括：让 agent 能按需加载和展示“技能包”，而不是把所有能力硬塞进系统提示词。
- 具体要实现的东西：
- 定义 skill 的目录结构，至少支持 `SKILL.md`。
- 实现 skill loader，能够列出、读取、摘要 skill。
- 第一版不必做复杂执行逻辑，先把 skills summary 放进 context。
- 第二版再加 `help/run` 两段式调用机制。
- 技能内容和工具体系分开管理：skill 负责“告诉 agent 如何工作”，tool 负责“提供可执行能力”。
- 当前这一版已经完成：
- 支持 `workspace / configured / builtin` 三层 skill 来源，且按优先级去重覆盖。
- 支持 `SKILL.md` frontmatter 解析，包括 `description`、`always`、`requires` 等字段。
- 支持区分 `always skills` 和普通 skills：
- `always skills` 全文注入 context。
- 其他 skills 以 summary 形式注入 context。
- 支持最小可用性检查，能把缺失 CLI / ENV 的 skill 标记为 unavailable。
- 支持通过配置禁用指定 skills，且会同步影响 discovery、summary 和 context 注入。
- 已增加内置 `memory`、`github`、`weather` skills，作为 builtin skills 的第一批落地。
- `agent/skills.py` 的主接口与加载流程已基本对齐 `nanobot`，只额外保留了 `configured skills_dir` 这一层扩展。
- 后续仍可继续增强：
- 增加更完整的 builtin skill 集合，例如 long-goal、github、cron 等。
- 增加“按需激活 / help-run 两段式”机制，而不只是 context 注入。
- 让 skills 与未来的 MCP / command / WebUI 配置形成联动。
- 建议按依赖分批接入现有 `nanobot` skills：
- `github` / `weather`：几乎可以直接接入，只依赖 shell + 外部 CLI / HTTP。
- `summarize`：shell 稳定后即可作为“外部 CLI skill”接入；如果不想依赖外部 `summarize` CLI，则放到模块 13 做原生 `summarize/web/transcribe` 工具后再接。
- `memory`：当前先保留 ZingClaw 适配版；等模块 9 的 memory 子系统彻底稳定、模块 13 补上历史搜索工具后，再进一步向 `nanobot` 文案对齐。
- `skill-creator`：放在模块 13 之后接入，前提是 filesystem / edit / search / 模板脚手架能力够完整。
- `long-goal`：放在模块 8 + 模块 13 + 模块 14 之后接入；依赖 `goal_state`、`long_task/complete_goal` 工具、runtime context 持续暴露。
- `cron`：放在模块 13 + 模块 14 之后接入；依赖定时任务执行器、调度注入、运行时事件和恢复逻辑。
- `clawhub`：放在最晚接入，等模块 13 / 16 具备远程 skill 拉取、安装、配置落盘和可观测性后再做。
- 对应的参考文件：
- 当前项目：`zingclaw/agent/context.py`
- 当前项目：`zingclaw/agent/skills.py`
- 当前项目：`zingclaw/skills/memory/SKILL.md`
- 参考：`reference/nanobot/agent/skills.py`
- 参考：`reference/nanobot/skills/README.md`
- 参考：`reference/nanobot/skills/memory/SKILL.md`
- 参考：`reference/nanobot/skills/long-goal/SKILL.md`
- 完成标志：
- 可以在不改主代码的前提下，增加新的 skill 并让模型感知到它。

## 模块 12：MCP 真接入

- 模块名称：`tools/mcp.py` 及相关 runtime
- 当前状态：已实现可运行主线，正在向更完整的 `nanobot` 形态增强
- 功能概括：把 MCP 从“占位接口”升级成真正可连接、可执行、可管理、可热重载的统一外部能力来源。
- 具体要实现的东西：
- 设计 MCP server 配置结构。
- 实现 MCP server 生命周期管理，例如启动、连接、关闭、健康检查。
- 把远端 MCP tools 拉平为本地统一 `Tool` 接口，继续走 `ToolRegistry`。
- 处理 MCP 工具的 schema 转换、错误归一化、超时、输出截断。
- 为 WebUI 未来的 MCP preset / runtime 配置预留接口，并和 session / runtime 状态打通。
- 当前这一版已经完成：
- 支持 `stdio` MCP server 直连，Windows 下补了命令启动兼容处理。
- 支持 `streamable_http`，并兼容 `http` / `streamable-http` 配置写法。
- 已补 `sse` 传输接入口，整体传输层更贴近 nanobot。
- 支持把 MCP `tools / resources / prompts` 统一注册到本地 `ToolRegistry`。
- 支持 `enabled_tools` 过滤、`tool_timeout`、`headers`、基础重连一次。
- 已把 MCP schema 归一化进一步收紧到更贴近 OpenAI function calling 的子集，例如去掉 `$schema` / `additionalProperties`，修正 `required` 结构。
- `AgentLoop` 会在每轮消息前尝试连接 MCP，并把 runtime 状态注入上下文。
- 已补 `status / reload` 第一版：可查看每个 server 的连接状态、能力数量、tool prefix，以及 `tools/resources/prompts` 的拆分计数，并可在运行中的 CLI 会话里手动 reload。
- 增加了真实本地 `stdio` / `streamable_http` / `sse` MCP server 测试夹具，不再只是 wrapper 级别单测。
- 已补 `mcp_presets` 的 session attachment / runtime lines 第一版，后续轮次会持续提示模型优先使用对应前缀工具。
- 已把 MCP reload 接到 `MessageBus` 控制消息链路，运行中的 loop 可以通过 bus/event 触发热重载，而不是只靠直接方法调用。
- 这一版还没完全做完的地方：
- `mcp_presets` 目前已具备 session 附着和 runtime lines，但还没有完整的 preset 管理 API / UI。
- MCP runtime 状态已可在 CLI 查看，但还没有完整暴露给未来 WebUI / settings 面板。
- 目前重点是“可用、稳定、统一接入”，还没把所有 nanobot 风格的管理体验都补齐。
- 下一步建议补强顺序：
- 第一步：补完整的 preset 管理能力，例如 attach / detach / list / clear。
- 第二步：继续增强 hot reload 和 reconnect 观测信息，让失败原因更容易排查。
- 第三步：为未来 WebUI 补运行时状态读取接口，让前端可以展示 server 列表、能力和连接状态。
- 对应的参考文件：
- 当前项目：`zingclaw/tools/mcp.py`
- 当前项目：`zingclaw/tools/registry.py`
- 参考：`reference/nanobot/agent/tools/mcp.py`
- 参考：`reference/nanobot/webui/mcp_presets_runtime.py`
- 参考：`reference/nanobot/webui/mcp_presets_api.py`
- 完成标志：
- 内置工具和 MCP 工具都能通过同一个 registry 暴露给模型，并正常执行。
- MCP server 可以在不重启主进程的前提下查看状态、触发 reload、并把关键 runtime 信息稳定注入上下文。

## 模块 13：工具体系扩展

- 模块名称：`tools/`
- 当前状态：已实现主工具带，但还没补齐 `nanobot` 里的全部内置工具
- 功能概括：在现有 filesystem 和 shell 基础上，逐步补齐 `nanobot` 常用工具族，并让后续 skills、goal、cron、MCP 都有稳定依赖面。
- 具体要实现的东西：
- 先补强已有工具，确保 `filesystem`、`shell`、`edit_file`、输出截断、错误返回都稳定。
- 第二批建议补：`search`、`web`、`message`、`spawn`、`exec_session`。
- 如果目标是尽快承接 `nanobot` 现有 skills，则这一阶段的优先级建议改成：
- 第一优先级：`grep/search`、`web/fetch`、更稳定的 `shell`
- 第二优先级：`long_task`、`complete_goal`
- 第三优先级：`cron` 执行器 / 调度工具
- 第四优先级：skill 模板脚手架 / skill 安装辅助能力（为 `skill-creator` / `clawhub` 做准备）
- 当前这一版已经完成：
- `apply_patch` 第一版：结构化多文件补丁编辑
- `filesystem` 第一版：`list_dir`、`read_file`、`write_file`、`edit_file`
- `shell` 第一版：`exec`
- `exec_session` 第一版：`write_stdin`、`list_exec_sessions`，且 `exec` 已支持 `yield_time_ms`
- `search` 第一版：`find_files`、`grep`
- `web` 第一版：`web_search`、`web_fetch`
- `message` 第一版：向当前会话输出 agent 主动消息
- `spawn` 第一版：`spawn`、`list_subagents`、`cancel_subagent`
- `cron` 第一版：统一调度工具入口
- `goal` 第一版：`long_task`、`complete_goal`
- `sandbox` 第一版：为 `exec` 提供可选 shell sandbox backend，先支持 `bwrap`
- 还没有补齐但值得继续做的工具：
- `image_generation`：后续如果要承接图像相关 skill 或 WebUI 富媒体能力，会很有用。
- `file_state` / `self` / `cli_apps` 这类偏高级、偏运行时管理工具，可以放在基础能力稳定后再做。
- 需要区分“基础工具”和“高级工具”。
- 基础工具优先级：文件、shell、搜索、补丁。
- 高级工具优先级：子 agent、长任务、定时任务、图像生成。
- 工具 discover/load 机制继续沿着当前 `ToolLoader` 演进，后面再考虑自动扫描。
- 这一模块完成后，可立即落地的 skill 包括：
- `github`
- `weather`
- `summarize`（如果选择外部 CLI 路线）
- `skill-creator`（如果同时补了模板脚手架）
- 这一模块完成一半后，可开始为这些 skill 做承接工具：
- `long-goal` -> `long_task` / `complete_goal`
- `cron` -> 定时任务工具 / 调度器
- `clawhub` -> 远程 skill 拉取 / 安装辅助
- 当前推荐的后续实现顺序：
- 第一步：继续补强现有工具的稳定性和可观测性，尤其是 `shell`、`exec_session`、`spawn`、`cron`。
- 第二步：补 `image_generation` 或 `sandbox` 这类对展示度 / 安全性帮助明显的工具。
- 第三步：再考虑 `file_state`、`self`、`cli_apps` 这类更偏“完整 agent 运行时”的工具。
- 对应的参考文件：
- 当前项目：`zingclaw/tools/base.py`
- 当前项目：`zingclaw/tools/registry.py`
- 当前项目：`zingclaw/tools/loader.py`
- 当前项目：`zingclaw/tools/filesystem.py`
- 当前项目：`zingclaw/tools/shell.py`
- 当前项目：`zingclaw/tools/exec_session.py`
- 当前项目：`zingclaw/tools/spawn.py`
- 当前项目：`zingclaw/tools/cron.py`
- 当前项目：`zingclaw/tools/sandbox.py`
- 参考：`reference/nanobot/agent/tools/loader.py`
- 参考：`reference/nanobot/agent/tools/search.py`
- 参考：`reference/nanobot/agent/tools/web.py`
- 参考：`reference/nanobot/agent/tools/message.py`
- 参考：`reference/nanobot/agent/tools/spawn.py`
- 参考：`reference/nanobot/agent/tools/exec_session.py`
- 参考：`reference/nanobot/agent/tools/image_generation.py`
- 参考：`reference/nanobot/agent/tools/sandbox.py`
- 完成标志：
- `ToolRegistry` 仍是唯一调用入口，但工具来源和种类显著扩展。

## 模块 14：Runner / Loop 生命周期与 Runtime Events

- 模块名称：`agent/runner.py`、`agent/loop.py`、`bus/runtime_events.py`
- 当前状态：已实现第一版主链路，后续可继续增强消费端体验
- 功能概括：在消息总线之外，单独实现“运行时事件总线”，把执行状态、进度和内部生命周期暴露给 CLI、日志和未来 WebUI。
- 具体要实现的东西：
- 在这一阶段单独实现 `runtime event bus`，不要和 `InboundMessage / OutboundMessage` 混在同一个消息模型文件里。
- 明确区分两类总线：
- `MessageBus`：负责用户消息进出。
- `RuntimeEventBus`：负责进程内状态事件订阅与广播。
- `RuntimeEventBus` 采用订阅式接口，例如 `subscribe()`、`publish()`、`publish_nowait()`。
- 定义一组运行时事件类型，例如 `SessionTurnStarted`、`TurnRunStatusChanged`、`TurnCompleted`，而不是继续沿用一个松散的大字典事件模型。
- 为 `runner` 增加生命周期 hook，例如 `before_iteration`、`after_iteration`、`before_execute_tools`。
- 为工具调用、模型增量输出、错误恢复增加 runtime events。
- 把当前 CLI 的流式输出逻辑，从直接回调，逐步升级成“事件驱动 + 终端消费”。
- 让 `loop` 处理 session、memory、skills、bus 的整合，而不是只做最小编排。
- 为未来 cron、subagent、goal continuation 预留进入点。
- 如果要承接 `long-goal` 和 `cron`，这一模块还要额外负责：
- 把 active goal / goal summary 挂进 runtime context。
- 让后台触发的 cron 消息也走统一的 inbound/outbound + runtime events 链路。
- 为“继续上一次目标”“cron 触发了一轮任务”这类场景保留清晰事件类型。
- 所有“注册消费者 / 订阅事件”的逻辑，优先放在这一层，而不是放回模块 2 的 `MessageBus`。
- 当前这一版已经完成：
- 已新增独立 `RuntimeEventBus`，支持 `subscribe()`、`publish()`、`publish_nowait()`。
- 已新增一组明确事件类型：`SessionTurnStarted`、`TurnRunStatusChanged`、`TurnOutputDelta`、`ToolCallStarted`、`ToolCallCompleted`、`TurnCompleted`。
- 已新增 `agent/hook.py`，把 runner 生命周期从纯内部流程提升为可挂接的 hook 面。
- `AgentRunner` 已接入 `before_run`、`before_iteration`、`on_text_delta`、`before_execute_tools`、`after_iteration`、`after_run`、`on_error`、`on_finally`。
- `AgentLoop` 已在 turn 开始和结束时发布 turn 级 runtime events，并把 runner 内部事件链接入同一个 bus。
- 现在已经能稳定暴露这条事件链：`turn started -> building context -> running -> calling model -> executing tools / streamed delta -> completed`。
- CLI 已开始直接消费 `runtime events`，用于显示 `preparing context`、`thinking`、`tool start/done` 这类运行态信息，而不只依赖最终 outbound 文本。
- 已补直接单测，能看到事件顺序、状态值、session/channel/chat 字段和工具结果预览。
- 这一版还没完全做完的地方：
- CLI 目前仍是“outbound + runtime events”双通道，正文流式输出还主要依赖 outbound stream，没有完全切到纯 runtime-events 渲染。
- `cron`、`subagent`、`goal continuation` 还没有把自己的后台运行状态统一接进 runtime event bus。
- 还没有为未来 WebUI 做专门的 runtime event adapter / serializer。
- 对应的参考文件：
- 当前项目：`zingclaw/agent/runner.py`
- 当前项目：`zingclaw/agent/loop.py`
- 当前项目：`zingclaw/agent/hook.py`
- 当前项目：`zingclaw/bus/runtime_events.py`
- 参考：`reference/nanobot/agent/runner.py`
- 参考：`reference/nanobot/agent/loop.py`
- 参考：`reference/nanobot/agent/hook.py`
- 参考：`reference/nanobot/agent/progress_hook.py`
- 参考：`reference/nanobot/bus/runtime_events.py`
- 完成标志：
- 运行过程不再是黑箱，CLI、日志、未来 WebUI 都能消费同一套运行事件。
- 结构上也和模块 2 完成分层：
- 模块 2 只管消息队列。
- 模块 14 只管运行时事件订阅。

## 模块 15：命令路由

- 模块名称：`command/router.py`、`command/builtin.py`
- 当前状态：已实现第一版，CLI 聊天命令已接入统一 router
- 功能概括：把 `/help`、`/clear` 这类命令从 CLI 私有逻辑提炼成统一命令体系。
- 具体要实现的东西：
- 把当前 CLI 的 slash commands 抽象成命令处理器。
- 让命令执行结果也能返回标准 `OutboundMessage` 或 runtime event。
- 允许不同 channel 共享同一批命令，不再每个入口单独实现。
- 第一版只迁移现有命令即可，不先追求复杂权限和命令插件。
- 当前这一版已经完成：
- 已新增 `command/router.py`，支持 `exact` 和 `prefix` 两种命令分发方式。
- 已新增 `command/builtin.py`，把 `/help`、`/model`、`/session`、`/clear`、`/mcp`、`/mcp reload` 抽成统一 builtin handlers。
- 命令处理结果已统一返回 `OutboundMessage`，不再只是 CLI 里直接 `print` 分支。
- `cli/main.py` 已改为优先走 command router，再决定是否进入正常 agent turn。
- one-shot `zingclaw chat -m "/help"` 和交互式 chat 都会走同一套命令分发逻辑。
- ` /exit ` 这类纯终端生命周期控制命令目前仍保留在 CLI 壳层，没有强行塞进通用 command router，这样边界更清晰。
- 这一版还没完全做完的地方：
- 目前还只是 CLI chat 接入，其他 channel 还没有正式复用这套 command router。
- 还没有接入更复杂的权限、优先级中断命令和插件式命令扩展。
- 还没有把命令执行过程进一步接到 runtime events 做更细粒度可观测性。
- 对应的参考文件：
- 当前项目：`zingclaw/cli/main.py`
- 当前项目：`zingclaw/command/router.py`
- 当前项目：`zingclaw/command/builtin.py`
- 参考：`reference/nanobot/command/router.py`
- 参考：`reference/nanobot/command/builtin.py`
- 完成标志：
- slash commands 不再和 CLI 终端强绑定，未来 Telegram / WebUI 也能共用。

## 模块 16：API / WebUI / Gateway

- 模块名称：`api/`、`webui/`、`gateway/`
- 当前状态：已实现第一版 API / Gateway 主线、最小 WebSocket 会话层，以及最小可用 WebUI 页面
- 功能概括：把本地 CLI agent 升级成真正可接前端和外部调用的服务。
- 具体要实现的东西：
- 先做最小 API，例如 `/v1/chat/completions` 和 `/v1/models`。
- 再做 WebSocket 或等价流式接口，承载前端流式对话。
- 然后再考虑 WebUI，会话列表、设置页、日志查看、MCP 配置等。
- Gateway 的职责是聚合 API、WebUI、channel、runtime，而不是把业务都塞进 CLI。
- 如果你暂时只想做核心框架，这一块可以晚于 memory / skills / MCP。
- 当前这一版已经完成：
- 已新增 `gateway/events.py`，把 `runtime events` 和 `OutboundMessage` 统一编码成前端可消费的 `GatewayEvent`。
- 已新增 `gateway/runtime.py`，负责持有 `MessageBus + AgentLoop`，并把 runtime / outbound 事件扇出给订阅者。
- 已新增 `api/server.py`，提供：
- `GET /`
- `GET /webui`
- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `GET /runtime/events`（SSE）
- `GET /ws`（WebSocket）
- `GET /api/sessions`
- `POST /api/sessions`
- `GET /api/sessions/{key}`
- `GET /api/sessions/{key}/messages`
- `POST /api/sessions/{key}/rename`
- `POST /api/sessions/{key}/clear`
- `DELETE /api/sessions/{key}`
- `GET /api/mcp/status`
- `POST /api/mcp/reload`
- `POST /v1/chat/completions` 已支持：
- 非流式 JSON 返回
- OpenAI 风格 SSE 流式返回
- `GET /ws` 已支持第一版前端会话协议：
- 连接后返回 `ready`
- 前端发送 `user_message`
- 服务端返回 `accepted`
- 后续持续推送统一 `event` 帧，其中承载 `runtime events` 和 `outbound` 事件
- 现在还支持第一版 websocket session actions：
- `list_sessions`
- `get_session`
- `create_session`
- `switch_session`
- `rename_session`
- `clear_session`
- `delete_session`
- session 管理后端第一版已支持：
- session list
- session detail / messages
- create
- rename
- clear
- delete
- 最小 WebUI 页面已经落地：
- 左侧 session list
- 右侧 transcript
- runtime feed
- turn stage / active tools 可视化面板
- 输入框发送消息
- 顶部运行状态和 session 操作按钮
- MCP 状态面板和 reload 按钮
- 更丰富的消息气泡信息（时间等）
- 已新增 `zingclaw gateway` CLI 命令，能直接启动最小 HTTP gateway。
- 当前边界：
- 这已经不只是接口层，而是有了最小可用前端壳，但还不是完整 WebUI 产品。
- 运行时事件、会话管理、基础聊天页面已经可用，但设置页、MCP 管理页、日志页、完整前端路由还没开始。
- 目前对前端最有价值的是：
- `/runtime/events`：适合快速调试和轻量订阅
- `/ws`：适合真正的前端会话层
- `/api/sessions`：适合左侧会话列表和历史面板
- 对应的参考文件：
- 当前项目：`zingclaw/cli/main.py`
- 当前项目：`zingclaw/api/server.py`
- 当前项目：`zingclaw/gateway/runtime.py`
- 当前项目：`zingclaw/gateway/events.py`
- 参考：`reference/nanobot/api/server.py`
- 参考：`reference/nanobot/gateway/runtime.py`
- 参考：`reference/nanobot/gateway/service.py`
- 参考：`reference/nanobot/webui/ws_http.py`
- 参考：`reference/nanobot/webui/session_list_index.py`
- 完成标志：
- 第一版已经达到：
- `ZingClaw` 不再只是命令行 demo，而是已经具备最小服务化接入能力。
- 后续完整达标还需要：
- 更完整的 WebSocket 前端会话协议
- WebUI 页面与更完整会话管理
- 更丰富的 gateway 管理能力

## 模块 17：测试与验收

- 模块名称：`tests/`
- 当前状态：部分实现
- 功能概括：保证从当前 P0 向 nanobot 方向演进时，不把已经跑通的主链路改坏。
- 具体要实现的东西：
- 每新增一个结构模块，都补对应的最小单测。
- 为 `bus`、`channel`、`provider factory`、`memory`、`skills`、`MCP` 增加独立测试。
- 保留一条完整集成链路测试，至少覆盖 `channel -> bus -> loop -> runner -> provider -> tools -> session`。
- 保留手工验证脚本或文档，验证 `zingclaw chat`、session 隔离、流式输出、工具执行。
- 后面如果做 API / WebUI，再增加端到端 smoke test。
- 对应的参考文件：
- 当前项目：`tests/`
- 参考：`reference/nanobot/tests/` 对应结构
- 完成标志：
- 架构升级过程中，任何一块改动都能快速知道有没有把主链路打断。

---

## 4. 分阶段落地建议

### Phase 1：先把结构骨架补齐

优先实现：

1. 模块 1：消息模型
2. 模块 2：消息总线
3. 模块 3：Channel 基础设施
4. 模块 4：CLI Bus Adapter 改造

这一阶段结束后，目标是：

- `zingclaw chat` 仍然可用
- 但内部结构已经不再是 CLI 直连 loop
- 后续所有新入口都能复用这条主链路

### Phase 2：把基础设施从单例实现升级成框架实现

优先实现：

1. 模块 5：Provider Registry / Factory
2. 模块 6：配置系统扩展
3. 模块 7：安全与工作区策略

这一阶段结束后，目标是：

- provider、路径策略、配置路径都不再散落
- 后续引入新 provider、新工具、新 channel 时不用大改主链路

### Phase 3：补记忆和上下文层

优先实现：

1. 模块 8：Session 增强
2. 模块 9：Memory 系统
3. 模块 10：Context Builder 升级

这一阶段结束后，目标是：

- agent 具备清晰的“会话状态层 + memory 子系统”结构：
- `session layer (sessions/*.jsonl / metadata / goal state)` + `memory subsystem (MEMORY/history/summary/consolidation)`
- context 不再只堆历史消息，而是开始分层拼接：身份/引导文件/长期记忆/近期摘要/当前历史
- 当前实际进展：
- 模块 8、9、10 都已经有可运行实现；其中模块 10 已进入模板化版本。

### Phase 4：补扩展能力层

优先实现：

1. 模块 11：Skills
2. 模块 12：MCP 真接入
3. 模块 13：工具体系扩展

这一阶段结束后，目标是：

- agent 能按需扩展能力
- 内置工具与外部工具都纳入统一接口
- 当前实际进展：
- 模块 11、12 已有可运行实现；模块 12 已经进入“增强 preset / runtime 管理体验”的阶段。
- 当前重点转为继续补模块 13，把工具体系从“主链路可用”推进到“足以承接更多 nanobot skills 和后续展示能力”。

### Phase 5：补可观测性和产品层

优先实现：

1. 模块 14：Runner / Loop 生命周期与 Runtime Events
2. 模块 15：命令路由
3. 模块 16：API / WebUI / Gateway
4. 模块 17：测试与验收补强

这一阶段结束后，目标是：

- 项目具备完整的框架感和产品化形态
- 不是只能本地跑的 CLI demo
- 当前实际进展：
- 模块 14 已实现第一版主链路，运行时事件已经从消息总线中独立出来。
- 下一步可以转向模块 15，或者继续增强模块 14 的 CLI / cron / subagent 消费端接入。

---

## 5. 最值得先做的下一步

如果从当前代码基线出发，最推荐马上开始的是：

1. 新建 `zingclaw/bus/events.py`
2. 新建 `zingclaw/bus/queue.py`
3. 新建 `zingclaw/channels/base.py`
4. 新建 `zingclaw/channels/manager.py`
5. 把现有 `zingclaw/cli/main.py` 改造成 `CLI Bus Adapter + CLI shell`

原因很简单：

- 这是当前项目和 nanobot 的最大结构差异
- 这一步做完，后续 memory、skills、MCP、WebUI 都更容易接
- 这一步不会推翻你现在已经跑通的 provider、tools、session

---

## 6. 一句话总结

`ZingClaw` 现在已经是一个能跑通 `CLI -> Session -> Context -> Runner -> Tools -> Provider` 的本地 agent 原型；要继续向完整 `nanobot` 靠拢，下一阶段最关键的不是再补一个工具，而是先把它升级成 `Channel -> Bus -> AgentLoop` 的框架结构。
