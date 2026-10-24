"""Package skill/ into dist/japanese-speaking-coach.zip for upload to Claude.ai.

The zip contains a single top-level folder named after the skill (the `name`
field in SKILL.md), which is the layout Claude expects for uploaded skills.
Python bytecode caches are excluded.

Usage:
    python tools/package_skill.py
"""

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skill"
DIST = ROOT / "dist"


def skill_name() -> str:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"^name:\s*([a-z0-9-]+)\s*$", text, re.M)
    if not match:
        raise SystemExit("SKILL.md frontmatter has no valid `name`")
    return match.group(1)


def main() -> int:
    name = skill_name()
    DIST.mkdir(exist_ok=True)
    target = DIST / f"{name}.zip"
    files = sorted(p for p in SKILL_DIR.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, Path(name) / path.relative_to(SKILL_DIR))
    print(f"wrote {target} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
