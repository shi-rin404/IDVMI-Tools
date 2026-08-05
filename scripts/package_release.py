from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


DEFAULT_ADDON_DIR_NAME = "IDVMI-Tools"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

EXCLUDED_PARTS = {
    ".git",
    ".github",
    ".vscode",
    ".claude",
    ".codex",
    ".agents",
    "__pycache__",
    "remote_import_cache",
    "dist",
    "user",
}
EXCLUDED_TOP_LEVEL = {
    ".gitignore",
    "scripts",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
}
EXCLUDED_NAMES = {
    "direct_url.json",
    "export_per_material_log.txt",
    "import_per_material_log.txt",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def ensure_clean_tree(root: Path) -> None:
    status = run_git(root, ["status", "--porcelain", "--untracked-files=no"])
    if status.strip():
        raise SystemExit(
            "Refusing to package a dirty tracked working tree. "
            "Commit or stash changes, or pass --allow-dirty."
        )


def tracked_files(root: Path) -> list[Path]:
    output = run_git(root, ["ls-files", "-z"])
    paths = []
    for item in output.split("\0"):
        if item:
            paths.append(root / item)
    return paths


def addon_version(root: Path) -> tuple[int, ...]:
    init_path = root / "__init__.py"
    module = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "bl_info" for target in node.targets):
            continue
        bl_info = ast.literal_eval(node.value)
        return tuple(int(part) for part in bl_info["version"])
    raise ValueError("Could not find bl_info['version'] in __init__.py")


def format_version(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def format_archive_version(version: tuple[int, ...]) -> str:
    return "_".join(str(part) for part in version)


def should_package(root: Path, path: Path, *, include_scripts: bool) -> bool:
    if not path.is_file():
        return False

    relative = path.relative_to(root)
    parts = set(relative.parts)
    if parts & EXCLUDED_PARTS:
        return False
    if relative.parts[0] in EXCLUDED_TOP_LEVEL and not (include_scripts and relative.parts[0] == "scripts"):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if path.name in EXCLUDED_NAMES:
        return False
    return True


def zip_name_for(root: Path, path: Path, prefix: str) -> str:
    relative = path.relative_to(root)
    return str(PurePosixPath(prefix, *relative.parts))


def write_zip(output_path: Path, root: Path, files: list[Path], prefix: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda item: str(item.relative_to(root)).lower()):
            archive_name = zip_name_for(root, path, prefix)
            info = zipfile.ZipInfo(archive_name, FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a clean Blender-compatible IDVMI Tools release zip."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip path. Defaults to dist/IDVMI-Tools-X_Y_Z.zip.",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_ADDON_DIR_NAME,
        help="Top-level folder name inside the zip.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Package tracked working-tree files even when tracked changes exist.",
    )
    parser.add_argument(
        "--include-scripts",
        action="store_true",
        help="Include the scripts/ directory in the release zip.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be packaged without writing the zip.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    version = addon_version(root)
    version_text = format_version(version)
    archive_version_text = format_archive_version(version)
    output_path = args.output or root / "dist" / f"{DEFAULT_ADDON_DIR_NAME}-{archive_version_text}.zip"

    if not args.allow_dirty:
        ensure_clean_tree(root)

    files = [
        path
        for path in tracked_files(root)
        if should_package(root, path, include_scripts=args.include_scripts)
    ]
    if not files:
        raise SystemExit("No files selected for packaging.")

    if args.dry_run:
        print(f"Version: v{version_text}")
        print(f"Output: {output_path}")
        print(f"Prefix: {args.prefix}/")
        print(f"Files: {len(files)}")
        for path in files:
            print(zip_name_for(root, path, args.prefix))
        return 0

    write_zip(output_path, root, files, args.prefix)
    print(f"Packaged v{version_text}: {output_path}")
    print(f"Files: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
