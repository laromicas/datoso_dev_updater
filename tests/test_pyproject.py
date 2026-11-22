"""Tests for format-preserving edits of versions and dependency floors (written first, TDD)."""

from __future__ import annotations

import pytest
from packaging.version import Version

from datoso_dev.pyproject import FloorEdit, set_floor, set_init_version, set_project_version

V = Version

PYPROJECT = """\
[project]
name = "datoso_seed_enhanced"
# keep this comment
dependencies = [
    "datoso>=1.1.0",           # core
    "datoso-plugin-internetarchive>=1.1.1",
    "requests",
]
dynamic = ["version"]

[project.optional-dependencies]
redump = ["datoso-seed-redump>=1.0.1"]
all = [
    "datoso-seed-redump>=1.0.1",
    "datoso_seed_tdc >= 1.0 , <2",
]
"""


def test_set_floor_replaces_only_that_requirement():
    text, edit = set_floor(PYPROJECT, 'datoso', V('1.1.2'))
    assert edit == FloorEdit('datoso', 'datoso>=1.1.0', 'datoso>=1.1.2')
    assert '"datoso>=1.1.2",           # core' in text  # comment and alignment preserved
    assert text.replace('datoso>=1.1.2', 'datoso>=1.1.0') == PYPROJECT  # nothing else changed


def test_set_floor_matches_normalised_names():
    _, edit = set_floor(PYPROJECT, 'datoso_plugin_internetarchive', V('1.2.0'))
    assert edit == FloorEdit(
        'datoso-plugin-internetarchive',
        'datoso-plugin-internetarchive>=1.1.1',
        'datoso-plugin-internetarchive>=1.2.0',
    )


def test_set_floor_adds_floor_when_unpinned():
    text, edit = set_floor(PYPROJECT, 'requests', V('2.31.0'))
    assert edit.new == 'requests>=2.31.0'
    assert '"requests>=2.31.0",' in text


def test_set_floor_keeps_other_specifiers():
    text, edit = set_floor(PYPROJECT, 'datoso-seed-tdc', V('1.1.0'), extra='all')
    assert edit.new == 'datoso_seed_tdc>=1.1.0,<2'  # spelling of the name kept, upper bound kept
    assert '"datoso_seed_tdc>=1.1.0,<2",' in text


def test_set_floor_in_one_extra_only():
    text, edit = set_floor(PYPROJECT, 'datoso-seed-redump', V('1.0.2'), extra='redump')
    assert edit.new == 'datoso-seed-redump>=1.0.2'
    assert 'redump = ["datoso-seed-redump>=1.0.2"]' in text
    assert '    "datoso-seed-redump>=1.0.1",' in text  # the `all` extra is untouched


def test_set_floor_unchanged_or_absent_returns_none():
    assert set_floor(PYPROJECT, 'datoso', V('1.1.0')) == (PYPROJECT, None)
    assert set_floor(PYPROJECT, 'not-a-dep', V('1.0')) == (PYPROJECT, None)
    assert set_floor(PYPROJECT, 'datoso', V('1.2'), extra='nope') == (PYPROJECT, None)


def test_set_floor_keeps_extras_and_markers():
    text = '[project]\ndependencies = ["datoso[ia]>=1.0; python_version >= \'3.11\'"]\n'
    new, edit = set_floor(text, 'datoso', V('1.1.2'))
    assert edit.new == 'datoso[ia]>=1.1.2; python_version >= "3.11"'
    assert 'datoso[ia]>=1.1.2' in new


# --------------------------------------------------------------------------- versions


@pytest.mark.parametrize('quote', ["'", '"'])
def test_set_init_version_keeps_quote_style(quote: str):
    text = f'"""Seed."""\n__all__ = [\'__version__\']\n__version__ = {quote}1.0.1{quote}  # bumped by datoso-dev\n'
    new = set_init_version(text, V('1.0.2'))
    assert new == text.replace('1.0.1', '1.0.2')


def test_set_init_version_requires_the_line():
    with pytest.raises(ValueError, match='__version__'):
        set_init_version('"""No version here."""\n', V('1.0.0'))


def test_set_project_version():
    text = '[project]\nname = "x"\nversion = "1.0.0"  # static\n'
    assert set_project_version(text, V('1.0.1')) == '[project]\nname = "x"\nversion = "1.0.1"  # static\n'
