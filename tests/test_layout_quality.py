import os
import tempfile
import unittest

import _paths  # noqa: F401  (puts src/ on the path)

import idef0_rules as rules
import layout_quality as lq
import router as rt
from layout_scenarios import SCENARIOS, _Build, olympiad, order
from model_editor import _diagonal, _even_slots
from rsf_document import RsfDocument

FRAME = (7.0, 7.0, 786.0, 430.0)
A = lq.BoxGeom(1, (100.0, 100.0, 120.0, 60.0), "A1")
B = lq.BoxGeom(2, (300.0, 200.0, 120.0, 60.0), "A2")


def _end(box=None, side=None):
    if box is None:
        return lq.EndInfo("frame", side=side)
    return lq.EndInfo("activity", box.id, side)


def _arrow(sector, points, start, end, flow=None, label=None, tilde=False):
    return lq.ArrowGeom(sector, sector if flow is None else flow, list(points), start, end,
                        label, tilde, f"arrow {sector}")


def _kinds(arrows, boxes=(A, B)):
    return lq.SheetGeometry(boxes, arrows, FRAME).assess().counts()


class Measuring(unittest.TestCase):

    def test_a_clean_box_to_box_arrow_has_no_faults(self):
        a = _arrow(1, [(220, 130), (260, 130), (260, 230), (300, 230)],
                   _end(A, "right"), _end(B, "left"))
        q = lq.SheetGeometry([A, B], [a], FRAME).assess()
        self.assertEqual(q.counts(), {})
        self.assertEqual(q.bends, 2)

    def test_crossings_are_counted_but_not_where_one_flow_forks(self):
        h = _arrow(1, [(7, 180), (300, 180)], _end(None, "left"), _end(B, "left"))
        v = _arrow(2, [(260, 7), (260, 300)], _end(None, "top"), _end(None, "bottom"))
        self.assertEqual(_kinds([h, v]).get("crossing"), 1)
        v.flow = h.flow
        self.assertNotIn("crossing", _kinds([h, v]))

    def test_running_on_top_of_another_arrow_is_an_overlap(self):
        a = _arrow(1, [(7, 180), (300, 180)], _end(None, "left"), _end(B, "left"))
        b = _arrow(2, [(30, 181), (250, 181)], _end(None, "left"), _end(None, "right"))
        self.assertEqual(_kinds([a, b]).get("overlap"), 1)

    def test_an_end_in_a_corner_or_on_the_number_is_a_fault(self):
        corner = _arrow(1, [(220, 105), (260, 105), (260, 230), (300, 230)],
                        _end(A, "right"), _end(B, "left"))
        self.assertEqual(_kinds([corner]), {"corner_end": 1})
        number = _arrow(1, [(220, 155), (260, 155), (260, 230), (300, 230)],
                        _end(A, "right"), _end(B, "left"))
        self.assertEqual(_kinds([number]), {"number_end": 1})

    def test_ends_crowded_on_one_side_are_a_fault(self):
        a = _arrow(1, [(7, 225), (300, 225)], _end(None, "left"), _end(B, "left"))
        b = _arrow(2, [(7, 232), (300, 232)], _end(None, "left"), _end(B, "left"))
        self.assertIn("crowded_ends", _kinds([a, b]))

    def test_a_long_way_round_is_a_detour(self):
        s_loop = _arrow(1, [(220, 130), (280, 130), (280, 180), (240, 180), (240, 230),
                            (300, 230)], _end(A, "right"), _end(B, "left"))
        q = lq.SheetGeometry([A, B], [s_loop], FRAME).assess()
        self.assertIn("detour", q.counts())
        self.assertGreater(q.detour, lq.DETOUR_NOTED)

    def test_feedback_into_a_control_goes_over_the_top(self):
        # From B's output back to A's top: under B is the wrong way round, over it is right.
        under = _arrow(1, [(420, 230), (440, 230), (440, 280), (160, 280), (160, 300),
                           (80, 300), (80, 80), (160, 80), (160, 100)],
                       _end(B, "right"), _end(A, "top"))
        self.assertIn("feedback_side", _kinds([under]))
        over = _arrow(1, [(420, 230), (440, 230), (440, 80), (160, 80), (160, 100)],
                      _end(B, "right"), _end(A, "top"))
        self.assertNotIn("feedback_side", _kinds([over]))

    def test_feedback_into_an_input_goes_under_the_bottom(self):
        over = _arrow(1, [(420, 230), (440, 230), (440, 80), (80, 80), (80, 130), (100, 130)],
                      _end(B, "right"), _end(A, "left"))
        self.assertIn("feedback_side", _kinds([over]))
        under = _arrow(1, [(420, 230), (440, 230), (440, 290), (80, 290), (80, 130),
                           (100, 130)], _end(B, "right"), _end(A, "left"))
        self.assertNotIn("feedback_side", _kinds([under]))

    def test_names_far_off_or_on_a_box_are_faults(self):
        route = [(220, 130), (260, 130), (260, 230), (300, 230)]
        far = _arrow(1, route, _end(A, "right"), _end(B, "left"), label=(500, 50, 40, 10))
        self.assertIn("label_far", _kinds([far]))
        tied = _arrow(1, route, _end(A, "right"), _end(B, "left"), label=(290, 160, 40, 10),
                      tilde=True)
        self.assertNotIn("label_far", _kinds([tied]))
        on_box = _arrow(1, route, _end(A, "right"), _end(B, "left"), label=(150, 120, 40, 10))
        self.assertIn("label_on_box", _kinds([on_box]))

    def test_a_route_through_a_box_and_boxes_on_each_other(self):
        a = _arrow(1, [(7, 230), (500, 230)], _end(None, "left"), _end(None, "right"))
        self.assertIn("through_box", _kinds([a]))
        c = lq.BoxGeom(3, (390.0, 220.0, 50.0, 50.0), "A3")
        self.assertIn("box_overlap", _kinds([], (A, B, c)))

    def test_boxes_go_down_the_diagonal_in_their_order(self):
        self.assertNotIn("off_diagonal", _kinds([], (A, B)))
        self.assertIn("off_diagonal", _kinds([], (B, A)))
        level = lq.BoxGeom(2, (300.0, 100.0, 120.0, 60.0), "A2")
        self.assertIn("off_diagonal", _kinds([], (A, level)))

    def test_a_segments_contribution_falls_when_its_crossing_goes(self):
        h = _arrow(1, [(7, 180), (300, 180)], _end(None, "left"), _end(B, "left"))
        v = _arrow(2, [(260, 7), (260, 300)], _end(None, "top"), _end(None, "bottom"))
        g = lq.SheetGeometry([A, B], [h, v], FRAME)
        crossing = g.contribution(2)
        g.arrows[2].points = [(560, 7), (560, 300)]
        self.assertLess(g.contribution(2), crossing)

    def test_least_length_counts_what_an_arrow_cannot_avoid(self):
        # Straight ahead: just the distance. Back the way it came: out and back again.
        self.assertEqual(lq.least_length((0, 0), rt.RIGHT, (100, 0), rt.RIGHT), 100)
        self.assertEqual(lq.least_length((0, 0), rt.RIGHT, (-100, 50), rt.RIGHT),
                         8 + 116 + 8 + 50)


class Attaching(unittest.TestCase):
    BOX = (100.0, 100.0, 120.0, 60.0)

    def test_the_middle_beats_a_corner_that_saves_a_little_length(self):
        # 148 is as near the bottom corner as an end may come; a route ending there instead of
        # in the middle would be 18 shorter - not enough to pay for it.
        options = dict(rt.attach_options(self.BOX, rt.SIDE_RIGHT, [], prefer=[148.0],
                                         corner=None, centre=rt.CENTRE))
        self.assertLess(options[130.0], options[148.0] - 18)

    def test_a_corner_or_the_number_is_dear_while_there_is_room(self):
        zone = ((140.0, 160.0),)
        options = rt.attach_options(self.BOX, rt.SIDE_RIGHT, [], prefer=[103.0, 155.0],
                                    corner=None, centre=rt.CENTRE, avoid=zone)
        self.assertTrue(all(112.0 <= c <= 148.0 for c, _ in options))
        for c, price in options:
            if c >= 140.0:
                self.assertGreater(price, rt.OUT_OF_PLACE)
        best = min(options, key=lambda o: o[1])[0]
        self.assertTrue(112.0 <= best < 140.0)

    def test_a_crowded_side_may_use_its_corners(self):
        taken = [112.0 + 6 * i for i in range(7)]
        options = rt.attach_options(self.BOX, rt.SIDE_LEFT, taken, corner=None,
                                    centre=rt.CENTRE)
        self.assertTrue(options)

    def test_even_slots_keep_off_the_number(self):
        self.assertEqual(_even_slots(self.BOX, rt.SIDE_LEFT, 1), [130.0])
        slots = _even_slots(self.BOX, rt.SIDE_RIGHT, 4, ((147.0, 160.0),))
        self.assertTrue(all(c < 147.0 for c in slots))
        self.assertEqual(len(slots), 4)

    def test_feedback_is_priced_the_wrong_way_round_only(self):
        price = rt.feedback_price((300.0, 200.0, 120.0, 60.0), over=True)
        self.assertEqual(price((440, 80), (160, 80)), 0.0)  # back to the left, above it
        self.assertEqual(price((440, 280), (160, 280)), rt.WRONG_WAY_ROUND)  # below it
        self.assertEqual(price((160, 280), (440, 280)), 0.0)  # going on to the right

    def test_a_route_through_a_box_is_counted(self):
        self.assertEqual(rt.through_boxes([(0, 130), (300, 130)], [self.BOX]), 1)
        self.assertEqual(rt.through_boxes([(0, 90), (300, 90)], [self.BOX]), 0)


class Undoing(unittest.TestCase):

    def test_a_restored_document_is_byte_for_byte_what_it_was(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = olympiad(os.path.join(tmp, "o.rsf"))
            before = ed.doc.to_bytes()
            saved = ed.doc.checkpoint()
            top = next(d for d in ed.snapshot().diagrams() if d.node == "A0")
            ed.add_activity(top.parent_id, "Наградить победителей")
            ed.doc.new_crosspoint()
            self.assertNotEqual(ed.doc.to_bytes(), before)
            ed.doc.restore(saved)
            self.assertEqual(ed.doc.to_bytes(), before)
            self.assertIsInstance(ed.doc, RsfDocument)


class Drawing(unittest.TestCase):
    """The layout as an agent gets it, building a sheet call by call."""

    def _top(self, ed):
        return next(d for d in ed.snapshot().diagrams() if d.node == "A0")

    def test_arrows_drawn_on_the_demo_keep_out_of_corners(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = lq.assess_diagram(self._top(olympiad(os.path.join(tmp, "o.rsf"))))
            self.assertFalse({"corner_end", "number_end", "through_box"} & set(q.counts()),
                             q.counts())

    def test_tidying_never_leaves_a_sheet_reading_worse(self):
        for name, build in SCENARIOS.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                ed = build(os.path.join(tmp, name + ".rsf"))
                top = self._top(ed)
                before = lq.assess_diagram(top).score
                result = ed.tidy_sheet(top.parent_id)
                after = lq.assess_diagram(self._top(ed)).score
                self.assertLessEqual(after, before)
                self.assertEqual(result["layout"]["after"], after)

    def test_tidying_that_finds_nothing_better_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = order(os.path.join(tmp, "o.rsf"))
            top = self._top(ed)
            ed.tidy_sheet(top.parent_id)
            tidied = ed.doc.to_bytes()
            result = ed.tidy_sheet(top.parent_id)
            if not result["rerouted"] and not result["labels_moved"]:
                self.assertEqual(ed.doc.to_bytes(), tidied)
            self.assertLessEqual(result["layout"]["after"], result["layout"]["before"])

    def test_the_report_names_the_fix_for_every_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = order(os.path.join(tmp, "o.rsf"))
            report = ed.layout_report(self._top(ed).parent_id)
            self.assertEqual(report["sheet"], "A0")
            self.assertTrue(report["worst"])
            for f in report["worst"]:
                self.assertIn(f["fix"], ("tidy_sheet", "tidy_labels", "move_activity"))



class Diagonal(unittest.TestCase):
    PAGE = (7.0, 7.0, 793.0, 437.0)

    def test_up_to_six_boxes_fill_the_page_down_the_diagonal(self):
        names = ["Собрать требования", "Спроектировать систему", "Написать код",
                 "Протестировать", "Внедрить", "Сопровождать"]
        for n in range(1, 7):
            with self.subTest(n=n):
                rects = _diagonal([(name, 10.0) for name in names[:n]], self.PAGE)
                self.assertEqual(len(rects), n)
                for x, y, w, h in rects:
                    self.assertTrue(self.PAGE[0] < x and x + w < self.PAGE[2])
                    self.assertTrue(self.PAGE[1] < y and y + h < self.PAGE[3])
                for (x0, y0, w, h), (x1, y1, _, _) in zip(rects, rects[1:]):
                    self.assertGreaterEqual(x1 - (x0 + w), 16.0)  # right of the one before
                    self.assertGreaterEqual(y1 - (y0 + h), 12.0)  # and below it
                widest = max(lq.text_width(word, 10.0) for name in names[:n]
                             for word in name.split())
                self.assertGreater(rects[0][2] - 10.0, widest)

    def test_boxes_added_before_any_arrow_are_spread_again_each_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _Build(os.path.join(tmp, "d.rsf"), "Сделать дело")
            b.context(["вход"], ["правило"], ["исполнитель"], ["результат"])
            first = b.ed.add_activity(b.top, "Начать")
            self.assertNotIn("relaid", first)
            second = b.ed.add_activity(b.top, "Продолжить")
            self.assertEqual([r["number"] for r in second["relaid"]], ["A1"])
            b.arrow({"activity": first["id"]}, {"activity": second["id"], "role": "input"},
                    "задел")
            third = b.ed.add_activity(b.top, "Закончить")
            self.assertNotIn("relaid", third)  # an arrow touches the boxes now: they stay


class LayingOut(unittest.TestCase):

    def _sheet(self, ed, node="A0"):
        return next(d for d in ed.snapshot().diagrams() if d.node == node)

    def _crowded(self, path):
        """Four boxes put by hand in a row along the top, drawn and forked."""
        b = _Build(path, "Провести олимпиаду")
        b.context(["заявки"], ["положение"], ["жюри"], ["итоги"])
        names = ["Подготовить задания", "Провести тур", "Проверить решения", "Подвести итоги"]
        a = {i + 1: b.ed.add_activity(b.top, n, x=40.0 + 150 * i, y=40.0, width=120.0,
                                      height=60.0)["id"] for i, n in enumerate(names)}
        b.arrow({"frame": "control"}, {"activity": a[1], "role": "control"}, "положение")
        b.arrow({"frame": "mechanism"}, {"activity": a[1], "role": "mechanism"}, "жюри")
        b.arrow({"frame": "input"}, {"activity": a[1], "role": "input"}, "заявки")
        for i in (1, 2, 3):
            b.arrow({"activity": a[i]}, {"activity": a[i + 1], "role": "input"}, f"итог {i}")
        b.fork("положение", {"activity": a[3], "role": "control"})
        b.fork("жюри", {"activity": a[2], "role": "mechanism"})
        b.fork("жюри", {"activity": a[4], "role": "mechanism"})
        b.arrow({"activity": a[4]}, {"frame": "output"}, "итоги")
        return b

    def test_a_crowded_sheet_comes_out_reading_better_with_its_arrows_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._crowded(os.path.join(tmp, "c.rsf"))
            sheet = self._sheet(b.ed)
            named = {a.sector_id: a.name for a in sheet.arrows if a.label is not None}
            findings = sorted((f.rule, f.sheet) for f in rules.check(b.ed.snapshot()))
            result = b.ed.layout_sheet(sheet.parent_id)
            self.assertLess(result["layout"]["after"], result["layout"]["before"])
            self.assertEqual(len(result["moved"]), 4)
            after = self._sheet(b.ed)
            self.assertEqual(lq.assess_diagram(after).score, result["layout"]["after"])
            # The same segments carry the same names, and the model breaks no new rule.
            still = {a.sector_id: a.name for a in after.arrows if a.label is not None}
            self.assertEqual(still, named)
            self.assertEqual(sorted((f.rule, f.sheet) for f in rules.check(b.ed.snapshot())),
                             findings)

    def test_forks_are_drawn_again_with_their_nodes_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._crowded(os.path.join(tmp, "c.rsf"))
            b.ed.layout_sheet(self._sheet(b.ed).parent_id)
            sheet = self._sheet(b.ed)
            meet = {}
            for a in sheet.arrows:
                for e, p in ((a.start, a.points[0]), (a.end, a.points[-1])):
                    if e.kind == "junction":
                        meet.setdefault(e.node, []).append(p)
            self.assertTrue(meet)
            for node, points in meet.items():
                self.assertEqual(len(points), 3, node)  # one in, two out
                self.assertEqual(len(set(points)), 1, node)  # all at one point

    def test_a_sheet_that_reads_well_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = olympiad(os.path.join(tmp, "o.rsf"))
            top = self._sheet(ed)
            ed.layout_sheet(top.parent_id)
            once = ed.doc.to_bytes()
            result = ed.layout_sheet(top.parent_id)
            if not result["moved"]:
                self.assertEqual(ed.doc.to_bytes(), once)
            self.assertLessEqual(result["layout"]["after"], result["layout"]["before"])


if __name__ == "__main__":
    unittest.main()
