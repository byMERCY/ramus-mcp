"""The notes for a GitHub release: the version's section of CHANGELOG.md, and a link to the
Russian one. The release workflow runs it on the tag it was pushed with:

    python tools/release_notes.py v1.1.1 notes.md
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/byMERCY/ramus-mcp"


def notes(tag: str, changelog: str) -> str:
    version = tag[1:] if tag.startswith("v") else tag
    found = re.search(rf"^## {re.escape(version)}(?: |$).*?\n(.*?)(?=^## |\Z)", changelog,
                      re.M | re.S)
    if found is None or not found.group(1).strip():
        raise ValueError(f"CHANGELOG.md has no section for {version}.")
    return f"{found.group(1).strip()}\n\n[По-русски]({REPO}/blob/{tag}/CHANGELOG.ru.md)\n"


def main() -> None:
    tag, out = sys.argv[1], sys.argv[2]
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
        text = notes(tag, fh.read())
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
