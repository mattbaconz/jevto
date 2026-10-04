"""Installer failure/upgrade tests; --live also runs the unmodified release installer.

No actual user profiles or PATH are changed by the default/local checks. Unix
profile fixtures substitute a private test-home variable in a copy of the script.
--test-user-path is for disposable Windows CI runners only and restores HKCU PATH.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.1"
RELEASE_ROOT = "https://github.com/mattbaconz/jevto/releases/download"
SH = ROOT / "site/install.sh"
PS = ROOT / "site/install.ps1"


def run(command, env, ok=True):
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=240)
    if (result.returncode == 0) != ok:
        raise AssertionError(f"{command}: exit={result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shell_path(path):
    value = Path(path).resolve().as_posix()
    if os.name == "nt":
        return "/" + value[0].lower() + value[2:]
    return value


def base_env():
    env = dict(os.environ)
    remove = {"OPENROUTER_API_KEY", "JEVTO_INSTALL_DIR", "JEVTO_NO_PATH", "ZDOTDIR", "PSMODULEPATH"}
    for key in list(env):
        if key.upper() in remove:
            env.pop(key)
    return env


def unix_fixtures(shell, root):
    fixtures = root / "unix fixtures"
    fixtures.mkdir()
    commands = fixtures / "commands"
    commands.mkdir()
    (commands / "uname").write_text('#!/bin/sh\ncase "$1" in -s) echo "$JEVTO_TEST_OS";; -m) echo "$JEVTO_TEST_ARCH";; esac\n', newline="\n")
    (commands / "curl").write_text('''#!/bin/sh
set -eu
printf '%s\\n' "$*" > "$JEVTO_TEST_CURL_ARGS"
[ "${JEVTO_TEST_DOWNLOAD_FAIL:-0}" != 1 ] || exit 22
while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then cp "$JEVTO_TEST_ARCHIVE" "$2"; exit; fi
    shift
done
exit 2
''', newline="\n")
    for command in commands.iterdir():
        command.chmod(0o755)
    source = SH.read_text().replace("$HOME", "$JEVTO_TEST_HOME").replace(
        "set -eu", 'set -eu\n    PATH="$JEVTO_TEST_COMMANDS:$PATH"', 1
    )
    targets = [
        ("Linux", "x86_64", "x86_64-unknown-linux-gnu", "bash"),
        ("Darwin", "arm64", "aarch64-apple-darwin", "zsh"),
        ("Darwin", "x86_64", "x86_64-apple-darwin", "sh"),
    ]
    for index, (system, arch, target, user_shell) in enumerate(targets):
        test_dir = fixtures / f"{index} space's"
        test_dir.mkdir()
        profile_root = test_dir / "profiles"
        profile_root.mkdir()
        original_profile = "# existing user settings\nexport EXISTING_SETTING=preserved\n"
        profile = profile_root / {"bash": ".bashrc", "zsh": ".zshrc", "sh": ".profile"}[user_shell]
        profile.write_text(original_profile)
        install = test_dir / "bin"
        archive = test_dir / "fixture.tar.gz"
        payload = b'#!/bin/sh\nprintf "jevto 0.1.1\\n"\n'
        with tarfile.open(archive, "w:gz") as tar:
            entry = tarfile.TarInfo(f"jevto-v{VERSION}-{target}/jevto")
            entry.size = len(payload)
            entry.mode = 0o755
            tar.addfile(entry, io.BytesIO(payload))
        script = test_dir / "install.sh"
        script.write_text(re.sub(r"expected_hash='[a-f0-9]{64}'", f"expected_hash='{sha(archive)}'", source), newline="\n")
        env = base_env()
        env.update({
            "PATH": str(commands) + os.pathsep + env["PATH"],
            "JEVTO_TEST_OS": system, "JEVTO_TEST_ARCH": arch,
            "JEVTO_TEST_COMMANDS": shell_path(commands),
            "JEVTO_TEST_ARCHIVE": shell_path(archive),
            "JEVTO_TEST_CURL_ARGS": shell_path(test_dir / "curl.args"),
            "JEVTO_INSTALL_DIR": shell_path(install),
            "JEVTO_TEST_HOME": shell_path(profile_root), "SHELL": "/bin/" + user_shell,
        })
        run([shell, str(script)], env)
        assert (install / "jevto").read_bytes() == payload
        assert f"jevto-v{VERSION}-{target}.tar.gz" in (test_dir / "curl.args").read_text()
        assert profile.read_text().startswith(original_profile)
        assert Path(str(profile) + ".jevto.bak").read_text() == original_profile
        first_profile = profile.read_text()
        run([shell, str(script)], env)
        assert profile.read_text() == first_profile, "rerun must not duplicate PATH lines"
        (install / "jevto").write_bytes(b"old installation")
        run([shell, str(script)], env)
        assert (install / "jevto").read_bytes() == payload, "replace an older binary"
        assert profile.read_text() == first_profile
        env["JEVTO_TEST_PROFILE"] = shell_path(profile)
        resolved = run([shell, "-c", '. "$JEVTO_TEST_PROFILE"; command -v jevto'], env)
        assert resolved.stdout.strip() == shell_path(install / "jevto"), resolved.stdout
        original_binary = (install / "jevto").read_bytes()
        script.write_text(re.sub(r"expected_hash='[a-f0-9]{64}'", "expected_hash='" + "0" * 64 + "'", source), newline="\n")
        failed = run([shell, str(script)], env, ok=False)
        assert "checksum mismatch" in failed.stderr
        assert (install / "jevto").read_bytes() == original_binary
        assert profile.read_text() == first_profile
        env["JEVTO_TEST_DOWNLOAD_FAIL"] = "1"
        run([shell, str(script)], env, ok=False)
        assert (install / "jevto").read_bytes() == original_binary
        env.pop("JEVTO_TEST_DOWNLOAD_FAIL")
        # Even a correctly hashed archive must contain a runnable, matching version.
        with tarfile.open(archive, "w:gz") as tar:
            wrong_version = b'#!/bin/sh\nprintf "jevto 9.9.9\\n"\n'
            entry = tarfile.TarInfo(f"jevto-v{VERSION}-{target}/jevto")
            entry.size = len(wrong_version)
            entry.mode = 0o755
            tar.addfile(entry, io.BytesIO(wrong_version))
        script.write_text(re.sub(r"expected_hash='[a-f0-9]{64}'", f"expected_hash='{sha(archive)}'", source), newline="\n")
        failed = run([shell, str(script)], env, ok=False)
        assert "Unexpected release version" in failed.stderr
        assert (install / "jevto").read_bytes() == original_binary
        assert profile.read_text() == first_profile
        env["JEVTO_TEST_ARCH"] = "riscv64"
        failed = run([shell, str(script)], env, ok=False)
        assert "No prebuilt release" in failed.stderr
        print(f"PASS Unix {target}: selection, quoted paths, profiles, rerun, replacement, bad hash, wrong version, download failure, unsupported CPU")


def windows_tests(powershell, root, archive_path):
    source = PS.read_text()
    destination = root / "Windows install with spaces" / "jevto.exe"
    fixture = root / "windows-fixture.ps1"
    wrapper = '''function Invoke-WebRequest {
    param($UseBasicParsing, $Uri, $OutFile, $TimeoutSec)
    if ($env:JEVTO_TEST_DOWNLOAD_FAIL -eq '1') { throw 'Test download failure' }
    Copy-Item -LiteralPath $env:JEVTO_TEST_ARCHIVE -Destination $OutFile
}
'''.replace("$UseBasicParsing,", "[switch]$UseBasicParsing,")
    fixture.write_text(wrapper + source)
    env = base_env()
    env.update({"JEVTO_TEST_ARCHIVE": str(archive_path), "JEVTO_INSTALL_DIR": str(destination.parent), "JEVTO_NO_PATH": "1"})
    command = [powershell, "-NoProfile", "-NonInteractive", "-File", str(fixture)]
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        original_path = winreg.QueryValueEx(key, "Path")
    run(command, env)
    original_binary_hash = sha(destination)
    run(command, env)
    assert sha(destination) == original_binary_hash
    destination.write_bytes(b"old installation")
    run(command, env)
    assert sha(destination) == original_binary_hash, "replace an older binary"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        assert winreg.QueryValueEx(key, "Path") == original_path, "NoPath must preserve the real user PATH"
    fixture.write_text(wrapper + re.sub(r"\$expectedHash = '[a-f0-9]{64}'", "$expectedHash = '" + "0" * 64 + "'", source))
    failed = run(command, env, ok=False)
    assert "checksum mismatch" in failed.stderr
    assert sha(destination) == original_binary_hash
    env["JEVTO_TEST_DOWNLOAD_FAIL"] = "1"
    run(command, env, ok=False)
    assert sha(destination) == original_binary_hash
    env.pop("JEVTO_TEST_DOWNLOAD_FAIL")
    invalid_archive = root / "unexpected-entry.zip"
    with zipfile.ZipFile(invalid_archive, "w") as archive:
        archive.writestr("unexpected/jevto.exe", b"not an executable")
    env["JEVTO_TEST_ARCHIVE"] = str(invalid_archive)
    fixture.write_text(wrapper + re.sub(r"\$expectedHash = '[a-f0-9]{64}'", f"$expectedHash = '{sha(invalid_archive)}'", source))
    failed = run(command, env, ok=False)
    assert "expected executable" in failed.stderr
    assert sha(destination) == original_binary_hash
    env["PROCESSOR_ARCHITEW6432"] = "ARM64"
    failed = run(command, env, ok=False)
    assert "No prebuilt Windows release" in failed.stderr
    print("PASS Windows: real extraction/version, spaced path, rerun, replacement, unchanged user PATH, bad hash, invalid archive, download failure, unsupported CPU")

    # Exercise the actual PATH transformation in isolation, without a registry mutation.
    helper = re.search(r"    function Add-JevtoPathEntry.*?\n    }", source, re.S).group(0)
    assertions = r'''
if ((Add-JevtoPathEntry 'C:\one;%TEST_PLACEHOLDER%;C:\two' 'C:\new dir') -cne 'C:\one;%TEST_PLACEHOLDER%;C:\two;C:\new dir') { throw 'PATH entries changed' }
if ((Add-JevtoPathEntry 'C:\one;C:\NEW DIR\' 'C:\new dir') -cne 'C:\one;C:\NEW DIR\') { throw 'PATH duplicate' }
if ((Add-JevtoPathEntry '' 'C:\new dir') -cne 'C:\new dir') { throw 'Empty PATH' }
'''
    helper_file = root / "path-check.ps1"
    helper_file.write_text(helper + assertions)
    run([powershell, "-NoProfile", "-NonInteractive", "-File", str(helper_file)], base_env())
    print("PASS Windows PATH transformation: variables preserved, case-insensitive deduplication, empty PATH")


def live_install(shell, root, test_user_path=False):
    env = base_env()
    install = root / "live install"
    env.update({"JEVTO_INSTALL_DIR": str(install), "JEVTO_NO_PATH": "1"})
    if os.name == "nt":
        command = [shell, "-NoProfile", "-NonInteractive", "-File", str(PS)]
        executable = install / "jevto.exe"
    else:
        command = [shell, str(SH)]
        executable = install / "jevto"
    run(command, env)
    version = run([str(executable), "--version"], env).stdout.strip()
    assert version == f"jevto {VERSION}"
    print(f"PASS actual release install: {platform.system()} {platform.machine()}, {version}")
    if test_user_path:
        if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
            raise AssertionError("--test-user-path is only for disposable Windows GitHub Actions runners")
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
            original, original_kind = winreg.QueryValueEx(key, "Path")
            try:
                env.pop("JEVTO_NO_PATH")
                command = [shell, "-NoProfile", "-NonInteractive", "-Command", '& $env:JEVTO_TEST_SCRIPT; if ($?) { (Get-Command jevto).Source } else { exit 1 }']
                env["JEVTO_TEST_SCRIPT"] = str(PS)
                discovered = run(command, env).stdout.strip().splitlines()[-1]
                assert Path(discovered).resolve() == executable.resolve()
                added, kind = winreg.QueryValueEx(key, "Path")
                # .NET Framework expands Windows 8.3 paths before adding the directory.
                canonical_install = str(install.resolve())
                expected_path = original.rstrip(";") + ";" + canonical_install if original else canonical_install
                assert expected_path == added, "Preserve existing PATH entries and append the canonical directory"
                assert kind == original_kind
                run(command, env)
                assert winreg.QueryValueEx(key, "Path") == (added, kind)
            finally:
                winreg.SetValueEx(key, "Path", 0, original_kind, original)
        print("PASS disposable Windows user PATH install, current-session command discovery, registry type, idempotence, restoration")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--test-user-path", action="store_true")
    parser.add_argument("--archive", type=Path, help="Existing Windows release zip for local fixture checks")
    parser.add_argument("--shell", help="PowerShell or POSIX shell to test")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jevto-installer-test-") as directory:
        root = Path(directory)
        if os.name == "nt":
            shell = args.shell or shutil.which("powershell") or shutil.which("pwsh")
            archive_path = args.archive
            if archive_path is None:
                archive_path = root / "release.zip"
                urllib.request.urlretrieve(f"{RELEASE_ROOT}/v{VERSION}/jevto-v{VERSION}-x86_64-pc-windows-msvc.zip", archive_path)
            expected = re.search(r"\$expectedHash = '([a-f0-9]{64})'", PS.read_text()).group(1)
            assert sha(archive_path) == expected, "Windows fixture archive must be an authentic release"
            windows_tests(shell, root, archive_path)
            git_shell = Path(r"C:\Program Files\Git\bin\bash.exe")
            if git_shell.is_file():
                unix_fixtures(str(git_shell), root)
        else:
            shell = args.shell or shutil.which("sh")
            unix_fixtures(shell, root)
        if args.live:
            live_install(shell, root, args.test_user_path)
    print("Installer checks passed.")


if __name__ == "__main__":
    main()
