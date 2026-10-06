"""Build the Claude Desktop extension: dist/ramus-mcp-<version>.mcpb.

An .mcpb is a ZIP with manifest.json at its root. This one is a uv bundle: it carries the
server's source with pyproject.toml (and uv.lock, which pins the dependencies), and Claude
Desktop installs Python and the dependencies itself when the extension is first started.

Before packing, the manifest is checked against the project: its version must be
pyproject.toml's, and it must list exactly the tools the server has. With Node installed,
``npx @anthropic-ai/mcpb validate manifest.json`` also checks it against the official schema.

Usage:  python tools/build_mcpb.py
"""

import asyncio
import glob
import json
import os
import sys
import tomllib
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP_FILES = ("manifest.json", "icon.png", "pyproject.toml", "uv.lock", "README.md", "LICENSE")


def check(manifest: dict) -> None:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        version = tomllib.load(fh)["project"]["version"]
    if manifest["version"] != version:
        sys.exit(f"manifest.json says version {manifest['version']}, pyproject.toml {version}.")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import server  # noqa: E402  (the server, to ask it which tools it has)

    served = {t.name for t in asyncio.run(server.mcp.list_tools())}
    listed = {t["name"] for t in manifest.get("tools", [])}
    if served != listed:
        sys.exit(f"manifest.json lists other tools than the server has: missing "
                 f"{sorted(served - listed)}, extra {sorted(listed - served)}.")
    if not os.path.isfile(os.path.join(ROOT, manifest["server"]["entry_point"])):
        sys.exit(f"The entry point {manifest['server']['entry_point']} is missing.")


def files() -> list:
    out = [f for f in TOP_FILES if os.path.isfile(os.path.join(ROOT, f))]
    out += sorted(os.path.relpath(p, ROOT).replace(os.sep, "/")
                  for p in glob.glob(os.path.join(ROOT, "src", "*.py")))
    return out


def main() -> None:
    with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    check(manifest)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    target = os.path.join(ROOT, "dist", f"{manifest['name']}-{manifest['version']}.mcpb")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files():
            z.write(os.path.join(ROOT, name), name)
    print(f"{target}  ({os.path.getsize(target) // 1024} KB, {len(files())} files)")


if __name__ == "__main__":
    main()
