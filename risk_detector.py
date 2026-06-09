"""风险代码识别 —— 规则引擎（多阶段验证的第一阶段）。

对 PR Diff 的新增行做静态规则扫描，输出五个维度的候选风险：
    Security        SQL注入 / XSS / SSRF / 路径遍历 / 硬编码密码 / Token泄露
    Performance     N+1查询 / 重复计算 / 大循环
    Reliability     空指针 / 异常遗漏 / 死锁风险
    Maintainability 重复代码 / 高复杂度函数
    Style           命名规范 / 注释规范

规则结果供 ReviewEngine 与 LLM 分析交叉验证（一致性检查），降低误报。

架构：规则以**可插拔的 RuleProvider** 实现，由 RuleRegistry 编排。内置 4 类
provider（正则行规则 / N+1 / 重复行 / 超大变更），第三方可实现 RuleProvider
并 `RiskDetector(...).register(provider)` 接入新规则，无需改动核心。
"""
from __future__ import annotations

import abc
import re

# 每条规则: (rule_id, category, level, confidence, 正则, 提示)
_LINE_RULES: list[tuple[str, str, str, float, re.Pattern, str]] = [
    # ---------- Security ----------
    ("SEC_SQL_FSTRING", "security", "P0", 0.9,
     re.compile(r"""(execute|executemany|query|rawQuery)\s*\(\s*f?["'].*(\{|%s|%d|"\s*\+|'\s*\+)""", re.I),
     "SQL 语句疑似拼接变量，存在 SQL 注入风险，应使用参数化查询"),
    ("SEC_SQL_CONCAT", "security", "P0", 0.85,
     re.compile(r"""(SELECT|INSERT|UPDATE|DELETE)\s.*["']\s*\+\s*\w""", re.I),
     "SQL 字符串与变量直接拼接，存在 SQL 注入风险"),
    ("SEC_XSS_INNERHTML", "security", "P1", 0.8,
     re.compile(r"\.(innerHTML|outerHTML)\s*=|document\.write\s*\("),
     "直接写入 HTML 可能引入 XSS，应使用 textContent 或做转义"),
    ("SEC_SSRF", "security", "P1", 0.6,
     re.compile(r"""(requests|httpx|urllib|axios|fetch)[\w.]*\s*[.(]\s*(get|post|request)?\s*\(?\s*f?["']?\s*(\+\s*\w|\{)"""),
     "外部请求地址由变量拼接，可能存在 SSRF 风险，应校验目标地址"),
    ("SEC_PATH_TRAVERSAL", "security", "P1", 0.65,
     re.compile(r"""open\s*\(\s*.*(\+\s*\w|f["'].*\{)|os\.path\.join\s*\(.*(request|input|param)""", re.I),
     "文件路径由外部输入拼接，可能存在路径遍历风险"),
    ("SEC_HARDCODED_SECRET", "security", "P0", 0.85,
     re.compile(r"""(password|passwd|secret|api_key|apikey|access_key|token)\s*[:=]\s*["'][^"'\s]{6,}["']""", re.I),
     "疑似硬编码密码/密钥，应移入环境变量或配置中心"),
    # Go 短变量声明 := 与反引号字符串不被上面的 [:=]+["'] 覆盖，单列一条
    ("SEC_GO_SHORT_VAR", "security", "P0", 0.85,
     re.compile(r"""(password|passwd|secret|api_key|apikey|access_key|token)\s*:=\s*["'`][^"'`\s]{6,}["'`]""", re.I),
     "疑似硬编码密码/密钥（Go := 短变量声明），应移入环境变量或配置中心"),
    ("SEC_TOKEN_LEAK", "security", "P0", 0.95,
     re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[\w-]{10,}|-----BEGIN (RSA |EC )?PRIVATE KEY-----|sk-[A-Za-z0-9]{20,})"),
     "检测到疑似真实凭证（Token/私钥），必须立即移除并轮换"),
    ("SEC_EVAL", "security", "P1", 0.8,
     re.compile(r"\b(eval|exec)\s*\("),
     "eval/exec 执行动态代码，存在代码注入风险"),
    ("SEC_SHELL_TRUE", "security", "P1", 0.75,
     re.compile(r"shell\s*=\s*True|os\.system\s*\(.*(\+|\{)"),
     "shell 命令拼接变量，存在命令注入风险"),
    ("SEC_PICKLE", "security", "P2", 0.7,
     re.compile(r"pickle\.loads?\s*\(|yaml\.load\s*\((?!.*Loader)"),
     "反序列化不可信数据存在代码执行风险"),

    # ---------- Performance ----------
    ("PERF_SELECT_STAR", "performance", "P3", 0.5,
     re.compile(r"SELECT\s+\*\s+FROM", re.I),
     "SELECT * 拉取全部列，建议只取需要的字段"),
    ("PERF_SLEEP_LOOP", "performance", "P2", 0.6,
     re.compile(r"time\.sleep\s*\(|Thread\.sleep\s*\("),
     "同步 sleep 可能阻塞执行（若处于循环/请求路径需关注）"),
    ("PERF_RANGE_LEN", "performance", "P3", 0.55,
     re.compile(r"for\s+\w+\s+in\s+range\s*\(\s*len\s*\("),
     "range(len(...)) 建议改用 enumerate"),

    # ---------- Reliability ----------
    ("REL_BARE_EXCEPT", "reliability", "P2", 0.85,
     re.compile(r"^\s*except\s*(Exception\s*)?:\s*(pass\s*)?$"),
     "裸 except / 吞异常会掩盖故障，应捕获具体异常并记录日志"),
    ("REL_CATCH_SWALLOW", "reliability", "P2", 0.8,
     re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}"),
     "空 catch 块吞掉异常，应记录或上抛"),
    ("REL_EQ_NONE", "reliability", "P3", 0.7,
     re.compile(r"[=!]=\s*None\b"),
     "与 None 比较应使用 is / is not"),
    ("REL_MUTABLE_DEFAULT", "reliability", "P1", 0.85,
     re.compile(r"def\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|\(\))"),
     "可变默认参数在调用间共享，存在隐蔽状态污染"),
    ("REL_OPEN_NO_WITH", "reliability", "P2", 0.55,
     re.compile(r"^\s*\w+\s*=\s*open\s*\("),
     "open() 未使用 with，异常路径下文件句柄可能泄露"),
    ("REL_LOCK_NO_WITH", "reliability", "P1", 0.6,
     re.compile(r"\.acquire\s*\(\s*\)"),
     "手动 acquire 锁，若异常路径未 release 存在死锁风险，建议 with 锁"),

    # ---------- Maintainability ----------
    ("MAINT_TODO", "maintainability", "P3", 0.6,
     re.compile(r"#\s*(TODO|FIXME|XXX)|//\s*(TODO|FIXME|XXX)", re.I),
     "遗留 TODO/FIXME，建议补充完成计划或关联 issue"),
    # 深层嵌套改由 DeepNestingRuleProvider 精确判定（仅控制流语句），
    # 不再用纯缩进正则——后者会误报多行 import/调用的续行。

    # ---------- Style ----------
    ("STYLE_LONG_LINE", "style", "P3", 0.5,
     re.compile(r"^.{161,}$"),
     "单行超过 160 字符，建议换行"),
    ("STYLE_PRINT_DEBUG", "style", "P3", 0.55,
     re.compile(r"^\s*(print\s*\(|console\.(log|debug)\s*\()"),
     "疑似调试输出残留，建议改用日志框架或删除"),
]

# 进入循环体后命中 -> N+1 / 循环内重查询
_LOOP_HEAD = re.compile(r"^\s*(for|while)\b.*[:{)]\s*$")
_QUERY_CALL = re.compile(
    r"\.(execute|query|filter|find|get|fetch|save|insert|update|delete)\s*\(|"
    r"(requests|httpx|axios|fetch)[\w.]*\s*[.(]", re.I)

# 深层嵌套只认「控制流语句」开头，避免把续行/深缩进数据行误判为嵌套
_CTRL_FLOW = re.compile(
    r"^(if|elif|else|for|while|with|try|except|finally|switch|case|do|"
    r"}\s*else|}\s*catch)\b")

# 重复行：跳过结构性样板（return X / 空容器赋值 / 纯括号收尾 / 纯字典键值）
_BOILERPLATE_LINE = re.compile(
    r"^(return\s+[\w.]+|[\w.]+\s*=\s*(\[\]|\{\}|\(\)|0|None|True|False|\"\"|'')|"
    r"[)\]}]+,?|(['\"]).*?\3\s*:\s*.+,?|break|continue|pass)$")
# 逻辑行信号：含函数调用或运算/比较/逻辑运算符（纯声明/数据行不算"可去重逻辑"）
_LOGIC_SIGNAL = re.compile(r"\(|[+\-*/%]|[<>=!]=|\b(and|or|not|in)\b")

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_patch_added_lines(patch: str) -> list[tuple[int, str]]:
    """解析 unified diff patch，返回 [(新文件行号, 新增行内容), ...]。"""
    added: list[tuple[int, str]] = []
    new_line = 0
    for raw in (patch or "").splitlines():
        m = _HUNK_HEADER.match(raw)
        if m:
            new_line = int(m.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            added.append((new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue  # 删除行不占新文件行号
        else:
            new_line += 1
    return added


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" \t"))


# ---------- 可插拔规则 provider ----------

class RuleProvider(abc.ABC):
    """规则 provider 抽象。第三方实现本接口即可接入自定义规则。

    detect 接收某文件已解析的新增行 [(行号, 行内容), ...]，返回候选风险列表。
    每条风险须含字段：rule/category/level/confidence/file/line/message/evidence。
    """

    id: str = ""

    @abc.abstractmethod
    def detect(self, filename: str,
               added: list[tuple[int, str]]) -> list[dict]:
        ...


class RegexLineRuleProvider(RuleProvider):
    """正则行级规则（安全/性能/稳定性/可维护性/风格五维），规则表可注入。"""

    id = "regex-line"

    def __init__(self, rules=_LINE_RULES):
        self.rules_ = rules

    def detect(self, filename, added):
        findings = []
        for line_no, text in added:
            stripped = text.strip()
            for rule_id, category, level, conf, pattern, message in self.rules_:
                if pattern.search(text):
                    findings.append({
                        "rule": rule_id, "category": category, "level": level,
                        "confidence": conf, "file": filename, "line": line_no,
                        "message": message, "evidence": stripped[:200],
                    })
        return findings


class NPlusOneRuleProvider(RuleProvider):
    """新增循环体内出现查询/外部调用，疑似 N+1。"""

    id = "n-plus-one"

    def detect(self, filename, added):
        findings = []
        loop_indent: int | None = None
        for line_no, text in added:
            if _LOOP_HEAD.match(text):
                loop_indent = _indent(text)
            elif loop_indent is not None:
                if text.strip() and _indent(text) <= loop_indent:
                    loop_indent = None
                elif _QUERY_CALL.search(text):
                    findings.append({
                        "rule": "PERF_N_PLUS_1", "category": "performance",
                        "level": "P1", "confidence": 0.7,
                        "file": filename, "line": line_no,
                        "message": "循环体内执行查询/外部调用，疑似 N+1，"
                                   "建议批量查询或移出循环",
                        "evidence": text.strip()[:200],
                    })
                    loop_indent = None  # 同一循环只报一次
        return findings


class DuplicateLineRuleProvider(RuleProvider):
    """同一 patch 内重复的**逻辑行**。

    仅统计「含函数调用或运算符」的实质逻辑行；跳过结构性样板（return x /
    空容器赋值 / 纯字典键值 / 括号收尾等），避免把相邻函数的样板行误判为重复。
    """

    id = "duplicate-line"
    _MIN_LEN = 30

    def detect(self, filename, added):
        findings = []
        seen_lines: dict[str, int] = {}
        for line_no, text in added:
            stripped = text.strip()
            if len(stripped) < self._MIN_LEN or stripped.startswith(("#", "//", "*")):
                continue
            # 声明行 / 块头 / 签名续行：跨函数重复是接口契约，非可提取的重复
            if (stripped.endswith(":")
                    or stripped.startswith(("def ", "class ", "async ",
                                            "@", "import ", "from "))):
                continue
            if _BOILERPLATE_LINE.match(stripped):
                continue  # 结构性样板行，不算重复
            if not _LOGIC_SIGNAL.search(stripped):
                continue  # 无调用/运算的纯声明/数据行，不算可去重逻辑
            if stripped in seen_lines:
                findings.append({
                    "rule": "MAINT_DUP_LINE", "category": "maintainability",
                    "level": "P3", "confidence": 0.5,
                    "file": filename, "line": line_no,
                    "message": f"与第 {seen_lines[stripped]} 行新增内容重复，"
                               "建议提取公共逻辑",
                    "evidence": stripped[:200],
                })
            else:
                seen_lines[stripped] = line_no
        return findings


class DeepNestingRuleProvider(RuleProvider):
    """深层嵌套：仅当**控制流语句**出现在深缩进处才报。

    相比纯缩进正则，跳过了多行 import/调用的续行、深缩进的数据/赋值行，
    只保留真正的「控制流套控制流」信号。
    """

    id = "deep-nesting"

    def __init__(self, min_indent: int = 20):
        self.min_indent_ = min_indent

    def detect(self, filename, added):
        findings = []
        for line_no, text in added:
            if _indent(text) < self.min_indent_:
                continue
            if _CTRL_FLOW.match(text.strip()):
                findings.append({
                    "rule": "MAINT_DEEP_NESTING", "category": "maintainability",
                    "level": "P3", "confidence": 0.5,
                    "file": filename, "line": line_no,
                    "message": "控制流嵌套层级过深，建议提前返回或拆分函数",
                    "evidence": text.strip()[:200],
                })
        return findings


class HugeChangeRuleProvider(RuleProvider):
    """单文件新增行数过大（高复杂度信号）。"""

    id = "huge-change"

    def __init__(self, max_added: int = 300):
        self.max_added_ = max_added

    def detect(self, filename, added):
        if len(added) <= self.max_added_:
            return []
        return [{
            "rule": "MAINT_HUGE_CHANGE", "category": "maintainability",
            "level": "P2", "confidence": 0.6,
            "file": filename, "line": added[0][0] if added else None,
            "message": f"单文件新增 {len(added)} 行，变更过大，建议拆分 PR",
            "evidence": "",
        }]


class RuleRegistry:
    """规则 provider 注册表，按注册顺序编排执行。"""

    def __init__(self, providers: list[RuleProvider] | None = None):
        self.providers_: list[RuleProvider] = list(providers or [])

    def register(self, provider: RuleProvider) -> RuleProvider:
        """注册一个 provider（返回自身便于链式/装饰器用法）。"""
        self.providers_.append(provider)
        return provider

    def detect_file(self, filename: str,
                    added: list[tuple[int, str]]) -> list[dict]:
        findings: list[dict] = []
        for provider in self.providers_:
            findings.extend(provider.detect(filename, added))
        return findings


def default_registry() -> RuleRegistry:
    """内置规则注册表。"""
    return RuleRegistry([
        RegexLineRuleProvider(),
        NPlusOneRuleProvider(),
        DuplicateLineRuleProvider(),
        DeepNestingRuleProvider(),
        HugeChangeRuleProvider(),
    ])


class RiskDetector:
    """规则引擎：编排 RuleRegistry 中的 provider 扫描 PR 变更。

    传入自定义 registry，或对默认实例 `register()` 追加 provider 以扩展规则。
    """

    def __init__(self, registry: RuleRegistry | None = None):
        self.registry_ = registry or default_registry()

    def register(self, provider: RuleProvider) -> RuleProvider:
        """追加一个自定义规则 provider。"""
        return self.registry_.register(provider)

    def detect_file(self, filename: str, patch: str) -> list[dict]:
        """扫描单个文件 patch 的新增行，返回候选风险列表。"""
        added = parse_patch_added_lines(patch)
        return self.registry_.detect_file(filename, added)

    def detect(self, files: list[dict]) -> list[dict]:
        """扫描 PR 全部变更文件。files 为 GitHub pulls/files 返回结构。"""
        findings: list[dict] = []
        for f in files:
            if f.get("status") == "removed":
                continue
            patch = f.get("patch")
            if patch:
                findings.extend(self.detect_file(f["filename"], patch))
        return findings
