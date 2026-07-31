"""Render the AgentSession phase machine as a diagram.

Kept beside the code it documents so the picture can be regenerated when the
phase table changes. The transitions below are transcribed from
`noema/agenthost/session.py` (`_REQUIRED_CALL` plus each tool's
`self._phase = ...` assignment).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

INK = "#1b2733"
MUTED = "#59677a"
EDGE = "#78859a"
ACCEPT = "#1c7a58"
REJECT = "#b03636"
HOST = "#6a4ba6"

BOX_W, BOX_H = 2.10, 1.00
DX = 4.35

# phase -> (x, label, the call the agent owes while in this phase)
PHASES = [
    ("idle", "begin_run"),
    ("open", "next_target"),
    ("targeted", "select_parent"),
    ("parented", "get_brief"),
    ("briefed", "submit_child"),
    ("complete", None),
]
X = {name: i * DX for i, (name, _) in enumerate(PHASES)}
OWES = dict(PHASES)

# (from, to, label) — one agent tool call per hop
FORWARD = [
    ("idle", "open", "begin_run\nseed + evaluate\ninitial program"),
    ("open", "targeted", "next_target\nsubstrate\n.target_scope()"),
    ("targeted", "parented", "select_parent\nsampling_request\n→ substrate.select"),
    ("parented", "briefed", "get_brief\ncoordination.advise\n→ render_brief"),
]


def _phase_box(ax, name: str) -> None:
    x = X[name]
    terminal = OWES[name] is None
    ax.add_patch(
        FancyBboxPatch(
            (x - BOX_W / 2, -BOX_H / 2),
            BOX_W,
            BOX_H,
            boxstyle="round,pad=0.05,rounding_size=0.18",
            linewidth=2.1 if terminal else 1.5,
            edgecolor=ACCEPT if terminal else EDGE,
            facecolor="#e7f2ec" if terminal else "#edf2f8",
            zorder=3,
        )
    )
    ax.text(x, 0.16, name, ha="center", va="center", fontsize=13,
            fontweight="bold", color=INK, zorder=4)
    ax.text(x, -0.24, "run complete" if terminal else f"owes: {OWES[name]}",
            ha="center", va="center", fontsize=8.6, style="italic",
            color=ACCEPT if terminal else MUTED, zorder=4)


def _arrow(ax, start, end, *, color=EDGE, rad=0.0, lw=1.7, ls="-", zorder=2):
    ax.add_patch(
        FancyArrowPatch(
            start, end,
            connectionstyle=f"arc3,rad={rad}",
            arrowstyle="-|>", mutation_scale=16,
            linewidth=lw, linestyle=ls, color=color,
            shrinkA=1, shrinkB=1, zorder=zorder,
        )
    )


def _elbow(ax, x_from, x_to, y_top, *, color, lw=2.0):
    """Orthogonal return edge: up, across above the labels, then down.

    An arc would cut straight through the transition labels at the span ends,
    so the return path is routed as right angles over the top instead.
    """
    ax.plot([x_from, x_from], [BOX_H / 2, y_top], color=color, lw=lw,
            solid_capstyle="round", zorder=2)
    ax.plot([x_from, x_to], [y_top, y_top], color=color, lw=lw,
            solid_capstyle="round", zorder=2)
    _arrow(ax, (x_to, y_top), (x_to, BOX_H / 2), color=color, lw=lw)


def _note(ax, x, y, w, h, *, edge, face, heading, body, hcolor=None):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.07,rounding_size=0.14",
            linewidth=1.4, edgecolor=edge, facecolor=face, zorder=3,
        )
    )
    ax.text(x + 0.24, y + h - 0.30, heading, fontsize=10, fontweight="bold",
            color=hcolor or edge, va="center", zorder=4)
    ax.text(x + 0.24, y + h - 0.78, body, fontsize=8.5, color=MUTED,
            va="top", zorder=4, linespacing=1.5)


def draw(output: Path, title: str) -> Path:
    fig, ax = plt.subplots(figsize=(19.0, 7.2))
    fig.patch.set_facecolor("white")

    for name, _ in PHASES:
        _phase_box(ax, name)

    # forward path
    for src, dst, text in FORWARD:
        _arrow(ax, (X[src] + BOX_W / 2, 0.0), (X[dst] - BOX_W / 2, 0.0))
        ax.text((X[src] + X[dst]) / 2, 1.28, text, ha="center", va="center",
                fontsize=8.6, color=INK, zorder=5, linespacing=1.55)

    # briefed -> complete (final accepted child)
    _arrow(ax, (X["briefed"] + BOX_W / 2, 0.0),
           (X["complete"] - BOX_W / 2, 0.0), color=ACCEPT, lw=2.0)
    ax.text((X["briefed"] + X["complete"]) / 2, 1.20,
            "accepted and\nchildren_accepted\n== stop_children",
            ha="center", va="center", fontsize=8.6, color=ACCEPT,
            fontweight="bold", zorder=5, linespacing=1.55)

    # accepted, run continues: briefed -> open, routed above the labels
    bx = X["briefed"]
    _elbow(ax, bx, X["open"], 2.95, color=ACCEPT)
    ax.text((bx + X["open"]) / 2, 3.42,
            "accepted  ·  evaluate → store.add → report_result(ACCEPTED)\n"
            "iteration += 1  ·  generation_tick auto-fires on substrate cadence",
            ha="center", va="center", fontsize=9.2, color=ACCEPT,
            fontweight="bold", zorder=5, linespacing=1.6)

    # rejection: phase unchanged, so the edge loops back into briefed itself.
    # Drawn above the boxes (zorder) or the hook hides behind the phase box.
    _arrow(ax, (bx - 0.25, -BOX_H / 2), (bx - BOX_W / 2 - 0.04, 0.06),
           color=REJECT, rad=-0.95, lw=1.8, zorder=6)
    ax.text(12.55, -1.18,
            "rejected — evaluation failed\n"
            "report_result(EVAL_ERROR)\n"
            "+ retry_advice → retry_brief\n"
            "same parent, same target",
            ha="center", va="center", fontsize=8.4, color=REJECT, zorder=5,
            linespacing=1.5)

    # terminal behaviour: next_target reports completion instead of raising
    cx = X["complete"]
    _arrow(ax, (cx, -BOX_H / 2), (cx, -1.30), color=ACCEPT, ls=":", lw=1.5)
    ax.text(cx, -1.70, 'next_target →\n{"status": "complete"}',
            ha="center", va="center", fontsize=8.4, color=ACCEPT, zorder=5,
            linespacing=1.5)

    # nested headless mutation (planned), hanging below the briefed phase
    _note(ax, bx - 2.55, -3.10, 5.10, 1.45,
          edge=HOST, face="#f4f0fb",
          heading="run_mutation  (planned)",
          body="headless coding CLI session,\n"
               "own worktree → child code → admission",
          hcolor=HOST)
    _arrow(ax, (bx - 0.55, -BOX_H / 2), (bx - 0.55, -1.65), color=HOST,
           lw=1.6, ls="--")
    _arrow(ax, (bx + 0.55, -1.65), (bx + 0.55, -BOX_H / 2), color=HOST,
           lw=1.6, ls="--")

    # bottom notes
    _note(ax, -2.30, -5.15, 11.60, 1.40,
          edge=REJECT, face="#fdf2f2",
          heading="Any out-of-order call → PhaseError",
          body="The exception carries required_call — the call named in the phase box above.\n"
               "Nothing happens: no evaluation, no store write, no coordination hook.")
    _note(ax, 10.30, -5.15, 12.60, 1.40,
          edge=EDGE, face="#f6f8fb",
          heading="The host owns every coordination hook",
          body="sampling_request · advise · report_result · retry_advice · on_generation_end\n"
               "(+ Intervention proposals, host-evaluated). The agent never implements them.",
          hcolor=INK)

    legend = [
        mpatches.Patch(facecolor="#edf2f8", edgecolor=EDGE,
                       label="phase — agent owes the named call"),
        mpatches.Patch(facecolor="#e7f2ec", edgecolor=ACCEPT, label="terminal phase"),
        mpatches.Patch(facecolor="#f4f0fb", edgecolor=HOST,
                       label="nested headless mutation session"),
        mpatches.Patch(facecolor="#fdf2f2", edgecolor=REJECT,
                       label="rejection / refusal (no store write)"),
    ]
    ax.legend(handles=legend, loc="upper right", bbox_to_anchor=(0.999, 0.998),
              fontsize=9, frameon=True, framealpha=0.97, borderpad=0.75,
              labelspacing=0.6)

    ax.set_title(title, fontsize=16, fontweight="bold", color=INK, pad=22)
    fig.text(0.5, 0.022,
             "AgentSession phase machine · noema/agenthost/session.py · "
             "NoemaController and the canonical loop are unaffected",
             ha="center", fontsize=9, color=MUTED)

    ax.set_xlim(-2.9, 23.9)
    ax.set_ylim(-5.6, 4.5)
    ax.set_aspect("equal")
    ax.axis("off")

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--title", default="NoemaAgent Host — AgentSession Loop State Machine"
    )
    args = parser.parse_args()
    print(f"wrote {draw(args.output, args.title)}")


if __name__ == "__main__":
    main()
