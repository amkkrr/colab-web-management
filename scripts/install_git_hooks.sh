#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="${REPO_ROOT}/.git/hooks"

if [ ! -d "${REPO_ROOT}/.git" ]; then
  echo "[error] ${REPO_ROOT} is not a git repository yet. Run 'git init' first." >&2
  exit 1
fi

mkdir -p "${HOOKS_DIR}"

cat > "${HOOKS_DIR}/pre-commit" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
python3 "${REPO_ROOT}/scripts/privacy_guard.py" --staged
EOF

cat > "${HOOKS_DIR}/pre-push" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
python3 "${REPO_ROOT}/scripts/privacy_guard.py" --all
python3 -m unittest discover -s "${REPO_ROOT}/tests"
EOF

chmod +x "${HOOKS_DIR}/pre-commit" "${HOOKS_DIR}/pre-push"
echo "[ok] Installed pre-commit & pre-push privacy/security hooks in ${HOOKS_DIR}"
echo "[tip] Optionally create ~/.config/colab-monitor/privacy_denylist.txt (one personal keyword per line, never committed)."
