# Security Policy

## Security Model & Boundaries

This project is designed as a **local-first monitoring and remote control plane** for Google Colab runtimes:

1. **Loopback-only by default**: `server.py` binds to `127.0.0.1:8765` and refuses non-loopback addresses unless `--allow-remote` is explicitly passed. All HTTP endpoints enforce `Host` and `Origin` loopback checks to mitigate DNS rebinding and cross-origin access.
2. **Action Allowlist (No Arbitrary RCE)**: Remote management on Colab (`agent/colab_agent.py`) strictly rejects free-form shell execution and only accepts 19 enumerated actions with parameter validation, HMAC-SHA256 signature verification, `instance_id` binding, `expires_at` anti-replay windows, and shared `cmd_id` idempotency across HTTPS and Google Drive channels.
3. **Credential Isolation & Redaction**: API keys live exclusively in server memory / `_colab_monitor/secrets/` (never served to browsers) and all API responses and whitelisted logs undergo verbatim + regex token redaction.
4. **Pure-Local State Storage**: SQLite databases, notebook URL bindings, and notebook injection backups are stored outside cloud-synced directories and refuse to initialize inside Google Drive / CloudStorage paths.
5. **User Data & Secret Leak Prevention**: The repository ships with `.gitignore`, a zero-dependency local `pre-commit` privacy guard (`scripts/privacy_guard.py`), Gitleaks rules (`.gitleaks.toml`), and GitHub Actions automated security workflows (Bandit, CodeQL, Gitleaks, privacy guard, and regression tests).

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.
Instead, use GitHub's **Private Vulnerability Reporting** ("Report a vulnerability" in the repository's **Security** tab) and include:
- A clear description of the issue and affected component (`server.py`, `agent/colab_agent.py`, `control/*`, or `web/*`)
- Steps to reproduce or a minimal proof of concept
- Any suggested fix or mitigation
