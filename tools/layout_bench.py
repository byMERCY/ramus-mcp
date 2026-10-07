"""Measure the layout code: build each scenario sheet from nothing (tests/layout_scenarios.py),
score it as drawn and after tidy_sheet (src/layout_quality.py), and time both.

    python tools/layout_bench.py [--png DIR] [--models DIR] [scenario ...]

``--png`` writes a picture of every sheet, as drawn and tidied, into DIR. ``--models`` also
measures the sheets of every .rsf model in DIR (and tidies a copy in memory - nothing is
written): your own models, which stay out of the repository. Lower scores are better; compare
two runs of this, before and after a change, rather than reading one score on its own.
"""

import argparse
import glob
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import layout_quality as lq  # noqa: E402
from layout_scenarios import SCENARIOS  # noqa: E402
from model_editor import ModelEditor  # noqa: E402
from render_png import render_diagram_png  # noqa: E402


def _sheets(ed):
    return [d for d in ed.snapshot().diagrams() if d.node != "A-0" or len(d.arrows) > 0]


def _line(name, before, after, built_s, tidy_s):
    counts = ", ".join(f"{k} {v}" for k, v in after.counts().items()) or "-"
    built = f"{built_s:6.2f}s" if built_s is not None else "      -"
    print(f"{name:<28} {before.score:8.1f} {after.score:8.1f} {built} {tidy_s:6.2f}s  {counts}")


def _png(folder, name, diagram):
    if folder:
        with open(os.path.join(folder, name + ".png"), "wb") as f:
            f.write(render_diagram_png(diagram))


def run_scenario(name, build, png_dir):
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.perf_counter()
        ed = build(os.path.join(tmp, name + ".rsf"))
        built_s = time.perf_counter() - t0
        top = next(d for d in ed.snapshot().diagrams() if d.node == "A0")
        before = lq.assess_diagram(top)
        _png(png_dir, f"{name}-drawn", top)
        t0 = time.perf_counter()
        ed.tidy_sheet(top.parent_id)
        tidy_s = time.perf_counter() - t0
        top = next(d for d in ed.snapshot().diagrams() if d.node == "A0")
        after = lq.assess_diagram(top)
        _png(png_dir, f"{name}-tidy", top)
        _line(name, before, after, built_s, tidy_s)
        return before.score, after.score


def run_models(folder, png_dir):
    total = [0.0, 0.0]
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.rsf"), recursive=True)):
        if ".backup" in os.path.basename(path):
            continue
        try:
            ed = ModelEditor(path)
            sheets = _sheets(ed)
        except Exception as e:  # a file this cannot read is reported, not fatal
            print(f"{os.path.basename(path)}: {e}")
            continue
        for d in sheets:
            label = f"{os.path.splitext(os.path.basename(path))[0][:20]}:{d.node}"
            before = lq.assess_diagram(d)
            t0 = time.perf_counter()
            try:
                ed.tidy_sheet(d.parent_id)
            except Exception as e:
                print(f"{label}: tidy_sheet failed: {e}")
                continue
            tidy_s = time.perf_counter() - t0
            after = lq.assess_diagram(next(x for x in ed.snapshot().diagrams()
                                           if x.parent_id == d.parent_id))
            _line(label, before, after, None, tidy_s)
            total[0] += before.score
            total[1] += after.score
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    parser.add_argument("--png", help="write pictures of each sheet here")
    parser.add_argument("--models", help="also measure the .rsf models in this folder")
    args = parser.parse_args()
    if args.png:
        os.makedirs(args.png, exist_ok=True)
    print(f"{'sheet':<28} {'drawn':>8} {'tidied':>8} {'build':>7} {'tidy':>7}  faults left")
    totals = [0.0, 0.0]
    for name in args.scenarios:
        b, a = run_scenario(name, SCENARIOS[name], args.png)
        totals[0] += b
        totals[1] += a
    print(f"{'scenarios total':<28} {totals[0]:8.1f} {totals[1]:8.1f}")
    if args.models:
        m = run_models(args.models, args.png)
        print(f"{'models total':<28} {m[0]:8.1f} {m[1]:8.1f}")


if __name__ == "__main__":
    main()
