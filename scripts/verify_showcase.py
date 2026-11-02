"""Offline portfolio validation. No third-party dependencies or secret output."""
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures = []
files = [p for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts]
for path in files:
    relative = path.relative_to(ROOT).as_posix()
    if path.name.startswith('.env') and path.name != '.env.example':
        failures.append(f'{relative}: environment file')
    if path.suffix.lower() in {'.sql', '.pem', '.key', '.db', '.log', '.zip', '.gz'}:
        failures.append(f'{relative}: excluded file type')
    text = path.read_text(encoding='utf-8')
    patterns = {
        'private key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'provider key': r'\bsk-[A-Za-z0-9_-]{20,}',
        'github token': r'\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}',
        'cloud key': r'\b(?:AKIA|LTAI)[A-Za-z0-9]{12,}',
        'credential URL': r'(?:mysql|postgres(?:ql)?|amqp|https?)://[^\s/:]+:[^\s/@]+@',
        'absolute local path': r'[CD]:[\\/](?:Users|001_D_desktop)[\\/]',
    }
    for label, pattern in patterns.items():
        if re.search(pattern, text):
            failures.append(f'{relative}: {label}')
    if path.suffix == '.py':
        ast.parse(text, filename=relative)
    if path.suffix == '.json':
        json.loads(text)
    if path.suffix == '.md':
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', text):
            if '://' in target or target.startswith('#'):
                continue
            local = target.split('#')[0]
            if local and not (path.parent / local).is_file():
                failures.append(f'{relative}: broken link {local}')

for item in json.loads((ROOT/'docs/source-manifest.json').read_text(encoding='utf-8')):
    digest = hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()
    if digest != item['sha256']:
        failures.append(f"{item['file']}: source hash mismatch")

print(json.dumps({'files_checked': len(files), 'failures': failures}, ensure_ascii=False, indent=2))
raise SystemExit(bool(failures))
