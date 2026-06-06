"""risk_detector.py 测试。"""
from risk_detector import RiskDetector, parse_patch_added_lines


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
        " " * 24 + "deep_nested_call()"))
    assert {"MAINT_TODO", "STYLE_LONG_LINE", "STYLE_PRINT_DEBUG",
            "MAINT_DEEP_NESTING"} <= rules_of(fs)


def test_duplicate_lines():
    d = RiskDetector()
    dup = "result = compute_everything(a, b, c, d)"
    fs = d.detect_file("a.py", wrap_patch(dup, "y = 2", dup))
    dups = [f for f in fs if f["rule"] == "MAINT_DUP_LINE"]
    assert len(dups) == 1 and "重复" in dups[0]["message"]


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
