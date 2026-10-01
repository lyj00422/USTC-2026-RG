"""Print `<md5>  <relpath>` for every file under a tree, for a byte-exact diff.

Run ON the Pi via _pi_run_file.py; the output is meant to be diffed against the
same listing produced locally (see _pi_tree_diff.py for why: normalising line
endings is what makes "the same file" mean the same thing on both sides).

Usage:
    python _pi_hash_tree.py <root>
"""
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    blob = path.read_bytes().replace(b"\r\n", b"\n")
    print(hashlib.md5(blob).hexdigest(), path.relative_to(root).as_posix())
