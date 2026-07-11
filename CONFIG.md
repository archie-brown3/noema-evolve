# noema configuration reference

Every noema run is described by a single `NoemaConfig` object (`noema/config.py`). It composes
noema's own settings (`budget`, `llm`, `coordination`) with three borrowed openevolve component
configs (`database`, `evaluator`, `prompt`). Build it in Python, or load it from a YAML file with
`NoemaConfig.from_yaml(path)` / `NoemaConfig.from_dict(d)`; `to_yaml()` renders the fully-resolved
config, which is what gets frozen and hashed per run.

**Convention used in this document — read this before setting anything.**

| Marker | Meaning |
|---|---|
| ✅ | **Implemented.** The key is read by code today. Safe to set. |
| 🔒 | **Implemented, but not settable in config.** The value is pinned by the arm's registry key; setting it in `coordination.params` raises `ValueError`. |
| ⚠️ | **Deprecated.** Still works, logs a warning; prefer the replacement named in the row. |
| 🚧 | **Planned.** The key **does not exist yet**. Setting it today does nothing, or errors. |

Every ✅ row below is traceable to a real field or a real `config.get(...)` call in the source.
Everything 🚧 lives in one place: the [Planned](#planned--not-yet-implemented) section at the end.
Nothing planned appears in the implemented tables.

> **Unknown keys are silently ignored.** `from_dict` uses `dacite` with `strict=False`, so a typo
> (or a planned-but-unbuilt key) does **not** raise — it is dropped. Check `to_yaml()` if a setting
> seems to have no effect.

---

## 1. Top-level run options (`NoemaConfig`)

Substrate-level. These are identical across coordination arms in a controlled ablation — an "arm"
differs **only** in `coordination.module`.

| Name | Description | Usage |
|---|---|---|
| ✅ `max_iterations` | Number of evolution iterations (mutations) to run. | `max_iterations: 100` — int. Default `100`. |
| ✅ `checkpoint_interval` | Write a checkpoint every N iterations. | `checkpoint_interval: 10` — int. Default `50`. |
| ✅ `random_seed` | Master seed. Also derives `coordination.seed` (`+1`) and `mutation_operator_seed` (`+2`). | `random_seed: 42` — int. Default `42`. |
| ✅ `language` | Program language, passed to the prompt sampler. | `language: "python"` — str. Default `"python"`. |
| ✅ `file_suffix` | Extension of the evolved program file, passed to the evaluator. | `file_suffix: ".py"` — str. Default `".py"`. |
| ✅ `diff_based_evolution` | If true, the LLM emits SEARCH/REPLACE diff blocks; if false, a full rewrite. | `diff_based_evolution: true` — bool. Default `true`. |
| ✅ `diff_pattern` | Regex used to extract diff blocks from the LLM response. | `diff_pattern: "<<<<<<< SEARCH\\n(.*?)=======\\n(.*?)>>>>>>> REPLACE"` — str. Default as shown. |
| ✅ `max_code_length` | Reject a child program whose code exceeds this many characters. | `max_code_length: 10000` — int. Default `10000`. |
| ✅ `num_inspirations` | Inspiration programs sampled from the parent's island per mutation. | `num_inspirations: 3` — int. Default `3`. |
| ✅ `num_top_programs` | Top programs included in the mutation prompt. **This top-level key governs**, not `prompt.num_top_programs`. | `num_top_programs: 5` — int. Default `5`. |
| ✅ `num_previous_programs` | Previous-attempt programs included in the mutation prompt. | `num_previous_programs: 3` — int. Default `3`. |

### 1.1 Retry loop (substrate-level)

Substrate config, **not** coordination-module params — even though `pes-faithful` is specified in
terms of them. They live at the top level of the run config.

| Name | Description | Usage |
|---|---|---|
| ✅ `retry_enabled` | Master switch for the retry loop. Everything below is inert while this is false. | `retry_enabled: false` — bool. Default `false`. |
| ✅ `retry_cap` | Number of **retries after** the initial attempt (so `retry_cap: 2` = up to 3 total rounds). | `retry_cap: 2` — int. Default `2`. |
| ✅ `retry_on` | Retry trigger. `"failure"` = parse/boundary/eval failures only. `"non_improvement"` additionally retries a valid child that fails to beat its parent, keeping the best attempt. | `retry_on: "non_improvement"` — one of `failure` \| `non_improvement`. Default `"failure"`. **Validated in `__post_init__` — any other value raises `ValueError`.** |

### 1.2 Mutation operator menu (substrate-level, opt-in)

| Name | Description | Usage |
|---|---|---|
| ✅ `mutation_operators` | EoH-derived operator menu; one operator is sampled per mutation. `null` = legacy path (`diff_based_evolution` is the sole control), zero behavior change. | `mutation_operators: ["m1", "m2"]` — list of `e1`, `e2`, `m1`, `m2`, `m3`, or `null`. Default `null`. Unknown names raise `ValueError`. `e1`/`e2` are `full_rewrite`; `m1`/`m2`/`m3` are `diff`. |
| ✅ `mutation_operator_seed` | RNG seed for operator sampling. | `mutation_operator_seed: 44` — int or `null`. Default `null` → `random_seed + 2`. |

> `prompt.programs_as_changes_description: true` requires every selected operator to be a `diff`
> operator; combining it with `e1`/`e2` raises `ValueError`.

---

## 2. `coordination.*`

The one section that is *supposed* to differ between arms.

**Arm identity lives in the module KEY, and only in the key.** Paired runs must differ in exactly
one setting (`coordination.module`), so the two PES variants are two registry keys rather than one
key plus a bundle of sub-options.

| Name | Description | Usage |
|---|---|---|
| ✅ `coordination.module` | Registry key selecting the coordination mechanism (the arm). | `module: "pes-faithful"` — one of `null` (OFF arm), `hifo`, `pes-custom`, `pes-faithful`. Default `"null"`. Unknown keys raise `ValueError` listing the registry. |
| ⚠️ `coordination.module: "pes"` | **Deprecated alias** for `pes-custom` (predates the arm split; kept so existing run configs keep working). Resolves to `pes-custom` with unchanged behavior and logs a deprecation warning. | Works, but prefer `pes-custom` in new configs. |
| ✅ `coordination.params` | Free-form dict of mechanism-specific params, handed to the module's constructor. Valid keys depend entirely on the selected module (§2.1, §2.2). | `params: {tips_per_prompt: 3}` — dict. Default `{}`. |
| ✅ `coordination.seed` | Seed for the module's own RNG. | `seed: 43` — int or `null`. Default `null` → `random_seed + 1`. |

The two PES arms, and what each key pins (`noema/coordination/pes/arms.py`):

| Arm key | `prompt_variant` | `executor_mode` | recent-strategies block | Declared run-config side |
|---|---|---|---|---|
| ✅ `pes-custom` | `custom` (lean noema recast) | `advisory` (plan as prompt suffix) | ON (`k=3`) | `retry_on: failure` (the default) |
| ✅ `pes-faithful` | `faithful` (near-verbatim LoongFlow) | `directive` (plan as the executor's brief) | OFF (`recent_strategies_k=0`) | `retry_on: non_improvement`, `retry_cap: 2` — **substrate config, §1.1; the arm key does NOT set these, you must** |

```yaml
coordination:
  module: "pes-faithful"
  params: {}            # arm-defining knobs must NOT go here — see §2.1
```

### 2.1 PES module params (`pes-custom` / `pes-faithful`)

Read in `PESPlannerModule.__init__` (`noema/coordination/pes/module.py`). These go under
`coordination.params` — **except the three arm-defining knobs, which are rejected there** (see the
box below).

| Name | Description | Usage |
|---|---|---|
| ✅ `max_code_chars` | Truncate parent code to this many characters in the planning prompt. | `max_code_chars: 2000` — int. Default `2000`. |
| ✅ `domain_context` | Problem-domain constraints shown to the planner (e.g. "explicit constructor, not iterative search"). Orthogonal to search mechanics. | `domain_context: "..."` — str. Default `""`, but **the controller pre-fills it from `prompt.system_message`** unless the experiment sets it explicitly here. |
| ✅ `reflection_enabled` | Enable the deferred reflection call (one metered call per assessed child, drained at the generation tick). | `reflection_enabled: true` — bool. Default `true`. |
| ✅ `max_pending_reflections_per_tick` | Cap on how many queued children get reflected on per generation tick. Spend escape hatch. | `max_pending_reflections_per_tick: 4` — int or `null` (uncapped). Default `null`. |
| ✅ `reflection_slice_max_tokens` | Cap on the Executive-Summary + Actionable-Guidance slice of a reflection brief that is re-injected downstream (the full text is retained internally). | `reflection_slice_max_tokens: 300` — int. Default `300`. |
| ✅ `context_window_tokens` | Context window assumed for the pre-flight size assertion — overflow fails loud rather than silently truncating. | `context_window_tokens: 10240` — int. Default `10240`. |
| ✅ `strategy_digest_chars` | Character budget per strategy in the cross-lineage digest. | `strategy_digest_chars: 150` — int. Default `150`. |

#### Arm-defining knobs — set by the module key, NOT settable in config

`prompt_variant`, `executor_mode`, and `recent_strategies_k` are the three knobs that decide *which
arm you are running*. Each named arm pre-sets them, and passing any of them in `coordination.params`
**raises `ValueError`** (`ARM_DEFINING_KNOBS` in `noema/coordination/pes/arms.py`). The failure mode
being designed out is silent drift: a run that reports itself as `pes-faithful` while quietly using
custom prompts because one knob was mistyped.

| Name | Description | Usage |
|---|---|---|
| 🔒 `prompt_variant` | Planner/summarizer prompt family: `custom` (lean noema recast) or `faithful` (near-verbatim LoongFlow math-agent port). | **Not settable.** Pinned by the arm key: `pes-custom` → `custom`, `pes-faithful` → `faithful`. |
| 🔒 `executor_mode` | How the plan reaches the mutation LLM: `advisory` (standard coordination suffix) or `directive` (verbatim LoongFlow executor prompt, plan as primary instruction — the scoped prompt-identity exemption). | **Not settable.** Pinned by the arm key: `pes-custom` → `advisory`, `pes-faithful` → `directive`. |
| 🔒 `recent_strategies_k` | How many recent cross-lineage strategies appear in the planner's "Recently Attempted Elsewhere" block; `0` disables it. | **Not settable.** Pinned by the arm key: `pes-custom` → `3` (the module default), `pes-faithful` → `0` (block off). |

> **`island_bests_provider` is NOT user-settable.** It is a callable injected by the controller into
> a *local* copy of the params (`controller.py`, `setdefault("island_bests_provider", ...)`) and read
> by the planner. It must never appear in `coordination.params` in your config: a callable is not
> YAML-serializable and would perturb the frozen run-config hash. Do not set it.

### 2.2 HiFo module params (`coordination.module: "hifo"`)

Read in `HiFoPromptModule.__init__` (`noema/coordination/hifo/module.py`). Defaults are the released
HiFo-Prompt values.

| Name | Description | Usage |
|---|---|---|
| ✅ `pool_max_size` | Insight-pool capacity. | `pool_max_size: 30` — int. Default `30`. |
| ✅ `initial_tips` | Seed tips for the pool. | `initial_tips: ["..."]` — list of str, or `null` for HiFo's own defaults. Default `null`. |
| ✅ `tips_per_prompt` | How many tips are injected into each mutation prompt. | `tips_per_prompt: 3` — int. Default `3`. |
| ✅ `tip_strategy` | Pool selection strategy used to draw those tips. | `tip_strategy: "adaptive"` — str. Default `"adaptive"`. |
| ✅ `extraction_probability` | Probability per generation tick of making the insight-extraction LLM call (the mechanism's only LLM call). | `extraction_probability: 0.8` — float in [0, 1]. Default `0.8`. |
| ✅ `failure_effectiveness` | Credit-assignment score given to tips behind a failed/unevaluable child. | `failure_effectiveness: -0.5` — float. Default `-0.5`. |
| ✅ `max_code_chars` | Code truncation limit for the extraction prompt. | `max_code_chars: 1000` — int. Default `1000`. |
| ✅ `min_tip_length` | Minimum character length for an extracted tip to be accepted into the pool. | `min_tip_length: 10` — int. Default `10`. |

The `null` arm (`NullCoordination`) takes **no params**.

---

## 3. `budget.*`

One shared token pool with per-account accounting. Accounts are `"mutation"` and `"coordination"`
(`noema/budget/ledger.py`).

| Name | Description | Usage |
|---|---|---|
| ✅ `budget.total_tokens` | Total token pool for the run. Exhaustion raises `BudgetExhausted`, which stops the run cleanly. | `total_tokens: 1000000` — int. Default `1_000_000`. |
| ✅ `budget.account_caps` | Optional per-account sub-caps, on top of the shared pool. | `account_caps: {coordination: 200000}` — dict of account name → int. Default `{}` (no sub-caps). Valid accounts: `mutation`, `coordination`. |
| ✅ `budget.log_path` | JSONL file receiving one `CallRecord` per LLM call. | `log_path: "runs/a/llm_calls.jsonl"` — str or `null`. Default `null` → `<output_dir>/llm_calls.jsonl`. |

---

## 4. `llm.*` (`LLMClientConfig`)

Settings for the `BudgetedLLM` clients. **noema uses a single model** for both the mutation and the
coordination account — there is no ensemble here (unlike openevolve's `primary_model` /
`secondary_model`, which noema does **not** read).

| Name | Description | Usage |
|---|---|---|
| ✅ `llm.model` | Model name, or a local model path for an inference node. | `model: "gpt-4o-mini"` — str. Default `"gpt-4o-mini"`. |
| ✅ `llm.api_base` | OpenAI-compatible API base URL. | `api_base: "http://localhost:8090/v1"` — str or `null`. Default `null`. |
| ✅ `llm.api_key` | API key. Use `"none"` for a local node. | `api_key: "none"` — str or `null`. Default `null`. |
| ✅ `llm.temperature` | Sampling temperature. | `temperature: 0.7` — float or `null`. Default `0.7`. |
| ✅ `llm.top_p` | Nucleus-sampling top-p. | `top_p: 0.95` — float or `null`. Default `null`. |
| ✅ `llm.max_tokens` | Max tokens per completion. | `max_tokens: 4096` — int or `null`. Default `4096`. |
| ✅ `llm.seed` | Sampling seed sent to the provider, where supported (determinism). | `seed: 42` — int or `null`. Default `null`. |
| ✅ `llm.timeout` | Per-request timeout in seconds. | `timeout: 300` — float. Default `60.0`. |
| ✅ `llm.retries` | Transport-level retry count for a failed request. Unrelated to the evolutionary `retry_*` loop in §1.1. | `retries: 3` — int. Default `3`. |
| ✅ `llm.retry_delay` | Seconds between transport retries. | `retry_delay: 5.0` — float. Default `5.0`. |

---

## 5. `database.*` (openevolve `DatabaseConfig`)

Borrowed wholesale from openevolve; noema does not override its defaults. Most-used fields:

| Name | Description | Usage |
|---|---|---|
| ✅ `database.population_size` | Max programs retained in the database. | `population_size: 60` — int. Default `1000`. |
| ✅ `database.archive_size` | Size of the elite archive. | `archive_size: 25` — int. Default `100`. |
| ✅ `database.num_islands` | Island count for the island-model population. | `num_islands: 4` — int. Default `5`. |
| ✅ `database.elite_selection_ratio` | Fraction of selections drawn from the elite archive. | `elite_selection_ratio: 0.3` — float. Default `0.1`. |
| ✅ `database.exploration_ratio` | Fraction of selections that explore. | `exploration_ratio: 0.2` — float. Default `0.2`. |
| ✅ `database.exploitation_ratio` | Fraction of selections that exploit. | `exploitation_ratio: 0.7` — float. Default `0.7`. |
| ✅ `database.migration_interval` | Generations between island migrations. | `migration_interval: 50` — int. Default `50`. |
| ✅ `database.migration_rate` | Fraction of an island migrated each time. | `migration_rate: 0.1` — float. Default `0.1`. |
| ✅ `database.db_path` | On-disk database location. | `db_path: "out/db"` — str or `null`. Default `null`. |
| ✅ `database.in_memory` | Keep the population in memory. | `in_memory: true` — bool. Default `true`. |
| ✅ `database.log_prompts` | Persist prompts alongside programs. | `log_prompts: true` — bool. Default `true`. |
| ✅ `database.random_seed` | Database RNG seed. | `random_seed: 42` — int. Default `42`. |
| ✅ `database.diversity_metric` | Diversity measure over programs. | `diversity_metric: "edit_distance"` — str. Default `"edit_distance"`. |
| ✅ `database.feature_dimensions`, `feature_bins` | MAP-Elites feature axes and bin count. | `feature_bins: 10` — int. Default `10`. |

Remaining openevolve fields (`artifacts_base_path`, `artifact_size_threshold`,
`cleanup_old_artifacts`, `artifact_retention_days`, `max_snapshot_artifacts`,
`diversity_reference_size`, `novelty_llm`, `embedding_model`, `similarity_threshold`) are accepted
and passed through unchanged.

---

## 6. `evaluator.*` (openevolve `EvaluatorConfig`)

Borrowed from openevolve, with **one noema-specific default change**.

| Name | Description | Usage |
|---|---|---|
| ✅ `evaluator.cascade_evaluation` | Multi-stage cascade evaluation. **noema defaults this to `false`** (openevolve defaults `true`, which warns and falls back unless the eval script defines `evaluate_stage1`). | `cascade_evaluation: false` — bool. **noema default `false`.** |
| ✅ `evaluator.cascade_thresholds` | Score thresholds to advance between cascade stages. | `cascade_thresholds: [0.5, 0.75]` — list of float. Default `[0.5, 0.75]`. |
| ✅ `evaluator.timeout` | Per-evaluation timeout in seconds. | `timeout: 60` — int. Default `300`. |
| ✅ `evaluator.max_retries` | Retries for a failed evaluation. | `max_retries: 3` — int. Default `3`. |
| ✅ `evaluator.parallel_evaluations` | Evaluations run concurrently. | `parallel_evaluations: 4` — int. Default `1`. |
| ✅ `evaluator.use_llm_feedback` | Ask an LLM to score the program alongside the metric. | `use_llm_feedback: false` — bool. Default `false`. |
| ✅ `evaluator.llm_feedback_weight` | Weight of that LLM feedback in the final score. | `llm_feedback_weight: 0.1` — float. Default `0.1`. |
| ✅ `evaluator.enable_artifacts` | Capture evaluation artifacts (stdout, traces). | `enable_artifacts: true` — bool. Default `true`. |
| ✅ `evaluator.memory_limit_mb`, `cpu_limit`, `distributed`, `max_artifact_storage` | Resource limits and artifact storage cap. | Defaults `null`, `null`, `false`, `104857600`. |

---

## 7. `prompt.*` (openevolve `PromptConfig`)

Borrowed from openevolve, with **one noema-specific default change that is also enforced**.

| Name | Description | Usage |
|---|---|---|
| ✅ `prompt.use_template_stochasticity` | Random template variation. **noema forces this off** — identical prompts across arms is a guarantee-triad property. | `use_template_stochasticity: false` — bool. **noema default `false`, and `true` raises `ValueError` in `__post_init__`.** |
| ✅ `prompt.system_message` | System message for the mutation LLM. Also supplies the PES planner's `domain_context` by default. | `system_message: \|` + block text — str. Default `"system_message"` (a placeholder — always set it). |
| ✅ `prompt.evaluator_system_message` | System message used for LLM-feedback evaluation. | str. Default `"evaluator_system_message"`. |
| ✅ `prompt.include_artifacts` | Include evaluation artifacts in the mutation prompt. | `include_artifacts: false` — bool. Default `true`. |
| ✅ `prompt.max_artifact_bytes` | Byte cap on included artifacts. | `max_artifact_bytes: 20480` — int. Default `20480`. |
| ✅ `prompt.artifact_security_filter` | Scrub artifacts before inclusion. | bool. Default `true`. |
| ✅ `prompt.programs_as_changes_description` | Render past programs as change descriptions rather than code. **Constrains `mutation_operators` to diff-mode operators** (see §1.2). | `programs_as_changes_description: false` — bool. Default `false`. |
| ✅ `prompt.template_dir` | Directory of custom prompt templates. | str or `null`. Default `null`. |
| ✅ `prompt.use_meta_prompting`, `meta_prompt_weight` | openevolve meta-prompting. | Defaults `false`, `0.1`. |
| ✅ `prompt.num_top_programs`, `num_diverse_programs` | openevolve's own prompt-context counts. **In noema the top-level `num_top_programs` / `num_previous_programs` / `num_inspirations` (§1) are what the controller passes to the sampler** — set those, not these. | ints. Defaults `3`, `2`. |

Formatting knobs also accepted and passed through: `system_message_changes_description`,
`initial_changes_description`, `template_variations`, `suggest_simplification_after_chars`,
`include_changes_under_chars`, `concise_implementation_max_lines`,
`comprehensive_implementation_min_lines`, `diff_summary_max_line_len`, `diff_summary_max_lines`,
`code_length_threshold`.

---

## 8. Worked example

The circle-packing example builds `NoemaConfig` **in Python**
(`examples/circle_packing/run_noema_arm.py`) — that is the reference usage.

> ⚠️ The YAML files in `examples/circle_packing/` (`config_local.yaml`, `config_phase_1.yaml`, …)
> are **openevolve** configs, not noema configs. They use `llm.primary_model` and
> `use_template_stochasticity: true`, which `NoemaConfig` does not read and does not permit. Do not
> feed them to `NoemaConfig.from_yaml`.

```python
config = NoemaConfig(
    max_iterations=50,
    checkpoint_interval=5,
    random_seed=42,
    diff_based_evolution=True,
    num_inspirations=0,
    num_top_programs=1,
    num_previous_programs=3,
    retry_enabled=False,
    retry_cap=2,
    database=DatabaseConfig(population_size=60, archive_size=25, num_islands=4,
                            elite_selection_ratio=0.3, exploitation_ratio=0.7,
                            db_path="noema_pes_output/db"),
    evaluator=EvaluatorConfig(cascade_evaluation=False, timeout=60),
    prompt=PromptConfig(use_template_stochasticity=False, include_artifacts=False,
                        system_message=SYSTEM_MESSAGE),
    budget=BudgetConfig(total_tokens=2_000_000),
    llm=LLMClientConfig(model="Qwen2.5-Coder-14B", api_base="http://localhost:8090/v1",
                        api_key="none", temperature=0.7, top_p=0.95,
                        max_tokens=4096, timeout=300),
    coordination=CoordinationConfig(module="pes"),
)
```

The equivalent noema YAML, for `NoemaConfig.from_yaml`:

```yaml
max_iterations: 50
checkpoint_interval: 5
random_seed: 42
diff_based_evolution: true
num_inspirations: 0
num_top_programs: 1
num_previous_programs: 3
retry_enabled: false
retry_cap: 2
retry_on: "failure"

coordination:
  module: "pes"          # null | hifo | pes
  params:
    reflection_enabled: true
    recent_strategies_k: 3

budget:
  total_tokens: 2000000
  account_caps: {}

llm:
  model: "Qwen2.5-Coder-14B"
  api_base: "http://localhost:8090/v1"
  api_key: "none"
  temperature: 0.7
  top_p: 0.95
  max_tokens: 4096
  timeout: 300

database:
  population_size: 60
  num_islands: 4

evaluator:
  cascade_evaluation: false
  timeout: 60

prompt:
  use_template_stochasticity: false   # required: must be false
  include_artifacts: false
  system_message: |
    You are an expert mathematician ...
```

Arms are compared by changing **only** `coordination.module`.

---

## Planned / not yet implemented

🚧 **None of the keys below exist in the code today.** They are specified in the approved plan and in
vault tasks 0066/0067, and are recorded here so the roadmap is visible — not so they can be set.
Because unknown keys are silently dropped by `dacite`, setting one today fails *quietly*: you would
get default behavior while believing you had configured a variant. Do not use them until this section
is folded into the tables above.

### Planned: `coordination.module` registry keys (task 0066, plan WP7)

The registry today is exactly `null`, `hifo`, `pes`. The planned split replaces the single `pes`
key, because the experiment's config checker requires paired runs to differ in exactly one setting —
so variant identity must live in the **module key**, never in sub-params.

| Name | Description | Usage |
|---|---|---|
| 🚧 `coordination.module: "pes-custom"` | The current `pes` behavior under an explicit name: `prompt_variant="custom"`, `executor_mode="advisory"`, recent-strategies block on, reflection-seeded retries on, `retry_on="failure"`. | Planned registry key. **Today, use `module: "pes"`.** |
| 🚧 `coordination.module: "pes-faithful"` | The LoongFlow fidelity anchor: `prompt_variant="faithful"`, `executor_mode="directive"`, recent-strategies block off, `retry_advice` returns empty, island-status block on. Its documented run config also sets the substrate keys `retry_on: non_improvement` and `retry_cap: 2` (§1.1) — those are **run config, not module params**. | Planned registry key. Today the underlying knobs exist individually (`prompt_variant`, `executor_mode`) and can be set by hand under `coordination.params`, but the named arm does not. |
| 🚧 `coordination.module: "pes"` (as a *deprecated alias*) | Under the planned split, `pes` survives only as a deprecated alias for `pes-custom` and will log a deprecation warning. | Today `pes` is the real, non-deprecated key and warns about nothing. |
| 🚧 `coordination.module: "pes-faithful-lean"` | A gated backlog probe for a context-richness micro-ablation, sequenced behind other probes. | Planned registry key; **not built, and not scheduled.** |

> `pes-full` is a **colloquial alias for `pes-faithful` in prose only**. It is not a config key today
> and is explicitly specified never to become one.

### Planned: params that will become *illegal* (task 0066)

A behavior change worth flagging, because it inverts today's rules. These knobs are settable in
`coordination.params` today (§2.1); once the named `pes-*` variants land, passing an **arm-defining**
knob for a named variant will **raise `ValueError`** — silent arm drift is the failure mode being
designed out.

| Name | Description | Usage |
|---|---|---|
| 🚧 `coordination.params.prompt_variant` | Arm-defining. Will raise if set on a named variant. | Today: ✅ settable (`custom` \| `faithful`, default `custom`). Planned: implied by the module key; setting it raises. |
| 🚧 `coordination.params.executor_mode` | Arm-defining. Will raise if set on a named variant. | Today: ✅ settable (`advisory` \| `directive`, default `advisory`). Planned: implied by the module key; setting it raises. |
| 🚧 `coordination.params.recent_strategies_k` | Arm-defining. Will raise if set on a named variant. | Today: ✅ settable (int, default `3`). Planned: implied by the module key (`pes-faithful` pins it to `0`); setting it raises. |

The remaining PES params in §2.1 (`max_code_chars`, `domain_context`, `reflection_enabled`,
`max_pending_reflections_per_tick`, `reflection_slice_max_tokens`, `context_window_tokens`,
`strategy_digest_chars`) are **not** arm-defining and are expected to stay freely settable.
