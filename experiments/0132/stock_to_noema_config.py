"""Translate a canonical stock OpenEvolve YAML into a NoemaConfig.

Single source of truth for task 0132: the stock YAML defines every
search-affecting parameter; noema recreates the *same effects* through its own
(superset) config. This does NOT change noema's config interface — it only
populates existing NoemaConfig fields from the stock config, so no shared value
is ever typed twice.

What maps 1:1 (noema borrows openevolve's DatabaseConfig/EvaluatorConfig/
PromptConfig and honours them by delegating to openevolve's own ProgramDatabase/
Evaluator): the whole `database`/`evaluator`/`prompt` sections, plus top-level
`max_iterations`, `random_seed`, `diff_based_evolution`, `max_code_length`.

What is remapped (same effect, different shape):
- `llm` flat -> noema per-role {mutation, coordination}, single model per seat;
- `prompt.num_top_programs`   -> top-level `num_top_programs`   (noema reads top-level);
- `prompt.num_diverse_programs` -> top-level `num_inspirations`.

What is refused (loud, not silent):
- a `secondary_model` / multi-model ensemble (noema is single-model per seat);
- a `prompt` section that does not explicitly set `use_template_stochasticity:
  false` — stock defaults it *true*, so an unset value means the two systems
  cannot agree (Decision #71).

Dropped (no search effect / not an openevolve Config field): `log_level`,
`log_dir`, `allow_full_rewrites`, `early_stopping_*`, `convergence_threshold`,
`max_tasks_per_child`, `evolution_trace`.

See task 0132 and Decisions #71/#72.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from noema.config import NoemaConfig

_ENV = re.compile(r"^\$\{([^}]+)\}$")

# Stock top-level keys with no search effect or no noema equivalent.
_DROP_TOP = {
    "log_level",
    "log_dir",
    "allow_full_rewrites",
    "early_stopping_patience",
    "convergence_threshold",
    "early_stopping_metric",
    "max_tasks_per_child",
    "evolution_trace",
}

# LLMClientConfig fields carried from the stock flat llm block (model handled
# separately; primary_model_weight and ensemble fields are intentionally dropped).
_LLM_FIELDS = (
    "api_base",
    "api_key",
    "temperature",
    "top_p",
    "max_tokens",
    "timeout",
    "retries",
    "retry_delay",
)

# ---------------------------------------------------------------------------
# Layer-2 field audit (task 0132 comparability checklist, gate 2).
#
# The gate: for EVERY field in the stock YAML the translator must report either
# the noema field it maps to, or membership of an explicit non-transfer list
# with a reason. Unmapped-and-undeclared fields are a hard failure.
#
# This exists because both leniencies below would otherwise hide a divergence:
#   - NoemaConfig leaves the borrowed database/evaluator/prompt sections
#     unvalidated (config.py:319) and dacite runs strict=False, so an unknown
#     nested key is silently dropped;
#   - _flatten_llm copies a fixed allow-list, so any other llm key vanishes.
# A silently-dropped search-affecting field is precisely the failure mode the
# equivalence study is built to rule out.
# ---------------------------------------------------------------------------

# Stock key -> reason it legitimately does not transfer. Dotted, leaf-level.
_NON_TRANSFER: Dict[str, str] = {
    "log_level": "logging verbosity only; no search effect",
    "log_dir": "logging destination only; no search effect",
    "allow_full_rewrites": "dead key — not a field of openevolve's Config; ignored by both systems",
    "max_tasks_per_child": "process-pool recycling; inert at parallel_evaluations=1 (Decision #72)",
    "evolution_trace": "stock-side tracing sink; noema emits its own attempt_trace",
    "database.db_path": "run-directory path; the noema runner sets its own output dir",
    "llm.primary_model_weight": "single-model per seat: weight is inert (refused unless 1.0)",
    "llm.secondary_model": "multi-model ensemble; noema is single-model per seat",
    "llm.secondary_model_weight": "multi-model ensemble; noema is single-model per seat",
    "llm.models": "multi-model ensemble; noema is single-model per seat",
    "llm.evaluator_models": "noema does not run an LLM evaluator seat",
    "llm.name": "openevolve per-model label; no behavioural effect",
    "llm.weight": "openevolve ensemble sampling weight; single-model per seat",
    "llm.manual_mode": "openevolve's human-in-the-loop queue; not a study condition",
    "llm.init_client": "runtime client-construction flag; not a search parameter",
    "llm._manual_queue_dir": "private path for openevolve's manual mode",
    "prompt.use_template_stochasticity": "Decision #71: refused unless explicitly false",
    "evaluator.parallel_evaluations": "Decision #72: sequential only (refused unless 1)",
}

# Stock keys that map to a differently-named noema field.
_REMAPPED: Dict[str, str] = {
    "llm.primary_model": "llm.{mutation,coordination}.model",
    "llm.model": "llm.{mutation,coordination}.model",
    "llm.random_seed": "llm.{mutation,coordination}.seed",
    "prompt.num_top_programs": "num_top_programs (top level)",
    "prompt.num_diverse_programs": "num_inspirations (top level)",
}

# Stock keys that are search-affecting and have NO noema equivalent. Setting one
# means the two systems cannot be made to agree, so translation refuses.
_REFUSED_IF_SET: Dict[str, str] = {
    "early_stopping_patience": "noema has no early-stopping path; the run ends at max_iterations",
    "early_stopping_metric": "noema has no early-stopping path; the run ends at max_iterations",
    "convergence_threshold": "noema has no early-stopping path; the run ends at max_iterations",
    "llm.reasoning_effort": (
        "openevolve forwards this to the provider; noema exposes disable_reasoning "
        "instead, so the two cannot be made to send the same request"
    ),
    "llm.system_message": (
        "openevolve's per-LLM system message overrides prompt.system_message; "
        "noema has no per-seat override, so the rendered prompt would differ"
    ),
}

# Borrowed sections: noema imports openevolve's own dataclass, so membership in
# the upstream field set IS the 1:1 mapping proof.
_BORROWED = ("database", "evaluator", "prompt")


def _borrowed_fields(section: str) -> set:
    import dataclasses

    from openevolve.config import DatabaseConfig, EvaluatorConfig, PromptConfig

    cls = {"database": DatabaseConfig, "evaluator": EvaluatorConfig, "prompt": PromptConfig}[
        section
    ]
    return {f.name for f in dataclasses.fields(cls)}


def _noema_top_fields() -> set:
    import dataclasses

    return {f.name for f in dataclasses.fields(NoemaConfig)}


def audit(stock: Dict[str, Any]) -> list:
    """Classify every leaf field of a stock config.

    Returns a list of (stock_key, disposition, detail) rows. Disposition is one
    of "mapped", "non-transfer", or "UNMAPPED". Callers must treat any UNMAPPED
    row as a hard failure — `assert_fully_mapped` does that.
    """
    rows: list = []

    def classify(key: str, section: Optional[str]) -> None:
        if key in _NON_TRANSFER:
            rows.append((key, "non-transfer", _NON_TRANSFER[key]))
            return
        if key in _REFUSED_IF_SET:
            rows.append((key, "non-transfer", "REFUSED: " + _REFUSED_IF_SET[key]))
            return
        if key in _REMAPPED:
            rows.append((key, "mapped", _REMAPPED[key]))
            return
        if section is None:
            if key in _noema_top_fields():
                rows.append((key, "mapped", key))
            else:
                rows.append((key, "UNMAPPED", "no NoemaConfig top-level field"))
            return
        if section in _BORROWED:
            leaf = key.split(".", 1)[1]
            if leaf in _borrowed_fields(section):
                rows.append((key, "mapped", f"{key} (borrowed {section} dataclass, 1:1)"))
            else:
                rows.append(
                    (key, "UNMAPPED", f"not a field of openevolve's {section} config")
                )
            return
        if section == "llm":
            leaf = key.split(".", 1)[1]
            if leaf in _LLM_FIELDS:
                rows.append((key, "mapped", f"llm.{{mutation,coordination}}.{leaf}"))
            else:
                rows.append((key, "UNMAPPED", "not carried by _flatten_llm"))
            return
        rows.append((key, "UNMAPPED", f"unknown stock section {section!r}"))

    for key, value in stock.items():
        if isinstance(value, dict) and key in (*_BORROWED, "llm"):
            for leaf in value:
                classify(f"{key}.{leaf}", key)
        else:
            classify(key, None)
    return rows


def assert_fully_mapped(stock: Dict[str, Any]) -> list:
    """Hard-fail on any stock field that is neither mapped nor declared."""
    rows = audit(stock)
    unmapped = [(k, d) for k, disp, d in rows if disp == "UNMAPPED"]
    if unmapped:
        detail = "; ".join(f"{k} ({why})" for k, why in unmapped)
        raise ValueError(
            "stock config has field(s) that are neither mapped to a noema field "
            f"nor on the declared non-transfer list: {detail}. Add a mapping or a "
            "declared reason — a silently dropped field breaks layer 2 of the "
            "0132 comparability checklist."
        )
    return rows


def _resolve_env(value: Any) -> Any:
    """Resolve a ${VAR} api_key reference the way openevolve does, so both
    systems receive the same effective key."""
    if not isinstance(value, str):
        return value
    match = _ENV.match(value)
    if not match:
        return value
    got = os.environ.get(match.group(1))
    if got is None:
        raise ValueError(
            f"env var {match.group(1)} referenced in stock config api_key is not set"
        )
    return got


def _flatten_llm(stock_llm: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    secondary = stock_llm.get("secondary_model")
    weight = stock_llm.get("secondary_model_weight")
    if secondary and (weight is None or weight > 0):
        raise ValueError(
            "stock config sets a secondary_model ensemble; noema is single-model "
            "per seat (task 0132). Use a single primary_model."
        )
    if len(stock_llm.get("models") or []) > 1 or len(stock_llm.get("evaluator_models") or []) > 1:
        raise ValueError(
            "stock config sets a multi-model ensemble via models[]; noema is "
            "single-model per seat (task 0132)."
        )

    weight = stock_llm.get("primary_model_weight")
    if weight is not None and weight != 1.0:
        raise ValueError(
            f"stock config sets llm.primary_model_weight={weight}; noema is "
            "single-model per seat, so only 1.0 is translatable (task 0132)."
        )

    model = stock_llm.get("primary_model") or stock_llm.get("model")
    if not model:
        single = stock_llm.get("models") or []
        if len(single) == 1:
            model = single[0].get("name")
    if not model:
        raise ValueError("stock config has no primary_model/model to map")

    seat: Dict[str, Any] = {"model": model}
    for field in _LLM_FIELDS:
        if stock_llm.get(field) is not None:
            seat[field] = _resolve_env(stock_llm[field]) if field == "api_key" else stock_llm[field]
    # openevolve forwards llm.random_seed to the provider as the sampling seed;
    # noema's LLMClientConfig calls the same thing `seed`. Carry it, or the two
    # systems send different requests from the same YAML.
    if stock_llm.get("random_seed") is not None:
        seat["seed"] = stock_llm["random_seed"]
    # Two independent dicts so the two seats are separate objects.
    return {"mutation": dict(seat), "coordination": dict(seat)}


def stock_dict_to_noema_dict(
    stock: Dict[str, Any],
    *,
    arm: str,
    substrate: str,
    selection: str,
    num_previous_programs: int = 1,
    seed: Optional[int] = None,
    budget_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Pure dict->dict translation (no I/O), so it is trivially testable."""
    # Gate 2: no field may pass through unclassified. Runs first so an unknown
    # key fails here rather than being silently dropped downstream.
    assert_fully_mapped(stock)
    for key, reason in _REFUSED_IF_SET.items():
        section, _, leaf = key.partition(".")
        present = (
            key in stock
            if not leaf
            else isinstance(stock.get(section), dict) and leaf in stock[section]
        )
        if present:
            raise ValueError(f"stock config sets {key}, which is not translatable: {reason}")

    parallel = (stock.get("evaluator") or {}).get("parallel_evaluations")
    if parallel is not None and parallel != 1:
        raise ValueError(
            f"stock config sets evaluator.parallel_evaluations={parallel}; the "
            "comparable pair is noema-null vs stock-sequential, so only 1 is "
            "translatable (Decision #72)."
        )

    data: Dict[str, Any] = {k: v for k, v in stock.items() if k not in _DROP_TOP}

    if isinstance(data.get("llm"), dict):
        data["llm"] = _flatten_llm(data["llm"])

    prompt = dict(data.get("prompt") or {})
    # Refuse rather than silently fix: an unset value means stock runs stochastic
    # (its default) while noema requires it off — the two cannot agree.
    if prompt.get("use_template_stochasticity") is not False:
        raise ValueError(
            "canonical stock config must set prompt.use_template_stochasticity: "
            "false — stock defaults it true, so an unset value means stock and "
            "noema cannot render the same prompt (Decision #71)."
        )
    # Context-count fields live at noema top-level; the borrowed PromptConfig
    # copies are inert (controller reads top-level), so move them.
    if "num_top_programs" in prompt:
        data["num_top_programs"] = prompt.pop("num_top_programs")
    if "num_diverse_programs" in prompt:
        data["num_inspirations"] = prompt.pop("num_diverse_programs")
    data["num_previous_programs"] = num_previous_programs
    data["prompt"] = prompt

    # db_path in the stock YAML points at a stock run dir; the noema runner sets
    # its own output dir, so drop it rather than carry a misleading path.
    database = dict(data.get("database") or {})
    database.pop("db_path", None)
    if database:
        data["database"] = database

    if seed is not None:
        data["random_seed"] = seed

    data["coordination"] = {"module": arm}
    data["substrate"] = {"kind": substrate}
    data["selection"] = {"policy": selection}
    if budget_tokens is not None:
        data["budget"] = {"total_tokens": budget_tokens}
    return data


def noema_config_from_stock_yaml(
    path: str | Path,
    *,
    arm: str = "null",
    substrate: str = "islands",
    selection: str = "stock_openevolve",
    num_previous_programs: int = 1,
    seed: Optional[int] = None,
    budget_tokens: Optional[int] = None,
) -> NoemaConfig:
    """Build a NoemaConfig whose search-affecting parameters match the stock YAML."""
    stock = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return NoemaConfig.from_dict(
        stock_dict_to_noema_dict(
            stock,
            arm=arm,
            substrate=substrate,
            selection=selection,
            num_previous_programs=num_previous_programs,
            seed=seed,
            budget_tokens=budget_tokens,
        )
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stock_yaml", type=Path)
    ap.add_argument("--arm", default="null")
    ap.add_argument("--substrate", default="islands")
    ap.add_argument("--selection", default="stock_openevolve")
    ap.add_argument("--num-previous-programs", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument(
        "--audit",
        action="store_true",
        help="print the per-field mapping table (gate 2) instead of the config",
    )
    args = ap.parse_args()

    if args.audit:
        stock = yaml.safe_load(Path(args.stock_yaml).read_text(encoding="utf-8")) or {}
        rows = audit(stock)
        width = max(len(k) for k, _, _ in rows)
        for key, disposition, detail in sorted(rows):
            print(f"{key:<{width}}  {disposition:<12}  {detail}")
        bad = sum(1 for _, d, _ in rows if d == "UNMAPPED")
        print(f"\n{len(rows)} fields; {bad} unmapped")
        raise SystemExit(1 if bad else 0)

    cfg = noema_config_from_stock_yaml(
        args.stock_yaml,
        arm=args.arm,
        substrate=args.substrate,
        selection=args.selection,
        num_previous_programs=args.num_previous_programs,
        seed=args.seed,
    )
    print(cfg.to_yaml())
