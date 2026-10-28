"""Bump the last digit of AegisTriage's version everywhere it lives.

    python -m scripts.bump_version          # 1.0.0 -> 1.0.1
    python -m scripts.bump_version minor    # 1.0.7 -> 1.1.0

Rolls over like the sibling projects: 1.0.9 -> 1.1.0, 1.9.9 -> 2.0.0.
Commit version.py, web/package.json and web/package-lock.json together.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_PY = os.path.join(ROOT, "version.py")
PACKAGE_FILES = [os.path.join(ROOT, "web", "package.json"), os.path.join(ROOT, "web", "package-lock.json")]


def next_version(current: str, part: str = "patch") -> str:
    major, minor, patch = (int(x) for x in current.split("."))
    if part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    if patch > 9:
        minor, patch = minor + 1, 0
    if minor > 9:
        major, minor = major + 1, 0
    return f"{major}.{minor}.{patch}"


def main(argv: list[str]) -> str:
    part = argv[1] if len(argv) > 1 else "patch"
    src = open(VERSION_PY).read()
    current = re.search(r'__version__ = "([\d.]+)"', src).group(1)
    new = next_version(current, part)
    open(VERSION_PY, "w").write(src.replace(f'"{current}"', f'"{new}"'))
    for path in PACKAGE_FILES:
        if not os.path.exists(path):
            continue
        data = json.load(open(path))
        data["version"] = new
        if path.endswith("package-lock.json") and "" in data.get("packages", {}):
            data["packages"][""]["version"] = new
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    print(f"{current} -> {new}")
    return new


if __name__ == "__main__":
    main(sys.argv)
