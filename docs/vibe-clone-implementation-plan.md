# Vibe Coding 翻版实施路线

这份文档面向一个明确目标：

- 先基于 `nanobot` 的核心实现做出一个你自己的翻版主干
- 再逐步加入你想要的 `CyberClaw` 风格能力
- 最后补上评测、展示和简历包装

这不是“先把 `nanobot` 全部学完再开始”，而是“抓住主骨架，一边复刻一边理解，一边用 AI 加速实现”。

## 总体策略

项目推进分成 4 个阶段：

1. 复刻最小主干
2. 跑通完整对话回路
3. 加入自己的差异化能力
4. 补评测、文档和展示

总原则：

- 第一版先做 `CLI + 单 provider + 4~6 个工具 + session`
- 不一开始追求多渠道、多模型、多平台 UI
- 每迁一个模块就立刻验证
- 让 AI 负责速度，你自己负责范围、验收和取舍

## 阶段 0：先定项目边界

在新仓库开始前，先把下面 5 件事写清楚。

### 0.1 定义第一版产品

第一版建议定位：

- 一个透明、可控、可扩展的本地 AI Agent / Coding Claw
- 先服务单用户、本地工作区、CLI 交互

### 0.2 锁定第一版功能

第一版只做这些：

- CLI 入口
- OpenAI-compatible provider
- `list_dir`
- `read_file`
- `write_file`
- `exec`
- session 持久化
- 基本上下文拼接
- 模型工具调用循环

第一版先不做这些：

- WebUI
- 多渠道聊天接入
- MCP 全量兼容
- 图片、语音、邮件
- 多 provider 路由
- 心跳任务
- 复杂技能市场

### 0.2.1 P0 必须实现的功能清单

P0 是“翻版真正能跑起来”的最低要求。没有这些，项目还只是半成品。

#### 运行链路

- 用户输入一条消息
- 系统加载最近历史
- 系统构造发给模型的上下文
- 模型可请求工具调用
- 系统执行工具并返回结果
- 模型产出最终回复
- 系统保存本轮消息

#### CLI 交互

- 支持连续对话
- 支持 `/exit`
- 支持显示当前模型
- 支持显示当前 session
- 支持打印工具调用提示
- 支持显示最终回复

#### Provider 能力

- 支持 OpenAI-compatible chat 调用
- 支持传入 tools schema
- 支持获取普通文本回复
- 支持获取 tool calls
- 支持基础超时和异常处理

#### 工具能力

- `list_dir`
- `read_file`
- `write_file`
- `exec`

#### Session 能力

- 每个会话持久化到本地
- 能加载最近若干轮历史
- 能区分 `user/assistant/tool`
- 能在新一轮对话时带上历史

#### 安全边界

- 文件工具只允许操作 workspace
- shell 工具只允许在 workspace 内运行
- 超时中断
- 输出截断

#### 工程配套

- 最小单元测试
- 最小集成测试
- 可启动的 CLI demo

### 0.2.2 P1 第二阶段建议功能

P1 是“从翻版变成你的产品”的关键部分。

#### 记忆系统

- 长期画像 `user_profile.md`
- 近期摘要 `recent_summary.json` 或 SQLite
- 上下文裁剪策略
- 定期摘要旧消息

#### 透明可观测性

- `llm_input` 日志
- `tool_call` 日志
- `tool_result` 日志
- `ai_message` 日志
- `system_action` 日志
- 每轮一个 JSONL 轨迹文件

#### 路径与平台兼容

- Windows / Unix 路径归一化
- 禁止 `..`
- 禁止绝对路径逃逸
- 工作区限制
- shell 的 cwd 规范化

#### 工具增强

- `grep`
- `find_files`
- `get_model_info`
- `save_user_profile`

### 0.2.3 P2 展示和增强功能

P2 是“项目更像成品”的增强层，不要抢在 P0/P1 前面做。

#### 产品化功能

- WebUI
- 运行时间线面板
- 设置页
- session 列表
- 历史搜索

#### 扩展能力

- skills loader
- 两段式技能调用
- MCP 接入
- 定时任务系统
- WebSocket / API 接口

#### 展示能力

- demo 录屏
- 架构图
- benchmark / eval 报告
- README 功能页

### 0.2.4 第一版工具清单

建议你把工具分成 3 批，不要一口气做完。

#### 第一批工具：必须做

- `list_dir(path=".")`
- `read_file(path, start_line=None, end_line=None)`
- `write_file(path, content)`
- `exec(command, cwd=".")`

#### 第二批工具：建议做

- `grep(pattern, path=".")`
- `find_files(glob, path=".")`
- `get_model_info()`
- `save_user_profile(text)`

#### 第三批工具：后续再做

- `schedule_task(...)`
- `list_scheduled_tasks()`
- `delete_scheduled_task(task_id)`
- `modify_scheduled_task(task_id, ...)`
- `run_skill(name, mode="help"|"run")`

### 0.2.5 第一版 CLI 交互清单

建议第一版 CLI 具备这些交互：

- 输入普通消息
- `/exit` 退出
- `/model` 显示当前模型
- `/session` 显示当前 session key
- `/clear` 清空当前会话
- `/help` 显示基础命令

这几个命令足够做第一版 demo，也能让项目更像真正产品。

### 0.2.6 第一版非功能性要求

除了功能外，第一版还建议满足这些工程要求：

- 所有文件读写统一使用 UTF-8
- 所有路径统一通过 `pathlib.Path`
- 任何工具结果都有最大长度保护
- shell 默认超时 30~60 秒
- 错误信息对用户可读
- 日志文件落盘
- 代码结构清楚到你能现场讲解

### 0.3 定义你的差异化能力顺序

你的差异化功能建议按这个顺序加：

1. 双水位记忆系统
2. 跨平台路径拦截
3. 透明事件日志
4. 两段式技能调用
5. 定时任务系统

## 阶段 1：搭新仓库和最小环境

这一阶段的目标不是迁代码，而是先把“可运行骨架”搭起来。

### 1.1 新仓库目录建议

```text
yourclaw/
  app/
    agent/
    tools/
    providers/
    session/
    memory/
    security/
    config/
    cli/
  tests/
  workspace/
  sessions/
  memory/
  logs/
  docs/
  pyproject.toml
  README.md
```

### 1.2 最小开发环境

建议先建独立虚拟环境：

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install openai pydantic pydantic-settings typer rich httpx loguru pytest pytest-asyncio jinja2 pyyaml
```

第一阶段不要直接搬 `nanobot` 全部依赖，只按需安装。

### 1.3 最小配置

第一版配置只保留这些字段：

- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `MODEL_NAME`
- `WORKSPACE_DIR`
- `MAX_ITERATIONS`
- `MAX_TOOL_RESULT_CHARS`

### 1.4 这一阶段的完成标准

- 新仓库能创建虚拟环境
- 能通过 `python -m ...` 启动一个最小 CLI
- 能成功读取 `.env` 或环境变量

## 阶段 2：复刻最小主干

这一阶段是整个项目最重要的部分。目标是复刻 `nanobot` 的最小运行链路，而不是照搬整个工程。

## 2.1 先复刻哪些模块

建议优先实现以下模块：

- `config/settings.py`
- `providers/base.py`
- `providers/openai_compat.py`
- `tools/base.py`
- `tools/registry.py`
- `tools/filesystem.py`
- `tools/shell.py`
- `session/manager.py`
- `agent/context.py`
- `agent/runner.py`
- `agent/loop.py`
- `cli/main.py`

### 2.2 主要参考的 `nanobot` 文件

建议重点参考这些文件：

- `nanobot/agent/loop.py`
- `nanobot/agent/runner.py`
- `nanobot/agent/context.py`
- `nanobot/agent/tools/base.py`
- `nanobot/agent/tools/registry.py`
- `nanobot/agent/tools/filesystem.py`
- `nanobot/agent/tools/shell.py`
- `nanobot/session/manager.py`
- `nanobot/providers/base.py`
- `nanobot/providers/openai_compat_provider.py`
- `nanobot/config/schema.py`
- `nanobot/config/loader.py`

### 2.3 不要整包复制的区域

第一阶段先不要动这些目录：

- `nanobot/channels/`
- `nanobot/webui/`
- `nanobot/gateway/`
- `nanobot/apps/`
- `nanobot/audio/`
- `nanobot/bridge/`
- 大部分 `skills/`

## 阶段 3：按顺序实现模块

这一阶段必须按顺序推进，不要跳着做。

### Step 1：`settings.py`

先把配置对象写出来。

建议最小职责：

- 读取环境变量
- 定义 workspace 路径
- 定义模型名和 provider 参数
- 定义最大迭代次数

完成标准：

- 启动 CLI 时能打印当前模型和 workspace

### Step 2：`providers/base.py`

先抽象一个基础 provider 接口。

最小接口建议：

- `chat(messages, tools, model, max_tokens, temperature)`
- 返回最终文本或工具调用请求

完成标准：

- 你能在不依赖其他模块的情况下单独调用 provider

### Step 3：`providers/openai_compat.py`

先只接 OpenAI-compatible。

这一版只解决：

- 发消息给模型
- 传工具定义
- 获取普通文本回复
- 获取工具调用

完成标准：

- 传入一个简单消息能拿到文本结果
- 传入工具定义时能拿到 tool call

### Step 4：`tools/base.py` + `tools/registry.py`

这两个模块是主骨架的一部分。

最小能力：

- 工具 schema
- 注册工具
- 根据名字查工具
- 执行工具
- 处理参数错误

完成标准：

- 手工注册 2 个工具并成功调用

### Step 5：`tools/filesystem.py`

第一版只做这些工具：

- `list_dir`
- `read_file`
- `write_file`

要求：

- 用 `pathlib.Path`
- 所有路径都必须经过统一解析
- 输出做长度裁剪

完成标准：

- agent 能列目录、读文件、写文件

### Step 6：`tools/shell.py`

第一版只做最小 shell 执行能力。

要求：

- 工作目录必须受 workspace 限制
- 超时控制
- 输出截断
- Windows 路径和 shell 兼容

完成标准：

- agent 能执行简单命令
- 超时和错误能被正常返回

### Step 7：`session/manager.py`

先做最小 session 持久化。

建议：

- 每个 session 一个 JSONL 文件
- 存 `role/content/timestamp`
- 支持读取最近若干轮消息

完成标准：

- 连续两轮对话能保留上下文

### Step 8：`agent/context.py`

上下文构建器第一版只做：

- system prompt
- 最近历史
- 当前用户输入
- 已注册工具定义

完成标准：

- 每轮能稳定生成发给模型的 `messages`

### Step 9：`agent/runner.py`

这是模型执行循环的核心。

职责：

- 调模型
- 检查有没有 tool call
- 执行工具
- 把工具结果写回消息链
- 直到得到最终回复

完成标准：

- 一轮“模型 -> 工具 -> 模型 -> 最终答案”的链路能跑通

### Step 10：`agent/loop.py`

这一层处理“回合级”逻辑。

职责：

- 接收用户输入
- 读取 session
- 调用 context builder
- 调用 runner
- 保存消息
- 输出最终结果

完成标准：

- 用户在 CLI 输入一句话，系统能跑完一整轮并保存

### Step 11：`cli/main.py`

CLI 第一版做最小交互即可。

建议：

- 支持持续对话
- 支持 `/exit`
- 支持打印工具调用提示

完成标准：

- 本地能稳定连续跑 3~5 轮

## 阶段 4：每个模块都要做最小验证

不要等全部做完再一起测。

建议你按下面顺序验证：

1. provider 单测
2. registry 单测
3. filesystem tool 单测
4. shell tool 单测
5. session 单测
6. runner 集成测试
7. CLI 手工验证

建议每次迁一个模块时，同时带走或自己重写一个最靠近的测试。

## 阶段 5：把翻版跑通

这一阶段的目标是得到第一版完整链路：

```text
User Input
-> AgentLoop
-> ContextBuilder
-> AgentRunner
-> Provider
-> ToolRegistry
-> Tool Result
-> Final Answer
-> Session Save
```

### 5.1 第一版完成后要做的验证

至少手工验证这些场景：

- “列出当前目录文件”
- “读取某个文件”
- “创建一个测试文件”
- “运行一个简单 shell 命令”
- “连续两轮记忆上下文”

### 5.2 第一版完成标志

你能录出一段稳定 demo：

- 启动 CLI
- 提问
- 调工具
- 返回答案
- 第二轮记住前文

### 5.3 第一版里程碑功能验收表

当你准备宣布“第一版翻版已完成”时，至少要满足下面这张表。

#### 基础能力

- 能启动 CLI
- 能读取配置
- 能调用模型
- 能执行至少 4 个工具
- 能保存并读取 session

#### 使用体验

- 用户能连续对话
- 工具调用过程能看见
- 错误不会直接崩溃退出
- 输出长度不会失控

#### 安全边界

- 文件工具不能越出 workspace
- shell cwd 不允许越界
- 超时命令能被中止
- 非法路径有明确报错

#### 代码质量

- 至少有一组 provider 测试
- 至少有一组工具测试
- 至少有一组 session 测试
- 至少有一条完整 runner/loop 集成测试

### 5.4 第一版建议展示的功能点

如果你要给别人演示，建议只演示这 5 个点：

1. 问一个需要读文件的问题
2. 让 agent 列目录并定位文件
3. 让 agent 读取文件并总结
4. 让 agent 写一个测试文件
5. 第二轮追问，证明它记得上下文

## 阶段 6：加入你的差异化能力

主干跑通后，再开始做“你的 claw”。

### 6.1 双水位记忆系统

建议拆成两层：

#### 长期画像

用 `user_profile.md` 存：

- 用户偏好
- 语言风格
- 常用工具和命令风格
- 特殊要求

#### 近期摘要

用 `recent_summary.json` 或 SQLite 存：

- 最近若干轮摘要
- 自动压缩旧会话
- 保留最新原始消息

#### 上下文拼接策略

建议上下文按这个顺序拼：

1. system prompt
2. 长期画像
3. 近期摘要
4. 最近 K 轮原始消息
5. 当前用户输入

完成标准：

- 连续长对话后，上下文长度仍可控
- agent 能利用长期偏好回答问题

### 6.2 跨平台路径拦截

你可以把这块作为安全边界核心能力。

要求：

- 拒绝 `..`
- 拒绝绝对路径逃逸
- 拒绝离开 workspace/office
- Windows 和 Unix 都能正确处理
- 所有文件工具统一走 resolver

完成标准：

- 构造越权路径时工具能拒绝执行
- Windows 风格和 Unix 风格路径都能正确规范化

### 6.3 透明事件日志

建议先定义 5 类事件：

- `llm_input`
- `tool_call`
- `tool_result`
- `ai_message`
- `system_action`

先写到 JSONL 文件：

- 每次运行一个 log 文件
- 包含时间、事件类型、摘要信息

第一版先不急着做 fancy UI，先保证日志结构清晰。

完成标准：

- 一轮对话结束后能完整回看工具链路

### 6.4 两段式技能调用

建议第一版做简化模式：

- `help`：先展示 `SKILL.md`
- `run`：确认后执行

重点不是做复杂交互，而是做出“先看说明，再执行”的流程。

### 6.5 定时任务系统

放在更后面做。

第一版只做：

- 创建 daily task
- 列出任务
- 删除任务
- 本地持久化

### 6.6 最终完整版本的功能版图

当你把翻版主干和自己的能力都补上后，项目最终可以长成下面这个功能版图。这个版图不是第一天就做，而是你后续持续迭代的目标。

#### 核心运行能力

- CLI 对话入口
- WebUI 对话入口
- 多 session 管理
- 流式输出
- 工具调用循环
- 可配置模型与 provider

#### 记忆能力

- 长期画像
- 近期摘要
- 上下文裁剪
- 手动写入偏好
- 历史搜索

#### 工具能力

- 文件读写
- shell 执行
- 文件搜索
- 配置查看
- 用户画像写入
- 定时任务
- 技能运行

#### 安全能力

- 路径越权拦截
- 工作区限制
- 超时中止
- 输出截断
- 危险命令拒绝

#### 透明性能力

- 结构化运行日志
- 每轮工具轨迹
- 错误事件记录
- 运行回放
- 时间线展示

#### 扩展能力

- skills 目录扫描
- `SKILL.md` 加载
- help/run 两段式技能调用
- MCP 接入
- API 接口

### 6.7 你真正应该实现的“产品感”功能

如果你担心项目只像一个底层框架，不像产品，那你至少要补这些“产品感”功能：

- 有一个清晰入口：CLI 或 WebUI
- 有用户能理解的命令和提示
- 有明确的工作区概念
- 有运行轨迹或日志文件
- 有 session/history
- 有至少一个真正好用的差异化能力

对你这个项目来说，最值得优先做出的“产品感”组合是：

- CLI
- session
- 文件工具
- shell 工具
- 双水位记忆
- 路径拦截
- JSONL 透明日志

## 阶段 7：再补评测

评测在这个项目里是配套，不是主角。

建议最小评测集：

- 文件读取任务
- 文件写入任务
- shell 执行任务
- 多轮记忆任务
- 路径安全拦截任务

指标建议：

- 任务成功率
- 平均工具调用次数
- 平均完成时长
- 安全拦截是否生效

完成标准：

- 你能生成一份简单表格或 Markdown report

## 阶段 8：最后做展示和包装

当主干和差异化能力都基本稳定后，再补这些：

- README 首页
- 架构图
- 功能截图或 GIF
- demo 录屏
- 简历项目描述

简历和 README 的叙事重点应该放在：

- 你实现了一个可运行的透明 Agent
- 你做了双层记忆和安全执行
- 你加了可回放的运行轨迹
- 你用评测闭环证明结果可用

## 推荐的实施节奏

如果你要快速推进，可以按这个节奏做：

### 第 1 周

- 搭新仓库
- 配环境
- 做 provider
- 做 tool registry
- 做 filesystem tools
- 做 shell tool

### 第 2 周

- 做 session
- 做 context builder
- 做 runner
- 做 loop
- 做 CLI
- 跑通完整链路

### 第 3 周

- 做长期画像
- 做近期摘要
- 做路径拦截
- 做透明日志

### 第 4 周

- 做两段式技能调用
- 做最小任务系统
- 补评测
- 补 README 和 demo

## 附录：功能实现清单

你可以把下面这份清单当成开发 checklist，做一个划一个。

### P0 主干

- [ ] CLI 能启动
- [ ] 配置能读取
- [ ] provider 能返回普通文本
- [ ] provider 能处理 tool call
- [ ] tool registry 能注册和执行工具
- [ ] `list_dir` 可用
- [ ] `read_file` 可用
- [ ] `write_file` 可用
- [ ] `exec` 可用
- [ ] session 能保存
- [ ] session 能读取
- [ ] loop 能跑完整轮
- [ ] runner 能处理工具循环
- [ ] 有最小测试

### P1 差异化

- [ ] 长期画像文件
- [ ] 近期摘要文件或 SQLite
- [ ] 上下文裁剪
- [ ] 路径归一化
- [ ] `..` 拦截
- [ ] 绝对路径拦截
- [ ] shell cwd 限制
- [ ] `llm_input` 日志
- [ ] `tool_call` 日志
- [ ] `tool_result` 日志
- [ ] `ai_message` 日志
- [ ] `system_action` 日志

### P2 增强

- [ ] `grep`
- [ ] `find_files`
- [ ] `save_user_profile`
- [ ] `get_model_info`
- [ ] 两段式技能调用
- [ ] 定时任务创建
- [ ] 定时任务列表
- [ ] 定时任务删除
- [ ] eval 脚本
- [ ] demo 录屏
- [ ] README 功能页

## 推荐的 Vibe Coding 工作流

每次只围绕一个模块推进。

推荐流程：

1. 选一个 `nanobot` 目标文件
2. 让 AI 用一句话解释它的职责
3. 让 AI 提炼“你自己的最小可复刻版本”
4. 让 AI 帮你生成模块骨架
5. 你手动删掉复杂度
6. 让 AI 帮你补 1~2 个测试
7. 运行、修正、提交

不要做的事：

- 一次让 AI 生成整个框架
- 一次迁几十个文件
- 不验证就继续叠功能
- 第一版就追求和 `nanobot` 等体量

## 第一批最值得迁移的内容

如果你要从 `nanobot` 里复制参考代码，建议按这个优先级：

### 第一批

- `agent/loop.py`
- `agent/runner.py`
- `agent/context.py`
- `agent/tools/base.py`
- `agent/tools/registry.py`
- `agent/tools/filesystem.py`
- `agent/tools/shell.py`
- `session/manager.py`
- `providers/base.py`
- `providers/openai_compat_provider.py`
- `config/schema.py`
- `config/loader.py`

### 第二批

- `security/workspace_access.py`
- `security/workspace_policy.py`
- `agent/memory.py`

### 第三批

- `skills/`
- `cron/`
- `api/`
- `webui/`

## 最后的判断标准

这个翻版项目做得对不对，不看你抄了多少，而看这 4 件事：

1. 你是否真的跑通了自己的完整链路
2. 你是否能解释每个核心模块的职责
3. 你是否加进了自己的能力，而不是永远停在复刻
4. 你是否能用 demo、日志和评测证明它能工作

如果主干跑通、差异化能力落地、日志和评测能展示出来，这个项目就已经足够作为一份有说服力的简历项目。
