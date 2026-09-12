# Coding Agent

一个 Python 命令行 Coding Agent：通过模型自主选择工具，完成文件阅读、代码修改、命令执行和结果验证。主 Agent 负责开发与整合，只读子 Agent 负责分析、调查和审查。

项目从单文件实现逐步拆分而来。`main.py` 保留对象组装、主 Agent 循环和退出清理，各功能模块放在 `CodingAgent/`。历史 TODO 实现仍保留，但当前任务管理使用 Task 看板。

## 功能与执行流程

1. 接收用户输入，通过 Hooks 记录事件，加载相关记忆。
2. 整理上下文，构造系统提示词与当轮工具列表，调用模型。
3. 根据模型请求执行工具：先经过权限检查，再执行本地工具、后台 Shell 或 MCP 工具。
4. 将工具结果、后台命令结果和子 Agent 交接结果送回主循环，继续推理。
5. 主 Agent 根据实际结果收尾；退出时请求子 Agent 停止、清理 Shell 进程并关闭 MCP 连接。

| 模块 | 当前实现 |
| --- | --- |
| 文件工具 | 文件读取与分页、写入、文本替换、路径匹配 |
| Shell 与后台执行 | 同步命令、后台线程执行、结果队列与主循环回收 |
| Task 看板 | 创建、更新、查看、认领、完成；任务记录存储为文件 |
| Subagent | 后台线程运行，只允许读取文件、匹配路径和加载 Skill；支持状态查询、预算限制、取消与结果交接 |
| Skill | 从工作目录的 `skills/*/SKILL.md` 加载清单，模型按需读取全文 |
| MCP | 读取服务器配置，按需连接 stdio 或 HTTP 服务，发现工具并动态加入工具池 |
| Memory | 本地 Markdown 记忆、索引、召回、提取与合并 |
| Compact | 上下文整理、超长工具结果归档、主动压缩与上下文超限后的重试 |
| Hooks 与权限 | 用户输入、工具执行前后、结束事件；拒绝规则、越界访问与部分操作确认 |
| TODO | `CodingAgent/todo.py` 保留历史实现，未注册到当前工具列表 |

子 Agent 的默认并发上限是 **4**，并不代表每个任务都必须启动四个。先创建并认领 Task，再使用 `task` 工具启动子 Agent。`task_id` 标识看板任务，`run_id` 标识一次运行；启动回执不代表任务完成，最终交接通过 `subagent_result` 返回。

Windows 下，名为 `bash` 的工具实际通过 `subprocess.Popen(shell=True)` 执行系统 Shell 命令，通常采用 `cmd.exe` 语法；需要 PowerShell 时应显式调用它。子 Agent 的只读限制在工具执行层实现，但主 Agent 的 Shell 拥有当前用户权限，独立工作目录不等同于操作系统沙箱。

## 目录结构

```text
main.py                       # 入口、依赖组装、主循环、统一退出
CodingAgent/
  config.py                   # 配置与基于工作目录的派生路径
  prompts.py                  # 主 Agent / 子 Agent 提示词
  messages.py                 # 消息文本提取
  skill_loader.py             # Skill 清单扫描与加载
  taskboard.py                # Task 存储与看板操作
  todo.py                     # 历史 TODO
  background.py               # 后台 Shell 任务与结果注入
  compact.py                  # 上下文压缩与结果归档
  hooks.py                    # 事件注册与默认处理
  permissions.py              # 工具权限检查
  memory/                     # 记忆存储与管理
  mcp/                        # 配置、异步桥接、客户端、工具管理
  subagent/                   # 状态、执行器、管理器、结果交接
  tools/                      # 文件、Shell、工具 schema、适配器与分发
skills/
  count_lines/SKILL.md
  python_file_summary/SKILL.md
```

## 运行环境

本机已验证模块导入的环境为 Python **3.11.9**，直接第三方依赖如下。版本取自本机环境，尚未验证全新环境安装，也未提供完整依赖锁文件。

| 包 | 本机版本 |
| --- | --- |
| anthropic | 1.2.0 |
| python-dotenv | 1.2.3 |
| PyYAML | 6.0.3 |
| mcp | 2.1.1 |

新环境可按这些版本安装：

```powershell
Set-Location D:\Agent
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install anthropic==1.2.0 python-dotenv==1.2.3 PyYAML==6.0.3 mcp==2.1.1
```

已有 `.venv` 可直接复用。当前 MCP 客户端使用 `from mcp import Client, StdioServerParameters`，不能仅凭包名相同就认为任意 SDK 版本都兼容。

## 模型配置与启动

在源码根目录创建 `.env`，根据实际服务填写：

```dotenv
ANTHROPIC_API_KEY=replace-with-your-key
ANTHROPIC_MODEL=replace-with-your-model-id
# 使用兼容服务时配置；直连默认服务可以省略这一项。
# ANTHROPIC_BASE_URL=https://your-provider.example
```

模型通过 Anthropic SDK 的 Messages 接口调用，服务需要兼容消息和工具调用格式。模型名称来自环境变量，当前没有可靠的硬编码默认值。已有进程环境变量优先于 `.env`。

```powershell
Set-Location D:\Agent
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -B -u .\main.py
```

看到 `s01 >>` 后输入任务。当前入口按单行读取，长提示词应粘贴成一行。输入 `exit`、`q`、空行或按 Ctrl+C 可退出；最后会显示 `[shutdown]` 清理日志。

直接这样启动时，工作目录就是源码目录。进行生成代码的验收时，建议使用下面的独立目录方式。

## 配置、Skill 与运行数据放在哪里

`load_config()` 使用 **当前工作目录 `Path.cwd()`**，而不是 `main.py` 所在目录。下列内容都相对于当前工作目录：

| 路径 | 用途 |
| --- | --- |
| `skills/*/SKILL.md` | Skill 清单与指令 |
| `mcp.json` | MCP 服务器配置 |
| `tasks/` | Task 看板记录 |
| `.memory/MEMORY.md`、`.memory/` | 记忆索引和文档 |
| `.transcripts/` | 上下文压缩相关归档 |
| `.task_outputs/` | 子 Agent 输出、工具结果归档等 |

源码仓库的 `.gitignore` 已排除 `.env`、本机 `mcp.json`、虚拟环境及运行数据。

不测试 MCP 时，工作目录中的 `mcp.json` 可以是：

```json
{"mcpServers": {}}
```

stdio 配置示例，服务脚本需要另行提供：

```json
{
  "mcpServers": {
    "local-service": {
      "command": "D:/Agent/.venv/Scripts/python.exe",
      "args": ["D:/path/to/server.py"]
    }
  }
}
```

HTTP 服务使用 `url`，可选 `headers`；单个服务不能同时设置 `command` 和 `url`。连接是按需发生的，仅启动 Agent 不代表已验证 MCP 服务。

Skill 由 YAML frontmatter 和正文组成，例如：

```markdown
---
name: python-file-summary
description: 当用户要求分析或概览 Python 文件时使用这个 Skill。
---
读取指定文件，列出 imports、classes、functions，并概括其作用。
```

目录名与 Skill 名称可以不同，调用 `load_skill` 时使用清单中的 `name`。

## 独立目录验收

本次准备的本机目录是 `D:\Agent-acceptance-20260912`：

```text
D:\Agent-acceptance-20260912/
  start.ps1              # 启动器
  TASK.txt               # 可直接粘贴的单行验收任务
  README.md              # 本次运行与复核说明
  runtime/
    main.py              # 源码快照
    CodingAgent/         # 模块快照
  skills/                # 本次复制的 Skill
  mcp.json               # 空配置，本轮不连接 MCP
  log_analyzer/          # 待 Agent 生成
```

测试目录不属于本 Git 仓库。启动器从该目录运行源码快照，复用 `D:\Agent\.venv`，显式加载 `D:\Agent\.env`，不复制密钥。源码快照不会随原仓库修改自动更新。

```powershell
Set-Location D:\Agent-acceptance-20260912
Get-Content .\TASK.txt -Raw -Encoding UTF8 | Set-Clipboard
.\start.ps1
```

出现 `s01 >>` 后粘贴并回车。若本机执行策略阻止脚本，可只对本次进程使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\Agent-acceptance-20260912\start.ps1
```

任务要求 Agent 开发标准库 JSONL 日志分析工具、创建至少八个测试、通过两个只读子 Agent 审查，并在后台执行测试后收集结果。复核命令：

```powershell
Set-Location D:\Agent-acceptance-20260912
D:\Agent\.venv\Scripts\python.exe -B -m unittest discover -s .\log_analyzer -p 'test_*.py' -v
$LASTEXITCODE
D:\Agent\.venv\Scripts\python.exe -B .\log_analyzer\analyzer.py --input .\log_analyzer\sample.jsonl --output .\log_analyzer\summary.json
$LASTEXITCODE
Get-Content .\log_analyzer\summary.json
```

预期至少八个测试通过，两个命令退出码均为 `0`。示例输出为 `total_valid=4`、`invalid_lines=2`，`by_level` 中 INFO=2、WARNING=1、ERROR=1。

验收时分别记录：任务看板状态、两个子 Agent 的 `task_id`/`run_id`/最终交接、Skill 实际调用、后台命令结果、测试数量与退出码。仅有“已启动”或自然语言“已完成”不能替代结果证据。未触发的能力标记为未覆盖；重试成功应说明重试过程。

本轮不覆盖 MCP 真实连接、Memory 跨会话持久化、Compact 触发、权限拒绝和取消行为，这些需要专项验证。当前文档给出的是验收方案，不代表真实模型已完成该任务。

在其他机器复现时，新建一个独立目录，把源码复制到其 `runtime/`、把 `skills/` 复制到目录根部，再用该机器的解释器加载模型环境、将 `runtime/` 加入 Python 模块搜索路径并运行其中的 `main.py`；工作目录保持为独立目录。不要复制已有 Task、记忆或运行输出，以免影响新一轮验收。
