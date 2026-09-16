"""Behaviour tests for runtime/wiki/reconcile.py.

The fake server below is not invented. Every field and every quirk it models was
read off a GitLab 18.9.1-ee server on 2026/09/16, with a group access token
whose bot user is a Maintainer on the project:

  * `GET /projects/:id/hooks` returns the hook URL verbatim, trigger token and
    all. It is not redacted, which is why the reconciler can compare the whole
    URL and so stay silent on a second run, and also why nothing here may print
    one.
  * `GET /projects/:id/triggers` returned an operator's token, created by the
    instance administrator, as four characters; a token the calling identity had just
    created came back at its full 26. That asymmetry is the whole reason the
    component insists on owning its own trigger token.
  * `POST /projects/:id/triggers` as that bot returned 201 with the bot's own
    `owner.id`, and `PUT /projects/:id/hooks/:hook` returned 200, so a Maintainer
    project access token is sufficient for both.

Three directions the brief names are covered here: hook missing, hook pointing
at an old hostname, hook already correct. So are the two that decide whether
this is safe to turn on by default: a token that cannot manage hooks, and an
API that fails after the probe passed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_helpers = importlib.util.spec_from_file_location(
    "runtime_helpers", Path(__file__).resolve().parents[1] / "runtime_helpers.py"
)
helpers = importlib.util.module_from_spec(_helpers)
_helpers.loader.exec_module(helpers)

FakeResponse = helpers.FakeResponse
reconcile = helpers.load_runtime_module("wiki/reconcile.py")

API = "https://gitlab.example.com/api/v4"
OLD_API = "https://gitlab.old.example/api/v4"
PROJECT = "79"
BOT = 6
OPERATOR = 1
OUR_TOKEN = "glptt-" + "a" * 20
FOREIGN = "glptt-" + "f" * 20   # a token created by somebody else
HOOK_URL = f"{API}/projects/{PROJECT}/ref/main/trigger/pipeline?token={OUR_TOKEN}"


def hook(hook_id=58, url=HOOK_URL, name="wiki-sync", **overrides):
    record = {
        "id": hook_id,
        "name": name,
        "description": "wiki edits start a sync pipeline",
        "url": url,
        "wiki_page_events": True,
        "push_events": False,
        "enable_ssl_verification": True,
        "url_variables": None,
    }
    record.update(overrides)
    return record


def trigger(trigger_id=7, owner=BOT, token=OUR_TOKEN, description="wiki-sync"):
    return {
        "id": trigger_id,
        "description": description,
        "token": token,
        "owner": {"id": owner, "username": "root" if owner == OPERATOR else "bot"},
    }


class FakeGitLab:
    """Holds hooks and triggers, and records every write."""

    def __init__(self, hooks=(), triggers=(), user_id=BOT, statuses=None):
        self.hooks = list(hooks)
        self.triggers = list(triggers)
        self.user_id = user_id
        self.statuses = statuses or {}
        self.writes = []
        self.reads = []
        self.next_id = 100

    def install(self, monkeypatch):
        monkeypatch.setattr(reconcile, "request", self.request)
        return self

    def request(self, method, url, *, headers=None, body=None, timeout=30):
        path = url[len(API):] if url.startswith(API) else url
        forced = self.statuses.get(f"{method} {path}") or self.statuses.get(method)
        if forced:
            return FakeResponse(forced, {"message": "forced"})

        if method == "GET":
            self.reads.append(path)
        if method == "GET" and path == "/user":
            return FakeResponse(200, {"id": self.user_id, "username": "bot"})
        if method == "GET" and path == f"/projects/{PROJECT}/hooks":
            return FakeResponse(200, self.hooks)
        if method == "GET" and path == f"/projects/{PROJECT}/triggers":
            return FakeResponse(200, self.triggers)

        fields = dict(pair.split("=", 1) for pair in body.decode().split("&")) if body else {}
        self.writes.append((method, path, fields))

        if method == "POST" and path == f"/projects/{PROJECT}/triggers":
            self.next_id += 1
            made = trigger(self.next_id, owner=self.user_id, token=OUR_TOKEN)
            self.triggers.append(made)
            return FakeResponse(201, made)
        if method == "POST" and path == f"/projects/{PROJECT}/hooks":
            self.next_id += 1
            return FakeResponse(201, {"id": self.next_id})
        if method == "PUT" and path.startswith(f"/projects/{PROJECT}/hooks/"):
            return FakeResponse(200, {"id": int(path.rsplit("/", 1)[1])})
        raise AssertionError(f"unexpected call: {method} {path}")


def run(monkeypatch, server, **env):
    FakeGitLab.install(server, monkeypatch)
    monkeypatch.setenv("CI_API_V4_URL", env.get("api", API))
    monkeypatch.setenv("CI_PROJECT_ID", PROJECT)
    monkeypatch.setenv("CI_DEFAULT_BRANCH", env.get("branch", "main"))
    monkeypatch.delenv("WIKI_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("WIKI_TOKEN", "a-token-value")
    return reconcile.main([])


def test_hook_missing_creates_the_trigger_and_the_hook(monkeypatch, capsys):
    server = FakeGitLab()
    assert run(monkeypatch, server) == 0

    methods = [(method, path) for method, path, _ in server.writes]
    assert methods == [
        ("POST", f"/projects/{PROJECT}/triggers"),
        ("POST", f"/projects/{PROJECT}/hooks"),
    ]
    created = server.writes[1][2]
    assert created["wiki_page_events"] == "true"
    assert created["push_events"] == "false"
    assert created["enable_ssl_verification"] == "true"
    assert created["name"] == "wiki-sync"
    assert f"projects%2F{PROJECT}%2Fref%2Fmain%2Ftrigger" in created["url"]

    output = capsys.readouterr().out
    assert "trigger  created" in output
    assert "webhook  created" in output
    assert "runs as this job's own identity" in output


def test_hook_on_an_old_hostname_is_repointed_with_its_own_token(monkeypatch, capsys):
    """The address is rewritten around the token, which is copied untouched.

    The token here belongs to the operator (`FOREIGN`), not to the identity the
    job runs as. Replacing it is what broke project 79's pipeline 6080, so the
    assertion that matters is that it survives the repair.
    """
    stale = f"{OLD_API}/projects/{PROJECT}/ref/main/trigger/pipeline?token={FOREIGN}"
    server = FakeGitLab(hooks=[hook(url=stale)], triggers=[trigger(trigger_id=4, owner=OPERATOR,
                                                                  token="6d05")])
    assert run(monkeypatch, server) == 0

    assert [(method, path) for method, path, _ in server.writes] == [
        ("PUT", f"/projects/{PROJECT}/hooks/58")
    ]
    patch = server.writes[0][2]
    assert list(patch) == ["url"], "only the drifted field is written"
    assert "gitlab.example.com" in patch["url"]
    assert patch["url"].endswith(FOREIGN), "the operator's token is carried across"

    output = capsys.readouterr().out
    assert "keeping the token the webhook already carries" in output
    assert "webhook  updated (id 58, url)" in output
    assert "gitlab.old.example" in output, "the log names the address it replaced"


def test_a_foreign_token_is_never_swapped_for_one_this_job_owns(monkeypatch, capsys):
    """No trigger is created, listed or reported when the hook supplies one.

    A trigger pipeline runs as its token's owner. Swapping a group member's
    token for a project bot's leaves the pipeline unable to see a group-level
    protected WIKI_TOKEN, so it is created with no jobs and fails.
    """
    stale = f"{OLD_API}/x?token={FOREIGN}"
    server = FakeGitLab(hooks=[hook(url=stale)], triggers=[trigger(trigger_id=4, owner=OPERATOR,
                                                                  token="6d05")])
    assert run(monkeypatch, server) == 0

    assert not any(path.endswith("/triggers") for _, path, _ in server.writes)
    assert server.reads.count(f"/projects/{PROJECT}/triggers") == 0, (
        "the trigger list is not even fetched when the hook carries a token"
    )
    assert server.reads.count("/user") == 0
    output = capsys.readouterr().out
    assert "belongs to" not in output, "another identity's token is not drift"
    assert FOREIGN not in output


def test_a_hook_carrying_no_token_gets_one_minted(monkeypatch, capsys):
    server = FakeGitLab(hooks=[hook(url=f"{API}/projects/{PROJECT}/ref/main/trigger/pipeline")],
                        triggers=[])
    assert run(monkeypatch, server) == 0

    methods = [(method, path) for method, path, _ in server.writes]
    assert methods == [
        ("POST", f"/projects/{PROJECT}/triggers"),
        ("PUT", f"/projects/{PROJECT}/hooks/58"),
    ]
    output = capsys.readouterr().out
    assert "trigger  created" in output
    assert "runs as this job's own identity" in output, "the identity change is announced"


def test_hook_already_correct_writes_nothing(monkeypatch, capsys):
    server = FakeGitLab(hooks=[hook()], triggers=[trigger()])
    assert run(monkeypatch, server) == 0

    assert server.writes == []
    assert "webhook  already correct (id 58)" in capsys.readouterr().out


def test_a_changed_default_branch_is_repaired(monkeypatch):
    server = FakeGitLab(hooks=[hook()], triggers=[trigger()])
    assert run(monkeypatch, server, branch="trunk") == 0
    assert server.writes[0][2]["url"].count("trunk") == 1


def test_a_flag_someone_turned_off_is_restored(monkeypatch):
    server = FakeGitLab(hooks=[hook(wiki_page_events=False)], triggers=[trigger()])
    assert run(monkeypatch, server) == 0
    assert server.writes[0][2] == {"wiki_page_events": "true"}


def test_an_unreadable_operator_token_is_not_a_reason_to_mint(monkeypatch, capsys):
    """GitLab shortens another user's trigger token to four characters.

    Until 1.1.1 that was read as "unusable, so make my own", which changed the
    identity a wiki edit's pipeline runs as and broke delivery. The value in the
    hook URL is readable whoever owns it, so the shortened listing no longer
    decides anything.
    """
    operators = trigger(trigger_id=4, owner=OPERATOR, token="6d05")
    stale = f"https://stale.example/projects/{PROJECT}/ref/main/trigger/pipeline?token={FOREIGN}"
    server = FakeGitLab(hooks=[hook(url=stale)], triggers=[operators])
    assert run(monkeypatch, server) == 0

    assert [method for method, _, _ in server.writes] == ["PUT"]
    assert server.writes[0][2]["url"].endswith(FOREIGN)
    assert "6d05" not in server.writes[0][2]["url"]
    assert "belongs to root" not in capsys.readouterr().out


def test_a_token_that_cannot_manage_hooks_reports_and_does_not_fail(monkeypatch, capsys):
    server = FakeGitLab(statuses={f"GET /projects/{PROJECT}/hooks": 403})
    assert run(monkeypatch, server) == 0

    assert server.writes == []
    output = capsys.readouterr().out
    assert "not reconciled" in output
    assert "`api` scope" in output
    assert "webhook-reconcile: off" in output


def test_an_api_failure_after_the_probe_fails_the_job(monkeypatch, capsys):
    server = FakeGitLab(hooks=[hook(url="https://stale/x")], statuses={"POST": 500})
    assert run(monkeypatch, server) == 1
    assert "HTTP 500" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["CI_API_V4_URL", "CI_PROJECT_ID", "CI_DEFAULT_BRANCH"])
def test_a_missing_job_variable_fails_loudly(monkeypatch, capsys, missing):
    server = FakeGitLab()
    FakeGitLab.install(server, monkeypatch)
    for name, value in (
        ("CI_API_V4_URL", API),
        ("CI_PROJECT_ID", PROJECT),
        ("CI_DEFAULT_BRANCH", "main"),
    ):
        monkeypatch.setenv(name, "" if name == missing else value)
    monkeypatch.setenv("WIKI_TOKEN", "a-token-value")
    assert reconcile.main([]) == 1
    assert missing in capsys.readouterr().err


def test_no_token_at_all_fails_rather_than_calling_the_api(monkeypatch, capsys):
    server = FakeGitLab()
    FakeGitLab.install(server, monkeypatch)
    monkeypatch.setenv("CI_API_V4_URL", API)
    monkeypatch.setenv("CI_PROJECT_ID", PROJECT)
    monkeypatch.setenv("CI_DEFAULT_BRANCH", "main")
    monkeypatch.delenv("WIKI_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WIKI_TOKEN", raising=False)
    assert reconcile.main([]) == 1
    assert server.writes == []
    assert "WIKI_ADMIN_TOKEN or WIKI_TOKEN" in capsys.readouterr().err


def test_the_admin_token_wins_over_the_sync_token(monkeypatch):
    server = FakeGitLab(hooks=[hook()], triggers=[trigger()])
    seen = []
    original = server.request

    def record(method, url, *, headers=None, body=None, timeout=30):
        seen.append(headers["PRIVATE-TOKEN"])
        return original(method, url, headers=headers, body=body, timeout=timeout)

    monkeypatch.setattr(reconcile, "request", record)
    monkeypatch.setenv("CI_API_V4_URL", API)
    monkeypatch.setenv("CI_PROJECT_ID", PROJECT)
    monkeypatch.setenv("CI_DEFAULT_BRANCH", "main")
    monkeypatch.setenv("WIKI_TOKEN", "narrow")
    monkeypatch.setenv("WIKI_ADMIN_TOKEN", "wide")
    assert reconcile.main([]) == 0
    assert set(seen) == {"wide"}


def test_no_output_line_ever_carries_a_trigger_token(monkeypatch, capsys):
    """The hook URL holds the token in clear, so every printed URL is elided."""
    server = FakeGitLab(hooks=[hook(url=f"{OLD_API}/x?token={OUR_TOKEN}")], triggers=[trigger()])
    assert run(monkeypatch, server) == 0
    captured = capsys.readouterr()
    assert OUR_TOKEN not in captured.out + captured.err
    assert "token=<token>" in captured.out


def test_elide_token_leaves_a_url_without_one_alone():
    assert reconcile.elide_token("https://example.com/hook") == "https://example.com/hook"
    assert reconcile.elide_token("https://example.com/hook?token=abc") == (
        "https://example.com/hook?token=<token>"
    )
