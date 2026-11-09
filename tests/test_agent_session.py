"""Tests for the agent session routine (task 3.1): agent/session.sh and agent/workspace-step.sh.

workspace-step.sh runs here directly, against temporary folders, as it would inside the agent
container. session.sh runs with a stand-in `docker` on PATH that answers the commands it
sends (`scout status`, the boards query, the before/after steps and the Claude Code session)
and logs each call, so the gate, the briefing and the order of steps are checked without
Docker or DeepSeek.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
SESSION, STEP = AGENT / "session.sh", AGENT / "workspace-step.sh"

needs_tools = pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("flock")), reason="needs git and flock"
)

STATUS_OK = "== postings\n  active 186\n\n== sources needing attention: none\n"
STATUS_BROKEN = "== postings\n  active 186\n\n== sources needing attention\n  workbc: structural\n"


def git(cwd, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def main_repo(tmp_path):
    """A stand-in for /src/main: a git repo with one commit on main."""
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    (main / "README.md").write_text("scout\n")
    git(main, "add", ".")
    git(main, "commit", "-q", "-m", "first")
    return main


def step(ws, main, which):
    outbox = ws.parent / "agent-outbox"
    outbox.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "SCOUT_WORKSPACE": str(ws),
        "SCOUT_SRC_MAIN": str(main),
        "SCOUT_OUTBOX": str(outbox),
    }
    return subprocess.run(
        ["bash", str(STEP), which], env=env, capture_output=True, text=True, timeout=60, check=False
    )


# ---------------------------------------------------------------- workspace-step.sh


@needs_tools
def test_before_clones_then_refreshes_main(tmp_path, main_repo):
    ws = tmp_path / "ws"
    r = step(ws, main_repo, "before")
    assert r.returncode == 0, r.stderr
    assert "cloned main" in r.stdout
    assert (ws / "notes").is_dir() and (ws / "scratch").is_dir()
    assert (ws / "repo" / "README.md").read_text() == "scout\n"

    (main_repo / "new.txt").write_text("x\n")
    git(main_repo, "add", ".")
    git(main_repo, "commit", "-q", "-m", "second")
    r = step(ws, main_repo, "before")
    assert r.returncode == 0, r.stderr
    assert git(ws / "repo", "rev-parse", "origin/main") == git(main_repo, "rev-parse", "main")


@needs_tools
@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to give the repo another owner")
def test_before_works_when_main_belongs_to_another_user(tmp_path, main_repo):
    # On the box /src/main belongs to shaw2012 and the step runs as uid 10001.
    for p in [main_repo, *main_repo.rglob("*")]:
        os.lchown(p, 65534, 65534)
    ws = tmp_path / "ws"
    for _ in range(2):  # clone, then fetch
        r = step(ws, main_repo, "before")
        assert r.returncode == 0, r.stderr
    assert (ws / "repo" / "README.md").exists()


@needs_tools
def test_before_ignores_a_changed_remote_url(tmp_path, main_repo):
    ws = tmp_path / "ws"
    step(ws, main_repo, "before")
    git(ws / "repo", "config", "remote.origin.url", "/nowhere")
    (main_repo / "new.txt").write_text("x\n")
    git(main_repo, "add", ".")
    git(main_repo, "commit", "-q", "-m", "second")
    assert step(ws, main_repo, "before").returncode == 0
    assert git(ws / "repo", "rev-parse", "origin/main") == git(main_repo, "rev-parse", "main")


@needs_tools
def test_before_refuses_a_repo_folder_that_isnt_git(tmp_path, main_repo):
    ws = tmp_path / "ws"
    (ws / "repo").mkdir(parents=True)
    r = step(ws, main_repo, "before")
    assert r.returncode == 1 and "isn't a git repo" in r.stderr


@needs_tools
def test_after_bundles_agent_branches_and_notes(tmp_path, main_repo):
    ws = tmp_path / "ws"
    step(ws, main_repo, "before")
    repo = ws / "repo"
    git(repo, "checkout", "-q", "-b", "agent/0000-orientation")
    (repo / "notes").mkdir()
    (repo / "notes" / "orientation.md").write_text("how it plugs in\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "orientation notes")
    git(repo, "branch", "scratch-branch")  # not agent/*: stays out of the bundle
    (ws / "notes" / "handoff.md").write_text("rules: scout-rules-1\n")
    (ws / ".scout-logs").mkdir()
    (ws / ".scout-logs" / "guard.log").write_text("".join(f'{{"n":{i}}}\n' for i in range(250)))

    r = step(ws, main_repo, "after")
    assert r.returncode == 0, r.stderr
    out = tmp_path / "agent-outbox"
    assert (out / "notes" / "handoff.md").read_text() == "rules: scout-rules-1\n"
    guard = (out / "guard.log").read_text().splitlines()
    assert len(guard) == 200 and guard[-1] == '{"n":249}'  # the last 200 blocks
    manifest = (out / "manifest.txt").read_text()
    assert "branch=agent/0000-orientation" in manifest and "orientation notes" in manifest
    heads = git(tmp_path, "bundle", "list-heads", str(out / "agent.bundle"))
    assert "refs/heads/agent/0000-orientation" in heads
    assert "scratch-branch" not in heads and "refs/heads/main" not in heads

    # The bundle is all a reviewer needs: fetch the branch into a fresh clone of main.
    review = tmp_path / "review"
    git(tmp_path, "clone", "-q", str(main_repo), str(review))
    git(
        review,
        "fetch",
        "-q",
        str(out / "agent.bundle"),
        "refs/heads/agent/0000-orientation:agent-branch",
    )
    assert git(review, "show", "agent-branch:notes/orientation.md") == "how it plugs in"


@needs_tools
def test_after_needs_the_outbox_mount(tmp_path, main_repo):
    ws = tmp_path / "ws"
    step(ws, main_repo, "before")
    env = {**os.environ, "SCOUT_WORKSPACE": str(ws), "SCOUT_SRC_MAIN": str(main_repo),
           "SCOUT_OUTBOX": str(tmp_path / "missing")}  # fmt: skip
    r = subprocess.run(["bash", str(STEP), "after"], env=env, capture_output=True, text=True,
                       check=False)  # fmt: skip
    assert r.returncode == 1 and "missing or not writable" in r.stderr


@needs_tools
def test_after_keeps_only_regular_note_files(tmp_path, main_repo):
    ws = tmp_path / "ws"
    step(ws, main_repo, "before")
    secret = tmp_path / "secret.env"
    secret.write_text("KEY=secret\n")
    (ws / "notes" / "link.md").symlink_to(secret)
    (ws / "notes" / "big.md").write_text("x" * 300_000)
    (ws / "notes" / "ok.md").write_text("fine\n")
    # Something left in the outbox (a link to somewhere else) is removed, not followed.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep\n")
    out = tmp_path / "agent-outbox"  # made by step()
    (out / "notes").symlink_to(elsewhere)
    (out / "old.txt").write_text("stale\n")

    assert step(ws, main_repo, "after").returncode == 0
    assert sorted(p.name for p in out.iterdir()) == ["manifest.txt", "notes"]
    assert sorted(p.name for p in (out / "notes").iterdir()) == ["ok.md"]
    assert (elsewhere / "keep.txt").read_text() == "keep\n"
    assert not (out / "agent.bundle").exists()  # no agent/* branches yet
    for p in out.rglob("*"):
        assert not p.is_symlink()


# ---------------------------------------------------------------- session.sh


@pytest.fixture
def box(tmp_path):
    """A fake /srv/scout, a tasks folder and a stand-in docker that logs every call."""
    home = tmp_path / "srv-scout"
    (home / "logs" / "digests").mkdir(parents=True)
    (home / "logs" / "digests" / "latest.txt").write_text("Scout digest: 3 new jobs\n")
    (home / "agent-outbox").mkdir()
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    # The stand-in docker must be executable. In the agent's container /tmp is a noexec
    # tmpfs (Docker's default), so these tests skip there; Claude runs them in review.
    probe = bin_ / "probe"
    probe.write_text("#!/bin/sh\nexit 0\n")
    probe.chmod(0o755)
    try:
        subprocess.run([str(probe)], check=True)
    except (PermissionError, subprocess.CalledProcessError):
        pytest.skip("can't run scripts from the temp folder (noexec /tmp)")
    calls = tmp_path / "docker-calls.log"
    docker = bin_ / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> {calls}\n'
        'case "$*" in\n'
        '  *" scout status"*) cat "$FAKE_STATUS"; exit "${FAKE_STATUS_RC:-0}" ;;\n'
        '  *"exec -T db psql"*) printf "ats,board,employer,postings,with_details,example_link\\n'
        'workday,fabrikam/careers,Fabrikam,3,0,https://fabrikam.wd3.myworkdayjobs.com/x\\n" ;;\n'
        '  *"workspace-step.sh before"*) echo "workspace: origin/main at abc1234" ;;\n'
        '  *"workspace-step.sh after"*) echo "workspace: outbox has 1 branch(es)" ;;\n'
        '  *" claude -p "*)\n'
        '    if [[ "$*" == *"rules version"* ]]; then\n'
        '      echo \'{"type":"result","subtype":"success","is_error":false,"num_turns":1,'
        '"result":"\'"${FAKE_RULES:-scout-rules-2}"\'"}\'\n'
        "    else\n"
        '      echo \'{"type":"system"}\'\n'
        '      echo \'{"type":"result","subtype":"\'"${FAKE_SUBTYPE:-success}"\'","is_error":false,'
        '"num_turns":12,"duration_ms":95000,"result":"committed notes"}\'\n'
        "    fi\n"
        '    exit "${FAKE_CLAUDE_RC:-0}" ;;\n'
        '  "rm -f "*) ;;\n'
        "esac\n"
    )
    docker.chmod(0o755)
    status = tmp_path / "status.txt"
    status.write_text(STATUS_OK)
    return {"home": home, "tasks": tasks, "bin": bin_, "calls": calls, "status": status}


def add_task(box, name, status="open"):
    (box["tasks"] / f"{name}.md").write_text(
        f"status: {status}\nbranch: agent/{name}\n\n# {name}\n"
    )


def session(box, *args, **env):
    e = {
        **os.environ,
        "SCOUT_HOME": str(box["home"]),
        "SCOUT_TASKS_DIR": str(box["tasks"]),
        "PATH": f"{box['bin']}:{os.environ['PATH']}",
        "FAKE_STATUS": str(box["status"]),
        **env,
    }
    return subprocess.run(
        ["bash", str(SESSION), *args],
        env=e,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def calls(box):
    p = box["calls"]
    return p.read_text().splitlines() if p.exists() else []


def last(box):
    return json.loads((box["home"] / "logs" / "agent" / "last-session.json").read_text())


@needs_tools
def test_nothing_to_do_skips_without_starting_the_agent(box):
    add_task(box, "0000-orientation", status="done")
    r = session(box)
    assert r.returncode == 0, r.stderr
    assert "nothing to do" in r.stdout
    assert not any("--profile agent" in c for c in calls(box))
    rec = last(box)
    assert rec["outcome"] == "skipped" and rec["task"] is None
    brief = box["home"] / "logs" / "agent-briefing"
    assert (brief / "status.txt").read_text() == STATUS_OK
    assert (brief / "digest.txt").read_text() == "Scout digest: 3 new jobs\n"
    assert "workday,fabrikam/careers,Fabrikam,3" in (brief / "boards.csv").read_text()


@needs_tools
def test_open_task_runs_before_session_after_in_order(box):
    add_task(box, "0000-orientation", status="done")
    add_task(box, "0002-second")
    add_task(box, "0001-workday")
    r = session(box)
    assert r.returncode == 0, r.stderr
    agent = [c for c in calls(box) if "--profile agent" in c]
    assert len(agent) == 3
    assert "workspace-step.sh before" in agent[0]
    assert "claude -p" in agent[1] and "Task: 0001." in agent[1] and "--max-turns 60" in agent[1]
    assert "-w /workspace" in agent[1]
    assert "workspace-step.sh after" in agent[2]
    # Only the after-step sees the outbox; the session never does.
    outbox = f"-v {box['home'] / 'agent-outbox'}:/outbox"
    assert outbox in agent[2]
    assert outbox not in agent[0] and outbox not in agent[1]
    s = (box["home"] / "logs" / "agent-briefing" / "session.txt").read_text()
    assert "task: 0001" in s and "/src/main/agent/tasks/0001-workday.md" in s
    assert "wrap_up_by_turn: 50" in s
    rec = last(box)
    assert rec["outcome"] == "ran" and rec["task"] == "0001" and rec["turns"] == 12
    assert rec["duration_s"] == 95 and rec["result"] == "committed notes"


@needs_tools
def test_source_needing_attention_starts_a_repair(box):
    box["status"].write_text(STATUS_BROKEN)
    r = session(box)
    assert r.returncode == 0, r.stderr
    assert any("Task: repair." in c for c in calls(box))
    assert last(box)["task"] == "repair"


@needs_tools
def test_failed_status_doesnt_start_a_repair(box):
    box["status"].write_text("error: database unavailable\n")
    r = session(box, FAKE_STATUS_RC="1")
    assert r.returncode == 0
    assert not any("--profile agent" in c for c in calls(box))
    assert "scout status failed" in last(box)["detail"]


@needs_tools
def test_task_by_hand_runs_even_when_done(box):
    add_task(box, "0000-orientation", status="done")
    r = session(box, "--task", "0000")
    assert r.returncode == 0, r.stderr
    assert any("Task: 0000." in c for c in calls(box))


@needs_tools
def test_unknown_task_is_refused(box):
    r = session(box, "--task", "0042")
    assert r.returncode == 64 and "no task file" in r.stderr
    assert not any("--profile agent" in c for c in calls(box))


@needs_tools
def test_turn_limit_is_recorded(box):
    add_task(box, "0001-workday")
    r = session(box, FAKE_SUBTYPE="error_max_turns", FAKE_CLAUDE_RC="1")
    assert r.returncode == 1
    assert last(box)["outcome"] == "turn_limit"
    assert any("workspace-step.sh after" in c for c in calls(box))  # outbox still refreshed


@needs_tools
def test_dry_run_writes_briefing_only(box):
    add_task(box, "0001-workday")
    r = session(box, "--dry-run")
    assert r.returncode == 0 and "open task 0001" in r.stdout
    assert not any("--profile agent" in c for c in calls(box))
    assert not (box["home"] / "logs" / "agent" / "last-session.json").exists()


@needs_tools
def test_a_second_session_waits_for_the_first(box):
    add_task(box, "0001-workday")
    lock = box["home"] / "logs" / "agent" / ".lock"
    lock.parent.mkdir(parents=True)
    holder = subprocess.Popen(["flock", str(lock), "sleep", "5"])
    try:
        for _ in range(50):  # wait until the lock is really held
            if subprocess.run(["flock", "-n", str(lock), "true"]).returncode != 0:
                break
            subprocess.run(["sleep", "0.1"])
        r = session(box)
    finally:
        holder.kill()
    assert r.returncode == 0 and "another agent session is running" in r.stdout
    assert calls(box) == []


@needs_tools
def test_pack_runs_only_the_after_step(box):
    r = session(box, "--pack")
    assert r.returncode == 0, r.stderr
    agent = [c for c in calls(box) if "--profile agent" in c]
    assert len(agent) == 1 and "workspace-step.sh after" in agent[0]


@needs_tools
def test_missing_outbox_is_reported_not_created(box):
    (box["home"] / "agent-outbox").rmdir()
    add_task(box, "0001-workday")
    r = session(box)
    assert "agent-outbox is missing" in r.stderr
    assert not any("workspace-step.sh after" in c for c in calls(box))
    assert not (box["home"] / "agent-outbox").exists()


@needs_tools
def test_check_rules(box):
    r = session(box, "--check-rules")
    assert r.returncode == 0 and "rules: PASS" in r.stdout
    r = session(box, "--check-rules", FAKE_RULES="I don't know")
    assert r.returncode == 1 and "rules: FAIL" in r.stdout


# ---------------------------------------------------------------- hooks/turns.sh

TURNS = AGENT / "managed-settings" / "hooks" / "turns.sh"


def turn(tmp_path, budget="10:6:8"):
    env = {**os.environ, "SCOUT_TURN_FILE": str(tmp_path / "count")}
    if budget is None:
        env.pop("SCOUT_TURN_BUDGET", None)
    else:
        env["SCOUT_TURN_BUDGET"] = budget
    return subprocess.run(["bash", str(TURNS)], env=env, input='{"tool_name":"Bash"}',
                          capture_output=True, text=True, check=False)  # fmt: skip


def test_turn_budget_reminds_once_then_says_stop(tmp_path):
    results = [turn(tmp_path) for _ in range(10)]
    codes = [r.returncode for r in results]
    assert codes == [0, 0, 0, 0, 0, 2, 0, 2, 2, 2]
    assert "about 6 of 10 turns used. Finish the step" in results[5].stderr
    assert "about 8 of 10 turns used. STOP NOW" in results[7].stderr
    assert "handoff.md" in results[9].stderr


def test_turn_budget_does_nothing_without_a_budget(tmp_path):
    for budget in (None, "", "junk", "60:40"):
        for _ in range(60):
            r = turn(tmp_path, budget)
            assert r.returncode == 0 and r.stderr == ""
    assert not (tmp_path / "count").exists()


@needs_tools
def test_session_passes_the_turn_budget_and_previous_outcome(box):
    add_task(box, "0001-workday")
    session(box, FAKE_SUBTYPE="error_max_turns", FAKE_CLAUDE_RC="1")
    session(box)
    agent = [c for c in calls(box) if " claude -p " in c]
    assert all("-e SCOUT_TURN_BUDGET=60:40:50" in c for c in agent)
    s = (box["home"] / "logs" / "agent-briefing" / "session.txt").read_text()
    assert "previous_session: turn_limit (task 0001," in s
