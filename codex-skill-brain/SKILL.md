---
name: brain
description: Interact with Archie's /root/claude-brain Obsidian vault as persistent project memory. Use when the user mentions /brain, claude-brain, the vault, the knowledge graph, tasks, remembering, saving project context, planning vault tasks, or retrieving project context.
---

# Brain

Use `/root/claude-brain` as the only project vault. It contains the task queue
and the connected knowledge graph for `noema-evolve`.

## Entry protocol

Before any vault work:

1. Read `/root/claude-brain/VAULT-CONTEXT.md`.
2. Read `/root/claude-brain/INDEX.md`; treat its `Now` list as ordered priority.
3. Read only the specific task or graph notes needed for the request. Do not
   scan the whole vault.

## Vault layout

- `INDEX.md`: task priority index.
- `tasks/`: atomic work items; completed tasks move to `tasks/done/`, never delete.
- `knowledge/`: durable graph notes. Put dated measurements and run analyses in
  `knowledge/evidence/`.
- `ops/`: infrastructure and operating runbooks.
- `attachments/`: images and other note attachments.
- `.vault-loop/log.md`: append-only activity log used by the autonomous loop.

Something to be done belongs in `tasks/`; something to be known belongs in the
knowledge graph; infrastructure procedures belong in `ops/`.

## Read and search

Use read-only shell tools when no vault MCP is available:

- `sed -n` or `less` to read notes with bounded output.
- `rg` to search filenames and note contents.
- `find` to list targeted folders.

Never infer note contents. Read the current file before relying on it.

## Writing notes

Use `apply_patch` for focused edits when possible. For writes outside the
workspace, request the required permission before copying the prepared file.
Avoid modifying raw experiment data (`examples/*/runs/`, checkpoints, and
`llm_calls.jsonl`) while documenting it.

For a knowledge note:

- Place it under the appropriate `knowledge/` subfolder; evidence goes in
  `knowledge/evidence/`.
- Use UTC ISO-8601 dates/timestamps.
- Search first to avoid duplicate notes.
- Link outward to related notes with `[[wikilinks]]`.
- Ensure the note is discoverable from an existing hub, task, or index entry.
- Store images in `attachments/` and embed them as `![[filename]]`.

For tasks, preserve the existing frontmatter and template. Keep at most one
task `status: in-progress`; do not mark work done without running its stated
verification. Do not rewrite `INDEX.md` unless task priority or task state
actually changed.

## Safety and scope

- Never delete vault content.
- Do not start live LLM runs or alter experiment data merely because a task or
  note mentions them.
- For analysis, read logs and artifacts read-only and state when the vault or
  run data may be stale.
- Record findings in an evidence note when the user asks to document results.
- When a graph is requested, generate the image in the repo or workspace first,
  then copy it to the vault's `attachments/` only with permission.

## Fallback tooling

If present, use the vault's validation script after writes:

`/root/claude-brain/scripts/vault_check.py /root/claude-brain`

If that script is absent, verify the written paths and links directly and say
that the linter was unavailable.
