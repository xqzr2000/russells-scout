# Agent tasks

The agent's work queue ([phase3-plan.md](../../docs/phase3-plan.md) §2.5). One file per task,
named `NNNN-short-name.md`. Claude adds and closes tasks in PRs; the agent reads them from
`/src/main/agent/tasks/` and can't change them.

`agent/session.sh` starts a session when a task has `status: open`. Without `--task` it picks
the lowest-numbered open task.

Each file starts with these lines:

```
status: open            # open | done
branch: agent/NNNN-short-name
```

Then:
- **Goal:** what the task should achieve.
- **Done when:** what has to be true on the branch.
- **Hints:** where to look, what to avoid.
- **Allowed paths:** only if the task widens the agent's usual list.
