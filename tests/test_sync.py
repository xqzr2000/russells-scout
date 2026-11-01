"""Tests for the WSL2 sync (task 2.6): ops/sync/sync-gate.sh, add-sync-key.sh, pull-from-box.sh.

A stand-in for ssh hands the remote command to the gate exactly as sshd does with a forced
command (as SSH_ORIGINAL_COMMAND), so real rsync and rrsync run end to end. Skipped where
rsync or rrsync isn't installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parent.parent / "ops" / "sync"
GATE, ADD_KEY, PULL = OPS / "sync-gate.sh", OPS / "add-sync-key.sh", OPS / "pull-from-box.sh"

needs_rsync = pytest.mark.skipif(
    not (shutil.which("rsync") and shutil.which("rrsync")), reason="needs rsync and rrsync"
)
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq7Zg0n1pJ2bQf3KxY0s9mVt0cF8yX1sZQ7mR2wEo4p scout-sync"


@pytest.fixture
def box(tmp_path):
    """A fake /srv/scout with backups, digests and a secret next to them."""
    home = tmp_path / "srv-scout"
    (home / "backups").mkdir(parents=True)
    (home / "backups" / "scout-20261101-033000.dump").write_bytes(b"PGDMP" + b"x" * 100)
    (home / "backups" / "status").write_text("result=ok\n")
    (home / "logs" / "digests").mkdir(parents=True)
    (home / "logs" / "digests" / "latest.txt").write_text("Scout: 3 new jobs\n")
    (home / "secrets").mkdir()
    (home / "secrets" / "db-roles.env").write_text("SCOUT_READER_PASSWORD=secret\n")
    fake_ssh = tmp_path / "fake-ssh"
    fake_ssh.write_text(
        "#!/usr/bin/env bash\n"
        "# like sshd with a forced command: the requested command goes in SSH_ORIGINAL_COMMAND\n"
        "while [[ $1 == -* ]]; do shift; [[ $1 != -* ]] && shift; done\n"
        "shift  # the host\n"
        f'SCOUT_HOME={home} SSH_ORIGINAL_COMMAND="$*" exec bash {GATE}\n'
    )
    fake_ssh.chmod(0o755)
    return home, fake_ssh


def gate(box, command: str | None):
    home, _ = box
    env = {**os.environ, "SCOUT_HOME": str(home)}
    env.pop("SSH_ORIGINAL_COMMAND", None)
    if command is not None:
        env["SSH_ORIGINAL_COMMAND"] = command
    return subprocess.run(
        ["bash", str(GATE)], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def rsync(box, *args):
    _, fake_ssh = box
    return subprocess.run(
        ["rsync", "-rt", "-e", str(fake_ssh), *args],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip


@needs_rsync
def test_downloads_backups_and_digests_and_stamps(box, tmp_path):
    home, _ = box
    out = tmp_path / "mirror"
    out.mkdir()
    assert rsync(box, "box:backups/", str(out / "backups")).returncode == 0
    r = rsync(box, "box:digests/", str(out / "digests"))
    assert r.returncode == 0, r.stderr
    assert (out / "backups" / "scout-20261101-033000.dump").read_bytes().startswith(b"PGDMP")
    assert (out / "digests" / "latest.txt").read_text() == "Scout: 3 new jobs\n"
    stamp = home / "logs" / "sync" / "last-sync"
    assert stamp.read_text().strip()[:2] == "20" and oct(stamp.stat().st_mode & 0o777) == "0o644"


@needs_rsync
@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("box:secrets/", "x"), "unknown folder"),
        (("box:../secrets/", "x"), "unknown folder"),
        (("box:backups/../secrets/", "x"), "unknown folder"),
        (("box:/etc/", "x"), "unknown folder"),
        (("box:backups/", "box:digests/", "x"), "one folder at a time"),
    ],
)
def test_other_downloads_are_refused(box, tmp_path, args, why):
    args = tuple(str(tmp_path / a) if a == "x" else a for a in args)
    r = rsync(box, *args)
    assert r.returncode != 0
    assert why in r.stderr
    assert not (tmp_path / "x" / "db-roles.env").exists()
    assert not (box[0] / "logs" / "sync" / "last-sync").exists()


@needs_rsync
def test_uploads_are_refused(box, tmp_path):
    src = tmp_path / "evil.txt"
    src.write_text("x")
    r = rsync(box, str(src), "box:backups/")
    assert r.returncode != 0 and "not an rsync download" in r.stderr
    assert not (box[0] / "backups" / "evil.txt").exists()


@pytest.mark.parametrize(
    ("command", "why"),
    [
        (None, "no shell for this key"),
        ("bash", "not an rsync download"),
        ("cat /srv/scout/secrets/db-roles.env", "not an rsync download"),
        ("rsync --server -tre.iLsfxCIvu . backups/", "not an rsync download"),
        ("rsync --server --sender", "incomplete rsync command"),
        ("rsync --server --sender -tre.iLsfxCIvu . backups/; cat /etc/passwd", "refused"),
    ],
)
def test_anything_but_rsync_is_refused(box, command, why):
    r = gate(box, command)
    assert r.returncode == 1
    assert why in r.stderr
    assert r.stdout == ""


@needs_rsync
def test_pull_script_end_to_end(box, tmp_path):
    _, fake_ssh = box
    mirror = tmp_path / "scout-mirror"
    env = {**os.environ, "SCOUT_MIRROR": str(mirror), "SCOUT_SYNC_HOST": "box",
           "SCOUT_SYNC_SSH": str(fake_ssh)}  # fmt: skip
    r = subprocess.run(["bash", str(PULL)], env=env, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "backups: ok" in r.stdout and "digests: ok" in r.stdout
    assert (mirror / "digests" / "latest.txt").exists()
    # Something deleted on the box stays in the mirror.
    (box[0] / "backups" / "scout-20261101-033000.dump").unlink()
    subprocess.run(["bash", str(PULL)], env=env, capture_output=True, text=True, check=True)
    assert (mirror / "backups" / "scout-20261101-033000.dump").exists()


def test_pull_script_reports_failure(tmp_path):
    env = {**os.environ, "SCOUT_MIRROR": str(tmp_path / "m"), "SCOUT_SYNC_HOST": "box",
           "SCOUT_SYNC_SSH": "false"}  # fmt: skip
    r = subprocess.run(["bash", str(PULL)], env=env, capture_output=True, text=True, check=False)
    assert r.returncode == 1
    assert "backups: FAILED" in r.stdout and "digests: FAILED" in r.stdout


def add_key(tmp_path, *args):
    env = {**os.environ, "SCOUT_AUTHORIZED_KEYS": str(tmp_path / "ssh" / "authorized_keys"),
           "SCOUT_HOME": str(tmp_path / "srv")}  # fmt: skip
    return subprocess.run(
        ["bash", str(ADD_KEY), *args], env=env, capture_output=True, text=True, check=False
    )


def test_add_key_locks_it_to_the_gate(tmp_path):
    ak = tmp_path / "ssh" / "authorized_keys"
    ak.parent.mkdir()
    ak.write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAInormalkeyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx me@wsl\n"
    )
    r = add_key(tmp_path, KEY)
    assert r.returncode == 0, r.stderr
    lines = ak.read_text().splitlines()
    assert lines[0].endswith("me@wsl")  # the normal key is untouched
    assert lines[1] == (
        f'command="{GATE}",restrict ssh-ed25519 '
        "AAAAC3NzaC1lZDI1NTE5AAAAIGq7Zg0n1pJ2bQf3KxY0s9mVt0cF8yX1sZQ7mR2wEo4p scout-sync"
    )
    assert oct(ak.stat().st_mode & 0o777) == "0o600"
    assert (tmp_path / "srv" / "logs" / "sync").is_dir()
    assert add_key(tmp_path, KEY).returncode == 0  # again: replaced, not doubled
    assert len(ak.read_text().splitlines()) == 2
    assert add_key(tmp_path, "--remove").returncode == 0
    assert ak.read_text().splitlines() == lines[:1]


def test_add_key_refuses_the_normal_key_and_junk(tmp_path):
    ak = tmp_path / "ssh" / "authorized_keys"
    ak.parent.mkdir()
    blob = KEY.split()[1]
    ak.write_text(f"ssh-ed25519 {blob} me@wsl\n")
    r = add_key(tmp_path, KEY)
    assert r.returncode == 1 and "normal key" in r.stderr
    assert add_key(tmp_path, "rm -rf /").returncode == 1
    assert add_key(tmp_path, 'ssh-ed25519 AAAA" ; touch /tmp/x').returncode == 1
    assert ak.read_text() == f"ssh-ed25519 {blob} me@wsl\n"
