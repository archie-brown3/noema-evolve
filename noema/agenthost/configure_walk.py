"""Pure section-walk state for the agency configure CLI (task 0189).

No terminal I/O — cursor, arming, and section index only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SECTION_ORDER: tuple[str, ...] = (
    "paths",
    "agent",
    "coordination",
    "advanced",
    "write_and_run",
)


def _empty_sections() -> dict[str, list[dict[str, Any]]]:
    return {name: [] for name in SECTION_ORDER}


@dataclass
class ConfigureWalk:
    """In-memory configure session navigation state."""

    section_index: int = 0
    field_index: int = 0
    armed: bool = False
    dirty: bool = False
    sections: dict[str, list[dict[str, Any]]] = field(default_factory=_empty_sections)
    _draft: Any = None

    @property
    def section_id(self) -> str:
        return SECTION_ORDER[self.section_index]

    def fields(self) -> list[dict[str, Any]]:
        return self.sections.get(self.section_id, [])

    def current_field(self) -> dict[str, Any]:
        fields = self.fields()
        if not fields:
            raise IndexError(f"no fields in section {self.section_id!r}")
        return fields[self.field_index]

    def move_section(self, delta: int) -> None:
        """Idle ←/→: change section. No-op while a field is armed."""

        if self.armed:
            return
        self.section_index = max(0, min(len(SECTION_ORDER) - 1, self.section_index + delta))
        self.field_index = 0

    def move_field(self, delta: int) -> None:
        """Idle ↑/↓: move field cursor."""

        if self.armed:
            return
        fields = self.fields()
        if not fields:
            return
        self.field_index = max(0, min(len(fields) - 1, self.field_index + delta))

    def arm(self) -> None:
        if not self.fields():
            return
        self.armed = True
        self._draft = self.current_field().get("value")

    def disarm(self, *, discard: bool) -> None:
        if not self.armed:
            return
        if discard:
            self.current_field()["value"] = self._draft
        else:
            self.dirty = True
        self.armed = False
        self._draft = None

    def cycle_value(self, delta: int) -> None:
        """Armed ←/→ on a closed field: cycle choices in place."""

        if not self.armed:
            return
        fld = self.current_field()
        if fld.get("kind") != "closed":
            return
        choices = list(fld.get("choices") or [])
        if not choices:
            return
        try:
            idx = choices.index(fld.get("value"))
        except ValueError:
            idx = 0
        fld["value"] = choices[(idx + delta) % len(choices)]
