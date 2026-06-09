"""上下文构建器 —— 系统核心能力。

四级上下文：
    一级  PR Diff：Changed Files / Patch / Commit
    二级  关联文件：解析变更文件的 import，拉取其依赖的仓库内文件
    三级  调用链：在 变更文件+关联文件+同目录文件 间构建依赖图，输出调用链
    四级  历史 Review：本 PR 已有评论 + 仓库近期 Review 评论（降低误报）

实现采用轻量静态分析（import/require 解析 + 路径匹配），不引入重型 AST 工具链。
支持语言（第一阶段）：Python / Java / Go / JavaScript / TypeScript。
"""
from __future__ import annotations

import logging
import os
import re

from config import CONFIG

_log = logging.getLogger(__name__)

# 各语言 import 提取正则 -> 捕获模块/路径字符串
_IMPORT_PATTERNS = {
    ".py": [
        re.compile(r"^\s*import\s+([\w.]+)", re.M),
    ],
    ".js": [
        re.compile(r"""import\s+(?:[\w*{}\s,]+\s+from\s+)?['"]([^'"]+)['"]"""),
        re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
    ],
    ".go": [
        re.compile(r'"([\w./-]+)"'),  # 仅在 import 块内使用（见 _extract_go_imports）
    ],
    ".java": [
        re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+);", re.M),
    ],
    ".kt": [
        re.compile(r"^\s*import\s+([\w.]+)", re.M),
    ],
}
# ts/tsx/jsx/mjs 复用 js 规则
for _ext in (".ts", ".tsx", ".jsx", ".mjs"):
    _IMPORT_PATTERNS[_ext] = _IMPORT_PATTERNS[".js"]

_CODE_EXTS = set(_IMPORT_PATTERNS) | {".rs", ".cpp", ".cc", ".h", ".hpp"}

_GO_IMPORT_BLOCK = re.compile(r"import\s*\((.*?)\)", re.S)
_GO_IMPORT_SINGLE = re.compile(r'^\s*import\s+"([\w./-]+)"', re.M)

# python from-import：from X import a, b -> 同时给出 X 与 X.a / X.b
# （from app import dao 中 dao 可能本身就是模块）
_PY_FROM_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+([\w.,\s]+)", re.M)


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def extract_imports(path: str, content: str) -> list[str]:
    """提取文件的 import 模块名/路径列表。"""
    ext = _ext(path)
    if not content or ext not in _IMPORT_PATTERNS:
        return []
    if ext == ".go":
        found = list(_GO_IMPORT_SINGLE.findall(content))
        for block in _GO_IMPORT_BLOCK.findall(content):
            found.extend(_IMPORT_PATTERNS[".go"][0].findall(block))
        return found
    found = []
    if ext == ".py":
        for mod, names in _PY_FROM_IMPORT.findall(content):
            found.append(mod)
            for name in names.split(","):
                name = name.strip().split()[0] if name.strip() else ""
                if name and name != "*":
                    found.append(f"{mod}.{name}")
    for pattern in _IMPORT_PATTERNS[ext]:
        found.extend(pattern.findall(content))
    return found


def resolve_import(imp: str, source_path: str, tree: list[str]) -> str | None:
    """把 import 字符串解析为仓库内文件路径（轻量匹配，找不到返回 None）。"""
    if not imp:
        return None
    source_dir = os.path.dirname(source_path)

    # 相对路径（js/ts: ./a/b, ../x）
    if imp.startswith("."):
        base = os.path.normpath(os.path.join(source_dir, imp))
        candidates = [base] + [base + e for e in
                               (".js", ".ts", ".tsx", ".jsx", ".mjs", ".py")]
        candidates += [os.path.join(base, "index" + e) for e in (".js", ".ts")]
        for c in candidates:
            if c in tree:
                return c
        return None

    # 点分模块（python/java/kotlin: a.b.c）或路径式（go: pkg/sub）
    parts = imp.replace(".", "/").split("/") if "." in imp else imp.split("/")
    stem = parts[-1]
    # 后缀从长到短：最长匹配优先（myapp/pkg/dao 先于 dao）
    suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
    code_paths = [p for p in tree if _ext(p) in _CODE_EXTS]
    for suffix in suffixes:
        same_dir, candidate = None, None
        for path in code_paths:
            no_ext = os.path.splitext(path)[0]
            dir_name = os.path.dirname(path)
            if (no_ext == suffix or no_ext.endswith("/" + suffix)
                    or no_ext == suffix + "/__init__"
                    or no_ext.endswith("/" + suffix + "/__init__")
                    # go 等以包目录为导入单位：匹配目录
                    or dir_name == suffix
                    or dir_name.endswith("/" + suffix)):
                if os.path.dirname(path) == source_dir:
                    same_dir = path
                    break
                candidate = candidate or path
        if same_dir or candidate:
            return same_dir or candidate
    # 兜底：文件名 stem 相同
    for path in code_paths:
        if os.path.basename(os.path.splitext(path)[0]) == stem:
            return path
    return None


class ContextBuilder:

    def __init__(self, github_client):
        self.github_ = github_client
        self.max_related_ = int(CONFIG.get("context_max_related_files", 8))
        self.max_file_bytes_ = int(CONFIG.get("context_max_file_bytes", 60000))
        self.history_limit_ = int(CONFIG.get("context_history_reviews", 20))

    def build(self, owner: str, repo: str, number: int,
              task_id: int | None = None) -> dict:
        """构建完整四级上下文。"""
        pr = self.github_.get_pull(owner, repo, number, task_id=task_id)
        head_ref = pr["head"]["sha"]

        # 一级：Diff / Commit / Changed Files
        files = self.github_.get_pull_files(owner, repo, number, task_id=task_id)
        commits = self.github_.get_pull_commits(owner, repo, number, task_id=task_id)
        changed_paths = [f["filename"] for f in files]

        try:
            tree = self.github_.list_repo_tree(owner, repo, head_ref, task_id=task_id)
        except Exception as e:  # noqa: BLE001 - 树拉取失败不阻塞主流程
            _log.warning("仓库树拉取失败，调用链/关联文件精度下降: %s", e)
            tree = list(changed_paths)

        # 变更文件完整内容（供 import 分析与行级定位）
        changed_contents: dict[str, str] = {}
        for f in files:
            if f.get("status") == "removed" or _ext(f["filename"]) not in _CODE_EXTS:
                continue
            content = self.github_.get_file_content(
                owner, repo, f["filename"], head_ref, task_id=task_id)
            changed_contents[f["filename"]] = content[: self.max_file_bytes_]

        # 二级：关联文件（变更文件 import 的仓库内文件）
        related = self._build_related(changed_contents, changed_paths, tree,
                                      owner, repo, head_ref, task_id)

        # 三级：调用链（依赖图）
        chains, edges = self._build_call_chains(changed_contents, related, tree)

        # 四级：历史 Review
        history = self._build_history(owner, repo, number, task_id)

        return {
            "pr": {
                "title": pr.get("title", ""),
                "body": pr.get("body") or "",
                "author": (pr.get("user") or {}).get("login", ""),
                "base": pr["base"]["ref"],
                "head": head_ref,
                "additions": pr.get("additions", 0),
                "deletions": pr.get("deletions", 0),
                "changed_files": pr.get("changed_files", len(files)),
            },
            "files": files,
            "commits": [
                {"sha": c["sha"][:8],
                 "message": (c.get("commit") or {}).get("message", "")}
                for c in commits
            ],
            "changed_contents": changed_contents,
            "related_files": related,
            "call_chains": chains,
            "dependency_edges": edges,
            "history_comments": history,
        }

    # ---------- 二级 ----------

    def _build_related(self, changed_contents: dict[str, str],
                       changed_paths: list[str], tree: list[str],
                       owner: str, repo: str, ref: str,
                       task_id: int | None) -> dict[str, str]:
        related: dict[str, str] = {}
        for path, content in changed_contents.items():
            for imp in extract_imports(path, content):
                if len(related) >= self.max_related_:
                    return related
                target = resolve_import(imp, path, tree)
                if (not target or target in changed_paths
                        or target in related):
                    continue
                text = self.github_.get_file_content(
                    owner, repo, target, ref, task_id=task_id)
                if text:
                    related[target] = text[: self.max_file_bytes_]
        return related

    # ---------- 三级 ----------

    def _build_call_chains(self, changed_contents: dict[str, str],
                           related: dict[str, str],
                           tree: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
        """在已知文件集合内构建依赖边，输出 调用链文本 与 边列表。"""
        known = {**related, **changed_contents}
        paths = list(known)
        edges: list[tuple[str, str]] = []
        for path, content in known.items():
            for imp in extract_imports(path, content):
                target = resolve_import(imp, path, paths)
                if target and target != path:
                    edges.append((path, target))

        # 从"无入边"的文件出发做 DFS，输出 a -> b -> c 链
        targets = {t for _, t in edges}
        roots = [p for p in paths if p not in targets] or paths[:1]
        graph: dict[str, list[str]] = {}
        for src, dst in edges:
            graph.setdefault(src, []).append(dst)

        chains: list[str] = []

        def dfs(node: str, trail: list[str]):
            nexts = [n for n in graph.get(node, []) if n not in trail]
            if not nexts:
                if len(trail) > 1:
                    chains.append(" -> ".join(trail))
                return
            for nxt in nexts:
                dfs(nxt, trail + [nxt])

        for root in roots:
            dfs(root, [root])
        return chains[:20], edges

    # ---------- 四级 ----------

    def _build_history(self, owner: str, repo: str, number: int,
                       task_id: int | None) -> list[dict]:
        history: list[dict] = []
        try:
            for c in self.github_.get_pull_review_comments(
                    owner, repo, number, task_id=task_id)[: self.history_limit_]:
                history.append({
                    "scope": "this_pr",
                    "path": c.get("path", ""),
                    "body": (c.get("body") or "")[:500],
                })
            for c in self.github_.get_repo_recent_review_comments(
                    owner, repo, limit=self.history_limit_, task_id=task_id):
                history.append({
                    "scope": "repo_recent",
                    "path": c.get("path", ""),
                    "body": (c.get("body") or "")[:500],
                })
        except Exception as e:  # noqa: BLE001 - 历史评论失败不阻塞主流程
            _log.warning("历史 Review 评论拉取失败，四级上下文降级: %s", e)
        return history[: self.history_limit_ * 2]


def diff_chars(ctx: dict) -> int:
    """PR 变更 Diff 的总字符数（分级阈值的依据）。"""
    return sum(len(f.get("patch") or "") for f in ctx["files"])


def context_to_prompt(ctx: dict, max_chars: int = 60000,
                      lean: bool = False,
                      include_related: bool = True,
                      include_chains: bool = True,
                      include_history: bool = True) -> str:
    """把上下文 bundle 压平成 LLM prompt 文本（带预算截断）。

    可分别控制是否纳入二/三/四级上下文：
      include_related  二级 关联文件（全文，最占字数）
      include_chains   三级 调用链（字数小）
      include_history  四级 历史 Review 评论（字数小，降误报价值高）
    lean=True 为快捷写法：等价于三者全 False（只留 PR 信息 + Commit + 一级 Diff）。

    summary 用全量；review 由 review_context_to_prompt 按 PR 体量分级取舍
    （见其文档与 hermes 延迟踩坑：大 PR 全量上下文会让推理模型长时间空转）。
    """
    if lean:
        include_related = include_chains = include_history = False

    parts: list[str] = []
    pr = ctx["pr"]
    parts.append(
        f"## PR 信息\n标题: {pr['title']}\n作者: {pr['author']}\n"
        f"分支: {pr['base']} <- {pr['head'][:8]}\n"
        f"变更: {pr['changed_files']} 文件, +{pr['additions']} -{pr['deletions']}\n"
        f"描述: {pr['body'][:1000]}")

    parts.append("## Commit 列表\n" + "\n".join(
        f"- {c['sha']} {c['message'].splitlines()[0] if c['message'] else ''}"
        for c in ctx["commits"][:20]))

    diff_parts = []
    for f in ctx["files"]:
        patch = f.get("patch") or "(无 patch，可能为二进制或超大文件)"
        diff_parts.append(f"### {f['filename']} ({f.get('status', '')})\n"
                          f"```diff\n{patch}\n```")
    parts.append("## 一级上下文: 变更 Diff\n" + "\n".join(diff_parts))

    if include_related and ctx["related_files"]:
        rel_parts = [f"### {p}\n```\n{t[:4000]}\n```"
                     for p, t in ctx["related_files"].items()]
        parts.append("## 二级上下文: 关联文件\n" + "\n".join(rel_parts))

    if include_chains and ctx["call_chains"]:
        parts.append("## 三级上下文: 调用链\n" + "\n".join(
            f"- {c}" for c in ctx["call_chains"]))

    if include_history and ctx["history_comments"]:
        parts.append("## 四级上下文: 历史 Review 评论\n" + "\n".join(
            f"- [{h['scope']}] {h['path']}: {h['body'][:200]}"
            for h in ctx["history_comments"]))

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n(上下文超长已截断)"
    return text


def review_context_to_prompt(ctx: dict, small_max: int = 15000,
                             medium_max: int = 40000) -> tuple[str, str, list[str]]:
    """按 PR 体量为 review 阶段分级取舍上下文。

    依据变更 Diff 字符数分三档（恢复 design.md「4 级上下文喂评审」的本意，
    同时把大 PR 撑爆推理模型的风险控制住）：
      小 PR (< small_max)            全量：关联文件 + 调用链 + 历史
      中 PR (small_max~medium_max)   保留调用链 + 历史，省略关联文件（最占字数）
      大 PR (>= medium_max)          仅保留历史（字数小、降误报价值最高），
                                     省略关联文件与调用链

    返回 (prompt, tier, omitted)。omitted 为被省略的上下文级别名，
    供报告标注「因体量已省略 X」，避免静默阉割。
    """
    size = diff_chars(ctx)
    if size < small_max:
        tier = "small"
        include_related, include_chains, include_history = True, True, True
    elif size < medium_max:
        tier = "medium"
        include_related, include_chains, include_history = False, True, True
    else:
        tier = "large"
        include_related, include_chains, include_history = False, False, True

    omitted: list[str] = []
    if not include_related and ctx["related_files"]:
        omitted.append("二级 关联文件")
    if not include_chains and ctx["call_chains"]:
        omitted.append("三级 调用链")
    if not include_history and ctx["history_comments"]:
        omitted.append("四级 历史评论")

    prompt = context_to_prompt(ctx, include_related=include_related,
                               include_chains=include_chains,
                               include_history=include_history)
    return prompt, tier, omitted
