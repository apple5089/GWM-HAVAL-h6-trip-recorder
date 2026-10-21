"""Check publishable files; report locations, never matching secret values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BLOCKED_DIRS={'data','backups','.secrets','gwm_client','racing-review','.capture-private'}
BLOCKED_SUFFIXES={'.enc','.key','.pem','.sqlite3','.db','.apk','.dex','.xlog','.pfx','.p12','.zip','.gz'}
PATTERNS={
    'private-key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token':re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'aws-access-key':re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
}

def files():
    result=subprocess.run(['git','-C',str(ROOT),'ls-files','-z'],capture_output=True)
    if result.returncode==0:
        return [ROOT/name.decode('utf-8') for name in result.stdout.split(b'\0') if name]
    return [p for p in ROOT.rglob('*') if p.is_file() and not any(x in p.relative_to(ROOT).parts for x in ('.git','__pycache__','.pytest_cache','.venv','external'))]

def main():
    problems=[];candidates=files()
    for path in candidates:
        relative=path.relative_to(ROOT)
        if any(part in BLOCKED_DIRS for part in relative.parts) or path.suffix in BLOCKED_SUFFIXES or path.name=='.env' or path.name.startswith('.env.') and path.name!='.env.example':
            problems.append((str(relative),'private-or-external-file'))
        raw=path.read_bytes()
        for label,pattern in PATTERNS.items():
            if pattern.search(raw):problems.append((str(relative),label))
    if problems:
        for path,label in problems:print(f'{path}: {label}')
        return 1
    print(f'Public tree check passed ({len(candidates)} files)')
    return 0

if __name__=='__main__':sys.exit(main())
