#!/usr/bin/env python3
"""Static/runtime-import contract for the private vision worker."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "vision_worker" / "worker.py").read_text(encoding="utf-8")
tree = ast.parse(src)

imports=set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports.update(alias.name.split(".")[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imports.add(node.module.split(".")[0])

required={"base64","hashlib","io","boto3","requests"}
missing=required-imports
assert not missing, f"vision worker missing imports: {sorted(missing)}"

for symbol in ("boto3.client(", "hashlib.sha256(", "base64.b64encode(", "io.BytesIO("):
    assert symbol in src, symbol

print("OK: vision worker imports all runtime dependencies")
