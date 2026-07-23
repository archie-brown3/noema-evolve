"""KernelBench L1/88 coordination smoke pilot (task 0104 / 0112).

This slice (task 0112) is config + protocol + aggregation logic only — no
GPU, no container runtime, no live LLM calls. See spec/KERNELBENCH-P88-PILOT.md
and README.md in this directory for the full picture and what remains
out of scope (the sandboxed worker, task 0104 proper).
"""
