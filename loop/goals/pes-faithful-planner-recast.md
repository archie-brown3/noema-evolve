predicate: cd /root/noema-evolve && python3 -m unittest tests.test_noema_prompts 2>/dev/null && grep -q 'FAITHFUL_PLANNER_SYSTEM' noema/coordination/pes/planner.py && grep -q 'def extract_final_plan' noema/coordination/pes/planner.py && grep -q 'prompt_variant: str = self.config.get("prompt_variant", "custom")' noema/coordination/pes/module.py
born: 2026-07-11
source: vault task 0063 (WP4 of the pes-faithful plan) — LoongFlow math-agent planner prompt ported near-verbatim (evolve_plan_prompt.py, Apache-2.0) as one completion behind prompt_variant="faithful" (default "custom" = byte-identical, sha256-pinned); host extracts the last `### Final Child Solution Generation Plan` slice with a logged fallback; island status block live at advise() time (deferred 0061 verifier condition discharged); provider exceptions fail loud. Fresh-context verifier PASS 2026-07-11; SHOULD-FIX findings (explicit max_tokens floor, frozen-hash pin, empty-slice warning) applied and pinned.
status: satisfied
last-pass: 2026-07-11
on-violation: prompt identity is guarantee triad #1 — the custom arm's prompt bytes drifting, or the faithful extraction feeding outlines to the executor, invalidates the arm comparison. Wake me.
retire-when: absorbed into the standing prompt-identity goal after the pes-faithful shakedown (RT-0003) validates planner-format compliance live. Human decision, logged.
