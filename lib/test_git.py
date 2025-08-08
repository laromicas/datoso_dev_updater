#!/usr/bin/env python3
"""Test git."""

import pytest

from lib import git
from lib.git import switch_branch, commit_all, delete_branch, execute, get_new_files, get_staged_files, get_all_files, get_modified_files, get_branch, create_branch, check_if_branch_exists, add_files_to_stage, undo_update, check_if_update_needed

from lib.config import PATH


GIT_DIR = 'datoso_dev_updater/test_git'
GIT_TEST_DIR = PATH / GIT_DIR


@pytest.fixture(scope='function', autouse=True)
def setup_function():
    """Set up test."""
    if GIT_TEST_DIR.exists():
        teardown_function()
    GIT_TEST_DIR.mkdir(parents=True)
    execute(['git', 'init'], cwd=GIT_TEST_DIR)
    create_file(GIT_TEST_DIR / 'initial_file', 'test')
    execute(['git', 'add', '.'], cwd=GIT_TEST_DIR)
    execute(['git', 'commit', '-m', 'Initial commit'], cwd=GIT_TEST_DIR)
    # yield True
    # teardown_function()

def teardown_function():
    """Tear down test."""
    execute(['rm', '-rf', GIT_TEST_DIR.name], cwd=GIT_TEST_DIR.parent)


def create_file(file: str, content: str) -> None:
    """Create a file."""
    with open(file, 'w') as f:
        f.write(content)

def test_execute():
    """Test execute."""
    assert execute(['ls', '-la'], safe=False) != ''

    git.dry_run = True
    assert execute(['ls', '-la'], safe=False) == ''
    assert execute(['ls', '-la'], safe=True) != ''
    git.dry_run = False

def test_get_new_files():
    """Test get_new_files."""
    assert get_new_files(GIT_TEST_DIR) == []
    create_file(GIT_TEST_DIR / 'test_file', 'test')
    assert get_new_files(GIT_TEST_DIR) == ['test_file']
    (GIT_TEST_DIR / 'test_file').unlink()

def test_get_staged_files():
    """Test get_staged_files."""
    assert get_staged_files(GIT_TEST_DIR) == []
    create_file(GIT_TEST_DIR / 'test_file', 'test')
    add_files_to_stage(GIT_TEST_DIR)
    assert get_staged_files(GIT_TEST_DIR) == ['test_file']
    (GIT_TEST_DIR / 'test_file').unlink()
    execute(['git', 'add', '-u'], cwd=GIT_TEST_DIR)

def test_get_modified_files():
    """Test get_modified_files."""
    assert get_modified_files(GIT_TEST_DIR) == []
    create_file(GIT_TEST_DIR / 'test_file', 'test')
    create_file(GIT_TEST_DIR / 'initial_file', 'another_test')
    execute(['git', 'add', 'test_file'], cwd=GIT_TEST_DIR)
    assert get_modified_files(GIT_TEST_DIR) == ['initial_file', 'test_file']
    (GIT_TEST_DIR / 'test_file').unlink()
    execute(['git', 'add', '-u'], cwd=GIT_TEST_DIR)

def test_get_all_files():
    """Test get_all_files."""
    assert get_all_files(GIT_TEST_DIR) == []
    create_file(GIT_TEST_DIR / 'test_file', 'test')
    create_file(GIT_TEST_DIR / 'test_file_2', 'test')
    execute(['git', 'add', 'test_file'], cwd=GIT_TEST_DIR)
    assert get_all_files(GIT_TEST_DIR) == ['test_file', 'test_file_2']
    (GIT_TEST_DIR / 'test_file').unlink()
    (GIT_TEST_DIR / 'test_file_2').unlink()
    execute(['git', 'add', '-u'], cwd=GIT_TEST_DIR)

def test_get_branch():
    """Test get_branch."""
    switch_branch(GIT_TEST_DIR, 'master')
    assert get_branch(GIT_TEST_DIR) == 'master'

def test_branch():
    """Test create_branch."""
    assert not check_if_branch_exists(GIT_TEST_DIR, 'test')
    create_branch(GIT_TEST_DIR, 'test')
    assert check_if_branch_exists(GIT_TEST_DIR, 'test')
    assert get_branch(GIT_TEST_DIR) == 'test'

    switch_branch(GIT_TEST_DIR, 'master')
    assert get_branch(GIT_TEST_DIR) == 'master'
    switch_branch(GIT_TEST_DIR, 'test')

    assert get_branch(GIT_TEST_DIR) == 'test'
    switch_branch(GIT_TEST_DIR, 'master')
    delete_branch(GIT_TEST_DIR, 'test')

    assert not check_if_branch_exists(GIT_TEST_DIR, 'test')


def test_commit_all():
    """Test commit_all."""
    assert not check_if_branch_exists(GIT_TEST_DIR, 'test')

    create_branch(GIT_TEST_DIR, 'test')
    assert check_if_branch_exists(GIT_TEST_DIR, 'test')
    assert get_branch(GIT_TEST_DIR) == 'test'

    create_file(GIT_TEST_DIR / 'test_file', 'test')
    add_files_to_stage(GIT_TEST_DIR)
    commit_all(GIT_TEST_DIR, 'Test commit')
    assert get_all_files(GIT_TEST_DIR) == []

    switch_branch(GIT_TEST_DIR, 'master')
    delete_branch(GIT_TEST_DIR, 'test')
    assert not check_if_branch_exists(GIT_TEST_DIR, 'test')

def test_undo_update():
    """Test undo_update."""
    (GIT_TEST_DIR / 'src' / 'datoso_dev_updater' / 'test_git').mkdir(parents=True)
    create_file(GIT_TEST_DIR / 'pyproject.toml', 'this_is_a_test')
    create_file(GIT_TEST_DIR / 'src' / 'datoso_dev_updater' / 'test_git' / '__init__.py', 'this_is_a_test')
    assert get_all_files(GIT_TEST_DIR) == ['pyproject.toml', f'src/{GIT_DIR}/__init__.py']

    add_files_to_stage(GIT_TEST_DIR)
    commit_all(GIT_TEST_DIR, 'Test commit')
    create_file(GIT_TEST_DIR / 'pyproject.toml', 'new_test')
    create_file(GIT_TEST_DIR / 'src' / 'datoso_dev_updater' / 'test_git' / '__init__.py', 'new_test')
    assert get_all_files(GIT_TEST_DIR) == ['pyproject.toml', f'src/{GIT_DIR}/__init__.py']

    undo_update(GIT_DIR)
    print(get_all_files(GIT_TEST_DIR))
    assert get_all_files(GIT_TEST_DIR) == []

def test_check_if_update_needed():
    """Test check_if_update_needed."""
    (GIT_TEST_DIR / 'src' / 'datoso_dev_updater' / 'test_git').mkdir(parents=True)
    create_file(GIT_TEST_DIR / 'pyproject.toml', 'test')
    create_file(GIT_TEST_DIR / 'src' / 'datoso_dev_updater' / 'test_git' / '__init__.py', 'test')
    add_files_to_stage(GIT_TEST_DIR)
    commit_all(GIT_TEST_DIR, 'Test commit')

    assert not check_if_update_needed(GIT_TEST_DIR)
    create_file(GIT_TEST_DIR / 'src' / 'datoso_dev_updater' / 'test_git' / '__init__.py', 'new_test')
    assert check_if_update_needed(GIT_TEST_DIR)
