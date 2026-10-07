"""Make the pictures in the README: a demo model built from nothing, step by step, the way an
agent builds one with the server's tools - in Russian and in English.

For each language it writes, under docs/images/<lang>/:

* build.gif         - the model growing, one tool call per frame;
* context.png       - the context diagram A-0;
* decomposition.png - the top decomposition A0;
* form.png          - the same decomposition in the IDEF0 diagram form;
* dfd.png           - a data flow diagram built the same way;
* in-ramus.png      - the same file as the Ramus engine draws it (only with RAMUS_JAVA and
                      RAMUS_MCP_JAR set, see ramus_validate.py).

The model itself is built in a temporary folder and not kept.

Usage:  python tools/make_demo.py
"""

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import idef0_rules as rules  # noqa: E402
from model_editor import ModelEditor  # noqa: E402
from render_png import render_diagram_png  # noqa: E402
from scene import Form  # noqa: E402

# One process, told in two languages.
DEMOS = {
    "ru": {
        "top": "Провести олимпиаду по программированию",
        "project": "Олимпиада",
        "boxes": ["Подготовить задания", "Провести тур", "Проверить решения", "Подвести итоги"],
        "input": "заявки участников", "control": "положение об олимпиаде",
        "mechanism": "жюри", "output": "итоги олимпиады",
        "tasks": "комплект заданий", "solutions": "решения участников", "scores": "оценки",
    },
    "en": {
        "top": "Run a programming contest",
        "project": "Contest",
        "boxes": ["Prepare problems", "Run the round", "Grade solutions", "Announce results"],
        "input": "registrations", "control": "contest rules",
        "mechanism": "jury", "output": "contest results",
        "tasks": "problem set", "solutions": "solutions", "scores": "scores",
    },
}
# A data flow diagram of an online shop, in the same two languages.
DFD = {
    "ru": {
        "top": "Обработать заказ интернет-магазина", "project": "Магазин",
        "processes": ["Принять заказ", "Проверить оплату", "Собрать заказ", "Отгрузить заказ"],
        "externals": ["Клиент", "Банк"], "stores": ["Заказы", "Каталог товаров"],
        "flows": ["заказ", "цены товаров", "новый заказ", "счёт", "запрос оплаты",
                  "подтверждение оплаты", "оплаченный заказ", "собранный заказ",
                  "отметка об отгрузке", "уведомление"],
    },
    "en": {
        "top": "Process an online order", "project": "Shop",
        "processes": ["Take the order", "Check the payment", "Pick the order", "Ship the order"],
        "externals": ["Customer", "Bank"], "stores": ["Orders", "Catalog"],
        "flows": ["order", "prices", "new order", "invoice", "payment request",
                  "payment confirmation", "paid order", "picked order", "shipping mark",
                  "notice"],
    },
}
WIDTH = 960  # of a GIF frame
BAR = 46  # the caption bar above it


def build(lang: str, folder: str, snap) -> str:
    """Build the demo model in ``folder``; ``snap(caption, ed, node)`` is called after each
    step with the call it stands for and the sheet it changed. Returns the file's path."""
    t = DEMOS[lang]
    path = os.path.join(folder, f"demo-{lang}.rsf")
    ed = ModelEditor.create(path, t["top"], author="ramus-mcp", project=t["project"])
    snap(f'create_model("{t["project"]}.rsf", "{t["top"]}")', ed, "A-0")

    def ids():
        model = ed.snapshot()
        return ({d.node: d.parent_id for d in model.diagrams()},
                {a.number: a.element_id for a in model.activities().values()})

    sheets, acts = ids()
    ctx, top = sheets["A-0"], acts["A0"]
    for role in ("input", "control", "mechanism"):
        ed.add_arrow(ctx, {"frame": role}, {"activity": top, "role": role}, t[role])
        snap(f'add_arrow("A-0", frame {role} -> A0, "{t[role]}")', ed, "A-0")
    ed.add_arrow(ctx, {"activity": top}, {"frame": "output"}, t["output"])
    snap(f'add_arrow("A-0", A0 -> frame output, "{t["output"]}")', ed, "A-0")

    for name in t["boxes"]:
        ed.add_activity(top, name)
        snap(f'add_activity("A0", "{name}")', ed, "A0")
    sheets, acts = ids()
    a1, a2, a3, a4 = (acts[f"A{i}"] for i in range(1, 5))

    def arrow(caption, source, target, name=None):
        result = ed.add_arrow(top, source, target, name)
        snap(caption, ed, "A0")
        return result["sector"]

    def segment_of(name):  # a piece of that flow on A0 to fork from
        sheet = next(d for d in ed.snapshot().diagrams() if d.node == "A0")
        return max((a for a in sheet.arrows if a.name == name and a.has_route),
                   key=lambda a: len(a.points)).sector_id

    # What came down from A-0 is drawn from the frame and joined to it: the levels balance.
    arrow(f'add_arrow("A0", frame control -> A1, "{t["control"]}")',
          {"frame": "control"}, {"activity": a1, "role": "control"}, t["control"])
    arrow(f'add_arrow("A0", frame mechanism -> A1, "{t["mechanism"]}")',
          {"frame": "mechanism"}, {"activity": a1, "role": "mechanism"}, t["mechanism"])
    arrow(f'add_arrow("A0", A1 -> A2 control, "{t["tasks"]}")',
          {"activity": a1}, {"activity": a2, "role": "control"}, t["tasks"])
    arrow(f'add_arrow("A0", frame input -> A2, "{t["input"]}")',
          {"frame": "input"}, {"activity": a2, "role": "input"}, t["input"])
    arrow(f'add_arrow("A0", A2 -> A3, "{t["solutions"]}")',
          {"activity": a2}, {"activity": a3, "role": "input"}, t["solutions"])
    arrow(f'add_arrow("A0", fork "{t["control"]}" -> A3 control)',
          {"arrow": segment_of(t["control"])}, {"activity": a3, "role": "control"})
    arrow(f'add_arrow("A0", fork "{t["mechanism"]}" -> A3 mechanism)',
          {"arrow": segment_of(t["mechanism"])}, {"activity": a3, "role": "mechanism"})
    arrow(f'add_arrow("A0", A3 -> A4, "{t["scores"]}")',
          {"activity": a3}, {"activity": a4, "role": "input"}, t["scores"])
    arrow(f'add_arrow("A0", fork "{t["control"]}" -> A4 control)',
          {"arrow": segment_of(t["control"])}, {"activity": a4, "role": "control"})
    arrow(f'add_arrow("A0", A4 -> frame output, "{t["output"]}")',
          {"activity": a4}, {"frame": "output"}, t["output"])
    ed.tidy_sheet(top)
    snap('tidy_sheet("A0")', ed, "A0")
    ed.save()
    found = rules.check(ed.snapshot())
    print(f"{lang}: check_model {rules.summary(found)}", [(f.sheet, f.rule) for f in found])
    return path


def build_dfd(lang: str, folder: str) -> str:
    """The data flow diagram demo: processes down the diagonal, the customer and the bank
    beside them, two data stores below; flows on whichever side suits."""
    t = DFD[lang]
    path = os.path.join(folder, f"dfd-{lang}.rsf")
    ed = ModelEditor.create(path, t["top"], author="ramus-mcp", project=t["project"],
                            notation="dfd")
    top = next(a.element_id for a in ed.snapshot().activities().values() if a.number == "A0")
    a = [ed.add_activity(top, n)["id"] for n in t["processes"]]
    client, bank = (ed.add_activity(top, n, kind="external")["id"] for n in t["externals"])
    orders, catalog = (ed.add_activity(top, n, kind="store")["id"] for n in t["stores"])
    f = iter(t["flows"])
    for source, target in (
            ({"activity": client}, {"activity": a[0]}),
            ({"activity": catalog, "side": "top"}, {"activity": a[0], "side": "bottom"}),
            ({"activity": a[0]}, {"activity": orders, "side": "top"}),
            ({"activity": a[0]}, {"activity": a[1]}),
            ({"activity": a[1]}, {"activity": bank}),
            ({"activity": bank, "side": "bottom"}, {"activity": a[1], "side": "bottom"}),
            ({"activity": a[1]}, {"activity": a[2]}),
            ({"activity": a[2]}, {"activity": a[3]}),
            ({"activity": a[3], "side": "bottom"}, {"activity": orders, "side": "right"}),
            ({"activity": a[3], "side": "top"}, {"activity": client, "side": "top"})):
        ed.add_arrow(top, source, target, next(f))
    ed.tidy_sheet(top)
    ed.save()
    return path


def _font(size: int):
    for name in ("consola.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _frame(png: bytes, caption: str, step: int, total: int) -> Image.Image:
    sheet = Image.open(io.BytesIO(png)).convert("RGB")
    sheet = sheet.resize((WIDTH, round(sheet.height * WIDTH / sheet.width)), Image.LANCZOS)
    frame = Image.new("RGB", (WIDTH, sheet.height + BAR), (255, 255, 255))
    frame.paste(sheet, (0, BAR))
    d = ImageDraw.Draw(frame)
    d.rectangle((0, 0, WIDTH, BAR - 1), fill=(31, 78, 121))
    d.text((14, BAR // 2), caption, fill=(255, 255, 255), font=_font(16), anchor="lm")
    d.text((WIDTH - 14, BAR // 2), f"{step}/{total}", fill=(255, 196, 61), font=_font(15),
           anchor="rm")
    return frame


def _sheet_png(model, node: str) -> bytes:
    d = next(x for x in model.diagrams() if x.node == node)
    return render_diagram_png(d, title=f"{node}: {d.parent_name}")


def _in_ramus(path: str, folder: str) -> None:
    if not (os.environ.get("RAMUS_JAVA") and os.environ.get("RAMUS_MCP_JAR")):
        return
    import ramus_validate as rv

    top = next(d for d in ModelEditor(path).snapshot().diagrams() if d.node == "A0")
    (picture,) = rv.ask(path, [("render_diagram", {"activity": top.parent_id})])
    rv.save_image(picture, os.path.join(folder, "in-ramus.png"))


def social_preview() -> None:
    """docs/images/social.png, 1280 x 640: the card a link to the repository shows (uploaded by
    hand in the repository's settings). The icon, the name and a line on the left; the demo's
    decomposition on the right."""
    out = os.path.join(ROOT, "docs", "images", "social.png")
    card = Image.new("RGB", (1280, 640), (31, 78, 121))
    d = ImageDraw.Draw(card)
    icon = Image.open(os.path.join(ROOT, "icon.png")).convert("RGBA").resize((150, 150))
    card.paste(icon, (70, 120), icon)

    def font(size):
        for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    d.text((70, 310), "ramus-mcp", fill=(255, 255, 255), font=font(68))
    d.text((72, 400), "Claude's eyes and hands", fill=(255, 196, 61), font=font(34))
    d.text((72, 445), "for Ramus IDEF0 models", fill=(255, 196, 61), font=font(34))
    d.text((72, 520), "read · see · check · edit · create", fill=(210, 225, 240), font=font(26))
    sheet = Image.open(os.path.join(ROOT, "docs", "images", "en", "decomposition.png"))
    sheet = sheet.convert("RGB").resize((700, round(sheet.height * 700 / sheet.width)),
                                        Image.LANCZOS)
    x, y = 530, (640 - sheet.height) // 2
    d.rounded_rectangle((x - 12, y - 12, x + sheet.width + 12, y + sheet.height + 12), 16,
                        fill=(255, 255, 255))
    card.paste(sheet, (x, y))
    card.save(out, optimize=True)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for lang in DEMOS:
            folder = os.path.join(ROOT, "docs", "images", lang)
            os.makedirs(folder, exist_ok=True)
            shots = []
            path = build(lang, tmp, lambda caption, ed, node:
                         shots.append((caption, _sheet_png(ed.snapshot(), node))))
            frames = [_frame(png, caption, i + 1, len(shots))
                      for i, (caption, png) in enumerate(shots)]
            frames[0].save(os.path.join(folder, "build.gif"), save_all=True,
                           append_images=frames[1:], loop=0, optimize=True,
                           duration=[1200] * (len(frames) - 1) + [4500])
            model = ModelEditor(path).snapshot()
            for node, name in (("A-0", "context.png"), ("A0", "decomposition.png")):
                with open(os.path.join(folder, name), "wb") as fh:
                    fh.write(_sheet_png(model, node))
            top = next(d for d in model.diagrams() if d.node == "A0")
            with open(os.path.join(folder, "form.png"), "wb") as fh:
                fh.write(render_diagram_png(top, form=Form(**model.form_of(top))))
            dfd = ModelEditor(build_dfd(lang, tmp)).snapshot()
            with open(os.path.join(folder, "dfd.png"), "wb") as fh:
                fh.write(_sheet_png(dfd, "A0"))
            _in_ramus(path, folder)
            sizes = {f: os.path.getsize(os.path.join(folder, f)) // 1024
                     for f in sorted(os.listdir(folder))}
            print("wrote", folder, sizes)
    social_preview()


if __name__ == "__main__":
    main()
