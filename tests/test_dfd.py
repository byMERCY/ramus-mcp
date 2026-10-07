"""Data flow diagrams: models drawn in DFD and DFDS notation, read, drawn, checked and built."""

import os
import tempfile
import unittest

import _paths  # noqa: F401  (puts src/ on the path)

import idef0_rules as rules
from model_editor import EditError, ModelEditor, _justify_roles
from ramus_rsf import RsfModel
from rsf_document import RsfDocument
from scene import Line, Poly, Rect, build_scene


def _ids(ed):
    model = ed.snapshot()
    return ({d.node: d.parent_id for d in model.diagrams()},
            {(a.number or a.name): a.element_id for a in model.activities().values()})


def _sheet(ed, node):
    return next(d for d in ed.snapshot().diagrams() if d.node == node)


class _Shop(unittest.TestCase):
    """A DFD model: two processes, a customer, a data store."""

    notation = "dfd"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "shop.rsf")
        self.ed = ModelEditor.create(self.path, "Обработать заказ", notation=self.notation)
        sheets, acts = _ids(self.ed)
        self.top = acts["A0"]
        self.a1 = self.ed.add_activity(self.top, "Принять заказ")["id"]
        self.a2 = self.ed.add_activity(self.top, "Отгрузить заказ")["id"]
        self.client = self.ed.add_activity(self.top, "Клиент", kind="external")["id"]
        self.orders = self.ed.add_activity(self.top, "Заказы", kind="store")["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def flow(self, source, target, name):
        return self.ed.add_arrow(self.top, source, target, name)


class Reading(_Shop):

    def test_the_notation_is_stored_on_the_model_and_its_decompositions(self):
        self.ed.save()
        doc = RsfDocument(self.path)
        types = {r["ELEMENT_ID"]: r["TYPE"]
                 for r in doc.table("IDEF0/attribute_decomposition_types").rows}
        self.assertEqual(types.get("3"), "1")  # the base function
        self.assertEqual(types.get(str(self.top)), "1")
        self.assertEqual(types.get(str(self.a1)), "1")  # inherited
        with RsfModel(self.path) as m:
            self.assertEqual({d.node: d.notation for d in m.diagrams()},
                             {"A-0": "dfd", "A0": "dfd"})

    def test_only_processes_are_numbered(self):
        sheet = _sheet(self.ed, "A0")
        self.assertEqual([(a.number, a.kind) for a in sheet.activities],
                         [("A1", "process"), ("A2", "process"), ("", "external"),
                          ("", "store")])
        self.assertEqual(self.ed.add_activity(self.top, "Упаковать заказ")["number"], "A3")

    def test_a_process_with_a_decomposition_is_marked_so(self):
        self.ed.add_activity(self.a1, "Проверить заказ")
        boxes = {a.element_id: a for a in _sheet(self.ed, "A0").activities}
        self.assertTrue(boxes[self.a1].decomposed)
        self.assertFalse(boxes[self.a2].decomposed)


class Drawing(_Shop):

    def _shapes(self, sheet):
        scene = build_scene(sheet)
        return scene.items

    def test_processes_are_rounded_and_stores_open_at_the_right(self):
        items = self._shapes(_sheet(self.ed, "A0"))
        rounded = [i for i in items if isinstance(i, Rect) and i.radius > 0]
        self.assertEqual(len(rounded), 2)  # the two processes
        boxes = {a.element_id: a for a in _sheet(self.ed, "A0").activities}
        store = boxes[self.orders]
        outline = [i for i in items if isinstance(i, Line) and i.points[0] ==
                   (store.x + store.width, store.y)]
        self.assertEqual(outline[0].points[-1], (store.x + store.width, store.y + store.height))

    def test_an_idef0_box_with_no_decomposition_has_its_corner_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = ModelEditor.create(os.path.join(tmp, "i.rsf"), "Сделать дело")
            _, acts = _ids(ed)
            ed.add_activity(acts["A0"], "Начать")
            box = _sheet(ed, "A0").activities[0]
            marks = [i for i in build_scene(_sheet(ed, "A0")).items if isinstance(i, Line)
                     and i.points == [(box.x + 4.0, box.y), (box.x, box.y + 4.0)]]
            self.assertEqual(len(marks), 1)


class Flows(_Shop):

    def test_a_flow_meets_a_box_on_the_side_it_is_told(self):
        result = self.flow({"activity": self.orders, "side": "top"},
                           {"activity": self.a2, "side": "bottom"}, "данные заказа")
        self.assertEqual(result["from"]["side"], "top")
        self.assertEqual(result["to"]["side"], "bottom")
        result = self.flow({"activity": self.client}, {"activity": self.a1}, "заказ")
        self.assertEqual((result["from"]["side"], result["to"]["side"]), ("right", "left"))

    def test_the_icom_rules_are_not_a_data_flow_diagrams(self):
        self.flow({"activity": self.client}, {"activity": self.a1}, "заказ")
        self.flow({"activity": self.a1}, {"activity": self.a2}, "принятый заказ")
        self.flow({"activity": self.a2}, {"activity": self.client, "side": "bottom"},
                  "посылка")
        self.flow({"activity": self.a1, "side": "bottom"}, {"activity": self.orders,
                                                             "side": "top"}, "запись")
        found = [f for f in rules.check(self.ed.snapshot(), "A0")]
        self.assertFalse({"needs_control", "wrong_side", "box_count"} & {f.rule for f in found})
        self.assertFalse([f for f in found if f.severity == rules.ERROR], found)

    def test_data_moves_only_through_a_process(self):
        self.flow({"activity": self.client}, {"activity": self.orders}, "заказ")
        found = {f.rule for f in rules.check(self.ed.snapshot(), "A0")}
        self.assertIn("dfd_through_process", found)
        self.assertIn("dfd_needs_input", found)  # neither process takes anything in

    def test_externals_and_stores_belong_on_data_flow_diagrams(self):
        with tempfile.TemporaryDirectory() as tmp:
            ed = ModelEditor.create(os.path.join(tmp, "i.rsf"), "Сделать дело")
            _, acts = _ids(ed)
            with self.assertRaisesRegex(EditError, "IDEF0 sheet holds activities only"):
                ed.add_activity(acts["A0"], "Клиент", kind="external")

    def test_the_context_of_a_dfd_may_show_its_surroundings(self):
        sheets, _ = _ids(self.ed)
        bank = self.ed.add_activity(sheets["A-0"], "Банк", kind="external")
        self.assertEqual(bank["kind"], "external")
        context = _sheet(self.ed, "A-0")
        self.assertEqual(sorted(a.kind for a in context.activities), ["external", "process"])
        with self.assertRaisesRegex(EditError, "holds exactly one activity"):
            self.ed.add_activity(sheets["A-0"], "Ещё процесс")


class Roles(_Shop):
    notation = "dfds"

    def test_roles_stand_in_rows_along_the_bottom_of_their_activity(self):
        names = ["Операционист", "Кредитный аналитик", "Служба безопасности"]
        for n in names:
            self.ed.add_activity(self.top, n, kind="role", owner=self.a1)
        boxes = {a.name: a for a in _sheet(self.ed, "A0").activities}
        owner = boxes["Принять заказ"]
        for n in names:
            r = boxes[n]
            self.assertEqual(r.kind, "role")
            self.assertEqual(r.owner_id, self.a1)
            self.assertTrue(owner.x <= r.x and r.x + r.width <= owner.x + owner.width + 0.01, n)
            self.assertTrue(owner.y <= r.y and r.y + r.height <= owner.y + owner.height, n)

    def test_a_role_needs_an_activity_to_belong_to(self):
        with self.assertRaisesRegex(EditError, "owner"):
            self.ed.add_activity(self.top, "Кладовщик", kind="role")

    def test_rows_wrap_and_the_last_one_sits_on_the_bottom(self):
        rects = _justify_roles((0.0, 0.0, 100.0, 60.0), [(60.0, 16.0), (60.0, 16.0)])
        (x1, y1, _, _), (x2, y2, _, h2) = rects
        self.assertEqual(x1, x2)
        self.assertLess(y1, y2)
        self.assertEqual(y2 + h2, 58.0)

    def test_a_dfds_name_is_a_card(self):
        self.ed.add_arrow(self.top, {"activity": self.client}, {"activity": self.a1}, "заявка")
        cards = [i for i in build_scene(_sheet(self.ed, "A0")).items if isinstance(i, Poly)
                 and len(i.points) == 6]
        self.assertEqual(len(cards), 1)


if __name__ == "__main__":
    unittest.main()
