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

    def test_between_two_arrows_it_runs_down_the_middle_not_hugging_one(self):
        # The only line the boxes offer here is just under the lower arrow; the middle of the
        # corridor between the two arrows is the place for a third.
        box = (150.0, 150.0, 100.0, 50.0)
        existing = [((100.0, 80.0), (500.0, 80.0)), ((50.0, 135.0), (350.0, 135.0))]
        pts = rt.route((400, 300), rt.UP, (200, 150), rt.DOWN, [box], SHEET, existing)
        level = [a[1] for a, b in _pieces(pts) if a[1] == b[1]]
        self.assertEqual(len(level), 1)
        self.assertTrue(all(abs(level[0] - y) >= rt.NEAR for y in (80.0, 135.0)), pts)

    def test_running_beside_an_arrow_costs_more_the_closer_it_is(self):
        self.assertGreater(rt._alongside(3.0, 10.0), rt._alongside(12.0, 10.0))
        self.assertGreater(rt._alongside(12.0, 10.0), 0.0)
        self.assertAlmostEqual(rt._alongside(rt.NEAR, 10.0), 0.0)
        self.assertGreater(rt._alongside(1.0, 10.0), rt._alongside(rt.ON_TOP, 10.0))

    def test_the_indexed_costs_are_the_plain_ones(self):
        import random
        rnd = random.Random(7)
        grid = [10.0 * k for k in range(30)]

        def piece():
            a = (rnd.choice(grid), rnd.choice(grid))
            if rnd.random() < 0.5:
                return a, (rnd.choice(grid), a[1])
            return a, (a[0], rnd.choice(grid))

        lines = [piece() for _ in range(40)] + [((0.0, 0.0), (50.0, 30.0))]  # one skewed
        labels = [(rnd.choice(grid), rnd.choice(grid), 40.0, 12.0) for _ in range(8)]
        costs = rt.Costs(lines, labels)
        for _ in range(500):
            a, b = piece()
            if a == b:
                continue
            plain = rt._line_cost(a, b, lines) + rt._label_cost(a, b, labels)
            self.assertAlmostEqual(costs.piece(a, b), plain, msg=(a, b))
            self.assertAlmostEqual(costs.piece(b, a), plain, msg=(b, a))

    def test_least_cost_counts_the_turns_no_route_can_avoid(self):
        self.assertEqual(rt.least_cost((0, 0), rt.RIGHT, (50, 0), rt.RIGHT), 50)
        self.assertEqual(rt.least_cost((0, 0), rt.RIGHT, (50, 20), rt.DOWN), 70 + rt.BEND)
        self.assertEqual(rt.least_cost((0, 0), rt.RIGHT, (50, 20), rt.RIGHT), 70 + 2 * rt.BEND)
        self.assertEqual(rt.least_cost((0, 0), rt.RIGHT, (-50, 0), rt.RIGHT), 50 + 2 * rt.BEND)
        self.assertEqual(rt.least_cost((0, 0), rt.LEFT, (50, 0), rt.RIGHT), 50 + 2 * rt.BEND)
        a, b = (60.0, 40.0, 80.0, 50.0), (300.0, 40.0, 80.0, 50.0)
        pts = rt.route((380, 65), rt.RIGHT, (60, 65), rt.RIGHT, [a, b], SHEET)
        self.assertGreaterEqual(rt.route_cost(pts), rt.least_cost(pts[0], rt.RIGHT, pts[-1],
                                                                  rt.RIGHT))

    def test_simplify_merges_straight_runs(self):
        self.assertEqual(rt.simplify([(0, 0), (5, 0), (10, 0), (10, 0), (10, 5)]),
                         [(0, 0), (10, 0), (10, 5)])


class ManyEnds(unittest.TestCase):
    """One search over every start and end does at least as well as routing each pair."""

    def test_the_best_pair_is_found_in_one_search(self):
        import random
        rnd = random.Random(7)
        for trial in range(12):
            boxes = []
            for i in range(4):
                boxes.append((80.0 + 150 * i + rnd.uniform(-10, 10),
                              60.0 + 80 * i + rnd.uniform(-10, 10), 100.0, 50.0))
            a, b = boxes[0], boxes[-1]
            starts = [((a[0] + a[2], a[1] + f * a[3]), rt.RIGHT, rnd.uniform(0, 20))
                      for f in (0.25, 0.5, 0.75)]
            ends = [((b[0], b[1] + f * b[3]), rt.RIGHT, rnd.uniform(0, 20))
                    for f in (0.3, 0.5, 0.7)]
            lines = [((300.0, 20.0), (300.0, 400.0)), ((40.0, 200.0), (700.0, 200.0))]
            costs = rt.Costs(lines)
            found = rt.route_many(starts, ends, boxes, SHEET, lines, costs=costs)
            many = rt.route_cost(found.points, costs=costs) + found.price
            pairs = min(rt.route_cost(rt.route(sp, sd, ep, ed, boxes, SHEET, lines,
                                                costs=costs), costs=costs) + s_price + e_price
                        for sp, sd, s_price in starts for ep, ed, e_price in ends)
            self.assertLessEqual(many, pairs + 1.0, trial)
            self.assertTrue(_orthogonal(found.points))
            for r in boxes[1:-1]:
                self.assertFalse(_through(found.points, r))

    def test_a_single_pair_is_routed_as_before(self):
        box = (120.0, 30.0, 60.0, 40.0)
        one = rt.route((60, 50), rt.RIGHT, (300, 50), rt.RIGHT, [box], SHEET)
        many = rt.route_many([((60, 50), rt.RIGHT, 0.0)], [((300, 50), rt.RIGHT, 0.0)], [box],
                             SHEET)
        self.assertEqual(one, many.points)


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
