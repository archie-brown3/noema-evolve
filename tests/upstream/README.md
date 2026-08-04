# Upstream test inventory

Raw donor dumps for fidelity review. **Not part of the noema suite** —
`tests/conftest.py` ignores any path under `upstream/`, and `pyproject.toml`
sets `testpaths = ["tests"]` plus `--ignore=tests/upstream`.

| slug | url | commit | paths copied | notes |
|------|-----|--------|----------------|-------|
| openevolve | https://github.com/codelion/openevolve | 80945ed82886d5c4ff2f3d22436765d50cb61266 | examples/attention_optimization/tests/,tests/,examples/mlx_metal_kernel_opt/quick_benchmark_test.py,examples/mlx_metal_kernel_opt/test_optimized_attention.py,openevolve/test_regional_endpoint.py | pin 80945ed |
| hifo-prompt | https://github.com/Challenger-XJTU/HiFo-Prompt | e64ce9edbfb4c8ebffd652b785b0c87261785586 | hifo/src/hifo/test/ |  |
| loongflow | https://github.com/baidu-baige/LoongFlow | 945c78bc1554f8281aac40320b3599bd68d528d7 | tests/ | Boltzmann also PyPI loongflow==0.0.1 |
| shinkaevolve | https://github.com/SakanaAI/ShinkaEvolve | a81940026ef841113676b081090318b26a6a89b5 | tests/ |  |
| levi | https://github.com/ttanv/levi | 341bd17d78c85441502d1d20663755d7748e9902 | tests/ |  |
| reevo | https://github.com/ai4co/reevo | 6dce18257da5e11db2d138e417a2fffc5c72d05f | problems/tsp_constructive/test/ |  |
| eoh | https://github.com/FeiLiu36/EoH | 36d10d49e9b80777fa544ac4e457b43ac6c2f9d0 | none | requested pin 36d10d4 -> 36d10d49e9b80777fa544ac4e457b43ac6c2f9d0 |
| mcts-ahd | https://github.com/zz1358m/MCTS-AHD-master | ee9c4f424503c65a5fd2b899e6620ce86079fedb | problems/tsp_constructive/test/,problems/bpp_offline_aco/eval_black_box_test.py,problems/car_mountain/test.py,problems/cvrp_aco/eval_black_box_test.py,problems/mkp_aco/eval_black_box_test.py,problems/tsp_aco/eval_black_box_test.py |  |
