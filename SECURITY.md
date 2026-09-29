# Security policy

JevTO runs commands with your permissions, stores their output locally, and can install hooks into coding-agent configurations. Please report anything that could widen permissions, leak captured output, or run a command the user did not ask for.

## Reporting

Open a private security advisory on the project repository, or email the maintainer listed in the repository profile. Please include the JevTO version (`jevto --version`), your OS, and reproduction steps. Do not include real secrets in reports.

## Scope and design commitments

- Hooks never set a permission decision; a failed or unparsable hook leaves the command native.
- The Claude and Codex rewrite hooks only wrap simple, allowlisted commands. Pipes, redirection, command substitution, and background execution are left untouched.
- The default MCP server exposes no command runner. The opt-in exact-command runner requires an external policy file pinned by SHA-256 and a separate host grant.
- Deterministic mode makes no network requests. Adaptive mode sends only policy-allowed, size-bounded, secret-filtered text to the Jev endpoint, and only with an explicit opt-in on each run.
- Captured output is stored unencrypted in the local user store for 24 hours by default. A capture ID grants recall to anyone who can read that store.

Known limits are documented in the README and in `jevto doctor --json`.
