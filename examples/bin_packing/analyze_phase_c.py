"""Phase C native-selection analysis: fitness vs token spend + best-result chart.

Joins llm_calls.jsonl (token spend) with evolution_trace.jsonl (child scores),
merging PE injected proposals from run.log (they are NOT in evolution_trace).
"""
import json, re, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = "examples/bin_packing/runs/phase-c-p1"
BASELINE = 0.9562142852869019
OUT = f"{BASE}/analysis"
os.makedirs(OUT, exist_ok=True)

# (label, dir, arm, substrate)
CELLS = [
    ("null×islands",   "null-islands-s42",     "null",   "islands"),
    ("null×tree",      "null-tree-s42",        "null",   "tree"),
    ("hifo×islands",   "hifo-islands-s42",     "hifo",   "islands"),
    ("hifo×tree",      "hifo-tree-s42",        "hifo",   "tree"),
    ("bandit×islands", "bandit-islands-s42-r2","bandit", "islands"),
    ("bandit×tree",    "bandit-tree-s42-r2",   "bandit", "tree"),
    ("pe×islands",     "pe-islands-s42",       "pe",     "islands"),
    ("pe×tree",        "pe-tree-s42-r2",       "pe",     "tree"),
]
ARM_COLOR = {"null":"#888888", "hifo":"#1f77b4", "bandit":"#2ca02c", "pe":"#d62728"}
SUB_STYLE = {"islands":"-", "tree":"--"}

INJ_RE = re.compile(r"Evaluated program (it\d+-pe\d+) .*combined_score=([0-9.]+)")
INJ_ITER_RE = re.compile(r"it(\d+)-pe")

def iter_cum_tokens(d):
    """cumulative tokens (prompt+completion) at the end of each iteration."""
    calls = []
    with open(f"{BASE}/{d}/llm_calls.jsonl") as f:
        for line in f:
            r = json.loads(line)
            calls.append((r.get("call_id",""), r.get("iteration",-1),
                          r.get("prompt_tokens",0)+r.get("completion_tokens",0)))
    calls.sort(key=lambda c: c[0])  # call-000000 order
    cum = 0; per_iter = {}
    for _, it, t in calls:
        cum += t
        per_iter[it] = cum  # last write per iteration = cumulative at its end
    return per_iter

def child_scores(d, is_pe):
    """iteration -> list of child combined_scores (mutation children + PE injections)."""
    scores = {}
    with open(f"{BASE}/{d}/evolution_trace.jsonl") as f:
        for line in f:
            r = json.loads(line)
            it = r.get("iteration", -1)
            cm = r.get("child_metrics") or {}
            s = cm.get("combined_score")
            if s is not None:
                scores.setdefault(it, []).append(float(s))
    if is_pe:  # merge injected proposals from run.log (absent from evolution_trace)
        with open(f"{BASE}/{d}/run.log") as f:
            for line in f:
                m = INJ_RE.search(line)
                if m:
                    it = int(INJ_ITER_RE.search(m.group(1)).group(1))
                    scores.setdefault(it, []).append(float(m.group(2)))
    return scores

def trajectory(d, is_pe):
    cum = iter_cum_tokens(d)
    scores = child_scores(d, is_pe)
    xs, ys = [0.0], [BASELINE]
    best = BASELINE
    for it in sorted(scores):
        for s in scores[it]:
            if s > best: best = s
        xs.append(cum.get(it, xs[-1]))
        ys.append(best)
    return xs, ys

def final_best(d):
    with open(f"{BASE}/{d}/run.log") as f:
        last = None
        for line in f:
            if line.startswith("BEST:"): last = line
    m = re.search(r"combined_score': ([0-9.]+)", last)
    return float(m.group(1))

# ---- Chart 1: fitness vs token spend ----
fig, ax = plt.subplots(figsize=(10, 6))
stats = []
for label, d, arm, sub in CELLS:
    xs, ys = trajectory(d, arm == "pe")
    ax.plot([x/1e6 for x in xs], ys, SUB_STYLE[sub], color=ARM_COLOR[arm],
            lw=1.8, label=label, alpha=0.9, drawstyle="steps-post")
    stats.append((label, arm, sub, ys[-1], final_best(d)))
ax.axhline(BASELINE, ls=":", color="black", lw=1.2, label=f"best-fit baseline ({BASELINE:.4f})")
ax.set_xlabel("Cumulative tokens spent (millions)")
ax.set_ylabel("Best combined_score so far")
ax.set_ylim(0.955, 0.966)
ax.set_xlim(0, 1.02)
ax.set_title("Phase C — Fitness vs Token Spend (native selection, seed 42, V4 Flash)\n"
             "solid = islands, dashed = tree  ·  FunSearch reference ≈ 0.975–0.985 (above frame)")
ax.legend(loc="lower right", fontsize=8, ncol=2)
ax.grid(True, alpha=0.25)
fig.tight_layout()
fig.savefig(f"{OUT}/fitness_vs_tokens.png", dpi=140)
print("saved fitness_vs_tokens.png")

# ---- Chart 2: best result per arm ----
fig2, ax2 = plt.subplots(figsize=(9, 5.5))
arms = ["null", "hifo", "bandit", "pe"]
import numpy as np
x = np.arange(len(arms)); w = 0.38
isl = [next(fb for l,a,s,ye,fb in stats if a==arm and s=="islands") for arm in arms]
tre = [next(fb for l,a,s,ye,fb in stats if a==arm and s=="tree") for arm in arms]
b1 = ax2.bar(x-w/2, isl, w, label="islands", color="#4c72b0")
b2 = ax2.bar(x+w/2, tre, w, label="tree", color="#dd8452")
ax2.axhline(BASELINE, ls=":", color="black", lw=1.2, label=f"baseline ({BASELINE:.4f})")
for bars in (b1, b2):
    for r in bars:
        ax2.annotate(f"{r.get_height():.4f}", (r.get_x()+r.get_width()/2, r.get_height()),
                     ha="center", va="bottom", fontsize=8)
ax2.set_xticks(x); ax2.set_xticklabels(arms)
ax2.set_ylim(0.955, 0.966)
ax2.set_ylabel("Best combined_score (final)")
ax2.set_title("Phase C — Best Result per Arm (native selection, seed 42)")
ax2.legend(loc="upper right", fontsize=9)
ax2.grid(True, axis="y", alpha=0.25)
fig2.tight_layout()
fig2.savefig(f"{OUT}/best_per_arm.png", dpi=140)
print("saved best_per_arm.png")

# ---- Stats table ----
print(f"\n{'cell':16} {'traj_end':>9} {'BEST:':>9} {'Δbaseline':>10}")
for label, arm, sub, ye, fb in stats:
    print(f"{label:16} {ye:>9.4f} {fb:>9.4f} {fb-BASELINE:>+10.4f}")
