# Trace-only EvoReplay export

## Goal

Export a completed Noema run that retained its replay traces but intentionally
discarded resumable checkpoints, without changing the existing checkpoint export.

## Source contract

`attempt_trace.jsonl` is authoritative for program code: every accepted record
contains a parent program and a candidate `{id, code}`. `evolution_trace.jsonl`
supplies metrics and generation metadata by `child_id`. `selection_trace.jsonl`,
`llm_calls.jsonl`, `run.log`, and redacted `config.yaml` remain immutable
sidecars.

## Design

`export_run()` will retain its current checkpoint path whenever checkpoints are
present. When no checkpoints exist but an attempt trace does, it will reconstruct
unique program rows from accepted attempts: add the parent program, then form the
candidate program by combining its id/code with the matching evolution-trace
child metrics and parent id. Program code is blobbed through the existing
`_program_row()` path. It will derive a minimal iteration membership row for an
accepted candidate and retain all attempts/selections verbatim.

If neither checkpoints nor an attempt trace exists, export continues to fail with
a clear `FileNotFoundError`. The implementation will not infer coordination
events or change experiment data.

## Acceptance criteria

- A trace-only fixture exports programs, code blobs, attempts, selections, and
  redacted config.
- Checkpoint-backed export remains byte-identical.
- `bb3a147` Phase C inputs can be exported from Git-extracted raw directories.
- D6 is computed from authoritative evolution/attempt/selection/log traces,
  while explicitly treating one seed per cell as descriptive evidence.
