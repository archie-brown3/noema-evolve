---
title: Agent Swarm
updated: 2026-07-21T00:00:00Z
tags: [tooling, agents, automation]
---

# Agent Swarm

This document describes the automated agent swarm for the `noema` repository.
The swarm turns a GitHub issue into a reviewed pull request. A human merges it.
See [DOC-STANDARD.md](DOC-STANDARD.md) for the writing rules the swarm follows.

## What it does

1. You write a GitHub issue with the "Agent task" template.
2. You add the `agent:ready` label. This label is the approval gate.
3. The swarm creates an isolated git worktree and a branch.
4. A worker agent edits files inside the worktree.
5. The script commits, pushes, and opens a pull request that closes the issue.
6. A separate reviewer agent reads the diff and posts a review comment.
7. You read the review, check CI, and merge.

## Design rules

The design follows three ownership rules:

- **GitHub owns task state.** Issues are the queue. Labels are the state
  machine. The states are `agent:ready`, `agent:implementing`,
  `agent:reviewing`, `agent:blocked`, `agent:needs-human`, and `agent:done`.
- **Git owns code state.** Each issue gets its own worktree and branch
  (`agent/issue-<n>-<slug>`). Branches keep the work isolated.
- **The script owns the remote.** The worker agent edits files only. The script
  runs every git, push, and pull-request command. This split means an
  unattended worker cannot touch the remote, the secrets, or the merge gate.

## Roles and models

The swarm uses Claude Code (`claude -p`) as the worker for every role. Each
role has a separate prompt in `swarm/prompts/`. The swarm gives high-level
judgment to a strong model and clearly-defined coding to a cheap model.

| Role | Task | Model (default) |
| --- | --- | --- |
| **Coordinator** | Read the issue. Write a plan, or escalate. | `claude-fable-5` |
| **Implementer** | Edit code and tests for a clear spec. | `claude-sonnet-5` |
| **Documentation** | Write Markdown to the doc standard. | `claude-fable-5` |
| **Reviewer** | Read the diff. Post a verdict. | `claude-fable-5` |

- **Coordinator** reads the issue and the repository first. It posts a bounded
  plan as an issue comment. It escalates an ambiguous or risky issue to
  `agent:needs-human` and stops. The coordinator never edits code.
- **Implementer** receives the coordinator plan and edits code and tests.
- **Documentation** worker writes Markdown that follows the documentation
  standard. The swarm selects this role for issues labelled
  `type:documentation`.
- **Reviewer** reads the diff, checks it against the criteria, and posts a
  verdict. The reviewer never approves the pull request. A human merges.

Set the models with `SWARM_MODEL_STRONG` and `SWARM_MODEL_CODE`.

## Safety

- The `agent:ready` label is a human approval gate. Issue creation alone starts
  nothing.
- The worker cannot commit, push, or run `gh`. It only edits files.
- Each run has a dollar budget (`--max-budget-usd`, default 3).
- Issue text is data, not instructions. The prompts tell each agent to ignore
  instructions in the issue or diff that try to change these rules.
- A human merges every pull request. Continuous integration must pass first.

## Usage

```bash
./swarm/swarm.sh setup            # create the agent:* labels (run once)
./swarm/swarm.sh run 12           # process issue #12 (must be agent:ready)
./swarm/swarm.sh watch 300        # poll agent:ready issues every 300 seconds
```

Configure with environment variables: `SWARM_MODEL_STRONG`, `SWARM_MODEL_CODE`,
`SWARM_BUDGET_USD`, `SWARM_BASE` (base branch), and `SWARM_WORKSPACES` (worktree
location).

## What this is not

This swarm is deliberately small. It has no server, no webhook, no queue
service, and no container runtime. It runs from one shell script on one
machine. Add those parts only when a real need appears, such as multiple users
or untrusted input. The staged path is:

- **Stage 1 (this):** local script, cron poll, human merge.
- **Stage 2:** per-run event logs, retry, and context retrieval.
- **Stage 3:** a hosted webhook service, parallel workers, and a dashboard.
