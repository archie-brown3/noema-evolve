"""Deterministic response tape — layer 5 of the 0132 comparability checklist.

Both systems are fed the SAME ordered list of canned LLM responses, indexed by
request number, with zero network. Any divergence in the resulting trajectory is
then attributable to selection/admission/eviction logic rather than to the model.

Why this file lives here and not in either system's tree:

- `.openevolve-upstream` is pinned at 80945ed and must stay byte-identical to the
  pin (layer 1). Injecting a replay module into it — as the retired fork did with
  `openevolve/llm/replay.py` — would void that. Upstream already exposes a
  sanctioned seam: `LLMModelConfig.init_client` (config.py:60), consumed at
  `llm/ensemble.py:25`, so a client can be supplied from outside.
- noema's `BudgetedLLM` takes an injectable `client` exposing
  `chat.completions.create(**params)`.

So neither tree is modified.

**Full-rewrite mode first.** With `diff_based_evolution: false` the child is
parsed straight from the response and is independent of the parent, so a
trajectory divergence is unambiguously an admission-or-selection difference. In
diff mode the same canned diff against a different parent yields a different
child, so divergence amplifies and cannot be attributed. Diff mode is the second
test, not the first.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


def seed_uuid4(seed: int) -> None:
    """Make `uuid.uuid4()` deterministic for this process.

    Root cause of the layer-5 nondeterminism. Program IDs are `str(uuid.uuid4())`
    (upstream controller.py:288, process_parallel.py:291), and `uuid4` draws from
    `os.urandom`, which `random.seed()` does not control. The islands and archive
    are `Set[str]` of those IDs (database.py:142,152), and parent selection does
    `list(self.islands[i])` then `random.choice(...)` by index (database.py:426).

    So a set of *different* UUIDs iterates in a different order every run, and the
    same random stream picks a different parent — at a fixed `random_seed` and
    even at a fixed `PYTHONHASHSEED`. Seeding uuid4 removes the last uncontrolled
    input. Applied identically to both arms, so it cannot favour either.

    Harness-level: patches `uuid` in this process only, leaving both trees intact.
    """
    import random as _random
    import uuid

    rng = _random.Random(seed)
    uuid.uuid4 = lambda: uuid.UUID(int=rng.getrandbits(128), version=4)


def code_id(code: str) -> str:
    """Content address for a program.

    The two systems mint different UUIDs for the same program, so trajectories
    can only be compared on content. Whitespace at the edges is stripped so a
    trailing-newline difference does not read as a different program.
    """
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()[:12]


@dataclass
class Tape:
    """An ordered list of canned responses, consumed one per LLM request."""

    responses: List[str]
    _counter: Iterator[int] = field(default_factory=lambda: itertools.count(), repr=False)

    @classmethod
    def from_json(cls, path: str | Path) -> "Tape":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(responses=list(payload["responses"]))

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps({"responses": self.responses}, indent=2) + "\n", encoding="utf-8"
        )

    def next(self) -> str:
        """Return the response for the next request, in order.

        Exhaustion raises rather than wrapping: silently reusing responses would
        make two runs of different lengths look artificially similar.
        """
        index = next(self._counter)
        if index >= len(self.responses):
            raise IndexError(
                f"tape exhausted: request {index} but only {len(self.responses)} "
                "responses. Lengthen the tape or shorten the run."
            )
        return self.responses[index]

    def __len__(self) -> int:
        return len(self.responses)


# --- stock seam: an LLMInterface supplied via LLMModelConfig.init_client -----


def make_stock_client(tape: Tape):
    """Build an `init_client` callable that serves `tape` to stock OpenEvolve."""
    from openevolve.llm.base import LLMInterface

    class TapeLLM(LLMInterface):
        def __init__(self, config: Any = None) -> None:
            self.model = getattr(config, "name", None) or "0132-tape"

        async def generate(self, prompt: str, **kwargs) -> str:
            return tape.next()

        async def generate_with_context(
            self, system_message: str, messages: List[Dict[str, str]], **kwargs
        ) -> str:
            return tape.next()

    return TapeLLM


# --- noema seam: an OpenAI-shaped client injected into BudgetedLLM ----------


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content
        self.reasoning_content = None


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Usage:
    # Fixed non-zero counts: noema meters every call, and a zero-token response
    # would let a budget guard end the run early and desynchronise the arms.
    prompt_tokens = 100
    completion_tokens = 100
    total_tokens = 200


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage()
        self.model = "0132-tape"


class TapeChatClient:
    """Minimal `chat.completions.create` stand-in backed by the same Tape."""

    def __init__(self, tape: Tape) -> None:
        self._tape = tape
        self.chat = self  # so `client.chat.completions.create` resolves
        self.completions = self

    async def create(self, **params: Any) -> _Response:
        return _Response(self._tape.next())


# --- trajectory record ------------------------------------------------------


@dataclass
class Step:
    """One request's observable effect on population state.

    Compared field-for-field across the two systems. Program identity is content
    based (`code_id`) because the UUIDs differ by construction.
    """

    index: int
    parent: Optional[str]
    child: Optional[str]
    admitted: bool
    population: List[str]
    island_sizes: List[int]
    best: Optional[float]

    def key(self) -> tuple:
        """The comparable projection — sorted population so insertion order,
        which is not a behavioural claim, does not register as divergence."""
        return (
            self.index,
            self.parent,
            self.child,
            self.admitted,
            tuple(sorted(self.population)),
            tuple(self.island_sizes),
            None if self.best is None else round(self.best, 12),
        )


def diff_trajectories(left: List[Step], right: List[Step]) -> List[Dict[str, Any]]:
    """First-divergence-wins comparison of two trajectories."""
    divergences: List[Dict[str, Any]] = []
    for i in range(max(len(left), len(right))):
        lhs = left[i] if i < len(left) else None
        rhs = right[i] if i < len(right) else None
        if lhs is None or rhs is None or lhs.key() != rhs.key():
            divergences.append(
                {
                    "index": i,
                    "left": None if lhs is None else lhs.__dict__,
                    "right": None if rhs is None else rhs.__dict__,
                }
            )
    return divergences
