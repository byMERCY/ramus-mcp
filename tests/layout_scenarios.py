"""Sheets to measure layout on: each built from nothing the way an agent builds one, call by
call, so the layout code has to place every box and route every arrow itself.

Used by the tests (a tidied sheet must not read worse than it was drawn) and by
``tools/layout_bench.py`` (scores, timings and pictures, to compare one version of the layout
code with another).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from model_editor import ModelEditor  # noqa: E402


class _Build:
    """A model being built, with the ids an agent would look up as it goes."""

    def __init__(self, path: str, top: str):
        self.ed = ModelEditor.create(path, top, author="ramus-mcp", project="bench")
        sheets, acts = self.ids()
        self.ctx, self.top = sheets["A-0"], acts["A0"]

    def ids(self):
        model = self.ed.snapshot()
        return ({d.node: d.parent_id for d in model.diagrams()},
                {a.number: a.element_id for a in model.activities().values()})

    def context(self, inputs=(), controls=(), mechanisms=(), outputs=()):
        for role, names in (("input", inputs), ("control", controls),
                            ("mechanism", mechanisms)):
            for n in names:
                self.ed.add_arrow(self.ctx, {"frame": role}, {"activity": self.top, "role": role},
                                  n)
        for n in outputs:
            self.ed.add_arrow(self.ctx, {"activity": self.top}, {"frame": "output"}, n)

    def boxes(self, names):
        for n in names:
            self.ed.add_activity(self.top, n)
        _, acts = self.ids()
        return {i: acts[f"A{i}"] for i in range(1, len(names) + 1)}

    def segment(self, name: str) -> int:
        sheet = next(d for d in self.ed.snapshot().diagrams() if d.parent_id == self.top)
        return max((a for a in sheet.arrows if a.name == name and a.has_route),
                   key=lambda a: len(a.points)).sector_id

    def arrow(self, source, target, name=None):
        return self.ed.add_arrow(self.top, source, target, name)

    def fork(self, name, target):
        return self.arrow({"arrow": self.segment(name)}, target)


def _box(a, role):
    return {"activity": a, "role": role}


def olympiad(path: str) -> ModelEditor:
    """The README's demo: four boxes down the diagonal, a control and a mechanism forked."""
    b = _Build(path, "Провести олимпиаду по программированию")
    b.context(["заявки участников"], ["положение об олимпиаде"], ["жюри"], ["итоги олимпиады"])
    a = b.boxes(["Подготовить задания", "Провести тур", "Проверить решения", "Подвести итоги"])
    b.arrow({"frame": "control"}, _box(a[1], "control"), "положение об олимпиаде")
    b.arrow({"frame": "mechanism"}, _box(a[1], "mechanism"), "жюри")
    b.arrow({"activity": a[1]}, _box(a[2], "control"), "комплект заданий")
    b.arrow({"frame": "input"}, _box(a[2], "input"), "заявки участников")
    b.arrow({"activity": a[2]}, _box(a[3], "input"), "решения участников")
    b.fork("положение об олимпиаде", _box(a[3], "control"))
    b.fork("жюри", _box(a[3], "mechanism"))
    b.arrow({"activity": a[3]}, _box(a[4], "input"), "оценки")
    b.fork("положение об олимпиаде", _box(a[4], "control"))
    b.arrow({"activity": a[4]}, {"frame": "output"}, "итоги олимпиады")
    return b.ed


def order(path: str) -> ModelEditor:
    """Five boxes - one more than the diagonal fits at the usual size - and two feedback loops,
    one into a control and one into an input."""
    b = _Build(path, "Выполнить заказ клиента")
    b.context(["заказ"], ["регламент"], ["персонал"], ["выполненный заказ"])
    a = b.boxes(["Принять заказ", "Спланировать производство", "Изготовить изделие",
                 "Проверить качество", "Отгрузить заказ"])
    b.arrow({"frame": "input"}, _box(a[1], "input"), "заказ")
    b.arrow({"frame": "control"}, _box(a[1], "control"), "регламент")
    b.arrow({"frame": "mechanism"}, _box(a[1], "mechanism"), "персонал")
    b.arrow({"activity": a[1]}, _box(a[2], "input"), "принятый заказ")
    b.arrow({"activity": a[2]}, _box(a[3], "control"), "план производства")
    b.arrow({"activity": a[3]}, _box(a[4], "input"), "изделие")
    b.arrow({"activity": a[4]}, _box(a[5], "input"), "годное изделие")
    b.arrow({"activity": a[5]}, {"frame": "output"}, "выполненный заказ")
    b.arrow({"activity": a[4]}, _box(a[3], "control"), "брак")
    b.arrow({"activity": a[5]}, _box(a[2], "control"), "отчёт об отгрузке")
    b.fork("регламент", _box(a[4], "control"))
    b.fork("регламент", _box(a[5], "control"))
    b.fork("персонал", _box(a[3], "mechanism"))
    b.fork("персонал", _box(a[5], "mechanism"))
    return b.ed


def software(path: str) -> ModelEditor:
    """Six boxes, the most IDEF0 allows on a sheet; feedback into an input and into a control,
    a standard and a team forked to most of the boxes."""
    b = _Build(path, "Разработать программный продукт")
    b.context(["потребность заказчика"], ["стандарты"], ["команда"], ["работающий продукт"])
    a = b.boxes(["Собрать требования", "Спроектировать систему", "Написать код",
                 "Протестировать", "Внедрить", "Сопровождать"])
    b.arrow({"frame": "input"}, _box(a[1], "input"), "потребность заказчика")
    b.arrow({"frame": "control"}, _box(a[1], "control"), "стандарты")
    b.arrow({"frame": "mechanism"}, _box(a[1], "mechanism"), "команда")
    b.arrow({"activity": a[1]}, _box(a[2], "input"), "требования")
    b.arrow({"activity": a[2]}, _box(a[3], "control"), "проект")
    b.arrow({"activity": a[3]}, _box(a[4], "input"), "сборка")
    b.arrow({"activity": a[4]}, _box(a[5], "input"), "проверенная сборка")
    b.arrow({"activity": a[5]}, _box(a[6], "input"), "внедрённый продукт")
    b.arrow({"activity": a[6]}, {"frame": "output"}, "работающий продукт")
    b.arrow({"activity": a[4]}, _box(a[3], "input"), "дефекты")
    b.arrow({"activity": a[6]}, _box(a[1], "control"), "запросы на изменения")
    for i in (2, 4, 5, 6):
        b.fork("стандарты", _box(a[i], "control"))
    for i in (3, 4, 5):
        b.fork("команда", _box(a[i], "mechanism"))
    return b.ed


SCENARIOS = {"olympiad": olympiad, "order": order, "software": software}
