"""Git helpers."""
import subprocess
from contextlib import suppress
from pathlib import Path

from rich.console import Console

from lib.config import PATH

console = Console()


def execute(args: list[str], *,  # noqa: PLR0913
            cwd: str | None=None, text: bool=True,
            stderr: int=subprocess.STDOUT,
            safe: bool=True,
            dry_run: bool=False) -> str:
    """Execute a command."""
    if not dry_run or safe:
        return subprocess.check_output(args, cwd=cwd, text=text, stderr=stderr) # noqa: S603
    console.print(f'[yellow]Dry run:[/yellow] [cyan]{" ".join(args)}[/cyan]')
    if cwd:
        console.print(f'[yellow]CWD:[/yellow] [cyan]{cwd}[/cyan]')
    return ''

def get_repo_root() -> str:
    """Get the root directory of the git repository."""
    repo_root_args = ['git', 'rev-parse', '--show-toplevel']
    return execute(repo_root_args).strip()

def get_current_branch() -> str:
    """Get the current git branch."""
    branch_args = ['git', 'rev-parse', '--abbrev-ref', 'HEAD']
    return execute(branch_args).strip()

def remove_duplicates(lst: list) -> list:
    """Remove duplicates from a list."""
    return list(dict.fromkeys(lst))

""" Git helpers file functions """
def get_new_files(plugin: str) -> list:
    """Get new files."""
    newfiles_args = ['git', 'ls-files', '--others', '--exclude-standard']
    return [x for x in execute(newfiles_args, cwd=(PATH / plugin)).split('\n') if x]

def get_staged_files(plugin: str) -> list:
    """Get staged files."""
    staged_args = ['git', 'diff', '--cached', '--name-only']
    return [x for x in execute(staged_args, cwd=(PATH / plugin)).split('\n') if x]

def get_modified_files(plugin: str) -> list:
    """Get modified files."""
    # args_updatedfiles = ["git", "ls-files", "--modified"] # noqa: ERA001
    modified_args = ['git', 'diff', 'HEAD', '--name-only']
    return [x for x in execute(modified_args, cwd=(PATH / plugin)).split('\n') if x]

def get_all_files(plugin: str) -> list:
    """Get modified files."""
    staged = get_staged_files(plugin)
    modified = get_modified_files(plugin)
    newfiles = get_new_files(plugin)
    return remove_duplicates(staged + modified + newfiles)

""" Git helpers branch functions """
def get_branch(plugin: str) -> str:
    """Get the current branch."""
    return execute(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=(PATH / plugin)).strip()

def create_branch(plugin: str, branch: str, *, dry_run: bool) -> None:
    """Create a branch."""
    execute(['git', 'checkout', 'master'], cwd=(PATH / plugin), safe=False, dry_run=dry_run)
    delete_branch(plugin, branch, dry_run=dry_run)  # Ensure branch is deleted if it exists
    execute(['git', 'checkout', '-b', branch], cwd=(PATH / plugin), safe=False, dry_run=dry_run)

def switch_branch(plugin: str, branch: str, *, dry_run: bool = False) -> None:
    """Checkout a branch."""
    execute(['git', 'checkout', branch], cwd=(PATH / plugin), dry_run=dry_run)

def delete_branch(plugin: str, branch: str, *, dry_run: bool = False) -> None:
    """Delete a branch."""
    execute(['git', 'branch', '-D', branch], cwd=(PATH / plugin), dry_run=dry_run)

def check_if_branch_exists(plugin: str, branch: str) -> bool:
    """Check if a branch exists."""
    try:
        execute(['git', 'show-ref', '--verify', f'refs/heads/{branch}'], cwd=(PATH / plugin))
    except subprocess.CalledProcessError:
        return False
    else:
        return True

def add_files_to_stage(plugin: str, *, dry_run: bool = False) -> None:
    """Stage files."""
    args = ['git', 'add']
    files = get_all_files(plugin, dry_run=dry_run)
    for file in files:
        args.append(file) # noqa: PERF402
    print(f'Adding files to stage: {files}')
    execute(args, cwd=(PATH / plugin), dry_run=dry_run)

""" Git helpers commit functions """
def commit_all(plugin: str, message: str, *, dry_run: bool = False) -> None:
    """Commit all files."""
    args = ['git', 'commit', '-m', message]
    execute(args, cwd=(PATH / plugin), dry_run=dry_run)


""" Git helpers python project functions """
def undo_update(plugin: str, *, dry_run: bool = False) -> None:
    """Undo the update of a plugin."""
    staged = get_staged_files(plugin)
    modified = get_modified_files(plugin)
    version_files = ['pyproject.toml', str(Path('src') / plugin / '__init__.py')]
    console.print(f'[magenta]Undoing update for [cyan]{plugin}[/cyan][/magenta]')
    console.print(f'Staged files: [yellow]{staged}[/yellow]')
    console.print(f'Modified files: [yellow]{modified}[/yellow]')
    console.print(f'Version files: [yellow]{version_files}[/yellow]')
    for version_file in version_files:
        if version_file in staged:
            args = ['git', 'restore', '--cached', version_file]
            with suppress(subprocess.CalledProcessError):
                execute(args, cwd=(PATH / plugin), safe=False, dry_run=dry_run)
        if version_file in modified:
            args = ['git', 'restore', version_file]
            with suppress(subprocess.CalledProcessError):
                execute(args, cwd=(PATH / plugin), safe=False, dry_run=dry_run)
    execute(['git', 'checkout', 'master'], cwd=(PATH / plugin), safe=False, dry_run=dry_run)

def check_if_update_needed(plugin: str) -> None:
    """Check if an update is needed."""
    args = ['git', 'diff', 'HEAD', '--name-only']
    output = execute(args, cwd=(PATH / plugin))
    updated_files = []
    init = False
    for filename in output.split('\n'):
        if filename.startswith('src/') and filename.endswith('__init__.py'):
            init = True
            continue
        updated_files.append(filename)

    return updated_files and init
