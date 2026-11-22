"""Interactive prompts behind a small interface, so menus can be tested with scripted answers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import questionary


@dataclass(frozen=True)
class Option:
    """One entry of a select or checkbox prompt."""

    label: str
    value: Any
    disabled: str | None = None  # reason shown next to an entry that can't be picked


class Prompter(Protocol):
    """What menus may ask the user. Every method returns ``None`` when the user cancels (Ctrl-C / Esc)."""

    def select(self, message: str, options: Sequence[Option]) -> Any | None:  # noqa: ANN401
        """Pick exactly one option; return its value."""

    def checkbox(self, message: str, options: Sequence[Option]) -> list[Any] | None:
        """Pick any number of options; return their values."""

    def confirm(self, message: str, *, default: bool = False) -> bool | None:
        """Yes/no question."""


class QuestionaryPrompter:
    """Terminal prompts (arrow keys, space to toggle, ``a`` to toggle all, ``i`` to invert)."""

    def select(self, message: str, options: Sequence[Option]) -> Any | None:  # noqa: ANN401
        """Pick exactly one option."""
        return questionary.select(message, choices=_choices(options)).ask()

    def checkbox(self, message: str, options: Sequence[Option]) -> list[Any] | None:
        """Pick any number of options."""
        return questionary.checkbox(
            message,
            choices=_choices(options),
            instruction='(space: toggle, a: all, i: invert, enter: done)',
        ).ask()

    def confirm(self, message: str, *, default: bool = False) -> bool | None:
        """Yes/no question."""
        return questionary.confirm(message, default=default).ask()


def _choices(options: Sequence[Option]) -> list[questionary.Choice]:
    return [questionary.Choice(o.label, value=o.value, disabled=o.disabled) for o in options]
