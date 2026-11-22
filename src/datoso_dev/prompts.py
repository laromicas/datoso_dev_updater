"""Interactive prompts behind a small interface, so menus can be tested with scripted answers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import questionary
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent, merge_key_bindings


@dataclass(frozen=True)
class Option:
    """One entry of a select or checkbox prompt."""

    label: str
    value: Any
    disabled: str | None = None  # reason shown next to an entry that can't be picked
    checked: bool = False  # pre-selected in a checkbox


class Prompter(Protocol):
    """What menus may ask the user. Every method returns ``None`` when the user cancels (Esc / Ctrl-C)."""

    def select(self, message: str, options: Sequence[Option]) -> Any | None:  # noqa: ANN401
        """Pick exactly one option; return its value."""

    def checkbox(self, message: str, options: Sequence[Option]) -> list[Any] | None:
        """Pick any number of options; return their values."""

    def confirm(self, message: str, *, default: bool = False) -> bool | None:
        """Yes/no question."""


class QuestionaryPrompter:
    """Terminal prompts: arrows to move, space to toggle, ``a`` all, ``i`` invert, Esc (or Ctrl-C) to go back."""

    SELECT_HINT = '(arrows: move, enter: choose, esc: back)'
    CHECKBOX_HINT = '(space: toggle, a: all, i: invert, enter: done, esc: back)'

    def __init__(self, **app_kwargs: Any) -> None:  # noqa: ANN401
        """``app_kwargs`` (e.g. ``input``/``output``) go to the prompt_toolkit application; used by tests."""
        self.app_kwargs = app_kwargs

    def select(self, message: str, options: Sequence[Option]) -> Any | None:  # noqa: ANN401
        """Pick exactly one option."""
        question = questionary.select(
            message, choices=_choices(options), instruction=self.SELECT_HINT, **self.app_kwargs
        )
        return _escapable(question).ask()

    def checkbox(self, message: str, options: Sequence[Option]) -> list[Any] | None:
        """Pick any number of options."""
        question = questionary.checkbox(
            message, choices=_choices(options), instruction=self.CHECKBOX_HINT, **self.app_kwargs
        )
        return _escapable(question).ask()

    def confirm(self, message: str, *, default: bool = False) -> bool | None:
        """Yes/no question."""
        return _escapable(questionary.confirm(message, default=default, **self.app_kwargs)).ask()


def _escapable(question: questionary.Question) -> questionary.Question:
    """Make Esc end the prompt with ``None``, like Ctrl-C (questionary only binds Ctrl-C)."""
    bindings = KeyBindings()

    @bindings.add('escape', eager=True)
    def _cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    app = question.application
    app.key_bindings = merge_key_bindings([app.key_bindings, bindings]) if app.key_bindings else bindings
    return question


def _choices(options: Sequence[Option]) -> list[questionary.Choice]:
    return [questionary.Choice(o.label, value=o.value, disabled=o.disabled, checked=o.checked) for o in options]
