#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_IGNORE_PATTERNS = [
    # Git
    ".git/",
    ".gitignore",

    # Env / secrets
    ".env",
    ".env.*",
    "!.env.example",
    "!.env.dev.example",
    "!.env.prod.example",
    "!.env.test.example",

    # Python cache / tooling
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "htmlcov/",

    # Virtual envs
    ".venv/",
    "venv/",
    "env/",

    # Frontend / build garbage
    "node_modules/",
    "dist/",
    "build/",

    # Django runtime artifacts
    "staticfiles/",
    "media/",
    "*.sqlite3",
    "*.db",
    "*.sql",
    "*.dump",
    "*.log",
    "*.tmp",

    # OS / IDE
    ".idea/",
    ".vscode/",
    ".claude/",
    ".DS_Store",
    "Thumbs.db",

    # Local generated archives
    "*.zip",
]


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negated: bool = False


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def extract_version(raw_text: str) -> str:
    """
    Поддерживает:
    - CaseUP build: 0.0.1.19 (13.03.2026)
    - CaseUP_ver_0.0.1.20_bug.zip
    - CaseUP_ver_0.0.1.20_actual.zip
    - CaseUP_ver_0.0.1.20_.zip
    - 0.0.1.20
    """
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)", raw_text)
    if not match:
        raise ValueError(
            "Не удалось извлечь версию из VERSION.txt. "
            "Ожидается строка, содержащая что-то вроде 0.0.1.20"
        )
    return match.group(1)


def build_archive_name(version: str) -> str:
    return f"CaseUP_ver_{version}.zip"


def normalize_rule(line: str) -> IgnoreRule:
    negated = line.startswith("!")
    pattern = line[1:] if negated else line
    return IgnoreRule(pattern=pattern.strip(), negated=negated)


def load_ignore_rules(ignore_file: Path) -> list[IgnoreRule]:
    raw_patterns: list[str] = []

    if ignore_file.exists():
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw_patterns.append(line)

    for pattern in DEFAULT_IGNORE_PATTERNS:
        if pattern not in raw_patterns:
            raw_patterns.append(pattern)

    return [normalize_rule(pattern) for pattern in raw_patterns]


def rule_matches_path(rel_path: str, is_dir: bool, pattern: str) -> bool:
    rel_path = rel_path.strip("/")
    check_value = rel_path + ("/" if is_dir else "")

    if pattern.endswith("/"):
        prefix = pattern.rstrip("/")
        return rel_path == prefix or rel_path.startswith(prefix + "/")

    return (
        fnmatch.fnmatch(rel_path, pattern)
        or fnmatch.fnmatch(check_value, pattern)
    )


def is_included_by_rules(path: Path, root: Path, rules: list[IgnoreRule]) -> bool:
    rel_path = path.relative_to(root).as_posix()
    is_dir = path.is_dir()

    included = True

    for rule in rules:
        if rule_matches_path(rel_path, is_dir, rule.pattern):
            included = rule.negated

    return included


def should_skip(path: Path, root: Path, rules: list[IgnoreRule]) -> bool:
    rel_path = path.relative_to(root).as_posix()

    # Сначала проверяем родителей: если каталог исключён и не переоткрыт,
    # внутрь заходить не надо.
    current = path.parent
    while current != root and current != current.parent:
        if not is_included_by_rules(current, root, rules):
            return True
        current = current.parent

    # Теперь сам файл / каталог
    return not is_included_by_rules(path, root, rules)


def collect_files(root: Path, rules: list[IgnoreRule]) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []

    for path in root.rglob("*"):
        if path.is_dir():
            continue

        if should_skip(path, root, rules):
            continue

        rel_path = path.relative_to(root).as_posix()
        result.append((path, rel_path))

    result.sort(key=lambda item: item[1])
    return result


def make_zip(root: Path, archive_path: Path, rules: list[IgnoreRule]) -> int:
    files = collect_files(root, rules)

    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        for abs_path, rel_path in files:
            zf.write(abs_path, arcname=rel_path)

    return len(files)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build clean CaseUP project archive.")
    parser.add_argument("--root", required=True, help="Project root path")
    parser.add_argument("--version-file", required=True, help="Path to VERSION.txt")
    parser.add_argument("--ignore-file", required=True, help="Path to ignore file")
    parser.add_argument("--output-dir", required=True, help="Directory for output zip")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    root = Path(args.root).resolve()
    version_file = Path(args.version_file).resolve()
    ignore_file = Path(args.ignore_file).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not root.exists() or not root.is_dir():
        print(f"[ERROR] Project root not found: {root}")
        return 1

    if not version_file.exists():
        print(f"[ERROR] VERSION.txt not found: {version_file}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        raw_version = read_text_file(version_file)
        version = extract_version(raw_version)
    except Exception as exc:
        print(f"[ERROR] Failed to read version: {exc}")
        return 1

    archive_name = build_archive_name(version)
    archive_path = output_dir / archive_name

    rules = load_ignore_rules(ignore_file)

    try:
        file_count = make_zip(root, archive_path, rules)
    except Exception as exc:
        print(f"[ERROR] Failed to build archive: {exc}")
        return 1

    print(f"[OK] Version      : {version}")
    print(f"[OK] Archive      : {archive_path}")
    print(f"[OK] Files packed : {file_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
