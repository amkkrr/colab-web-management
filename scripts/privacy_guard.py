#!/usr/bin/env python3
"""
Zero-dependency Privacy & User-Data Leak Guard for Public Repositories.

Used by:
1. Local Git pre-commit / pre-push hooks (`scripts/install_git_hooks.sh`)
2. pre-commit framework (`.pre-commit-config.yaml`)
3. GitHub Actions automated security pipeline (`.github/workflows/security-and-ci.yml`)

Blocks accidental commits of:
- User runtime files (active_endpoint.json, uptime_ledger.json, *.sqlite3, *.ipynb, outputs/, _colab_monitor/)
- Bytecode & OS metadata (*.pyc embeds absolute local paths + emails!, .DS_Store)
- Local host paths (/Users/<name>/, C:\\Users\\<name>\\, GoogleDrive-<email>)
- Personal emails, API keys/tokens, private keys, and real trycloudflare.com URLs
- Optional private local denylist terms (~/.config/colab-monitor/privacy_denylist.txt, never committed)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_DENYLIST = Path.home() / ".config" / "colab-monitor" / "privacy_denylist.txt"

FORBIDDEN_FILENAMES = frozenset(
    {
        ".ds_store",
        "thumbs.db",
        "active_endpoint.json",
        "uptime_ledger.json",
        "notebooks_launcher.json",
        "shutdown_markers.json",
        "privacy_denylist.txt",
    }
)

FORBIDDEN_EXTENSIONS = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".sqlite",
        ".sqlite3",
        ".sqlite3-wal",
        ".sqlite3-shm",
        ".ipynb",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".log",
    }
)

FORBIDDEN_DIR_SEGMENTS = frozenset(
    {
        "__pycache__",
        "_colab_monitor",
        "outputs",
        "ref_inputs",
        "notebook_backups",
        "secrets",
    }
)

MAX_NON_FONT_BYTES = 512 * 1024

CONTENT_RULES: list[tuple[str, re.Pattern[str], bool]] = [
    # (rule_id, regex, skip_in_tests_and_policy_files)
    ("private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), False),
    ("openai-or-sk-key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), True),
    ("google-oauth-token", re.compile(r"\bya29\.[A-Za-z0-9._-]{10,}\b"), True),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), True),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{12,}\b"), True),
    ("github-pat", re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), False),
    ("personal-email", re.compile(r"[A-Za-z0-9._%+-]+@(?:gmail|icloud|qq|163|126|outlook|hotmail|protonmail)\.[A-Za-z]{2,}", re.I), True),
    ("local-user-path", re.compile(r"(?:/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|C:\\Users\\[A-Za-z0-9._-]+\\)"), True),
    ("google-drive-account-path", re.compile(r"GoogleDrive-[A-Za-z0-9._%+-]+@", re.I), True),
    (
        "real-trycloudflare-tunnel",
        re.compile(r"https://(?!demo-|example-)[a-z0-9]+-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+\.trycloudflare\.com", re.I),
        True,
    ),
]

POLICY_SELF_FILES = frozenset({"scripts/privacy_guard.py", ".gitleaks.toml", "web/assets/fonts/OFL.txt"})


def load_denylist(extra_path: Path | None = None) -> list[str]:
    terms: list[str] = []
    paths = [DEFAULT_LOCAL_DENYLIST]
    if extra_path:
        paths.append(extra_path)
    for p in paths:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    clean = line.strip()
                    if clean and not clean.startswith("#") and len(clean) >= 3:
                        terms.append(clean)
            except OSError:
                continue
    return sorted(set(terms), key=len, reverse=True)


def check_file(rel_path: str, abs_path: Path, denylist: list[str]) -> list[str]:
    errors: list[str] = []
    norm_rel = rel_path.replace("\\", "/").lstrip("./")
    parts = [p for p in norm_rel.split("/") if p]
    fname = parts[-1] if parts else norm_rel
    fname_low = fname.lower()
    suffix_low = Path(fname).suffix.lower()

    # 1. Forbidden file names, extensions & directories
    if fname_low in FORBIDDEN_FILENAMES or fname_low.startswith(".env"):
        errors.append(f"[FORBIDDEN_FILE] {norm_rel}: runtime/personal file must never be committed")
    if suffix_low in FORBIDDEN_EXTENSIONS:
        errors.append(f"[FORBIDDEN_EXT] {norm_rel}: disallowed extension {suffix_low} (may contain local paths/secrets/user data)")
    for seg in parts[:-1]:
        if seg.lower() in FORBIDDEN_DIR_SEGMENTS:
            errors.append(f"[FORBIDDEN_DIR] {norm_rel}: directory '{seg}/' must never be committed")
            break
    if fname_low.startswith("._tmp_") or fname_low.startswith("._"):
        errors.append(f"[FORBIDDEN_TMP] {norm_rel}: temporary or AppleDouble file")

    if not abs_path.is_file():
        return errors

    # 2. Unexpected large binary/data guard
    try:
        size = abs_path.stat().st_size
    except OSError:
        return errors
    if suffix_low != ".woff2" and size > MAX_NON_FONT_BYTES:
        errors.append(f"[OVERSIZED_FILE] {norm_rel}: {size // 1024} KiB exceeds 512 KiB non-font limit")

    # Skip font binaries for text scanning
    if suffix_low in (".woff2", ".woff", ".ttf", ".png", ".jpg", ".jpeg", ".webp"):
        return errors

    try:
        raw_bytes = abs_path.read_bytes()
    except OSError:
        return errors
    if b"\x00" in raw_bytes[:8192]:
        return errors

    text = raw_bytes.decode("utf-8", errors="replace")
    is_test_or_policy = norm_rel.startswith("tests/") or norm_rel in POLICY_SELF_FILES

    for line_no, line in enumerate(text.splitlines(), 1):
        if "privacy-guard: allow" in line:
            continue
        for rule_id, pat, skip_in_test in CONTENT_RULES:
            if skip_in_test and is_test_or_policy:
                continue
            m = pat.search(line)
            if m:
                snippet = m.group(0)[:40]
                errors.append(f"[{rule_id}] {norm_rel}:{line_no}: matched disallowed pattern ({snippet}...)")
        if not is_test_or_policy and denylist:
            low_line = line.lower()
            for term in denylist:
                if term.lower() in low_line:
                    errors.append(f"[private-denylist] {norm_rel}:{line_no}: matched local private denylist term")
                    break

    return errors


def _git_files(staged_only: bool) -> list[str] | None:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged_only else ["git", "ls-files"]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None
        return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    except OSError:
        return None


def _walk_workspace_files() -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", "venv")]
        for fn in filenames:
            abs_p = Path(dirpath) / fn
            files.append(abs_p.relative_to(ROOT).as_posix())
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Privacy & secret leak guard for public GitHub repos")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="Scan git staged files (for pre-commit hook)")
    group.add_argument("--all", action="store_true", help="Scan all tracked/workspace files (for CI & pre-push)")
    group.add_argument("--files", nargs="+", help="Scan explicit file paths")
    parser.add_argument("--denylist", type=Path, default=None, help="Optional extra private denylist file path")
    args = parser.parse_args(argv)

    denylist = load_denylist(args.denylist)
    if args.files:
        targets = []
        for f in args.files:
            p = Path(f)
            abs_p = p.resolve() if p.is_absolute() else (ROOT / p).resolve()
            try:
                rel = abs_p.relative_to(ROOT).as_posix()
            except ValueError:
                rel = p.as_posix()
            targets.append((rel, abs_p))
    elif args.staged:
        staged = _git_files(staged_only=True)
        if staged is None:
            print("[privacy-guard] Not inside a git repository; skipping --staged check.", file=sys.stderr)
            return 0
        targets = [(rel, (ROOT / rel).resolve()) for rel in staged]
    else:
        tracked = _git_files(staged_only=False)
        rel_list = tracked if tracked is not None else _walk_workspace_files()
        targets = [(rel, (ROOT / rel).resolve()) for rel in rel_list]

    all_errors: list[str] = []
    for rel, abs_p in targets:
        all_errors.extend(check_file(rel, abs_p, denylist))

    if all_errors:
        print(f"[privacy-guard] BLOCKED: found {len(all_errors)} privacy/security violation(s):", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"[privacy-guard] OK: scanned {len(targets)} file(s), 0 privacy/security violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
