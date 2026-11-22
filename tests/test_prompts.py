"""Tests for the real questionary prompter, driven by keystrokes through a prompt_toolkit pipe (no terminal)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from datoso_dev.prompts import Option, QuestionaryPrompter

ESC, ENTER, DOWN, SPACE = '\x1b', '\r', '\x1b[B', ' '
OPTIONS = [Option('first', 1), Option('second', 2), Option('blocked', 3, disabled='up to date')]


@pytest.fixture
def keys() -> Iterator:
    with create_pipe_input() as pipe:
        yield pipe


def prompter(pipe) -> QuestionaryPrompter:
    return QuestionaryPrompter(input=pipe, output=DummyOutput())


def test_select_enter_picks_highlighted(keys):
    keys.send_text(DOWN + ENTER)
    assert prompter(keys).select('Pick', OPTIONS) == 2


def test_checkbox_space_toggles(keys):
    keys.send_text(SPACE + DOWN + SPACE + ENTER)
    assert prompter(keys).checkbox('Pick', OPTIONS) == [1, 2]


def test_confirm_yes(keys):
    keys.send_text('y')
    assert prompter(keys).confirm('Sure?') is True


@pytest.mark.parametrize('method', ['select', 'checkbox'])
def test_escape_cancels_choice_prompts(keys, method: str):
    keys.send_text(SPACE + ESC)  # even with something toggled
    assert getattr(prompter(keys), method)('Pick', OPTIONS) is None


def test_escape_cancels_confirm(keys):
    keys.send_text(ESC)
    assert prompter(keys).confirm('Sure?', default=True) is None


def test_checkbox_hint_mentions_escape():
    assert 'esc' in QuestionaryPrompter.CHECKBOX_HINT


def test_checkbox_prechecked_options(keys):
    options = [Option('first', 1, checked=True), Option('second', 2)]
    keys.send_text(ENTER)
    assert prompter(keys).checkbox('Pick', options) == [1]
