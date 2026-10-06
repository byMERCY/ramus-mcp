import asyncio
import json
import os
import tomllib
import unittest

import _paths  # noqa: F401  (puts src/ on the path)

ROOT = os.path.dirname(_paths.SRC)

try:
    import server
except ImportError:  # pragma: no cover - mcp not installed
    server = None


class Manifest(unittest.TestCase):
    """The Claude Desktop extension's manifest says what the project is."""

    def setUp(self):
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as fh:
            self.manifest = json.load(fh)

    def test_its_version_is_the_project_s(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            self.assertEqual(self.manifest["version"], tomllib.load(fh)["project"]["version"])

    @unittest.skipIf(server is None, "mcp package not installed")
    def test_it_lists_exactly_the_server_s_tools(self):
        served = {t.name for t in asyncio.run(server.mcp.list_tools())}
        self.assertEqual({t["name"] for t in self.manifest["tools"]}, served)

    def test_what_it_points_at_is_there(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, self.manifest["server"]["entry_point"])))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, self.manifest["icon"])))
        self.assertIn("RAMUS_MODELS_DIR", self.manifest["server"]["mcp_config"]["env"])
