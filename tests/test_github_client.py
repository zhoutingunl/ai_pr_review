"""github_client.py 测试（mock HTTP）。"""
import pytest
from unittest.mock import MagicMock, patch

import requests

from github_client import GithubClient, GithubError, parse_pr_url


def make_client(store=None):
    client = GithubClient(token="t", store=store)
    client.session_ = MagicMock()
    return client


def fake_resp(status=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def test_parse_pr_url():
    assert parse_pr_url("https://github.com/a/b/pull/12") == ("a", "b", 12)
    assert parse_pr_url("http://github.com/x.y/z-w/pull/3/") == ("x.y", "z-w", 3)
    with pytest.raises(GithubError):
        parse_pr_url("https://gitlab.com/a/b/pull/1")
    with pytest.raises(GithubError):
        parse_pr_url("随便的字符串")


def test_token_header():
    client = GithubClient(token="abc")
    assert client.session_.headers["Authorization"] == "Bearer abc"
    anon = GithubClient(token="")
    assert "Authorization" not in anon.session_.headers


def test_get_pull(store):
    client = make_client(store)
    client.session_.request.return_value = fake_resp(json_data={"title": "T"})
    assert client.get_pull("a", "b", 1)["title"] == "T"
    metrics = store.list_github_metrics()
    assert metrics[0]["operation"] == "fetch" and metrics[0]["success"] == 1


def test_request_http_error(store):
    client = make_client(store)
    client.session_.request.return_value = fake_resp(404, text="Not Found")
    with pytest.raises(GithubError, match="404"):
        client.get_pull("a", "b", 1)
    assert store.list_github_metrics()[0]["success"] == 0


def test_request_network_error(store):
    client = make_client(store)
    client.session_.request.side_effect = requests.ConnectionError("断网")
    with patch("github_client.time.sleep"):
        with pytest.raises(GithubError, match="请求异常"):
            client.get_pull("a", "b", 1)
    assert client.session_.request.call_count == 3  # GET 自动重试
    assert store.list_github_metrics()[0]["success"] == 0


def test_request_retry_then_success(store):
    client = make_client(store)
    client.session_.request.side_effect = [
        requests.Timeout("超时"), fake_resp(json_data={"title": "T"})]
    with patch("github_client.time.sleep"):
        assert client.get_pull("a", "b", 1)["title"] == "T"
    assert store.list_github_metrics()[0]["success"] == 1


def test_request_write_no_retry(store):
    client = make_client(store)
    client.session_.request.side_effect = requests.Timeout("超时")
    with patch("github_client.time.sleep"):
        with pytest.raises(GithubError):
            client.create_issue_comment("a", "b", 1, "x")
    assert client.session_.request.call_count == 1  # 写操作不重试


def test_paginate_multi_page():
    client = make_client()
    page1 = [{"i": n} for n in range(100)]
    page2 = [{"i": 100}]
    client.session_.request.side_effect = [
        fake_resp(json_data=page1), fake_resp(json_data=page2)]
    files = client.get_pull_files("a", "b", 1)
    assert len(files) == 101


def test_paginate_empty():
    client = make_client()
    client.session_.request.return_value = fake_resp(json_data=[])
    assert client.get_pull_commits("a", "b", 1) == []


def test_get_pull_diff():
    client = make_client()
    client.session_.request.return_value = fake_resp(text="diff --git ...")
    assert client.get_pull_diff("a", "b", 1).startswith("diff")


def test_get_file_content():
    client = make_client()
    client.session_.request.return_value = fake_resp(text="print('hi')")
    assert client.get_file_content("a", "b", "x.py", "sha") == "print('hi')"


def test_get_file_content_binary():
    client = make_client()
    client.session_.request.return_value = fake_resp(text="PK\x00\x03bin")
    assert client.get_file_content("a", "b", "x.zip", "sha") == ""


def test_get_file_content_missing():
    client = make_client()
    client.session_.request.return_value = fake_resp(404, text="no")
    assert client.get_file_content("a", "b", "gone.py", "sha") == ""


def test_list_repo_tree():
    client = make_client()
    client.session_.request.return_value = fake_resp(json_data={"tree": [
        {"path": "a.py", "type": "blob"},
        {"path": "dir", "type": "tree"},
        {"path": "b.js", "type": "blob"},
    ]})
    assert client.list_repo_tree("a", "b", "main") == ["a.py", "b.js"]


def test_review_comments():
    client = make_client()
    client.session_.request.return_value = fake_resp(json_data=[{"body": "旧评论"}])
    assert client.get_pull_review_comments("a", "b", 1)[0]["body"] == "旧评论"
    assert client.get_repo_recent_review_comments("a", "b")[0]["body"] == "旧评论"


def test_repo_recent_comments_bad_shape():
    client = make_client()
    client.session_.request.return_value = fake_resp(json_data={"message": "err"})
    assert client.get_repo_recent_review_comments("a", "b") == []


def test_create_issue_comment(store):
    client = make_client(store)
    client.session_.request.return_value = fake_resp(json_data={"id": 5})
    assert client.create_issue_comment("a", "b", 1, "你好")["id"] == 5
    assert store.list_github_metrics()[0]["operation"] == "comment"


def test_create_review_payload(store):
    client = make_client(store)
    client.session_.request.return_value = fake_resp(json_data={"id": 9})
    result = client.create_review(
        "a", "b", 1, "总评", event="REQUEST_CHANGES",
        comments=[{"path": "x.py", "line": 3, "body": "行级"}])
    assert result["id"] == 9
    kwargs = client.session_.request.call_args.kwargs
    payload = kwargs["json"]
    assert payload["event"] == "REQUEST_CHANGES"
    assert payload["comments"][0] == {"path": "x.py", "line": 3,
                                      "side": "RIGHT", "body": "行级"}
    assert store.list_github_metrics()[0]["operation"] == "review"


def test_create_review_no_comments():
    client = make_client()
    client.session_.request.return_value = fake_resp(json_data={"id": 1})
    client.create_review("a", "b", 1, "干净")
    payload = client.session_.request.call_args.kwargs["json"]
    assert "comments" not in payload
