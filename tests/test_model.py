import os
import tempfile
import unittest

import _paths  # noqa: F401  (puts src/ on the path)
import rsf_builder as fx

from ramus_rsf import RsfModel


class TinyModel(unittest.TestCase):
    """The fixture is a version-2 style file whose attribute ids differ from the real ones, with
    a deleted box and arrow in it - so everything here also proves lookups are by name and
    deleted elements stay out."""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        path = os.path.join(cls._dir.name, "tiny.rsf")
        fx.tiny_model(path)
        cls.model = RsfModel(path)
        cls.diagrams = {d.node: d for d in cls.model.diagrams()}
        cls.acts = cls.model.activities()
        cls.a0 = cls.diagrams["A0"]
        cls.arrow = {a.sector_id: a for d in cls.diagrams.values() for a in d.arrows}

    @classmethod
    def tearDownClass(cls):
        cls.model.close()
        cls._dir.cleanup()

    # ------------------------------------------------------------------ boxes and numbering

    def test_names_come_from_the_attribute_the_qualifier_designates(self):
        self.assertEqual(self.acts[fx.TOP].name, "Сделать дело")
        self.assertEqual(self.acts[fx.C1].name, "Подготовить")

    def test_deleted_elements_are_left_out(self):
        self.assertNotIn(fx.GONE, self.acts)
        self.assertNotIn(38, self.arrow)

    def test_node_numbers_follow_idef0(self):
        self.assertEqual(
            {fx.TOP: "A0", fx.C1: "A1", fx.C2: "A2", fx.G1: "A11"},
            {i: self.acts[i].number for i in (fx.TOP, fx.C1, fx.C2, fx.G1)},
        )
        self.assertEqual(set(self.diagrams), {"A-0", "A0", "A1"})

    def test_siblings_are_ordered_through_a_deleted_one(self):
        # C2's "previous" is the deleted box, whose own previous is C1.
        self.assertEqual([a.element_id for a in self.a0.activities], [fx.C1, fx.C2])

    def test_the_top_box_is_drawn_on_the_context_diagram_of_the_base_function(self):
        context = self.diagrams["A-0"]
        self.assertEqual(context.parent_id, fx.BASE)
        self.assertEqual([a.element_id for a in context.activities], [fx.TOP])
        self.assertEqual([a.sector_id for a in context.arrows], [39])

    def test_the_largest_diagram_comes_first(self):
        self.assertEqual(self.model.diagrams()[0].node, "A0")

    def test_box_look_is_read_by_attribute_name(self):
        c1 = self.acts[fx.C1]
        self.assertEqual((c1.fill, c1.color, c1.font_size, c1.type), ("#00ff00", "#000000", 10.0, 1))
        self.assertIsNone(self.acts[fx.C2].fill)

    # ------------------------------------------------------------------------------- arrows

    def test_an_end_on_a_box_side_carries_the_icom_role_of_that_side(self):
        a = self.arrow[30]
        self.assertEqual((a.start.kind, a.start.side, a.start.role), ("frame", "left", "input"))
        self.assertEqual(
            (a.end.kind, a.end.activity_id, a.end.side, a.end.role), ("activity", fx.C1, "left", "input")
        )
        out = self.arrow[31].start
        self.assertEqual((out.side, out.role), ("right", "output"))
        control = self.arrow[34].end
        self.assertEqual((control.side, control.role), ("top", "control"))

    def test_a_node_shared_with_other_segments_is_a_junction(self):
        self.assertEqual(self.arrow[31].end.kind, "junction")
        self.assertEqual(self.arrow[31].end.node, 110)

    def test_a_missing_border_row_means_the_end_is_open(self):
        self.assertEqual(self.arrow[35].end.kind, "open")

    def test_segments_that_meet_at_a_junction_are_one_flow(self):
        self.assertEqual({self.arrow[s].flow for s in (31, 32, 33)}, {self.arrow[31].flow})
        self.assertEqual(len({self.arrow[s].flow for s in (30, 31, 34, 35)}), 4)

    def test_the_route_is_in_stored_order(self):
        self.assertEqual(self.arrow[32].points, [(260.0, 100.0), (260.0, 150.0), (300.0, 150.0)])
        self.assertEqual(self.arrow[32].geometry, "stored")

    def test_the_label_and_where_its_zigzag_joins_the_line(self):
        label = self.arrow[30].label
        self.assertEqual((label.x, label.y, label.width, label.height), (30.0, 60.0, 40.0, 9.8))
        self.assertEqual(label.tilde_pos, 0.5)
        self.assertIsNone(self.arrow[34].label.tilde_pos)
        self.assertIsNone(self.arrow[31].label)  # SHOW_TEXT = 0

    def test_a_stream_with_no_name_is_labelled_by_the_catalog_items_attached(self):
        self.assertEqual(self.arrow[34].name, "Нормы;\nПравила")

    def test_an_alternative_text_beats_the_stream_name(self):
        self.assertEqual(self.arrow[35].name, "другое имя")
        self.assertEqual(self.arrow[30].name, "данные")

    def test_colour_and_width_come_from_the_sector_style(self):
        self.assertEqual((self.arrow[30].color, self.arrow[30].width), ("#0000ff", 1.5))
        self.assertEqual((self.arrow[31].color, self.arrow[31].width), ("#000000", 0.5))
        self.assertEqual(self.arrow[30].font_size, 10.0)

    def test_an_undrawn_inherited_segment_gets_a_short_stand_in_from_the_frame(self):
        a = self.arrow[36]
        self.assertEqual(a.geometry, "stub")
        self.assertEqual(a.points, [(7.0, 200.0), (43.0, 200.0)])  # inward from the left edge
        self.assertEqual((a.start.kind, a.end.kind), ("frame", "open"))

    # --------------------------------------------------------------------- frame and texts

    def test_the_frame_takes_the_right_edge_from_the_arrows_and_the_bottom_from_the_version(self):
        self.assertEqual(self.a0.frame, (7.0, 7.0, 793.0, 437.0))

    def test_free_text_comes_from_the_diagram_blob(self):
        (text,) = self.a0.texts
        self.assertEqual((text.text, text.color, text.font_size), ("Цель: проверка", "#ff0000", 10.0))
        self.assertEqual((text.x, text.y, text.width, text.height), (20.0, 300.0, 200.0, 40.0))
        self.assertEqual(self.diagrams["A1"].texts, [])

    # --------------------------------------------------------------------------- tunnels

    def test_a_tunnel_is_kept_where_the_arrow_stops_at_a_box_that_has_a_decomposition(self):
        self.assertEqual(self.arrow[30].end.tunnel, "soft")

    def test_a_tunnel_is_kept_on_a_frame_below_the_top_diagrams(self):
        self.assertEqual(self.arrow[37].start.tunnel, "hard")

    def test_a_tunnel_is_dropped_when_the_arrow_continues_on_the_other_level(self):
        # sector 31 starts at C1, and sector 40 on C1's own diagram carries the same node.
        self.assertIsNone(self.arrow[31].start.tunnel)
        self.assertIsNone(self.arrow[40].end.tunnel)

    def test_a_tunnel_is_dropped_on_a_box_with_nothing_inside(self):
        self.assertIsNone(self.arrow[37].end.tunnel)  # G1 has no decomposition

    def test_a_tunnel_is_dropped_on_the_frame_of_the_top_diagrams(self):
        self.assertIsNone(self.arrow[33].end.tunnel)


@unittest.skipUnless(_paths.MODEL_EXAMPLE, "Ramus sample model not installed")
class RamusSampleVersion1(unittest.TestCase):
    """The shipped samples are version-1 files: routes inline in the diagram blobs."""

    @classmethod
    def setUpClass(cls):
        cls.model = RsfModel(_paths.MODEL_EXAMPLE)
        cls.diagrams = {d.node: d for d in cls.model.diagrams()}
        cls.acts = cls.model.activities()

    @classmethod
    def tearDownClass(cls):
        cls.model.close()

    def test_the_sheets_and_numbers_of_the_model(self):
        self.assertEqual(
            set(self.diagrams), {"A-0", "A0", "A1", "A2", "A3", "A11", "A12", "A121", "A122"}
        )
        self.assertEqual(self.diagrams["A0"].parent_name, "Деятельность предприятия")
        self.assertEqual(
            [a.number for a in self.diagrams["A0"].activities], ["A1", "A2", "A3"]
        )

    def test_every_box_has_a_name(self):
        self.assertTrue(self.acts)
        self.assertTrue(all(a.name for a in self.acts.values()))

    def test_every_drawn_arrow_has_a_stored_route(self):
        arrows = [a for d in self.diagrams.values() for a in d.arrows]
        self.assertGreater(len(arrows), 200)
        self.assertTrue(all(a.has_route and a.geometry == "stored" for a in arrows))

    def test_every_end_on_a_box_lies_on_that_side_of_that_box(self):
        eps = 0.51
        for d in self.diagrams.values():
            for a in d.arrows:
                for end, (x, y) in ((a.start, a.points[0]), (a.end, a.points[-1])):
                    if end.kind != "activity":
                        continue
                    b = self.acts[end.activity_id]
                    if end.side == "right":
                        ok = abs(x - (b.x + b.width)) < eps and b.y - eps <= y <= b.y + b.height + eps
                    elif end.side == "left":
                        ok = abs(x - b.x) < eps and b.y - eps <= y <= b.y + b.height + eps
                    elif end.side == "top":
                        ok = abs(y - b.y) < eps and b.x - eps <= x <= b.x + b.width + eps
                    else:
                        ok = abs(y - (b.y + b.height)) < eps and b.x - eps <= x <= b.x + b.width + eps
                    self.assertTrue(ok, (d.node, a.sector_id, end.side, (x, y), (b.x, b.y, b.width, b.height)))

    def test_every_end_on_the_frame_lies_on_that_edge_of_it(self):
        for d in self.diagrams.values():
            left, top, right, bottom = d.frame
            edge = {"left": ("x", left), "right": ("x", right), "top": ("y", top), "bottom": ("y", bottom)}
            for a in d.arrows:
                for end, (x, y) in ((a.start, a.points[0]), (a.end, a.points[-1])):
                    if end.kind == "frame":
                        axis, want = edge[end.side]
                        self.assertAlmostEqual(x if axis == "x" else y, want, delta=0.51)

    def test_the_older_page_is_shorter(self):
        self.assertEqual(self.diagrams["A0"].frame, (7.0, 7.0, 793.0, 419.4))

    def test_a_stream_with_no_name_is_labelled_by_the_catalog_items_attached(self):
        names = {a.name for a in self.diagrams["A0"].arrows}
        self.assertIn("Транспортные средства;\nПроизводственное оборудование", names)

    def test_the_context_diagram_keeps_its_free_text(self):
        texts = [t.text for t in self.diagrams["A-0"].texts]
        self.assertTrue(any(t.startswith("Точка зрения") for t in texts))

    def test_a_flow_that_forks_is_one_flow(self):
        flows = {}
        for a in self.diagrams["A0"].arrows:
            flows.setdefault(a.flow, []).append(a)
        forked = [f for f in flows.values() if len(f) > 1]
        self.assertTrue(forked)
        self.assertTrue(all(len({a.name for a in f if a.name}) <= 1 for f in forked))


@unittest.skipUnless(_paths.ENTERPRISE, "Ramus sample model not installed")
class RamusSampleEnglish(unittest.TestCase):
    def test_the_english_sample_reads_the_same_way(self):
        with RsfModel(_paths.ENTERPRISE) as model:
            diagrams = {d.node: d for d in model.diagrams()}
            self.assertIn("A0", diagrams)
            self.assertTrue(all(a.name for a in model.activities().values()))
            self.assertTrue(diagrams["A0"].arrows)


if __name__ == "__main__":
    unittest.main()
