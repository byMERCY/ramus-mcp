"""An editable .rsf file: the foundation the "hands" are built on.

:mod:`ramus_rsf` reads a model to look at it. This module holds a whole file open for
*changing* it: every member of the ZIP is kept, the table dumps are parsed into rows that can
be added, altered and removed, and the file is written back so that Ramus cannot tell our
output from its own.

Faithfulness is the whole point, so the rules below are the ones Ramus follows when it writes a
dump (checked against the files it produced, byte for byte):

* one line, no indentation: ``<?xml ...?><table ...><fields>...</fields><data>...</data></table>``;
  an empty table is ``<data/>``;
* a cell holding NULL is left out of its row entirely; an empty string is ``<f id="n"/>``;
* booleans are ``TRUE``/``FALSE``, binary is upper-case hex of each byte plus 128, text is
  written raw (UTF-8, line breaks as they are) with only ``& < >`` escaped.

A table nobody touched is copied out exactly as it came in; only changed tables are rebuilt.

New ids follow Ramus's own rules: an element gets one more than the largest ELEMENT_ID in the
file (that is what Ramus does when it creates one), and crosspoints and ordinates come from the
counters in ``data/sequences.xml``, which are moved on so the next Ramus session does not hand
the same number out again.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

NO_REMOVAL = 2147483647  # REMOVED_BRANCH_ID of a row that has never been deleted
SEQUENCES = "data/sequences.xml"

Row = Dict[str, str]  # column -> cell text; a NULL cell is simply absent


@dataclass(frozen=True)
class Field:
    """One column of a table dump, with its XML attributes in the order the file had them."""

    id: str
    name: str
    type: str
    attrs: Tuple[Tuple[str, str], ...]


# --------------------------------------------------------------------------- escaping

def _escape_text(value: str) -> str:
    # A carriage return stays raw: that is how Ramus writes one (a name typed on Windows keeps
    # its CR LF), and an XML parser would otherwise fold it into a plain LF.
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    return (_escape_text(value).replace('"', "&quot;").replace("\r", "&#13;")
            .replace("\n", "&#10;").replace("\t", "&#9;"))


def mask(data: bytes) -> str:
    """Real bytes -> the hex text a dump stores (each byte plus 128, upper case)."""
    return bytes(b ^ 0x80 for b in data).hex().upper()


def format_double(value: float) -> str:
    """A double the way Java's Double.toString writes the common cases (80.0, 0.5, 12.25)."""
    value = float(value)
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value)}.0"
    return repr(value)


def format_cell(value: Any, sql_type: str) -> Optional[str]:
    """A Python value -> the cell text for a column of this SQL type; None stays NULL."""
    if value is None:
        return None
    t = sql_type.upper()
    if isinstance(value, (bytes, bytearray)):
        return mask(bytes(value))
    if t == "BOOLEAN":
        if isinstance(value, str):
            return "TRUE" if value.strip().lower() == "true" else "FALSE"
        return "TRUE" if value else "FALSE"
    if t in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT"):
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(int(value))
    if t in ("DOUBLE", "FLOAT", "REAL", "DOUBLE PRECISION"):
        return format_double(value)
    return str(value)


# ------------------------------------------------------------------------------ tables

class Table:
    """One parsed table dump. Rows are dicts of raw cell text and may be changed freely; mark
    the table changed (``touch``) or use the helpers, which do it for you."""

    def __init__(self, name: str, raw: bytes):
        self.name = name
        self._raw = raw
        self.dirty = False

        text = raw.decode("utf-8")
        start = text.find("<table")
        if start < 0:
            raise ValueError(f"{name}: not a table dump")
        self._declaration = text[:start]  # kept verbatim

        # XML parsing folds CR LF into LF (end-of-line normalisation), which would lose the CR a
        # cell holds. Ramus's dumps are one line, so every CR in one is inside a cell's text:
        # protect them as character references, which the parser keeps as real CRs.
        root = ET.fromstring(raw.replace(b"\r", b"&#13;"))
        self._header: Tuple[Tuple[str, str], ...] = tuple(root.attrib.items())
        self.fields: List[Field] = []
        fields_el = root.find("fields")
        for fe in (fields_el if fields_el is not None else []):
            self.fields.append(Field(fe.get("id"), fe.get("name"), fe.get("type") or "",
                                     tuple(fe.attrib.items())))
        self._by_id = {f.id: f for f in self.fields}
        self._by_name = {f.name: f for f in self.fields}

        self.rows: List[Row] = []
        data_el = root.find("data")
        for row_el in (data_el if data_el is not None else []):
            row: Row = {}
            for cell in row_el.findall("f"):
                f = self._by_id.get(cell.get("id"))
                if f is not None:
                    row[f.name] = cell.text if cell.text is not None else ""
            self.rows.append(row)

    # -------------------------------------------------------------- columns

    @property
    def columns(self) -> List[str]:
        return [f.name for f in self.fields]

    def has(self, column: str) -> bool:
        return column in self._by_name

    def type_of(self, column: str) -> str:
        return self._by_name[column].type

    # ---------------------------------------------------------------- query

    def where(self, **match: Any) -> List[Row]:
        """Rows whose cells equal the given values (compared as text, so 79 matches "79")."""
        want = {k: (None if v is None else str(v)) for k, v in match.items()}
        return [r for r in self.rows if all(r.get(k) == v for k, v in want.items())]

    # ---------------------------------------------------------------- change

    def touch(self) -> None:
        self.dirty = True

    def add(self, values: Dict[str, Any]) -> Row:
        """Append a row. Values are Python values, converted for each column's type; a column
        not given (or given None) is NULL. Unknown columns are an error - better than a cell
        the file silently drops."""
        unknown = set(values) - set(self._by_name)
        if unknown:
            raise KeyError(f"{self.name} has no column(s) {sorted(unknown)}")
        row: Row = {}
        for f in self.fields:
            text = format_cell(values.get(f.name), f.type)
            if text is not None:
                row[f.name] = text
        self.rows.append(row)
        self.dirty = True
        return row

    def set(self, row: Row, **values: Any) -> None:
        """Change cells of a row that belongs to this table. None makes a cell NULL."""
        for column, value in values.items():
            if column not in self._by_name:
                raise KeyError(f"{self.name} has no column {column}")
            text = format_cell(value, self._by_name[column].type)
            if text is None:
                row.pop(column, None)
            else:
                row[column] = text
        self.dirty = True

    def remove(self, row: Row) -> None:
        self.rows.remove(row)
        self.dirty = True

    # ----------------------------------------------------------- serialise

    def to_bytes(self) -> bytes:
        """The dump as Ramus would write it. An untouched table is returned as it was read."""
        if not self.dirty:
            return self._raw
        out = [self._declaration, "<table"]
        for k, v in self._header:
            out.append(f' {k}="{_escape_attr(v)}"')
        out.append("><fields>")
        for f in self.fields:
            out.append("<field")
            for k, v in f.attrs:
                out.append(f' {k}="{_escape_attr(v)}"')
            out.append("/>")
        out.append("</fields>")
        if not self.rows:
            out.append("<data/>")
        else:
            out.append("<data>")
            for row in self.rows:
                cells = []
                for f in self.fields:
                    if f.name in row:
                        v = row[f.name]
                        cells.append(f'<f id="{f.id}"/>' if v == ""
                                     else f'<f id="{f.id}">{_escape_text(v)}</f>')
                out.append("<row>" + "".join(cells) + "</row>" if cells else "<row/>")
            out.append("</data>")
        out.append("</table>")
        return "".join(out).encode("utf-8")

    def rebuilt(self) -> bytes:
        """Serialise from the parsed rows even if nothing changed - used to prove the writer
        reproduces Ramus's own output."""
        was = self.dirty
        self.dirty = True
        try:
            return self.to_bytes()
        finally:
            self.dirty = was


# ---------------------------------------------------------------------------- document

class RsfDocument:
    """A .rsf file held open for editing. Load, change tables, then ``save`` to a path."""

    def __init__(self, path: str):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self._infos = z.infolist()
            self._members: Dict[str, bytes] = {i.filename: z.read(i.filename) for i in self._infos}
        self._tables: Dict[str, Table] = {}
        self._sequences_text = self._members.get(SEQUENCES, b"").decode("utf-8")
        self._sequences_dirty = False
        self._next_element: Optional[int] = None

    # ------------------------------------------------------------ members

    def members(self) -> List[str]:
        return [i.filename for i in self._infos]

    def table_names(self) -> List[str]:
        names = []
        for n in self.members():
            if n.startswith("data/") and n.endswith(".xml") and n != SEQUENCES:
                head = self._members[n][:300]
                if b"<table" in head:
                    names.append(n[len("data/"):-len(".xml")])
        return names

    def has_table(self, name: str) -> bool:
        return ("data/" + name + ".xml") in self._members

    def table(self, name: str) -> Table:
        """The table ``data/<name>.xml`` (e.g. ``"IDEF0/attribute_rectangles"``)."""
        if name not in self._tables:
            member = "data/" + name + ".xml"
            if member not in self._members:
                raise KeyError(f"this file has no table {name}")
            self._tables[name] = Table(name, self._members[member])
        return self._tables[name]

    @property
    def changed(self) -> bool:
        return self._sequences_dirty or any(t.dirty for t in self._tables.values())

    # ---------------------------------------------------- branches & names

    def current_branch(self) -> int:
        """The branch the file was saved on: the newest any element mentions (0 for Ramus 2)."""
        seen = {0}
        if self.has_table("elements"):
            for r in self.table("elements").rows:
                seen.add(int(r.get("CREATED_BRANCH_ID", "0") or 0))
                removed = int(r.get("REMOVED_BRANCH_ID", str(NO_REMOVAL)) or NO_REMOVAL)
                if removed != NO_REMOVAL:
                    seen.add(removed)
        return max(seen)

    @staticmethod
    def alive(row: Row, branch: int) -> bool:
        created = int(row.get("CREATED_BRANCH_ID", "0") or 0)
        removed = int(row.get("REMOVED_BRANCH_ID", str(NO_REMOVAL)) or NO_REMOVAL)
        return created <= branch < removed

    def attribute_id(self, name: str) -> Optional[int]:
        branch = self.current_branch()
        for r in self.table("attributes").rows:
            if r.get("ATTRIBUTE_NAME") == name and self.alive(r, branch):
                return int(r["ATTRIBUTE_ID"])
        return None

    def qualifier_id(self, name: str) -> Optional[int]:
        branch = self.current_branch()
        for r in self.table("qualifiers").rows:
            if r.get("QUALIFIER_NAME") == name and self.alive(r, branch):
                return int(r["QUALIFIER_ID"])
        return None

    def add_row(self, table: str, values: Dict[str, Any]) -> Row:
        """Add a row, filling in the branch bookkeeping a Ramus 3 file expects: a new element
        is created on the current branch and never removed, a new value belongs to the current
        branch. Files without those columns (Ramus 2) are left alone."""
        t = self.table(table)
        branch = self.current_branch()
        values = dict(values)
        if t.has("CREATED_BRANCH_ID"):
            values.setdefault("CREATED_BRANCH_ID", branch)
        if t.has("REMOVED_BRANCH_ID"):
            values.setdefault("REMOVED_BRANCH_ID", NO_REMOVAL)
        if t.has("VALUE_BRANCH_ID"):
            values.setdefault("VALUE_BRANCH_ID", branch)
        return t.add(values)

    # --------------------------------------------------------------- ids

    def new_element_id(self) -> int:
        """One more than the largest element id in the file - deleted rows included, since
        Ramus 3 keeps them and their ids stay taken."""
        if self._next_element is None:
            ids = [int(r["ELEMENT_ID"]) for r in self.table("elements").rows if r.get("ELEMENT_ID")]
            self._next_element = max(ids, default=0) + 1
        value = self._next_element
        self._next_element += 1
        return value

    def sequence(self, key: str) -> Optional[int]:
        m = re.search(r'<entry key="' + re.escape(key) + r'">(-?\d+)</entry>', self._sequences_text)
        return int(m.group(1)) if m else None

    def _set_sequence(self, key: str, value: int) -> None:
        pattern = r'(<entry key="' + re.escape(key) + r'">)(-?\d+)(</entry>)'
        if re.search(pattern, self._sequences_text):
            self._sequences_text = re.sub(pattern, lambda m: m.group(1) + str(value) + m.group(3),
                                          self._sequences_text, count=1)
        elif "</properties>" in self._sequences_text:
            self._sequences_text = self._sequences_text.replace(
                "</properties>", f'<entry key="{key}">{value}</entry>\n</properties>', 1)
        else:
            raise ValueError("data/sequences.xml is missing or not a properties file")
        self._sequences_dirty = True

    def _take(self, key: str, used: Iterable[int]) -> int:
        """The next number of a Ramus counter: never below the counter, never one already
        used in the tables (a file edited by hand may disagree with its counter)."""
        floor = max([v + 1 for v in used] + [self.sequence(key) or 0, 0])
        self._set_sequence(key, floor + 1)
        return floor

    def new_crosspoint(self) -> int:
        used = []
        if self.has_table("IDEF0/attribute_sector_borders"):
            used = [int(r["CROSSPOINT"]) for r in self.table("IDEF0/attribute_sector_borders").rows
                    if r.get("CROSSPOINT") not in (None, "")]
        return self._take("crosspoint_sequence", used)

    def new_ordinate(self) -> int:
        used = []
        if self.has_table("IDEF0/attribute_sector_points"):
            for r in self.table("IDEF0/attribute_sector_points").rows:
                for c in ("X_ORDINATE_ID", "Y_ORDINATE_ID"):
                    if r.get(c) not in (None, ""):
                        used.append(int(r[c]))
        return self._take("ordinates__sequence", used)

    # -------------------------------------------------------------- save

    def member_bytes(self, name: str) -> bytes:
        """What will be written for a member: rebuilt if its table changed."""
        if name == SEQUENCES and self._sequences_dirty:
            return self._sequences_text.encode("utf-8")
        if name.startswith("data/") and name.endswith(".xml"):
            key = name[len("data/"):-len(".xml")]
            t = self._tables.get(key)
            if t is not None:
                return t.to_bytes()
        return self._members[name]

    def write_to(self, target) -> None:
        """Write the whole file, as it now stands, to a path or a binary file object. Members
        keep their order, names, timestamps and compression."""
        with zipfile.ZipFile(target, "w") as out:
            for info in self._infos:
                clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                clone.compress_type = info.compress_type
                clone.external_attr = info.external_attr
                clone.create_system = info.create_system
                out.writestr(clone, self.member_bytes(info.filename))

    def to_bytes(self) -> bytes:
        """The whole file in memory - what a reader is handed to look at unsaved changes."""
        buf = io.BytesIO()
        self.write_to(buf)
        return buf.getvalue()

    def save(self, path: str, overwrite: bool = False) -> str:
        """Write the file to ``path``. Refuses to replace an existing file unless told to, and
        never leaves a half-written one: it goes to a temporary file next to the target first
        and is moved into place only once complete."""
        path = os.path.abspath(path)
        if os.path.exists(path) and not overwrite:
            raise FileExistsError(f"{path} already exists; pass overwrite to replace it")
        folder = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".rsf-", suffix=".part", dir=folder)
        os.close(fd)
        try:
            self.write_to(tmp)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return path
