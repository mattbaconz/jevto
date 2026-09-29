# Demo recording

`jevto-demo.gif` is rendered from `demo.tape` with [VHS](https://github.com/charmbracelet/vhs) (needs `ttyd` and `ffmpeg`).

```powershell
cargo build --release -p jevto
python -c "import sys; sys.path.insert(0, 'benchmarks'); import token_bench as t; from pathlib import Path; t.setup_rust_failure(Path('demo-ws'))"
cd demo-ws; cargo test --no-run; cd ..
$env:JEVTO_DEMO_WS = (Resolve-Path demo-ws).Path
$env:PATH = "$PWD\target\release;$env:PATH"
vhs assets/demo/demo.tape
```

The workspace is the `rust-test-failure` scenario from the token benchmark: 150 passing tests and one failing assertion.
