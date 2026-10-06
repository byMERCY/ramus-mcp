import os
import tempfile
import unittest

import _paths  # noqa: F401  (puts src/ on the path)
import rsf_builder as fx

import idef0_rules as rules
from model_editor import EditError, ModelEditor
from ramus_rsf import Activity, Arrow, Diagram, End

SAMPLES = [p for p in (_paths.MODEL_EXAMPLE, _paths.ENTERPRISE) if p]


class _OnTiny(unittest.TestCase):
    """The fixture: A-0 holds A0 (TOP) with one output (39) that its sheet does not carry on.
    On A0: frame -> C1 input (30, a soft tunnel at C1); C1 output -> fork to C2 input and to the
    frame; frame -> C2 control (34); C2 output -> nothing (35); an inherited stub from the frame
    left (36). On A1 (C1's sheet): frame -> G1 input (37, the same flow as 30 but not joined to
    it) and G1 output -> frame (40, joined to 31's start)."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "tiny.rsf")
        fx.tiny_model(self.path)
        self.editor = ModelEditor(self.path)

    def tearDown(self):
        self._dir.cleanup()

    def found(self, sheet=None):
        return rules.check(self.editor.snapshot(), sheet)

    def rule(self, name, sheet=None):
        return [f for f in self.found(sheet) if f.rule == name]


class Syntax(_OnTiny):

    def test_a_box_with_no_control_is_an_error(self):
        missing = {(f.sheet, f.activity) for f in self.rule("needs_control")}
        self.assertEqual(missing, {("A-0", "A0"), ("A0", "A1"), ("A1", "A11")})
        self.assertTrue(all(f.severity == rules.ERROR for f in self.rule("needs_control")))

    def test_an_output_into_nowhere_still_counts_as_an_output(self):
        self.assertEqual(self.rule("needs_output"), [])

    def test_an_end_attached_to_nothing_is_an_error(self):
        (f,) = self.rule("dangling_end")
        self.assertEqual((f.sheet, f.segment, f.severity), ("A0", 35, rules.ERROR))

    def test_an_arrow_come_down_but_not_drawn_on_is_reported_with_its_fix(self):
        (f,) = self.rule("not_drawn")
        self.assertEqual((f.sheet, f.segment, f.flow), ("A0", 36, fx.S_DATA))
        self.assertIn('"frame": "input"', f.fix)

    def test_sheets_of_fewer_than_three_boxes_are_warned_about(self):
        counts = {f.sheet for f in self.rule("box_count")}
        self.assertEqual(counts, {"A0", "A1"})
        self.assertEqual(self.rule("context_one_box"), [])

    def test_one_sheet_can_be_checked_alone(self):
        self.assertEqual({f.sheet for f in self.found("a1")}, {"A1"})

    def test_findings_come_top_down_errors_first(self):
        found = self.found()
        order = ["A-0", "A0", "A1"]
        keys = [(order.index(f.sheet), f.severity != rules.ERROR) for f in found]
        self.assertEqual(keys, sorted(keys))


class Balance(_OnTiny):

    def test_an_arrow_with_no_continuation_on_the_other_level_is_unbalanced(self):
        segments = {(f.sheet, f.segment) for f in self.rule("unbalanced")}
        # 39: A0's output on A-0, not on A0's sheet; 33: leaves A0's sheet, not A0 on A-0;
        # 34: a control on A0's frame that A0 has not on A-0.
        self.assertTrue({("A-0", 39), ("A0", 34)} <= segments, segments)
        fix = next(f.fix for f in self.rule("unbalanced") if f.segment == 34)
        self.assertIn('sheet="A-0"', fix)
        self.assertIn('"role": "control"', fix)

    def test_a_joined_pair_is_balanced(self):
        hit = [f for f in self.found() if f.segment in (31, 40)]
        self.assertEqual(hit, [])

    def test_the_same_flow_on_both_levels_unjoined_is_one_finding_with_join_as_its_fix(self):
        (f,) = self.rule("not_joined")
        self.assertEqual(f.fix, "join_levels(first=30, second=37)")
        self.assertEqual((f.sheet, f.activity), ("A1", "A1"))
        # 30's other end, on A0's frame, has nothing on A-0: that is a finding of its own.
        self.assertEqual([(g.segment, g.activity) for g in self.rule("unbalanced")
                          if g.segment in (30, 37)], [(30, "A0")])

    def test_join_levels_settles_it_and_lifts_the_tunnel(self):
        result = self.editor.join_levels(37, 30)  # either order
        self.assertEqual(result["joined"], [30, 37])
        self.assertEqual(self.rule("not_joined"), [])
        arrows = {a.sector_id: a for d in self.editor.snapshot().diagrams() for a in d.arrows}
        self.assertEqual(arrows[30].end.node, arrows[37].start.node)
        self.assertIsNone(arrows[30].end.tunnel)

    def test_join_levels_refuses_what_is_not_one_arrow_on_two_levels(self):
        with self.assertRaisesRegex(EditError, "not an arrow on a box"):
            self.editor.join_levels(30, 34)          # both on A0
        with self.assertRaisesRegex(EditError, "already joined"):
            self.editor.join_levels(31, 40)
        with self.assertRaisesRegex(EditError, "no arrow segment 999"):
            self.editor.join_levels(30, 999)

    def test_join_levels_refuses_an_end_already_continued(self):
        # A second output of the same flow leaving C1's sheet: 31 is continued by 40 already.
        r = self.editor.add_arrow(fx.C1, {"activity": fx.G1}, {"frame": "output"},
                                  flow=fx.S_RESULT)
        self.assertEqual(r["joined_to_other_level"], [])
        with self.assertRaisesRegex(EditError, "already continued on A1 by segment 40"):
            self.editor.join_levels(31, r["sector"])

    def test_join_levels_refuses_two_flows(self):
        # Draw on A1 a frame input of another flow, then try to join it to 30.
        r = self.editor.add_arrow(fx.C1, {"frame": "input"}, {"activity": fx.G1, "role": "input"},
                                  name="другое")
        with self.assertRaisesRegex(EditError, "different flows"):
            self.editor.join_levels(30, r["sector"])

    def test_joining_survives_a_save(self):
        self.editor.join_levels(30, 37)
        out = os.path.join(self._dir.name, "joined.rsf")
        self.editor.save(out)
        self.assertEqual([f for f in rules.check(ModelEditor(out).snapshot())
                          if f.rule == "not_joined"], [])


class Names(unittest.TestCase):

    def test_an_activity_is_named_by_a_verb_in_the_infinitive(self):
        for name in ("Проверить заявку", "Собраться в назначенном месте", "Пойти по маршруту",
                     "Достичь финальной точки", "Нести груз", "Plan the route", "A=0?"):
            self.assertIsNone(rules._not_a_verb(name), name)
        for name in ("Проверка заявки", "Отчётность", "Часть работ", "Маркетинг",
                     "Стратегическое планирование", "Planning", "Inspection of goods"):
            self.assertIsNotNone(rules._not_a_verb(name), name)

    def test_a_flow_named_by_a_verb_is_flagged(self):
        sheet = _sheet([_arrow(1, 5, "получить данные"), _arrow(2, 6, "данные")])
        found = rules._flow_names(sheet, set())
        self.assertEqual([(f.rule, f.flow) for f in found], [("flow_name_noun", 5)])

    def test_a_flow_with_no_name_is_an_error_once_per_flow(self):
        a, b = _arrow(1, 5, ""), _arrow(2, 5, "")
        b.flow = a.flow
        found = rules._flow_names(_sheet([a, b]), set())
        self.assertEqual([(f.rule, f.severity) for f in found], [("unnamed_arrow", rules.ERROR)])


class Sides(unittest.TestCase):

    def test_leaving_a_box_by_its_input_side_or_entering_by_its_output_is_an_error(self):
        box = Activity(1, "Сделать", 0, -1, 100, 100, 80, 50, number="A1")
        a = Arrow(9, 5, "что-то", End("activity", 1, "left", "input"),
                  End("frame", side="right", role="output"), [(100, 120), (793, 120)])
        b = Arrow(10, 6, "другое", End("frame", side="left", role="input"),
                  End("activity", 1, "right", "output"), [(7, 110), (180, 110)])
        sheet = _sheet([a, b], [box])
        wrong = [f for x in (a, b) for f in rules._arrow_rules(sheet, x) if f.rule == "wrong_side"]
        self.assertEqual(sorted((f.segment, f.severity) for f in wrong),
                         [(9, rules.ERROR), (10, rules.ERROR)])


class Samples(unittest.TestCase):

    @unittest.skipUnless(SAMPLES, "Ramus samples not installed")
    def test_ramus_samples_break_no_syntax_rule(self):
        from ramus_rsf import RsfModel
        for path in SAMPLES:
            with self.subTest(os.path.basename(path)):
                errors = [f for f in rules.check(RsfModel(path)) if f.severity == rules.ERROR]
                self.assertEqual(errors, [])


def _arrow(sid, stream, name):
    return Arrow(sid, stream, name, End("frame", side="left", role="input"),
                 End("frame", side="right", role="output"), [(7, 50), (793, 50)], flow=sid)


def _sheet(arrows, boxes=()):
    return Diagram(parent_id=1, parent_name="", activities=list(boxes), arrows=arrows, node="A0")
