# 0188 Stage 5 — two-way mutation matrix

- Repo commit: `9deea94` (branch `cursor/0186-fidelity-inventory`)
- Upstream pin: `openevolve` @ `80945ed` (`pyproject.toml:20`), installed at `/root/noema-evolve/.venv/lib/python3.12/site-packages/openevolve/`
- Mutants: 40 (Option C — wrapper stores + novelty guard + PromptSampler routing)
- Matrix collection (Scope A, Rule 2 oracle): 1012 nodes, 959 green at baseline
- Declared population (Scope B, Rule 1 rows): 112 baseline-green nodes
- Full per-cell record: `mutation-matrix.csv` (rows = baseline-green tests, columns = mutants, `K` = killed).

## Verdicts

- **Rule 1 — every population test is killed by >=1 mutant:** VIOLATED (47 placebo(s))
- **Rule 2 — every mutant is killed by >=1 test:** VIOLATED (13 survivor(s))

Rule 2 detail: 20 pinned (killed by a population test), 7 incidentally covered (killed only outside the population, or only by an aggregate guard), 13 coverage holes.

## Rule 1 violations — tests killed by zero mutants

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
- `tests/test_noema_islands_fidelity_spec.py::IslandsStockFidelitySpec::test_omitted_selection_and_old_database_config_mean_stock`
- `tests/test_noema_islands_fidelity_spec.py::IslandsStockFidelitySpec::test_stock_has_no_numpy_or_program_metadata_side_effects`
- `tests/test_noema_islands_fidelity_spec.py::IslandsStockFidelitySpec::test_stock_is_one_atomic_delegation_to_openevolve`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandBestTrackingThroughWrapper::test_better_program_raises_the_island_best`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandBestTrackingThroughWrapper::test_combined_score_not_raw_score_drives_the_island_best`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandBestTrackingThroughWrapper::test_worse_program_does_not_lower_the_island_best`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandMapElitesThroughWrapper::test_checkpoint_round_trip_preserves_per_island_placement`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandMapElitesThroughWrapper::test_no_migrant_suffix_ids_are_ever_generated`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandMigrationThroughWrapper::test_single_island_configuration_never_grows_by_migration`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandPlacementThroughWrapper::test_child_inherits_parent_island_when_no_target_specified`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandSelectionThroughWrapper::test_empty_island_selection_still_returns_a_parent`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandSelectionThroughWrapper::test_exploitation_mode_still_returns_a_live_program`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandSelectionThroughWrapper::test_exploration_mode_spreads_across_the_island`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandSelectionThroughWrapper::test_inspirations_come_from_the_parents_island`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandSelectionThroughWrapper::test_selection_parent_and_inspirations_share_the_requested_island`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandSelectionThroughWrapper::test_single_program_island_returns_it_with_no_inspirations`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandTopProgramsThroughWrapper::test_empty_island_yields_no_top_programs`
- `tests/test_noema_islands_wrapper_fidelity_spec.py::TestIslandTopProgramsThroughWrapper::test_out_of_range_scope_raises_indexerror`
- `tests/test_noema_prompts.py::TestPromptAssembly::test_empty_advice_is_byte_identical`
- `tests/test_noema_prompts.py::TestPromptAssembly::test_prompt_deterministic_across_builds`
- `tests/test_noema_substrate.py::TestSubstrateDatabase::test_fitness_uses_combined_score`
- `tests/test_noema_substrate.py::TestSubstrateDatabase::test_island_fitnesses`
- `tests/test_noema_substrate.py::TestSubstrateDatabase::test_top_programs_ordering`

## Rule 2 violations — mutants killed by zero tests

- `database.all_fitnesses.island_zero`
- `database.get.best_fallback`
- `database.sample_from_island.drop_inspirations`
- `database.save.iteration_zero`
- `database.store_artifacts.noop`
- `islands.capabilities.overclaim`
- `islands.elites.worst_first`
- `islands.load_state_dict.ignore_state`
- `islands.snapshot.limit_before_sort`
- `islands.state_dict.empty`
- `islands.top_programs.island_wins`
- `prompts.build_mutation_prompt.unfiltered_metrics`
- `prompts.build_mutation_prompt.unfiltered_program_lists`

## Incidentally covered mutants

Killed, but only outside the declared population or only by an aggregate guard (triage ledger parity, servable-surface routing, donor collection count). Not a coverage hole; not evidence of a pin either.

- `database.feature_dimensions.empty` — 1 killer(s), e.g. `tests/test_noema_fitness_characterization.py::TestCallSiteShapes::test_stores_and_views_pass_their_feature_dimensions_through`
- `database.fitness.no_feature_exclusion` — 1 killer(s), e.g. `tests/test_noema_fitness_characterization.py::TestCallSiteShapes::test_stores_and_views_pass_their_feature_dimensions_through`
- `islands.regions.generic_label` — 5 killer(s), e.g. `tests/test_noema_cross_substrate_portability.py::TestPESDeclaresItsTopologyAdaptation::test_faithful_planner_renders_native_labels_per_substrate`
- `islands.snapshot.regions_always` — 1 killer(s), e.g. `tests/test_noema_region_context.py::TestIslandsStoreRegions::test_regions_ride_on_the_global_snapshot_only`
- `islands.steps_per_generation.one` — 4 killer(s), e.g. `tests/test_noema_agent_arm_sweep.py::TestEveryArmFiresPerChildHooks::test_every_registry_arm_participates_in_one_accepted_child`
- `islands.target_scope.off_by_one` — 2 killer(s), e.g. `tests/test_noema_controller_upstream_fidelity_spec.py::TestControllerBackedOpenEvolveIslandSelection::test_controller_iteration_sampling_uses_the_iteration_target_scope_sequence`
- `islands.topology.flat` — 4 killer(s), e.g. `tests/test_noema_cross_substrate_portability.py::TestPESDeclaresItsTopologyAdaptation::test_adaptation_is_declared_only_off_islands`

## Pinned mutants

- `database.best_program.island_zero` — 2 population killer(s)
- `database.end_generation.always_false` — 12 population killer(s)
- `database.end_generation.bare_increment` — 2 population killer(s)
- `database.init.no_novelty_guard` — 1 population killer(s)
- `database.island_fitnesses.global` — 4 population killer(s)
- `database.last_iteration.off_by_one` — 1 population killer(s)
- `database.load.reset_generations` — 1 population killer(s)
- `database.num_islands.off_by_one` — 32 population killer(s)
- `database.num_programs.off_by_one` — 2 population killer(s)
- `database.per_island_bests.neg_inf_default` — 4 population killer(s)
- `islands.add.ignore_target_scope` — 16 population killer(s)
- `islands.native_select.source_is_target` — 3 population killer(s)
- `islands.per_scope_bests.global_best` — 4 population killer(s)
- `islands.population.ignore_scope` — 13 population killer(s)
- `islands.scopes.drop_last` — 13 population killer(s)
- `islands.top_programs.reversed` — 1 population killer(s)
- `prompts.build_mutation_prompt.drop_parent2` — 1 population killer(s)
- `prompts.inject_advice.no_header` — 1 population killer(s)
- `prompts.make_prompt_sampler.allow_stochasticity` — 1 population killer(s)
- `prompts.make_prompt_sampler.skip_template_registration` — 3 population killer(s)
