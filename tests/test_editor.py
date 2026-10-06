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


if __name__ == "__main__":
    unittest.main()
