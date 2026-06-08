"""context_builder.py 测试。"""
from unittest.mock import MagicMock

from context_builder import (ContextBuilder, context_to_prompt, diff_chars,
                             extract_imports, resolve_import,
                             review_context_to_prompt)


# ---------- import 提取 ----------

def test_extract_imports_python():
    code = "from app.dao import UserDao\nimport util\nimport os.path\n"
    found = extract_imports("s.py", code)
    # from-import 同时给出模块本身与 模块.成员（成员可能也是模块）
    assert {"app.dao", "app.dao.UserDao", "util", "os.path"} <= set(found)


def test_extract_imports_python_from_multi():
    found = extract_imports("s.py", "from app import dao, model\n")
    assert {"app", "app.dao", "app.model"} <= set(found)


def test_extract_imports_js_ts():
    code = ("import x from './lib/a';\n"
            "import {y} from \"../b\";\n"
            "const z = require('pkg/c');\n")
    assert extract_imports("m.ts", code) == ["./lib/a", "../b", "pkg/c"]


def test_extract_imports_go():
    code = ('package main\nimport "fmt"\n'
            'import (\n  "strings"\n  "myapp/pkg/dao"\n)\n')
    assert extract_imports("m.go", code) == ["fmt", "strings", "myapp/pkg/dao"]


def test_extract_imports_java_kotlin():
    assert extract_imports("A.java",
                           "import com.foo.Dao;\nimport static a.b.C;") \
        == ["com.foo.Dao", "a.b.C"]
    assert extract_imports("A.kt", "import com.foo.Bar") == ["com.foo.Bar"]


def test_extract_imports_unknown_ext():
    assert extract_imports("a.rs", "use foo;") == []
    assert extract_imports("a.py", "") == []


# ---------- 路径解析 ----------

TREE = ["app/service.py", "app/dao.py", "app/util/__init__.py",
        "web/lib/a.ts", "web/lib/index.ts", "web/b.ts",
        "src/com/foo/Dao.java", "pkg/dao/dao.go", "README.md"]


def test_resolve_relative():
    assert resolve_import("./lib/a", "web/b.ts", TREE) == "web/lib/a.ts"
    assert resolve_import("./lib", "web/b.ts", TREE) == "web/lib/index.ts"
    assert resolve_import("./不存在", "web/b.ts", TREE) is None


def test_resolve_dotted():
    assert resolve_import("app.dao", "app/service.py", TREE) == "app/dao.py"
    assert resolve_import("app.util", "app/service.py", TREE) \
        == "app/util/__init__.py"
    assert resolve_import("com.foo.Dao", "src/com/foo/Main.java", TREE) \
        == "src/com/foo/Dao.java"


def test_resolve_go_path():
    assert resolve_import("myapp/pkg/dao", "main.go", TREE) == "pkg/dao/dao.go"


def test_resolve_not_found():
    assert resolve_import("totally.unknown", "a.py", TREE) is None
    assert resolve_import("", "a.py", TREE) is None


def test_resolve_same_dir_priority():
    tree = ["a/dao.py", "b/dao.py", "b/svc.py"]
    assert resolve_import("dao", "b/svc.py", tree) == "b/dao.py"


# ---------- 构建 ----------

def make_github():
    gh = MagicMock()
    gh.get_pull.return_value = {
        "title": "标题", "body": "描述", "user": {"login": "dev"},
        "base": {"ref": "main"}, "head": {"sha": "abc12345"},
        "additions": 5, "deletions": 2, "changed_files": 1,
    }
    gh.get_pull_files.return_value = [
        {"filename": "app/service.py", "status": "modified",
         "patch": "@@ -1 +1,2 @@\n+from app import dao\n+x = 1"},
        {"filename": "img.png", "status": "added", "patch": None},
        {"filename": "old.py", "status": "removed", "patch": "@@ -1 +0,0 @@\n-x"},
    ]
    gh.get_pull_commits.return_value = [
        {"sha": "abcdef1234", "commit": {"message": "提交一"}}]
    gh.list_repo_tree.return_value = ["app/service.py", "app/dao.py"]
    gh.get_file_content.side_effect = lambda o, r, path, ref, task_id=None: {
        "app/service.py": "from app import dao\nx = 1",
        "app/dao.py": "def query():\n    pass",
    }.get(path, "")
    gh.get_pull_review_comments.return_value = [
        {"path": "app/service.py", "body": "历史行级评论"}]
    gh.get_repo_recent_review_comments.return_value = [
        {"path": "app/dao.py", "body": "仓库历史评论"}]
    return gh


def test_build_full_context():
    cb = ContextBuilder(make_github())
    ctx = cb.build("o", "r", 1)
    assert ctx["pr"]["title"] == "标题"
    assert len(ctx["files"]) == 3
    assert ctx["commits"][0]["message"] == "提交一"
    assert "app/service.py" in ctx["changed_contents"]
    assert "old.py" not in ctx["changed_contents"]  # 删除的文件不取内容
    assert "app/dao.py" in ctx["related_files"]      # 二级上下文
    assert any("app/service.py -> app/dao.py" in c
               for c in ctx["call_chains"])          # 三级上下文
    assert len(ctx["history_comments"]) == 2         # 四级上下文


def test_build_tree_failure_not_fatal():
    gh = make_github()
    gh.list_repo_tree.side_effect = RuntimeError("树挂了")
    ctx = ContextBuilder(gh).build("o", "r", 1)
    assert ctx["pr"]["title"] == "标题"


def test_build_history_failure_not_fatal():
    gh = make_github()
    gh.get_pull_review_comments.side_effect = RuntimeError("评论挂了")
    ctx = ContextBuilder(gh).build("o", "r", 1)
    assert ctx["history_comments"] == []


def test_related_files_cap():
    gh = make_github()
    cb = ContextBuilder(gh)
    cb.max_related_ = 0
    ctx = cb.build("o", "r", 1)
    assert ctx["related_files"] == {}


# ---------- prompt 压平 ----------

def test_context_to_prompt():
    ctx = ContextBuilder(make_github()).build("o", "r", 1)
    prompt = context_to_prompt(ctx)
    assert "一级上下文" in prompt and "二级上下文" in prompt
    assert "三级上下文" in prompt and "四级上下文" in prompt
    assert "app/service.py" in prompt


def test_context_to_prompt_lean():
    ctx = ContextBuilder(make_github()).build("o", "r", 1)
    lean = context_to_prompt(ctx, lean=True)
    # 精简模式保留 PR 信息与一级 Diff，丢弃二/三/四级
    assert "一级上下文" in lean and "app/service.py" in lean
    assert "二级上下文" not in lean
    assert "三级上下文" not in lean
    assert "四级上下文" not in lean
    # 精简后应短于全量
    assert len(lean) < len(context_to_prompt(ctx))


def test_context_to_prompt_granular_flags():
    ctx = ContextBuilder(make_github()).build("o", "r", 1)
    # 只省略二级关联文件，保留三/四级
    p = context_to_prompt(ctx, include_related=False)
    assert "二级上下文" not in p
    assert "三级上下文" in p and "四级上下文" in p


def test_diff_chars():
    ctx = {"files": [{"patch": "abc"}, {"patch": "de"}, {"patch": None}]}
    assert diff_chars(ctx) == 5


def test_review_context_tiers():
    ctx = ContextBuilder(make_github()).build("o", "r", 1)
    size = diff_chars(ctx)

    # 小 PR：阈值放大 -> 全量 4 级，无省略
    p, tier, omitted = review_context_to_prompt(
        ctx, small_max=size + 1000, medium_max=size + 2000)
    assert tier == "small" and omitted == []
    assert "二级上下文" in p and "三级上下文" in p and "四级上下文" in p

    # 中 PR：省略关联文件，保留调用链 + 历史
    p, tier, omitted = review_context_to_prompt(
        ctx, small_max=0, medium_max=size + 1000)
    assert tier == "medium"
    assert "二级 关联文件" in omitted
    assert "二级上下文" not in p
    assert "三级上下文" in p and "四级上下文" in p

    # 大 PR：仅留历史，省略关联文件 + 调用链
    p, tier, omitted = review_context_to_prompt(
        ctx, small_max=0, medium_max=0)
    assert tier == "large"
    assert "二级 关联文件" in omitted and "三级 调用链" in omitted
    assert "二级上下文" not in p and "三级上下文" not in p
    assert "四级上下文" in p   # 历史评论始终保留（降误报价值最高）


def test_context_to_prompt_truncation():
    ctx = ContextBuilder(make_github()).build("o", "r", 1)
    prompt = context_to_prompt(ctx, max_chars=100)
    assert prompt.endswith("(上下文超长已截断)")


def test_context_to_prompt_no_optional_sections():
    ctx = {"pr": {"title": "t", "body": "", "author": "a", "base": "main",
                  "head": "abcdefgh", "additions": 0, "deletions": 0,
                  "changed_files": 0},
           "files": [{"filename": "f.py", "status": "added", "patch": None}],
           "commits": [{"sha": "x", "message": ""}],
           "related_files": {}, "call_chains": [], "history_comments": []}
    prompt = context_to_prompt(ctx)
    assert "二级上下文" not in prompt and "无 patch" in prompt
