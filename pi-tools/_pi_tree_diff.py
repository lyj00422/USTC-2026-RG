"""Print a normalised hash per file for a runtime tree, to diff repo vs Pi.

Run locally AND on the Pi (via _pi_run_file.py); the two outputs are meant to
be diffed directly, so paths are printed relative to the root each time.

Why normalised: the repo is a Windows checkout (CRLF) and the Pi's copies are
LF, so a plain `md5sum` reports every single file as different and hides the
handful that really are.  Line endings are stripped before hashing for exactly
that reason -- and that is also what the push tool does on the way over, so the
two agree about what "the same file" means.

`data/` is skipped on purpose: it holds the action packages and the captured
telemetry, which are field data and are NOT supposed to match the repo.  Same
for the venv, backups, dist and logs.

Usage:
    python _pi_tree_diff.py <root> [<subdir> ...]
"""
import hashlib
import pathlib
import sys

SKIP_DIRS = {
    ".venv", "venv", "backups", "dist", "data", "logs",
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache", "node_modules",
}
EXTS = {
    ".py", ".yaml", ".yml", ".json", ".sh", ".md", ".bat", ".txt",
    ".ini", ".cfg", ".toml", ".rules", ".service",
}


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    only = [sys.argv[2]] if len(sys.argv) > 2 else None
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in EXTS:
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if only and not rel.as_posix().startswith(only):
            continue
        data = path.read_bytes().replace(b"\r\n", b"\n")
        print(hashlib.md5(data).hexdigest(), rel.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
