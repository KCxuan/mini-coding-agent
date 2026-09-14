# Coding Agent 手动运行测试指南

这些脚本测试 Agent 自身的程序逻辑，由你手动启动。
使用 Python 标准库 unittest，不需要另外安装 pytest，但仍需要项目原有依赖。

测试数量不代表通过数量；请在目标环境实际运行后记录通过数、失败数和耗时。

## 1. 先运行文件工具测试

在项目根目录打开 PowerShell。该目录应同时包含 main.py、CodingAgent 和 tests。

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_files -v -b
```

使用其他环境时，把解释器路径替换为安装了项目依赖的 Python 3.11+ 解释器。
不要通过运行 main.py 来启动测试。

- `-v`：列出每条测试名称和结果。
- `-b`：通过时隐藏业务代码的打印，失败时附上输出。
- `-B`：不生成字节码缓存。
- `-X utf8`：使用 UTF-8，减少 Windows 中文输出乱码。

## 2. 运行全部测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -t . -v -b
$LASTEXITCODE
```

`-s tests` 指定测试目录，`-t .` 把当前项目根目录作为导入起点。
请从根目录执行，不要先进入 tests。

最后一行应紧接测试命令执行，用来读取退出码：0 表示成功，非 0 表示失败或错误。

当前静态统计为 **88 个 test_ 方法**。部分方法通过 subTest 检查多个输入，
这些输入不另算独立测试方法。没有 skip 或 expectedFailure 标记。
先确认实际发现了测试，再看结尾的 OK / FAILED；Ran 0 tests 不算验收成功。

以下只是输出示意，不是实际结果：

```text
test_read_middle_page (...) ... ok
...
Ran N tests in T seconds
OK
```

出现 FAIL，查看断言的期望值和实际值；出现 ERROR，查看 traceback。
失败可能来自业务缺陷、测试假设、依赖或环境，需要分析后判断。
不要为了全绿直接删除断言或把失败标记为跳过。

## 3. 按模块运行

| 模块 | 方法数 | 验证内容 |
| --- | ---: | --- |
| tests.test_files | 11 | 分页、EOF、非法参数、写入与编辑结果、越界路径、glob |
| tests.test_taskboard | 10 | 依赖阻塞与解锁、循环依赖、认领、完成、持久化重载 |
| tests.test_subagent_executor | 14 | 只读限制、工具证据、取消、轮数耗尽、超时、截断与总结失败 |
| tests.test_subagent_manager | 9 | 四个名额、同时抢占、结果只收一次、取消隔离、线程失败和关闭 |
| tests.test_background_and_loop | 11 | 后台结果、工具分发、晚到结果、主循环重入和压缩重试 |
| tests.test_compact | 9 | 原文归档、超长结果、路径限制、工具消息配对、摘要和最近结果 |
| tests.test_memory | 11 | 索引、有效性、去重、临时记忆拒绝、召回降级、合并恢复 |
| tests.test_mcp | 13 | 配置、工具名冲突、路由绑定、权限策略、连接故障与清理 |

只运行并发管理器：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_subagent_manager -v -b
```

只运行一个方法：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest tests.test_files.FileToolsTests.test_read_middle_page -v -b
```

如果出现 ModuleNotFoundError，先确认使用的解释器和项目依赖。
如果无法导入 MCP SDK 的 Client，请核对 requirements.txt 中的 SDK 版本。
这属于运行环境问题，不能写成相关功能已经通过。

## 4. 隔离方式和覆盖边界

- 每个测试使用系统临时目录，文件、看板、记忆和归档都写入临时工作区，结束后清理。
  越界测试的外部文件也在这一临时目录中，不涉及真实个人文件。
- 模型由 ScriptedClient 替代，只返回预设响应或异常，不读取 .env，不调用真实模型。
- MCP 服务和 Shell 由替身代替，不启动外部服务或运行 Shell 命令。
  测试期间另拦截常用 socket 连接入口和 subprocess.Popen，发现误调用就报错。
  这是防止测试误用外部依赖的措施，不是系统级安全沙箱。
- 并发测试创建真实 Python 线程，通过事件控制执行结束时机，清理时等待线程退出。
  等待有上限；机器负载过高也可能导致超时，需结合错误分析。
- 主循环测试从当前 main.py 提取 agent_loop 和 inject_async_results 的原函数 AST，
  在替身依赖下执行原函数体，避免入口的客户端、信号处理和退出钩子初始化。
  它验证循环逻辑，不验证 CLI 启动、入口依赖组装和真实 SDK 协议兼容性。
- 压缩测试使用已配对的工具消息检查行为，不能据此声称任意上下文均可正确压缩。
- 真实 Shell 进程树终止、MCP 网络传输、真实模型任务完成率、真实 Token 成本、
  跨操作系统兼容性不在这套测试范围内。
- 后台长输出当前只通知前 500 字符。本套后台测试检查短结果和投递时机，
  不证明完整长输出已保留。后续可针对该行为另写回归测试并修复实现。

## 5. 学习顺序和结果记录

先读 test_files.py，每个方法大致分为：准备数据 → 调用真实方法 → 断言结果。
再读任务看板，最后看 helpers.py 中的模型替身以及子 Agent 测试。
文件顶部和关键步骤有中文说明。

运行后记录：代码版本、Python 与依赖版本、完整命令、日期、测试数、
通过数、失败数、错误数、跳过数、耗时和退出码。
不同测试方法中的输入变体不要重复计数。

需要把输出保存到文件时，可以执行：

```powershell
New-Item -ItemType Directory -Force .\tests\.results | Out-Null
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -t . -v -b 2>&1 | Tee-Object -FilePath .\tests\.results\latest.txt
$testExitCode = $LASTEXITCODE
"Exit code: $testExitCode" | Tee-Object -FilePath .\tests\.results\latest.txt -Append
```

日志目录已在 tests/.gitignore 中忽略。latest.txt 每次会覆盖，
保留多次实验记录时请更换文件名。分享失败信息时保留 traceback 和命令。

完成离线测试后，再设计单独的真实任务评测集，统计编程任务成功率和模型用量。
不要把“88 条测试通过”写成“88 个编程任务完成”。