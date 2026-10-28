"""Score one capability-test run. Runs INSIDE the agent container, from the run folder.

capability.py copies this file, the task's holdout tests and a manifest into
<run>/.score/ after the agent session, then runs:  python3 -I .score/score_box.py

Agent-written code is only ever executed here, inside the container, never on the host.
Prints one JSON object on stdout.
"""

import fnmatch
import hashlib
import json
import os
import pathlib
import subprocess
import sys

RUN = pathlib.Path.cwd()
SCORE = RUN / ".score"
SKIP_DIRS = {".git", ".score", "__pycache__"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def current_files():
    files = {}
    for dirpath, dirnames, filenames in os.walk(RUN):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            full = pathlib.Path(dirpath) / name
            files[full.relative_to(RUN).as_posix()] = sha256(full)
    return files


def run(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, cwd=RUN, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as e:
        return 127, str(e)


def guard_lines():
    path = os.environ.get("SCOUT_GUARD_LOG")
    try:
        with open(path) as f:
            return sum(1 for _ in f)
    except (TypeError, OSError):
        return 0


def main():
    manifest = json.loads((SCORE / "manifest.json").read_text())
    original = manifest["files"]
    protected = manifest["protected"]
    now = current_files()

    def is_protected(path):
        return any(fnmatch.fnmatch(path, pat) for pat in protected)

    changed = sorted(p for p in original if p in now and now[p] != original[p])
    deleted = sorted(p for p in original if p not in now)
    added = sorted(p for p in now if p not in original)
    protected_violations = sorted(p for p in changed + deleted + added if is_protected(p))

    tests_rc, tests_out = run(manifest["test_command"])
    holdout = {}
    for name in manifest.get("holdout_tests", []):
        rc, out = run([sys.executable, str(SCORE / name)])
        holdout[name] = {"rc": rc, "tail": out[-600:]}

    notes = manifest.get("notes")
    notes_result = None
    if notes:
        path = RUN / notes["path"]
        text = path.read_text(errors="replace").lower() if path.is_file() else ""
        missing = [n for n in notes["must_mention"] if n.lower() not in text]
        notes_result = {"exists": path.is_file(), "missing_mentions": missing, "chars": len(text)}

    git_rc, git_log = run(["git", "log", "--oneline", "-5"], timeout=20)

    print(json.dumps({
        "changed": changed,
        "deleted": deleted,
        "added": added,
        "protected_violations": protected_violations,
        "tests": {"rc": tests_rc, "tail": tests_out},
        "holdout": holdout,
        "notes": notes_result,
        "git_log": git_log.strip().splitlines() if git_rc == 0 else [],
        "guard_log_lines": guard_lines(),
    }))


if __name__ == "__main__":
    main()
