import io
import os
import tempfile
import unittest
import xml.dom.minidom

import _paths  # noqa: F401  (puts src/ on the path)
import rsf_builder as fx
from PIL import Image

import render_png
import render_svg
import scene as sc
from ramus_rsf import RsfModel


class Wrapping(unittest.TestCase):
    def test_explicit_newlines_are_kept(self):
        self.assertEqual(sc.wrap("one\ntwo", 1000, 10), ["one", "two"])
        self.assertEqual(sc.wrap("a\n\nb", 1000, 10), ["a", "", "b"])

    def test_lines_break_at_spaces_when_they_do_not_fit(self):
        width = sc.text_width("aaaa bbbb", 10)
        self.assertEqual(sc.wrap("aaaa bbbb", width + 1, 10), ["aaaa bbbb"])
        self.assertEqual(sc.wrap("aaaa bbbb", width - 1, 10), ["aaaa", "bbbb"])

    def test_a_word_too_wide_for_a_line_is_cut_after_its_hyphen(self):
        word = "Планово-экономическое"
        width = max(sc.text_width("Планово-", 12), sc.text_width("экономическое", 12)) + 1
        self.assertLess(width, sc.text_width(word, 12))
        self.assertEqual(sc.wrap(word, width, 12), ["Планово-", "экономическое"])

    def test_a_hyphenated_word_that_fits_stays_together(self):
        self.assertEqual(sc.wrap("Планово-экономический", 1000, 12), ["Планово-экономический"])

    def test_one_long_word_with_no_break_is_left_whole(self):
        self.assertEqual(sc.wrap("непрерывность", 5, 12), ["непрерывность"])


class Geometry(unittest.TestCase):
    L = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]

    def test_a_point_a_fraction_of_the_way_along_a_route(self):
        self.assertEqual(sc._point_at(self.L, 0.0), (0.0, 0.0))
        self.assertEqual(sc._point_at(self.L, 0.5), (10.0, 0.0))
        self.assertEqual(sc._point_at(self.L, 0.75), (10.0, 5.0))
        self.assertEqual(sc._point_at(self.L, 1.0), (10.0, 10.0))

    def test_the_head_points_along_the_last_segment(self):
        head = sc._head([(0.0, 50.0), (100.0, 50.0)], "#000000")
        tip, a, b = head.points
        self.assertEqual(tip, (100.0, 50.0))
        self.assertEqual({a[0], b[0]}, {92.0})  # base is 8 back
        self.assertEqual({a[1], b[1]}, {47.0, 53.0})  # 6 wide

    def test_a_zero_length_last_segment_does_not_lose_the_direction(self):
        head = sc._head([(0.0, 0.0), (10.0, 0.0), (10.0, 0.0)], "#000000")
        self.assertEqual(head.points[0], (10.0, 0.0))
        self.assertEqual({round(p[0], 6) for p in head.points[1:]}, {2.0})

    def test_a_route_with_no_length_has_no_head(self):
        self.assertIsNone(sc._head([(1.0, 1.0), (1.0, 1.0)], "#000000"))

    def test_soft_tunnel_brackets_are_arcs_and_hard_ones_are_squared(self):
        soft = sc._tunnel([(0.0, 0.0), (100.0, 0.0)], at_start=False, soft=True, color="#000000")
        hard = sc._tunnel([(0.0, 0.0), (100.0, 0.0)], at_start=False, soft=False, color="#000000")
        self.assertEqual([len(b.points) for b in soft], [9, 9])
        self.assertEqual([len(b.points) for b in hard], [4, 4])

    def test_the_brackets_sit_behind_the_end_they_mark(self):
        for b in sc._tunnel([(0.0, 0.0), (100.0, 0.0)], at_start=False, soft=False, color="#000"):
            self.assertTrue(all(p[0] < 100.0 for p in b.points))
        for b in sc._tunnel([(0.0, 0.0), (100.0, 0.0)], at_start=True, soft=False, color="#000"):
            self.assertTrue(all(p[0] > 0.0 for p in b.points))

    def test_dashes_cut_a_line_into_its_visible_pieces(self):
        pieces = render_png._dashes([(0.0, 0.0), (100.0, 0.0)], 10.0, 10.0)
        self.assertEqual(len(pieces), 5)
        for piece in pieces:
            self.assertAlmostEqual(piece[-1][0] - piece[0][0], 10.0)


class _Tiny:
    """The fixture's A0 sheet, built once per test class."""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.TemporaryDirectory()
        path = os.path.join(cls._dir.name, "tiny.rsf")
        fx.tiny_model(path)
        with RsfModel(path) as model:
            cls.diagram = next(d for d in model.diagrams() if d.node == "A0")
        cls.scene = sc.build_scene(cls.diagram)

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def items(self, kind):
        return [i for i in self.scene.items if isinstance(i, kind)]


class TinyScene(_Tiny, unittest.TestCase):
    def test_a_head_goes_wherever_a_segment_ends_on_something(self):
        tips = {head.points[0] for head in self.items(sc.Poly)}
        self.assertEqual(
            tips,
            {(120.0, 100.0),  # on a box
             (300.0, 150.0),  # on a box, after a bend
             (793.0, 100.0),  # on the frame
             (350.0, 120.0),  # on a box top
             (450.0, 150.0),  # on nothing
             (43.0, 200.0)},  # the stand-in segment
        )

    def test_there_is_no_head_where_the_flow_goes_on_at_a_junction(self):
        self.assertNotIn((260.0, 100.0), {head.points[0] for head in self.items(sc.Poly)})

    def test_lines_use_the_sector_colour_and_are_never_thinner_than_a_pixel(self):
        by_start = {l.points[0]: l for l in self.items(sc.Line) if len(l.points) >= 2}
        blue = by_start[(7.0, 100.0)]
        self.assertEqual((blue.color, blue.width), ("#0000ff", 1.5))
        thin = by_start[(220.0, 100.0)]  # stored as 0.5 wide
        self.assertEqual(thin.width, sc.MIN_LINE_WIDTH)

    def test_a_stand_in_segment_is_dashed(self):
        stub = next(l for l in self.items(sc.Line) if l.points == [(7.0, 200.0), (43.0, 200.0)])
        self.assertEqual(stub.dash, (5.0, 3.0))

    def test_names_are_drawn_where_the_file_puts_them_and_not_where_it_hides_them(self):
        texts = {t.text: t for t in self.items(sc.Text)}
        self.assertIn("данные", texts)
        label = texts["данные"]
        self.assertEqual(label.x, 30.0 + 40.0 / 2)  # centred in its box
        self.assertEqual((texts["Нормы;"].size, "Правила" in texts), (10.0, True))
        self.assertNotIn("результат", texts)  # the one with SHOW_TEXT = 0 has no label

    def test_the_zigzag_starts_with_a_dot_on_the_line(self):
        dots = self.items(sc.Dot)
        self.assertEqual(len(dots), 1)
        self.assertEqual((dots[0].x, dots[0].y), (63.5, 100.0))  # half way along 7 -> 120

    def test_free_text_and_the_heading_are_there(self):
        texts = {t.text: t for t in self.items(sc.Text)}
        self.assertEqual(texts["Цель: проверка"].color, "#ff0000")
        self.assertIn("A0: Сделать дело", texts)

    def test_the_canvas_is_the_page_plus_a_strip_for_the_heading(self):
        s = self.scene
        self.assertLessEqual(s.x, 0.0)
        self.assertLessEqual(s.y, -sc.HEADER_HEIGHT)
        self.assertGreaterEqual(s.x + s.width, 800.0)
        self.assertGreaterEqual(s.y + s.height, 437.0 + sc.PAGE_MARGIN)

    def test_the_frame_is_drawn_dashed(self):
        frame = self.items(sc.Rect)[0]
        self.assertEqual((frame.x, frame.y, frame.w, frame.h), (7.0, 7.0, 786.0, 430.0))
        self.assertEqual(frame.dash, (4.0, 3.0))

    def test_a_tunnel_end_is_marked_with_brackets(self):
        soft = [l for l in self.items(sc.Line) if len(l.points) == 9]
        self.assertEqual(len(soft), 2)  # sector 30 ends in a soft tunnel at box C1


class Svg(_Tiny, unittest.TestCase):
    def setUp(self):
        self.svg = render_svg.render_diagram(self.diagram)

    def test_it_is_well_formed_xml_with_the_scene_as_its_viewbox(self):
        dom = xml.dom.minidom.parseString(self.svg.encode("utf-8"))
        root = dom.documentElement
        self.assertEqual(root.tagName, "svg")
        x, y, w, h = (float(v) for v in root.getAttribute("viewBox").split())
        self.assertAlmostEqual(w, self.scene.width, places=1)
        self.assertAlmostEqual(y, self.scene.y, places=1)

    def test_every_head_is_a_polygon_and_the_colours_and_dashes_come_through(self):
        self.assertEqual(self.svg.count("<polygon"), 6)
        self.assertIn('stroke="#0000ff"', self.svg)
        self.assertIn('stroke-dasharray="5 3"', self.svg)
        self.assertIn(">данные</text>", self.svg)

    def test_text_is_escaped(self):
        scene = sc.Scene(0, 0, 50, 20, [sc.Text(1, 10, "a<b & c>d", 10, "#000")])
        text = render_svg.render_scene(scene)
        xml.dom.minidom.parseString(text.encode("utf-8"))
        self.assertIn("a&lt;b &amp; c&gt;d", text)


class Png(_Tiny, unittest.TestCase):
    def setUp(self):
        self.png = render_png.render_diagram_png(self.diagram)
        self.img = Image.open(io.BytesIO(self.png)).convert("RGB")

    def at(self, x, y):
        """Pixel at a model coordinate."""
        k = render_png.SCALE
        return self.img.getpixel((int(round((x - self.scene.x) * k)), int(round((y - self.scene.y) * k))))

    def test_it_is_a_png_of_the_scene_size(self):
        self.assertTrue(self.png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(
            self.img.size,
            (round(self.scene.width * render_png.SCALE), round(self.scene.height * render_png.SCALE)),
        )

    def test_the_arrow_is_drawn_in_its_own_colour_on_a_white_page(self):
        r, g, b = self.at(90.0, 100.0)  # on sector 30, away from its label and zigzag
        self.assertTrue(b > 200 and r < 90 and g < 90, (r, g, b))
        self.assertEqual(self.at(600.0, 400.0), (255, 255, 255))

    def test_boxes_are_filled_with_their_own_colour(self):
        r, g, b = self.at(170.0, 112.0)  # inside C1, away from its name
        self.assertTrue(g > 200 and r < 60 and b < 60, (r, g, b))


@unittest.skipUnless(_paths.MODEL_EXAMPLE, "Ramus sample model not installed")
class RamusSample(unittest.TestCase):
    def test_every_diagram_of_the_sample_renders_to_valid_svg_and_png(self):
        with RsfModel(_paths.MODEL_EXAMPLE) as model:
            for d in model.diagrams():
                xml.dom.minidom.parseString(render_svg.render_diagram(d).encode("utf-8"))
                self.assertTrue(render_png.render_diagram_png(d).startswith(b"\x89PNG"))

    def test_the_sample_keeps_its_red_and_blue_arrows(self):
        with RsfModel(_paths.MODEL_EXAMPLE) as model:
            d = next(x for x in model.diagrams() if x.node == "A0")
            colours = {a.color for a in d.arrows}
        self.assertIn("#ff0000", colours)
        self.assertIn("#0000ff", colours)


if __name__ == "__main__":
    unittest.main()
