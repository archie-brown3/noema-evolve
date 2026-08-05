# 0188 two-way mutation matrix — run 3, at the Stage 6 reduced head

<!-- GENERATED FILE — DO NOT EDIT BY HAND. Produced by scripts/mutation_matrix_report.py from artifacts/mutation/runs-3.jsonl.gz; regenerate instead of editing, or your change is silently reverted on the next run. This is dissertation evidence: the caveats below (Rule 1's literal-bar reinterpretation, the unchanged-population statement, the run-against-commit provenance) are load-bearing claims about what the matrix does and does not prove, not prose to be tightened. -->

- Reduced at commit `2ceb2b4` (HEAD when this report was generated), branch `cursor/0186-fidelity-inventory`. This is NOT necessarily the commit the matrix executed against — the driver runs first and the artifact is committed afterwards. The run-against commit is recorded in the stage note; for run 3 it was `6201c6e`, the Stage 6 reduction commit itself, so the population measured here IS the population that ships.
- **Supersedes run 2** as the dissertation artifact, per the Stage 6 orchestrator constraint: the two rules must hold on the *reduced* population, and this is that matrix. Run 2's numbers (123 population rows, 35 pinned / 5 incidental) describe a test population that no longer exists.
- Upstream pin: `openevolve` @ `80945ed` (`pyproject.toml:20`), installed at `/root/noema-evolve/.venv/lib/python3.12/site-packages/openevolve/`
- Mutants: 40 (Option C — wrapper stores + novelty guard + PromptSampler routing)
- Matrix collection (Scope A, Rule 2 oracle): 1009 nodes, 956 green at baseline
- Declared population (Scope B, Rule 1 rows): 109 baseline-green nodes
- Full per-cell record: `mutation-matrix.csv` (rows = baseline-green tests, columns = mutants, `K` = killed).

## Verdicts

- **Rule 1 — every population test is killed by >=1 mutant:** HOLDS where actionable — 0 unresolved placebo(s), 24 donor-routed and 6 no-mutant-in-scope nodes declared below. **This is a reinterpretation of the gate's literal bar, not a pass of it as written — the orchestrator owns that call.**
- **Rule 2 — every mutant is killed by >=1 test:** HOLDS

Rule 2 detail: 34 pinned (killed by a population test), 6 incidentally covered (killed only outside the population, or only by an aggregate guard), 0 coverage holes.

## Rule 1 violations — tests killed by zero mutants, with no declared reason

_None._

## Rule 1 declared exclusions — unkilled, but not placebos

Rule 1's only resolutions are *rewrite* and *delete*. These nodes admit neither, so they are named here rather than removed from the population — the population is exactly what the stage note stated before the first run. They are findings about the **catalogue's reach**, which is what a two-way matrix exists to surface.

### Donor-routed (24)

Byte-identical donor bodies under `AdapterRouted_*`. Editing one destroys the instrument whose entire value is that no donor byte is edited (spec §5 §3.0). Their discriminating power is upstream's business.

- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_concurrent_island_access_TestConcurrentIslandAccess::test_concurrent_island_state_modification_causes_race_condition`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_concurrent_island_access_TestConcurrentIslandAccess::test_sequential_island_access_works_correctly`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_database_TestProgramDatabase::test_add_and_get`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_database_TestProgramDatabase::test_best_program_tracking`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_database_TestProgramDatabase::test_get_best_program`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_database_TestProgramDatabase::test_population_limit_enforcement`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_child_placement_TestIslandChildPlacement::test_child_inherits_parent_island_when_no_target_specified`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_child_placement_TestRegressionOldBehavior::test_with_target_island_child_goes_to_target`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_child_placement_TestRegressionOldBehavior::test_without_target_island_child_inherits_parent`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_tracking_TestIslandTracking::test_better_program_updates_island_best`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_tracking_TestIslandTracking::test_empty_island_top_programs`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_tracking_TestIslandTracking::test_invalid_island_index_handling`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_tracking_TestIslandTracking::test_island_best_persistence`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_tracking_TestIslandTracking::test_island_best_with_combined_score`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_tracking_TestIslandTracking::test_migration_updates_island_best`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_island_tracking_TestIslandTracking::test_worse_program_does_not_update_island_best`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandEdgeCases::test_empty_island_fallback`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandEdgeCases::test_island_id_wrapping`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandEdgeCases::test_single_program_island`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandRatios::test_exploitation_uses_archive`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandRatios::test_exploration_exploitation_random_ratios`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandRatios::test_exploration_mode_uniform_distribution`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandRatios::test_sample_from_island_returns_from_correct_island`
- `tests/test_noema_islands_adapter_fidelity_spec.py::AdapterRouted_test_sample_from_island_ratios_TestSampleFromIslandRatios::test_sample_from_island_with_different_islands`

### Real claim, no mutant in Option C (6)

- `tests/test_noema_islands_fidelity_spec.py::IslandsStockFidelitySpec::test_omitted_selection_and_old_database_config_mean_stock` — a differential test between two IslandsStore instances — a class-level mutant changes BOTH sides identically, so no mutant of this shape can ever be detected by it
- `tests/test_noema_islands_fidelity_spec.py::IslandsStockFidelitySpec::test_stock_has_no_numpy_or_program_metadata_side_effects` — an absence claim (no numpy/metadata side effects); every Option C mutant is a wrong RETURN VALUE, none introduces a side effect
- `tests/test_noema_prompts.py::TestPromptAssembly::test_empty_advice_is_byte_identical` — killed by spec §4a's OPTIONAL 7th mutant prompts.inject_advice.always_separator, which Option C does not include
- `tests/test_noema_prompts.py::TestPromptAssembly::test_prompt_deterministic_across_builds` — no Option C mutant makes the sampler non-deterministic; make_prompt_sampler.allow_stochasticity drops the GUARD, which test_stochasticity_rejected already pins
- `tests/test_noema_substrate.py::TestSubstrateDatabase::test_fitness_uses_combined_score` — the only fitness mutant (database.fitness.no_feature_exclusion) is identical whenever combined_score is present — by construction, per spec §2b; a mutant that ignores combined_score is not in Option C
- `tests/test_noema_substrate.py::TestSubstrateDatabase::test_top_programs_ordering` — base-class SubstrateDatabase.top_programs; spec §1c excludes the shadowed base add/top_programs from the catalogue in favour of the IslandsStore overrides, so no mutant reaches this call path

## Rule 2 violations — mutants killed by zero tests

_None._

## Incidentally covered mutants

Killed, but only outside the declared population or only by an aggregate guard (triage ledger parity, servable-surface routing, donor collection count). Not a coverage hole; not evidence of a pin either.

- `database.feature_dimensions.empty` — 1 killer(s), e.g. `tests/test_noema_fitness_characterization.py::TestCallSiteShapes::test_stores_and_views_pass_their_feature_dimensions_through`
- `database.fitness.no_feature_exclusion` — 1 killer(s), e.g. `tests/test_noema_fitness_characterization.py::TestCallSiteShapes::test_stores_and_views_pass_their_feature_dimensions_through`
- `islands.regions.generic_label` — 5 killer(s), e.g. `tests/test_noema_cross_substrate_portability.py::TestPESDeclaresItsTopologyAdaptation::test_faithful_planner_renders_native_labels_per_substrate`
- `islands.snapshot.regions_always` — 1 killer(s), e.g. `tests/test_noema_region_context.py::TestIslandsStoreRegions::test_regions_ride_on_the_global_snapshot_only`
- `islands.target_scope.off_by_one` — 2 killer(s), e.g. `tests/test_noema_controller_upstream_fidelity_spec.py::TestControllerBackedOpenEvolveIslandSelection::test_controller_iteration_sampling_uses_the_iteration_target_scope_sequence`
- `islands.topology.flat` — 4 killer(s), e.g. `tests/test_noema_cross_substrate_portability.py::TestPESDeclaresItsTopologyAdaptation::test_adaptation_is_declared_only_off_islands`

## Pinned mutants

- `database.all_fitnesses.island_zero` — 1 population killer(s)
- `database.best_program.island_zero` — 1 population killer(s)
- `database.end_generation.always_false` — 13 population killer(s)
- `database.end_generation.bare_increment` — 2 population killer(s)
- `database.get.best_fallback` — 1 population killer(s)
- `database.init.no_novelty_guard` — 1 population killer(s)
- `database.island_fitnesses.global` — 6 population killer(s)
- `database.last_iteration.off_by_one` — 2 population killer(s)
- `database.load.reset_generations` — 1 population killer(s)
- `database.num_islands.off_by_one` — 29 population killer(s)
- `database.num_programs.off_by_one` — 3 population killer(s)
- `database.per_island_bests.neg_inf_default` — 2 population killer(s)
- `database.sample_from_island.drop_inspirations` — 1 population killer(s)
- `database.save.iteration_zero` — 1 population killer(s)
- `database.store_artifacts.noop` — 1 population killer(s)
- `islands.add.ignore_target_scope` — 19 population killer(s)
- `islands.capabilities.overclaim` — 1 population killer(s)
- `islands.elites.worst_first` — 1 population killer(s)
- `islands.load_state_dict.ignore_state` — 1 population killer(s)
- `islands.native_select.source_is_target` — 3 population killer(s)
- `islands.per_scope_bests.global_best` — 5 population killer(s)
- `islands.population.ignore_scope` — 16 population killer(s)
- `islands.scopes.drop_last` — 13 population killer(s)
- `islands.snapshot.limit_before_sort` — 1 population killer(s)
- `islands.state_dict.empty` — 1 population killer(s)
- `islands.steps_per_generation.one` — 1 population killer(s)
- `islands.top_programs.island_wins` — 1 population killer(s)
- `islands.top_programs.reversed` — 1 population killer(s)
- `prompts.build_mutation_prompt.drop_parent2` — 1 population killer(s)
- `prompts.build_mutation_prompt.unfiltered_metrics` — 1 population killer(s)
- `prompts.build_mutation_prompt.unfiltered_program_lists` — 1 population killer(s)
- `prompts.inject_advice.no_header` — 1 population killer(s)
- `prompts.make_prompt_sampler.allow_stochasticity` — 1 population killer(s)
- `prompts.make_prompt_sampler.skip_template_registration` — 3 population killer(s)
