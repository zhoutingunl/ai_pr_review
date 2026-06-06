"""GitHub 客户端。

职责：
    * 解析 PR 地址
    * 拉取 PR 元信息 / Diff / Commit / Changed Files
    * 拉取仓库文件内容（供上下文构建）
    * 拉取历史 Review 评论（四级上下文）
    * 回写 Review（Comment / Approve / Request Changes）与行级评论

令牌来源：.env 的 GITHUB_TOKEN，缺省回落 `gh auth token`（见 config.py）。
所有调用产生 github_metric 指标（注入 Store 时）。
"""
from __future__ import annotations

import re
import time

import requests

from config import CONFIG

_API = "https://api.github.com"

_PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)/?")


class GithubError(Exception):
    """GitHub API 调用失败。"""


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """解析 PR 地址 -> (owner, repo, number)。"""
    m = _PR_URL_RE.match(url.strip())
    if not m:
        raise GithubError(f"无效的 PR 地址: {url}")
    return m.group("owner"), m.group("repo"), int(m.group("number"))


class GithubClient:

    def __init__(self, token: str | None = None, store=None):
        self.token_ = token if token is not None else CONFIG.github_token_
        self.store_ = store
        self.session_ = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-pr-review",
        }
        if self.token_:
            headers["Authorization"] = f"Bearer {self.token_}"
        self.session_.headers.update(headers)

    # ---------- 基础请求 ----------

    def _record(self, operation: str, success: bool, started: float,
                error: str | None = None, task_id: int | None = None) -> None:
        if self.store_:
            self.store_.record_github_metric(
                operation, success, int((time.time() - started) * 1000),
                task_id=task_id, error=error)

    def _request(self, method: str, path: str, operation: str = "fetch",
                 task_id: int | None = None, **kwargs):
        started = time.time()
        url = path if path.startswith("http") else f"{_API}{path}"
        # GET 幂等，网络抖动（超时/断连）自动重试；写操作不重试避免重复提交
        attempts = 3 if method.upper() == "GET" else 1
        last_error: Exception | None = None
        resp = None
        for attempt in range(attempts):
            try:
                resp = self.session_.request(method, url, timeout=60, **kwargs)
                break
            except requests.RequestException as e:
                last_error = e
                if attempt < attempts - 1:
                    time.sleep(2 * (attempt + 1))
        if resp is None:
            self._record(operation, False, started, str(last_error), task_id)
            raise GithubError(f"GitHub 请求异常: {last_error}") from last_error
        if resp.status_code >= 400:
            error = f"{resp.status_code}: {resp.text[:300]}"
            self._record(operation, False, started, error, task_id)
            raise GithubError(f"GitHub API 失败 {method} {path} -> {error}")
        self._record(operation, True, started, task_id=task_id)
        return resp

    def _get_json(self, path: str, operation: str = "fetch",
                  task_id: int | None = None, **kwargs):
        return self._request("GET", path, operation, task_id, **kwargs).json()

    def _paginate(self, path: str, operation: str = "fetch",
                  task_id: int | None = None, max_pages: int = 10) -> list:
        items, page = [], 1
        while page <= max_pages:
            batch = self._get_json(f"{path}{'&' if '?' in path else '?'}"
                                   f"per_page=100&page={page}",
                                   operation, task_id)
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

    # ---------- PR 拉取 ----------

    def get_pull(self, owner: str, repo: str, number: int,
                 task_id: int | None = None) -> dict:
        """PR 元信息：标题、描述、分支、head sha、状态等。"""
        return self._get_json(f"/repos/{owner}/{repo}/pulls/{number}",
                              task_id=task_id)

    def get_pull_files(self, owner: str, repo: str, number: int,
                       task_id: int | None = None) -> list[dict]:
        """Changed Files（含 patch / additions / deletions / status）。"""
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/files",
                              task_id=task_id)

    def get_pull_commits(self, owner: str, repo: str, number: int,
                         task_id: int | None = None) -> list[dict]:
        return self._paginate(f"/repos/{owner}/{repo}/pulls/{number}/commits",
                              task_id=task_id)

    def get_pull_diff(self, owner: str, repo: str, number: int,
                      task_id: int | None = None) -> str:
        """整体 unified diff 文本。"""
        resp = self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}",
            task_id=task_id, headers={"Accept": "application/vnd.github.diff"})
        return resp.text

    # ---------- 仓库内容（上下文用） ----------

    def get_file_content(self, owner: str, repo: str, path: str, ref: str,
                         task_id: int | None = None) -> str:
        """按 ref 拉取单文件文本内容（raw）。不存在/二进制返回空串。"""
        try:
            resp = self._request(
                "GET", f"/repos/{owner}/{repo}/contents/{path}?ref={ref}",
                task_id=task_id, headers={"Accept": "application/vnd.github.raw+json"})
        except GithubError:
            return ""
        text = resp.text
        if "\x00" in text[:1024]:
            return ""
        return text

    def list_repo_tree(self, owner: str, repo: str, ref: str,
                       task_id: int | None = None) -> list[str]:
        """递归列出仓库文件路径（供调用链分析定位文件）。"""
        data = self._get_json(
            f"/repos/{owner}/{repo}/git/trees/{ref}?recursive=1", task_id=task_id)
        return [item["path"] for item in data.get("tree", [])
                if item.get("type") == "blob"]

    # ---------- 历史 Review（四级上下文） ----------

    def get_pull_review_comments(self, owner: str, repo: str, number: int,
                                 task_id: int | None = None) -> list[dict]:
        """本 PR 已有的行级评论。"""
        return self._paginate(
            f"/repos/{owner}/{repo}/pulls/{number}/comments", task_id=task_id)

    def get_repo_recent_review_comments(self, owner: str, repo: str,
                                        limit: int = 20,
                                        task_id: int | None = None) -> list[dict]:
        """仓库维度最近的历史 Review 评论（降低误报用）。"""
        items = self._get_json(
            f"/repos/{owner}/{repo}/pulls/comments?sort=created&direction=desc"
            f"&per_page={min(limit, 100)}", task_id=task_id)
        return items if isinstance(items, list) else []

    # ---------- 回写 ----------

    def create_issue_comment(self, owner: str, repo: str, number: int,
                             body: str, task_id: int | None = None) -> dict:
        """PR 整体评论（Conversation 区）。"""
        return self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments",
            operation="comment", task_id=task_id, json={"body": body}).json()

    def create_review(self, owner: str, repo: str, number: int, body: str,
                      event: str = "COMMENT", comments: list[dict] | None = None,
                      task_id: int | None = None) -> dict:
        """提交 Review。

        event: COMMENT / APPROVE / REQUEST_CHANGES
        comments: [{"path": "service.py", "line": 123, "body": "..."}]
                  按 file + line 精确定位（side 固定 RIGHT，即变更后的行）。
        """
        payload: dict = {"body": body, "event": event}
        if comments:
            payload["comments"] = [
                {"path": c["path"], "line": int(c["line"]),
                 "side": c.get("side", "RIGHT"), "body": c["body"]}
                for c in comments
            ]
        return self._request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            operation="review", task_id=task_id, json=payload).json()
