# HiFo Phase-C Reflection Catalogue

## Provenance

This appendix records the retained HiFo reflections from the 28 July 2026 Phase-C
native-selection sweep (`bb3a147`). It covers the final checkpoint of each
`hifo × substrate` arm:

- islands: 199 mutations, checkpoint 198;
- tree: 198 mutations, checkpoint 197;
- CVT: 191 mutations, checkpoint 190.

The hindsight lists below are the complete final `InsightPool.tips` contents
(30 tips per arm). The evolution traces save the same three selected tips and a
foresight directive in each mutation prompt. Repeating those prompt blocks would
add hundreds of copies, so the foresight section lists every distinct saved
directive with its exact injection count.

## Duplicate-admission audit

`InsightPool.add_tip()` rejects an incoming tip only when `_similarity()` is
strictly greater than `0.7`. Despite being described as Jaccard in discussion,
that function is **not Jaccard similarity**: it lowercases and whitespace-splits
each tip, then computes `|intersection| / max(|left|, |right|)`. True Jaccard
would divide by `|union|`.

The retained final pools satisfy the implemented invariant:

| Arm | Tip pairs | Pairs above 0.7 | Largest implemented score | Largest true Jaccard |
| --- | ---: | ---: | ---: | ---: |
| islands | 435 | 0 | 0.391 | 0.266 |
| tree | 435 | 0 | 0.550 | 0.423 |
| CVT | 435 | 0 | 0.581 | 0.419 |

The existing unit test `test_add_tip_rejects_near_duplicates` passes. This proves
the rule is implemented and that no final retained pair violates it. It does
**not** prove an extraction candidate was rejected during these runs: candidate
text, its score, and the accept/reject decision were not logged. A conclusive
runtime check would record each candidate alongside its maximum similarity, the
matched tip, and the admission outcome, then assert that no rejected candidate
appears in a later checkpoint.

The absence of lexical duplicates should not be read as semantic novelty. For
example, the closest CVT pair restates the same local-versus-global packing
principle at scores of 0.581 (implemented metric) and 0.419 (true Jaccard).

## Hindsight — islands

- Exploit the gap between offline optimality and online constraints by evolving instance-specific heuristics that prioritize immediate decisions based on local bin state, rather than relying on static global ordering.
- **Evolve the decision heuristic within a fixed, domain-appropriate harness to separate search for scoring logic from immutable problem constraints.**
- **Design the scoring function to evaluate partial solutions (e.g., open bins) based on future packing potential, not just immediate fit.**
- **Design the evaluation metric to directly reflect the performance gap (e.g., mean excess over optimal) rather than absolute correctness, enabling the evolutionary process to discover non-obvious, high-quality strategies in domains where trivial solutions exist.**
- **Decompose the problem into a local, evolvable scoring heuristic while maintaining a fixed, globally optimal decision framework.** The algorithm separates the immutable "harness" (which handles global constraints like bin capacity and the rule to place an item in the highest-scoring bin) from the evolvable "heuristic" (which scores individual bins for the current item). This allows the search for performance gains to focus on a local, context-dependent decision rule without violating the global problem structure.
- **Constrain the solution space to a regime where heuristic quality directly impacts performance, avoiding trivial or near-optimal baselines.** The description explicitly avoids offline heuristics (like First-Fit-Decreasing) that leave no room for improvement. By enforcing an online, immediate-decision constraint, the algorithm ensures that the evolved heuristic must genuinely solve a non-trivial trade-off, making the optimization process meaningful and the resulting patterns transferable to other problems where local decisions have long-term consequences.
- Isolate a critical decision heuristic within a fixed algorithmic framework, then evolve that heuristic to adapt to problem-specific trade-offs while maintaining overall structural guarantees.
- When making sequential decisions, evaluate each option based on its impact on future flexibility rather than only on immediate fit.
- **Evolve a per-decision heuristic that scores available actions in real time, rather than attempting to precompute a full static ordering or policy, enabling the algorithm to adapt to dynamic, online constraints while preserving the ability to exploit local structure.**
- **Embed the evolvable component within a fixed, problem-specific harness that enforces feasibility and global constraints, ensuring that the search for high-performance local rules does not violate the overall solution structure.**
- Balance local optimization with global solution structure when making decisions.
- Exploit online decision-making constraints by designing heuristics that score partial solutions incrementally, enabling adaptive trade-offs between immediate fit quality and long-term packing efficiency.
- Embed evolvable, domain-specific scoring functions within a fixed algorithmic harness to allow automated discovery of heuristics that outperform classical greedy rules like best-fit.
- Evolve a scoring function that guides greedy decisions, balancing immediate local fit with the potential to accommodate future items.
- Isolate the core decision heuristic as an optimizable module, allowing the overall algorithm structure to remain fixed while the heuristic is adapted to the problem instance.
- **Use online, irrevocable decision-making with a learned scoring heuristic to balance immediate fit against long-term packing efficiency.**
- **Allow the core decision heuristic to evolve adaptively to the problem instance, rather than relying on a fixed, static rule.**
- **Maintain solution flexibility by scoring open partial solutions (bins) relative to the incoming item, rather than greedily committing to a simple first-fit rule.**
- **Decompose the decision into an immediate scoring function that is decoupled from the global solver harness.** The code separates the static, non-evolvable harness (which enforces constraints and executes placement) from a lightweight, evolvable scoring mechanism (F_mut). This allows the optimization algorithm to focus on learning a high-quality local ranking heuristic without needing to reinvent the procedural logic of constraint satisfaction, enabling the search to find patterns that improve global outcomes (like bin count) through local, per-step choices.
- **Formulate the problem to preserve and exploit the inherent uncertainty of the solution space.** The benchmark deliberately uses an *online* formulation (irreversible decisions with incomplete future information) rather than an offline one. This ensures that the heuristic must balance immediate fit against future flexibility, preventing the algorithm from converging on a trivial, high-performing solution (like First-Fit-Decreasing) and creating a genuine landscape for evolutionary improvement. This design principle of injecting or preserving decision-time uncertainty forces the optimizer to discover robust, adaptive strategies.
- Apply complete solution rewrites as a diversification mechanism to avoid premature convergence.
- Reconstruct solutions from scratch using randomized heuristics to ensure diversity and avoid premature convergence.
- **When an existing algorithmic approach reaches a performance plateau or is fundamentally misaligned with the problem's structure, discard its core logic entirely and construct a new solution framework from the ground up, rather than iteratively patching or tuning the existing one.**
- **Leverage the ability to completely restructure the decision space, enabling the exploration of solution architectures inaccessible through incremental modifications.**
- Periodically restart the optimization process from scratch to escape local optima and inject fresh diversity into the search.
- **Periodically discard and reconstruct the entire solution to escape local optima and explore distant regions of the search space.**
- **Use the information from a previous solution as a blueprint for a completely new construction, rather than making incremental repairs or mutations.**
- **Consider radical redesign instead of incremental tuning when performance plateaus, as a full rewrite allows for the integration of fresh theoretical insights and the elimination of accumulated inefficiencies.**
- Periodically discard accumulated algorithmic complexity and redesign the core decision logic to eliminate legacy inefficiencies and incorporate fresh structural insights.
- Use complete algorithmic restructuring as a deliberate meta-strategy to escape local optima in the design space, enabling fundamentally different search dynamics.

## Hindsight — tree

- Employ machine learning or pattern recognition to mine deep problem structures and optimal solution patterns then use learned insights to intelligently bias towards promising search regions or constructive choices
- Explore objective function engineering by introducing auxiliary or surrogate objectives or by dynamically adjusting weights to reshape the search landscape aiding escape from local optima or guiding diverse exploration
- Combine fine-grained local refinement with high-impact structural mutation (e.g., full rewrites) to effectively balance the exploitation of current solutions with the exploration of novel solution architectures.
- **Decompose the problem into a deterministic harness and a single, evolvable decision heuristic.** By fixing the overall decision flow (e.g., immediate placement, no reordering) and isolating only the scoring function for each option, the algorithm creates a clean optimization target. This separation allows the search to focus on improving the quality of local decisions without destabilizing the global solution structure, making the approach transferable to any problem where a fixed decision loop can be paired with a tunable evaluation rule.
- **Design the scoring heuristic to explicitly balance immediate packing efficiency (local fit) with preservation of bin capacity diversity (future flexibility), preventing premature consolidation that degrades overall solution quality.**
- **Decompose the problem into a fixed global framework and a localized, evolvable decision heuristic.** The algorithm separates the immutable online placement harness (global structure) from a mutable scoring function (F_mut) that evaluates local options. This allows the core optimization logic to be refined through search without destabilizing the overall solution architecture.
- **Use a scoring mechanism that ranks feasible local actions to guide a greedy, immediate decision.** The heuristic scores each open bin for the arriving item, and the item is placed in the highest-scoring bin that fits. This pattern transforms a complex sequential decision into a simple, greedy selection based on a learned or evolved ranking, preserving the online constraint while enabling performance improvement through heuristic optimization.
- Decouple the invariant algorithmic skeleton from the evolvable decision heuristic.
- Prioritize choices that maintain flexibility for future decision-making steps.
- **Decompose the problem into a fixed, optimal harness and an evolvable, local heuristic.** The harness handles the global solution structure (e.g., online placement, feasibility constraints) with a known optimal or near-optimal framework, while only the local decision-making rule (the scoring heuristic) is subject to evolution or optimization. This isolates the hard-to-design, problem-specific "tactical" choice from the robust "strategic" skeleton.
- **Restrict optimization to the part of the algorithm where a meaningful performance gap exists.** The description explicitly avoids optimizing an offline heuristic (which is already near-optimal) and instead targets the online heuristic, where a real gap to optimal remains. This principle dictates that optimization effort should be concentrated on the algorithmic component that is both critical to performance and not already solved by a known optimal or near-optimal strategy.
- **Restrict the decision space to a specific, non-trivial sub-problem where heuristic improvement yields measurable impact.** The benchmark deliberately shifts from an offline setting (where the optimal heuristic is known and leaves no room for evolution) to an online setting (where the per-bin heuristic directly determines performance). This principle dictates that for optimization, one should isolate the "evolvable" or tunable component to a part of the problem where the optimal decision is not trivially known, ensuring that any improvement in the heuristic translates directly into a measurable performance gain.
- **Use a greedy, scoring-based local decision rule that evaluates future flexibility via a dynamic, per-item metric.** The algorithm places each arriving item into the bin with the highest score that fits, rather than using a static rule (e.g., first-fit). This pattern combines immediate feasibility (local constraint) with a learned scoring function that implicitly encodes global packing efficiency. The design principle is to frame each sequential decision as a maximization over a learned or evolved utility function, where the function itself is optimized to balance immediate fit with the preservation of capacity for future items.
- Balance immediate optimality with long-term flexibility by using a scoring heuristic that considers both current fit and future packing potential.
- Separate the decision criteria from the decision rule, allowing the heuristic to be evolved or optimized independently of the placement logic.
- In sequential decision-making, design scoring functions that evaluate candidate choices based on their anticipated impact on future resource usage, and optimize these functions through meta-heuristic search to balance local fit with long-term global objectives.
- Balance local optimization with global solution structure when making decisions.
- Use a tunable scoring function to guide each local decision, balancing immediate feasibility with long-term global efficiency through learning or evolution.
- Separate the decision heuristic from the fixed algorithmic framework, allowing the heuristic to be independently optimized while the rest of the problem-solving structure remains unchanged.
- Balance immediate placement decisions with long-term packing efficiency when scoring candidate bins.
- Prioritize heuristics that preserve flexibility for future items by avoiding premature commitment to suboptimal configurations.
- **Periodically reset the solution to a fresh state to escape local optima and maintain global search diversity.**
- **Favor complete reconstruction over incremental modification when the solution landscape is rugged or deceptive.**
- Design the algorithm with a modular, decoupled structure that allows entire components to be replaced or rewritten independently, enabling rapid adaptation to changing problem characteristics.
- Periodically replace the current solution with a completely new one generated from scratch to escape local optima and explore uncharted regions of the search space.
- Use full solution reconstruction rather than incremental modifications to ensure global structural changes and prevent premature convergence.
- Periodically discard existing solution structures and rebuild from scratch to escape local optima.
- Embrace radical redesign rather than incremental improvement to avoid inherited biases.
- Apply strong perturbations that fundamentally alter solution structure to prevent premature convergence and maintain search vitality.
- Leverage accumulated search knowledge to guide solution reconstruction, balancing radical exploration with learned structural patterns.

## Hindsight — CVT

- Evaluate each decision based on its impact on the flexibility of remaining solution space, not just its immediate cost or feasibility.
- Separate the core decision rule from a parameterized scoring function that can be independently optimized to improve long-term problem-solving performance.
- Balance local optimization with global solution structure when making decisions.
- Prioritize choices that maintain flexibility for future decision-making steps.
- **Design the decision-making heuristic to operate on a limited, online information horizon, ensuring that each choice is locally optimal under the constraint of immediate commitment, which forces the algorithm to discover robust packing strategies rather than exploiting global reordering.**
- In online settings with irreversible decisions, design the heuristic to incorporate features that measure future flexibility, avoiding premature commitments that degrade long-term performance.
- Design the decision heuristic to evaluate each candidate action by its immediate fit quality, not by a global optimum, enabling online adaptation without reordering.
- Use a learned or optimized scoring function to evaluate each feasible decision, then greedily select the highest-scoring option, as the scoring function can encode long-term consequences beyond immediate fit.
- In online decision-making, prioritize choices that preserve flexibility for future items, since irreversible decisions require the heuristic to balance current packing with future packing potential.
- In online sequential decision problems, evaluate each available choice using a heuristic that scores its potential to accommodate future demands, rather than solely maximizing immediate benefit.
- Employ adaptive scoring functions that can be evolved or learned to improve global performance, as the relative importance of local and future constraints may vary with problem instance characteristics.
- Concentrate the optimization search on the specific decision contexts where the performance gap between simple baseline methods and optimality is greatest.
- Use a scoring function that evaluates each local decision not only by its immediate feasibility but also by its impact on future solution flexibility and overall solution quality.
- Design decision-making heuristics to balance greedy local optimization with awareness of global structural constraints, enabling adaptive trade-offs that improve long-term performance.
- Balance local optimization with global solution structure when making decisions, as demonstrated by the online bin packing heuristic that scores bins not only on immediate fit but also on long-term packing efficiency.
- Prioritize choices that maintain flexibility for future decision-making steps, mirroring how the evolved heuristic avoids overly tight packing to accommodate subsequent items.
- Separate structural constraint enforcement from heuristic evolution to enable safe and focused strategy improvement.
- Focus optimization on decision points where local choices have the greatest impact on global solution quality.
- **Balance local optimization with global solution structure when making decisions, as seen in online bin packing where the heuristic scores bins to fit the current item while preserving capacity for future items.**
- **Use an evolvable decision policy that can be optimized via search or learning, rather than a fixed rule, to exploit problem-specific patterns and improve performance over generic heuristics.**
- Use a learnable scoring function to guide greedy decisions in online settings, enabling adaptation to problem-specific patterns while maintaining computational efficiency.
- Favor decisions that preserve future flexibility by avoiding premature commitment of resources, thereby improving long-term solution quality.
- **Balance immediate local fit with the preservation of capacity for future items when designing online decision heuristics.**
- **Use a parameterized scoring function that can be evolved to capture complex trade-offs between short-term gains and long-term solution quality.**
- Decompose the problem into a fixed, inviolable global constraint structure (e.g., online placement, no reordering) and an evolvable local decision heuristic, allowing optimization to focus on the component with the greatest performance leverage.
- Use a scoring- or ranking-based selection mechanism for local decisions, where the scoring function itself is the primary target for adaptation or learning, enabling the algorithm to discover non-obvious trade-offs between immediate fit quality and long-term packing efficiency.
- **Embed problem-specific constraints into the decision heuristic to enforce feasibility while leaving room for optimization.**
- **Design the decision process to make irreversible choices that preserve future flexibility.**
- **Evolve a per-decision heuristic within a fixed procedural harness to separate strategic adaptation from operational constraints.**
- **Use online decision-making that commits immediately to local choices, forcing the heuristic to account for long-term packing efficiency rather than relying on global reordering.**

## Foresight directive catalogue

Every logged directive below was paired with a navigator regime. The final state
was `exploration` in all three arms. Snapshot-level regime counts were:

| Arm | Exploration | Exploitation | Balanced |
| --- | ---: | ---: | ---: |
| islands | 45 | 0 | 4 |
| tree | 177 | 9 | 11 |
| CVT | 182 | 3 | 5 |

### Islands

- experimenting with hybrid strategy combinations — 36
- considering completely different algorithmic paradigms — 36
- exploring novel solution construction methodologies — 35
- investigating alternative problem decomposition approaches — 34
- introducing new randomization or adaptive mechanisms — 28
- balancing local optimality with global search strategies — 4
- managing computational complexity and time efficiency — 2
- refining core evaluation and scoring functions — 1
- optimizing objective function evaluation criteria — 1
- improving precision of existing heuristics and rules — 1

### Tree

- considering completely different algorithmic paradigms — 37
- exploring novel solution construction methodologies — 36
- introducing new randomization or adaptive mechanisms — 35
- experimenting with hybrid strategy combinations — 35
- investigating alternative problem decomposition approaches — 22
- refining core evaluation and scoring functions — 5
- reducing unnecessary computational overhead and redundancy — 3
- optimizing objective function evaluation criteria — 2
- improving algorithm robustness across different problem instances — 2
- optimizing established successful strategies and patterns — 1
- managing computational complexity and time efficiency — 1
- improving precision of existing heuristics and rules — 1
- fine-tuning critical algorithm parameters and thresholds — 1
- considering long-term impact of current decisions — 1
- balancing local optimality with global search strategies — 1

### CVT

- experimenting with hybrid strategy combinations — 35
- exploring novel solution construction methodologies — 32
- introducing new randomization or adaptive mechanisms — 31
- investigating alternative problem decomposition approaches — 29
- considering completely different algorithmic paradigms — 27
- improving precision of existing heuristics and rules — 3
- refining core evaluation and scoring functions — 1
- optimizing objective function evaluation criteria — 1
- fine-tuning critical algorithm parameters and thresholds — 1
- balancing local optimality with global search strategies — 1

