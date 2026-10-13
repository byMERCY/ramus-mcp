import asyncio
import json
import os
import re
import sys
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


class ReleaseNotes(unittest.TestCase):
    """A release is described by its version's section of the changelog, in both languages."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        self.addCleanup(sys.path.remove, os.path.join(ROOT, "tools"))
        import release_notes
        self.notes = release_notes.notes
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            self.version = tomllib.load(fh)["project"]["version"]

    def test_the_version_has_its_section_in_both_changelogs(self):
        for name in ("CHANGELOG.md", "CHANGELOG.ru.md"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                self.assertRegex(fh.read(), rf"(?m)^## {re.escape(self.version)} ", name)

    def test_the_notes_are_that_section_alone_with_the_russian_one_linked(self):
        changelog = ("# Changelog\n\n## 1.2.0 — 2026-11-01\n\n### Added\n\n- A.\n\n"
                     "## 1.1.10 — 2026-10-20\n\n- B.\n\n## 1.1.1 — 2026-10-13\n\n- C.\n")
        text = self.notes("v1.1.1", changelog)
        self.assertTrue(text.startswith("- C.\n"))
        self.assertIn("/blob/v1.1.1/CHANGELOG.ru.md", text)
        self.assertNotIn("- A.", self.notes("v1.1.10", changelog))
        self.assertIn("### Added", self.notes("v1.2.0", changelog))
        with self.assertRaisesRegex(ValueError, "no section for 1.3.0"):
            self.notes("v1.3.0", changelog)
