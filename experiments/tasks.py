"""固定题目和初始样例；数据不包含模型答案。"""

BUILD_TASK = """在当前工作目录实现一个可运行的 Python 标准库命令行工具 log_report.py，用于统计 JSONL 日志。接口：python log_report.py --input 输入路径 --output 报告路径。输入为 UTF-8，每行一条 JSON；空白行忽略，不计入有效或无效记录。有效记录必须是对象，level 必须是区分大小写的 INFO、WARNING、ERROR 之一，message 必须是字符串（允许空字符串）；其他字段忽略。损坏的 JSON、非对象、缺字段、错误类型或未知级别计为无效记录，不能导致后续有效记录被跳过。输出 UTF-8 JSON 对象，必须恰好包含 total_valid、invalid_lines、counts；counts 恰好包含 INFO、WARNING、ERROR，所有计数为整数，未出现的级别为 0。空输入输出全部为 0。输出目录不存在时创建。输入不存在时以非零退出码结束；--help 正常显示帮助并以 0 退出。不得覆盖输入文件。保留已有 TASK.md、sample.jsonl、mcp.json 和 skills 目录内容不变。sample.jsonl 是公开样例。请使用任务看板管理工作，完成实现并自行编写、运行开发测试，最终报告修改文件、测试命令、实际退出码及未完成事项。只在当前工作目录开发，不读取外层 experiments 目录中的程序或其他运行结果，不连接 MCP，不安装依赖。"""

SAMPLE = '{"level":"INFO","message":"started"}\n\nnot-json\n{"level":"ERROR","message":"failed"}\n'

SPEC = """# 日志处理库行为规范

所有模块仅使用标准库。以下是必须满足的外部行为，不要求改进风格或增加新功能。

parser.parse_lines(lines) 返回 (records, invalid_count)。空白行忽略；损坏 JSON、
非对象、缺字段或字段无效计入 invalid_count，继续处理后续行。
有效记录要求 level 属于 INFO/WARNING/ERROR，message 是字符串。

config.resolve_config(defaults, file_config, cli_config) 合并三层配置。
优先级为 CLI > 文件 > 默认。CLI 中 None 表示未指定；0、False、空字符串都是显式值，必须保留。
不得修改三个输入字典。

filters.filter_records(records, min_level='INFO', max_items=None) 按严重程度 INFO < WARNING < ERROR 过滤。
min_level 是包含边界的最低级别。保持原记录顺序，最多返回 max_items 项；None 表示不限，0 表示返回空列表。
调用者仅传入合法级别、合法记录和非负整数/None，不要求校验非法调用参数。

writer.write_csv(records, path) 写出可被标准库 csv.reader 正确读取的 UTF-8 CSV。
首行必须为 level,message；后续每行两列，与记录对应。message 可能包含逗号、引号和换行。
输出父目录已由调用者创建。空 records 也要输出表头。

审查只判断是否违反以上规范，报告文件、函数、触发例子和影响。
不要要求每个文件都必须有问题，不把未要求的特性当作缺陷。
"""

REVIEW_FILES = {
    "SPEC.md": SPEC,
    "parser.py": '''import json


def parse_lines(lines):
    records = []
    invalid_count = 0
    for line in lines:
        if not line.strip():
            invalid_count += 1
            continue
        item = json.loads(line)
        if (not isinstance(item, dict)
                or item.get("level") not in ("INFO", "WARNING", "ERROR")
                or not isinstance(item.get("message"), str)):
            invalid_count += 1
            continue
        records.append(item)
    return records, invalid_count
''',
    "config.py": '''def resolve_config(defaults, file_config, cli_config):
    result = dict(defaults)
    result.update(file_config)
    for key, value in cli_config.items():
        if value:
            result[key] = value
    return result
''',
    "filters.py": '''LEVELS = {"INFO": 0, "WARNING": 1, "ERROR": 2}


def filter_records(records, min_level="INFO", max_items=None):
    selected = [item for item in records
                if LEVELS[item["level"]] > LEVELS[min_level]]
    if max_items:
        selected = selected[:max_items]
    return selected
''',
    "writer.py": '''def write_csv(records, path):
    with open(path, "w", encoding="utf-8", newline="") as stream:
        stream.write("level,message\\n")
        for item in records:
            stream.write(item["level"] + "," + item["message"] + "\\n")
''',
}

REVIEW_TASKS = [
    ("R1", "parser.py", "核对输入解析与无效记录处理"),
    ("R2", "config.py", "核对配置覆盖规则与输入字典是否被修改"),
    ("R3", "filters.py", "核对过滤边界、返回数量与顺序"),
    ("R4", "writer.py", "核对 CSV 格式、特殊字符与空输入"),
]


def review_prompt(mode):
    assignments = "；".join(f"{name}：{path}，{goal}" for name, path, goal in REVIEW_TASKS)
    scheduling = (
        "四份任务必须依次启动：收到并处理前一个子 Agent 的最终交接后，才启动下一个；任何时候最多一个运行中的子 Agent。"
        if mode == "serial" else
        "四份任务相互独立，请分别创建并认领后尽快连续启动四个子 Agent，不要等前一位完成再启动后一位；收齐后汇总。"
    )
    return (
        "请审查当前日志处理库，严格以 SPEC.md 为依据。固定拆成四份只读子任务：" + assignments + "。"
        + scheduling +
        "四份子任务都要要求先阅读 SPEC.md，然后检查分配文件，必要时阅读关联模块。"
        "每个子 Agent 使用现有 summary/remaining JSON 交接格式，把发现写在 summary 内。"
        "主 Agent 可以维护任务看板，但禁止修改或新建其他文件，禁止 Shell、MCP、安装依赖或执行代码。"
        "只读取当前工作区，不读取外层 experiments 目录或其他运行结果。"
        "每份任务只启动一次，失败或超时如实交接，不重启替代。没有其他工作时停止请求工具，让宿主等待自动结果，避免轮询。"
        "最终由主 Agent 合并去重，输出逐项问题清单：文件、函数、违反的规范、最小触发例子、影响；另列未确认问题。"
        "不要根据猜测补满问题数量，不把风格建议当作缺陷。最终同时列出四份任务各自的 task_id 和 run_id。"
    )
