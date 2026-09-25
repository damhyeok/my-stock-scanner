"""Export tracked runtime files only; never copy source Git history or secrets."""
import argparse
from pathlib import Path, PurePosixPath
import subprocess


def include(path):
    p = PurePosixPath(path)
    if any(part.startswith('.') or part in {'venv', '__pycache__', 'tests'} for part in p.parts):
        return False
    if path == 'web_data.bootstrap.db.gz':
        return True
    if p.suffix == '.py':
        return (len(p.parts) == 1 and not p.name.startswith('test_')) or (
            p.parts[0] in {'close_bet_staged', 'market_betting_engine'}
        )
    return p.parts[0] == 'config' and p.suffix == '.json'


def export(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target == source or source in target.parents:
        raise ValueError('Export destination must be outside source checkout')
    target.mkdir(parents=True, exist_ok=True)
    if any(p.name != '.git' for p in target.iterdir()):
        raise ValueError('Export destination must be empty except for .git')
    files = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z']).decode().split('\0')
    for name in filter(include, filter(None, files)):
        src = source / name
        if src.is_symlink():
            raise ValueError('Symlinks are not permitted: ' + name)
        dst = target / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    (target / 'requirements.txt').write_bytes((source / 'requirements-web.txt').read_bytes())
    (target / 'runtime.txt').write_text('python-3.11\n', encoding='utf-8')
    (target / '.gitignore').write_text(
        '__pycache__/\n*.pyc\n.env\n.streamlit/secrets.toml\n*.db\n*.db.gz\n!web_data.bootstrap.db.gz\n',
        encoding='utf-8',
    )
    sha = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip()
    (target / 'SOURCE_REVISION').write_text(sha + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default='.')
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    export(args.source, args.target)
