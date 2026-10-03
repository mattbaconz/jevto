"""Prepare and verify the real CLI fixture used by launch.tape (no provider calls)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'benchmarks'))
from token_bench import setup_rust_failure


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--jevto', type=Path, required=True)
    parser.add_argument('--after-recording', action='store_true')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    binary = args.jevto.resolve()
    env = os.environ.copy()
    env.pop('OPENROUTER_API_KEY', None)
    env['JEVTO_MODE'] = 'rules'
    env['JEVTO_STORE_DIR'] = str(workspace / '.jevto-demo')
    env['CARGO_TERM_COLOR'] = 'never'
    env['RUST_TEST_THREADS'] = '1'
    if not args.after_recording:
        if workspace.exists():
            raise SystemExit('Choose a new demo workspace; existing work is never overwritten.')
        setup_rust_failure(workspace)
        subprocess.run(['cargo', 'test', '--no-run', '--offline'], cwd=workspace, env=env, check=True, capture_output=True)
        native = subprocess.run(['cargo', 'test'], cwd=workspace, env=env, capture_output=True)
        selected = subprocess.run([str(binary), 'run', '--mode', 'rules', '--', 'cargo', 'test'], cwd=workspace, env=env, capture_output=True)
        assert native.returncode == selected.returncode == 101
        assert b'leading space must be rejected' in selected.stdout + selected.stderr
        (workspace / 'selected.txt').write_bytes(selected.stdout + selected.stderr)
        print(selected.stdout.decode('utf-8', errors='replace'))
    store = workspace / '.jevto-demo' / 'captures'
    metadata = sorted(store.glob('*.json'), key=lambda p: p.stat().st_mtime)[-1]
    capture = json.loads(metadata.read_text())
    capture_id = capture['capture_id']
    assert capture['exit_code'] == 101
    recalled = subprocess.run([str(binary), 'recall', capture_id], cwd=workspace, env=env, capture_output=True, check=True)
    streams = {}
    for stream in ('stdout', 'stderr'):
        raw = (store / f'{capture_id}.{stream}').read_bytes()
        returned = recalled.stdout if stream == 'stdout' else recalled.stderr
        assert raw == returned, f'{stream} recall differs from captured bytes'
        streams[stream] = {'bytes': len(raw), 'sha256': digest(raw), 'recall_exact': True}
    proof = {
        'recorded_date': '2026-10-03',
        'version': subprocess.check_output([str(binary), '--version'], env=env, text=True).strip(),
        'executable_sha256': digest(binary.read_bytes()),
        'scenario': 'Generated Rust fixture: 150 passing tests, one deliberately failing leading-space assertion',
        'mode': 'rules',
        'provider_calls': 0,
        'exit_code': capture['exit_code'],
        'capture_id': capture_id,
        'completeness': capture['completeness'],
        'streams': streams,
        'footage': 'Actual command execution recorded by VHS; synthetic fixture; typing and reading holds are presentation timing, not performance measurements.',
        'verified_after_recording': args.after_recording,
    }
    (ROOT / 'site/demo/proof.json').write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
