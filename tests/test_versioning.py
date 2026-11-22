"""Tests for version bump rules (written first, TDD)."""

from __future__ import annotations

import pytest
from packaging.version import Version

from datoso_dev.versioning import Part, VersionError, bump, bump_choices

V = Version


@pytest.mark.parametrize(
    ('current', 'part', 'expected'),
    [
        ('1.1.1', Part.PATCH, '1.1.2'),
        ('1.1.1', Part.MINOR, '1.2.0'),
        ('1.1.1', Part.MAJOR, '2.0.0'),
        ('1.0', Part.PATCH, '1.0.1'),  # short versions are normalised to three parts
        ('0.9.9', Part.MINOR, '0.10.0'),
    ],
)
def test_bump_final(current: str, part: Part, expected: str):
    assert bump(V(current), part) == V(expected)


@pytest.mark.parametrize(
    ('current', 'part', 'pre', 'expected'),
    [
        ('1.1.1', Part.PATCH, 'rc', '1.1.2rc1'),
        ('1.1.1', Part.MINOR, 'b', '1.2.0b1'),
        ('1.1.2rc1', Part.PATCH, 'rc', '1.1.2rc2'),  # same base and label: next pre-release number
        ('1.1.2a3', Part.PATCH, 'rc', '1.1.2rc1'),  # moving up a label restarts at 1
        ('1.1.2rc1', Part.PATCH, None, '1.1.2'),  # releasing the final version of a pre-release
        ('1.2.0rc2', Part.MINOR, None, '1.2.0'),
        ('1.2.0rc2', Part.PATCH, None, '1.2.0'),  # any part finalises the pending base
        ('1.2.0rc2', Part.MAJOR, None, '2.0.0'),  # ...unless it is a bigger bump
    ],
)
def test_bump_prerelease(current: str, part: Part, pre: str | None, expected: str):
    assert bump(V(current), part, pre=pre) == V(expected)


def test_bump_rejects_going_down_a_label():
    with pytest.raises(VersionError, match='rc'):
        bump(V('1.1.2rc1'), Part.PATCH, pre='a')


def test_bump_rejects_unknown_label():
    with pytest.raises(VersionError, match='label'):
        bump(V('1.1.1'), Part.PATCH, pre='dev')


def test_bump_drops_dev_and_local():
    assert bump(V('1.1.1.dev3'), Part.PATCH) == V('1.1.1')
    assert bump(V('1.1.1+local'), Part.PATCH) == V('1.1.2')


# --------------------------------------------------------------------------- bump_choices


def test_choices_for_released_version():
    choices = bump_choices(V('1.1.1'), released=V('1.1.1'))
    assert [(c.part, c.version) for c in choices] == [
        (Part.PATCH, V('1.1.2')),
        (Part.MINOR, V('1.2.0')),
        (Part.MAJOR, V('2.0.0')),
    ]


def test_choices_offer_keep_when_already_bumped():
    """__version__ already bumped (1.1.2) but never released (last 1.1.1): keeping it is the first choice."""
    choices = bump_choices(V('1.1.2'), released=V('1.1.1'))
    assert (choices[0].part, choices[0].version) == (Part.KEEP, V('1.1.2'))
    # bumps are computed from the released version, so "patch" still means 1.1.2 and is not duplicated
    assert [(c.part, c.version) for c in choices[1:]] == [(Part.MINOR, V('1.2.0')), (Part.MAJOR, V('2.0.0'))]


def test_choices_never_released():
    choices = bump_choices(V('1.0.0'), released=None)
    assert (choices[0].part, choices[0].version) == (Part.KEEP, V('1.0.0'))


def test_choices_with_pre_label():
    choices = bump_choices(V('1.1.1'), released=V('1.1.1'), pre='rc')
    assert [str(c.version) for c in choices] == ['1.1.2rc1', '1.2.0rc1', '2.0.0rc1']


def test_choice_label():
    [patch, *_] = bump_choices(V('1.1.1'), released=V('1.1.1'))
    assert patch.label == 'patch (1.1.2)'
