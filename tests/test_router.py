import unittest

import _paths  # noqa: F401  (puts src/ on the path)

import router as rt

SHEET = (7.0, 7.0, 786.0, 430.0)


def _pieces(points):
    return list(zip(points, points[1:]))


def _orthogonal(points):
    return all(a[0] == b[0] or a[1] == b[1] for a, b in _pieces(points))


def _through(points, box):
    return any(rt._crosses_rect(a, b, box) for a, b in _pieces(points))


class Routing(unittest.TestCase):

    def test_two_ends_level_with_each_other_get_a_straight_line(self):
        pts = rt.route((100, 50), rt.RIGHT, (200, 50), rt.RIGHT, [], SHEET)
        self.assertEqual(pts, [(100, 50), (200, 50)])

    def test_a_box_in_the_way_is_gone_round_not_through(self):
        box = (120.0, 30.0, 60.0, 40.0)
        pts = rt.route((60, 50), rt.RIGHT, (300, 50), rt.RIGHT, [box], SHEET)
        self.assertTrue(_orthogonal(pts))
        self.assertFalse(_through(pts, box))
        self.assertEqual((pts[0], pts[-1]), ((60, 50), (300, 50)))

    def test_a_control_is_reached_from_above_with_one_bend(self):
        b = (220.0, 150.0, 80.0, 50.0)
        pts = rt.route((100, 60), rt.RIGHT, (260, 150), rt.DOWN, [b], SHEET)
        self.assertEqual(pts, [(100, 60), (260, 60), (260, 150)])

    def test_an_arrow_back_to_an_earlier_box_goes_round_both(self):
        a = (60.0, 40.0, 80.0, 50.0)   # target, on the left
        b = (300.0, 40.0, 80.0, 50.0)  # source, on the right
        pts = rt.route((380, 65), rt.RIGHT, (60, 65), rt.RIGHT, [a, b], SHEET)
        self.assertTrue(_orthogonal(pts))
        self.assertFalse(_through(pts, a) or _through(pts, b))
        self.assertGreater(pts[1][0], 380)       # first leaves rightwards
        self.assertLess(pts[-2][0], 60)          # last arrives moving right

    def test_it_will_not_draw_on_top_of_an_arrow_already_there(self):
        existing = [((100.0, 50.0), (300.0, 50.0))]
        box = (150.0, 70.0, 60.0, 40.0)
        pts = rt.route((100, 50), rt.RIGHT, (300, 50), rt.RIGHT, [box], SHEET, existing)
        on_top = any(a[1] == b[1] == 50.0 and abs(a[0] - b[0]) > 30 for a, b in _pieces(pts))
        self.assertFalse(on_top)

    def test_a_narrow_gap_between_boxes_is_used_without_a_jog(self):
        # Two boxes 22 apart: the fixed 14-unit stubs of old overlapped there and the route
        # had to step back on itself. It should drop straight down the middle of the gap.
        a = (501.0, 213.0, 72.0, 51.0)
        b = (595.0, 295.0, 72.0, 51.0)
        pts = rt.route((573, 238.5), rt.RIGHT, (595, 320.5), rt.RIGHT, [a, b], SHEET)
        self.assertEqual(pts, [(573, 238.5), (584, 238.5), (584, 320.5), (595, 320.5)])

    def test_the_turn_is_made_halfway_when_nothing_says_otherwise(self):
        pts = rt.route((100, 50), rt.RIGHT, (200, 120), rt.RIGHT, [], SHEET)
        self.assertEqual(pts, [(100, 50), (150, 50), (150, 120), (200, 120)])

    def test_simplify_merges_straight_runs(self):
        self.assertEqual(rt.simplify([(0, 0), (5, 0), (10, 0), (10, 0), (10, 5)]),
                         [(0, 0), (10, 0), (10, 5)])


class Attaching(unittest.TestCase):
    BOX = (100.0, 100.0, 120.0, 60.0)

    def test_a_free_side_takes_its_middle(self):
        self.assertEqual(rt.attach(self.BOX, rt.SIDE_LEFT, []), 130.0)

    def test_a_preferred_spot_wins_when_free(self):
        self.assertEqual(rt.attach(self.BOX, rt.SIDE_LEFT, [], prefer=120.0), 120.0)

    def test_taken_spots_are_left_alone(self):
        c = rt.attach(self.BOX, rt.SIDE_LEFT, [130.0], prefer=131.0)
        self.assertGreaterEqual(abs(c - 130.0), 12.0)
        self.assertTrue(108.0 <= c <= 152.0)

    def test_a_crowded_side_still_keeps_as_far_from_the_others_as_it_can(self):
        # A side with no evenly spaced spot left: the new end must not land on a taken one
        # (it used to fall back to the middle, right on top of an arrow there).
        box = (501.0, 213.0, 72.0, 51.0)
        c = rt.attach(box, rt.SIDE_RIGHT, [237.46, 250.44])
        self.assertEqual(c, 221.0)  # the far end of the free stretch, 16 clear

    def test_several_preferences_are_tried_in_order(self):
        self.assertEqual(rt.attach(self.BOX, rt.SIDE_LEFT, [120.0], prefer=[121.0, 145.0]), 145.0)

    def test_the_point_is_on_the_side(self):
        self.assertEqual(rt.point_on(self.BOX, rt.SIDE_TOP, 150.0), (150.0, 100.0))
        self.assertEqual(rt.point_on(self.BOX, rt.SIDE_RIGHT, 130.0), (220.0, 130.0))


if __name__ == "__main__":
    unittest.main()
