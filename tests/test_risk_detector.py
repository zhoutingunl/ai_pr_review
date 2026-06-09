"""risk_detector.py 测试。"""
from risk_detector import (RiskDetector, RuleProvider, RuleRegistry,
                           RegexLineRuleProvider, default_registry,
                           parse_patch_added_lines)


def wrap_patch(*lines):
    body = "\n".join("+" + line for line in lines)
    return f"@@ -1,1 +1,{len(lines)} @@\n{body}"


def rules_of(findings):
    return {f["rule"] for f in findings}


def test_parse_patch_line_numbers():
    patch = ("@@ -10,3 +20,4 @@\n context\n+新增一\n-删除\n+新增二\n"
             "@@ -50 +99,2 @@\n+第二段\n")
    added = parse_patch_added_lines(patch)
    assert added == [(21, "新增一"), (22, "新增二"), (99, "第二段")]


def test_parse_patch_empty():
    assert parse_patch_added_lines("") == []
    assert parse_patch_added_lines(None) == []


def test_sql_injection_rules():
    d = RiskDetector()
    fs = d.detect_file("a.py", wrap_patch(
        'cur.execute(f"SELECT * FROM t WHERE id={uid}")',
        'sql = "SELECT a FROM t WHERE x=" + user_input'))
    assert {"SEC_SQL_FSTRING", "SEC_SQL_CONCAT"} <= rules_of(fs)


def test_xss_and_eval():
    d = RiskDetector()
    fs = d.detect_file("a.js", wrap_patch(
        "el.innerHTML = userInput;",
        "document.write(data);",
        "eval(code);"))
    assert {"SEC_XSS_INNERHTML", "SEC_EVAL"} <= rules_of(fs)


def test_secret_and_token():
    d = RiskDetector()
    fs = d.detect_file("conf.py", wrap_patch(
        'password = "supersecret9"',
        'token = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'))
    assert {"SEC_HARDCODED_SECRET", "SEC_TOKEN_LEAK"} <= rules_of(fs)


def test_go_short_var_secret():
    """Go := 短变量声明的硬编码密钥（旧 [:=] 正则漏报）。"""
    d = RiskDetector()
    fs = d.detect_file("main.go", wrap_patch(
        '\tsecret := "s3cr3t-value"',
        '\tapiKey := `hardcoded-token-x`'))   # 反引号字符串
    assert "SEC_GO_SHORT_VAR" in rules_of(fs)
    # Rust/Swift 的 let 形式本就被 SEC_HARDCODED_SECRET 命中（关键字在变量名）
    fs2 = d.detect_file("a.rs", wrap_patch('    let api_key = "abcdef123";'))
    assert "SEC_HARDCODED_SECRET" in rules_of(fs2)


def test_shell_and_pickle():
    d = RiskDetector()
    fs = d.detect_file("a.py", wrap_patch(
        "subprocess.run(cmd, shell=True)",
        "data = pickle.loads(blob)"))
    assert {"SEC_SHELL_TRUE", "SEC_PICKLE"} <= rules_of(fs)


def test_n_plus_1_in_loop():
    d = RiskDetector()
    fs = d.detect_file("a.py", wrap_patch(
        "for user in users:",
        "    profile = db.query(user.id)"))
    assert "PERF_N_PLUS_1" in rules_of(fs)


def test_no_n_plus_1_outside_loop():
    d = RiskDetector()
    fs = d.detect_file("a.py", wrap_patch(
        "for user in users:",
        "    total += 1",
        "result = db.query(1)"))  # 已退出循环缩进
    assert "PERF_N_PLUS_1" not in rules_of(fs)


def test_reliability_rules():
    d = RiskDetector()
    fs = d.detect_file("a.py", wrap_patch(
        "except:",
        "if x == None:",
        "def f(items=[]):",
        "fh = open('/tmp/x')",
        "lock.acquire()"))
    assert {"REL_BARE_EXCEPT", "REL_EQ_NONE", "REL_MUTABLE_DEFAULT",
            "REL_OPEN_NO_WITH", "REL_LOCK_NO_WITH"} <= rules_of(fs)


def test_catch_swallow():
    d = RiskDetector()
    fs = d.detect_file("A.java", wrap_patch("try { x(); } catch (Exception e) {}"))
    assert "REL_CATCH_SWALLOW" in rules_of(fs)


def test_style_and_maintainability():
    d = RiskDetector()
    long_line = "x = 1  # " + "很" * 200
    fs = d.detect_file("a.py", wrap_patch(
        "# TODO: 以后再说",
        long_line,
        "print('debug')",
        " " * 24 + "if cond:"))   # 深缩进的控制流语句 -> 深层嵌套
    assert {"MAINT_TODO", "STYLE_LONG_LINE", "STYLE_PRINT_DEBUG",
            "MAINT_DEEP_NESTING"} <= rules_of(fs)


def test_deep_nesting_only_control_flow():
    """深层嵌套只报控制流语句，续行/深缩进数据行不误报。"""
    d = RiskDetector()
    # 深缩进的续行（多行 import/调用参数）与普通调用 -> 不应触发
    fs = d.detect_file("a.py", wrap_patch(
        " " * 24 + "some_continuation_arg,",
        " " * 24 + "deep_nested_call()"))
    assert "MAINT_DEEP_NESTING" not in rules_of(fs)
    # 深缩进的 for/while/except -> 应触发
    fs = d.detect_file("a.py", wrap_patch(
        " " * 20 + "for x in items:",
        " " * 20 + "except ValueError:"))
    assert "MAINT_DEEP_NESTING" in rules_of(fs)


def test_duplicate_lines():
    d = RiskDetector()
    dup = "result = compute_everything(a, b, c, d)"
    fs = d.detect_file("a.py", wrap_patch(dup, "y = 2", dup))
    dups = [f for f in fs if f["rule"] == "MAINT_DUP_LINE"]
    assert len(dups) == 1 and "重复" in dups[0]["message"]


def test_duplicate_skips_boilerplate():
    """结构性样板行（return x / 空容器赋值 / 字典键值）重复出现不算重复。"""
    d = RiskDetector()
    fs = d.detect_file("a.py", wrap_patch(
        "        findings = []                          ",  # 凑够长度的空容器赋值
        "        return findings_value_that_is_long_enough",
        "        findings = []                          ",
        "        return findings_value_that_is_long_enough",
        '        "rule": rule_identifier, "category": cat_value,',
        '        "rule": rule_identifier, "category": cat_value,'))
    assert "MAINT_DUP_LINE" not in rules_of(fs)


def test_duplicate_skips_signatures():
    """相同方法签名/块头（接口契约）跨函数重复不算重复。"""
    d = RiskDetector()
    sig = "    def detect(self, filename, added_lines_param):"
    fs = d.detect_file("a.py", wrap_patch(sig, "    x = compute(a)", sig))
    assert "MAINT_DUP_LINE" not in rules_of(fs)


def test_huge_change():
    d = RiskDetector()
    lines = [f"v{i} = {i}" for i in range(301)]
    fs = d.detect_file("big.py", wrap_patch(*lines))
    assert "MAINT_HUGE_CHANGE" in rules_of(fs)


def test_detect_multi_files_skip_removed():
    d = RiskDetector()
    files = [
        {"filename": "a.py", "status": "modified",
         "patch": wrap_patch("except:")},
        {"filename": "b.py", "status": "removed",
         "patch": wrap_patch("except:")},
        {"filename": "c.bin", "status": "added", "patch": None},
    ]
    fs = d.detect(files)
    assert {f["file"] for f in fs} == {"a.py"}


def test_finding_fields():
    d = RiskDetector()
    fs = d.detect_file("a.py", wrap_patch("except:"))
    f = fs[0]
    assert f["file"] == "a.py" and isinstance(f["line"], int)
    assert 0 < f["confidence"] <= 1 and f["level"].startswith("P")


# ---------- 可插拔规则架构 ----------

def test_default_registry_providers():
    reg = default_registry()
    ids = [p.id for p in reg.providers_]
    assert ids == ["regex-line", "n-plus-one", "duplicate-line",
                   "deep-nesting", "huge-change"]


def test_register_custom_provider():
    """第三方实现 RuleProvider 即可接入新规则，无需改核心。"""

    class TodoChineseProvider(RuleProvider):
        id = "todo-zh"

        def detect(self, filename, added):
            out = []
            for line_no, text in added:
                if "待办" in text:
                    out.append({
                        "rule": "CUSTOM_TODO_ZH", "category": "maintainability",
                        "level": "P3", "confidence": 0.6, "file": filename,
                        "line": line_no, "message": "发现中文待办标记",
                        "evidence": text.strip()[:200]})
            return out

    d = RiskDetector()
    d.register(TodoChineseProvider())
    fs = d.detect_file("a.py", wrap_patch("x = 1  # 待办: 重构"))
    assert "CUSTOM_TODO_ZH" in rules_of(fs)


def test_custom_registry_isolated():
    """传入自定义 registry 可完全替换内置规则集。"""
    reg = RuleRegistry([RegexLineRuleProvider()])  # 只保留正则规则
    d = RiskDetector(registry=reg)
    fs = d.detect_file("a.py", wrap_patch(
        "for u in users:", "    db.query(u)"))
    # 只有正则 provider，N+1 不会被检出
    assert "PERF_N_PLUS_1" not in rules_of(fs)


def test_huge_change_provider_threshold_configurable():
    from risk_detector import HugeChangeRuleProvider
    reg = RuleRegistry([HugeChangeRuleProvider(max_added=2)])
    d = RiskDetector(registry=reg)
    fs = d.detect_file("a.py", wrap_patch("a=1", "b=2", "c=3"))
    assert "MAINT_HUGE_CHANGE" in rules_of(fs)
