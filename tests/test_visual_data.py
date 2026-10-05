import unittest

import _paths  # noqa: F401  (puts src/ on the path)
from rsf_builder import Writer, mask, stroke_font_color

import visual_data as vd
from ramus_rsf import RsfModel


class Unmask(unittest.TestCase):
    def test_a_zero_byte_is_80_and_ff_is_7f(self):
        self.assertEqual(vd.unmask("80817F"), b"\x00\x01\xff")

    def test_is_the_inverse_of_masking(self):
        data = bytes(range(256))
        self.assertEqual(vd.unmask(mask(data)), data)

    def test_rejects_text_that_is_not_hex(self):
        with self.assertRaises(vd.BlobError):
            vd.unmask("not hex")


class SectorStyle(unittest.TestCase):
    def test_reads_stroke_font_and_colour(self):
        style = vd.decode_sector_style(stroke_font_color(1.5, (0, 0, 255), ("Dialog", 10, 0)))
        self.assertEqual(style.stroke.width, 1.5)
        self.assertEqual(style.stroke.cap, 2)
        self.assertIsNone(style.stroke.dash)
        self.assertEqual((style.font.name, style.font.size, style.font.style), ("Dialog", 10, 0))
        self.assertEqual(style.color.hex(), "#0000ff")

    def test_a_real_default_style_from_a_ramus_file(self):
        # The VISUAL_ATTRIBUTES of an ordinary black arrow, unmasked: 1.5-wide stroke,
        # Dialog 10, black. Copied from a sample model.
        raw = bytes.fromhex(
            "01" "000000000000f83f" "02000000" "00000000" "0000000000000000" "0000000000002440"
            "ffffffff" "00" "01" "06000000" "4469616c6f67" "0a000000" "00000000"
            "01" "00000000" "00000000" "00000000"
        )
        style = vd.decode_sector_style(raw)
        self.assertEqual(style.stroke.width, 1.5)
        self.assertEqual(style.stroke.miter_limit, 10.0)
        self.assertEqual(style.font.name, "Dialog")
        self.assertEqual(style.color.hex(), "#000000")

    def test_a_dashed_stroke_keeps_its_pattern(self):
        w = Writer().flag(True).f64(1.0).i32(0).i32(0).f64(0.0).f64(10.0).i32(2).f64(4.0).f64(2.0)
        w.flag(True)  # font: null
        w.flag(True).i32(1).i32(2).i32(3)
        style = vd.decode_sector_style(w.bytes())
        self.assertEqual(style.stroke.dash, (4.0, 2.0))
        self.assertIsNone(style.font)
        self.assertEqual((style.color.r, style.color.g, style.color.b), (1, 2, 3))

    def test_the_decorated_strokes_are_stored_as_negative_codes(self):
        w = Writer().flag(False).i32(-21)
        w.flag(True)  # font: null
        w.flag(True).i32(0).i32(0).i32(0)
        stroke = vd.decode_sector_style(w.bytes()).stroke
        self.assertEqual((stroke.kind, stroke.level), ("arrowed", 1))

    def test_no_payload_or_a_broken_one_means_defaults(self):
        for data in (b"", b"\x01", b"\x01" + b"\x00" * 5):
            style = vd.decode_sector_style(data)
            self.assertEqual((style.stroke, style.font, style.color), (None, None, None))


class DiagramBlobWithOnlyTexts(unittest.TestCase):
    """Version 2: the routes moved to tables, the blob keeps the free text labels."""

    def test_reads_texts_and_resolves_back_references(self):
        w = Writer().i32(2).i32(2)
        w.flag(False).flag(True).string("Arial").i32(10).i32(1)  # font, new (#0)
        w.flag(True).i32(255).i32(0).i32(0)  # colour, new (#0)
        w.f64(1.0).f64(2.0).f64(3.0).f64(4.0).string("one")
        w.flag(False).flag(False).i32(0)  # the same font, by number
        w.flag(False).i32(0)  # the same colour, by number
        w.f64(5.0).f64(6.0).f64(7.0).f64(8.0).string(None)
        blob = vd.decode_diagram_blob(w.bytes(), strict=True)
        self.assertEqual(blob.version, 2)
        self.assertEqual(blob.sectors, [])
        first, second = blob.texts
        self.assertEqual((first.x, first.y, first.width, first.height, first.text), (1, 2, 3, 4, "one"))
        self.assertEqual(first.font, vd.Font("Arial", 10, 1))
        self.assertEqual(second.font, first.font)
        self.assertEqual(second.color, vd.Color(255, 0, 0))
        self.assertIsNone(second.text)

    def test_no_data_at_all_is_an_empty_diagram(self):
        for data in (b"", b"\x02\x00"):
            blob = vd.decode_diagram_blob(data)
            self.assertEqual((blob.version, blob.sectors, blob.texts), (0, [], []))

    def test_a_back_reference_to_nothing_is_an_error(self):
        w = Writer().i32(2).i32(1).flag(False).flag(False).i32(3)
        with self.assertRaises(vd.BlobError):
            vd.decode_diagram_blob(w.bytes())

    def test_truncation_is_an_error(self):
        data = Writer().i32(2).i32(1).flag(False).bytes()
        with self.assertRaises(vd.BlobError):
            vd.decode_diagram_blob(data)

    def test_strict_mode_rejects_leftover_bytes(self):
        data = Writer().i32(2).i32(0).raw(b"\x00").bytes()
        self.assertEqual(vd.decode_diagram_blob(data).texts, [])
        with self.assertRaises(vd.BlobError):
            vd.decode_diagram_blob(data, strict=True)


def _v1_blob(show_tilda=True, with_label=True) -> bytes:
    """One sector, three points: (10,20) -> (10,50) -> (90,50). The second point shares its x
    with the first and the third shares its y with the second, by back-reference."""
    w = Writer().i32(1).i32(1)  # version 1, one sector
    w.string(None).string("")  # two legacy strings
    w.flag(show_tilda).i32(3)
    # point 0: new x (axis 0) = 10, new y (axis 1) = 20
    w.flag(True).i32(0).f64(10.0).flag(True).i32(1).f64(20.0).i32(-1).i32(-1).i32(-1)
    # point 1: x = ordinate #0 again; y new = 50. It also carries an old function reference.
    w.flag(False).i32(0).flag(True).i32(1).f64(50.0).i32(1).i32(-1).i32(0).i32(5).i32(2)
    # point 2: x new = 90; y = ordinate #2 (the 50)
    w.flag(True).i32(0).f64(90.0).flag(False).i32(2).i32(-1).i32(-1).i32(-1)
    w.i32(0).i32(77)  # the sector's own id
    if with_label:
        w.flag(False).f64(30.0).f64(40.0).f64(60.0).f64(10.0).flag(True)
        if show_tilda:
            w.f64(0.25)
    else:
        w.flag(True)
    w.i32(0)  # no free texts
    return w.bytes()


class DiagramBlobWithRoutes(unittest.TestCase):
    """Version 1: every arrow's route is inline."""

    def test_reads_a_route_with_shared_coordinates(self):
        blob = vd.decode_diagram_blob(_v1_blob(), strict=True)
        (sector,) = blob.sectors
        self.assertEqual(sector.sector_id, 77)
        self.assertEqual([(p.x, p.y) for p in sector.points], [(10, 20), (10, 50), (90, 50)])
        self.assertEqual([p.kind for p in sector.points], [-1, 1, -1])

    def test_reads_the_label_and_where_the_zigzag_joins(self):
        label = vd.decode_diagram_blob(_v1_blob()).sectors[0].label
        self.assertEqual((label.x, label.y, label.width, label.height), (30, 40, 60, 10))
        self.assertTrue(label.transparent)
        self.assertEqual(label.tilde_pos, 0.25)

    def test_a_label_without_a_zigzag_has_no_position_on_the_line(self):
        label = vd.decode_diagram_blob(_v1_blob(show_tilda=False), strict=True).sectors[0].label
        self.assertIsNone(label.tilde_pos)

    def test_a_sector_can_have_no_label(self):
        blob = vd.decode_diagram_blob(_v1_blob(with_label=False), strict=True)
        self.assertIsNone(blob.sectors[0].label)

    def test_an_implausible_count_is_an_error(self):
        data = Writer().i32(1).i32(1_000_000).bytes()
        with self.assertRaises(vd.BlobError):
            vd.decode_diagram_blob(data)


@unittest.skipUnless(_paths.MODEL_EXAMPLE and _paths.ENTERPRISE, "Ramus sample models not installed")
class RealBlobs(unittest.TestCase):
    def test_every_diagram_blob_in_the_samples_is_understood_to_the_last_byte(self):
        for path in (_paths.MODEL_EXAMPLE, _paths.ENTERPRISE):
            with RsfModel(path) as model:
                rows = model.table("IDEF0/attribute_visual_datas")
                self.assertTrue(rows)
                for row in rows:
                    blob = vd.decode_diagram_blob(vd.unmask(row["DATA"]), strict=True)
                    self.assertEqual(blob.version, 1)

    def test_the_sector_ids_in_a_blob_are_the_sectors_of_that_diagram(self):
        with RsfModel(_paths.MODEL_EXAMPLE) as model:
            owners = model._links("F_FUNCTION_SECTOR")
            for row in model.table("IDEF0/attribute_visual_datas"):
                blob = vd.decode_diagram_blob(vd.unmask(row["DATA"]))
                for s in blob.sectors:
                    self.assertEqual(owners[s.sector_id], int(row["ELEMENT_ID"]))


if __name__ == "__main__":
    unittest.main()
