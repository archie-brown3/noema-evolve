# noema-evolve

Controlled ablation of **coordination mechanisms** in LLM-driven evolutionary search.
MSc dissertation project, built on a fork of
[OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) (an open-source
implementation of DeepMind's AlphaEvolve).

## The idea

LLM-driven evolutionary search systems (AlphaEvolve, FunSearch, EoH, HiFo-Prompt,
ShinkaEvolve, A2DEPT) mix two kinds of machinery:

- **Substrate** — the evolution engine: population database, evaluator, mutation
  prompts, parsing. Held constant across experiment arms.
- **Coordination mechanism** — anything that observes search state and steers it:
  injecting guidance into prompts, or hinting at selection decisions.

The study asks: *does coordination actually help, and which kind, at equal token
spend?* Answering that cleanly requires arms that are identical except for the
mechanism — which the frameworks above cannot provide, because coordination is
entangled with their substrates.

**noema** provides that isolation. It owns the top-level evolution loop
([`noema/controller.py`](noema/controller.py)) and borrows OpenEvolve's evaluator,
program database, and prompt sampler as libraries behind thin adapters
([`noema/substrate/`](noema/substrate/) — the only package that touches openevolve
internals). Coordination mechanisms are pluggable modules behind one interface
([`noema/coordination/base.py`](noema/coordination/base.py)); the arm is selected by
config alone. Every LLM call — mutation and coordination alike — is metered against a
shared token budget ([`noema/budget/`](noema/budget/)), so arms are compared at equal
spend and coordination's token cost is part of the experiment.

Design rationale, OpenEvolve component audit, and the full task list: [PLAN.md](PLAN.md).

## Guarantees

Enforced by tests (`tests/test_noema_*.py`), not convention:

- **Prompt identity** — template stochasticity off, coordination advice injected only
  as a delimited suffix; the shared prompt prefix is byte-identical across arms.
- **Exact metering** — the ledger's clients are the only LLM API clients in a run;
  components that would make unmetered calls (novelty embeddings, LLM feedback,
  cascade evaluation) are hard-rejected. Per-attempt usage logged to `llm_calls.jsonl`.
- **Determinism** — deterministic program IDs, separate RNG stream for coordination,
  checkpoint/resume restores DB + ledger + coordination state + RNG streams.

## Arms

| Arm | Module | Status |
|---|---|---|
| Coordination OFF | `null` | built |
| HiFo-Prompt (insight pool + navigator) | `hifo` | built, tested; live pilot pending |
| Selection-level steering (UCB bandit / softmax scheduler over diff-vs-rewrite) | — | under assessment |

## Quick start

```bash
pip install -e ".[dev]"
python -m unittest discover tests
```

Running an arm, configuration, and checkpoint/resume: see
[`noema/README.md`](noema/README.md).

## Repository layout

```
noema/            the framework (controller, budget ledger, coordination modules, substrate adapters)
openevolve/       upstream OpenEvolve, used as a library (pinned; see PLAN.md for the audited commit)
PLAN.md           design document: audit, architecture, task list
tests/            main suite; noema tests are tests/test_noema_*.py
```

## Provenance and licensing

- OpenEvolve is Apache-2.0; this repository preserves its [LICENSE](LICENSE). The
  upstream README is available in the upstream repo and this repo's history.
- `noema/coordination/hifo/` contains code borrowed from
  [HiFo-Prompt](https://github.com/Challenger-XJTU/HiFo-Prompt) (MIT), with provenance
  headers and every local change marked `NOEMA:`.
