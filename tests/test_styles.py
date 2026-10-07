"""Colours, widths, dashes and fonts of boxes and arrows - written so Ramus shows them."""

import os
import tempfile
import unittest

import _paths  # noqa: F401  (puts src/ on the path)

import styles as st
from layout_scenarios import olympiad
from model_editor import EditError
from scene import Line, Rect, Text, build_scene


class Words(unittest.TestCase):

    def test_colours_by_name_hex_or_numbers(self):
        self.assertEqual(st.parse_color("green"), (0, 255, 0))
        self.assertEqual(st.parse_color("Зелёный"), (0, 255, 0))
        self.assertEqual(st.parse_color("#f00"), (255, 0, 0))
        self.assertEqual(st.parse_color("#00A0a0"), (0, 160, 160))
        self.assertEqual(st.parse_color("12, 34, 56"), (12, 34, 56))
        self.assertEqual(st.parse_color("rgb(1,2,3)"), (1, 2, 3))
        with self.assertRaises(st.StyleError):
            st.parse_color("заметный")

    def test_a_colour_is_kept_as_java_does(self):
        self.assertEqual(st.argb((255, 255, 255)), -1)
        self.assertEqual(st.argb((0, 0, 0)), -16777216)
        self.assertEqual(st.argb((0, 255, 0)), -16711936)

    def test_line_kinds(self):
        self.assertIsNone(st.line_of("solid"))
        self.assertEqual(st.line_name(st.line_of("dash-dot")), "dash_dot")
        with self.assertRaises(st.StyleError):
            st.line_of("wavy")


class Styling(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ed = olympiad(os.path.join(self.tmp.name, "o.rsf"))

    def tearDown(self):
        self.tmp.cleanup()

    def sheet(self):
        return next(d for d in self.ed.snapshot().diagrams() if d.node == "A0")

    def test_a_box_takes_its_colours_font_and_type(self):
        box = self.sheet().activities[0]
        done = self.ed.style_activity(box.element_id, fill="green", color="#000080",
                                      font_size=12, bold=True, block_type="operation")
        self.assertEqual((done["fill"], done["color"], done["block_type"]),
                         ("#00ff00", "#000080", "operation"))
        again = next(a for a in self.sheet().activities if a.element_id == box.element_id)
        self.assertEqual((again.fill, again.color, again.font_size, again.bold, again.type),
                         ("#00ff00", "#000080", 12.0, True, 3))
        items = build_scene(self.sheet()).items
        self.assertTrue(any(isinstance(i, Rect) and i.fill == "#00ff00" for i in items))
        self.assertTrue(any(isinstance(i, Text) and i.bold and i.text.startswith("Подготовить")
                            for i in items))

    def test_an_arrow_takes_its_colour_width_and_dashes_on_every_branch(self):
        sheet = self.sheet()
        rules = next(a for a in sheet.arrows if a.name == "положение об олимпиаде")
        done = self.ed.style_arrow(rules.sector_id, color="red", width=2, line="dashed")
        family = [a for a in self.sheet().arrows if a.flow == rules.flow]
        self.assertGreater(len(family), 1)  # it forks to A3 and A4
        self.assertEqual(sorted(done["segments"]), sorted(a.sector_id for a in family))
        for a in family:
            self.assertEqual((a.color, a.width, a.dash), ("#ff0000", 2.0, (6.0, 4.0)))
        lines = [i for i in build_scene(self.sheet()).items
                 if isinstance(i, Line) and i.color == "#ff0000"]
        self.assertTrue(lines and all(i.dash == (6.0, 4.0) for i in lines))

    def test_only_what_is_given_changes(self):
        arrow = next(a for a in self.sheet().arrows if a.name == "оценки")
        self.ed.style_arrow(arrow.sector_id, color="blue", width=3)
        self.ed.style_arrow(arrow.sector_id, font_size=11, bold=True)
        again = next(a for a in self.sheet().arrows if a.sector_id == arrow.sector_id)
        self.assertEqual((again.color, again.width, again.font_size, again.bold),
                         ("#0000ff", 3.0, 11.0, True))

    def test_new_things_come_styled_and_branches_look_like_their_trunk(self):
        top = self.sheet().parent_id
        box = self.ed.add_activity(top, "Наградить победителей", style={"fill": "yellow"})
        self.assertEqual(box["style"]["fill"], "#ffff00")
        made = self.ed.add_arrow(top, {"activity": box["id"]}, {"frame": "output"}, "дипломы",
                                 style={"color": "teal", "width": 2})
        self.assertEqual(made["style"]["color"], "#008080")
        branch = self.ed.add_arrow(top, {"arrow": made["sector"]},
                                   {"activity": self.sheet().activities[0].element_id,
                                    "role": "control"})
        styled = next(a for a in self.sheet().arrows if a.sector_id == branch["sector"])
        self.assertEqual((styled.color, styled.width), ("#008080", 2.0))
        with self.assertRaises(EditError):
            self.ed.add_activity(top, "Ещё", style={"shadow": True})

    def test_nonsense_is_refused(self):
        box = self.sheet().activities[0]
        with self.assertRaises(EditError):
            self.ed.style_activity(box.element_id, fill="заметный")
        with self.assertRaises(EditError):
            self.ed.style_activity(box.element_id, block_type="cloud")
        arrow = self.sheet().arrows[0]
        with self.assertRaises(EditError):
            self.ed.style_arrow(arrow.sector_id, width=40)


if __name__ == "__main__":
    unittest.main()
