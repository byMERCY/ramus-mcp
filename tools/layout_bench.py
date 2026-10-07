"""Measure the layout code: build each scenario sheet from nothing (tests/layout_scenarios.py),
score it as drawn, after tidy_sheet and after layout_sheet (src/layout_quality.py), and time
each.

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


def _line(name, before, after, laid, built_s, tidy_s, laid_s):
    counts = ", ".join(f"{k} {v}" for k, v in laid.counts().items()) or "-"
    built = f"{built_s:6.2f}s" if built_s is not None else "      -"
    print(f"{name:<24} {before.score:7.1f} {after.score:7.1f} {laid.score:7.1f} {built} "
          f"{tidy_s:6.2f}s {laid_s:6.2f}s  {counts}")


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
        saved = ed.doc.checkpoint()
        t0 = time.perf_counter()
        ed.tidy_sheet(top.parent_id)
        tidy_s = time.perf_counter() - t0
        tidied = next(d for d in ed.snapshot().diagrams() if d.node == "A0")
        after = lq.assess_diagram(tidied)
        _png(png_dir, f"{name}-tidy", tidied)
        ed.doc.restore(saved)
        t0 = time.perf_counter()
        ed.layout_sheet(top.parent_id)
        laid_s = time.perf_counter() - t0
        laid_out = next(d for d in ed.snapshot().diagrams() if d.node == "A0")
        laid = lq.assess_diagram(laid_out)
        _png(png_dir, f"{name}-laid", laid_out)
        _line(name, before, after, laid, built_s, tidy_s, laid_s)
        return before.score, after.score, laid.score


def run_models(folder, png_dir):
    total = [0.0, 0.0, 0.0]
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
            saved = ed.doc.checkpoint()
            try:
                t0 = time.perf_counter()
                ed.tidy_sheet(d.parent_id)
                tidy_s = time.perf_counter() - t0
                after = lq.assess_diagram(next(x for x in ed.snapshot().diagrams()
                                               if x.parent_id == d.parent_id))
                ed.doc.restore(saved)
                t0 = time.perf_counter()
                ed.layout_sheet(d.parent_id)
                laid_s = time.perf_counter() - t0
                laid_out = next(x for x in ed.snapshot().diagrams() if x.parent_id == d.parent_id)
                laid = lq.assess_diagram(laid_out)
                ed.doc.restore(saved)
            except Exception as e:
                print(f"{label}: failed: {e}")
                ed.doc.restore(saved)
                continue
            _png(png_dir, label.replace(":", "-") + "-laid", laid_out)
            _line(label, before, after, laid, None, tidy_s, laid_s)
            for i, v in enumerate((before.score, after.score, laid.score)):
                total[i] += v
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    parser.add_argument("--png", help="write pictures of each sheet here")
    parser.add_argument("--models", help="also measure the .rsf models in this folder")
    args = parser.parse_args()
    if args.png:
        os.makedirs(args.png, exist_ok=True)
    print(f"{'sheet':<24} {'drawn':>7} {'tidied':>7} {'laid':>7} {'build':>7} {'tidy':>7} "
          f"{'layout':>7}  faults left after layout_sheet")
    totals = [0.0, 0.0, 0.0]
    for name in args.scenarios:
        for i, v in enumerate(run_scenario(name, SCENARIOS[name], args.png)):
            totals[i] += v
    print(f"{'scenarios total':<24} {totals[0]:7.1f} {totals[1]:7.1f} {totals[2]:7.1f}")
    if args.models:
        m = run_models(args.models, args.png)
        print(f"{'models total':<24} {m[0]:7.1f} {m[1]:7.1f} {m[2]:7.1f}")


if __name__ == "__main__":
    main()
