# mini-coding-agent API / Protocol Specification

> 源码基线：`cb4e694`；核验日期：2026-10-04。本文描述当前实现，不是第三方协议的完整规范。
>
> 扫描范围：两个入口、全部 `CodingAgent` 模块、测试、实验脚本、配置示例和仓库 Skill。正文描述运行协议，附录记录实验及历史接口。只新增文档，不改变运行行为。

## Protocol Inventory

| Protocol | Type | Producer | Consumer | Source |
| -------- | ---- | -------- | -------- | ------ |
| Messages 请求与响应 | [External] | 主循环 / Anthropic SDK | 模型服务 / 主循环 | [同步请求][mreq]、[异步请求][areq] |
| 流式文本与最终响应 | [External] | SDK stream | 异步主循环 / 终端 | [异步请求][areq] |
| message / text / tool_use / tool_result | [External] | 用户、SDK、工具循环 | SDK、调度器、压缩器 | [主循环][mloop]、[图片与消息转换][images] |
| 工具定义与 input_schema | [External] | 内置 Schema、MCP 工具池 | Messages API | [工具定义][schemas]、[MCP 工具池][mmanager] |
| ToolCall → Handler → ToolContent | [Internal] | 工具调度器、Handler | 主循环 | [同步调度][dispatcher]、[异步调度][adispatcher] |
| 文件工具的分页、搜索及变更结果 | [Internal] | FileTools | 主 Agent / Subagent | [文件工具][files] |
| Shell 输出、退出码及全文存档 | [Internal] | ShellRunner | Dispatcher / BackgroundManager / Compactor | [Shell][shell]、[压缩器][compact] |
| MCP 配置与连接生命周期 | [Internal] | mcp.json、MCPManager | MCPClient / AsyncMCPClient | [MCP 配置][mconfig]、[同步客户端][mclient]、[异步客户端][amclient] |
| MCP list_tools / call_tool 的 SDK 对象 | [External] | MCP SDK 会话 | MCPClient | [发现与调用][mclient] |
| MCP Tool → Anthropic tools | [Adapter] | MCPClient / MCPManager | 模型、MCP Handler | [Schema 转换][mclient]、[名称与路由][mmanager] |
| MCP result → ToolContent → tool_result | [Adapter] | MCPClient、图片适配器 | Dispatcher / Messages API | [结果格式化][mclient]、[图片适配][images] |
| Task Board 任务对象与工具文本 | [Internal] | TaskStore / TaskBoard | 主 Agent、SubagentManager | [任务板][taskboard] |
| Task Board reminder 文本块 | [Internal] | 主 / 异步主循环 | 下一次模型请求 | [同步提醒](../main.py#L645)、[异步提醒](../main_async.py#L667) |
| Subagent 启动回执 | [Internal] | SubagentTools | 主循环、LLM | [Subagent 工具适配][subtools] |
| SubagentState / response_log / evidence | [Internal] | 执行器、结果整理器 | Manager、主 Agent | [状态][substate]、[执行器][subexec]、[结果][subresults] |
| Subagent 状态与取消回执 | [Internal] | SubagentManager | subagent_status / subagent_cancel | [Manager][submanager] |
| Subagent 最终结果通知 | [Internal] | SubagentTools | messages / 主 Agent | [通知注入][subtools] |
| 后台 Shell 启动回执与 task_notification | [Internal] | Dispatcher / BackgroundManager | messages / 主 Agent | [后台任务][background] |
| ToolContent 与图片内容块 | [Adapter] | read_image、MCP 图片格式化器 | Messages API、预览与压缩 | [图片工具][files]、[内容块][images] |
| Hook 回调与短路返回 | [Internal] | HookRegistry / 回调 | 输入入口、Dispatcher、主循环、Subagent | [Hook][hooks]、[异步 Hook][ahooks] |
| 权限 decision / 拒绝原因 | [Internal] | PermissionManager / 终端 | PreToolUse / Dispatcher | [权限][permissions]、[异步权限][apermissions] |
| 压缩摘要、JSONL transcript、输出标记 | [Internal] | ContextCompactor / ShellRunner | 后续模型请求、read_file | [压缩器][compact]、[Shell][shell] |
| 请求预算估算对象 | [Internal] | context_budget | ContextCompactor / 主循环 | [上下文预算][context] |
| Memory 候选、选择、召回、合并 | [Internal] | MemoryManager / 辅助模型 | MemoryStore / system prompt | [Memory Manager][memorymanager] |
| Memory Markdown、索引及记录对象 | [Internal] | MemoryStore | MemoryManager / prompt builder | [Memory Store][memorystore] |
| Skill manifest / catalog / load | [Internal] | SkillLoader、SKILL.md | prompt builder / load_skill | [Skill][skillloader] |
| AGENT.md 与 system prompt 输入 | [Internal] | 项目指引加载器、各模块 | 主 / 子 Agent 提示词 | [项目指引][instructions]、[提示词][prompts] |
| AgentConfig、环境变量与目录约定 | [Internal] | load_config / 启动环境 | 两个入口、各模块 | [配置][config] |
| 实验 result.json / acceptance / intervals | [Internal] | experiments 脚本 | 人工复核、终端报告 | [实验记录][experiment]、[验收][grading] |
| 历史 TODO 数据与兼容分支 | [Internal] | TODOManager、旧历史 | 直接调用 / 兼容读取 | [TODO][todo]、[结果][subresults]、[压缩器][compact] |

[mreq]: ../main.py#L330
[areq]: ../main_async.py#L320
[mloop]: ../main.py#L439
[aloop]: ../main_async.py#L452
[schemas]: ../CodingAgent/tools/schemas.py#L3
[dispatcher]: ../CodingAgent/tools/dispatcher.py#L15
[adispatcher]: ../CodingAgent/async_support.py#L174
[images]: ../CodingAgent/images.py#L10
[files]: ../CodingAgent/tools/files.py#L20
[shell]: ../CodingAgent/tools/shell.py#L16
[mconfig]: ../CodingAgent/mcp/config.py#L5
[mclient]: ../CodingAgent/mcp/client.py#L33
[mmanager]: ../CodingAgent/mcp/manager.py#L17
[amclient]: ../CodingAgent/mcp/async_client.py#L13
[ammanager]: ../CodingAgent/mcp/async_manager.py#L9
[bridge]: ../CodingAgent/mcp/bridge.py#L6
[taskboard]: ../CodingAgent/taskboard.py#L7
[substate]: ../CodingAgent/subagent/state.py#L6
[submanager]: ../CodingAgent/subagent/manager.py#L45
[subexec]: ../CodingAgent/subagent/executor.py#L32
[subresults]: ../CodingAgent/subagent/results.py#L39
[subtools]: ../CodingAgent/tools/adapters.py#L11
[background]: ../CodingAgent/background.py#L17
[hooks]: ../CodingAgent/hooks.py#L6
[ahooks]: ../CodingAgent/async_support.py#L67
[permissions]: ../CodingAgent/permissions.py#L57
[apermissions]: ../CodingAgent/async_support.py#L81
[compact]: ../CodingAgent/compact.py#L25
[context]: ../CodingAgent/context_budget.py#L6
[memorymanager]: ../CodingAgent/memory/manager.py#L162
[memorystore]: ../CodingAgent/memory/store.py#L51
[skillloader]: ../CodingAgent/skill_loader.py#L7
[instructions]: ../CodingAgent/project_instructions.py#L4
[prompts]: ../CodingAgent/prompts.py#L3
[config]: ../CodingAgent/config.py#L12
[todo]: ../CodingAgent/todo.py#L7
[experiment]: ../experiments/run.py#L73
[grading]: ../experiments/grade_build.py#L15

## 1. 阅读约定与边界

- **[External]**：项目调用或消费的第三方 SDK 接口与结构；不补充未使用的 HTTP、SSE、JSON-RPC、握手或协议版本字段。
- **[Adapter]**：跨协议转换，例如 MCP 内容转 Anthropic 内容块。
- **[Internal]**：Python 模块、模型提示词、消息通知或持久化文件之间的数据约定。
- **Schema 声明**、**运行时校验**、**提示词要求**分别陈述；声明一个 JSON Schema 不代表 Dispatcher 执行了通用 Schema 校验。
- JSON 示例是源码约束下的**合成结构示例**，不是实际模型或外部服务的调用日志。SDK 对象示例展示项目访问的属性投影，不声称是完整网络响应。
- `<MODEL>`、`<WORKDIR>`、`<base64-image-data>` 是明确的占位值。图片占位值不能直接发送给模型或通过 Base64 校验。
- 所有长度、轮数和预算都是当前项目的本地配置，不代表模型服务的官方限制。

### 1.1 必须区分的返回形态

| 形态 | 例子 | 接收方处理 |
| --- | --- | --- |
| 普通字符串 | `"Edited README.md"` | 直接放入 tool_result.content |
| 包含 JSON 的字符串 | task 启动回执、subagent_status | 主循环或模型按需解析；不是 tool_result 的新增顶层字段 |
| 内容块列表 | read_image 返回 text + image | 作为 tool_result.content 列表发送 |
| 内部对象 | Task、SubagentState、SDK content block | 由对应模块转换或原样交给 SDK |
| XML 风格文本 | task_notification、subagent_result | 包在普通 text block 内；没有新增 Anthropic block type |

主 / 异步主循环的类型注解写成 `-> str`，当前正常结束执行的是裸 `return`，实际返回 `None`。最终回答通过 `messages` 中的 assistant 内容以及异步 stream 输出交付。Source code：[主循环][mloop]、[异步主循环][aloop]。

入口接受 query 字符串并构造首条 user message；主循环以 messages 与 active_request 作为内部输入。active_request 单独保留当前用户请求，供压缩摘要使用。相关 Memory 在进入本次循环时召回，再作为每轮 system prompt 的文本输入。

## 2. [External] Anthropic Messages 与内容块

### 2.1 请求接口

| 项目 | 约定 |
| --- | --- |
| Purpose | 将当前上下文与可用工具交给模型，取得下一步文本或工具请求 |
| Producer | 主循环、SubagentExecutor、ContextCompactor、MemoryManager |
| Consumer | anthropic.Anthropic / anthropic.AsyncAnthropic 的 Messages 接口 |
| Source code | [request_usable_response][mreq]、[async_request_usable_response][areq]、[SubagentExecutor][subexec]、[ContextCompactor][compact]、[MemoryManager][memorymanager] |

客户端构造使用 `api_key=os.getenv("ANTHROPIC_API_KEY")`、`base_url=os.getenv("ANTHROPIC_BASE_URL")`。模型取自 `ANTHROPIC_MODEL`；当前代码没有实现注释中提到的默认模型回退。

| 调用场景 | 接口 | 实际传入字段 / options |
| --- | --- | --- |
| 同步主 Agent | messages.create | model、system、messages、tools、max_tokens |
| 异步主 Agent | messages.stream | model、system、messages、tools、max_tokens |
| Subagent 工作 | with_options(...).messages.create | options：timeout=剩余秒数、max_retries=0；请求：model、system、messages、tools、max_tokens=16384 |
| Subagent 中断收尾 | with_options(...).messages.create | options：timeout=20 默认、max_retries=0；请求：model、system、messages、max_tokens=4096；不传 tools |
| Compact 摘要 | messages.create | model、system、messages、max_tokens=25000、timeout=1200；不传 tools |
| Memory 选择 | messages.create | model、messages、max_tokens=1000、temperature=0.0 |
| Memory 提取 | messages.create | model、messages、max_tokens=1000 |
| Memory 合并 | messages.create | model、messages、max_tokens=3000 |

普通主请求的参数形态：

~~~python
response = client.messages.create(
    model=MODEL,
    system=system_prompt,
    messages=messages,
    tools=tools,
    max_tokens=16384,
)
~~~

`system` 是独立的字符串参数，当前历史中不构造 `role="system"` 的 message。每轮重新构建 system prompt 和工具池。

### 2.2 message 与 content block

**Producer / Consumer**：CLI 输入、SDK 响应、工具与通知注入器 → messages / SDK / Compactor。**Source code**：[主循环][mloop]、[images.block_dict / text_history][images]、[SubagentExecutor.execute_subagent][subexec]。

| 结构 | 字段 | 当前用途 |
| --- | --- | --- |
| Message | role | 当前构造值为 user 或 assistant |
| Message | content | 字符串或内容块列表 |
| Text block | type="text"、text | 文本、提醒、后台通知 |
| Tool-use block | type="tool_use"、id、name、input | SDK 对象上的工具调用约定 |
| Tool-result block | type="tool_result"、tool_use_id、content | 项目构造的调用回填 |
| Image block | type="image"、source | 第 8 节定义的 Base64 图片 |
| source | type="base64"、media_type、data | 当前实际构造的图片来源 |

~~~json
[
  {"role": "user", "content": "读取 CodingAgent/messages.py 的文本提取函数。"},
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_read_1",
        "name": "read_file",
        "input": {"path": "CodingAgent/messages.py", "offset": 4, "limit": 3}
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_read_1",
        "content": "File: CodingAgent/messages.py\nLines 4-6 of 11\n4: def extract_text(content) -> str:\n5:     if not isinstance(content, list):\n6:         return str(content)\nNext offset: 7"
      }
    ]
  }
]
~~~

主循环保存的 `response.content` 可以仍是 SDK block 对象；Subagent 会调用 `block.model_dump(mode="json")` 保存为字典。序列化辅助函数也通过 `model_dump(mode="json")` 处理 SDK 对象。两种内存表示不应混同于网络线上的完整 JSON。

生命周期：用户 message → 模型响应 → assistant message → user 中的工具结果或通知 → 再次请求模型。

### 2.3 响应消费、停止原因与重试

| 响应属性 | Consumer | 处理规则 |
| --- | --- | --- |
| content | 主 / 子循环、摘要、Memory | 读取 block.type；text 使用 block.text；工具使用 id/name/input |
| stop_reason | 主循环、Subagent、Compactor | 各调用场景有不同校验规则 |
| usage | 主循环 | 直接打印 SDK usage 对象，没有统一转换为 usage 字典 |
| usage.output_tokens | Subagent 响应记录器 | 写入 response_log；缺失时为 null |

主 Agent：[request_usable_response][mreq] / [async_request_usable_response][areq]。

| 条件 | 主 Agent 下一步 |
| --- | --- |
| end_turn，存在非空文本且没有 tool_use | 接收响应；检查后台工作与 Stop Hook 后结束或继续 |
| tool_use，至少存在一个 tool_use block | 接收响应，随后执行工具 |
| max_tokens | 丢弃该响应，不写入主历史、不执行其中的工具；最多重试一次，max_tokens 从 16384 增加到 32768 |
| tool_use，但没有 tool_use block | 最多重试一次；不因此增加输出额度 |
| 空内容、纯空白文本或仅未知/思考块 | 最多重试一次 |
| end_turn 同时包含 tool_use | 立即抛 IncompleteResponseError |
| 其他 stop_reason | 立即抛 IncompleteResponseError |

`usage` 除上述访问外保持 SDK 对象；本文不从测试替身的字段扩展出生产协议。Subagent 与 Compact 的差异分别见第 6、10 节。

### 2.4 异步流式接口

~~~text
llm.messages.stream(...)
→ async for text in stream.text_stream：立即打印字符串片段
→ await stream.get_final_message()：取得完整 SDK Message
→ 校验 stop_reason/content
→ 保存 assistant message 并执行工具
~~~

Source code：[async_request_usable_response][areq]。项目不直接消费 SSE event、delta、事件序号或 JSON-RPC 消息。流式片段已经打印，也可能在最终校验时判定该响应被截断；打印过的片段不因此进入有效主历史。

异步入口的主 Agent 使用 AsyncAnthropic；Subagent、Memory 和 Compact 仍使用同步 Anthropic 客户端。异步调用方通过线程适配等待 Memory/Compact 完成。

## 3. [External / Internal] Tool Calling 与内置工具

### 3.1 工具定义与 input_schema

| 项目 | 约定 |
| --- | --- |
| Purpose | 向模型声明工具名称、用途和输入形态 |
| Producer | build_tool_schemas、MCPManager.assemble_tool_pool |
| Consumer | Messages API；模型输出的 name 再用于 Handler 查找 |
| Source code | [工具定义][schemas]、[MCP 工具池][mmanager]、[主循环][mloop] |
| Schema | Tool 定义包含 name:string、description:string、input_schema:dict |
| 生命周期 | 每轮组装 tools 与同名 handlers → 模型生成 tool_use → Dispatcher 查找 Handler |

`read_file` 的实际 Schema 示例：

~~~json
{
  "name": "read_file",
  "description": "Read a numbered page of a UTF-8 file. offset is the 1-based starting line (default 1). limit defaults to 200 and is capped at 200 lines. Use the returned next offset for the next page.",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 200},
      "offset": {"type": "integer", "minimum": 1, "default": 1}
    },
    "required": ["path"]
  }
}
~~~

当前工具定义实际使用的 JSON Schema 关键字：`type`、`properties`、`required`、`default`、`minimum`、`maximum`、`minLength`、`pattern`、`items`、`minItems`、`additionalProperties`、`description`；动态 connect_mcp 另使用 `enum`。只在源码声明的位置生效为工具描述，不补齐统一的 strict 配置。

### 3.2 ToolCall → ToolContent → tool_result

**Producer**：SDK tool-use block、Handler。**Consumer**：ToolDispatcher / AsyncToolDispatcher、主循环、下一轮 Messages API。**Source code**：[同步调度][dispatcher]、[异步调度][adispatcher]、[主循环][mloop]、[normalize_tool_result][images]。

| 读取的属性 | 下一步 |
| --- | --- |
| tool_call.id | 原样成为 tool_result.tool_use_id |
| tool_call.name | PreToolUse 日志 / 权限检查；handlers.get(name) |
| tool_call.input | 背景判定；以 Handler(**input) 展开为关键字参数 |
| Handler 返回值 | PostToolUse 观察，然后 normalize_tool_result |
| 归一化的 ToolContent | 填入 tool_result.content |

~~~mermaid
flowchart LR
    U[User message] --> M[Messages API]
    M --> A[assistant: tool_use]
    A --> P[PreToolUse / permission]
    P --> D[ToolDispatcher]
    D --> H[Tool Handler]
    H --> N[normalize_tool_result]
    N --> R[user: tool_result]
    R --> M
    M --> F[assistant: final text]
~~~

请求块：

~~~json
{
  "type": "tool_use",
  "id": "call_read_1",
  "name": "read_file",
  "input": {"path": "CodingAgent/messages.py", "offset": 4, "limit": 3}
}
~~~

调用接口与回填形态：

~~~python
output = handlers[tool_call.name](**tool_call.input)
# read_file(path="CodingAgent/messages.py", offset=4, limit=3)

tool_result = {
    "type": "tool_result",
    "tool_use_id": tool_call.id,
    "content": normalized_output,
}
messages.append({"role": "user", "content": [tool_result]})
~~~

`ToolContent = str | list[dict]`。归一化规则：

| Handler 输出 | normalize_tool_result 的结果 |
| --- | --- |
| str | 原样返回 |
| 非空 list，所有元素均为 dict，且 type 为 text 或 image | 原样保留列表 |
| 其他值，包括 dict、空 list、非内容块列表 | str(value)，不是自动 json.dumps |

当前工具结果外层仅构造 `type / tool_use_id / content`；代码没有把 MCP 的 `is_error` 转成 Anthropic tool_result 的错误标志。失败、拒绝或未知工具通常仍通过 content 字符串告知模型。

同一响应中的工具按列表顺序执行，全部结果放进一条 user message。异步 Dispatcher 会等待同步工作或 awaitable 返回值；MCP Handler 虽然是 lambda，也可能返回需要 await 的协程。它不把整个工具批次自动并发执行。

### 3.3 活跃工具输入目录

当前基础工具集合共 **18 个**，每轮另加 `connect_mcp` 和已连接服务发现的 MCP 工具。**Producer**：[build_tool_schemas](../CodingAgent/tools/schemas.py#L346)。**Consumer**：模型与对应 Handler；Handler 绑定见 [main.py](../main.py#L290)、[main_async.py](../main_async.py#L280)。

表中“默认”来自实际 Handler 或显式 Schema default，具体区别见备注。

| Tool | 输入字段与含义 | 必填 | 默认 / Schema 约束 |
| --- | --- | --- | --- |
| bash | command:string，命令文本；run_in_background:boolean，后台标志 | command | Handler 默认 run_in_background=false；Schema 未写 default |
| read_file | path:string，文件路径；offset:integer，起始行；limit:integer，页大小 | path | offset=1、limit=200；Schema offset≥1、1≤limit≤200 |
| write_file | path:string，目标路径；content:string，新全文 | path、content | Schema 无额外长度约束 |
| edit_file | path:string；old_content:string，查找文本；new_content:string，替换文本 | 全部 | 替换首个匹配 |
| glob | pattern:string，路径匹配模式 | pattern | Schema 无额外约束 |
| grep | pattern:string，字面单行文本；path:string，范围；glob:string/null，文件过滤；ignore_case:boolean；limit:integer，返回行数 | pattern | path="."、glob=null、ignore_case=false、limit=100；pattern minLength=1，1≤limit≤200 |
| load_skill | name:string，Skill catalog 中的名称 | name | 按名称查找 |
| read_image | path:string，本地图片路径 | path | 返回图片内容块 |
| task | task_id:string，已认领任务；prompt:string，独立任务说明 | 全部 | 启动只读 Subagent；不是创建看板任务 |
| create_task | subject:string，主题；description:string，描述 | subject | Handler description="" |
| update_task | task_id:string；addBlockedBy:array[string]，新增依赖 ID | 全部 | Schema 两处 ID pattern 为 ^task_[0-9a-f]{8}$，数组 minItems=1 |
| list_tasks | 空对象 | 无 | Schema properties={} |
| get_task | task_id:string | task_id | 返回任务 JSON 字符串 |
| claim_task | task_id:string | task_id | 工具包装固定 owner="agent" |
| complete_task | task_id:string | task_id | 工具包装固定 owner="agent" |
| compact | 空对象 | 无 | Handler 接受 **kwargs；由主循环在批次结束后压缩 |
| subagent_status | run_id:string | run_id | 不领取完成通知 |
| subagent_cancel | run_id:string | run_id | 请求取消，不直接中止线程 |
| connect_mcp | name:string，配置中的服务名 | name | 动态 enum 为配置服务列表；为空时使用 "(no servers configured)" |

`additionalProperties=false` 只显式出现在 grep、read_image、task、create_task、update_task、subagent_status、subagent_cancel。其他工具未声明这个关键字。

**运行时边界**：

- Dispatcher 没有统一 JSON Schema 校验器，通常通过 `Handler(**tool_call.input)` 调用。缺参数、多参数和类型错误的表现取决于 Handler、Hook 与 Python 调用本身。
- read_file 使用 `type(value) is int` 校验 offset/limit；limit 超过 200 时实际截为 200。直接调用 Handler 时允许 limit=None 并回退到 200，工具 Schema 没有声明 null。
- grep 校验非空单行 pattern、非空 path、glob 类型、真正的 bool 和 1..200 的整数 limit；搜索必须在工作区内。update_task 的运行校验另外检查任务存在、依赖关系和状态，不以 Schema 正则作为全部校验。
- 文件工具会解析路径并拒绝越出 WORKDIR。即使权限 Hook 同意越界访问，FileTools 的路径约束仍存在。

### 3.4 内置工具输出与具体例子

文件工具、Skill 和 Shell 的通常输出是**文本字符串**。下列示例保持当前返回风格；任务及 Subagent 的完整协议分别见第 5、6 节。

| Tool / Producer | 输入例子 | 输出形态与例子 | Source code |
| --- | --- | --- | --- |
| read_file / FileTools | {"path":"CodingAgent/messages.py","offset":4,"limit":3} | 分页文本，包含 File、Lines、带行号正文、Next offset 或 [EOF] | [文件工具][files] |
| write_file / FileTools | {"path":"notes.txt","content":"hello"} | "Wrote 5 bytes to notes.txt" | [文件工具][files] |
| edit_file / FileTools | {"path":"notes.txt","old_content":"hello","new_content":"ready"} | "Edited notes.txt"；找不到文本："Error: hello not found in notes.txt" | [文件工具][files] |
| glob / FileTools | {"pattern":"CodingAgent/mcp/*.py"} | 排序、去重的匹配路径，以换行分隔；空："(no matches)"；最多展示 200 个路径 | [文件工具][files] |
| grep / FileTools | {"pattern":"tool_use_id","path":"CodingAgent/subagent/executor.py","glob":"*.py","limit":100} | 带范围、命中路径/行号、返回数量和 Truncated 文本的搜索结果 | [文件工具][files] |
| bash / ShellRunner | {"command":"python -c \"print('ready')\""} | "ready"；非零退出添加 "Error: Command exited with code N" | [Shell][shell] |
| load_skill / SkillLoader | {"name":"count-lines"} | 完整 SKILL.md 原文字符串，包括 frontmatter；不存在则返回可用名称 | [Skill][skillloader] |
| read_image / FileTools | {"path":"screenshots/result.png"} | text + image 列表；失败为 "Error: ..." 字符串 | [文件工具][files]、[图片][images] |
| create_task / TaskBoard | {"subject":"检查消息协议"} | "Created task task_12345678: (检查消息协议)" | [任务板][taskboard] |
| update_task / TaskBoard | {"task_id":"task_12345678","addBlockedBy":["task_87654321"]} | "Updated task task_12345678: (检查消息协议) -> task_87654321" | [任务板][taskboard] |
| list_tasks / TaskBoard | {} | "[>] task_12345678: 检查消息协议 [in_progress] [agent]"；空："No tasks. Use create_task to add some." | [任务板][taskboard] |
| get_task / TaskBoard | {"task_id":"task_12345678"} | JSON 编码字符串，解析后为第 5 节 Task 对象 | [任务板][taskboard] |
| claim_task / TaskBoard | {"task_id":"task_12345678"} | "Claimed task_12345678 (检查消息协议)" 或条件未满足的文本 | [任务板][taskboard] |
| complete_task / TaskBoard | {"task_id":"task_12345678"} | "Completed task_12345678 (检查消息协议)"，可追加 "Unblocked: ..." | [任务板][taskboard] |
| compact / run_compact | {} | "本轮工具调用存在压缩请求，agent将会首先执行其他工具调用请求，最后压缩" | [工具适配](../CodingAgent/tools/adapters.py#L120) |
| task / SubagentTools | {"task_id":"task_12345678","prompt":"阅读消息协议并交接结论。"} | JSON 字符串：started 或 not_started 回执 | [Subagent 工具][subtools] |
| subagent_status / SubagentTools | {"run_id":"run_00000000000000000000000000000001"} | JSON 字符串：运行快照、最终结果或 not_found | [Subagent 工具][subtools] |
| subagent_cancel / SubagentTools | 同上 | JSON 字符串：cancelling、已结束状态或 not_found | [Subagent 工具][subtools] |
| connect_mcp / MCPManager | {"name":"fetch"} | "Connected to MCP server 'fetch'. Discovered N tools: ..."；失败或配置不存在为文本 | [MCP Manager][mmanager] |

任务 ID、文件修改场景及 Shell 输出是合成示例；它们没有在生成本文时执行。

**read_file 分页输出**：

~~~text
File: CodingAgent/messages.py
Lines 4-6 of 11
4: def extract_text(content) -> str:
5:     if not isinstance(content, list):
6:         return str(content)
Next offset: 7
~~~

当 offset 超过文件行数（包括空文件）：

~~~text
File: empty.txt
[EOF] Total lines: 0; requested offset: 1
~~~

**grep 文本协议**，在上述单文件范围内搜索，示例对应一个命中：

~~~text
Search: tool_use_id
Scope: CodingAgent/subagent/executor.py
Files: *.py

CodingAgent/subagent/executor.py:212:                        "tool_use_id": tool_call.id,

Returned: 1 matching lines
Truncated: false
~~~

`Truncated: true/false` 是文本行，不是 JSON bool。超限、超过 15 秒或 rg 异常会返回相应提示；结果可能不完整。默认使用 ripgrep 过滤规则，搜索是 fixed-strings；glob 过滤可以覆盖忽略规则。找不到 rg 时返回错误字符串。

其他输出细节：

- write_file 的 "bytes" 数字来自 `len(content)`，实际计数为 Python 字符数，不是 UTF-8 编码字节数。
- glob 返回匹配路径，代码没有统一排除匹配到的目录；超过 200 个匹配会追加省略提示。
- 工具名 bash 实际通过 `subprocess.Popen(..., shell=True, cwd=WORKDIR)` 执行，不固定指定一个 Bash 可执行文件。
- Shell 内部接口 `run_bash_process(command, timeout=120.0)` 返回 `tuple[output:str, exit_code:int|None]`；stdout + stderr 合并后 strip，空输出为 "(no output)"。
- Shell 超时输出 "Error: Command timed out."，exit_code=None。后台状态将其判为 failed；前台内容由错误文本表达。
- 前台 bash 超过 50000 字符才触发 Shell 全文存档，存档后的首尾预览最多 500 字符。后台使用 500 字符存档阈值。标记格式见第 10 节。

### 3.5 工具执行错误与计数

| 产生位置 | 返回 / 行为 |
| --- | --- |
| PreToolUse 拒绝 | 阻断原因字符串；Handler 不执行，也不触发 PostToolUse |
| Handler 不存在 | "Unknown: <tool_name>" |
| 主 Dispatcher 捕获 Handler / 后台启动异常 | "Error: <exception text>" |
| 文件工具处理异常 | 通常 "Error: ..." |
| Subagent 禁止的工具 | "Error: 子 Agent 只读模式禁止调用工具：<name>" |
| MCP 错误 | "MCP error: ..." 或包含错误 text 的图片列表 |

PreToolUse / PostToolUse 回调异常不统一包进 Handler 的异常分支，可能上抛。不能假定所有故障都会变成 tool_result。

主循环在调用 Dispatcher 前增加 tool_call_count，因此拒绝、未知工具等调用尝试也计入。Subagent 有独立计数；主循环只登记当前请求中 started 回执包含的非空字符串 run_id，在最终通知到达时按 run_id **赋值**更新子计数，最后汇总。

## 4. [External / Adapter / Internal] MCP

### 4.1 配置与连接

| 项目 | 约定 |
| --- | --- |
| Purpose | 从本地配置连接服务，保持会话并按需发现工具 |
| Producer | mcp.json → load_mcp_config → MCPServerConfig |
| Consumer | MCPManager / AsyncMCPManager、MCPClient / AsyncMCPClient |
| Source code | [配置][mconfig]、[客户端][mclient]、[异步客户端][amclient]、[异步 Manager][ammanager] |

配置是项目内部约定：

~~~json
{
  "mcpServers": {
    "local": {"command": "python", "args": ["server.py"], "env": {"MODE": "readonly"}},
    "remote": {"url": "https://mcp.example.test/mcp", "headers": {"X-Test": "example"}}
  }
}
~~~

这是合成配置；server.py 与示例地址不代表仓库提供可启动的服务。实际配置/凭据不复制到本文。

| 字段 / 属性 | 类型与默认 | 实际处理 |
| --- | --- | --- |
| mcpServers | object，缺失时 {} | name → spec 映射 |
| name | 非空字符串 | 配置键转为 MCPServerConfig.name |
| command | string/null | 与 url 恰有一个非空值；stdio 启动命令 |
| args | array[string]，配置缺省 [] | command 配置复制列表；HTTP 配置的对象属性为 None |
| env | object[string,string]/null | stdio 环境参数；空对象被归一化为 None |
| url | string/null | HTTP 服务目标 |
| headers | object[string,string]/null | 必须配合非空 url；空对象归一化为 None |
| transport_kind | 计算属性 "stdio" 或 "http" | 不是配置中的 transport 字段 |

加载器检查对象层级、上述类型和 command/url 的互斥关系。两个入口捕获配置缺失或无效并使用空配置继续启动。

`connect_mcp({"name":"fetch"})` 调用 Manager，返回文本字符串，例如：

~~~text
Connected to MCP server 'fetch'. Discovered 1 tools: fetch
~~~

其他实际形式包括 `MCP server 'fetch' already connected. Tools: ...`、`MCP server 'fetch' not found in config. Available: ...` 和 `Error: ...`。

| 生命周期 | 同步实现 | 异步实现 |
| --- | --- | --- |
| 打开会话 | AsyncBridge 的常驻事件循环持有后台会话 | _owner asyncio.Task 持有会话 |
| ready | threading.Event 通知 connect 等待方 | asyncio.Event 在发现成功、失败或清理时唤醒等待方 |
| 已连接判定 | _session 非 None | _session 非 None，且 _owner 存在、未结束 |
| 发布到 Manager.clients | connect 返回成功之后 | connect 返回成功之后 |
| 连接超时 | connect 默认 60 秒 | connect 默认 60 秒；失败/取消路径清理 owner |
| 工具调用超时 | bridge.run 默认 120 秒 | wait_for 默认 120 秒 |
| 关闭 | _closed 通知，disconnect 默认等待 10 秒 | _stop 通知，disconnect 默认等待 10 秒并等待会话清理 |
| 工具池刷新 | 每轮只收录 is_connected 的客户端 | 复用相同组池逻辑；异步连接另用锁避免重复连接 |

Source code：[AsyncBridge][bridge]、[MCPClient][mclient]、[AsyncMCPClient][amclient]。HTTP 配置有 headers 时显式使用 streamable_http_client 和 httpx2.AsyncClient；其他目标传给 SDK Client。项目没有自行定义 MCP 初始化请求或 JSON-RPC envelope。

### 4.2 [External] list_tools 与 SDK Tool 的属性协议

**Purpose**：逐页读取工具定义。**Producer**：MCP SDK Client。**Consumer**：MCPClient._list_all_tools。**Source code**：[发现函数](../CodingAgent/mcp/client.py#L75)。

| 对象 / 调用 | 当前读取的字段 |
| --- | --- |
| session.list_tools(cursor=cursor) | 初始 cursor=None；下一页取 page.next_cursor |
| page | tools：工具对象列表；next_cursor：无下一页时为假值 |
| Tool | name、description、input_schema |
| 项目保存的工具字典 | name、description、input_schema |

SDK 对象属性投影：

~~~json
{
  "tools": [
    {
      "name": "read_file",
      "description": "Read a numbered page of a UTF-8 file.",
      "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
      }
    }
  ],
  "next_cursor": null
}
~~~

本节及第 14.3 节使用**合成 MCP 发现样例**：服务名 local、工具名 read_file，path 字段借用项目的文件读取场景。它不代表当前本地配置中存在 local 服务，也不代表 fetch 等远端工具的真实 Schema。那些工具的完整输入定义由运行时发现提供，仓库没有固定声明。

### 4.3 [Adapter] MCP Tool → Anthropic tools

~~~text
session.list_tools(cursor=...)
→ page.tools 中的 SDK Tool
→ MCPClient 保存 {name, description, input_schema}
→ MCPManager.assemble_tool_pool
→ {name: "mcp__<server>__<tool>", description, input_schema}
→ Messages API 的 tools
~~~

**Producer / Consumer**：MCPClient、MCPManager → 模型 / handlers。**Source code**：[_schema_to_dict](../CodingAgent/mcp/client.py#L22)、[assemble_tool_pool](../CodingAgent/mcp/manager.py#L97)。

| 转换位置 | 规则 |
| --- | --- |
| tool.description | None 或其他假值在发现时变成 "" |
| input_schema=None | 变成 {"type":"object","properties":{}} |
| input_schema 是 dict | 直接使用该字典 |
| input_schema 有 model_dump | model_dump(by_alias=False, exclude_none=True)；非 dict 结果回退为 {"type":"object"} |
| 其他 input_schema 值 | 回退为空 object Schema |
| 服务 / 工具名 | 非 ASCII 字母、数字、下划线、连字符替换为 "_"；结果不能为空 |
| 模型可见名称 | mcp__ + 规范化服务名 + __ + 规范化工具名；超过 64 字符抛 ValueError，不截断名称 |
| 名称冲突 | 检查与已放入工具池的名称冲突，包括内置工具和其他 MCP 工具；抛 ValueError |
| 组池时 Schema | 假值回退为空 object；非 dict 或 type 非 object 被拒绝；缺 type 按 object 检查，不额外补入 type |
| Handler | 捕获客户端及原始工具名；客户端 call_tool 使用原始 name |
| 权限策略 | key=(规范化服务名, 原始工具名)；默认 "confirm" |

上一步发现样例保存后的字典：

~~~json
{
  "name": "read_file",
  "description": "Read a numbered page of a UTF-8 file.",
  "input_schema": {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"]
  }
}
~~~

服务 local 加入池后：

~~~json
{
  "name": "mcp__local__read_file",
  "description": "Read a numbered page of a UTF-8 file.",
  "input_schema": {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"]
  }
}
~~~

调用链：

~~~python
# 模型输出 name="mcp__local__read_file"
handlers["mcp__local__read_file"](path="README.md")
# 路由到已绑定的客户端：
client.call_tool("read_file", {"path": "README.md"})
~~~

每轮生成同一组 `tools, handlers`。connect_mcp 成功后，新工具在**下一轮组池**进入模型请求；当前已取出的 Handler 字典不会就地增加工具。工具 Schema 的内部属性由远端提供并透传，不据此推测具体服务的业务参数。

超长名称、名称冲突或非法顶层 Schema 的 ValueError 发生在 assemble_tool_pool，位于本轮模型请求与 Dispatcher 调用之前；它们不会由 Dispatcher 自动转换为某个 tool_result。

### 4.4 [External / Adapter] call_tool 与结果格式化

| 项目 | 约定 |
| --- | --- |
| Purpose | 调用原始 MCP 工具，把返回内容适配为 ToolContent |
| Producer | session.call_tool(tool_name, args or {}) |
| Consumer | MCPClient._format_mcp_tool_result → Dispatcher → 主循环 |
| Source code | [call_tool](../CodingAgent/mcp/client.py#L156)、[格式化器](../CodingAgent/mcp/client.py#L173)、[异步调用](../CodingAgent/mcp/async_client.py#L135) |

格式化器消费的是 **SDK 属性对象**，不是通用的原始网络 JSON 解析器。

| 对象属性 | 消费方式 |
| --- | --- |
| result.is_error | getattr(..., False)，再转 bool |
| result.content | getattr(..., None) or [] |
| block.type | 区分 text / image；其他类型当前跳过 |
| block.text | 非空值转字符串 |
| block.data | 图片 Base64 字符串 |
| block.mime_type / block.mimeType | 优先 mime_type，假值时兼容 mimeType |
| result.structured_content | 无可用文本且无图片时，非 None 值 json.dumps |

转换优先级：

1. 有图片：保存原图并转换成 text 元数据 + image block，保留内容顺序；不再读取 structured_content。
2. 没图片但有文本：文本以换行合并并 strip；忽略 structured_content。
3. 没有效文本：尝试序列化 structured_content。
4. 仍无正文：使用 "(empty MCP tool result)"。
5. is_error 为真：文本结果前加 "MCP error: "；图片结果前插错误 text block。

**文本成功**，SDK 属性投影：

~~~json
{
  "is_error": false,
  "content": [
    {"type": "text", "text": "# mini-coding-agent"},
    {"type": "text", "text": "项目提供工具调用与只读子 Agent。"}
  ]
}
~~~

格式化后的 ToolContent 是一个字符串：

~~~json
"# mini-coding-agent\n项目提供工具调用与只读子 Agent。"
~~~

主循环回填：

~~~json
{
  "type": "tool_result",
  "tool_use_id": "call_mcp_read_1",
  "content": "# mini-coding-agent\n项目提供工具调用与只读子 Agent。"
}
~~~

**结构化 fallback**，沿用仓库测试的结果场景：

~~~json
{"is_error": false, "content": [], "structured_content": {"count": 2}}
~~~

~~~json
"{\"count\": 2}"
~~~

第二段是 JSON 编码的**字符串值**，不是 dict 返回值。

**工具报告错误**：

~~~json
{"is_error": true, "content": [{"type": "text", "text": "bad input"}]}
~~~

~~~json
{
  "type": "tool_result",
  "tool_use_id": "call_mcp_error_1",
  "content": "MCP error: bad input"
}
~~~

**图片**：

转换前的 SDK 属性投影（数据为不可解码的占位符）：

~~~json
{
  "is_error": false,
  "content": [
    {"type":"image","data":"<base64-image-data>","mime_type":"image/png"}
  ]
}
~~~

~~~text
MCP image 的 data / mime_type（或 mimeType）
→ 校验并写入 .task_outputs/tool-results/images/<uuid>.<ext>
→ make_image_content：路径 text + Base64 image
→ ToolContent 列表
→ Anthropic tool_result.content 列表
~~~

转换后使用第 8.1 节的 text + image 结构：text 使用刚保存图片的绝对路径，例如 <WORKDIR>/.task_outputs/tool-results/images/<uuid>.png；source.data 保留 MCP 返回的 Base64，source.media_type 来自已校验的 MIME。图片元数据由项目补充，不是远端 text block。

具体图片结构见第 8 节。MCP 图片结果 is_error=true 时插入：

~~~json
{"type": "text", "text": "MCP error: tool returned an error."}
~~~

| 调用异常 | 返回文本 |
| --- | --- |
| 会话未连接 | MCP error: server 'local' is not connected |
| 调用超时 | MCP error: calling 'read_file' on 'local' timed out after 120.0s |
| SDK 调用或格式化异常 | MCP error: <异常类型>: <异常文本> |

结果 formatter 本身的图片校验异常会上抛；call_tool 捕获后转换为上述 MCP error 文本。异步调用复用相同 formatter，在线程适配中执行。

### 4.5 权限与后续流向

外部 `mcp__...` 工具默认确认；只有 policy=="allow" 直接通过，其他 policy 值都会进入确认流程。拒绝返回 "Permission denied by user"，模型收到对应 tool_result。连接工具本身名为 connect_mcp，不进入 `name.startswith("mcp__")` 这条检查。

完成一次调用后，模型收到适配后的结果；它只能依据后续请求中的实际 tools 选择下一次工具。Subagent 的白名单不包含 MCP 工具或 connect_mcp。

## 5. [Internal] Task Board

| 项目 | 约定 |
| --- | --- |
| Purpose | 持久化工作任务、领取状态与依赖关系 |
| Producer | TaskStore / TaskBoard |
| Consumer | 主 Agent、SubagentManager.start、依赖检查 |
| Source code | [Task / TaskStore / TaskBoard][taskboard]、[任务工具 Schema](../CodingAgent/tools/schemas.py#L180) |
| 生命周期 | create_task → 可选 update_task → claim_task → 开展或委派工作 → 主 Agent 验收 → complete_task |

### 5.1 Task 对象与磁盘格式

`tasks/<id>.json` 保存 `asdict(Task)`；当前目录是 `tasks`，不是旧注释中的 `.tasks`。

| 字段 | 类型 | 产生与含义 |
| --- | --- | --- |
| id | string | "task_" + 8 位小写十六进制；运行时随机生成 |
| subject | string | 创建时 strip，不能为空 |
| description | string | 工具默认 "" |
| status | string | pending / in_progress / completed |
| owner | string/null | 创建为 null；工具认领后固定 "agent" |
| blockedBy | array[string] | 创建为 []；保存依赖任务 ID |

~~~json
{
  "id": "task_12345678",
  "subject": "检查消息协议",
  "description": "阅读消息结构并给出结论。",
  "status": "in_progress",
  "owner": "agent",
  "blockedBy": []
}
~~~

这是内部对象 / 文件 JSON 的例子。get_task 返回的是此对象的 **JSON 字符串**；其 json.dumps 使用默认的 ensure_ascii=True，非 ASCII 字符可表现为转义序列。

### 5.2 状态与依赖协议

~~~mermaid
stateDiagram-v2
    [*] --> pending: create_task
    pending --> pending: update_task 添加依赖
    pending --> in_progress: claim_task
    in_progress --> completed: complete_task
~~~

| 操作 | 运行约束 | 返回与后续 |
| --- | --- | --- |
| create_task | subject 非空；生成未占用文件名 | Created task ...；新任务固定 pending、owner=null、blockedBy=[] |
| update_task | 仅 pending 且 unowned；依赖必须存在；不能自依赖或形成环；去重追加 | Updated task ...；不替换已有依赖 |
| claim_task | 必须 pending、unowned，且所有依赖 completed | 设置 in_progress、owner="agent"；否则返回条件未满足文本 |
| complete_task | 必须 in_progress 且属于 owner="agent" | 设置 completed；返回 Completed ...；新解锁任务以 subject 列入 Unblocked 文本 |
| get_task | 任务文件存在、状态合法、文件中的 id 匹配请求 | JSON 字符串；不存在/读取异常上抛到 Dispatcher 处理 |
| list_tasks | 读取排序后的 task_*.json | 文本列表；pending=[ ]、in_progress=[>]、completed=[x] |

`addBlockedBy` 是工具输入的 camelCase 字段；`blockedBy` 是 Task 存储字段；TaskStore 内部方法的参数写为 `add_blocked_by`。这三者是实际适配命名。

TaskStore.create 的签名保留 status/owner 参数，但当前创建逻辑固定使用 pending/null。工具包装器的 claim_task/complete_task 固定 owner="agent"，不提供模型可自由指定 owner 的接口。

当前协议没有 Task.failed / Task.cancelled 状态。Subagent 结束或取消不会自动修改看板状态。看板文件的领取操作也没有实现事务或锁定领取协议。

主循环还会构造一个隐式看板提醒协议。连续 4 个工具批次没有调用 create_task / update_task / claim_task / complete_task 时，在该批次 user.content 的 tool_result 后追加以下块，并重置计数：

~~~json
{
  "type":"text",
  "text":"<reminder>Update the task board. Use create_task to plan, claim_task to start, and complete_task when finished. Call list_tasks if you need the current state.</reminder>"
}
~~~

Producer 为两个入口的工具循环，Consumer 为下一次模型请求；Source code：[main.py](../main.py#L645)、[main_async.py](../main_async.py#L667)。计数按工具批次，而非工具数量；上述四种名称出现就重置，不检查结果是否成功。task / get_task / list_tasks / subagent_status 本身不重置该计数。源码注释提到 3 轮，但运行条件是 >3，应按实际条件理解。

### 5.3 ID 与关联键

| ID | 示例 | 作用 / 存活范围 |
| --- | --- | --- |
| tool-use id | call_task_1 | 一次模型工具调用与即时 tool_result 的配对键；项目不规定其格式 |
| task_id | task_12345678 | 看板文件 ID；跨进程可从 tasks 文件读取 |
| run_id | run_00000000000000000000000000000001 | 一次 Subagent 执行；"run_" + 32 位 UUID hex；当前进程内查询 |
| 后台 Shell task_id | bg_0001 | 本进程递增编号，格式为 bg_ 加至少 4 位数字；不是看板任务 |

同一已认领任务可以传给不同的 Subagent 运行；Manager 没有额外规定一个 task_id 只能启动一次。task_id 不应被当成 subagent_status 的查询键。

## 6. [Internal] Subagent

### 6.1 启动输入与即时回执

| 项目 | 约定 |
| --- | --- |
| Purpose | 为已有看板任务启动独立上下文中的只读工作 |
| Producer | task tool → SubagentTools.run_subagent → SubagentManager.start |
| Consumer | 主循环、LLM；执行线程消费 SubagentState |
| Source code | [启动适配][subtools]、[Manager.start][submanager]、[状态][substate] |
| 输入 | task_id:string、prompt:string |
| 返回 | JSON 编码字符串；作为原 task 调用的 tool_result.content |
| 生命周期 | 校验任务与名额 → 登记线程 → 立即回执 → 执行与收尾 → 发布最终结果 |

启动约束：任务存在，status=="in_progress" 且 owner=="agent"；prompt.strip() 非空；Manager 尚未 closing 且运行数小于 max_workers。默认并发上限为 4。工具没有 max_turns、timeout 或 workdir 输入字段。

成功回执，下面展示**字符串解析后的对象**：

~~~json
{
  "status": "started",
  "task_id": "task_12345678",
  "run_id": "run_00000000000000000000000000000001",
  "message": "子 Agent 已启动。最终结果将自动作为 subagent_result 通知送达。"
}
~~~

对应外层工具结果：

~~~json
{
  "type": "tool_result",
  "tool_use_id": "call_task_1",
  "content": "{\"status\": \"started\", \"task_id\": \"task_12345678\", \"run_id\": \"run_00000000000000000000000000000001\", \"message\": \"子 Agent 已启动。最终结果将自动作为 subagent_result 通知送达。\"}"
}
~~~

失败回执：

~~~json
{
  "status": "not_started",
  "task_id": "task_12345678",
  "run_id": null,
  "error": "ValueError: 请先认领该任务，再启动 subagent"
}
~~~

其他启动失败，例如 prompt 为空、并发名额已满、关闭中或线程启动异常，也使用 not_started + null run_id + "异常类型: 文本"。

| 回执字段 | started | not_started | Consumer |
| --- | --- | --- | --- |
| status | "started" | "not_started" | 主循环 receipt.get("status") 与 LLM |
| task_id | 回显请求 ID | 回显请求 ID | LLM 关联看板任务 |
| run_id | 新运行 ID | null | 主循环 receipt.get("run_id")、状态查询与取消 |
| message | 启动通知文本 | 不构造 | LLM |
| error | 不构造 | 异常说明 | LLM |

主循环只在 status=="started" 且 run_id 为非空字符串时登记该次运行的工具统计。无法解析的回执、not_started 或其他工具错误不满足此隐式协议。

### 6.2 SubagentState 与执行权限

**Producer**：SubagentManager / SubagentExecutor。**Consumer**：执行器、结果整理器。**Source code**：[SubagentState][substate]、[SubagentExecutor][subexec]。

| 内部字段 | 类型 / 默认 | 用途 |
| --- | --- | --- |
| task_id、prompt | string | 任务关联与首条 user message |
| workdir | Path | 解析后必须等于 Executor 的当前 WORKDIR |
| run_id | string，自动生成 | 一次执行标识 |
| max_turns | int，30 | 工作模型请求轮数上限 |
| timeout_seconds | float，600 | 工作阶段预算 |
| summary_timeout_seconds | float，20 | 中断收尾请求预算 |
| status | string，"running" | 执行 / 最终状态 |
| turns_used、tool_call_count | int，0 | 工作请求轮数、工具尝试次数 |
| messages | list[dict]，[] | 独立历史；SDK block 保存为字典 |
| response_log | list[dict]，[] | 模型响应事实记录 |
| cancel_event | threading.Event | 取消信号，不能直接 JSON 序列化 |
| summary、remaining | string，"" | 最终交接内容 |
| error | string/null，null | 执行异常 / 截断说明 |
| warnings | array[string]，[] | 收尾等非主错误说明 |

Subagent 不继承主历史；初始历史是 `[{"role":"user","content":prompt}]`。可用工具只有 **read_file、glob、grep、load_skill**；执行器另以白名单再次检查，不仅依靠提示词。

Subagent 的工具执行直接走 execute_subagent_tool，不进入主 Dispatcher、PreToolUse/PostToolUse 或交互审批。它使用 Stop Hook；禁止的工具返回错误文本，参数非 dict 返回 "Error: 工具参数必须是一个对象"。允许的 Handler 返回值转为 str。TimeoutError / SubagentCancelled 继续上抛给子循环设置终态。

### 6.3 状态查询与取消回执

**Producer**：SubagentManager.get / cancel。**Consumer**：subagent_status / subagent_cancel → JSON 字符串 → 主 Agent。**Source code**：[Manager](../CodingAgent/subagent/manager.py#L97)、[工具适配](../CodingAgent/tools/adapters.py#L106)。

| 接口情形 | 返回字段 |
| --- | --- |
| status：执行或收尾中 | run_id、status="running"、message |
| status：已请求取消 | run_id、status="cancelling"、message |
| status：已发布终态 | 第 6.4 节完整最终对象 |
| status：不存在 | run_id、status="not_found"、error |
| cancel：仍活动 | run_id、status="cancelling"、message |
| cancel：已结束 | run_id、原终态 status、message；不是完整最终对象 |
| cancel：不存在 | run_id、status="not_found"、error |

实际运行快照例子：

~~~json
{
  "run_id": "run_00000000000000000000000000000001",
  "status": "running",
  "message": "子 Agent 正在执行或收尾，最终结果尚未发布。"
}
~~~

取消回执：

~~~json
{
  "run_id": "run_00000000000000000000000000000001",
  "status": "cancelling",
  "message": "已请求取消。当前操作返回或超时后停止后续步骤，最终结果将自动交接。"
}
~~~

查询已请求取消的运行时，message 为 "已请求取消，正在等待当前操作结束并交接结果。"。

不存在的运行：

~~~json
{
  "run_id": "run_missing",
  "status": "not_found",
  "error": "当前进程中没有这次运行，请检查 run_id。"
}
~~~

cancel_event 请求取消是协作信号，当前模型请求或工具可能先返回/超时。cancelling 是 Manager 根据 event 生成的查询/回执状态，不等于已经发布的 cancelled 终态。

查询不会消费 ready 中的自动通知；collect 清空通知队列，但已发布结果仍可查询。运行和结果均在内存中，进程重启后旧 run_id 不可据看板文件恢复。

关闭时 Manager 停止接收新运行、通知取消，默认最多等待 5 秒；shutdown 返回尚未发布结果的 run_id 列表，不强制终止线程，也不把它们伪装成 cancelled。

### 6.4 最终结果、response_log 与 evidence

| 项目 | 约定 |
| --- | --- |
| Purpose | 向主 Agent 交接已结束的执行及可核对证据 |
| Producer | SubagentExecutor.finalize_subagent、Manager 兜底、format_subagent_result |
| Consumer | subagent_status、通知注入器、主 Agent |
| Source code | [执行器][subexec]、[结果整理](../CodingAgent/subagent/results.py#L87)、[最终 JSON](../CodingAgent/subagent/results.py#L137) |
| 返回 | format_subagent_result 返回 JSON 字符串；Manager.get 对终态结果 json.loads 后返回新 dict |
| 生命周期 | 执行与收尾结束 → Manager 发布 state 到 results 并加入 ready → collect / status → 主 Agent 验收 |

| 最终字段 | 类型 | 含义 |
| --- | --- | --- |
| run_id、task_id | string | 执行与看板任务关联 |
| status | string | completed / failed / timed_out / budget_exhausted / cancelled |
| summary、remaining | string | 交接摘要、未完成事项 |
| error | string/null | 执行错误；不表示所有工具结果都成功 |
| warnings | array[string] | 收尾失败、空摘要等说明 |
| turns_used | integer | 工作模型请求尝试次数；收尾请求不另加一轮 |
| tool_call_count | integer | 本次子运行尝试执行的工具数量 |
| response_log | array[object] | 工作/收尾响应记录 |
| evidence | array[object] | 已有 tool_result 的工具证据预览 |

response_log 的每行字段：

| 字段 | 类型 / 值 | 来源 |
| --- | --- | --- |
| phase | "work" / "summary" | 记录调用场景 |
| turn | integer | state.turns_used 的当前值 |
| stop_reason | SDK 属性值，缺失为 null | getattr(response, "stop_reason", None) |
| content_types | array | 每个 block.type；缺失可为 null |
| max_tokens | integer | 工作 16384；收尾 4096 |
| output_tokens | SDK usage.output_tokens，缺失为 null | 不估算、不补入其他用量字段 |

evidence 的每行字段：

| 字段 | 类型 | 来源 / 规则 |
| --- | --- | --- |
| tool_use_id | string | 已记录 tool_result 的匹配 ID |
| tool | string | 对应 tool_use.name；找不到时 "unknown" |
| arguments | {"text":string,"truncated":boolean} | 原 input 用 json.dumps 转成文本后取前 1200 字符 |
| output | 同上 | 原工具输出字符串取前 1200 字符 |
| exit_code | 可选，兼容旧记录 | 仅 bash 输出能解析出该字段时加入；当前只读子工具不会产生此分支 |

预览中的 truncated 表示完整文本长度大于 1200。证据包含拒绝和报错结果；没有 tool_result 的未执行请求不变成 evidence。最终对象没有额外的 usage 字典。

**成功结果**：

~~~json
{
  "run_id": "run_00000000000000000000000000000001",
  "task_id": "task_12345678",
  "status": "completed",
  "summary": "CodingAgent/messages.py::extract_text 对非列表输入返回 str(content)。",
  "remaining": "",
  "error": null,
  "warnings": [],
  "turns_used": 2,
  "tool_call_count": 1,
  "response_log": [
    {"phase": "work", "turn": 1, "stop_reason": "tool_use", "content_types": ["tool_use"], "max_tokens": 16384, "output_tokens": 40},
    {"phase": "work", "turn": 2, "stop_reason": "end_turn", "content_types": ["text"], "max_tokens": 16384, "output_tokens": 60}
  ],
  "evidence": [
    {
      "tool_use_id": "call_sub_read_1",
      "tool": "read_file",
      "arguments": {"text": "{\"path\": \"CodingAgent/messages.py\", \"offset\": 4, \"limit\": 3}", "truncated": false},
      "output": {"text": "File: CodingAgent/messages.py\nLines 4-6 of 11\n4: def extract_text(content) -> str:\n5:     if not isinstance(content, list):\n6:         return str(content)\nNext offset: 7", "truncated": false}
    }
  ]
}
~~~

output_tokens 数字只是合成 SDK usage 值，不是实际调用统计。

**工作模型失败，收尾模型也失败的完整结果**：

~~~json
{
  "run_id": "run_00000000000000000000000000000002",
  "task_id": "task_12345678",
  "status": "failed",
  "summary": "本次执行结束：failed；已请求模型 1 轮，记录工具结果 0 条。",
  "remaining": "任务完成情况尚未确认，请主 Agent 检查返回的证据。",
  "error": "RuntimeError: offline error",
  "warnings": ["收尾总结失败，返回程序生成的基本交接：offline error"],
  "turns_used": 1,
  "tool_call_count": 0,
  "response_log": [],
  "evidence": []
}
~~~

| 状态 | 产生条件 / 下一步 |
| --- | --- |
| completed | 工作阶段无工具请求且提取到非空最终文本；主 Agent 仍需依据 summary/remaining/evidence 验收 |
| failed | 工作异常、空最终文本、Manager 捕获执行器异常等；保留已有记录并交接 |
| timed_out | 工作预算耗尽、TimeoutError 或 anthropic.APITimeoutError；停止启动新工具，尝试收尾 |
| budget_exhausted | 工作轮数自然耗尽，或工作响应 stop_reason=="max_tokens"；截断响应中的工具不执行，尝试收尾 |
| cancelled | 取消信号在执行/收尾检查或结果发布前被观察到；程序整理已有记录，观察到信号后不启动新的模型请求 |

例如以下是**终态对象的字段投影**，其余字段仍遵循上表：

~~~json
[
  {"status": "timed_out", "error": "TimeoutError: Subagent 工作时间已耗尽"},
  {"status": "budget_exhausted", "error": "模型达到单次输出 token 上限，响应未完整结束。"},
  {"status": "cancelled", "remaining": "本次运行因取消而结束，不能据此认定原任务完成。请主 Agent 根据已有证据决定后续处理。"}
]
~~~

Subagent 工作阶段只专门检查 max_tokens，没有主循环那套完整的 stop_reason 一致性校验；不能把第 2.3 节的全部响应校验套到子循环。

### 6.5 模型交接文本与程序兜底

**Producer**：子工作模型 / 中断收尾模型。**Consumer**：apply_subagent_summary。**Source code**：[子提示词][prompts]、[摘要解析](../CodingAgent/subagent/results.py#L104)、[finalize_subagent](../CodingAgent/subagent/executor.py#L249)。

提示词要求最终文本只包含两字段 JSON：

~~~json
{
  "summary": "CodingAgent/messages.py::extract_text 只拼接 text block 的 text，分隔符为换行。",
  "remaining": ""
}
~~~

| 层次 | 规则 |
| --- | --- |
| 提示词要求 | 只含 summary/remaining；值都为字符串；禁止代码围栏或额外正文 |
| 运行时解析 | 验证对象及两个字段都是 str；不会专门拒绝其他键 |
| 有效 JSON | summary.strip() 非空才覆盖现有 summary；remaining.strip() 写入 |
| 非 JSON / 字段不合约 | 原文本保留为 summary；remaining 指明模型未单列剩余问题，需要主 Agent 验收 |
| 空文本 | 增加 warning，保留程序生成的摘要与未完成说明 |
| 中断且没有 final_text | 额外发一次无 tools 收尾请求；超过 40000 字符的 transcript 使用头 8000 + 尾 32000 + 省略标记 |
| 收尾异常 / max_tokens | warning 记录失败，保留基础交接 |
| 取消 | 观察到取消后走程序整理；不启动新的模型请求；保留 messages、response_log、error 及取消前摘要 |

取消不是对同步 SDK 请求的即时中止。若工作或收尾请求已经发出，允许它先返回 / 超时，再在检查点整理为 cancelled；Manager 发布前再检查 event，确保发布前收到的取消不被误交付成 completed。已经发布的结果则保持原终态。

### 6.6 最终通知如何注入 messages

**Producer**：SubagentTools.inject_subagent_results。**Consumer**：主 / 异步主循环下一次模型请求。**Source code**：[通知注入](../CodingAgent/tools/adapters.py#L49)、[同步循环][mloop]、[异步循环][aloop]。

~~~text
Manager 完成执行与收尾
→ results[run_id] = state；ready 加入 run_id；释放并发名额
→ collect() 领取 ready 中的 state；清空 ready
→ format_subagent_result(state)：完整 JSON 字符串
→ text = "<subagent_result>\n" + JSON + "\n</subagent_result>"
→ 合并到最后的 user.content，或追加一条 user message
→ 下一次 Messages 请求
→ 主 Agent 判断是否继续工作 / 完成看板任务
~~~

通知外层的实际结构如下，text 内的 JSON 在此仅展示三个字段的**投影**；实际注入的是第 6.4 节完整对象：

~~~json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "<subagent_result>\n{\"run_id\":\"run_00000000000000000000000000000001\",\"task_id\":\"task_12345678\",\"status\":\"completed\"}\n</subagent_result>"
    }
  ]
}
~~~

- 最后一条消息是 user 且 content 为列表：extend 新通知块。
- 最后一条是 user 且 content 非列表：将原内容 str(...) 包成 text，再追加通知。
- 其他情形：新增一条 user message。
- 返回值是注入数量 int，不是最终结果字符串。
- 最终通知不复用原 task 的 tool_use_id；原 task 已由启动回执完成即时配对。

主循环每轮开头收集通知。如果模型本轮没有工具请求但后台仍活动，程序每 0.1 秒等待与收集；收到结果后进入下一轮模型请求。同步路径使用 time.sleep，异步路径使用 asyncio.sleep。确认无活动运行后还会再收集一次，处理完成时间与状态检查之间的竞态。

## 7. [Internal] Background Shell Task

| 项目 | 约定 |
| --- | --- |
| Purpose | 后台运行独立 Shell 命令，并通过新消息交付完成通知 |
| Producer | Dispatcher → BackgroundManager.start / _run / collect |
| Consumer | 主循环、LLM、read_file |
| Source code | [后台管理器][background]、[Shell][shell]、[Dispatcher][dispatcher] |
| 生命周期 | 即时文本回执 → 后台进程 → 完成登记 → collect → user text 通知 → 下一次模型请求 |

### 7.1 启动、内部状态与输出

只有 `tool_call.name=="bash"` 且 `tool_call.input.get("run_in_background") is True` 进入后台分支。

~~~json
{
  "type": "tool_use",
  "id": "call_bg_1",
  "name": "bash",
  "input": {"command": "python -c \"print('ready')\"", "run_in_background": true}
}
~~~

Dispatcher 回填原调用的即时结果：

~~~json
{
  "type": "tool_result",
  "tool_use_id": "call_bg_1",
  "content": "Background task started: bg_0001\nThe result will be collected and injected into the messages later."
}
~~~

内部登记（仅作对象投影，不是模型可调用的状态 API）：

~~~json
{
  "bg_0001": {
    "tool_use_id": "call_bg_1",
    "command": "python -c \"print('ready')\"",
    "status": "running"
  }
}
~~~

| 内部约定 | 内容 |
| --- | --- |
| tasks[bg_id] | 启动时 tool_use_id / command / status；结束时追加 exit_code |
| results[bg_id] | format_bash_output 生成的字符串；异常则 "Error: ..." |
| _ready | 已完成且未收集的 bg_id 列表 |
| completed | exit_code==0 |
| failed | exit_code 非 0 或 None，或后台执行异常 |
| running_tasks() | 内部 list[dict]，每项 task_id 加当前登记字段；只列 running |
| collect() | list[str]，每项完整通知文本；成功构造全部通知后移除对应 tasks/results 并清空 ready |

边界：command 为空时 BackgroundManager.start 返回 "Error: Invalid command" 而不抛异常；当前 Dispatcher 仍可能将它包成 "Background task started: Error: Invalid command..."。因此该句式本身不是可靠的成功状态字段；没有对应 bg_id 登记与通知。本文保留此当前行为，不将其改写为 JSON 成功回执。

### 7.2 task_notification 文本协议

~~~xml
<task_notification>
  <task_id>bg_0001</task_id>
  <status>completed</status>
  <exit_code>0</exit_code>
  <command>python -c "print('ready')"</command>
  <summary>ready</summary>
</task_notification>
~~~

| 标签 | 类型 / 条件 | 含义 |
| --- | --- | --- |
| task_id | 文本 | 后台 bg_id |
| status | completed / failed | 完成后的状态 |
| exit_code | 整数字符串或 "unknown" | 内部 exit_code=None 时写 unknown |
| command | XML 转义文本 | 原命令 |
| full_output | 可选路径文本 | 长输出成功存档时出现 |
| hint | 与 full_output 一起出现 | 指示使用 read_file 读取全文 |
| summary | XML 转义文本 | 短输出全文或最多 500 字符的首尾预览；存档失败时退回含说明的全文 |

带存档的两个可选标签形态：

~~~xml
<full_output>&lt;WORKDIR&gt;/.task_outputs/tool-results/shell_00000000000000000000000000000001.txt</full_output>
<hint>需要更多详情时，使用 read_file 读取 full_output 指向的文件。</hint>
~~~

实际路径来自 ShellRunner，WORKDIR 占位符不代表真实路径。生成器对 task_id、status、command、path、summary 使用 XML escape；没有构造新的 content block 类型。

注入 messages：

~~~json
{
  "role": "user",
  "content": [
    {
      "type": "text",
      "text": "<task_notification>\n  <task_id>bg_0001</task_id>\n  <status>completed</status>\n  <exit_code>0</exit_code>\n  <command>python -c \"print('ready')\"</command>\n  <summary>ready</summary>\n</task_notification>"
    }
  ]
}
~~~

注入规则与 Subagent 类似：扩展最后 user 列表，或把原文本包成 text，或新增 user。完成通知不再次生成原 call_bg_1 的 tool_result。collect 为一次性消费；当前没有注册独立的 background_status 或 background_cancel 工具。

## 8. [External / Adapter] Image / Multimodal

| 项目 | 约定 |
| --- | --- |
| Purpose | 让模型接收实际图片，同时保留可重新读取的本地路径 |
| Producer | FileTools.run_read_image、MCPClient 图片 formatter、make_image_content |
| Consumer | Anthropic tool_result、终端预览、ContextCompactor、预算估算 |
| Source code | [images.py][images]、[read_image](../CodingAgent/tools/files.py#L272)、[MCP 图片结果](../CodingAgent/mcp/client.py#L185) |
| 生命周期 | 读取 / 保存图片 → text 元数据 + image → tool_result → 旧历史清理时去掉图片数据 → read_image(path) 重新加载 |

### 8.1 图片内容块

read_image 的 ToolContent：

~~~json
[
  {
    "type": "text",
    "text": "Image file: <WORKDIR>/screenshots/result.png\nMedia type: image/png\nUse read_image(path) to reload this image after compaction."
  },
  {
    "type": "image",
    "source": {
      "type": "base64",
      "media_type": "image/png",
      "data": "<base64-image-data>"
    }
  }
]
~~~

模型收到的外层：

~~~json
{
  "type": "tool_result",
  "tool_use_id": "call_image_1",
  "content": [
    {
      "type": "text",
      "text": "Image file: <WORKDIR>/screenshots/result.png\nMedia type: image/png\nUse read_image(path) to reload this image after compaction."
    },
    {
      "type": "image",
      "source": {"type": "base64", "media_type": "image/png", "data": "<base64-image-data>"}
    }
  ]
}
~~~

| 字段 / 数据 | 规则 |
| --- | --- |
| text 中的 Image file | Path.resolve() 产生的绝对路径；不把 path 放成 image 顶层字段 |
| media_type | 支持 image/png、image/jpeg、image/gif、image/webp |
| data | ASCII Base64 字符串；示例占位值不是有效图片 |
| 原图校验 | 非空，最大 32 MiB；按文件头识别格式 |
| MCP 图片校验 | 严格 Base64 解码；声明 MIME 必须匹配检测结果 |
| MCP 保存路径 | 配置 image_dir 下 UUID hex 文件名，加检测类型对应的扩展名 |
| 图片目录未配置 | MCP formatter 抛 ValueError，call_tool 转为 MCP error 文本 |
| 多张图片 | MCP formatter 顺序追加每张的元数据 text 和 image |

当前 CLI 输入只有 query 文本，没有实现专门的“用户上传图片”输入接口。images 辅助函数可以处理历史中顶层 image block 及 tool_result 内的 image；这不等于 CLI 已提供图片附件功能。

### 8.2 预览、清理与重读

`tool_result_preview(content, limit=500)` 返回终端文本。存在图片时显示图片数量、路径元数据与其他文本，不打印 Base64。默认文字预览超过 500 字符会截断并追加终端预览提示；原 ToolContent 不由此改变。

历史清理使用替换块：

~~~json
{
  "type": "text",
  "text": "[Image data removed from history. Use read_image with the saved file path to inspect it again.]"
}
~~~

`text_history(messages)` 生成深拷贝，替换顶层和嵌套 tool_result 图片，供摘要、转录及文字预算估算使用。micro_compact 可在较旧已消费的工具结果中替换图片；保存路径文本仍保留。需要重新看图时，模型再次调用 read_image，生成一个新的 tool_use / tool_result 配对。

## 9. [Internal] Hook、权限与异步适配

### 9.1 Hook 回调接口

| 项目 | 约定 |
| --- | --- |
| Purpose | 在输入、工具执行和结束处调用已注册回调，观察或影响下一步 |
| Producer | 输入入口、Dispatcher、主 / 子循环 |
| Consumer | HookRegistry / AsyncHookRegistry 中按顺序注册的 callback |
| Source code | [HookRegistry / DefaultHooks][hooks]、[AsyncHookRegistry][ahooks]、[主入口注册](../main.py#L240)、[异步注册](../main_async.py#L783) |
| 接口 | register(hook_name, callback)；trigger(hook_name, *args)；异步 trigger_async |
| Schema | Python 调用约定，没有 Hook JSON envelope |

| Hook 名称 | 实际参数 | 默认回调 / Consumer 对返回值的处理 |
| --- | --- | --- |
| UserPromptSubmit | query:str | context_inject_hook 只打印工作目录并返回 None；入口忽略 trigger 返回值 |
| PreToolUse | tool_call SDK 对象 | log_hook，然后权限回调；Dispatcher 在返回值为真时阻断 |
| PostToolUse | tool_call、原始 Handler output | large_output_hook 只提醒；Dispatcher 不用回调返回值替换 output |
| Stop | messages:list[dict]、本 Agent 的 tool_call_count:int | summary_hook 打印计数并返回 None；循环将真值结果作为新 user.content 后继续 |

触发规则：按注册顺序调用，遇到**首个非 None 返回值**立即短路。False 或 "" 也会阻止后续回调被调用，但 Dispatcher / Stop 调用方另外按真值判断是否阻断或继续；两层条件不能混写成同一个规则。

异步 registry 支持普通返回值及 awaitable，等待后仍按“非 None”短路；没有并发执行 Hook。异步主入口只为工具调度另建异步 Pre/Post registry，UserPromptSubmit 和 Stop 仍调用原同步 HOOKS。

具体拒绝流转例子：

~~~text
PreToolUse(tool_call.name="mcp__local__read_file", tool_call.input={"path":"README.md"})
→ 权限确认被拒绝
→ "Permission denied by user"
→ Dispatcher 直接返回该字符串
→ {"type":"tool_result","tool_use_id":"call_mcp_read_1","content":"Permission denied by user"}
~~~

DefaultHooks.large_output_hook 只在工具的**文字**超过 100000 字符时打印提醒，不把图片 Base64 计入，也不压缩输出。Subagent 只消费 Stop Hook，工具路径没有 Pre/Post Hook。

### 9.2 权限 decision 与拒绝字符串

**Producer**：PermissionManager / AsyncPermissionManager、ask_user。**Consumer**：PreToolUse / Dispatcher。**Source code**：[权限][permissions]、[异步权限][apermissions]。

| 接口 / 字段 | 返回形态 |
| --- | --- |
| check_deny_list(command) | None 或 "Blocked: '<pattern>' is on the deny list" |
| check_rules(tool_name, args) | None 或原因字符串 |
| ask_user(tool_name, args, reason) | "allow" / "deny" |
| check_permission(block) / 异步同类方法 | None 允许，或拒绝原因字符串 |
| get_mcp_policy(tool_name) | 字符串；"allow" 直接通过，其他值触发确认 |

当前规则检查：文件工具越出工作区 → "Access outside workspace"；潜在破坏性命令 → "Potentially destructive command"；MCP 拒绝 → "Permission denied by user"。deny list 的匹配是拒绝，不通过用户确认覆盖。

确认输入只接受 y/yes 为 allow，其他为 deny。后台线程需要确认时直接 deny，不建立跨线程审批队列。异步 ask_user 的 EOF 返回 deny；同步 ask_user 未捕获 input 的 EOFError，可能上抛。

权限返回值是内部 decision 或工具正文，不是 Anthropic permission block，也不是统一 status 对象。

### 9.3 异步函数返回与取消边界

**Producer / Consumer**：异步主循环、Dispatcher ↔ 同步 Handler / MCP 协程。**Source code**：[run_sync / finish_task](../CodingAgent/async_support.py#L14)、[AsyncToolDispatcher][adispatcher]、[AsyncConsole](../CodingAgent/async_support.py#L55)。

| 接口 | 约定 |
| --- | --- |
| run_sync(func, *args, **kwargs) | 在线程执行同步函数并等待其实际结束；结果类型保持原函数结果 |
| finish_task(task) | 外部取消先等内部任务实际完成，再传递 CancelledError；不伪造成功结果 |
| AsyncConsole.read(prompt) | 返回输入字符串；内部锁保证同一时刻一个输入提示 |
| AsyncToolDispatcher | 指定本地工具在线程执行；其他 Handler 直接调用，返回 awaitable 时 await |
| BackgroundManager / SubagentManager | 同步 start/collect/status/cancel 接口；后台工作仍由原线程管理器持有 |

线程执行的工具集合为 bash、read_file、write_file、edit_file、glob、grep、load_skill、read_image。此适配改变等待方式，不改变 ToolContent 或通知结构。

## 10. [Internal] Compact / Context

### 10.1 请求预算对象与估算

| 项目 | 约定 |
| --- | --- |
| Purpose | 在模型请求前检查本地 token、请求体字节和图片数量预算 |
| Producer | context_budget.request_payload / estimate_* |
| Consumer | ContextCompactor.prepare、主循环 |
| Source code | [预算函数][context]、[prepare](../CodingAgent/compact.py#L483)、[配置][config] |
| Schema | 内部估算对象仅包含 system、messages、tools；不是完整 Messages 请求 |

~~~json
{
  "system": "当前 Agent 的 system prompt。",
  "messages": [{"role": "user", "content": "读取消息协议。"}],
  "tools": [
    {
      "name": "read_file",
      "description": "Read a numbered page of a UTF-8 file. offset is the 1-based starting line (default 1). limit defaults to 200 and is capped at 200 lines. Use the returned next offset for the next page.",
      "input_schema": {
        "type": "object",
        "properties": {
          "path": {"type": "string"},
          "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 200},
          "offset": {"type": "integer", "minimum": 1, "default": 1}
        },
        "required": ["path"]
      }
    }
  ]
}
~~~

这是估算对象的合成投影示例；实际 tools 来自完整工具池，不只含本例一个工具。

| 常量 / 输入 | 当前值 | 含义 |
| --- | --- | --- |
| context_window_tokens | 默认 1000000 | 项目本地窗口配置 |
| COMPACT_TRIGGER_RATIO | 0.90 | token / body 预算触发比例 |
| output_reserve | 主循环 32768 | max(主输出 16384，重试输出 32768)；独立于估算对象 |
| IMAGE_TOKEN_BUDGET | 1024 | 每张图片额外 token 预留 |
| MAX_REQUEST_BODY_BYTES | 48 × 1024 × 1024 | 本地请求体上限基数 |
| MAX_IMAGES_PER_REQUEST | 600 | 本地图片数量限制 |

token 估算把去图片数据后的 JSON 文本按字符加权：CJK 基本汉字 6 单位，ASCII 字母 3，其他字符 10；总单位向上除以 10，再加图片数量 × 1024。字节估算使用含实际 Base64 的 JSON UTF-8 字节长度。它们不是服务端 tokenizer 或 usage 统计。

prepare 的通过条件：

~~~text
estimated_tokens + output_reserve < int(context_window_tokens × 0.90)
estimated_body_bytes < int(48 MiB × 0.90)
image_count <= 600
~~~

生命周期：副本处理最新批次长输出 → 能放下则返回 → micro_compact 旧结果 → 仍超预算则摘要旧历史 → 仍不满足则 ContextBudgetError；调用方只有成功返回后才替换 messages。

压缩器的成功返回契约如下；SDK block 可继续保留在近期历史中，不要求所有 content 已转成 dict：

| 方法 | 成功返回 | 无法完成 / 失败 |
| --- | --- | --- |
| prepare(messages, active_request, *, system_prompt, tools, output_reserve) | list[dict]，基于深拷贝的候选历史；压缩后必须满足本地预算 | 抛异常，调用方不覆盖原历史 |
| tool_result_budget(messages, max_chars=None) | 传入的历史 list；就地处理最后 user 批次的大输出 | 保存失败时对应正文保留；不保证整批一定降到阈值内 |
| micro_compact(messages, *, should_stop=None) | 传入的历史 list；就地处理较旧已消费的结果 | should_stop(messages) 为真时提前结束；存档失败保留正文 |
| compact_history(messages, active_request) | 有旧历史时为 [summary_message, *recent_history]；无旧历史时返回同一个 messages 对象 | 摘要 / 转录 / 减少量检查失败时抛异常 |
| reactive_compact(messages, active_request) | 成功替换后的历史 list | compact_history 返回原对象时抛 RuntimeError，其他异常上抛 |
| summarize_history(old_history) | 非空 str 摘要 | stop_reason、工具块或空文本不符合要求时抛 RuntimeError |
| write_transcript(messages) | 新转录文件的 Path | 文件异常上抛 |

### 10.2 工具输出全文标记

**Purpose**：缩短上下文，同时让模型能用 read_file 找回全文。**Producer**：ShellRunner、ContextCompactor。**Consumer**：模型、persisted_output_path、read_file。**Source code**：[Shell][shell]、[持久化预览](../CodingAgent/compact.py#L158)。

~~~text
<persisted-output>
Full output: <WORKDIR>/.task_outputs/tool-results/shell_00000000000000000000000000000001.txt
Preview:
首部输出
... [中间内容已省略，请读取全文] ...
尾部输出
需要完整详情时，请使用 read_file 读取上述文件。
</persisted-output>
~~~

这是 Shell 的标记形态。Compactor 使用同样的外层标记与 `Full output:` / `Preview:` 行，但自己的省略文字不同，且不追加 Shell 的那句全文提示。

| 生产场景 | 阈值 / 预览 | 文件约定 |
| --- | --- | --- |
| 前台 Shell | >50000 字符存档；预览最多 500 字符 | shell_<UUID hex>.txt |
| 后台 Shell | >500 字符存档；预览最多 500 字符 | 相同 |
| 最新 user 批次结果预算 | 文字合计 >200000 时，优先处理 >30000 字符的结果 / 图片列表中的长 text | <净化 tool_use_id>_<UUID hex>.txt |
| 较旧已消费结果 micro_compact | 除最近 3 个结果外，>500 字符可存档；替换确实缩短才采用 | 同上 |
| Compactor.persisted_preview | 默认原文预览 2000 字符；micro 使用 500；省略标记和路径另计 | 可以复用已存在、已验证的全文路径 |

标记仍是 tool_result.content 中的字符串，不改变 tool_use_id。路径识别依赖确切的 `<persisted-output>\n` 开头及 `Full output: ` 行；还验证路径位于 tool_results_dir 下且文件存在。

存档失败：Shell 退回包含失败说明的全文，full_path=None；Compactor 退回原输出，不返回虚假的成功路径。

### 10.3 历史摘要与 transcript

| 项目 | 约定 |
| --- | --- |
| Purpose | 用摘要替换旧历史，同时保留最近完整交互和全文转录 |
| Producer | ContextCompactor |
| Consumer | 下次模型请求、需要查阅转录的工具 |
| Source code | [历史切分](../CodingAgent/compact.py#L116)、[摘要](../CodingAgent/compact.py#L391)、[summary_message](../CodingAgent/compact.py#L429) |
| 存储 | .transcripts/transcript_<UUID hex>.jsonl，每行一个序列化 Message |
| 下一步 | [summary_message, *recent_history] → Messages API |

切分尽量保留最近至少 5 条消息，且只在此前 tool_use 都匹配到 tool_result 时允许切开；最后 assistant 响应之后的新 tool_result 保持在近期历史。无对应调用的结果在切分检查中会报错。

transcript 使用 text_history 的副本：SDK block 转字典，图片数据替换为第 8.2 节文字；它不保存图片 Base64 原文。消息行示例：

~~~jsonl
{"role":"assistant","content":[{"type":"tool_use","id":"call_read_1","name":"read_file","input":{"path":"CodingAgent/messages.py","offset":4,"limit":3}}]}
{"role":"user","content":[{"type":"tool_result","tool_use_id":"call_read_1","content":"File: CodingAgent/messages.py\nLines 4-6 of 11\n4: def extract_text(content) -> str:\n5:     if not isinstance(content, list):\n6:         return str(content)\nNext offset: 7"}]}
~~~

摘要模型返回的是普通 text，不要求固定 JSON 业务对象。运行校验必须 stop_reason=="end_turn"、无 tool_use、摘要非空。

新的 user 摘要消息：

~~~json
{
  "role": "user",
  "content": "[Compacted]\n\nCurrent user request:\n整理消息协议。\n\nConversation summary (reference only):\n\"已读取 CodingAgent/messages.py；还需整理 MCP 与 Subagent。\"\n\nFull transcript: <WORKDIR>/.transcripts/transcript_00000000000000000000000000000001.jsonl"
}
~~~

其中摘要经过 json.dumps 后嵌进 reference 文本，以保留字符串引用边界。当前用户请求单独保留；近期交互原样接在摘要消息后。

| 失败 / 边界 | 行为 |
| --- | --- |
| 摘要请求自身超过本地预算 | ContextBudgetError；当前没有实现分段摘要 |
| 摘要截断、返回工具、为空 | 抛异常，不提交替换历史 |
| 候选摘要既未减少 token 也未减少字节 | 抛异常，不提交替换历史 |
| prepare 压缩后仍超预算 | ContextBudgetError，不提交替换历史 |
| 写入了 transcript 后摘要失败 | transcript 文件可已存在；历史仍不被候选覆盖 |
| 最新工具结果里的图片 | tool_result_budget 只处理长 text，micro 不清理尚未消费的结果 |

### 10.4 显式 compact 与响应式压缩

`compact` Handler 仅返回说明文字；主循环看到 tool_call.name=="compact" 就置 compact_requested。该标志不依赖 Handler 回执的内容。

~~~text
模型请求 compact（可能与其他工具同批）
→ 主循环按顺序执行所有工具
→ 追加整批 user tool_result
→ 同步入口 compact_history / 异步入口 reactive_compact
→ 新 history 进入下一轮模型请求
~~~

没有可摘要的旧历史时，同步 compact_history 返回原历史；异步显式路径调用 reactive_compact，会因没有可替换历史而报错。自动 prepare 在两个入口共用同一实现。

模型请求异常文本包含 `prompt_too_long` 或 `too many tokens` 时，主循环尝试 reactive_compact 后重试；连续这种异常最多压缩重试一次，取得合法响应后计数清零。不是按照官方错误对象字段识别，也不会把任意模型错误都当成上下文错误。

## 11. [Internal] Memory

### 11.1 Memory 模型交换协议

| 项目 | 约定 |
| --- | --- |
| Purpose | 选择相关长期记录、从对话提取候选、合并已有记录 |
| Producer | MemoryManager 的辅助模型请求 |
| Consumer | 数组解析器、候选验证器、MemoryStore、system prompt |
| Source code | [MemoryManager][memorymanager]、[MemoryStore][memorystore]、[提示词][prompts] |
| 生命周期 | 请求开始召回 → 加入 system 文本 → 主 Agent 正常结束后提取 → 有新记录才触发合并检查 |

| 交换阶段 | 模型文本 / 解析后的形态 | Consumer |
| --- | --- | --- |
| 选择 | JSON array[int]，当前 catalog 的索引，例如 [0,2] | select_relevent_memory，转为 filename 列表 |
| 提取 | JSON array[object]，name/type/scope/description/body | validate_memory_record(require_scope=True)、should_store_memory |
| 合并 | JSON array[object]，name/type/description/body；不要求 scope | validate_memory_record、MemoryStore.replace_records |
| 召回交付 | JSON 编码字符串，array[{source,content}] | build_system_prompt 的 Relevant memory records 文本 |

数组解析器从文本中依次尝试以 "[" 开头的 JSON 数组，因此能提取带前后正文的数组；找不到数组返回 []。它不要求整段输出严格只含 JSON。

选择示例：

~~~json
[0, 2]
~~~

索引必须引用本次 catalog 的位置；代码以 isinstance(index, int) 及范围检查过滤，再去重，默认最多 5 个 filename。请求/处理抛异常时按关键词选择；仅返回无法提取数组的文本会得到 []，不必然触发关键词 fallback。

提取候选：

~~~json
[
  {
    "name": "Python code style",
    "type": "feedback",
    "scope": "persistent",
    "description": "后续 Python 代码采用清晰命名。",
    "body": "用户要求以后新增 Python 代码使用清晰的变量和函数名称。"
  }
]
~~~

| 候选字段 | 约定 / 运行校验 |
| --- | --- |
| name | 名称；转 str 后 strip，非空 |
| type | user / feedback / project / reference |
| scope | 提取时 persistent / current_task；只有 persistent 可存储 |
| description | 简短描述；转 str 后 strip，非空 |
| body | 正文；转 str 后 strip，非空 |

字段类型是期望形态；验证器实际使用 str(...) 转换，不对输入原类型做严格 string 校验。验证后只保留约定字段；非空 scope 可以作为验证结果字段，但不写入记忆文件。

should_store_memory 排除当前会话/任务等临时标记；名称 slug、规范化 description 或规范化 body 任一与已有记录相同就拒绝。每写入一个候选都会加入本批次去重集合。

召回交付值，下面展示**JSON 字符串解析后的数组**：

~~~json
[
  {
    "source": "python-code-style.md",
    "content": "---\nname: Python code style\ndescription: 后续 Python 代码采用清晰命名。\ntype: feedback\n---\n\n用户要求以后新增 Python 代码使用清晰的变量和函数名称。"
  }
]
~~~

load_memories 返回上面数组的 JSON 字符串，或空字符串 ""。召回的 Markdown 文件全文累计默认最多 20000 字符，包含 frontmatter；可截断单条文件内容，预算不包含 JSON 包裹开销。原始记忆文件不因此改变。

### 11.2 Memory 文件、索引与内部记录

**Producer**：MemoryStore。**Consumer**：MemoryManager、prompt builder。**Source code**：[文档格式](../CodingAgent/memory/store.py#L35)、[列表记录](../CodingAgent/memory/store.py#L147)。

文件名由 `memory_slug(name)` 生成：名称小写，将非 \w 字符序列变为 "-"，去掉首尾 "-_"；空结果回退 "memory"。例如上述记录写入 `.memory/python-code-style.md`：

~~~markdown
---
name: Python code style
description: 后续 Python 代码采用清晰命名。
type: feedback
---

用户要求以后新增 Python 代码使用清晰的变量和函数名称。
~~~

| 位置 / 对象 | 字段 |
| --- | --- |
| Markdown frontmatter | name、description、type |
| Markdown 正文 | body 对应的文本；不是 YAML body 字段 |
| list_memory_files 返回的记录 | name、type、description、filename、body |
| 索引 MEMORY.md | 每条一行 Markdown 链接和描述 |
| load_memories 的召回项 | source、content |

内部记录：

~~~json
{
  "name": "Python code style",
  "type": "feedback",
  "description": "后续 Python 代码采用清晰命名。",
  "filename": "python-code-style.md",
  "body": "用户要求以后新增 Python 代码使用清晰的变量和函数名称。"
}
~~~

索引行：

~~~markdown
- [Python code style](python-code-style.md) - 后续 Python 代码采用清晰命名。
~~~

读取列表时，对缺失元数据可回退：name 使用文件 stem、type 使用 "unknown"、description 使用 ""。创建接口则只接受四种 MEMORY_TYPES 和非空名称/描述/正文。

文件名必须是单个 basename；不能通过记忆读取接口把 MEMORY.md 当成记录，也不能越出工作区中的记忆目录。每次写入后重建索引。

### 11.3 取材、合并与失败返回

| 行为 | 当前约定 |
| --- | --- |
| 选择 query | 最近最多 4 条有文字的 user message，合并后取前 4000 字符 |
| 提取 dialogue | 最近最多 12 条 message 的文字，合并后取前 8000 字符 |
| message_text | 字符串原样；列表只取 text block，不展开 tool_result 内的 content |
| 后台通知 | 因为是 user 中的 text block，可能进入上述取材；没有专门解析通知 envelope 的排除器 |
| 合并触发 | 记录数 >40；主循环仅在本次 extract_memories 返回非零后调用合并检查 |
| 合并输入 | 当前记录 catalog；超过 80000 字符跳过本次合并 |
| 合并输出 | 有效且非空的记录数组，名称 slug 不得重复 |
| 最多 30 条 | 是合并提示词要求，程序没有对输出数量再裁剪 |
| 失败 | extract_memories / consolidate_memories 通常打印 skipped 并返回 0；load_memories 空召回返回 "" |
| 合并替换 | 保留旧文件快照；写入异常路径尝试恢复旧文件并重建索引 |

| 方法 | 成功返回 | 跳过 / 异常边界 |
| --- | --- | --- |
| select_relevent_memory(messages, max_items=5) | list[str]，所选 filename | 无记录 / 无 query 返回 []；请求 / 解析异常转关键词选择；列表读取错误可先于异常捕获发生 |
| load_memories(messages) | str，source/content 数组的 JSON 编码 | 没有召回为 ""；读取或路径错误可能上抛，由调用方处理 |
| extract_memories(messages) | int，本次成功写入的候选数量 | 无可存候选 / 捕获异常返回 0；之前已经写入的文件不会因后续候选失败自动回滚 |
| consolidate_memories() | int，替换成功后的记录总数；不是删除数量 | 未达阈值 / 捕获异常返回 0；替换异常路径尝试恢复快照 |
| MemoryStore.write_memory_file(...) | Path，写入的记忆文件 | 校验 / I/O 错误上抛 |
| MemoryStore.replace_records(records, consolidated) | None | 替换异常路径尝试恢复后上抛 |

Source code：[选择与召回](../CodingAgent/memory/manager.py#L180)、[提取](../CodingAgent/memory/manager.py#L239)、[合并](../CodingAgent/memory/manager.py#L304)。选择 catalog 截为 12000 字符；提取的已有记录 catalog 截为 6000 字符。索引是本次 list_memory_files 的位置，不是永久记录 ID。

合并候选例子：

~~~json
[
  {
    "name": "Python code style",
    "type": "feedback",
    "description": "后续 Python 代码采用清晰命名。",
    "body": "保留用户的长期命名偏好。"
  }
]
~~~

Memory 文本进入 system prompt 时被说明为 earlier-session background knowledge。文件索引只是 catalog，模型本次可用的正文是实际召回的 source/content 文本；没有专门注册 Memory 工具。

## 12. [Internal] 配置、Skill、项目指引与状态边界

### 12.1 AgentConfig 与目录协议

**Purpose**：让各模块共享模型、工作区、目录和本地预算。**Producer**：load_config。**Consumer**：两个入口及模块构造。**Source code**：[AgentConfig][config]、[客户端构造](../main.py#L85)、[依赖声明](../requirements.txt)。

| 配置 / 输入 | 实际值或来源 |
| --- | --- |
| workdir | Path.cwd()；由启动目录决定，不固定为源码目录 |
| model | os.getenv("ANTHROPIC_MODEL")，可能为 None |
| max_subagents | dataclass 默认 4；load_config 未从环境变量读取此项 |
| context_window_tokens | dataclass 默认 1000000；load_config 未从环境变量读取此项 |
| ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL | 客户端参数；不写入本文或例子 |
| skills_dir | WORKDIR/skills |
| transcript_dir | WORKDIR/.transcripts |
| tool_results_dir | WORKDIR/.task_outputs/tool-results |
| memory_dir / memory_index | WORKDIR/.memory；其下 MEMORY.md |
| task_dir | WORKDIR/tasks |
| mcp_config_path | WORKDIR/mcp.json |

AgentConfig 是 frozen dataclass；Path 属性不是 JSON 字段。依赖文件声明 anthropic==1.2.0、mcp==2.1.1；这只是仓库声明，不作为本次实时服务兼容性证明。

### 12.2 Skill 与项目指引

| 项目 | 约定 |
| --- | --- |
| Purpose | 按需向模型提供技能原文和项目约定 |
| Producer | skills/*/SKILL.md、根 AGENT.md、SkillLoader |
| Consumer | build_system_prompt、build_subagent_prompt、load_skill |
| Source code | [SkillLoader][skillloader]、[项目指引][instructions]、[提示词][prompts] |
| 生命周期 | 启动扫描 catalog / 读取 AGENT.md → system prompt 提供索引 → 模型按名称 load_skill → 原文作为 tool_result |

Skill 的 manifest 读取 name、description；缺 name 回退目录名，缺 description 回退正文首行。内部 skills[name] 包含 name、description、content，其中 content 为完整文件原文。

仓库真实 Skill 例子：

~~~markdown
---
name: count-lines
description: 当用户要求统计代码文件行数时使用。
---
~~~

该块是真实 manifest 的 frontmatter 片段，不是 load_skill 的完整输出。模型输入 `{"name":"count-lines"}`，不是按目录名 `count_lines` 调用。catalog 格式为：

~~~text
count-lines: 当用户要求统计代码文件行数时使用。
python-file-summary: 当用户要求分析或概览 Python 文件时使用这个 Skill。
~~~

load_skill 找不到名称时返回 `Skill <name> not found. Available skills: ...`。它只交付指令文本，不增添工具或扩大子 Agent 权限。

项目指引读取的文件是根目录 **AGENT.md**（单数），以 utf-8-sig 启动时读取一次；不存在或纯空白则返回 ""，其他读取错误抛 RuntimeError。内容作为主 / 子 system prompt 的文本段，不放入被摘要替换的历史消息。当前没有遍历各子目录指引的加载协议。

### 12.3 状态与错误不应合并成一个枚举

| 协议 | status / error 形态 | Consumer |
| --- | --- | --- |
| 模型响应 | stop_reason；主循环只接受 end_turn/tool_use，max_tokens 有专门重试 | 响应校验器 |
| Task Board | pending / in_progress / completed | 主 Agent、依赖检查 |
| task 启动 | started / not_started | 主循环、LLM |
| Subagent 查询 | running / cancelling / not_found，或终态对象 | 主 Agent |
| Subagent 最终结果 | completed / failed / timed_out / budget_exhausted / cancelled；error 与 warnings 独立 | 主 Agent 验收 |
| 后台 Shell | running；通知使用 completed / failed，exit_code 为文本 | BackgroundManager、LLM |
| 工具错误 / 权限拒绝 | content 中的字符串或错误 text block | LLM |
| 主循环终止异常 | IncompleteResponseError、AgentRoundLimitError、ContextBudgetError | CLI 输出 [未完成]，退出码 1 |
| 实验 | initializing / agent_returned / interrupted / error | 人工复核 |

主循环默认允许 60 轮模型执行循环；超过额度前读取 y/yes 才增加下一批 60 轮，空输入、n/no 或 EOF 拒绝继续。该确认没有 JSON receipt。正常最终消息、错误退出和资源清理都不自动撤销此前的文件修改。

## 13. 附录：实验、历史接口与兼容分支

### 13.1 实验结果与验收格式 [Internal]

| 项目 | 约定 |
| --- | --- |
| Purpose | 保存一次真实模型实验的执行记录，供外部验收与人工复核 |
| Producer | experiments/run.py；experiments/grade_build.py |
| Consumer | result.json、控制台摘要、实验复核者 |
| Source code | [result 初始化][experiment]、[结果收尾与分支](../experiments/run.py#L160)、[run_checks][grading] |
| 启用情况 | 仅手动执行实验启动器时启用；不是主 Agent 的工具 |
| 生命周期 | 建立隔离 workspace → 加载同步 main.py → 观察执行 → cleanup → 外部验收 / 审查记录 → 保存 result.json |

实验入口接受 build / serial / parallel。它将结果写入 experiments/runs/<experiment_run_id>/；experiment_run_id 由时间、mode 和 8 位 UUID 片段构成，独立于子 Agent 的 run_id。

result.json 字段按写入阶段区分：

| 字段 | 类型 / 写入条件 | 含义 |
| --- | --- | --- |
| experiment | string，初始化 | build / serial / parallel |
| experiment_run_id | string，初始化 | 本次实验标识 |
| agent_source_sha256 | string，初始化 | main.py 与 CodingAgent 下 Python 文件的相对路径和字节联合哈希；没有包含 main_async.py |
| python | string，初始化 | sys.version 的首段 |
| status | string，初始化及结束 | initializing → agent_returned / interrupted / error |
| interactive_prompts | int，初始化 0 | 包装 input 的调用次数 |
| elapsed_seconds | number 或 null | Agent 执行部分耗时，结束时保留 3 位小数；不包含外部 grading |
| subagent_intervals | array，初始化 [] | 观察到的子 Agent 执行区间，见下表 |
| peak_active_subagents | int，初始化 0 | observed_run 同时处于 active 集合的峰值 |
| review_tool_violations | array[string] | 审查模式中调用 bash / write_file / edit_file / connect_mcp / mcp__* 的名称记录；观察 Hook 不负责阻断 |
| result_directory | string | 本次结果目录 |
| model | string/null，成功加载入口后 | 实际 MODEL 配置；未配置时可能为 null |
| error | string，仅异常分支 | `TypeName: message` |
| cleanup_error | string，仅清理异常 | 清理失败文本；独立于主 status |
| final_answer | string，收尾 | 历史中最后一条 assistant 消息的 text |
| changed_initial_files | array[string]，收尾 | 初始 workspace 文件内容哈希发生变化的相对路径 |
| acceptance | array[check]，build 且 agent_returned | 外部验收结果 |
| acceptance_passed / acceptance_total | int，同上且 grading 成功 | 通过数量 / 总数量 |
| task_passed | bool，build 验收或 grading 异常 | 所有 checks 通过且初始文件未被修改；grading 异常为 false |
| grading_error | string，仅 grading 异常 | 外部验收异常文本 |
| unexpected_new_files | array[string]，serial / parallel | 非初始且不属于 tasks / .memory / .transcripts / .task_outputs / __pycache__ 的新文件 |
| four_runs_completed | bool，serial / parallel | 恰好 4 条区间且发布状态都为 completed |
| scheduling_observation | string，serial / parallel | serial_observed / parallel_overlap_observed / requested_schedule_not_confirmed |
| review_score | string，serial / parallel | 当前固定为 pending_human_review |

每条 subagent_intervals 记录的协议：

| 字段 | 类型 / 生命周期 |
| --- | --- |
| run_id / task_id / prompt | string；进入 observed_run 时保存 |
| start_seconds | number；相对 Agent 计时起点 |
| end_seconds | number 或 null；初始 null，执行器返回 / 抛错的 finally 设置 |
| status | string；执行器收尾后保存，cleanup 后以 manager.get(run_id)["status"] 再核对 |
| turns_used | int；执行器收尾时保存 |
| summary / remaining | string；执行器收尾时保存 |
| error | string 或 null；执行器收尾时保存 |

区间结构示例（时间与内容均为构造值，不是已完成实验的证据）：

~~~json
{
  "run_id": "run_0123456789abcdef0123456789abcdef",
  "task_id": "task_a1b2c3d4",
  "prompt": "只读检查 CodingAgent/messages.py 的文本提取行为。",
  "start_seconds": 2.125,
  "end_seconds": 4.375,
  "status": "completed",
  "turns_used": 2,
  "summary": "extract_text 提取列表内 type=text 的块。",
  "remaining": "请主 Agent 核对证据。",
  "error": null
}
~~~

check 没有统一完整字段集，只有 name 与 passed 总是存在：

| check 分支 | 字段 |
| --- | --- |
| 正常数据场景 | name:string、passed:bool、exit_code:int、expected:object、actual:解析出的 JSON 或 null、stderr:string |
| help / missing_input | name、passed、exit_code、stderr |
| 缺入口 / 捕获异常 | name、passed、detail:string |

以下正常场景期望值来自验收代码，actual / passed 是结构示例：

~~~json
{
  "name": "normal_levels",
  "passed": true,
  "exit_code": 0,
  "expected": {
    "total_valid": 4,
    "invalid_lines": 0,
    "counts": {"INFO": 2, "WARNING": 1, "ERROR": 1}
  },
  "actual": {
    "total_valid": 4,
    "invalid_lines": 0,
    "counts": {"INFO": 2, "WARNING": 1, "ERROR": 1}
  },
  "stderr": ""
}
~~~

total_valid / invalid_lines / counts 属于实验要求 Agent 编写的 log_report.py 的输出契约，**不属于 CodingAgent 的工具结果封装**。验收要求这些计数为真正的 int，不能用 bool 替代；还检查输入文件字节未改变。无入口的实际失败格式为：

~~~json
{"name":"entrypoint_exists","passed":false,"detail":"Missing log_report.py"}
~~~

控制台 concise 摘要删除 final_answer / subagent_intervals / acceptance，增加 subagent_runs（run_id、task_id、start_seconds、end_seconds、status），存在 acceptance 时再增加 checks（name、passed）。因此控制台摘要不能替代完整 result.json。

退出码规则：status 不是 agent_returned 或 task_passed 显式为 false 时返回 1，否则返回 0。审查模式没有自动设置 task_passed=true；review_score 仍为 pending_human_review。区间重叠只能证明记录到的调度行为，不能自动证明审查结论正确。当前记录没有统一 token / cost / usage 汇总字段。

### 13.2 历史 TODO 协议 [Internal，未注册]

| 项目 | 约定 |
| --- | --- |
| Purpose | 保存一组可整批替换的内存 TODO |
| Producer | TODOManager.update / run_todo_write |
| Consumer | TODOManager.render；历史调用方 |
| Source code | [TODOManager][todo] |
| 启用情况 | 两个入口仍保留 TODO 变量；当前 tools / handlers 均不注册 TODO 工具 |

update 接受 list，兼容 string：先 JSON 解析，再 ast.literal_eval；两次解析均失败返回 Invalid todos format。列表最多 20 条，最多 1 条 in_progress。每条读取 content、status，status 缺省为 pending，并 strip / lower；content 去除首尾空白后必须非空。成功后只保存这两个字段并替换整个 items。

~~~json
[
  {"content":"检查 CodingAgent/messages.py","status":"completed"},
  {"content":"核对 tool_use 与 tool_result 配对","status":"in_progress"},
  {"content":"更新协议文档","status":"pending"}
]
~~~

返回值为普通文本，不是 Task Board JSON：

~~~text
[x] 检查 CodingAgent/messages.py
[>] 核对 tool_use 与 tool_result 配对
[] 更新协议文档
Completed: 1/3
~~~

update 校验错误通常抛 ValueError；run_todo_write 捕获异常并返回 Error: <message>。TODO 没有 task_id / run_id、持久化文件或依赖关系，不能据此替代当前 Task Board 协议。

### 13.3 兼容与未启用接口

| 分支 | 源码证据 | 当前适用范围 |
| --- | --- | --- |
| MCP Tool Schema 的 input_schema 兼容 dict / model_dump | [_schema_to_dict](../CodingAgent/mcp/client.py#L22) | 适配 SDK 返回对象；不是增加远端 Schema 字段 |
| MCP image 的 mime_type / mimeType | [结果格式化](../CodingAgent/mcp/client.py#L173) | 二选一读取已有 SDK 属性 |
| Subagent evidence 从 bash 输出提取 exit_code | [subagent_evidence](../CodingAgent/subagent/results.py#L39) | 已有兼容分支；当前子 Agent 工具白名单没有 bash，正常路径不会产生这条证据 |
| 旧存档标记 [Earlier tool result saved at path] | [persisted_output_path](../CodingAgent/compact.py#L158) | 仍可读取；当前存档生产方使用 persisted-output 标签 |
| drain_background_tasks | [辅助函数](../CodingAgent/background.py#L164) | 独立排空辅助接口；主循环采用自己的等待 / 注入逻辑 |
| agent_loop 的 -> str 注解 | [同步][mloop]、[异步][aloop] | 实际正常 return 为 None；回答从 messages 历史读取 / 流式显示 |
| TODOManager / TODO | [历史 TODO][todo] | 存在实例和实现，但未开放为模型工具 |

### 13.4 本文的证据边界

本文依据 cb4e694 的生产与消费代码及测试断言，不依据第三方官方规范补字段。扫描覆盖两个入口、CodingAgent 全部模块、tests、experiments、仓库配置 / 说明和 skills。当前 167 项离线测试已通过、退出码 0；它们是离线验证记录，不是 Anthropic、真实 MCP 服务或新实验已联调成功的声明。

下面所有 End-to-End 响应与 ID 均为**结构示例**。文件分页示例使用当前仓库的真实行内容；MCP 的远端工具与发现对象明确使用合成 fixture；图片示例的数据占位符不能用于解码或请求。

## 14. End-to-End 数据流示例

这些示例展示没有触发压缩、权限被允许、Hook 未强制继续的成功路径；异常和异步收尾分支见相应协议章节。每次主请求都传入当前 system 字符串、完整 messages、当前 tools 和 max_tokens=16384；以下 JSON 只表示标明的 messages 或 SDK 属性投影，不伪装为完整 HTTP body。

### 14.1 普通文本回答

**Source code**：[Messages 请求][mreq]、[主循环][mloop]、[异步流式入口][areq]。

~~~text
CLI User → UserPromptSubmit → user message
→ Messages API → SDK text block
→ assistant message → 后台检查 → Stop Hook → Memory 收尾 → 终端回答
~~~

**① 用户输入与请求历史**

~~~json
[{"role":"user","content":"你好。"}]
~~~

UserPromptSubmit("你好。") 的返回值不参与 message 构造。SDK 调用使用第 2.1 节的参数；这次模型可以自行决定不调用工具。

**② 模型响应：SDK 对象的消费属性投影**

~~~json
{
  "stop_reason": "end_turn",
  "content": [
    {"type":"text","text":"你好，我可以协助阅读和修改当前项目。"}
  ]
}
~~~

**③ 主循环保存的完整历史：assistant SDK block 在此按字典展示**

~~~json
[
  {"role":"user","content":"你好。"},
  {
    "role":"assistant",
    "content":[{"type":"text","text":"你好，我可以协助阅读和修改当前项目。"}]
  }
]
~~~

没有 tool_use，且没有运行中或待注入的后台任务；Stop 返回 None，Memory 完成提取 / 必要合并后 agent_loop 返回 None。同步 CLI 从最后一条 assistant 消息提取并打印文本；异步 CLI 已经通过 text_stream 显示响应。最终回答不是 agent_loop 的字符串返回值。

### 14.2 内置工具：read_file + bash，同一响应两个调用

**Source code**：[ToolDispatcher][dispatcher]、[文件分页][files]、[ShellRunner.run_bash](../CodingAgent/tools/shell.py#L155)、[主循环][mloop]。

~~~text
User → Messages API
→ assistant.content 中的 read_file、bash 两个 tool_use
→ 对每个调用依次执行 PreToolUse / 权限
→ Dispatcher → Handler(**input) → PostToolUse → ToolContent
→ 同一个 user message 中的两个 tool_result
→ 下一次 Messages API → Final Response
~~~

**① 首次请求的 messages**

~~~json
[{"role":"user","content":"读取 CodingAgent/messages.py 第 4 到 6 行，再运行一个输出 protocol check 的 Python 命令。"}]
~~~

**② 模型响应：SDK 属性投影**

~~~json
{
  "stop_reason": "tool_use",
  "content": [
    {
      "type":"tool_use",
      "id":"call_e2e_read_1",
      "name":"read_file",
      "input":{"path":"CodingAgent/messages.py","offset":4,"limit":3}
    },
    {
      "type":"tool_use",
      "id":"call_e2e_bash_1",
      "name":"bash",
      "input":{"command":"python -c \"print('protocol check')\"","run_in_background":false}
    }
  ]
}
~~~

**③ Dispatcher 与 Handler 内部交付**

| 调用 | PreToolUse | Handler 实际调用 | Handler → ToolContent |
| --- | --- | --- | --- |
| call_e2e_read_1 | 观察同一个 SDK block；权限允许时无阻断字符串 | FileTools.run_read_file(path="CodingAgent/messages.py", offset=4, limit=3) | 分页普通文本 |
| call_e2e_bash_1 | 观察第二个 SDK block；权限允许时无阻断字符串 | ShellRunner.run_bash(command=..., run_in_background=False) | 普通文本 protocol check |

ShellRunner.run_bash_process 的内部返回为 Python 二元组 ("protocol check", 0)，format_bash_output 返回 "protocol check"；短输出没有全文存档，也没有把 exit_code 加入 tool_result。PostToolUse 接收 Handler 的原始输出；归一化后仍是 str。

**④ 下一次 Messages API 使用的完整 messages**

~~~json
[
  {
    "role": "user",
    "content": "读取 CodingAgent/messages.py 第 4 到 6 行，再运行一个输出 protocol check 的 Python 命令。"
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_e2e_read_1",
        "name": "read_file",
        "input": {
          "path": "CodingAgent/messages.py",
          "offset": 4,
          "limit": 3
        }
      },
      {
        "type": "tool_use",
        "id": "call_e2e_bash_1",
        "name": "bash",
        "input": {
          "command": "python -c \"print('protocol check')\"",
          "run_in_background": false
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_e2e_read_1",
        "content": "File: CodingAgent/messages.py\nLines 4-6 of 11\n4: def extract_text(content) -> str:\n5:     if not isinstance(content, list):\n6:         return str(content)\nNext offset: 7"
      },
      {
        "type": "tool_result",
        "tool_use_id": "call_e2e_bash_1",
        "content": "protocol check"
      }
    ]
  }
]
~~~

call_e2e_read_1 / call_e2e_bash_1 分别原样成为 tool_use_id。异步入口也按这个顺序等待两个工具，未自动并行执行同一响应的调用。

**⑤ 下一次模型响应与交付**

~~~json
{
  "stop_reason":"end_turn",
  "content":[
    {"type":"text","text":"第 4 到 6 行定义 extract_text，并将非列表输入转为字符串。命令输出 protocol check。"}
  ]
}
~~~

主循环再追加一条 assistant message；按 14.1 的结束流程返回。若某个 Handler 出错，④ 中只替换对应的 content 为实际 Error: ... / 拒绝文本，调用 ID 仍配对；不增加 is_error=true。

### 14.3 MCP：发现、Schema 与调用结果转换

**Source code**：[MCPClient][mclient]、[MCPManager][mmanager]、[Dispatcher][dispatcher]、[异步 MCPClient][amclient]。

本例沿用第 4 节的**合成 SDK fixture**：假设配置中有 local 服务，其远端提供 read_file(path)。这不是当前 mcp.json 的配置或已发现工具；description、Schema、返回文本也不宣称来自实际服务。

~~~text
MCP list_tools → page.tools / next_cursor → MCPClient.tools
→ MCPManager 前缀 / input_schema → Anthropic tools + Handler
→ Messages API tool_use → Dispatcher → client.call_tool(raw_name, args)
→ SDK result → MCPClient 格式化 → ToolContent
→ Anthropic tool_result → 下一次 Messages API → Final Response
~~~

**① 发现：session.list_tools(cursor=None) 返回对象的属性投影**

~~~json
{
  "tools": [
    {
      "name": "read_file",
      "description": "Read a numbered page of a UTF-8 file.",
      "input_schema": {
        "type": "object",
        "properties": {
          "path": {
            "type": "string"
          }
        },
        "required": [
          "path"
        ]
      }
    }
  ],
  "next_cursor": null
}
~~~

next_cursor=null，所以不请求下一页。MCPClient.tools 保存该 tools 数组内的 name / description / input_schema；它不保存 next_cursor。

**② MCPManager 产出的 Anthropic tools 元素**

~~~json
{
  "name": "mcp__local__read_file",
  "description": "Read a numbered page of a UTF-8 file.",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string"
      }
    },
    "required": [
      "path"
    ]
  }
}
~~~

同期注册的内部 Handler 等价于：

~~~python
handlers["mcp__local__read_file"] = (
    lambda *, client=server, tool="read_file", **kwargs:
    client.call_tool(tool, kwargs)
)
~~~

名字带前缀，Schema 保留 fixture 原字段。若先调用 connect_mcp({"name":"local"})，成功内容为普通文本：

~~~text
Connected to MCP server 'local'. Discovered 1 tools: read_file
~~~

其 tool_result 返回后，**下一轮** assemble_tool_pool 才把②加入请求 tools；本例以下从已连接、已组装工具池的状态开始。

**③ 模型响应：SDK 属性投影**

~~~json
{
  "stop_reason":"tool_use",
  "content":[
    {
      "type":"tool_use",
      "id":"call_e2e_mcp_1",
      "name":"mcp__local__read_file",
      "input":{"path":"README.md"}
    }
  ]
}
~~~

PreToolUse 按前缀名查权限；本例已获允许。Handler 去除路由前缀的方式是使用绑定的 raw_name；不在运行时猜测拆分 input。调用最终成为：

~~~python
await session.call_tool("read_file", {"path": "README.md"})
~~~

**④ MCP call_tool result：SDK 对象的属性投影**

~~~json
{
  "is_error": false,
  "content": [
    {
      "type": "text",
      "text": "# mini-coding-agent"
    },
    {
      "type": "text",
      "text": "A small coding agent."
    }
  ],
  "structured_content": null
}
~~~

_format_mcp_tool_result 读取 is_error=false，按顺序收集两个 text，再以换行连接并 strip；返回 ToolContent **str**：

~~~text
# mini-coding-agent
A small coding agent.
~~~

structured_content=null 没有被使用。若④改为 content=[]、structured_content={"path":"README.md","lines":2}，得到的是 JSON 字符串 '{"path": "README.md", "lines": 2}'；若存在图片则走第 4.4 / 8 节的列表路径。

**⑤ 下一次 Messages API 的完整 messages**

~~~json
[
  {
    "role": "user",
    "content": "使用已连接的 local MCP 读取 README.md，概述返回内容。"
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_e2e_mcp_1",
        "name": "mcp__local__read_file",
        "input": {
          "path": "README.md"
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_e2e_mcp_1",
        "content": "# mini-coding-agent\nA small coding agent."
      }
    ]
  }
]
~~~

**⑥ 最终模型响应**

~~~json
{
  "stop_reason":"end_turn",
  "content":[{"type":"text","text":"返回内容将该项目描述为一个小型 coding agent。"}]
}
~~~

该响应作为 assistant message 追加并显示。若远端 result.is_error=true，⑤的 content 变成以 MCP error: 开头的字符串，或含错误 text 的图片列表；Anthropic tool_result 仍只包含 type / tool_use_id / content。

### 14.4 Subagent：看板认领 → 启动回执 → 最终通知 → 验收

**Source code**：[TaskBoard][taskboard]、[SubagentTools][subtools]、[Manager][submanager]、[执行器][subexec]、[最终 JSON](../CodingAgent/subagent/results.py#L137)、[主循环][mloop]。

~~~text
主 Agent create_task → task_<id>（pending）
→ claim_task → in_progress / owner=agent
→ task(task_id, prompt) → started receipt / run_id
                           └→ 后台线程 → 独立 messages / 只读工具 → SubagentState 终态
→ subagent_status(run_id) → 快照，不领取通知
→ Manager 发布结果 → collect 一次性领取
→ user text "<subagent_result>...JSON...</subagent_result>"
→ 下一次主 Messages 请求 → 主 Agent 核对 evidence
→ complete_task(task_id) → Task Board completed → 最终回答
~~~

本例假设查询发生时子线程仍在执行，随后在下一轮请求前发布结果。因此 running 查询回执与 completed 最终通知可以先后出现在同一个 user message；它们反映不同时间点。查询不能阻止自动通知。

**① 启动前的看板持久化对象**

create_task 生成 ID 后，claim_task 保存的 tasks/task_a1b2c3d4.json：

~~~json
{
  "id":"task_a1b2c3d4",
  "subject":"检查文本提取",
  "description":"核对 extract_text 的非列表输入行为。",
  "status":"in_progress",
  "owner":"agent",
  "blockedBy":[]
}
~~~

task 读取这个对象并校验认领状态；不重新创建 Task，也不把 run_id 写进看板文件。

**② task Handler 的即时返回：JSON 字符串所包含的对象**

~~~json
{
  "status": "started",
  "task_id": "task_a1b2c3d4",
  "run_id": "run_0123456789abcdef0123456789abcdef",
  "message": "子 Agent 已启动。最终结果将自动作为 subagent_result 通知送达。"
}
~~~

这个对象被编码为 **str**，作为 call_task_review_1 的 tool_result.content 返回。主循环通过 receipt.get("status") / receipt.get("run_id") 登记本轮启动的运行。子线程与主循环同时存活；started 仅表示成功启动。

**③ 子 Agent 的完整独立工作历史**

~~~json
[
  {
    "role": "user",
    "content": "只读检查 CodingAgent/messages.py 第 4 到 6 行，说明 extract_text 对非列表输入的处理。返回 summary 和 remaining 两个字符串字段。"
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_sub_read_1",
        "name": "read_file",
        "input": {
          "path": "CodingAgent/messages.py",
          "offset": 4,
          "limit": 3
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_sub_read_1",
        "content": "File: CodingAgent/messages.py\nLines 4-6 of 11\n4: def extract_text(content) -> str:\n5:     if not isinstance(content, list):\n6:         return str(content)\nNext offset: 7"
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "text",
        "text": "{\"summary\":\"第 4 到 6 行表明 extract_text 对非列表输入返回 str(content)。\",\"remaining\":\"\"}"
      }
    ]
  }
]
~~~

第一次子请求携带只读 tools；call_sub_read_1 由 execute_subagent_tool 执行。第二次子请求收到 end_turn + text；text 内是模型按提示返回的 JSON 字符串，apply_subagent_summary 解析出 summary / remaining。此成功路径没有额外的 phase=summary 收尾模型调用。

**④ 子线程发布的完整最终 JSON**

output_tokens 的 48 / 64 是构造值，不是本次联调的计数；其余状态与预览规则按生产代码生成。

~~~json
{
  "run_id": "run_0123456789abcdef0123456789abcdef",
  "task_id": "task_a1b2c3d4",
  "status": "completed",
  "summary": "第 4 到 6 行表明 extract_text 对非列表输入返回 str(content)。",
  "remaining": "",
  "error": null,
  "warnings": [],
  "turns_used": 2,
  "tool_call_count": 1,
  "response_log": [
    {
      "phase": "work",
      "turn": 1,
      "stop_reason": "tool_use",
      "content_types": [
        "tool_use"
      ],
      "max_tokens": 16384,
      "output_tokens": 48
    },
    {
      "phase": "work",
      "turn": 2,
      "stop_reason": "end_turn",
      "content_types": [
        "text"
      ],
      "max_tokens": 16384,
      "output_tokens": 64
    }
  ],
  "evidence": [
    {
      "tool_use_id": "call_sub_read_1",
      "tool": "read_file",
      "arguments": {
        "text": "{\"path\": \"CodingAgent/messages.py\", \"offset\": 4, \"limit\": 3}",
        "truncated": false
      },
      "output": {
        "text": "File: CodingAgent/messages.py\nLines 4-6 of 11\n4: def extract_text(content) -> str:\n5:     if not isinstance(content, list):\n6:         return str(content)\nNext offset: 7",
        "truncated": false
      }
    }
  ]
}
~~~

SubagentManager 将终态对象保存于 results，并把 run_id 加入 ready；此时释放并发名额。collect 清空 ready，但保留 results，所以后续 subagent_status 仍可查询④。

**⑤ 主 Agent 从用户输入到最终回答的完整 messages**

以下 assistant block 是 SDK 对象的字典展示；JSON 字符串嵌套在 content 中，转义的换行是 JSON 表示法。模型的 tool_use 轮次使用 stop_reason=tool_use，最后一个文本响应使用 end_turn。

~~~json
[
  {
    "role": "user",
    "content": "委派只读子任务，核对 extract_text 对非列表输入的行为，验收后完成看板任务。"
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_create_review_1",
        "name": "create_task",
        "input": {
          "subject": "检查文本提取",
          "description": "核对 extract_text 的非列表输入行为。"
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_create_review_1",
        "content": "Created task task_a1b2c3d4: (检查文本提取)"
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_claim_review_1",
        "name": "claim_task",
        "input": {
          "task_id": "task_a1b2c3d4"
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_claim_review_1",
        "content": "Claimed task_a1b2c3d4 (检查文本提取)"
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_task_review_1",
        "name": "task",
        "input": {
          "task_id": "task_a1b2c3d4",
          "prompt": "只读检查 CodingAgent/messages.py 第 4 到 6 行，说明 extract_text 对非列表输入的处理。返回 summary 和 remaining 两个字符串字段。"
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_task_review_1",
        "content": "{\"status\":\"started\",\"task_id\":\"task_a1b2c3d4\",\"run_id\":\"run_0123456789abcdef0123456789abcdef\",\"message\":\"子 Agent 已启动。最终结果将自动作为 subagent_result 通知送达。\"}"
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_status_review_1",
        "name": "subagent_status",
        "input": {
          "run_id": "run_0123456789abcdef0123456789abcdef"
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_status_review_1",
        "content": "{\n  \"run_id\": \"run_0123456789abcdef0123456789abcdef\",\n  \"status\": \"running\",\n  \"message\": \"子 Agent 正在执行或收尾，最终结果尚未发布。\"\n}"
      },
      {
        "type": "text",
        "text": "<subagent_result>\n{\n  \"run_id\": \"run_0123456789abcdef0123456789abcdef\",\n  \"task_id\": \"task_a1b2c3d4\",\n  \"status\": \"completed\",\n  \"summary\": \"第 4 到 6 行表明 extract_text 对非列表输入返回 str(content)。\",\n  \"remaining\": \"\",\n  \"error\": null,\n  \"warnings\": [],\n  \"turns_used\": 2,\n  \"tool_call_count\": 1,\n  \"response_log\": [\n    {\n      \"phase\": \"work\",\n      \"turn\": 1,\n      \"stop_reason\": \"tool_use\",\n      \"content_types\": [\n        \"tool_use\"\n      ],\n      \"max_tokens\": 16384,\n      \"output_tokens\": 48\n    },\n    {\n      \"phase\": \"work\",\n      \"turn\": 2,\n      \"stop_reason\": \"end_turn\",\n      \"content_types\": [\n        \"text\"\n      ],\n      \"max_tokens\": 16384,\n      \"output_tokens\": 64\n    }\n  ],\n  \"evidence\": [\n    {\n      \"tool_use_id\": \"call_sub_read_1\",\n      \"tool\": \"read_file\",\n      \"arguments\": {\n        \"text\": \"{\\\"path\\\": \\\"CodingAgent/messages.py\\\", \\\"offset\\\": 4, \\\"limit\\\": 3}\",\n        \"truncated\": false\n      },\n      \"output\": {\n        \"text\": \"File: CodingAgent/messages.py\\nLines 4-6 of 11\\n4: def extract_text(content) -> str:\\n5:     if not isinstance(content, list):\\n6:         return str(content)\\nNext offset: 7\",\n        \"truncated\": false\n      }\n    }\n  ]\n}\n</subagent_result>"
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "call_complete_review_1",
        "name": "complete_task",
        "input": {
          "task_id": "task_a1b2c3d4"
        }
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "call_complete_review_1",
        "content": "Completed task_a1b2c3d4 (检查文本提取)"
      }
    ]
  },
  {
    "role": "assistant",
    "content": [
      {
        "type": "text",
        "text": "已核对返回的代码证据：extract_text 对非列表输入返回 str(content)。看板任务已完成。"
      }
    ]
  }
]
~~~

这条历史明确包含两种交付：

| 位置 | 内容形态 | 关联 |
| --- | --- | --- |
| task 的 user 回填 | tool_result，content 为 started JSON 字符串 | 原 tool_use_id=call_task_review_1 |
| 状态查询的回填 | tool_result，content 为 running JSON 字符串 | tool_use_id=call_status_review_1 |
| 最终交接 | text，text 为 subagent_result 标签内的完整 JSON | 通过 run_id / task_id 关联；没有 tool_use_id |
| 主 Agent 验收后 | complete_task 的 tool_result，content 为普通文本 | 修改 Task Board；子线程没有自行完成看板任务 |

主模型在收到完整 evidence 后判断该只读检查已达到要求，再请求 complete_task；这一步是模型决策与主 Agent 的验收职责，代码没有自动证明 summary 正确。若主模型先返回最终文本但后台尚在运行，主循环会等待、注入最终通知并继续请求模型，不提前结束本轮用户任务。

**⑥ 成功流的替代分支**

| 条件 | 对⑤的实际影响 |
| --- | --- |
| task 启动失败 | call_task_review_1 的 content 为 not_started JSON 字符串、run_id=null；没有本次运行的自动最终通知 |
| 查询未知 run_id | 查询 content 为 not_found JSON 字符串；不创建运行 |
| 查询后取消仍活跃的运行 | subagent_cancel 返回 cancelling JSON 字符串；最终交接 status=cancelled，见第 6.3 / 6.4 / 6.5 节 |
| 工作超时 / 预算耗尽 / 执行失败 | 最终标签内保留 timed_out / budget_exhausted / failed 和实际 error / warnings / evidence；主 Agent 决定下一步 |
| 已领取自动通知 | 后续 collect 不再注入同一运行；status 查询仍可读取 retained result |

这些分支均不会自动把 Task Board 改成 failed / cancelled / completed。task_id 表示待验收的工作，run_id 表示一次执行，二者的生命周期独立。
