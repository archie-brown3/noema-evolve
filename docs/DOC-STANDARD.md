# Documentation Standard

All documentation that the agent swarm writes must follow this standard.
It combines three sources: **ASD-STE100 Simplified Technical English** for the
prose, the **claude-brain vault conventions** for structure, and a
**human-readability** layer on top.

## 1. Write in Simplified Technical English (ASD-STE100)

ASD-STE100 is a controlled English standard. It removes ambiguity so that a
reader — human or agent — understands a sentence one way only.

Core rules the swarm must apply:

1. **One instruction per sentence.** For a procedure, write one command per
   sentence. For a description, write one idea per sentence.
2. **Keep sentences short.** Procedural sentences: 20 words maximum.
   Descriptive sentences: 25 words maximum.
3. **Use the active voice.** Write "The controller starts the loop", not
   "The loop is started by the controller".
4. **Use the imperative for instructions.** Write "Run the tests", not
   "The tests should be run" or "You may want to run the tests".
5. **Use simple present or simple past tense.** Avoid the future and the
   perfect tenses when a simpler tense works.
6. **Use approved words with one meaning.** Prefer a common word with a single
   meaning. Do not use a word as two parts of speech. For example, use "the
   test" (noun) and "check the result" (verb) — do not write "to test the
   test".
7. **Keep articles and connectors.** Write "the evaluator returns a score", not
   "evaluator returns score". Keep "that" and "which".
8. **Use consistent terminology.** Name a thing the same way every time. Do not
   call it a "worker" in one paragraph and an "agent" in the next unless they
   differ.
9. **Avoid clusters of nouns.** Break long noun strings apart. Write "the
   budget for each run", not "the per-run budget allocation figure".
10. **Do not use -ing verbs as the main verb.** Write "The script creates a
    branch", not "The script is creating a branch".

## 2. Structure like a vault note

- **Frontmatter** at the top: `title`, `updated` (UTC ISO-8601, e.g.
  `2026-07-21T00:00:00Z`), and `tags`.
- **Atomic:** one document covers one topic. Split a document that grows past
  one topic.
- **Linked:** link to related documents. Every document links out to at least
  one other, and something links back to it. Do not leave a document with no
  links.
- **Never delete:** to replace a document, add a note at the top that points to
  the new one.

## 3. Make it human-readable

- Start with a one-sentence statement of what the document is for.
- Use short sections with clear headings a reader can scan.
- Show a concrete example or command for each claim that needs one.
- Put the most useful information first. A reader who stops after the first
  screen must still get the main point.

## Self-check before finishing

1. Is every procedural sentence 20 words or fewer?
2. Is every sentence active and one idea?
3. Does the document link out, and is it linked in?
4. Can a new reader act on it after one read?

If any answer is "no", fix it before you finish.
