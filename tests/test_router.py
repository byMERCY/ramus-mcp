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

    def test_the_point_is_on_the_side(self):
        self.assertEqual(rt.point_on(self.BOX, rt.SIDE_TOP, 150.0), (150.0, 100.0))
        self.assertEqual(rt.point_on(self.BOX, rt.SIDE_RIGHT, 130.0), (220.0, 130.0))


if __name__ == "__main__":
    unittest.main()
