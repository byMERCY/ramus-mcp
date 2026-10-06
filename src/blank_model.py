"""A new Ramus model file, written from nothing.

A fresh file is the fixed part every Ramus 3 file has (:mod:`ramus_schema`) and the few rows
that make it hold one IDEF0 model:

* the model's catalog (a qualifier, named as Ramus names it in the list of models - "Работы"
  by default) and the text attribute that names its activities ("Название");
* element 1, the base function Ramus keeps for its own catalog; element 2, the name attribute
  as an element of the attributes catalog; element 3, the model's base function - the owner of
  the context diagram A-0, with the project's settings (author, project name, dates, page
  size) that the diagram frame shows; element 4, the model's entry in the model tree.

The top activity, A0, is added by the editor like any other box (``ModelEditor.create``).
Ramus numbers these elements so in a file it creates, and so do the qualifier (14) and the
name attribute (55), right after the system ones.
"""

from __future__ import annotations

import datetime as _dt
import io
import zipfile
from typing import Optional

try:
    from . import ramus_schema as schema
    from .rsf_document import RsfDocument
except ImportError:  # pragma: no cover - script execution
    import ramus_schema as schema
    from rsf_document import RsfDocument

DEFAULT_MODEL = "Работы"  # what Ramus calls a new IDEF0 model in its list of models
NAME_ATTRIBUTE = "Название"
MODEL_QUALIFIER, NAME_ATTRIBUTE_ID = 14, 55
SYSTEM_BASE, NAME_ELEMENT, BASE_FUNCTION, MODEL_TREE_ENTRY = 1, 2, 3, 4

# The plugins a file of the desktop Ramus 3 lists, in its order.
PLUGINS = (
    "Attribute.Core.Hierarchical", "Core", "Attribute.Core.Icon", "Attribute.Eval.Function",
    "Attribute.Core.Text", "Attribute.Core.Double", "Attribute.Core.Long", "Attribute.Core.Date",
    "Attribute.Core.OtherElement", "Attribute.Core.ElementList", "Autochange", "Eval",
    "Attribute.IDEF0.FRectangle", "Attribute.IDEF0.Status", "Attribute.IDEF0.Font",
    "Attribute.IDEF0.OunerId", "Attribute.IDEF0.Type", "Attribute.IDEF0.VisualData",
    "Attribute.IDEF0.Color", "Attribute.IDEF0.AnyToAny", "Attribute.IDEF0.Sector",
    "Attribute.IDEF0.SectorBorder", "Attribute.IDEF0.ProjectPreferences",
    "Attribute.IDEF0.DecompositionType", "Attribute.IDEF0.SectorPoint",
    "Attribute.IDEF0.SectorProperties", "IDEF0", "Chart",
)
# The attributes a model's catalog carries besides its name: the hierarchy and the IDEF0
# attributes of a box, from F_VISUAL_DATA (20) to F_LINK (34).
MODEL_ATTRIBUTES = (1,) + tuple(range(20, 35))
EMPTY_DIAGRAM = bytes([2, 0, 0, 0, 0, 0, 0, 0])  # a version-2 diagram with no free texts


def blank(model_name: str = DEFAULT_MODEL, author: str = "", project: str = "",
          now: Optional[_dt.datetime] = None) -> RsfDocument:
    """A new file with one empty IDEF0 model in it, held open for editing."""
    now = now or _dt.datetime.now().astimezone()
    doc = RsfDocument(io.BytesIO(_skeleton(now)))
    add = doc.add_row

    for aid, name, plugin, kind, comparable in schema.ATTRIBUTES:
        add("attributes", {"ATTRIBUTE_ID": aid, "ATTRIBUTE_NAME": name,
                           "ATTRIBUTE_TYPE_PLUGIN_NAME": plugin, "ATTRIBUTE_TYPE_NAME": kind,
                           "ATTRIBUTE_TYPE_COMPARABLE": comparable, "ATTRIBUTE_SYSTEM": True})
    add("attributes", {"ATTRIBUTE_ID": NAME_ATTRIBUTE_ID, "ATTRIBUTE_NAME": NAME_ATTRIBUTE,
                       "ATTRIBUTE_TYPE_PLUGIN_NAME": "Core", "ATTRIBUTE_TYPE_NAME": "Text",
                       "ATTRIBUTE_TYPE_COMPARABLE": True, "ATTRIBUTE_SYSTEM": False})
    for qid, name, naming in schema.QUALIFIERS:
        add("qualifiers", {"QUALIFIER_ID": qid, "QUALIFIER_NAME": name, "QUALIFIER_SYSTEM": True,
                           "ATTRIBUTE_FOR_NAME": naming})
    add("qualifiers", {"QUALIFIER_ID": MODEL_QUALIFIER, "QUALIFIER_NAME": model_name,
                       "QUALIFIER_SYSTEM": False, "ATTRIBUTE_FOR_NAME": NAME_ATTRIBUTE_ID})
    for qid, aid, system, position in schema.QUALIFIER_ATTRIBUTES:
        add("qualifiers_attributes", {"QUALIFIER_ID": qid, "ATTRIBUTE_ID": aid,
                                      "ATTRIBUTE_SYSTEM": system, "ATTRIBUTE_POSITION": position})
    for position, aid in enumerate(MODEL_ATTRIBUTES):
        add("qualifiers_attributes", {"QUALIFIER_ID": MODEL_QUALIFIER, "ATTRIBUTE_ID": aid,
                                      "ATTRIBUTE_SYSTEM": True, "ATTRIBUTE_POSITION": position})
    add("qualifiers_attributes", {"QUALIFIER_ID": MODEL_QUALIFIER,
                                  "ATTRIBUTE_ID": NAME_ATTRIBUTE_ID, "ATTRIBUTE_SYSTEM": False,
                                  "ATTRIBUTE_POSITION": 0})
    field_id = 1
    for pid, table, kind, cls, plugin, type_name, fields in schema.PERSISTENTS:
        add("persistents", {"PERSISTENT_ID": pid, "TABLE_NAME": table, "TABLE_TYPE": kind,
                            "CLASS_NAME": cls, "PLUGIN_NAME": plugin, "TYPE_NAME": type_name,
                            "PERSISTENT_EXISTS": True})
        for name, column, column_id, field_type, autoset, primary in fields:
            add("persistent_fields", {
                "PERSISTENT_FIELD_ID": field_id, "PERSISTENT_ID": pid, "FIELD_NAME": name,
                "FIELD_DATABASE_NAME": column, "FIELD_ID": column_id, "FIELD_EXISTS": True,
                "FIELD_TYPE": field_type, "FIELD_AUTOSET": autoset, "FIELD_PRIMARY": primary})
            field_id += 1
    for stream in ("/user/qualifier/auto-add-attributes.xml", "/properties/idef0.xml"):
        add("streams", {"STREAM_ID": stream})

    # The elements that make the model, and what each holds.
    for eid, qualifier in ((SYSTEM_BASE, 8), (NAME_ELEMENT, 3), (BASE_FUNCTION, 8),
                           (MODEL_TREE_ENTRY, 9)):
        # Ramus 3 keeps names in attributes; the element's own name column is empty, never
        # NULL (the application refuses a file with a NULL there).
        add("elements", {"ELEMENT_ID": eid, "ELEMENT_NAME": "", "QUALIFIER_ID": qualifier})
        top = eid in (SYSTEM_BASE, BASE_FUNCTION)  # base functions sit at the root, as 0
        add("Core/attribute_hierarchicals", {
            "ATTRIBUTE_ID": 1, "ELEMENT_ID": eid, "ICON_ID": -1,
            "PARENT_ELEMENT_ID": 0 if top else -1, "PREVIOUS_ELEMENT_ID": 0 if top else -1})
    for aid, eid, value in ((8, NAME_ELEMENT, NAME_ATTRIBUTE), (9, NAME_ELEMENT, "Core.Text"),
                            (8, MODEL_TREE_ENTRY, model_name)):
        add("Core/attribute_texts", {"ATTRIBUTE_ID": aid, "ELEMENT_ID": eid, "VALUE": value})
    for aid, eid, value in ((44, SYSTEM_BASE, 8), (7, NAME_ELEMENT, NAME_ATTRIBUTE_ID),
                            (44, BASE_FUNCTION, MODEL_QUALIFIER), (6, MODEL_TREE_ENTRY,
                                                                   MODEL_QUALIFIER)):
        add("Core/attribute_longs", {"ATTRIBUTE_ID": aid, "ELEMENT_ID": eid, "VALUE": value})
    stamp = short_date(now)
    for aid in (32, 33):  # F_REV_DATE, F_SYSTEM_REV_DATE
        add("Core/attribute_dates", {"ATTRIBUTE_ID": aid, "ELEMENT_ID": BASE_FUNCTION,
                                     "VALUE": stamp})
    add("IDEF0/attribute_model_preferences", {
        "ATTRIBUTE_ID": 45, "ELEMENT_ID": BASE_FUNCTION, "CHANGE_DATE": stamp,
        "CREATE_DATE": stamp, "DEFINITION": "", "DIAGRAM_SIZE": "A4",
        "PROJECT_AUTOR": author or "", "PROJECT_NAME": project or "", "USED_AT": ""})
    add("Core/attribute_other_element_properties", {"ATTRIBUTE": 36, "QUALIFIER": 6,
                                                    "QUALIFIER_ATTRIBUTE": 39})
    add("Core/attribute_element_list_properties", {"ATTRIBUTE_ID": 5, "QUALIFIER1": 2,
                                                   "QUALIFIER2": 3})
    return doc


def short_date(when: _dt.datetime) -> str:
    """A date as Ramus stores one: Java's short US form, "10/6/26 2:07 PM"."""
    hour = when.hour % 12 or 12
    return f"{when.month}/{when.day}/{when:%y} {hour}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"


def java_date(when: _dt.datetime) -> str:
    """Java's Date.toString(): "Tue Oct 06 22:14:45 GMT+03:00 2026"."""
    offset = when.utcoffset() or _dt.timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    zone = "GMT" if minutes == 0 else \
        f"GMT{'+' if minutes > 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    return f"{when:%a %b %d %H:%M:%S} {zone} {when:%Y}"


def _skeleton(now: _dt.datetime) -> bytes:
    """Every member of a new file, the tables still empty."""
    stamp = java_date(now)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for member, table, columns in schema.TABLES:
            info = zipfile.ZipInfo(member, date_time=now.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, _table(table, columns, stamp) if table else _properties(member, now))
    return buf.getvalue()


def _table(name: str, columns: str, stamp: str) -> bytes:
    fields = "".join(f'<field id="{i}" name="{c.split(":")[0]}" type="{c.split(":")[1]}"/>'
                     for i, c in enumerate(columns.split()))
    return (f'<?xml version="1.0" encoding="UTF-8"?><table generate-from-table="{name}" '
            f'generate-time="{stamp}" prefix="ramus_"><fields>{fields}</fields><data/></table>'
            ).encode("utf-8")


def _properties(member: str, now: _dt.datetime) -> bytes:
    """One of the Java properties files a .rsf holds, as Properties.storeToXML writes it."""
    if member == "data/application_metadata.xml":
        comment = "Ramus file metadata"
        entries = [(f"Plugin_{i}", p) for i, p in enumerate(PLUGINS)] + [
            ("PluginCount", str(len(PLUGINS))), ("ApplicationName", "Ramus"),
            ("ApplicationVersion", "2.0"), ("FileOpenMinimumVersion", "2.0"),
            ("CurrentTimeMillis", str(int(now.timestamp() * 1000))),
            ("CurrentDateTime", java_date(now))]
    elif member == "data/sequences.xml":
        comment = "Sequence list file"
        entries = [("ordinates__sequence", "1"), ("crosspoint_sequence", "1")]
    elif member == "properties/idef0.xml":
        comment, entries = "", [("OUNERS_IDS", "")]
    elif member == "user/qualifier/auto-add-attributes.xml":
        comment = "Path: /user/qualifier/auto-add-attributes.xml"
        entries = [("AUTO_ADD_ATTRIBUTE_IDS", str(NAME_ATTRIBUTE_ID)),
                   ("ATTRIBUTE_FOR_NAME", str(NAME_ATTRIBUTE_ID))]
    else:  # pragma: no cover - the schema lists no other
        raise ValueError(f"no contents known for {member}")
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
             '<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">',
             "<properties>", f"<comment>{comment}</comment>" if comment else "<comment/>"]
    lines += [f'<entry key="{k}">{v}</entry>' if v else f'<entry key="{k}"/>' for k, v in entries]
    lines.append("</properties>")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")

