#!/bin/sh
# JevTO installer: https://github.com/mattbaconz/jevto
# Optional: JEVTO_INSTALL_DIR=/absolute/path or JEVTO_NO_PATH=1.
# Keep execution at the end so a truncated download cannot start an installation.
jevto_install() (
    set -eu
    release='v0.1.1'
    release_root='https://github.com/mattbaconz/jevto/releases/download'
    fail() { printf 'JevTO: %s\n' "$*" >&2; exit 1; }
    command -v curl >/dev/null 2>&1 || fail 'curl is required. You can also download a release at https://jevto.xyz/docs/#manual-install'
    command -v tar >/dev/null 2>&1 || fail 'tar is required to extract the release.'
    case "$(uname -s)/$(uname -m)" in
        Darwin/arm64|Darwin/aarch64)
            target='aarch64-apple-darwin'
            expected_hash='cbfdad8d0492e7760ba74f489aaf27697c9c9136332ca959f08d52d6d0db7b32' ;;
        Darwin/x86_64)
            target='x86_64-apple-darwin'
            expected_hash='d2147291dbbdb4aa980c4509c11394b05b219610f9a352a4782c337fbef07fa2' ;;
        Linux/x86_64)
            target='x86_64-unknown-linux-gnu'
            expected_hash='c3355276955373a8c8873964325bca13a7cb9988917aa9a3ebe3f7cb1cef504b' ;;
        *) fail 'No prebuilt release for this OS/CPU. Windows: use install.ps1. Other systems: https://jevto.xyz/docs/#manual-install' ;;
    esac
    case "${JEVTO_NO_PATH:-0}" in 0|1) ;; *) fail 'JEVTO_NO_PATH must be 0 or 1.' ;; esac
    install_dir=${JEVTO_INSTALL_DIR:-"$HOME/.local/bin"}
    case "$install_dir" in /*) ;; *) fail 'JEVTO_INSTALL_DIR must be an absolute path.' ;; esac
    case "$install_dir" in *:*|*'
'*|*"$(printf '\r')"*) fail 'The install directory cannot contain colons or newlines.' ;; esac
    [ ! -d "$install_dir/jevto" ] || fail "A directory already exists at $install_dir/jevto."
    archive_name="jevto-$release-$target"
    temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/jevto-install.XXXXXXXX")
    staged_file=''
    cleanup() {
        [ -z "$staged_file" ] || rm -f "$staged_file"
        # This directory is private and was returned by mktemp above.
        [ -z "$temp_dir" ] || rm -rf "$temp_dir"
    }
    trap cleanup EXIT
    trap 'exit 1' HUP INT TERM
    printf 'Downloading JevTO %s for %s...\n' "$release" "$target"
    curl --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 180 --retry 2 \
        -fsSL "$release_root/$release/$archive_name.tar.gz" -o "$temp_dir/release.tar.gz"
    if command -v sha256sum >/dev/null 2>&1; then
        actual_hash=$(sha256sum "$temp_dir/release.tar.gz")
    elif command -v shasum >/dev/null 2>&1; then
        actual_hash=$(shasum -a 256 "$temp_dir/release.tar.gz")
    else
        fail 'A SHA-256 tool is required (sha256sum or shasum). Nothing was installed.'
    fi
    [ "${actual_hash%% *}" = "$expected_hash" ] || fail 'Release checksum mismatch. Nothing was installed.'
    tar -xzf "$temp_dir/release.tar.gz" -C "$temp_dir" "$archive_name/jevto"
    candidate="$temp_dir/$archive_name/jevto"
    [ -f "$candidate" ] && [ ! -L "$candidate" ] || fail 'The archive does not contain a regular JevTO executable.'
    chmod 755 "$candidate"
    version_output=$("$candidate" --version 2>&1) || fail "The release cannot run on this system: $version_output"
    [ "$version_output" = "jevto ${release#v}" ] || fail "Unexpected release version: $version_output"
    mkdir -p "$install_dir"
    staged_file=$(mktemp "$install_dir/.jevto.XXXXXXXX")
    cp "$candidate" "$staged_file"
    chmod 755 "$staged_file"
    mv -f "$staged_file" "$install_dir/jevto"
    staged_file=''

    if [ "${JEVTO_NO_PATH:-0}" != 1 ]; then
        # Quote the literal path, including spaces and apostrophes, for shell profiles.
        quoted_dir="'$(printf '%s' "$install_dir" | sed "s/'/'\\\\''/g")'"
        path_line="case \":\$PATH:\" in *:${quoted_dir}:*) ;; *) export PATH=${quoted_dir}:\"\$PATH\" ;; esac # JevTO"
        add_profile() {
            if [ -f "$1" ] && grep -Fqx "$path_line" "$1"; then return; fi
            if [ -f "$1" ] && [ ! -e "$1.jevto.bak" ]; then cp -p "$1" "$1.jevto.bak"; fi
            printf '\n%s\n' "$path_line" >> "$1"
            printf 'Added PATH entry to %s\n' "$1"
        }
        current_shell=${SHELL:-}
        case "${current_shell##*/}" in
            zsh)
                mkdir -p "${ZDOTDIR:-$HOME}"
                add_profile "${ZDOTDIR:-$HOME}/.zshrc" ;;
            bash)
                add_profile "$HOME/.bashrc"
                if [ -f "$HOME/.bash_profile" ]; then add_profile "$HOME/.bash_profile"
                elif [ -f "$HOME/.bash_login" ]; then add_profile "$HOME/.bash_login"
                else add_profile "$HOME/.profile"; fi ;;
            sh|dash|ksh|'') add_profile "$HOME/.profile" ;;
            *) printf 'Shell not configured automatically. Add %s to your PATH.\n' "$install_dir" ;;
        esac
    fi
    printf '\nInstalled %s to %s/jevto\n' "$version_output" "$install_dir"
    if [ "${JEVTO_NO_PATH:-0}" = 1 ]; then
        printf 'PATH was left unchanged (JEVTO_NO_PATH=1).\n'
    else
        printf 'Open a new terminal to use jevto, or run %s/jevto now.\n' "$install_dir"
    fi
    printf 'Claude Code setup: https://jevto.xyz/docs/#claude-code\n'
    printf 'Agent hooks were not changed. Rerun this installer to update.\n'
)
jevto_install
