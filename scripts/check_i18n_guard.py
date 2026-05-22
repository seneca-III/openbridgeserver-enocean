#!/usr/bin/env python3
"""Diff-scoped i18n hard gate for frontend files.

Checks:
1) Hardcoded user-facing strings in changed gui/src + frontend/src .vue/.js/.ts files.
   If base/head refs are provided, only added lines in the diff are scanned.
2) Locale key parity between de.json and en.json when locale files are changed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TARGET_FILE_RE = re.compile(r"^(gui/src|frontend/src)/.+\.(vue|js|ts)$")
LOCALE_FILE_RE = re.compile(r"^(gui|frontend)/src/locales/(de|en)\.json$")
TEMPLATE_BLOCK_RE = re.compile(r"<template\b[^>]*>(.*?)</template>", re.IGNORECASE | re.DOTALL)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
ATTR_RE = re.compile(
    r"\b(label|title|placeholder|alt|aria-label|helper-text|tooltip|caption|headline|confirm-text|cancel-text|no-data-text|loading-text)\s*=\s*(['\"])(.*?)\2"
)
TEXT_NODE_RE = re.compile(r">([^<{][^<]*)<")
UI_CALL_RE = re.compile(r"\b(?:alert|confirm|prompt|toast(?:\.[A-Za-z_][A-Za-z0-9_]*)?|notify(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\(\s*(['\"`])(.+?)\1")
ERROR_RE = re.compile(r"\b(?:throw\s+new\s+Error|new\s+Error)\s*\(\s*(['\"`])(.+?)\1")
ASSIGN_RE = re.compile(r"\b(?:errorMessage|warningMessage|successMessage|message|label|title|placeholder|tooltip|caption)\s*[:=]\s*(['\"`])(.+?)\1")
COMMENT_RE = re.compile(r"<!--.*?-->")


@dataclass
class Violation:
    path: str
    line: int
    kind: str
    snippet: str


class Allowlist:
    def __init__(self, path: Path):
        self.literal: set[str] = set()
        self.patterns: list[re.Pattern[str]] = []
        if not path.exists():
            return
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("re:"):
                self.patterns.append(re.compile(line[3:]))
            else:
                self.literal.add(line)

    def contains(self, text: str) -> bool:
        value = " ".join(text.split())
        if value in self.literal:
            return True
        return any(p.search(value) for p in self.patterns)


def run_git_diff(repo_root: Path, base: str | None, head: str | None) -> list[str]:
    candidates: list[list[str]] = []
    if base and head:
        candidates.append(["git", "diff", "--name-only", f"{base}...{head}"])
        candidates.append(["git", "diff", "--name-only", base, head])
    candidates.extend(
        [
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            ["git", "diff", "--name-only", "main...HEAD"],
            ["git", "diff", "--name-only", "HEAD~1...HEAD"],
        ]
    )

    for cmd in candidates:
        proc = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True)
        if proc.returncode == 0:
            files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if files:
                return sorted(set(files))
            return []

    tried = " | ".join(" ".join(c) for c in candidates)
    raise RuntimeError(f"Could not determine changed files via git diff ({tried})")


def parse_added_lines(diff_output: str) -> dict[str, set[int]]:
    added_lines: dict[str, set[int]] = defaultdict(set)
    current_file: str | None = None
    current_new_line: int | None = None

    for raw_line in diff_output.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:]
            current_new_line = None
            continue

        if raw_line.startswith("@@ "):
            match = HUNK_RE.match(raw_line)
            current_new_line = int(match.group(1)) if match else None
            continue

        if current_file is None or current_new_line is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added_lines[current_file].add(current_new_line)
            current_new_line += 1
            continue

        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue

        current_new_line += 1

    return dict(added_lines)


def run_git_diff_added_lines(repo_root: Path, base: str | None, head: str | None) -> dict[str, set[int]] | None:
    if not base or not head:
        return None

    candidates = [
        ["git", "diff", "--unified=0", "--no-color", f"{base}...{head}"],
        ["git", "diff", "--unified=0", "--no-color", base, head],
    ]

    for cmd in candidates:
        proc = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True)
        if proc.returncode == 0:
            return parse_added_lines(proc.stdout)

    return None


def flatten_keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        joined = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys.update(flatten_keys(value, joined))
        else:
            keys.add(joined)
    return keys


def has_letters(text: str) -> bool:
    return re.search(r"[A-Za-zÄÖÜäöüß]", text) is not None


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def is_technical_token(text: str) -> bool:
    compact = text.strip()
    if compact.startswith(("http://", "https://", "/", "./", "../", "#")):
        return True
    if compact.startswith("[") and compact.endswith("]"):
        return True
    if compact.upper() == compact and re.fullmatch(r"[A-Z0-9_./:+-]+", compact):
        return True
    if re.fullmatch(r"[A-Za-z0-9_./:+-]+", compact):
        return True
    return False


def should_flag(candidate: str, allowlist: Allowlist) -> bool:
    text = normalize_text(candidate)
    if len(text) < 2:
        return False
    if not has_letters(text):
        return False
    if "{{" in text or "}}" in text:
        return False
    if "$t(" in text or "t(" in text:
        return False
    if text.startswith(("$t(", "t(")):
        return False
    if is_technical_token(text):
        return False
    if allowlist.contains(text):
        return False
    return True


def add_violations_from_matches(
    *,
    path: str,
    line: int,
    kind: str,
    matches: Iterable[re.Match[str]],
    candidate_group: int,
    allowlist: Allowlist,
    sink: list[Violation],
) -> None:
    for match in matches:
        candidate = match.group(candidate_group)
        if should_flag(candidate, allowlist):
            sink.append(Violation(path=path, line=line, kind=kind, snippet=normalize_text(candidate)))


def scan_vue(path: str, content: str, allowlist: Allowlist, changed_lines: set[int] | None) -> list[Violation]:
    violations: list[Violation] = []

    for tpl_match in TEMPLATE_BLOCK_RE.finditer(content):
        tpl = tpl_match.group(1)
        start_line = content.count("\n", 0, tpl_match.start(1)) + 1
        for idx, raw_line in enumerate(tpl.splitlines(), start=start_line):
            if changed_lines is not None and idx not in changed_lines:
                continue
            line = COMMENT_RE.sub("", raw_line)
            if not line.strip():
                continue
            add_violations_from_matches(
                path=path,
                line=idx,
                kind="template-attr",
                matches=ATTR_RE.finditer(line),
                candidate_group=3,
                allowlist=allowlist,
                sink=violations,
            )
            add_violations_from_matches(
                path=path,
                line=idx,
                kind="template-text",
                matches=TEXT_NODE_RE.finditer(line),
                candidate_group=1,
                allowlist=allowlist,
                sink=violations,
            )

    stripped = TEMPLATE_BLOCK_RE.sub("\n", content)
    for line_no, raw_line in enumerate(stripped.splitlines(), start=1):
        if changed_lines is not None and line_no not in changed_lines:
            continue
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-ui-call",
            matches=UI_CALL_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-error",
            matches=ERROR_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-assign",
            matches=ASSIGN_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )

    return violations


def scan_script(path: str, content: str, allowlist: Allowlist, changed_lines: set[int] | None) -> list[Violation]:
    violations: list[Violation] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        if changed_lines is not None and line_no not in changed_lines:
            continue
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-ui-call",
            matches=UI_CALL_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-error",
            matches=ERROR_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-assign",
            matches=ASSIGN_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
    return violations


def scan_hardcoded_strings(
    repo_root: Path,
    files: list[str],
    allowlist: Allowlist,
    added_lines_by_file: dict[str, set[int]],
    line_filtering_enabled: bool,
) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path in files:
        path = repo_root / rel_path
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        changed_lines = added_lines_by_file.get(rel_path, set()) if line_filtering_enabled else None
        if rel_path.endswith(".vue"):
            violations.extend(scan_vue(rel_path, content, allowlist, changed_lines))
        else:
            violations.extend(scan_script(rel_path, content, allowlist, changed_lines))
    return sorted(violations, key=lambda v: (v.path, v.line, v.kind, v.snippet))


def check_locale_pair(repo_root: Path, app: str) -> list[str]:
    locale_root = repo_root / app / "src" / "locales"
    de_path = locale_root / "de.json"
    en_path = locale_root / "en.json"
    try:
        de = json.loads(de_path.read_text(encoding="utf-8"))
        en = json.loads(en_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{app}: invalid JSON in locale files ({exc})"]

    if not isinstance(de, dict) or not isinstance(en, dict):
        return [f"{app}: locale root must be a JSON object in both de.json and en.json"]

    de_keys = flatten_keys(de)
    en_keys = flatten_keys(en)
    errors: list[str] = []

    for key in sorted(de_keys - en_keys):
        errors.append(f"{app}/src/locales/en.json is missing key: {key}")
    for key in sorted(en_keys - de_keys):
        errors.append(f"{app}/src/locales/de.json is missing key: {key}")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff-scoped i18n hard gate")
    parser.add_argument("--base", help="Base git ref for diff", default=None)
    parser.add_argument("--head", help="Head git ref for diff", default=None)
    parser.add_argument("--allowlist", default="scripts/i18n-allowlist.txt", help="Allowlist file path")
    parser.add_argument("files", nargs="*", help="Explicit files to scan instead of deriving from git diff")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    if args.files:
        changed_files = sorted(set(args.files))
        added_lines_by_file: dict[str, set[int]] = {}
        line_filtering_enabled = False
    else:
        changed_files = run_git_diff(repo_root, args.base, args.head)
        added_lines_by_file_or_none = run_git_diff_added_lines(repo_root, args.base, args.head)
        line_filtering_enabled = added_lines_by_file_or_none is not None
        added_lines_by_file = added_lines_by_file_or_none or {}

    if not changed_files:
        print("i18n guard: no changed files in diff; nothing to do.")
        return 0

    scan_files = [p for p in changed_files if TARGET_FILE_RE.match(p)]
    touched_apps = {m.group(1) for p in changed_files if (m := LOCALE_FILE_RE.match(p))}

    if not scan_files and not touched_apps:
        print("i18n guard: no relevant frontend/i18n files in diff; nothing to do.")
        return 0

    allowlist = Allowlist(repo_root / args.allowlist)
    violations = scan_hardcoded_strings(
        repo_root,
        scan_files,
        allowlist,
        added_lines_by_file,
        line_filtering_enabled,
    )

    locale_errors: list[str] = []
    for app in sorted(touched_apps):
        locale_errors.extend(check_locale_pair(repo_root, app))

    if violations:
        print("i18n guard: hardcoded user-facing strings detected in changed files:")
        for v in violations:
            print(f"  {v.path}:{v.line}: [{v.kind}] {v.snippet}")

    if locale_errors:
        print("i18n guard: locale consistency errors:")
        for err in locale_errors:
            print(f"  {err}")

    if violations or locale_errors:
        print("i18n guard: FAILED")
        return 1

    print(f"i18n guard: OK (scanned {len(scan_files)} changed frontend files; locale-app-checks: {', '.join(sorted(touched_apps)) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
