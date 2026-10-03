# Demo recording

## Launch recording (v0.1.1, 2026-10-03)

The website's `site/demo/jevto-demo.mp4` is a VHS recording of the real released Windows CLI on the generated Rust fixture. No provider call is made. The fixture is synthetic; output is not fabricated. Typing and reading holds are presentation timing, not latency measurements.

Download the Windows v0.1.1 archive and its checksum from GitHub Releases, verify the archive's SHA-256, and extract it. Use an isolated workspace that does not already exist, a working Cargo toolchain, and the extracted binary:

```powershell
python assets/demo/prepare_launch.py --workspace C:\demo\jevto-launch --jevto C:\tools\jevto.exe
$env:JEVTO_DEMO_WS = 'C:\demo\jevto-launch'
$env:JEVTO_STORE_DIR = 'C:\demo\jevto-launch\.jevto-demo'
$env:JEVTO_MODE = 'rules'
$env:CARGO_TERM_COLOR = 'never'
$env:RUST_TEST_THREADS = '1'
$env:PATH = 'C:\tools;' + $env:PATH
vhs assets/demo/launch.tape
python assets/demo/prepare_launch.py --after-recording --workspace C:\demo\jevto-launch --jevto C:\tools\jevto.exe
```

VHS needs its normal `ttyd`, browser, and `ffmpeg` dependencies. Commands above use PowerShell and should run in a disposable shell so their environment overrides do not persist into your normal workflow. The preparation command refuses to overwrite an existing fixture.

The committed `site/demo/proof.json` records the executable hash/version, recorded capture, exit 101, and separate byte-exact stdout/stderr recall checks after recording. The accompanying VTT and HTML transcript describe the silent recording. Full recall restores both original streams; `--lines` narrows a stream and prints the selected bytes to stdout.

## Earlier recording

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
