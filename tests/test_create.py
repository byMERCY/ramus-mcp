import os
import tempfile
import unittest
import zipfile

import _paths  # noqa: F401  (puts src/ on the path)

import blank_model
import idef0_rules as rules
import ramus_schema as schema
from model_editor import EditError, ModelEditor
from ramus_rsf import RsfModel
from rsf_document import RsfDocument


class Creating(unittest.TestCase):
    """A model made from nothing: what Ramus would hold in a file it had just created."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "Олимпиада.rsf")
        self.editor = ModelEditor.create(self.path, "Провести олимпиаду", author="Иванов И.")

    def tearDown(self):
        self._dir.cleanup()

    def test_the_file_holds_a_context_diagram_with_its_one_top_activity(self):
        model = RsfModel(self.path)
        (sheet,) = model.diagrams()
        self.assertEqual(sheet.node, "A-0")
        self.assertEqual([(a.number, a.name) for a in sheet.activities],
                         [("A0", "Провести олимпиаду")])
        self.assertEqual(sheet.frame[3], 437.0)  # a Ramus 3 page

    def test_it_has_every_member_and_table_a_ramus_file_has(self):
        with zipfile.ZipFile(self.path) as z:
            self.assertEqual(z.namelist(), [m for m, _, _ in schema.TABLES])
            meta = z.read("data/application_metadata.xml").decode("utf-8")
        self.assertIn('<entry key="PluginCount">28</entry>', meta)
        self.assertIn("\r\n", meta)  # Java writes properties files with CR LF
        doc = RsfDocument(self.path)
        self.assertEqual(len(doc.table("persistents").rows), len(schema.PERSISTENTS))
        self.assertEqual(len(doc.table("persistent_fields").rows),
                         sum(len(p[6]) for p in schema.PERSISTENTS))

    def test_every_table_is_written_the_way_ramus_writes_it(self):
        doc = RsfDocument(self.path)
        for name in doc.table_names():
            with self.subTest(table=name):
                self.assertEqual(doc.table(name).rebuilt(), doc.member_bytes(f"data/{name}.xml"))

    def test_an_element_name_is_empty_never_null(self):
        doc = RsfDocument(self.path)
        self.assertTrue(all(r.get("ELEMENT_NAME") == "" for r in doc.table("elements").rows))

    def test_the_frame_shows_author_project_and_dates(self):
        (prefs,) = RsfDocument(self.path).table("IDEF0/attribute_model_preferences").rows
        self.assertEqual((prefs["PROJECT_AUTOR"], prefs["PROJECT_NAME"]), ("Иванов И.", "Олимпиада"))
        self.assertRegex(prefs["CREATE_DATE"], r"^\d{1,2}/\d{1,2}/\d\d \d{1,2}:\d\d [AP]M$")

    def test_it_can_be_decomposed_drawn_on_and_saved(self):
        acts = self.editor.snapshot().activities()
        top = next(a.element_id for a in acts.values() if a.number == "A0")
        ids = [self.editor.add_activity(top, n)["id"] for n in ("Подготовить", "Провести", "Проверить")]
        self.editor.add_arrow(top, {"frame": "control"}, {"activity": ids[0], "role": "control"},
                              "положение")
        self.editor.add_arrow(top, {"activity": ids[0]}, {"activity": ids[1], "role": "input"},
                              "задания")
        self.editor.save()
        sheets = {d.node: d for d in RsfModel(self.path).diagrams()}
        self.assertEqual([a.number for a in sheets["A0"].activities], ["A1", "A2", "A3"])
        self.assertEqual(len(sheets["A0"].arrows), 2)

    def test_the_first_arrows_get_ramus_s_default_look(self):
        acts = self.editor.snapshot().activities()
        top = next(a.element_id for a in acts.values() if a.number == "A0")
        context = next(d.parent_id for d in self.editor.snapshot().diagrams() if d.node == "A-0")
        self.editor.add_arrow(context, {"activity": top}, {"frame": "output"}, "результаты")
        (arrow,) = next(d for d in self.editor.snapshot().diagrams() if d.node == "A-0").arrows
        self.assertEqual(arrow.font_size, 8.0)

    def test_a_fresh_model_only_lacks_what_is_still_to_be_drawn(self):
        found = {f.rule for f in rules.check(self.editor.snapshot())}
        self.assertEqual(found, {"needs_control", "needs_output"})

    def test_an_existing_file_is_kept_unless_overwrite_and_then_backed_up(self):
        with self.assertRaises(FileExistsError):
            ModelEditor.create(self.path, "Другое")
        ModelEditor.create(self.path, "Другое", overwrite=True)
        self.assertTrue(os.path.isfile(os.path.join(self._dir.name, "Олимпиада.backup.rsf")))
        (sheet,) = RsfModel(self.path).diagrams()
        self.assertEqual(sheet.activities[0].name, "Другое")

    def test_names_and_paths_are_checked(self):
        with self.assertRaises(EditError):
            ModelEditor.create(os.path.join(self._dir.name, "x.rsf"), "  ")
        with self.assertRaises(EditError):
            ModelEditor.create(os.path.join(self._dir.name, "x.txt"), "Сделать")


class Dates(unittest.TestCase):

    def test_dates_are_written_as_java_writes_them(self):
        import datetime as dt
        moment = dt.datetime(2026, 10, 6, 14, 7, 5, tzinfo=dt.timezone(dt.timedelta(hours=3)))
        self.assertEqual(blank_model.short_date(moment), "10/6/26 2:07 PM")
        self.assertEqual(blank_model.java_date(moment), "Tue Oct 06 14:07:05 GMT+03:00 2026")
        self.assertEqual(blank_model.short_date(moment.replace(hour=0)), "10/6/26 12:07 AM")
