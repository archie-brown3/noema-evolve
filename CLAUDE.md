# CLAUDE.md

noema: controlled ablation of coordination mechanisms in LLM-driven evolutionary
search (MSc dissertation). Task queue + knowledge graph: the Obsidian vault at
`/root/claude-brain` (read `INDEX.md` first). Loop machinery: `loop/`.

## NEVER (laws; exceptions require asking first)
- Never modify `noema/coordination/base.py` without asking. Interface additions are
  sanctioned one at a time, only when a second mechanism needs them.
- Never touch experiment data: `examples/*/noema_*output*`, `examples/*/openevolve_output*`,
  any `checkpoints/` dir, any `llm_calls.jsonl`. Run dirs are the study's raw data.
- Never start a live LLM run (real provider or local inference node) unattended.
  Live runs queue for the user — they spend the study's tokens and compute.
- Never edit, delete, or weaken a test to make it pass. That is a fail, always.
- Never change code affecting the guarantee triad (prompt identity, metering,
  determinism) without extending the corresponding test file in the same commit:
  `tests/test_noema_prompts.py`, `tests/test_noema_budget_ledger.py` /
  `test_noema_budgeted_llm.py`, `tests/test_noema_controller.py`.
- Never add a dependency. Propose it in `loop/memory/STATE.md` and stop.
- Never exceed 200 changed lines in one commit without asking.
- Never exceed effort high inside any loop. xhigh is for one-shot reviews only.
- Never report work as done from your own assessment. Done = the check passed.
- Never invent a secret, an endpoint, or a convention. Stop and ask.
- Never echo, transcribe, or explain your internal reasoning in response text.
- Never delete vault files. Superseded notes get an `> archived:` banner (vault rule).
- When a task's done-when passes, write `loop/goals/<name>.md` with the condition as
  its predicate before reporting success.

## DISPATCH (route every seat; log to loop/memory/dispatch.tsv)
| seat        | model          | effort  | tools                          |
|-------------|----------------|---------|--------------------------------|
| triage      | haiku          | default | none (bash assembles input)    |
| conductor   | claude-fable-5 | high    | Read only                      |
| worker      | sonnet         | medium  | Read,Edit,Write,scoped Bash    |
| verifier    | claude-fable-5 | high    | none (sees only spec + diff)   |
| gate        | loop/guardrails/verify.sh | — | deterministic, final vote |
1. Decisions (plan/review/route/standoff) -> fable-5, effort high, read-only.
2. Reads >50k tokens (run logs, JSONL dumps) -> haiku summarizes first. Never fable.
3. Spec complete -> sonnet, effort medium. Escalate one rung on a miss without asking.
4. Maker and checker disagree twice -> stop, queue for the user.

## WORDS
- "done" = the predicate passes; nothing else
- "small" = under 50 changed lines; "quick" = under 10 minutes
- "cleanup" = behavior identical, `loop/guardrails/verify.sh` green before and after
- "arm" = a coordination module config (`null` | `hifo` | `pes`); arms differ ONLY
  in `coordination.module`
- "guarantee triad" = prompt identity, metering integrity, determinism — the three
  properties the test suite enforces and the study's validity rests on
- "live run" = any invocation that makes real LLM calls (metered or local)

## DONE
- Every task has a machine-checkable done_when before work starts (vault task
  format: the "Done when" checklist).
- A fresh-context agent that saw neither plan nor draft verifies against it.
- `loop/guardrails/verify.sh` has the final vote.
- Deviations: take the conservative option, log to IMPLEMENTATION.md, continue.
- Completed vault tasks move to `tasks/done/`, INDEX.md is updated, one line goes
  to `.vault-loop/log.md` (vault-loop conventions).
