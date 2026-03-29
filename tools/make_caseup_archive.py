#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import zipfile
from pathlib import Path


DEFAULT_IGNORE_PATTERNS = [
    # Git
    ".git/",
    ".gitignore",

    # Env / secrets
    ".env",
    ".env.*",

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
    ".DS_Store",
    "Thumbs.db",

    # Local generated archives
    "*.zip",
]


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


def load_ignore_patterns(ignore_file: Path) -> list[str]:
    patterns: list[str] = []

    if ignore_file.exists():
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)

    for pattern in DEFAULT_IGNORE_PATTERNS:
        if pattern not in patterns:
            patterns.append(pattern)

    return patterns


def matches_pattern(rel_path: str, is_dir: bool, patterns: list[str]) -> bool:
    check_value = rel_path + ("/" if is_dir and not rel_path.endswith("/") else "")

    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(check_value, pattern):
            return True

        if pattern.endswith("/"):
            if rel_path.startswith(pattern) or check_value.startswith(pattern):
                return True

    return False


def should_skip(path: Path, root: Path, patterns: list[str]) -> bool:
    rel_path = path.relative_to(root).as_posix()

    # Проверяем сам объект
    if matches_pattern(rel_path, path.is_dir(), patterns):
        return True

    # Проверяем родителей, чтобы не заходить в игнорируемые каталоги
    current = path.parent
    while current != root and current != current.parent:
        parent_rel = current.relative_to(root).as_posix()
        if matches_pattern(parent_rel, True, patterns):
            return True
        current = current.parent

    return False


def collect_files(root: Path, patterns: list[str]) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []

    for path in root.rglob("*"):
        if path.is_dir():
            continue

        if should_skip(path, root, patterns):
            continue

        rel_path = path.relative_to(root).as_posix()
        result.append((path, rel_path))

    result.sort(key=lambda item: item[1])
    return result


def make_zip(root: Path, archive_path: Path, patterns: list[str]) -> int:
    files = collect_files(root, patterns)

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

    patterns = load_ignore_patterns(ignore_file)

    try:
        file_count = make_zip(root, archive_path, patterns)
    except Exception as exc:
        print(f"[ERROR] Failed to build archive: {exc}")
        return 1

    print(f"[OK] Version      : {version}")
    print(f"[OK] Archive      : {archive_path}")
    print(f"[OK] Files packed : {file_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())