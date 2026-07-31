"""Stock-YAML -> NoemaConfig translation must preserve every shared search
parameter and refuse the two genuinely non-transferable stock features."""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/0132"))
from stock_to_noema_config import (  # noqa: E402
    assert_fully_mapped,
    audit,
    stock_dict_to_noema_dict,
)
from noema.config import NoemaConfig  # noqa: E402


def _stock():
    """A stock config exercising every mapping bucket: flat llm, relocated prompt
    counts, a rich (non-lean) database section, diff mode, and drop-only keys."""
    return {
        "max_iterations": 200,
        "checkpoint_interval": 5,
        "log_level": "INFO",            # dropped
        "allow_full_rewrites": True,    # dropped (not even an openevolve field)
        "random_seed": 42,
        "diff_based_evolution": True,
        "max_code_length": 20000,
        "llm": {
            "primary_model": "deepseek/deepseek-v4-flash",
            "primary_model_weight": 1.0,
            "api_base": "https://openrouter.ai/api/v1",
            "temperature": 0.7,
            "top_p": 0.95,
            "max_tokens": 4096,
            "timeout": 300,
        },
        "prompt": {
            "system_message": "S",
            "num_top_programs": 1,
            "num_diverse_programs": 0,
            "include_artifacts": False,
            "use_template_stochasticity": False,
        },
        "database": {
            "population_size": 60,
            "num_islands": 4,
            "migration_interval": 25,
            "migration_rate": 0.2,
            "feature_dimensions": ["score", "complexity"],
            "feature_bins": 12,
            "db_path": "/stock/run/db",  # dropped
        },
        "evaluator": {"cascade_evaluation": False, "cascade_thresholds": [0.4, 0.8]},
    }


def _config(**overrides):
    d = stock_dict_to_noema_dict(
        _stock(), arm="null", substrate="islands", selection="stock_openevolve", **overrides
    )
    return NoemaConfig.from_dict(d), d


def test_shared_parameters_survive_exactly():
    cfg, _ = _config()
    # top-level
    assert cfg.max_iterations == 200
    assert cfg.random_seed == 42
    assert cfg.diff_based_evolution is True
    assert cfg.max_code_length == 20000
    # llm flattened to both seats, single model
    assert cfg.llm.mutation.model == "deepseek/deepseek-v4-flash"
    assert cfg.llm.coordination.model == "deepseek/deepseek-v4-flash"
    assert cfg.llm.mutation.temperature == 0.7
    assert cfg.llm.mutation.top_p == 0.95
    # rich database section carried (honoured via openevolve's ProgramDatabase)
    assert cfg.database.population_size == 60
    assert cfg.database.num_islands == 4
    assert cfg.database.migration_interval == 25
    assert cfg.database.migration_rate == 0.2
    assert cfg.database.feature_dimensions == ["score", "complexity"]
    assert cfg.database.feature_bins == 12
    assert cfg.evaluator.cascade_thresholds == [0.4, 0.8]


def test_prompt_counts_relocate_to_top_level():
    cfg, d = _config()
    assert cfg.num_top_programs == 1          # from prompt.num_top_programs
    assert cfg.num_inspirations == 0          # from prompt.num_diverse_programs
    assert cfg.num_previous_programs == 1     # explicit param default
    # and they are removed from the prompt section so the inert copies can't drift
    assert "num_top_programs" not in d["prompt"]
    assert "num_diverse_programs" not in d["prompt"]


def test_noema_only_sections_come_from_args():
    cfg, _ = _config()
    assert cfg.coordination.module == "null"
    assert cfg.substrate.kind == "islands"
    assert cfg.selection.policy == "stock_openevolve"


def test_dead_and_stock_only_keys_are_dropped():
    _, d = _config()
    assert "log_level" not in d
    assert "allow_full_rewrites" not in d
    assert "db_path" not in d.get("database", {})


def test_seed_override():
    cfg, _ = _config(seed=44)
    assert cfg.random_seed == 44


def test_ensemble_is_refused():
    stock = _stock()
    stock["llm"]["secondary_model"] = "some/other-model"
    with pytest.raises(ValueError, match="ensemble"):
        stock_dict_to_noema_dict(
            stock, arm="null", substrate="islands", selection="stock_openevolve"
        )


def test_unset_stochasticity_is_refused():
    stock = _stock()
    del stock["prompt"]["use_template_stochasticity"]
    with pytest.raises(ValueError, match="use_template_stochasticity"):
        stock_dict_to_noema_dict(
            stock, arm="null", substrate="islands", selection="stock_openevolve"
        )


# --- layer-2 gate: every field mapped or declared ---------------------------


def test_every_field_of_the_fixture_is_classified():
    rows = audit(_stock())
    assert rows and all(disp != "UNMAPPED" for _, disp, _ in rows)
    # and every leaf key of the fixture appears exactly once
    expected = sum(
        len(v) if isinstance(v, dict) and k in ("llm", "prompt", "database", "evaluator") else 1
        for k, v in _stock().items()
    )
    assert len(rows) == expected


def test_unknown_top_level_key_is_a_hard_failure():
    stock = _stock()
    stock["totally_new_knob"] = 3
    with pytest.raises(ValueError, match="neither mapped .* nor on the declared"):
        assert_fully_mapped(stock)


def test_unknown_key_in_a_borrowed_section_is_a_hard_failure():
    """NoemaConfig leaves database/evaluator/prompt unvalidated and dacite runs
    strict=False, so this key would otherwise be silently dropped — the exact
    layer-2 failure mode the audit exists to catch."""
    stock = _stock()
    stock["database"]["not_a_real_field"] = 999
    # confirm the silent-drop path really is silent...
    assert NoemaConfig.from_dict({"database": dict(stock["database"])}) is not None
    # ...and that translation now refuses it
    with pytest.raises(ValueError, match="not a field of openevolve's database config"):
        stock_dict_to_noema_dict(
            stock, arm="null", substrate="islands", selection="stock_openevolve"
        )


def test_unknown_llm_key_is_a_hard_failure():
    stock = _stock()
    stock["llm"]["mystery_knob"] = 1
    with pytest.raises(ValueError, match="not carried by _flatten_llm"):
        stock_dict_to_noema_dict(
            stock, arm="null", substrate="islands", selection="stock_openevolve"
        )


def test_llm_random_seed_reaches_both_seats():
    stock = _stock()
    stock["llm"]["random_seed"] = 7
    cfg = NoemaConfig.from_dict(
        stock_dict_to_noema_dict(
            stock, arm="null", substrate="islands", selection="stock_openevolve"
        )
    )
    assert cfg.llm.mutation.seed == 7
    assert cfg.llm.coordination.seed == 7


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda s: s["llm"].__setitem__("reasoning_effort", "high"), "reasoning_effort"),
        (lambda s: s["llm"].__setitem__("system_message", "override"), "system_message"),
        (lambda s: s.__setitem__("early_stopping_patience", 10), "early_stopping_patience"),
        (lambda s: s["evaluator"].__setitem__("parallel_evaluations", 4), "parallel_evaluations"),
        (lambda s: s["llm"].__setitem__("primary_model_weight", 0.8), "primary_model_weight"),
    ],
)
def test_untranslatable_settings_are_refused(mutate, match):
    stock = _stock()
    mutate(stock)
    with pytest.raises(ValueError, match=match):
        stock_dict_to_noema_dict(
            stock, arm="null", substrate="islands", selection="stock_openevolve"
        )


@pytest.mark.parametrize(
    "path",
    [
        "experiments/0132/generated-configs/stock-sequential-s42.yaml",
        ".openevolve-upstream/examples/circle_packing/config_phase_1.yaml",
        ".openevolve-upstream/examples/circle_packing/config_phase_2.yaml",
    ],
)
def test_real_stock_configs_are_fully_mapped(path):
    """The gate itself, against the configs the study actually uses."""
    repo = Path(__file__).resolve().parents[1]
    stock = yaml.safe_load((repo / path).read_text(encoding="utf-8")) or {}
    rows = assert_fully_mapped(stock)
    assert len(rows) > 20
