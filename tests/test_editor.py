import os
import tempfile
import unittest
import zipfile

import _paths  # noqa: F401  (puts src/ on the path)
import rsf_builder as fx

from model_editor import EditError, ModelEditor
from ramus_rsf import RsfModel
from rsf_document import NO_REMOVAL, RsfDocument, Table, format_cell, format_double

SAMPLES = [p for p in (_paths.MODEL_EXAMPLE, _paths.ENTERPRISE) if p]


def _dump(rows_xml: str, fields_xml: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?><table generate-from-table="t" '
            'generate-time="x" prefix="ramus_"><fields>' + fields_xml + '</fields>'
            + rows_xml + '</table>').encode("utf-8")


FIELDS = ('<field id="0" name="ID" type="BIGINT"/><field id="1" name="NAME" type="CLOB"/>'
          '<field id="2" name="W" type="DOUBLE"/>')


class Writing(unittest.TestCase):
    """The serialiser must write exactly what Ramus writes."""

    @unittest.skipUnless(SAMPLES, "Ramus samples not installed")
    def test_rebuilding_a_ramus_sample_reproduces_every_table(self):
        for path in SAMPLES:
            doc = RsfDocument(path)
            for name in doc.table_names():
                with self.subTest(file=os.path.basename(path), table=name):
                    self.assertEqual(doc.table(name).rebuilt(), doc.member_bytes("data/" + name + ".xml"))

    @unittest.skipUnless(SAMPLES, "Ramus samples not installed")
    def test_an_unchanged_save_writes_every_member_as_it_was(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "copy.rsf")
            RsfDocument(SAMPLES[0]).save(out)
            with zipfile.ZipFile(SAMPLES[0]) as a, zipfile.ZipFile(out) as b:
                self.assertEqual(a.namelist(), b.namelist())
                for n in a.namelist():
                    self.assertEqual(a.read(n), b.read(n), n)

    def test_null_cells_are_left_out_and_empty_ones_self_close(self):
        raw = _dump('<data><row><f id="0">1</f><f id="1"/></row><row><f id="0">2</f></row></data>', FIELDS)
        t = Table("t", raw)
        self.assertEqual(t.rows, [{"ID": "1", "NAME": ""}, {"ID": "2"}])
        self.assertEqual(t.rebuilt(), raw)
        t.add({"ID": 3, "NAME": None, "W": 2.0})
        self.assertIn(b'<row><f id="0">3</f><f id="2">2.0</f></row>', t.to_bytes())

    def test_an_empty_table_is_written_as_empty_data(self):
        raw = _dump("<data/>", FIELDS)
        t = Table("t", raw)
        self.assertEqual(t.rebuilt(), raw)
        t.add({"ID": 1})
        t.remove(t.rows[0])
        self.assertTrue(t.to_bytes().endswith(b"</fields><data/></table>"))

    def test_carriage_returns_in_text_survive_unchanged(self):
        raw = _dump('<data><row><f id="0">1</f><f id="1">line one\r\nline two</f></row></data>', FIELDS)
        t = Table("t", raw)
        self.assertEqual(t.rows[0]["NAME"], "line one\r\nline two")
        self.assertEqual(t.rebuilt(), raw)

    def test_markup_characters_in_text_are_escaped(self):
        t = Table("t", _dump("<data/>", FIELDS))
        t.add({"ID": 1, "NAME": 'a < b & c > "d"'})
        self.assertIn(b'<f id="1">a &lt; b &amp; c &gt; "d"</f>', t.to_bytes())
        self.assertEqual(Table("t", t.to_bytes()).rows[0]["NAME"], 'a < b & c > "d"')

    def test_values_are_formatted_per_column_type(self):
        self.assertEqual(format_double(80), "80.0")
        self.assertEqual(format_double(12.25), "12.25")
        self.assertEqual(format_cell(True, "BOOLEAN"), "TRUE")
        self.assertEqual(format_cell(False, "BOOLEAN"), "FALSE")
        self.assertEqual(format_cell(True, "INTEGER"), "1")
        self.assertEqual(format_cell(7, "BIGINT"), "7")
        self.assertEqual(format_cell(b"\x00\xff", "VARBINARY"), "807F")
        self.assertIsNone(format_cell(None, "CLOB"))

    def test_an_unknown_column_is_refused(self):
        t = Table("t", _dump("<data/>", FIELDS))
        with self.assertRaises(KeyError):
            t.add({"ID": 1, "NOPE": 2})


class Ids(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "tiny.rsf")
        fx.tiny_model(self.path)

    def tearDown(self):
        self._dir.cleanup()

    def test_a_new_element_id_is_past_every_id_in_the_file_deleted_ones_included(self):
        doc = RsfDocument(self.path)
        biggest = max(int(r["ELEMENT_ID"]) for r in doc.table("elements").rows)
        self.assertEqual(doc.new_element_id(), biggest + 1)
        self.assertEqual(doc.new_element_id(), biggest + 2)

    def test_a_new_row_gets_the_branch_bookkeeping(self):
        doc = RsfDocument(self.path)
        row = doc.add_row("elements", {"ELEMENT_ID": 999, "QUALIFIER_ID": fx.Q_MODEL})
        self.assertEqual(row["CREATED_BRANCH_ID"], "0")
        self.assertEqual(row["REMOVED_BRANCH_ID"], str(NO_REMOVAL))

    @unittest.skipUnless(SAMPLES, "Ramus samples not installed")
    def test_crosspoints_come_from_the_counter_and_move_it_on(self):
        doc = RsfDocument(SAMPLES[0])
        counter = doc.sequence("crosspoint_sequence")
        used = [int(r["CROSSPOINT"]) for r in doc.table("IDEF0/attribute_sector_borders").rows
                if r.get("CROSSPOINT") not in (None, "")]
        first = doc.new_crosspoint()
        self.assertGreaterEqual(first, counter or 0)
        self.assertGreater(first, max(used))
        self.assertEqual(doc.new_crosspoint(), first + 1)
        with tempfile.TemporaryDirectory() as tmp:
            out = doc.save(os.path.join(tmp, "x.rsf"))
            self.assertEqual(RsfDocument(out).sequence("crosspoint_sequence"), first + 2)

    def test_saving_never_replaces_a_file_by_accident(self):
        doc = RsfDocument(self.path)
        with self.assertRaises(FileExistsError):
            doc.save(self.path)


class Renaming(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "tiny.rsf")
        fx.tiny_model(self.path)
        self.editor = ModelEditor(self.path)

    def tearDown(self):
        self._dir.cleanup()

    def _saved(self) -> RsfModel:
        out = os.path.join(self._dir.name, "out.rsf")
        self.editor.save(out, overwrite=True)
        return RsfModel(out)

    def test_a_renamed_activity_reads_back_with_its_new_name(self):
        self.editor.rename_activity(fx.C1, "Подготовить данные")
        self.assertEqual(self.editor.snapshot().activities()[fx.C1].name, "Подготовить данные")
        model = self._saved()
        self.assertEqual(model.activities()[fx.C1].name, "Подготовить данные")
        self.assertEqual(model.activities()[fx.C2].name, "Выполнить")

    def test_renaming_a_flow_renames_every_segment_but_one_with_its_own_text(self):
        result = self.editor.rename_flow(fx.S_DATA, "сведения")
        self.assertEqual(result["old_name"], "данные")
        names = {a.sector_id: a.name for d in self._saved().diagrams() for a in d.arrows
                 if a.stream_id == fx.S_DATA}
        self.assertEqual(names[30], "сведения")
        self.assertEqual(names[35], "другое имя")  # an alternative text still wins

    def test_only_real_boxes_and_flows_can_be_renamed(self):
        with self.assertRaises(EditError):
            self.editor.rename_activity(fx.S_DATA, "x")
        with self.assertRaises(EditError):
            self.editor.rename_flow(fx.C1, "x")
        with self.assertRaises(EditError):
            self.editor.rename_activity(123456, "x")
        with self.assertRaises(EditError):
            self.editor.rename_activity(fx.C1, "   ")

    def test_a_change_to_an_older_branch_adds_a_version_instead_of_rewriting_history(self):
        doc = self.editor.doc
        texts = doc.table("Core/attribute_texts")
        # Pretend the file was saved on branch 2: the existing values now belong to the past.
        doc.table("elements").set(doc.table("elements").where(ELEMENT_ID=fx.C2)[0], CREATED_BRANCH_ID=2)
        before = len(texts.where(ELEMENT_ID=fx.C1, ATTRIBUTE_ID=fx.ATTR["NAME"]))
        self.editor.rename_activity(fx.C1, "Новая версия")
        rows = texts.where(ELEMENT_ID=fx.C1, ATTRIBUTE_ID=fx.ATTR["NAME"])
        self.assertEqual(len(rows), before + 1)
        self.assertEqual(rows[0]["VALUE"], "Подготовить")  # the old value is kept
        self.assertEqual(rows[-1]["VALUE_BRANCH_ID"], "2")
        self.assertEqual(self.editor.snapshot().activities()[fx.C1].name, "Новая версия")

    def test_saving_over_the_original_keeps_one_backup_of_it(self):
        with open(self.path, "rb") as fh:
            original = fh.read()
        self.editor.rename_activity(fx.C1, "Первое")
        first = self.editor.save()
        self.assertTrue(os.path.isfile(first["backup"]))
        with open(first["backup"], "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.editor.rename_activity(fx.C1, "Второе")
        second = self.editor.save()
        self.assertNotIn("backup", second)
        self.assertEqual(RsfModel(self.path).activities()[fx.C1].name, "Второе")

    @unittest.skipUnless(_paths.MODEL_EXAMPLE, "Ramus samples not installed")
    def test_a_ramus2_file_keeps_its_copy_of_the_name_in_step(self):
        editor = ModelEditor(_paths.MODEL_EXAMPLE)
        acts = editor.snapshot().activities()
        rows = {int(r["ELEMENT_ID"]): r for r in editor.doc.table("elements").rows}
        target = next((i for i in acts if rows.get(i, {}).get("ELEMENT_NAME")), None)
        if target is None:
            self.skipTest("no activity keeps a name copy in this sample")
        editor.rename_activity(target, "Новое имя")
        self.assertEqual(rows[target]["ELEMENT_NAME"], "Новое имя")
        self.assertEqual(editor.snapshot().activities()[target].name, "Новое имя")


def _rect(a):
    return (a.x, a.y, a.width, a.height)


def _touches(a, b, gap=0.0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + gap <= bx or bx + bw + gap <= ax or ay + ah + gap <= by or by + bh + gap <= ay)


class Adding(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "tiny.rsf")
        fx.tiny_model(self.path)
        self.editor = ModelEditor(self.path)

    def tearDown(self):
        self._dir.cleanup()

    def _sheet(self, model, parent_id):
        return next(d for d in model.diagrams() if d.parent_id == parent_id)

    def test_a_new_box_goes_last_on_its_sheet_and_takes_the_next_number(self):
        result = self.editor.add_activity(fx.TOP, "Проверить")
        self.assertEqual(result["number"], "A3")
        out = os.path.join(self._dir.name, "out.rsf")
        self.editor.save(out)
        sheet = self._sheet(RsfModel(out), fx.TOP)
        self.assertEqual([a.number for a in sheet.activities], ["A1", "A2", "A3"])
        self.assertEqual(sheet.activities[-1].name, "Проверить")
        self.assertEqual(sheet.activities[-1].element_id, result["id"])

    def test_a_new_box_is_written_with_ramus_bookkeeping(self):
        new = self.editor.add_activity(fx.TOP, "Проверить")["id"]
        doc = self.editor.doc
        element = doc.table("elements").where(ELEMENT_ID=new)[0]
        self.assertEqual(element["QUALIFIER_ID"], str(fx.Q_MODEL))
        self.assertEqual(element["REMOVED_BRANCH_ID"], str(NO_REMOVAL))
        place = doc.table("Core/attribute_hierarchicals").where(ELEMENT_ID=new)[0]
        self.assertEqual(place["PARENT_ELEMENT_ID"], str(fx.TOP))
        self.assertEqual(place["PREVIOUS_ELEMENT_ID"], str(fx.C2))  # after the last box

    def test_a_new_box_takes_its_look_from_the_box_before_it(self):
        new = self.editor.add_activity(fx.C1, "Ещё")["id"]  # the box before it is G1
        acts = self.editor.snapshot().activities()
        self.assertEqual(acts[new].type, acts[fx.G1].type)
        self.assertEqual(acts[new].font_size, acts[fx.G1].font_size)

    def test_without_a_position_the_box_lands_clear_of_boxes_arrows_and_labels(self):
        new = self.editor.add_activity(fx.TOP, "Проверить")["id"]
        sheet = self._sheet(self.editor.snapshot(), fx.TOP)
        box = next(_rect(a) for a in sheet.activities if a.element_id == new)
        left, top, right, bottom = sheet.frame
        self.assertTrue(left <= box[0] and top <= box[1]
                        and box[0] + box[2] <= right and box[1] + box[3] <= bottom)
        for a in sheet.activities:
            if a.element_id != new:
                self.assertFalse(_touches(box, _rect(a)), a.name)
        for arrow in sheet.arrows:
            for p, q in zip(arrow.points, arrow.points[1:]):
                seg = (min(p[0], q[0]), min(p[1], q[1]), abs(p[0] - q[0]), abs(p[1] - q[1]))
                self.assertFalse(_touches(box, seg), f"crosses arrow {arrow.sector_id}")
            if arrow.label is not None:
                lb = arrow.label
                self.assertFalse(_touches(box, (lb.x, lb.y, lb.width, lb.height)))

    def test_a_given_position_is_used_and_must_be_on_the_sheet(self):
        result = self.editor.add_activity(fx.TOP, "Здесь", x=600, y=330, width=100, height=50)
        self.assertEqual((result["x"], result["y"]), (600.0, 330.0))
        with self.assertRaises(EditError):
            self.editor.add_activity(fx.TOP, "Мимо", x=780, y=400, width=100, height=50)

    def test_the_first_box_under_an_activity_gives_it_a_decomposition(self):
        result = self.editor.add_activity(fx.C2, "Первая работа")
        self.assertEqual(result["number"], "A21")
        model = self.editor.snapshot()
        sheet = self._sheet(model, fx.C2)
        self.assertEqual(sheet.node, "A2")
        self.assertEqual([a.name for a in sheet.activities], ["Первая работа"])
        data = self.editor.doc.table("IDEF0/attribute_visual_datas").where(ELEMENT_ID=fx.C2)
        self.assertTrue(data and data[0].get("DATA"))  # an empty version-2 diagram, not nothing

    def test_the_context_diagram_keeps_its_single_box(self):
        with self.assertRaises(EditError):
            self.editor.add_activity(fx.BASE, "Вторая вершина")

    @unittest.skipUnless(_paths.MODEL_EXAMPLE, "Ramus samples not installed")
    def test_adding_to_a_ramus2_model_keeps_its_conventions(self):
        editor = ModelEditor(_paths.MODEL_EXAMPLE)
        a0 = next(a for a in editor.snapshot().activities().values() if a.number == "A0")
        result = editor.add_activity(a0.element_id, "Контролировать качество")
        self.assertEqual(result["number"], "A4")
        element = editor.doc.table("elements").where(ELEMENT_ID=result["id"])[0]
        self.assertEqual(element["ELEMENT_NAME"], "Контролировать качество")  # Ramus 2 keeps a copy
        data = editor.doc.table("IDEF0/attribute_visual_datas").where(ELEMENT_ID=result["id"])[0]
        self.assertTrue(data["DATA"])  # Ramus 2 stores an empty diagram for a leaf


class Arrows(unittest.TestCase):
    """The fixture's A0 sheet (TOP decomposed) holds C1 = A1, itself decomposed into G1, and
    C2 = A2. On A0 the flow "данные" enters C1's input from the frame; on C1's own sheet that
    arrow has no continuation yet."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "tiny.rsf")
        fx.tiny_model(self.path)
        self.editor = ModelEditor(self.path)

    def tearDown(self):
        self._dir.cleanup()

    def _sheet(self, parent_id):
        return next(d for d in self.editor.snapshot().diagrams() if d.parent_id == parent_id)

    def _arrow(self, sector):
        return next(a for d in self.editor.snapshot().diagrams() for a in d.arrows
                    if a.sector_id == sector)

    def test_an_output_to_a_control_reads_back_with_its_ends_and_name(self):
        r = self.editor.add_arrow(fx.TOP, {"activity": fx.C1},
                                  {"activity": fx.C2, "role": "control"}, "указание")
        a = self._arrow(r["sector"])
        self.assertEqual(a.name, "указание")
        self.assertEqual((a.start.kind, a.start.activity_id, a.start.role),
                         ("activity", fx.C1, "output"))
        self.assertEqual((a.end.kind, a.end.activity_id, a.end.role),
                         ("activity", fx.C2, "control"))
        c1, c2 = fx.BOXES[fx.C1], fx.BOXES[fx.C2]
        self.assertEqual(a.points[0][0], c1[0] + c1[2])   # leaves C1's right side
        self.assertEqual(a.points[-1][1], c2[1])           # arrives on C2's top

    def test_the_route_is_orthogonal_and_keeps_out_of_the_boxes(self):
        r = self.editor.add_arrow(fx.TOP, {"activity": fx.C2},
                                  {"activity": fx.C1, "role": "input"}, "возврат")
        pts = self._arrow(r["sector"]).points
        for p, q in zip(pts, pts[1:]):
            self.assertTrue(p[0] == q[0] or p[1] == q[1], (p, q))
            for box in self._sheet(fx.TOP).activities:
                rect = (box.x, box.y, box.width, box.height)
                inside = (rect[0] + 1, rect[1] + 1, rect[2] - 2, rect[3] - 2)
                self.assertFalse(_touches((min(p[0], q[0]), min(p[1], q[1]),
                                           abs(p[0] - q[0]), abs(p[1] - q[1])), inside),
                                 f"{(p, q)} runs through {box.name}")

    def test_route_points_share_ordinates_along_each_straight_piece(self):
        r = self.editor.add_arrow(fx.TOP, {"activity": fx.C1},
                                  {"activity": fx.C2, "role": "control"}, "указание")
        rows = sorted(self.editor.doc.table("IDEF0/attribute_sector_points").where(
            ELEMENT_ID=r["sector"]), key=lambda row: int(row["POSITION"]))
        self.assertGreaterEqual(len(rows), 2)
        for a, b in zip(rows, rows[1:]):
            if a["Y_POSITION"] == b["Y_POSITION"]:
                self.assertEqual(a["Y_ORDINATE_ID"], b["Y_ORDINATE_ID"])
            if a["X_POSITION"] == b["X_POSITION"]:
                self.assertEqual(a["X_ORDINATE_ID"], b["X_ORDINATE_ID"])
        self.assertTrue(all(row["POINT_TYPE"] == "-1" for row in rows))

    def test_a_new_flow_is_created_with_its_name(self):
        r = self.editor.add_arrow(fx.TOP, {"activity": fx.C1},
                                  {"activity": fx.C2, "role": "mechanism"}, "исполнитель")
        element = self.editor.doc.table("elements").where(ELEMENT_ID=r["stream"])[0]
        self.assertEqual(element["QUALIFIER_ID"], str(fx.Q_STREAMS))
        self.assertEqual(self.editor._stream_names()[r["stream"]], "исполнитель")

    def test_an_existing_flow_can_be_drawn_again_by_id(self):
        before = len(self.editor.doc.table("elements").rows)
        r = self.editor.add_arrow(fx.TOP, {"activity": fx.C1},
                                  {"activity": fx.C2, "role": "control"}, flow=fx.S_RESULT)
        self.assertEqual(r["stream"], fx.S_RESULT)
        self.assertEqual(r["name"], "результат")
        self.assertEqual(len(self.editor.doc.table("elements").rows), before + 1)  # the sector only

    def test_a_frame_arrow_joins_the_same_flow_left_unfinished_on_the_level_above(self):
        r = self.editor.add_arrow(fx.C1, {"frame": "input"},
                                  {"activity": fx.G1, "role": "control"}, "данные")
        self.assertEqual(r["joined_to_other_level"], ["source"])
        self.assertEqual(r["stream"], fx.S_DATA)
        new = self._arrow(r["sector"])
        self.assertEqual(new.start.node, 101)   # the node of A0's arrow into C1
        self.assertIsNone(self._arrow(30).end.tunnel)  # so that end is no longer a tunnel

    def test_a_frame_arrow_with_nothing_to_join_is_left_a_tunnel(self):
        r = self.editor.add_arrow(fx.C1, {"activity": fx.G1}, {"frame": "output"}, "отчёт")
        self.assertEqual(r["joined_to_other_level"], [])
        self.assertEqual(self._arrow(r["sector"]).end.tunnel, "hard")

    def test_the_name_is_placed_clear_of_the_boxes(self):
        r = self.editor.add_arrow(fx.TOP, {"activity": fx.C1},
                                  {"activity": fx.C2, "role": "control"}, "указание")
        lb = self._arrow(r["sector"]).label
        self.assertIsNotNone(lb)
        for box in self._sheet(fx.TOP).activities:
            self.assertFalse(_touches((lb.x, lb.y, lb.width, lb.height),
                                      (box.x, box.y, box.width, box.height)), box.name)

    def test_what_idef0_does_not_allow_is_refused(self):
        add = self.editor.add_arrow
        cases = [
            (fx.TOP, {"frame": "input"}, {"frame": "output"}, "x"),               # frame to frame
            (fx.TOP, {"activity": fx.C1, "role": "input"},
             {"activity": fx.C2, "role": "input"}, "x"),                          # leaves by an input
            (fx.TOP, {"activity": fx.C1}, {"activity": fx.C2}, "x"),              # no role at target
            (fx.TOP, {"activity": fx.G1}, {"activity": fx.C2, "role": "input"}, "x"),  # not on sheet
            (fx.TOP, {"activity": fx.C1}, {"activity": fx.C1, "role": "input"}, "x"),  # into itself
            (fx.TOP, {"activity": fx.C1}, {"activity": fx.C2, "role": "input"}, None),  # no name
            (fx.C2, {"activity": fx.C2}, {"frame": "output"}, "x"),               # C2 has no sheet
        ]
        for sheet, src, dst, name in cases:
            with self.subTest(src=src, dst=dst):
                with self.assertRaises(EditError):
                    add(sheet, src, dst, name)

    @unittest.skipUnless(_paths.MODEL_EXAMPLE, "Ramus samples not installed")
    def test_a_ramus2_file_is_refused_with_the_way_out(self):
        editor = ModelEditor(_paths.MODEL_EXAMPLE)
        a0 = next(a for a in editor.snapshot().activities().values() if a.number == "A0")
        kids = [a for a in editor.snapshot().activities().values() if a.parent_id == a0.element_id]
        with self.assertRaises(EditError) as caught:
            editor.add_arrow(a0.element_id, {"activity": kids[0].element_id},
                             {"activity": kids[1].element_id, "role": "input"}, "x")
        self.assertIn("Ramus 3", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
