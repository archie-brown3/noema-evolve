# noema: OpenEvolve + HiFo-Prompt integration plan

**Status: review and planning only — no implementation code in this document.**

noema is a research framework for controlled ablation of coordination mechanisms in
LLM-driven evolutionary search. The architecture is a transplant: noema owns the
top-level controller loop, borrows OpenEvolve's evaluator and program database as
libraries, and runs coordination mechanisms (first: HiFo-Prompt's insight pool +
navigator) as pluggable modules behind a common interface. All LLM calls — mutation
and coordination alike — are metered by a shared token budget ledger.

Repos audited:

- `openevolve` @ `80945ed` ("Fix bugs (#442)")
- `hifo-prompt` @ HEAD of https://github.com/Challenger-XJTU/HiFo-Prompt (code under `hifo/src/hifo/`)

---

## Part 1: OpenEvolve component audit

### 1.1 Evaluator ("sandbox")

**Where it lives:** `openevolve/evaluator.py` (single class `Evaluator`, ~730 lines), plus
`openevolve/evaluation_result.py` (`EvaluationResult` dataclass: `metrics: Dict[str, float]` +
`artifacts: Dict[str, str|bytes]`).

**Entry points:**

- `Evaluator.__init__(config, evaluation_file, llm_ensemble=None, prompt_sampler=None, database=None, suffix=".py")`
  (`evaluator.py:40`) — loads the user's evaluation script via `importlib` at construction
  time (`_load_evaluation_function`, `evaluator.py:67`) and requires it to define
  `evaluate(program_path)` (optionally `evaluate_stage1/2/3` for cascade).
- `await evaluator.evaluate_program(program_code: str, program_id: str) -> Dict[str, float]`
  (`evaluator.py:132`) — the main call. Writes code to a tempfile, runs direct or cascade
  evaluation with retry, returns a metrics dict.
- `await evaluator.evaluate_multiple(programs: List[(code, id)])` (`evaluator.py:709`) —
  parallel batch via internal `TaskPool` (`utils/async_utils.py`), concurrency =
  `config.parallel_evaluations`.
- `evaluator.get_pending_artifacts(program_id)` (`evaluator.py:319`) — side-channel:
  artifacts from the last evaluation are stashed in `_pending_artifacts` keyed by program
  id and must be popped by the caller after `evaluate_program` returns.

**Config surface:** `EvaluatorConfig` (`config.py:356`): `timeout`, `max_retries`,
`cascade_evaluation` (default **True** — note this is on by default and warns if the eval
script has no `evaluate_stage1`), `cascade_thresholds`, `parallel_evaluations`,
`use_llm_feedback`, `llm_feedback_weight`, `enable_artifacts`. Also the env var
`ENABLE_ARTIFACTS` (read per-call, `evaluator.py:151`). `memory_limit_mb` / `cpu_limit`
exist in config but are **not implemented** (commented as such).

**Dependencies on the rest of the codebase:** imports `EvaluatorConfig`, `ProgramDatabase`,
`EvaluationResult`, `LLMEnsemble`, `PromptSampler`, `TaskPool`, `format_metrics_safe`. The
LLM ensemble / prompt sampler / database arguments are **optional** and only used by the
`use_llm_feedback` path (`_llm_evaluate`, `evaluator.py:550`) and prompt logging. With
`use_llm_feedback=False` (the default) you can construct
`Evaluator(EvaluatorConfig(...), "evaluator.py")` and nothing else.

**Standalone verdict:** importable and callable standalone. Two caveats matter for noema:

1. **It is not actually a sandbox.** The user's `evaluate()` runs *in-process* in a thread
   executor (`_direct_evaluate`, `evaluator.py:349`), with `asyncio.wait_for` for timeout.
   A timeout abandons the thread but cannot kill it (a runaway `evaluate` keeps burning
   CPU), and there is no memory/CPU isolation. OpenEvolve gets practical isolation only
   when the *user's eval script* itself shells out to a subprocess. noema must either
   accept this or wrap evaluation in a subprocess itself (see risk register).
2. It mutates `sys.path` and `sys.modules["evaluation_module"]` at load time, and the
   cascade path re-imports the module on **every** evaluation (`_cascade_evaluate`,
   `evaluator.py:380`). Harmless for a single evaluator per process; do not construct two
   Evaluators with different eval files in one process.

### 1.2 Program database

**Where it lives:** `openevolve/database.py` — `Program` dataclass (`database.py:44`) and
`ProgramDatabase` (`database.py:113`), ~2550 lines. MAP-Elites + islands + archive +
best-program tracking + artifact and prompt logging + checkpoint save/load.

**Storage:** plain in-memory `Dict[str, Program]` (`self.programs`), plus per-island
`Set[str]` membership, per-island MAP-Elites feature maps (`Dict[feature_key, program_id]`),
an elite `archive: Set[str]`, and `best_program_id`. Optional disk persistence
(`config.db_path`, `save()`/`load()` at `database.py:590/639`) writes JSON per program;
checkpointing is driven externally by the controller.

**Scoring:** fitness is `get_fitness_score(metrics, feature_dimensions)`
(`utils/metrics_utils.py`) = `metrics["combined_score"]` if present, else the average of
numeric non-feature metrics. Comparison logic in `_is_better` (`database.py:1101`).
**Convention to respect: evaluators should return `combined_score`.**

**Sampling:** `sample(num_inspirations)` (`database.py:382`) → (parent, inspirations) using
`current_island`; `sample_from_island(island_id, num_inspirations)` (`database.py:403`) is
the stateless-ish, thread-safe variant used by the parallel controller — this is the one
noema should call. Strategy: exploration (uniform island) / exploitation (archive) /
fitness-weighted, with ratios from `DatabaseConfig`.

**Is sampling separable from storage?** They live on the same class, but sampling only
reads `self.programs`, `self.islands`, `self.archive` and config ratios. Practically:
noema does not need to separate them — treat `ProgramDatabase` as one unit and, if a
future arm needs custom parent selection (e.g. a ShinkaEvolve-style bandit), bypass
`sample()` and pick from `db.programs` / `db.islands` directly, then call `db.add()` as
usual. Nothing in `add()` depends on how the parent was chosen.

**Island mechanics the external controller must drive** (the DB does none of this on a
timer): `increment_island_generation()` (`database.py:1769`), `should_migrate()` /
`migrate_programs()` (`database.py:1775/1780`), `next_island()` for round-robin. Child
programs auto-inherit the parent's island via `parent.metadata["island"]` in `add()`.

**Hidden couplings to know about:**

- Constructor seeds the **global** `random` module (`database.py:170-175`) — fine for
  reproducibility, but noema's controller should own seeding policy explicitly.
- `add()` runs a novelty check (`_is_novel`, `database.py:1058`) that can make **two kinds
  of network calls** (embeddings + LLM judge) if `embedding_model` is configured — off by
  default, and must stay off (or be ledger-wrapped) in noema.
- Constructor auto-loads from `config.db_path` if the path exists (`database.py:164`).
- `config.log_prompts` (default True) makes `add()`-adjacent code store full prompts on
  `Program.prompts` — useful for noema's provenance logging, keep it.

### 1.3 LLM call sites (complete enumeration)

Every chat-completion call funnels through `LLMInterface.generate_with_context`
(implemented by `OpenAILLM`, `llm/openai.py:108`, dispatched via `LLMEnsemble`,
`llm/ensemble.py`). Embeddings use a separate raw client. The full list:

| # | Site | Trigger | Prompt template | Response parsing | Token usage recorded? |
|---|------|---------|-----------------|------------------|----------------------|
| 1 | `process_parallel.py:201` (`_run_iteration_worker`, child process) | every evolution iteration in the production path | `diff_user` / `full_rewrite_user` / `user_message_with_changes_description` + `system_message`, built by `PromptSampler.build_prompt` | `extract_diffs`/`apply_diff` or `parse_full_rewrite` (`utils/code_utils.py`) | **No** |
| 2 | `iteration.py:92` (`run_iteration_with_shared_db`) | legacy/single-process iteration path (not used by the current controller, which always goes through `ProcessParallelController`) | same as #1 | same as #1 | **No** |
| 3 | `evaluator.py:574` (`_llm_evaluate`, uses `generate_all_with_context` — calls **every** model in the evaluator ensemble) | per evaluation, only if `evaluator.use_llm_feedback=True` (default False) | `evaluation` template | regex-extract JSON block, numeric keys → metrics, rest → artifacts | **No** |
| 4 | `database.py:1014/1023` (`_llm_judge_novelty`, called from `add()` → `_is_novel`) | on every `db.add()` if `embedding_model` set and a similar program found | hardcoded `NOVELTY_SYSTEM_MSG`/`NOVELTY_USER_MSG` in `novelty_judge.py` | substring search for `NOVEL` / `NOT NOVEL` | **No** |
| 5 | `embedding.py:79` (`EmbeddingClient.get_embedding`, sync OpenAI client) | on every `db.add()` if `embedding_model` set | n/a (embedding) | vector | **No** (cost table exists at `embedding.py:25` but is never used) |

Two more places *construct* messages but go through the same `generate_with_context`:
`OpenAILLM.generate` (prompt → single user message) and the ensemble's parallel helpers
(`generate_multiple`, `parallel_generate`) — no additional call sites, currently unused
by the main flow.

**The critical finding for the ledger:** `OpenAILLM._call_api` (`llm/openai.py:212`)
returns `response.choices[0].message.content` and **discards `response.usage`**. There is
no token accounting anywhere in OpenEvolve (confirmed by grep: no
`prompt_tokens`/`completion_tokens` outside a comment). Retries inside
`generate_with_context` (`llm/openai.py:191`) and inside the OpenAI SDK client itself
(`max_retries`, `llm/openai.py:85`) mean one logical call can be several billed requests —
the ledger must meter at the lowest level (the raw API response), not at the call site.

**The clean injection hook:** `LLMModelConfig.init_client: Optional[Callable]`
(`config.py:60`) — `LLMEnsemble` calls `model_cfg.init_client(model_cfg)` instead of
constructing `OpenAILLM` when set (`ensemble.py:25`). If noema reuses any OpenEvolve
component that owns an ensemble, a ledger-wrapped `LLMInterface` can be injected without
patching OpenEvolve.

### 1.4 Coupling assessment

| Component | Rating | Notes |
|-----------|--------|-------|
| `Evaluator` | **clean import** (with eyes open) | Constructor needs only `EvaluatorConfig` + eval file path. Async API. Caveats: not a real sandbox (§1.1), artifacts side-channel must be popped, `cascade_evaluation` defaults True. |
| `ProgramDatabase` | **needs light wrapping** | Constructable from `DatabaseConfig` alone. Wrapping needed to: own the island/migration bookkeeping loop, keep novelty features off, control global-random seeding, and expose the narrower read API a coordination module sees. No surgery. |
| `PromptSampler`/`TemplateManager` | **clean import** | Needs only `PromptConfig`. See §1.5. |
| `LLMEnsemble`/`OpenAILLM` | **needs light wrapping** | Usable as-is, but useless for budgeting (usage discarded). noema should implement its own `LLMInterface` (ledger-aware) and either use it directly or inject via `init_client`. |
| `OpenEvolve` controller / `ProcessParallelController` | **not imported** | This is exactly what noema replaces. The process-worker path also duplicates iteration logic (`process_parallel.py` reimplements `iteration.py`) and rebuilds components per worker from a config dict — a design noema deliberately avoids. |

**Minimal import set for the noema controller:**

```
openevolve.config             (EvaluatorConfig, DatabaseConfig, PromptConfig, LLMModelConfig)
openevolve.evaluator          (Evaluator)
openevolve.evaluation_result  (EvaluationResult)
openevolve.database           (Program, ProgramDatabase)
openevolve.prompt.sampler     (PromptSampler)          # optional but recommended, §1.5
openevolve.utils.code_utils   (extract_diffs, apply_diff, parse_full_rewrite, format_diff_summary)
openevolve.llm.base           (LLMInterface)           # to subclass for the ledger client
```

Transitive deps: `dacite`, `yaml`, `numpy`, `openai` — all already required by openevolve.
Pin the openevolve commit (git submodule or pinned pip install from git) since noema
depends on internals, not a stable API.

### 1.5 Prompt scaffolding

- Defaults live as string constants in `prompt/templates.py` **and** as files in
  `openevolve/prompts/defaults/*.txt` + `fragments.json`. `TemplateManager`
  (`templates.py:175`) loads defaults, then overlays every `*.txt` from
  `PromptConfig.template_dir` — filename stem = template key. So a per-experiment template
  directory fully controls prompt text without touching openevolve.
- `PromptSampler.build_prompt(...)` (`sampler.py:51`) assembles: metrics, improvement-area
  fragments, rendered evolution history (previous attempts, island top programs,
  inspirations), artifacts, and the current program into the user template via
  `str.format`. Template keys can be overridden per-sampler (`set_templates`) or per-call
  (`template_key=`).
- **Placeholders are fixed**: a template can only reference fields `build_prompt` puts in
  its `format(...)` call. There is **no coordination/guidance slot today**. Options for
  noema: (a) append coordination text to the returned `prompt["user"]` /
  `prompt["system"]` strings after `build_prompt` returns — zero openevolve changes, exact
  and auditable; or (b) custom templates with an extra placeholder + a thin wrapper that
  passes the value. **Recommendation: (a)** — the injected block is the single controlled
  variable, and string concatenation makes "identical except the ablated component"
  trivially verifiable by diffing logged prompts.
- **Ablation hazard:** `use_template_stochasticity` defaults **True** (`config.py:257`) —
  random phrase variations between `{{ }}` markers per prompt (`sampler.py` applies them
  when enabled). Must be **False** in all arms, or identical-prompt guarantees are void.
  Note also `random_seed` default is 42, not None, in both `Config` and `DatabaseConfig`.

---

## Part 2: HiFo-Prompt mechanism extraction

Code under `hifo/src/hifo/`. The system = EoH-style evolution (substrate) + two
coordination components (mechanism): **hindsight** = `InsightPool`, **foresight** =
`EvolutionaryNavigator`.

### 2.1 The core mechanisms, their state, and per-generation I/O

**`InsightPool`** (`methods/hifo/insight_pool.py`, ~230 lines, self-contained, stdlib only):

- **State across generations:** `tips` (deque, max 30 text strings) and `tip_stats[tip] =
  {used_count, effectiveness (EMA in [-1,1]), total_effectiveness, last_used_generation,
  tags}`; `current_generation` counter.
- **Operations:** `add_tip` (dedup by word-overlap similarity > 0.7; eviction when full —
  tips used < 3 times have "probation immunity", otherwise lowest
  `effectiveness − 0.01·generations_idle` is evicted); `get_tips(k=3,
  strategy="adaptive")` (score = effectiveness − 0.1·log(uses+1) + recency bonus; marks
  selected tips used); `update_tip_stats(tip, effectiveness)` (EMA, α=0.3, clamped to
  [-1,1]); `update_generation(n)`.

**`EvolutionaryNavigator`** (`methods/hifo/evolutionary_navigator.py`, ~100 lines, pure
heuristic, **no LLM**):

- **State:** `stagnation_count`, `improvement_count`, `last_best_fitness`, `last_guidance`.
- **Per generation:** `get_guidance(best_fitness_history, diversity_history, ...)` →
  `(regime, design_directive)`. Regime ∈ {exploration, exploitation, balanced}: stagnation
  ≥ 3 gens or diversity < 0.3 → exploration; ≥ 2 consecutive improvements → exploitation;
  else weighted random. Directive = random choice from fixed per-regime string lists.
  Note: improvement is computed as `last_best − current` (their objectives are minimized).

**Glue logic living in `InterfaceEC`** (`methods/hifo/hifo_interface_EC.py`) that is
*part of the mechanism* and must be transplanted with it:

- **History tracking** (`update_population_metrics`, line 269): rolling
  `best_fitness_history`, `avg_fitness_history` (last 50), and `diversity_history`
  (pairwise fraction of distinct algorithm-description strings — a crude population
  diversity proxy).
- **Credit assignment** (`calculate_insight_effectiveness` line 90 +
  `update_insight_feedback` line 120): after an offspring is evaluated, effectiveness is
  computed from its fitness vs population best/avg/worst (piecewise-linear into [-1,1];
  eval failure → −0.5) and applied to **every tip that was in the prompt** for that
  offspring. Requires each offspring to carry `metadata.insights` (the exact tips used).
- **Insight extraction — the mechanism's own LLM call**
  (`extract_insights_from_population`, line 298): with probability 0.8 per
  generation-step, builds a prompt from the top 30% of the population (algorithm
  one-liners, falling back to truncated code), asks for "1–2 concise, generic design
  principles" formatted as `- ...` bullet lines, parses lines starting with `-`, and
  `add_tip`s each (min length 10).

**Per-mutation injection** (in `hifo_evolution.py`, every operator prompt i1/e1/e2/m1/m2/m3):
three appended text blocks — (1) "Consider these successful design principles..." + the k=3
tips; (2) "please pay special attention to: {design_directive}"; (3) a fixed one-line
regime instruction (explore / refine / balance). This composed suffix is the entire
output of the mechanism per mutation.

### 2.2 Mechanism vs substrate boundary

**Transplant (mechanism):**

- `InsightPool` — reimplement nearly verbatim (it has no external deps).
- `EvolutionaryNavigator` — reimplement verbatim; adapt the improvement sign to noema's
  maximized `combined_score`.
- From `InterfaceEC`: history tracking, effectiveness computation, per-offspring
  insight-attribution metadata, and the insight-extraction prompt + `-` bullet parsing.
- The *shape* of the prompt injection (three suffix blocks) — re-expressed as noema's
  coordination text block.

**Discard (substrate):**

- `hifo.py` / `methods/hifo/hifo.py` outer loop, population management
  (`methods/management/*`), parent selection (`methods/selection/*`), the EoH operator
  suite and its prompt bodies (`hifo_evolution.py` i1/e1/e2/m1/m2/m3 — noema uses
  OpenEvolve-style diff/rewrite mutation prompts), `{...}` brace + regex response parsing,
  `InterfaceLLM`/`api_general.py` (blocking `http.client`, no usage capture, silent
  infinite-retry style), joblib parallelism, numba acceleration, problem definitions.

**Fidelity notes (record these in the ablation writeup):**

1. In the original, `get_offspring` runs under `joblib.Parallel` (default loky =
   **subprocesses**), so `update_insight_feedback` and `update_tip_stats` mutate a *copy*
   of the pool in the worker; credit assignment appears to be largely **lost** in the
   original implementation, and the exception path that penalizes tips (−0.8,
   `hifo_interface_EC.py:231`) is dead code (the freshly-reassigned offspring dict has no
   `metadata`). A faithful-to-paper transplant (in-process, working feedback) will be
   *more* functional than the released code — flag this deviation explicitly.
2. Effectiveness math assumes minimization; noema must flip it.
3. The extraction probability (0.8), pool size (30), k=3 tips, EMA α, decay/probation
   constants are all magic numbers — make them `CoordinationConfig` fields so ablations
   can hold them fixed.

### 2.3 Mechanism LLM calls (coordination budget)

Exactly **one** kind: the insight-extraction call (§2.1), ~1 call per generation with
p=0.8, prompt size O(top-30% of population descriptions), small completion (1–2 bullet
lines). The navigator makes none. In noema, this call goes through the ledger's
**coordination** account. (The original also implicitly relies on the mutation LLM
returning a one-sentence algorithm description in braces; noema's equivalent input is the
`changes_description` / diff summary already produced by the mutation flow, or program
code truncated the way HiFo does.)

### 2.4 What the mechanism needs from the host

Per mutation (before the LLM call):

- current generation/iteration number;
- recent population statistics: best fitness history, average fitness history, a
  diversity signal (noema can supply OpenEvolve's island diversity or the HiFo-style
  distinct-description fraction — choose one and keep it fixed across arms);
- the ability to inject a text block into the mutation prompt;
- per-child attribution: which tips/directive/regime were injected (host stores this on
  the child's metadata so post-eval credit can find it).

Per evaluation result (after):

- the child's fitness + the population stats snapshot, to compute effectiveness and
  update tip stats.

Per generation:

- a "generation tick" to advance `current_generation`, append histories, and (with
  probability p) run insight extraction over the current top programs — requiring read
  access to top-k programs (code + short description + fitness) and a **coordination LLM
  handle**.

Plus: checkpoint/restore of pool + navigator state, and structured logging (the original
logs pool size, recent tips, last guidance per generation — noema should log at least
that, plus per-tip stats).

This exact list is what the `CoordinationModule` interface below exposes — no more.

---

## Part 3: Integration plan

### 3.1 `CoordinationModule` interface

```python
# noema/coordination/base.py  (sketch — signatures only)

@dataclass
class GenerationContext:          # host → module, read-only
    iteration: int                # global mutation counter
    generation: int               # island generation / batch counter
    island: int
    parent: ProgramView           # id, code, fitness, metadata (frozen view)
    inspirations: list[ProgramView]
    top_programs: list[ProgramView]        # island-local top-k
    best_fitness_history: list[float]      # host-maintained, definition fixed per experiment
    avg_fitness_history: list[float]
    diversity_history: list[float]

@dataclass
class Advice:                     # module → host
    prompt_block: str = ""        # text appended to the mutation user prompt ("" = no-op)
    system_block: str = ""        # optional system-message suffix
    attribution: dict = field(default_factory=dict)
        # opaque payload the host stores on the child (e.g. {"insights": [...],
        # "directive": ..., "regime": ...}) and hands back in report_result
    sampling_hint: dict | None = None      # OPTIONAL, see note below

class CoordinationModule(ABC):
    def __init__(self, config: dict, llm: BudgetedLLM, rng: random.Random): ...
        # llm is pre-bound to the ledger's "coordination" account

    def advise(self, ctx: GenerationContext) -> Advice: ...
        # called once per mutation, before the mutation LLM call

    def report_result(self, ctx: GenerationContext, child: ProgramView,
                      attribution: dict, eval_failed: bool) -> None: ...
        # called once per evaluated child (credit assignment)

    def on_generation_end(self, ctx: GenerationContext) -> None: ...
        # batch/generation tick; may make coordination LLM calls (HiFo: insight extraction)

    def state_dict(self) -> dict: ...          # checkpointing
    def load_state_dict(self, d: dict) -> None: ...
    def log_snapshot(self) -> dict: ...        # per-generation JSON for the run log

class NullCoordination(CoordinationModule):    # the coordination-OFF arm
    # advise() returns Advice(""), everything else is a no-op
```

**Justification from HiFo:** `advise` covers tips+directive+regime injection and
attribution; `report_result` covers effectiveness feedback (including the failure
penalty); `on_generation_end` covers history updates and the insight-extraction LLM call;
`state_dict` covers pool/navigator persistence. `GenerationContext` carries exactly the
host data HiFo consumes (§2.4).

**HiFo-specific things that must NOT leak into the generic interface:**

- regime/directive/insight semantics — hidden inside `Advice.prompt_block` +
  the opaque `attribution` dict (the host never interprets them);
- HiFo's specific effectiveness formula, pool constants, extraction probability — module
  config, not interface;
- the assumption that coordination only ever *adds prompt text*. A ShinkaEvolve-style
  bandit sampler wants to influence *selection* (parent / island / operator / model),
  not prompts. Hence the optional `sampling_hint` field (e.g. `{"island": 3}` or
  `{"llm_model": "..."}`): the host honors keys it understands and logs what it honored.
  HiFo never sets it; the brute-force arm ignores it. Keep it a hint, not a command, so
  the host loop stays identical across arms. Resist adding anything else to the interface
  until a second mechanism (the bandit) actually needs it.

### 3.2 Budget ledger interface and interception point

```python
# noema/budget/ledger.py  (sketch)

class BudgetExhausted(Exception): ...

@dataclass
class CallRecord:
    account: str            # "mutation" | "coordination"
    tag: str                # e.g. "mutate", "hifo.extract_insights"
    model: str
    prompt_tokens: int
    completion_tokens: int
    attempts: int           # billed requests incl. retries
    latency_s: float
    iteration: int

class TokenLedger:
    def __init__(self, total_budget_tokens: int, accounts: dict[str, float] | None = None,
                 count_retries: bool = True): ...
    def charge(self, record: CallRecord) -> None      # raises BudgetExhausted at/over cap
    def remaining(self, account: str | None = None) -> int
    def snapshot(self) -> dict                        # for checkpoints + run log (JSONL of CallRecords)

class BudgetedLLM(openevolve.llm.base.LLMInterface):
    """The ONLY object in noema that talks to the chat-completions API."""
    def __init__(self, model_cfg, ledger: TokenLedger, account: str, tag: str): ...
    async def generate_with_context(self, system_message, messages, **kw) -> str:
        # - checks ledger.remaining(account) BEFORE the call (pre-flight, estimate)
        # - makes the raw API call itself (async openai client), reads response.usage
        # - charges actual prompt+completion tokens, PER ATTEMPT (retries are billed tokens)
        # - returns content string (drop-in compatible with OpenEvolve's LLMInterface)
```

**Where it intercepts:**

- **noema's own calls (the normal case):** the controller and coordination modules are
  handed `BudgetedLLM` instances pre-bound to the right account. Since noema owns the
  loop, there is no other path to the API.
- **Why not wrap OpenEvolve's `OpenAILLM`?** Because it discards `response.usage`
  (§1.3) — wrapping *around* it can only estimate via a tokenizer. `BudgetedLLM`
  therefore reimplements the (small) call logic with the OpenAI SDK directly and meters
  exact usage, including retries. It stays `LLMInterface`-compatible so it can also be
  injected into any OpenEvolve component via `LLMModelConfig.init_client` if one is ever
  reused with an ensemble.
- **Leak prevention:** run with `use_llm_feedback=False`, `embedding_model=None`
  (novelty off), no `evaluator_models` in use, so call sites #3/#4/#5 from §1.3 are dead.
  Belt-and-braces: a test monkeypatches `openai.OpenAI`/`AsyncOpenAI` constructors to
  count instantiations and asserts the only client in the process belongs to the ledger.

Budget semantics to fix up front (document in config): one shared total with per-account
sub-caps (mutation vs coordination) vs one pool both draw from — for coordination-cost
ablations you want **one shared pool** (coordination spends tokens that mutation could
have used; that is the experimental point), with per-account *accounting* but a single
*cap*. `BudgetExhausted` ends the run cleanly with a final checkpoint, so arms are
compared at equal token spend.

### 3.3 Controller loop sketch

Single-process asyncio; parallelism via concurrent in-flight iterations, not worker
processes (coordination state stays in one place — the exact failure of the original
HiFo, §2.2). Sketch:

```python
# noema/controller.py (pseudocode)

db        = ProgramDatabase(db_config)                 # openevolve, novelty features OFF
evaluator = Evaluator(eval_config, eval_file)          # openevolve, no LLM args
sampler   = PromptSampler(prompt_config)               # shared template_dir, stochasticity OFF
ledger    = TokenLedger(total_budget)
mut_llm   = BudgetedLLM(model_cfg, ledger, account="mutation",     tag="mutate")
coord     = make_module(arm)                           # NullCoordination | HiFoPrompt(...)
                                                       #   given BudgetedLLM(..., "coordination")

seed_program = evaluate_and_add(initial_code)          # via evaluator + db.add

for iteration in range(start, max_iterations):
    island = iteration % num_islands                   # round-robin, mirrors openevolve
    parent, inspirations = db.sample_from_island(island, k)
    ctx = build_context(iteration, island, parent, inspirations,
                        db.get_top_programs(5, island_idx=island), histories)

    advice = coord.advise(ctx)                                     # ── coordination hook 1
    prompt = sampler.build_prompt(current_program=parent.code, ...)
    user   = prompt["user"] + render(advice.prompt_block)          # "" in the OFF arm
    system = prompt["system"] + render(advice.system_block)

    try:
        response = await mut_llm.generate_with_context(system, [user])   # ── ledger meter
    except BudgetExhausted:
        break

    child_code = apply_diff(parent.code, response) or parse_full_rewrite(response)
    if child_code is None or too_long(child_code):
        coord.report_result(ctx, child=None, attribution=advice.attribution, eval_failed=True)
        continue

    metrics   = await evaluator.evaluate_program(child_code, child_id)   # openevolve sandbox
    artifacts = evaluator.get_pending_artifacts(child_id)
    child     = Program(id, child_code, parent_id=parent.id,
                        generation=parent.generation+1, metrics=metrics,
                        metadata={"coordination": advice.attribution, ...})
    db.add(child, iteration=iteration)
    db.store_artifacts(child_id, artifacts) if artifacts

    coord.report_result(ctx, child_view, advice.attribution,             # ── hook 2
                        eval_failed=("error" in metrics))

    if end_of_generation(iteration):                   # every num_islands iterations (or batch)
        update_histories(db)                           # host-owned, fixed definition
        coord.on_generation_end(ctx)                   # ── hook 3 (HiFo: insight extraction)
        db.increment_island_generation()
        if db.should_migrate(): db.migrate_programs()

    if iteration % checkpoint_interval == 0:
        save(db.save(...), coord.state_dict(), ledger.snapshot(), rng_states)
```

The OFF arm and the HiFo arm differ **only** in `make_module(arm)` — same templates, same
seeds, same budget, same loop.

### 3.4 Risk register

| # | Risk | Likelihood/impact | Mitigation / fallback |
|---|------|-------------------|-----------------------|
| 1 | **Evaluator isn't a sandbox**: user eval runs in-process; timeout leaks the thread; no memory caps; cascade defaults on. A hostile/hung evolved program stalls or OOMs the whole (now single-process) controller. | Medium / High | Write noema eval scripts so `evaluate()` runs the program in a `subprocess` with rlimits (OpenEvolve's own examples do this); set `cascade_evaluation=False` explicitly. Fallback: a noema `SubprocessEvaluator` wrapper with the same async API. |
| 2 | **Token metering gaps**: usage discarded by OpenEvolve's client; SDK+wrapper retries multiply billed tokens; hidden call sites (novelty judge, LLM feedback, embeddings) bypass the ledger if ever enabled. | Medium / High (invalidates equal-budget claim) | `BudgetedLLM` owns the raw client and charges per attempt from `response.usage`; features that call LLMs from inside borrowed components stay hard-disabled; CI test asserts the ledger client is the only OpenAI client constructed; reconcile ledger totals against provider dashboard on pilot runs. |
| 3 | **Prompt non-identity across arms**: template stochasticity (default ON), seed-42 defaults, dict-ordered history sections, and the coordination block shifting other content make "identical except ablated component" false. | Medium / High (core validity) | `use_template_stochasticity=False`; single shared `template_dir` committed per experiment; coordination text injected only as a suffix block; log full prompts (`log_prompts=True` + ledger JSONL) and add an automated prompt-diff check between arms on a dry run. |
| 4 | **Async/coupling mismatches in borrowed components**: `db.add()` is not coroutine-safe under concurrent in-flight iterations; the DB seeds global `random`; migration/generation bookkeeping is easy to drive inconsistently vs upstream behavior. | Medium / Medium | Start strictly sequential (concurrency=1) for correctness baselines; if throughput is needed, serialize `db.add`/`coord.*` behind an asyncio lock while letting LLM+eval calls overlap; controller owns all RNG seeding after DB construction; copy the island-cadence logic from `process_parallel.py` rather than inventing it. |
| 5 | **Transplant infidelity for HiFo**: the released code's credit assignment is partly broken (subprocess state loss, dead penalty path, minimization-sign assumptions); a "fixed" reimplementation may behave differently from the paper. | High / Medium (interpretation, not validity) | Implement the paper's described behavior; document each deviation from released code (§2.2 list); unit-test pool/navigator against hand-computed traces; if results surprise, run a variant with feedback disabled to isolate the effect of the (broken-in-original) credit loop. |
| 6 | **Upstream drift / internal-API breakage**: noema imports openevolve internals (`sample_from_island`, `_pending_artifacts` contract, etc.), not a stable API. | Low / Medium | Pin the exact commit (submodule); a thin `noema/substrate/` adapter module is the only place that touches openevolve symbols, so an upgrade is one file. |

### 3.5 Ordered implementation task list

Phase A gets the **brute-force arm (coordination OFF) running end-to-end first**; HiFo
lands second; nothing in Phase B blocks Phase A.

**Phase A — substrate + ledger + OFF arm**

1. Repo scaffolding: `noema/` package, pinned `openevolve` dependency (submodule or
   git-pinned install), experiment config schema (YAML → dataclasses), fixed seed policy.
2. `TokenLedger` + `BudgetedLLM` (async OpenAI client, per-attempt metering, JSONL call
   log, `BudgetExhausted`); unit tests with a mocked API (usage arithmetic, retry
   billing, pre-flight check, exhaustion mid-run).
3. Substrate adapters (`noema/substrate/`): thin wrappers constructing
   `ProgramDatabase` (novelty off, seeding policy) and `Evaluator`
   (`cascade_evaluation=False`, artifacts pop) + `ProgramView` read-only views; smoke
   test: add/sample/checkpoint round-trip, evaluate a toy program.
4. Prompt assembly: shared `template_dir`, stochasticity off, suffix-injection helper;
   golden-file test that two arms with `NullCoordination` vs empty advice produce
   byte-identical prompts.
5. `CoordinationModule` ABC + `NullCoordination`.
6. Controller loop (sequential first): sample → advise → prompt → mutate → parse →
   evaluate → add → report → generation tick → checkpoint; checkpoint/resume including
   ledger + RNG + coordination state.
7. **Milestone: brute-force end-to-end run** on a small benchmark (e.g. the
   function-minimization example task) with a tiny budget; verify ledger totals vs
   provider dashboard, checkpoint/resume equivalence, and prompt logs.

**Phase B — HiFo transplant**

8. Port `InsightPool` + `EvolutionaryNavigator` into `noema/coordination/hifo/` with all
   magic numbers in config; unit tests against hand-computed traces (eviction, probation,
   EMA, regime transitions, sign convention flipped to maximization).
9. `HiFoPrompt(CoordinationModule)`: advise (tips + directive + regime suffix,
   attribution payload), report_result (effectiveness + failure penalty),
   on_generation_end (histories + p=0.8 insight extraction via coordination-account
   `BudgetedLLM`, `-` bullet parsing), state_dict, log_snapshot.
10. Fidelity checklist vs released HiFo (documented deviations from §2.2), plus an
    integration test on a stub LLM asserting: tips appear in prompts, credit reaches
    `tip_stats`, extraction charges the coordination account.
11. **Milestone: two-arm pilot** (OFF vs HiFo) at equal total budget, same seeds/templates;
    verify the only diff in logged prompts is the coordination block and that the ledger
    splits spend by account.
12. Cleanups for arm #3 readiness: confirm `sampling_hint` pathway with a trivial test
    module (e.g. random island override) before starting the ShinkaEvolve-style bandit.
