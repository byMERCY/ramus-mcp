import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest

import _paths  # noqa: F401  (puts src/ on the path)
import rsf_builder as fx

try:
    import server
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - the mcp package is a requirement
    server = None


def _reset():
    server._Open.editor = None
    server._Open.model = None
    server._Open.path = None
    server._Open.diagrams = []
    server._Open.activities = {}


@unittest.skipIf(server is None, "mcp package not installed")
class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls._dir.name, "tiny.rsf")
        fx.tiny_model(cls.path)

    @classmethod
    def tearDownClass(cls):
        _reset()
        cls._dir.cleanup()

    def setUp(self):
        _reset()

    def test_nothing_works_before_a_model_is_opened(self):
        for call in (server.list_diagrams, server.get_function_tree, server.get_diagram,
                     server.render_diagram):
            with self.assertRaisesRegex(ValueError, "No model is open"):
                call()
        self.assertEqual(server.current_model(), {"open": False})

    def test_a_missing_file_is_reported(self):
        with self.assertRaisesRegex(ValueError, "not a file"):
            server.open_model(os.path.join(self._dir.name, "nope.rsf"))

    def test_open_model_summarises_the_sheets(self):
        info = server.open_model(self.path)
        self.assertEqual(info["diagrams"], 3)
        self.assertEqual(info["nodes"], ["A0", "A1", "A-0"])
        self.assertEqual(info["largest"][0]["node"], "A0")
        self.assertEqual(server.current_model(),
                         {"open": True, "path": self.path, "diagrams": 3, "unsaved_changes": False})

    def test_list_diagrams_gives_node_and_sizes(self):
        server.open_model(self.path)
        rows = {r["node"]: r for r in server.list_diagrams()}
        self.assertEqual((rows["A0"]["activities"], rows["A0"]["arrows"]), (2, 7))
        self.assertEqual(rows["A-0"]["parent"], "Сделать дело")

    def test_a_diagram_is_found_by_node_in_any_case_or_by_index(self):
        server.open_model(self.path)
        self.assertEqual(server.get_diagram(node="a0")["node"], "A0")
        self.assertEqual(server.get_diagram(node=" A-0 ")["node"], "A-0")
        self.assertEqual(server.get_diagram(index=0)["node"], "A0")
        self.assertEqual(server.get_diagram(index=2)["index"], 2)

    def test_an_unknown_diagram_says_what_there_is(self):
        server.open_model(self.path)
        with self.assertRaisesRegex(ValueError, "A-0, A0, A1"):
            server.get_diagram(node="A9")
        with self.assertRaisesRegex(ValueError, "No diagram #7"):
            server.get_diagram(index=7)

    def test_get_diagram_reports_boxes_flows_and_texts(self):
        server.open_model(self.path)
        d = server.get_diagram(node="A0")
        self.assertEqual([a["number"] for a in d["activities"]], ["A1", "A2"])
        self.assertEqual(d["frame"], {"left": 7.0, "top": 7.0, "right": 793.0, "bottom": 437.0})
        self.assertEqual([t["text"] for t in d["texts"]], ["Цель: проверка"])
        self.assertNotIn("arrows", d)

        # The flow that enters through the frame and ends on box A1.
        entry = next(f for f in d["flows"] if f["name"] == "данные" and f["to"][0]["kind"] == "activity")
        self.assertEqual([e["kind"] for e in entry["from"]], ["frame"])
        self.assertEqual((entry["from"][0]["side"], entry["from"][0]["role"]), ("left", "input"))
        self.assertEqual(entry["to"][0]["number"], "A1")
        self.assertEqual(entry["to"][0]["role"], "input")

    def test_a_flow_that_forks_is_reported_once_with_all_its_ends(self):
        server.open_model(self.path)
        flow = next(f for f in server.get_diagram(node="A0")["flows"] if f["name"] == "результат")
        self.assertEqual([(e["number"], e["role"]) for e in flow["from"]], [("A1", "output")])
        self.assertEqual(
            sorted((e["kind"], e.get("role")) for e in flow["to"]),
            [("activity", "input"), ("frame", "output")],
        )

    def test_a_stream_named_by_its_catalog_items_and_an_alternative_text_show_in_flows(self):
        server.open_model(self.path)
        names = {f["name"] for f in server.get_diagram(node="A0")["flows"]}
        self.assertIn("Нормы;\nПравила", names)
        self.assertIn("другое имя", names)

    def test_tunnels_are_reported_on_the_end_that_has_one(self):
        server.open_model(self.path)
        flows = server.get_diagram(node="A0")["flows"]
        flow = next(f for f in flows if f["name"] == "данные" and f["to"][0]["kind"] == "activity")
        self.assertEqual(flow["to"][0]["tunnel"], "soft")

    def test_routes_are_only_included_on_request(self):
        server.open_model(self.path)
        d = server.get_diagram(node="A0", include_routes=True)
        row = next(a for a in d["arrows"] if a["id"] == 32)
        self.assertEqual(row["route"], [[260.0, 100.0], [260.0, 150.0], [300.0, 150.0]])
        self.assertEqual(row["from"]["kind"], "junction")
        self.assertEqual(row["to"]["number"], "A2")
        self.assertEqual(next(a for a in d["arrows"] if a["id"] == 30)["color"], "#0000ff")
        self.assertEqual(next(a for a in d["arrows"] if a["id"] == 36)["geometry"], "stub")

    def test_the_function_tree_nests_by_decomposition_in_sheet_order(self):
        server.open_model(self.path)
        (top,) = server.get_function_tree()
        self.assertEqual((top["number"], top["name"]), ("A0", "Сделать дело"))
        self.assertEqual([c["number"] for c in top["children"]], ["A1", "A2"])
        self.assertEqual([g["number"] for g in top["children"][0]["children"]], ["A11"])

    def test_render_returns_a_png_image(self):
        server.open_model(self.path)
        image = server.render_diagram(node="A0")
        self.assertTrue(image.data.startswith(b"\x89PNG"))
        self.assertEqual(image.to_image_content().mimeType, "image/png")

    def test_the_svg_text_is_an_svg(self):
        server.open_model(self.path)
        svg = server.render_diagram_svg_text(node="A1")
        self.assertTrue(svg.lstrip().startswith("<?xml"))
        self.assertIn("<svg", svg)

    def test_opening_another_model_replaces_the_first(self):
        server.open_model(self.path)
        second = os.path.join(self._dir.name, "second.rsf")
        fx.tiny_model(second)
        server.open_model(second)
        self.assertEqual(server.current_model()["path"], second)


@unittest.skipIf(server is None, "mcp package not installed")
class Editing(unittest.TestCase):
    """The hands, called as the tools are: by IDEF0 number, seen at once, written on save."""

    def setUp(self):
        _reset()
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "tiny.rsf")
        fx.tiny_model(self.path)
        server.open_model(self.path)

    def tearDown(self):
        _reset()
        self._dir.cleanup()

    def test_a_change_is_seen_at_once_and_not_written_until_saved(self):
        with open(self.path, "rb") as fh:
            original = fh.read()
        server.rename_activity("A1", "Подготовить всё")
        names = [a["name"] for a in server.get_diagram(node="A0")["activities"]]
        self.assertIn("Подготовить всё", names)
        self.assertTrue(server.current_model()["unsaved_changes"])
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), original)

    def test_add_a_box_and_an_arrow_by_number_then_save_as(self):
        box = server.add_activity("A0", "Проверить")
        self.assertEqual(box["number"], "A3")
        arrow = server.add_arrow("A0", {"activity": "A2"}, {"activity": "A3", "role": "input"},
                                 "результат проверки")
        self.assertEqual(arrow["to"]["number"], "A3")
        flows = server.get_diagram(node="A0")["flows"]
        self.assertTrue(any(f["name"] == "результат проверки" for f in flows))
        out = os.path.join(self._dir.name, "edited.rsf")
        saved = server.save_model(out)
        self.assertEqual(saved["saved"], os.path.abspath(out))
        self.assertEqual(server.current_model()["path"], os.path.abspath(out))
        self.assertFalse(server.current_model()["unsaved_changes"])
        from ramus_rsf import RsfModel
        reread = {d.node: d for d in RsfModel(out).diagrams()}["A0"]
        self.assertEqual([a.number for a in reread.activities], ["A1", "A2", "A3"])

    def test_saving_over_the_original_keeps_a_backup(self):
        server.rename_flow(fx.S_DATA, "сведения")
        result = server.save_model()
        self.assertTrue(os.path.isfile(result["backup"]))

    def test_switching_models_never_drops_or_writes_changes_silently(self):
        server.rename_activity("A2", "Другое")
        with self.assertRaisesRegex(ValueError, "unsaved changes"):
            server.open_model(self.path)
        server.open_model(self.path, discard_unsaved=True)
        self.assertFalse(server.current_model()["unsaved_changes"])

    def test_flows_carry_their_stream_id_for_the_editing_tools(self):
        flows = server.get_diagram(node="A0")["flows"]
        self.assertTrue(all("stream" in f for f in flows))
        self.assertTrue(all(f["segments"] for f in flows))

    def test_move_a_box_by_number_and_its_arrows_follow(self):
        result = server.move_activity("A2", y=240)
        self.assertEqual(result["number"], "A2")
        box = next(a for a in server.get_diagram(node="A0")["activities"] if a["number"] == "A2")
        self.assertEqual(box["y"], 240.0)
        self.assertTrue(result["rerouted"])

    def test_delete_a_box_and_the_next_one_moves_up(self):
        server.add_activity("A0", "Проверить")
        result = server.delete_activity("A2")
        self.assertEqual([a["number"] for a in result["removed_activities"]], ["A2"])
        numbers = [a["number"] for a in server.get_diagram(node="A0")["activities"]]
        self.assertEqual(numbers, ["A1", "A2"])
        self.assertEqual(server.get_function_tree()[0]["children"][1]["name"], "Проверить")

    def test_delete_an_arrow_by_its_segment_id(self):
        flow = next(f for f in server.get_diagram(node="A0")["flows"] if f["name"] == "результат")
        result = server.delete_arrow(flow["segments"][0])
        self.assertTrue(result["removed_arrows"])
        names = [f["name"] for f in server.get_diagram(node="A0")["flows"]]
        self.assertNotIn("результат", names)

    def test_branch_off_an_arrow_by_its_segment_id(self):
        flow = next(f for f in server.get_diagram(node="A0")["flows"] if f["stream"] == fx.S_DATA)
        result = server.add_arrow("A0", {"arrow": flow["segments"][0]},
                                  {"activity": "A2", "role": "control"})
        self.assertIn("fork", result)
        forked = next(f for f in server.get_diagram(node="A0")["flows"] if f["stream"] == fx.S_DATA)
        self.assertIn("A2", [e.get("number") for e in forked["to"]])

    def test_check_the_model_then_join_what_it_says_and_check_again(self):
        report = server.check_model()
        self.assertGreater(report["errors"], 0)
        self.assertEqual(report["errors"] + report["warnings"], len(report["findings"]))
        (unjoined,) = [f for f in report["findings"] if f["rule"] == "not_joined"]
        self.assertEqual(unjoined["fix"], "join_levels(first=30, second=37)")
        server.join_levels(30, 37)
        self.assertFalse([f for f in server.check_model()["findings"] if f["rule"] == "not_joined"])
        self.assertTrue(server.current_model()["unsaved_changes"])

    def test_each_edit_says_what_it_broke_and_what_it_settled(self):
        box = server.add_activity("A0", "Проверить")
        new = {(f["rule"], f.get("activity")) for f in box["idef0"]["new"]}
        self.assertTrue({("needs_control", "A3"), ("needs_output", "A3")} <= new, new)
        arrow = server.add_arrow("A0", {"frame": "control"}, {"activity": "A3", "role": "control"},
                                 "регламент")
        self.assertGreaterEqual(arrow["idef0"]["resolved"], 1)
        self.assertNotIn("needs_control", {f["rule"] for f in arrow["idef0"]["new"]})
        self.assertTrue({"errors", "warnings", "new", "resolved"} <= set(arrow["idef0"]))

    def test_check_one_sheet_and_an_unknown_one(self):
        report = server.check_model("A1")
        self.assertEqual(report["checked"], "A1")
        self.assertEqual({f["sheet"] for f in report["findings"]}, {"A1"})
        with self.assertRaisesRegex(ValueError, "No sheet 'A9'"):
            server.check_model("A9")

    def test_tidy_a_sheet_and_its_names_by_number(self):
        result = server.tidy_sheet("A0")
        self.assertEqual(result["sheet"], "A0")
        self.assertIn("rerouted", result)
        self.assertIn("labels_moved", server.tidy_labels("A0"))
        self.assertTrue(server.current_model()["unsaved_changes"])


@unittest.skipIf(server is None, "mcp package not installed")
class OverStdio(unittest.TestCase):
    """The same thing through the real protocol: spawn the server and talk to it."""

    def test_a_client_can_open_a_model_read_a_diagram_and_look_at_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tiny.rsf")
            fx.tiny_model(path)

            async def session_run():
                params = StdioServerParameters(
                    command=sys.executable, args=[os.path.join(_paths.SRC, "server.py")]
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        opened = await session.call_tool("open_model", {"path": path})
                        diagram = await session.call_tool("get_diagram", {"node": "A0"})
                        picture = await session.call_tool("render_diagram", {"node": "A0"})
                        missing = await session.call_tool("get_diagram", {"node": "A9"})
                        return tools, opened, diagram, picture, missing

            tools, opened, diagram, picture, missing = asyncio.run(
                asyncio.wait_for(session_run(), timeout=60)
            )

        self.assertEqual(
            {t.name for t in tools.tools},
            {"open_model", "current_model", "list_diagrams", "get_function_tree",
             "get_diagram", "render_diagram", "render_diagram_svg_text",
             "rename_activity", "rename_flow", "add_activity", "add_arrow", "move_activity",
             "delete_activity", "delete_arrow", "tidy_sheet", "tidy_labels", "save_model",
             "check_model", "join_levels"},
        )
        self.assertFalse(opened.isError)
        data = json.loads(diagram.content[0].text)
        self.assertEqual([a["number"] for a in data["activities"]], ["A1", "A2"])
        self.assertTrue(data["flows"])

        (image,) = picture.content
        self.assertEqual(image.mimeType, "image/png")
        self.assertTrue(base64.b64decode(image.data).startswith(b"\x89PNG"))

        self.assertTrue(missing.isError)
        self.assertIn("A9", missing.content[0].text)


if __name__ == "__main__":
    unittest.main()
