"""Ask the real Ramus engine what it sees in a .rsf file - an independent check of what we write.

This is a development tool, not part of the server. It runs a headless Ramus engine that speaks
MCP (for example the ``ramus-mcp.jar`` built from ramus-next) read-only, opens the file in it,
and prints what that engine reports - or saves the picture it draws. If our writer got anything
wrong, the engine refuses the file, or reads back something other than what was meant.

Point it at the engine with two environment variables:

    RAMUS_JAVA     the java executable (Java 17+)
    RAMUS_MCP_JAR  the engine's jar

Usage:
    python tools/ramus_validate.py model.rsf                 # the activity tree, as Ramus reads it
    python tools/ramus_validate.py model.rsf <id> out.png    # Ramus's own drawing of a sheet

The engine was built without the Chart plugin some Ramus 3 files list; such a file is checked
through a copy whose plugin list leaves Chart out (its tables are empty in the files seen).
"""

import base64
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import zipfile

UNKNOWN_TO_ENGINE = ("Chart",)


def _engine():
    java, jar = os.environ.get("RAMUS_JAVA"), os.environ.get("RAMUS_MCP_JAR")
    if not java or not jar:
        sys.exit("Set RAMUS_JAVA and RAMUS_MCP_JAR to the Ramus engine to check against.")
    return java, jar


def for_engine(path, out_path):
    """A copy the engine can open: plugins it was built without are struck from the list."""
    with zipfile.ZipFile(path) as z, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as o:
        for info in z.infolist():
            data = z.read(info.filename)
            if info.filename == "data/application_metadata.xml":
                t = data.decode("utf-8")
                plugins = re.findall(r'<entry key="Plugin_(\d+)">([^<]*)</entry>', t)
                keep = [n for _, n in sorted(plugins, key=lambda p: int(p[0]))
                        if n not in UNKNOWN_TO_ENGINE]
                t = re.sub(r'<entry key="Plugin_\d+">[^<]*</entry>\n?', "", t)
                t = re.sub(r'<entry key="PluginCount">\d+</entry>',
                           f'<entry key="PluginCount">{len(keep)}</entry>', t)
                listing = "".join(f'<entry key="Plugin_{i}">{n}</entry>\n' for i, n in enumerate(keep))
                data = t.replace("</properties>", listing + "</properties>").encode("utf-8")
            o.writestr(info, data)
    return out_path


def ask(path, calls, timeout=90):
    """Open ``path`` in the engine and make the given tool calls; returns their results."""
    java, jar = _engine()
    with tempfile.TemporaryDirectory() as tmp:
        copy = for_engine(path, os.path.join(tmp, "check.rsf"))
        p = subprocess.Popen([java, "-Djava.awt.headless=true", "-Dstdout.encoding=UTF-8",
                              "-Dfile.encoding=UTF-8", "-jar", jar, copy, "--read-only"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(l) for l in p.stdout], daemon=True).start()

        def send(message):
            p.stdin.write((json.dumps(message) + "\n").encode())
            p.stdin.flush()

        def wait(i):
            while True:
                try:
                    line = lines.get(timeout=timeout)
                except queue.Empty:
                    err = p.stderr.read().decode("utf-8", "replace") if p.poll() is not None else ""
                    raise RuntimeError("the engine did not answer - it could not open the file?\n"
                                       + err[-2000:])
                m = json.loads(line.decode("utf-8"))
                if m.get("id") == i:
                    return m

        send({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "ramus-validate", "version": "1"}}})
        wait(0)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        results = []
        for i, (name, args) in enumerate(calls, start=1):
            send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                  "params": {"name": name, "arguments": args}})
            results.append(wait(i).get("result"))
        p.stdin.close()
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
        return results


def text(result):
    return "\n".join(c.get("text", "") for c in (result or {}).get("content", [])
                     if c.get("type") == "text")


def save_image(result, out_path):
    for c in (result or {}).get("content", []):
        if c.get("type") == "image":
            with open(out_path, "wb") as fh:
                fh.write(base64.b64decode(c["data"]))
            return out_path
    return None


if __name__ == "__main__":
    if len(sys.argv) == 2:
        (tree,) = ask(sys.argv[1], [("get_function_tree", {})])
        print(text(tree))
    elif len(sys.argv) == 4:
        (picture,) = ask(sys.argv[1], [("render_diagram", {"activity": int(sys.argv[2])})])
        print(save_image(picture, sys.argv[3]) or "the engine drew nothing")
    else:
        sys.exit(__doc__)
