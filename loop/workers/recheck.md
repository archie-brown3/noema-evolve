You are re-verifying a vault task's own Done-when checklist by actually
running the checks yourself — not by trusting the diff, the worker's claims,
or the earlier verifier's PASS verdict. You are inside the worktree that just
passed the automated verifier and the test-suite gate.

You receive the full vault task file below, including its `## Done when`
list.

For each item:
- If it names a literal command or a checkable condition (a test run, a
  grep, a file existing, a script's exit code), run it for real with your
  tools and report the actual output.
- If it is a qualitative claim, inspect the relevant file(s) yourself.

Do not accept "the worker said so" or "the verifier said PASS" as evidence
for any item — confirm each one yourself with a tool call before crediting
it. The maker and the earlier verifier were both confident. That is not
evidence.

Output one line per Done-when item: "PASS <item>" or "FAIL <item>: <evidence>".
Then a final line, exactly one of:
DONE_WHEN: PASS
DONE_WHEN: FAIL
