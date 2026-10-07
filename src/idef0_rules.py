"""The IDEF0 rules a model is checked against - what makes a diagram right, not only drawn.

Syntax - an *error*, the method is broken: every activity has at least one control and one
output; an arrow leaves a box only by its output side (right) and enters by input, control or
mechanism; no end of an arrow is left attached to nothing; every box and every arrow is named.

Balance and style - a *warning*, allowed but worth a look: an arrow on a box that has a
decomposition comes onto that decomposition's frame with the same role, and every arrow on a
frame comes from the box above (ICOM balance; an unpaired end is what Ramus draws as a tunnel,
which is right only when it was meant); an arrow inherited from the box above is drawn on to a
box; a sheet has 3 to 6 boxes; an activity is named by a verb ("Проверить заявку"), a flow by a
noun; no arrow crosses a sheet from frame to frame without touching a box.

After FIPS PUB 183 (IDEF0) and its Russian counterpart, Р 50.1.028-2001. Pure: reads a model
through the reader, writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

try:
    from .ramus_rsf import Activity, Arrow, Diagram, End, RsfModel
except ImportError:  # pragma: no cover - script execution
    from ramus_rsf import Activity, Arrow, Diagram, End, RsfModel

ERROR, WARNING = "error", "warning"


@dataclass
class Finding:
    """One thing wrong with the model: which rule, how bad, where, what, and how to mend it
    with the editing tools."""

    rule: str
    severity: str
    sheet: str  # the node number of the sheet it is on
    message: str
    fix: Optional[str] = None
    activity: Optional[str] = None  # the box's number
    flow: Optional[int] = None  # the stream id
    segment: Optional[int] = None

    def as_dict(self) -> Dict[str, object]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def check(model: RsfModel, sheet: Optional[str] = None) -> List[Finding]:
    """Every rule over every sheet - or over the one numbered ``sheet`` ("A0", "A-0", "A12").
    Findings come sheet by sheet, top down, errors first."""
    diagrams = model.diagrams()
    world = _World(diagrams, model.activities(), model.nodes())
    found: List[Finding] = []
    named_streams: set = set()
    seen: set = set()
    for d in sorted(diagrams, key=_sheet_order):
        if sheet is not None and d.node.lower() != sheet.strip().lower():
            continue
        if d.notation == "idef0":
            found += _box_count(d)
            for box in d.activities:
                found += _box_rules(d, box)
            for a in d.arrows:
                found += _arrow_rules(d, a)
                found += _balance(world, d, a, seen)
        else:
            for box in d.activities:
                found += _dfd_box_rules(d, box)
            for a in d.arrows:
                found += _dfd_arrow_rules(d, a)
        found += _flow_names(d, named_streams)
    order = {d.node: i for i, d in enumerate(sorted(diagrams, key=_sheet_order))}
    found.sort(key=lambda f: (order.get(f.sheet, 0), f.severity != ERROR))
    return found


def summary(found: List[Finding]) -> Dict[str, int]:
    return {"errors": sum(f.severity == ERROR for f in found),
            "warnings": sum(f.severity == WARNING for f in found)}


# ------------------------------------------------------------------------------- the rules

def _box_count(d: Diagram) -> List[Finding]:
    n = len(d.activities)
    if d.node == "A-0":
        if n != 1:
            return [Finding("context_one_box", ERROR, d.node,
                            f"The context diagram A-0 holds {n} boxes; it holds exactly one - "
                            f"the whole system as a single activity.")]
        return []
    if n < 3:
        return [Finding("box_count", WARNING, d.node,
                        f"{d.node} is decomposed into {n} box{'es' if n != 1 else ''}; IDEF0 asks "
                        f"for 3 to 6 on a sheet. Split it further (add_activity), or leave "
                        f"{d.node} undecomposed if there is no more to say.")]
    if n > 6:
        return [Finding("box_count", WARNING, d.node,
                        f"{d.node} has {n} boxes; IDEF0 asks for at most 6 on a sheet. Group some "
                        f"of them under one activity and decompose that instead.")]
    return []


def _box_rules(d: Diagram, box: Activity) -> List[Finding]:
    out = []
    label = _box_label(box)
    if not box.name.strip():
        out.append(Finding("unnamed_activity", ERROR, d.node,
                           f"Box {box.number} has no name.",
                           fix=f'rename_activity(activity="{box.number}", name="<verb phrase>")',
                           activity=box.number))
    else:
        problem = _not_a_verb(box.name)
        if problem:
            out.append(Finding("activity_name_verb", WARNING, d.node,
                               f"{label}: {problem}. IDEF0 names an activity by what is done - a "
                               f"verb phrase, e.g. «Проверить заявку», not «Проверка заявки».",
                               fix=f'rename_activity(activity="{box.number}", name="...")',
                               activity=box.number))
    controls = [a for a in d.arrows if _on(a.end, box, "top")]
    outputs = [a for a in d.arrows if _on(a.start, box, "right")]
    if not controls:
        out.append(Finding("needs_control", ERROR, d.node,
                           f"{label} has no control (an arrow into its top side). Every activity "
                           f"is governed by something - rules, a plan, a standard, an order.",
                           fix=f'add_arrow(sheet="{d.node}", source={{"frame": "control"}} or '
                               f'{{"activity": "..."}}, target={{"activity": "{box.number}", '
                               f'"role": "control"}}, name="...")',
                           activity=box.number))
    if not outputs:
        out.append(Finding("needs_output", ERROR, d.node,
                           f"{label} has no output (an arrow out of its right side). Every "
                           f"activity produces something, or it does nothing.",
                           fix=f'add_arrow(sheet="{d.node}", source={{"activity": '
                               f'"{box.number}"}}, target={{"frame": "output"}} or '
                               f'{{"activity": "...", "role": "..."}}, name="...")',
                           activity=box.number))
    return out


def _arrow_rules(d: Diagram, a: Arrow) -> List[Finding]:
    out = []
    name = _flow_label(a)
    where = dict(flow=a.stream_id, segment=a.sector_id)
    s, e = a.start, a.end
    if s.kind == "activity" and s.side != "right":
        out.append(Finding("wrong_side", ERROR, d.node,
                           f"{name} leaves {_box_ref(d, s)} through its {s.role} side ({s.side}); "
                           f"an arrow leaves a box only by its right side, as an output.",
                           **where))
    if e.kind == "activity" and e.side == "right":
        out.append(Finding("wrong_side", ERROR, d.node,
                           f"{name} goes into {_box_ref(d, e)} through its output side (right); "
                           f"it enters by the left (input), top (control) or bottom (mechanism).",
                           **where))
    if s.kind == "frame" and s.side == "right":
        out.append(Finding("wrong_side", WARNING, d.node,
                           f"{name} comes onto the sheet through the right side of the frame, "
                           f"where outputs leave.", **where))
    if e.kind == "frame" and e.side != "right":
        out.append(Finding("wrong_side", WARNING, d.node,
                           f"{name} leaves the sheet through the {e.side} side of the frame; "
                           f"what leaves an activity is an output, on the right.", **where))
    if s.kind == "frame" and e.kind == "frame":
        out.append(Finding("frame_to_frame", WARNING, d.node,
                           f"{name} crosses the sheet from frame to frame without touching a "
                           f"box: no activity here uses or makes it. Take it to the box that "
                           f"does, or leave it off this sheet.", **where))
    for end, other, which in ((s, e, "start"), (e, s, "end")):
        if end.kind != "open":
            continue
        if other.kind == "frame":
            out.append(_not_drawn(d, a, other, incoming=(which == "end")))
        else:
            out.append(Finding("dangling_end", ERROR, d.node,
                               f"Segment {a.sector_id} of {name} has its {which} attached to "
                               f"nothing.",
                               fix=f"delete_arrow(segment={a.sector_id}) and draw it again "
                                   f"with add_arrow",
                               **where))
    return out


def _not_drawn(d: Diagram, a: Arrow, frame_end: End, incoming: bool) -> Finding:
    name = _flow_label(a)
    flow = f", flow={a.stream_id}" if a.stream_id is not None else ""
    if incoming:
        return Finding("not_drawn", WARNING, d.node,
                       f"{name} comes onto {d.node} from the box above as its {frame_end.role} "
                       f"but is not taken to any box here yet.",
                       fix=f'add_arrow(sheet="{d.node}", source={{"frame": '
                           f'"{frame_end.role}"}}, target={{"activity": "...", "role": '
                           f'"..."}}{flow})',
                       flow=a.stream_id, segment=a.sector_id)
    return Finding("not_drawn", WARNING, d.node,
                   f"{name} leaves {d.node} as an output of the box above but comes from no box "
                   f"here yet.",
                   fix=f'add_arrow(sheet="{d.node}", source={{"activity": "..."}}, '
                       f'target={{"frame": "output"}}{flow})',
                   flow=a.stream_id, segment=a.sector_id)


def _flow_names(d: Diagram, seen: set) -> List[Finding]:
    """Names belong to flows, not segments: each flow (all its pieces and branches) once, and
    each stream once in the whole model."""
    out = []
    groups: Dict[int, List[Arrow]] = {}
    for a in d.arrows:
        groups.setdefault(a.flow, []).append(a)
    for arrows in groups.values():
        names = [a.name.strip() for a in arrows if a.name.strip()]
        if not names:
            a = arrows[0]
            if a.stream_id is not None and ("unnamed", a.stream_id) in seen:
                continue
            seen.add(("unnamed", a.stream_id))
            fix = (f"rename_flow(flow={a.stream_id}, name=\"<noun phrase>\")"
                   if a.stream_id is not None else None)
            out.append(Finding("unnamed_arrow", ERROR, d.node,
                               f"An arrow ({_ends_text(d, arrows)}) has no name.", fix=fix,
                               flow=a.stream_id, segment=a.sector_id))
            continue
        for a in arrows:
            key = ("named", a.stream_id, a.name.strip())
            if not a.name.strip() or key in seen:
                continue
            seen.add(key)
            word = _first_word(a.name)
            if word and _is_infinitive(word):
                out.append(Finding("flow_name_noun", WARNING, d.node,
                                   f"{_flow_label(a)} is named by a verb («{word}»); IDEF0 names "
                                   f"what an arrow carries by a noun - a thing, data, a document.",
                                   fix=(f'rename_flow(flow={a.stream_id}, name="...")'
                                        if a.stream_id is not None else None),
                                   flow=a.stream_id, segment=a.sector_id))
    return out


# ---------------------------------------------------------------- data flow diagrams
#
# A DFD or DFDS sheet has no ICOM: a flow meets a box on any side. What it has instead
# (Gane & Sarson, Yourdon): every process takes something in and gives something out - one
# with only inputs is a "black hole", one with only outputs a "miracle" - and data moves only
# through a process: never straight between two external entities, two data stores, or an
# external entity and a store.

_OBJECT = {"external": "external entity", "store": "data store", "role": "role"}


def _dfd_box_rules(d: Diagram, box: Activity) -> List[Finding]:
    out = []
    what = _OBJECT.get(box.kind, "process")
    label = f"{box.number} «{box.name.strip()}»" if box.number else \
        f"{what} «{box.name.strip()}»"
    ref = box.number or str(box.element_id)
    if not box.name.strip():
        out.append(Finding("unnamed_activity", ERROR, d.node,
                           f"A {what} on {d.node} has no name.",
                           fix=f'rename_activity(activity="{ref}", name="...")',
                           activity=box.number or None))
        return out
    if box.kind != "process":
        return out
    problem = _not_a_verb(box.name)
    if problem:
        out.append(Finding("activity_name_verb", WARNING, d.node,
                           f"{label}: {problem}. A process is named by what it does - a verb "
                           f"phrase, e.g. «Проверить заказ».",
                           fix=f'rename_activity(activity="{ref}", name="...")',
                           activity=box.number))
    ins = [a for a in d.arrows if a.end.kind == "activity" and a.end.activity_id == box.element_id]
    outs = [a for a in d.arrows
            if a.start.kind == "activity" and a.start.activity_id == box.element_id]
    if not ins:
        out.append(Finding("dfd_needs_input", ERROR, d.node,
                           f"{label} takes nothing in: a process that makes data from nothing "
                           f"(a \"miracle\"). Draw the flow it works on into it.",
                           fix=f'add_arrow(sheet="{d.node}", source={{"activity": "..."}} or '
                               f'{{"frame": "left"}}, target={{"activity": "{ref}"}}, '
                               f'name="...")',
                           activity=box.number))
    if not outs:
        out.append(Finding("dfd_needs_output", ERROR, d.node,
                           f"{label} gives nothing out: data goes in and is lost (a \"black "
                           f"hole\"). Draw what it produces out of it.",
                           fix=f'add_arrow(sheet="{d.node}", source={{"activity": "{ref}"}}, '
                               f'target={{"activity": "..."}} or {{"frame": "right"}}, '
                               f'name="...")',
                           activity=box.number))
    return out


def _dfd_arrow_rules(d: Diagram, a: Arrow) -> List[Finding]:
    out = []
    name = _flow_label(a)
    where = dict(flow=a.stream_id, segment=a.sector_id)
    kinds = {b.element_id: b.kind for b in d.activities}
    ends = [kinds.get(e.activity_id) for e in (a.start, a.end) if e.kind == "activity"]
    if len(ends) == 2 and "process" not in ends:
        pair = " and ".join(_OBJECT.get(k, k) for k in ends)
        out.append(Finding("dfd_through_process", ERROR, d.node,
                           f"{name} runs straight between a {pair}; data moves only through a "
                           f"process. Put the process that moves it in between.", **where))
    for end, other, which in ((a.start, a.end, "start"), (a.end, a.start, "end")):
        if end.kind == "open" and other.kind == "frame":  # come down from above, not drawn on
            out.append(_not_drawn(d, a, other, incoming=(which == "end")))
        elif end.kind == "open":
            out.append(Finding("dangling_end", ERROR, d.node,
                               f"Segment {a.sector_id} of {name} has its {which} attached to "
                               f"nothing.",
                               fix=f"delete_arrow(segment={a.sector_id}) and draw it again "
                                   f"with add_arrow",
                               **where))
    if a.start.kind == "frame" and a.end.kind == "frame":
        out.append(Finding("frame_to_frame", WARNING, d.node,
                           f"{name} crosses the sheet from frame to frame without touching a "
                           f"box.", **where))
    return out


# ------------------------------------------------------------------------- ICOM balance

class _World:
    """What balance needs to look across levels: every sheet, by the activity it decomposes,
    and every segment, by id, with the sheet it is on."""

    def __init__(self, diagrams: List[Diagram], acts: Dict[int, Activity],
                 nodes: Dict[int, List[Tuple[int, str]]]):
        self.acts = acts
        self.nodes = nodes
        self.sheet_of = {d.parent_id: d for d in diagrams}
        self.arrows: Dict[int, Tuple[Arrow, Diagram]] = {
            a.sector_id: (a, d) for d in diagrams for a in d.arrows}

    def partners(self, end: End, direction: str, on: Diagram):
        """The segment ends on sheet ``on`` that meet this end's node from the other way."""
        if end.node is None:
            return []
        out = []
        for sid, way in self.nodes.get(end.node, []):
            if way == direction or sid not in self.arrows:
                continue
            arrow, sheet = self.arrows[sid]
            if sheet is on:
                out.append(arrow.start if way == "out" else arrow.end)
        return out

    def unjoined(self, arrow: Arrow, end: End, there: Diagram, here: Diagram, fits,
                 its_end: str) -> Optional[Arrow]:
        """The same flow drawn on sheet ``there`` at the place that would continue ``end`` -
        an arrow whose ``its_end`` ("start"/"end") is on the same side and passes ``fits`` -
        but joined to nothing on ``here``: the arrow is on both levels, only not tied
        together."""
        if arrow.stream_id is None:
            return None
        way = "out" if its_end == "start" else "in"
        for other in there.arrows:
            e = getattr(other, its_end)
            if other.stream_id == arrow.stream_id and e.side == end.side and fits(e) and \
                    not self.partners(e, way, here):
                return other
        return None


def _not_joined(upper: Arrow, lower: Arrow, box: Activity, above: Diagram, below: Diagram,
                end: End, seen: set) -> List[Finding]:
    key = ("join", upper.sector_id, lower.sector_id)
    if key in seen:
        return []
    seen.add(key)
    return [Finding(
        "not_joined", WARNING, below.node,
        f"{_flow_label(upper)} is drawn on both levels - as {_article(end.role)} of "
        f"{box.number} on {above.node} and on the frame of {below.node} - but the two are not "
        f"joined, so each is a tunnel and the levels do not balance.",
        fix=f"join_levels(first={upper.sector_id}, second={lower.sector_id})",
        activity=box.number, flow=upper.stream_id, segment=lower.sector_id)]


def _balance(world: _World, d: Diagram, a: Arrow, seen: set) -> List[Finding]:
    out = []
    name = _flow_label(a)
    flow = f", flow={a.stream_id}" if a.stream_id is not None else ""
    for end, direction in ((a.start, "out"), (a.end, "in")):
        incoming = direction == "out"  # the arrow starts here: it comes in from the other level
        if end.tunnel == "soft":
            continue  # drawn in round brackets on purpose: a tunnel someone chose
        if end.kind == "frame" and d.node != "A-0":
            box = world.acts.get(d.parent_id)
            above = world.sheet_of.get(box.parent_id) if box is not None else None
            if box is None or above is None:
                continue
            pair = [p for p in world.partners(end, direction, above)
                    if p.kind == "activity" and p.activity_id == box.element_id]
            twin = None if pair else world.unjoined(
                a, end, above, d, lambda e: e.kind == "activity"
                and e.activity_id == box.element_id, its_end="end" if incoming else "start")
            if twin is not None:
                out += _not_joined(twin, a, box, above, d, end, seen)
            elif not pair:
                target = (f'source={{"frame" or "activity": "..."}}, target={{"activity": '
                          f'"{box.number}", "role": "{end.role}"}}' if incoming else
                          f'source={{"activity": "{box.number}"}}, target={{"frame" or '
                          f'"activity": "..."}}')
                out.append(Finding(
                    "unbalanced", WARNING, d.node,
                    f"{name} {'comes onto' if incoming else 'leaves'} {d.node} as its "
                    f"{end.role}, but on {above.node} box {box.number} has no such "
                    f"{end.role}: the levels do not balance (Ramus draws it as a tunnel - right "
                    f"only if it was meant to be hidden there).",
                    fix=f'add_arrow(sheet="{above.node}", {target}{flow})',
                    activity=box.number, flow=a.stream_id, segment=a.sector_id))
            elif pair[0].side != end.side:
                out.append(Finding(
                    "role_mismatch", WARNING, d.node,
                    f"{name} is {_article(pair[0].role)} of {box.number} on {above.node} but "
                    f"comes onto {d.node} as {_article(end.role)}: one arrow, two roles.",
                    activity=box.number, flow=a.stream_id, segment=a.sector_id))
        elif end.kind == "activity" and end.activity_id in world.sheet_of:
            below = world.sheet_of[end.activity_id]
            box = world.acts.get(end.activity_id)
            if box is None:
                continue
            pair = [p for p in world.partners(end, direction, below) if p.kind == "frame"]
            into = direction == "in"  # it ends on the box: it should come onto its sheet
            twin = None if pair else world.unjoined(
                a, end, below, d, lambda e: e.kind == "frame",
                its_end="start" if into else "end")
            if twin is not None:
                out += _not_joined(a, twin, box, d, below, end, seen)
            elif not pair:
                target = (f'source={{"frame": "{end.role}"}}, target={{"activity": "...", '
                          f'"role": "..."}}' if into else
                          f'source={{"activity": "..."}}, target={{"frame": "output"}}')
                out.append(Finding(
                    "unbalanced", WARNING, d.node,
                    f"{name} is {_article(end.role)} of {box.number} here but "
                    f"{'does not come onto' if into else 'comes from nothing on'} "
                    f"{box.number}'s own sheet: the levels do not balance (Ramus draws it as a "
                    f"tunnel - right only if it was meant to be hidden there).",
                    fix=f'add_arrow(sheet="{box.number}", {target}{flow})',
                    activity=box.number, flow=a.stream_id, segment=a.sector_id))
    return out


# ------------------------------------------------------------------------------- names

# Russian infinitives end so: проверить, собраться, нести, нестись, беречь, беречься.
_INFINITIVE = ("ть", "ться", "ти", "тись", "чь", "чься")
# ... as do some nouns: every -ость (отчётность, область) and a handful of others.
_NOUNS_IN_TI = {"часть", "власть", "страсть", "снасть", "честь", "весть", "сеть", "нить", "мать",
                "путь", "суть", "печать", "треть", "четверть", "смерть", "ртуть", "плоть",
                "кровать"}
# English names that read as nouns: planning, inspection, management, maintenance.
_ENGLISH_NOUNS = ("ing", "tion", "sion", "ment", "ance", "ence", "ness")


def _first_word(name: str) -> str:
    m = re.search(r"[^\W\d_]+(?:-[^\W\d_]+)*", name)
    return m.group(0).lower() if m else ""


def _is_cyrillic(word: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in word)


def _is_infinitive(word: str) -> bool:
    if not _is_cyrillic(word) or word in _NOUNS_IN_TI or word.endswith("ость"):
        return False
    return word.endswith(_INFINITIVE)


def _not_a_verb(name: str) -> Optional[str]:
    """Why an activity's name does not read as a verb phrase, or None if it does (or the
    language is not one this can tell)."""
    word = _first_word(name)
    if not word:
        return None
    if _is_cyrillic(word):
        if _is_infinitive(word):
            return None
        return f"the name starts with «{word}», not a verb in the infinitive"
    if word.isascii() and len(word) > 5 and word.endswith(_ENGLISH_NOUNS):
        return f"the name starts with «{word}», which reads as a noun"
    return None


# ------------------------------------------------------------------------------- helpers

def _on(end: End, box: Activity, side: str) -> bool:
    return end.kind == "activity" and end.activity_id == box.element_id and end.side == side


def _box_label(box: Activity) -> str:
    return f"{box.number} «{box.name.strip()}»" if box.name.strip() else box.number


def _box_ref(d: Diagram, end: End) -> str:
    box = next((b for b in d.activities if b.element_id == end.activity_id), None)
    return _box_label(box) if box is not None else f"box {end.activity_id}"


def _flow_label(a: Arrow) -> str:
    text = " ".join(a.name.split())
    return f"«{text}»" if text else f"the unnamed arrow (segment {a.sector_id})"


def _ends_text(d: Diagram, arrows: List[Arrow]) -> str:
    def one(e: End) -> str:
        if e.kind == "activity":
            box = next((b for b in d.activities if b.element_id == e.activity_id), None)
            return f"{box.number if box else e.activity_id} {e.role}"
        return f"frame {e.role}" if e.kind == "frame" else e.kind
    starts = [one(a.start) for a in arrows if a.start.kind != "junction"]
    ends = [one(a.end) for a in arrows if a.end.kind != "junction"]
    return f"{', '.join(starts) or '?'} -> {', '.join(ends) or '?'}"


def _article(role: Optional[str]) -> str:
    role = role or "end"
    return f"an {role}" if role[0] in "aeiou" else f"a {role}"


def _sheet_order(d: Diagram) -> Tuple[int, str]:
    return (0 if d.node == "A-0" else 1, d.node)
