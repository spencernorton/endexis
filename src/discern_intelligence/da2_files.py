"""Read DA2 report and query files, change them where they stand, and write them back.

A `.dareport` or `.daquery` (from DA2 or from Endexis) is edited in place. Only the XML members a
change touches are written again: every other member keeps its bytes, and whatever this module doesn't model (joins,
sub-queries, scripts, styles, other layout items) stays as it was. A change that can't be made safely raises
DA2FileError with a reason a DA2 user can act on, and nothing is changed.
"""

from __future__ import annotations

import functools
import hashlib
import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from .da2_generator import (LIVE_CWC_ELEMENT_MAP, VERIFIED_CWC_ENCOUNTER_UUIDS, DA2ReportGenerator,
                            daquery_element_type)

BIRT_NS = "http://www.eclipse.org/birt/2005/design"
B = f"{{{BIRT_NS}}}"
ET.register_namespace("", BIRT_NS)

FILE_KINDS = (".dareport", ".daquery")
MEMBER_KINDS = (".rpt", ".rptdesign", ".daquery")
MAX_FILE_BYTES = 1_000_000
# Parsing takes up to ~35 bytes of memory per byte of XML; DA2's own reports unpack to about 100 KB.
MAX_UNZIPPED_BYTES = 2_000_000
MAX_MEMBERS = 20
MAX_DEPTH = 200
# Fixed, so the same changes to the same file always give the same new ids.
ID_NAMESPACE = uuid.UUID("4f1b8f8e-8d0c-5b9a-9a51-0e5d2f3c7a10")
# A value on one of these fields picks out a patient (a FIN, an MRN or other alias, a phone number, a street, a birth
# date), and the site holds no patient data, so a file that filters on one is refused. Codes such as an alias's type
# or pool, or a financial class, aren't values of that kind.
PATIENT_ID_FIELD_RE = re.compile(
    r"(?i)\b(?:c?mrn|ssn|dob|e-?mail)\b|\bfin\b(?! class)|medical record|social security|national health"
    r"|financial (?:num|number|id)\b|\b(?:encounter|preadmit|member|account|acct|phone|phn) (?:num|number|nbr)\b"
    r"|\bstreet\b|\bbirth (?:dt|date)|alias\b(?! (?:pool|type|active|beg|end|update|id|desc)\b)"
    r"|^(?:person|per|patient|pat|enc|encntr|encounter) id$")
# Patient names: a name, or a list of them, is patient data. A pattern ("contains O'B", which Endexis writes) or DA2's
# own test-patient rule (Person Name Last < ZZ) is not, unless it holds a whole "Last, First" name.
PATIENT_NAME_FIELD_RE = re.compile(
    r"(?i)^(?:person|per|patient|pat)\b(?!.*\b(?:org|prsnl|phys|provider)\b).*\bname\b"
    r"|^name (?:first|last|middle|full|hx (?:full|last|first))|\b(?:card|guarantor|maiden|patient|person|sub per) name\b")
RANGE_OPS = frozenset({"LESS THAN", "GREATER THAN", "LESS THAN OR EQUAL", "GREATER THAN OR EQUAL"})
# Where a layout can use a column, by the BIRT property that holds the reference.
LAYOUT_USES = {"keyExpr": "a group", "expressionValue": "a group's contents entry", "onFetch": "a script",
               "beforeOpen": "a script", "afterOpen": "a script", "onCreate": "a script", "onRender": "a script",
               "testExpr": "a highlight rule", "expr": "a highlight rule", "expression": "a computed column",
               "valueExpr": "a visibility rule", "filterExpr": "a table filter", "sortExpr": "a sort"}
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DECL_RE = re.compile(rb"<\?xml[^>]*\?>\s*")


class DA2FileError(ValueError):
    """A file or change that was refused, with the reason for the user."""


@dataclass(frozen=True)
class Column:
    name: str
    uuid: str
    type: str
    subquery: str = ""  # set when the column comes from an inline sub-query (joinParent)


@dataclass(frozen=True)
class Filter:
    field: str
    operator: str  # DA2's operator ("IN", "NOT IN", "LIKE", "LESS THAN", ...), or "PROMPT" for a runtime prompt
    values: tuple[str, ...]

    def text(self) -> str:
        if self.operator == "PROMPT":
            return f"{self.field}: asked when the report runs"
        if self.operator in ("IN", "NOT IN"):
            return f"{self.field} {self.operator.lower()} ({', '.join(self.values)})"
        shown = ", ".join(f"'{v}'" for v in self.values)
        op = "is like" if self.operator == "LIKE" else self.operator.lower()
        return f"{self.field} {op} {shown}".rstrip()


@functools.cache
def _generator() -> DA2ReportGenerator:
    return DA2ReportGenerator()


@functools.cache
def _uuid_names() -> dict[str, str]:
    """The catalog's name for each DA2 element id it knows."""
    names = {str(e.get("element_uuid", "")).lower(): n for n, e in _generator()._da2_catalog.items()}
    names.update({v[1].lower(): k for k, v in LIVE_CWC_ELEMENT_MAP.items()})
    return names


def _known_uuids() -> frozenset[str]:
    return frozenset(_uuid_names())


def _identifies(fields: set[str], operator: str, values: list[str]) -> bool:
    """Whether a filter with these literal values on a field with these names would pick out a patient."""
    if not any(v.strip() for v in values):
        return False
    if any(PATIENT_ID_FIELD_RE.search(f) for f in fields):
        return True
    patterns = operator in ("LIKE", "NOT LIKE") and all("%" in v and "," not in v for v in values if v.strip())
    return any(PATIENT_NAME_FIELD_RE.search(f) for f in fields) and not patterns and operator not in RANGE_OPS


@functools.cache
def _element_names() -> dict[str, str]:
    names = {n.lower(): n for n in _generator()._da2_catalog}
    names.update({n.lower(): n for n in LIVE_CWC_ELEMENT_MAP})
    return names


def element_names() -> frozenset[str]:
    """Every DA2 element name Endexis knows, spelled as the catalog spells it."""
    return frozenset(_element_names().values())


def known_element(name: str) -> str | None:
    """The catalog's own spelling of a DA2 element name (any case), or None if Endexis doesn't know it."""
    return _element_names().get(str(name).strip().lower())


def shown(text: object) -> str:
    """A DA2 name or value as inert markdown: one code span on one line, so text from an uploaded file can't add a link,
    an image or HTML to a reply (Open WebUI ends a <details> block at the first </details>, even in a code span)."""
    text = " ".join(str(text).replace("`", "'").replace("<", "‹").replace(">", "›").split())
    return f"`{text if len(text) <= 200 else text[:199] + '…'}`" if text else "`(blank)`"


def element_for(name: str, domain: str) -> tuple[str, str, str]:
    """(name, uuid, type) of the DA2 element called `name` in `domain`, as the generator writes it in a .daquery."""
    key = known_element(name)
    if not key:
        raise DA2FileError(f"Endexis doesn't know a DA2 column called {shown(name)}.")
    if key in LIVE_CWC_ELEMENT_MAP:
        ename, euuid, etype = LIVE_CWC_ELEMENT_MAP[key]
        edomain = "CWC Encounter"
    else:
        el = _generator()._da2_catalog[key]
        ename, euuid = key, str(el.get("element_uuid") or "")
        etype = daquery_element_type(key, str(el.get("ccl_data_type") or ""))
        edomain = str(el.get("da2_domain") or "")
    if domain not in [d.strip() for d in edomain.split("/")]:
        raise DA2FileError(f"{shown(ename)} is in DA2's {edomain or 'unknown'} domain, not this query's {shown(domain)}.")
    try:
        synthetic = uuid.UUID(euuid).version == 5
    except ValueError:
        synthetic = True
    if synthetic or domain == "CWC Encounter" and euuid not in VERIFIED_CWC_ENCOUNTER_UUIDS:
        raise DA2FileError(f"Endexis has no checked DA2 id for {shown(ename)}, so it can't add it. Add it in DA2.")
    return ename, euuid, etype


def _parse(body: bytes, member: str) -> ET.Element:
    if b"\x00" in body:  # UTF-16 would hide a DOCTYPE from the check below
        raise DA2FileError(f"{shown(member)} isn't UTF-8 text, as DA2 files are.")
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise DA2FileError(f"{shown(member)} has a DOCTYPE, which DA2 files never have.")
    enc = re.match(rb"""\s*<\?xml[^>]*?\bencoding\s*=\s*["']([^"']*)""", body.removeprefix(b"\xef\xbb\xbf"))
    if enc and enc[1].lower() not in (b"utf-8", b"utf8"):  # it would be written back as UTF-8 under the old name
        raise DA2FileError(f"{shown(member)} is in {shown(enc[1].decode('ascii', 'replace'))} text; DA2 files are UTF-8.")
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        root = ET.fromstring(body, parser=parser)
    except ET.ParseError as exc:
        raise DA2FileError(f"{shown(member)} isn't well-formed XML ({exc}).") from None
    depth, level = 0, [root]
    while level:  # writing it back recurses once per level
        depth, level = depth + 1, [c for n in level for c in n]
    if depth > MAX_DEPTH:
        raise DA2FileError(f"{shown(member)} is nested {depth} levels deep; DA2 files are far shallower.")
    return root


def _unzip(data: bytes, name: str) -> dict[str, bytes]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DA2FileError(f"{name} doesn't open as a .dareport.") from None
    infos = zf.infolist()
    if not infos or len(infos) > MAX_MEMBERS:
        raise DA2FileError(f"{name} holds {len(infos)} files; a .dareport holds a few.")
    out: dict[str, bytes] = {}
    total = 0
    for info in infos:
        mname = info.filename
        if info.is_dir() or "/" in mname or "\\" in mname or mname.startswith(".") or mname in out or \
                PurePosixPath(mname).suffix.lower() not in MEMBER_KINDS:
            raise DA2FileError(f"{name} holds {shown(mname)}, which isn't part of a DA2 report.")
        try:
            with zf.open(info) as fh:
                body = fh.read(MAX_UNZIPPED_BYTES + 1)
        except (RuntimeError, NotImplementedError, zipfile.BadZipFile, OSError):
            raise DA2FileError(f"{name} can't be unpacked (is it password-protected?).") from None
        total += len(body)
        if total > MAX_UNZIPPED_BYTES:
            raise DA2FileError(f"{name} unpacks to more than {MAX_UNZIPPED_BYTES:,} bytes.")
        out[mname] = body
    return out


def read_da2_file(data: bytes, filename: str) -> "DA2Files":
    """Reads an uploaded `.dareport` or `.daquery`. DA2FileError says why one can't be used."""
    name = PurePosixPath(str(filename).replace("\\", "/")).name or "file"
    ext = PurePosixPath(name).suffix.lower()
    if ext in (".rpt", ".rptdesign"):
        raise DA2FileError(f"A {ext} is only part of a report. Upload the .dareport, which holds the .rpt, the "
                           "layout and the queries, or the .daquery.")
    if ext not in FILE_KINDS:
        raise DA2FileError(f"{name} isn't a DA2 report or query. Upload a .dareport or .daquery; data exports "
                           "(CSV, Excel, PDF) can't be used here.")
    if len(data) > MAX_FILE_BYTES:
        raise DA2FileError(f"{name} is {len(data):,} bytes; files here can be up to {MAX_FILE_BYTES:,}.")
    files = DA2Files(name, _unzip(data, name) if ext == ".dareport" else {name: bytes(data)})
    named = files.patient_fields()
    if named:
        raise DA2FileError(f"{name} filters on patient identifiers ({', '.join(map(shown, named))}). This site "
                           "can't hold patient data: take those values out in DA2, then upload it again.")
    return files


class _Query:
    """One DA2 query: a .daquery file, or one in a .dareport."""

    def __init__(self, member: str, root: ET.Element):
        self.member, self.root = member, root
        self.head = root.find("queries")
        qlist = root.find("query_list")
        items = [c for c in (qlist if qlist is not None else []) if str(c.tag).startswith("query_list_item")]
        self.main = next((i for i in items if i.get("inline_ind", "0") == "0"), None)
        self.elements = self.main.find("output/elements") if self.main is not None else None
        if self.head is None or self.elements is None:
            raise DA2FileError(f"{shown(member)} isn't a DA2 query: it has no output columns.")
        dom = self.main.find("domain")
        self.domain = dom.get("name", "") if dom is not None else ""

    @property
    def name(self) -> str:
        return self.head.get("name", "")

    @property
    def uuid(self) -> str:
        return self.head.get("uuid", "")

    def columns(self) -> list[Column]:
        return [Column(e.get("name", ""), e.get("uuid", ""), e.get("type", ""), e.get("joinParent", ""))
                for e in self.elements if e.tag == "element"]

    def filter_nodes(self) -> list[ET.Element]:
        qual = self.main.find("qualification")
        return [] if qual is None else [n for n in qual.iter() if str(n.tag).startswith("filter") and n.find("element") is not None]

    def filters(self) -> list[Filter]:
        out = []
        for node in self.filter_nodes():
            field = node.find("element").get("name", "")
            if node.find("runtimeprompt") is not None:
                out.append(Filter(field, "PROMPT", ()))
                continue
            op = node.find("operator")
            values = tuple(v.get("display") or v.get("text") or v.get("cdfmeaning") or ""
                           for v in node.iterfind("values/value"))
            out.append(Filter(field, op.get("type", "") if op is not None else "", values))
        return out

    def renumber(self) -> None:
        cols = [e for e in self.elements if e.tag == "element"]
        for i, el in enumerate(cols, 1):
            el.set("index", str(i))
        self.elements.set("xCount", str(len(cols)))


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _prop(node: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in node if c.get("name") == name and c.tag in (f"{B}property", f"{B}text-property",
                                                                        f"{B}expression")), None)


class _Layout:
    """The BIRT layout bound to one query's data set: its column lists, and the one plain table that shows it."""

    def __init__(self, root: ET.Element, dataset: ET.Element):
        self.root, self.ds = root, dataset
        self.ds_name = dataset.get("name", "")
        bound = [e for e in root.iter() if _text(_prop(e, "dataSet")) == self.ds_name]
        tables = [e for e in bound if e.tag == f"{B}table"]
        self.table = tables[0] if len(bound) == 1 and len(tables) == 1 else None
        self.why_not = "" if self.table is not None else (
            "its layout shows that data in more than one place" if bound else "its layout has no table for it")
        if self.table is not None and not self._plain():
            self.table, self.why_not = None, "its table merges cells"

    def _rows(self) -> list[ET.Element]:
        return [r for r in self.table.iter(f"{B}row")]

    def width(self, row: ET.Element) -> int:
        """How many table columns the row's cells cover. BIRT pads a shorter row with empty cells at the end."""
        return sum(self._span(c, "colSpan") for c in row.findall(f"{B}cell"))

    def _span(self, cell: ET.Element, name: str) -> int:
        value = _text(_prop(cell, name))
        return int(value) if value.isascii() and value.isdigit() else 1

    def _plain(self) -> bool:
        width = len(self.table.findall(f"{B}column"))
        for row in self._rows():
            cells = row.findall(f"{B}cell")
            if any(self._span(c, "rowSpan") > 1 for c in cells) or self.width(row) > width:
                return False
        return width > 0

    def can_add(self) -> bool:
        detail = self.table.find(f"{B}detail")
        row = detail.find(f"{B}row") if detail is not None else None
        return row is None or self.width(row) == len(self.table.findall(f"{B}column"))

    def _list(self, name: str, parent: ET.Element | None = None) -> ET.Element | None:
        parent = self.ds if parent is None else parent
        return next((c for c in parent if c.tag == f"{B}list-property" and c.get("name") == name), None)

    def _result_sets(self) -> list[ET.Element]:
        cached = next((c for c in self.ds if c.tag == f"{B}structure" and c.get("name") == "cachedMetaData"), None)
        return [x for x in (self._list("resultSet"), self._list("resultSet", cached) if cached is not None else None)
                if x is not None]

    def bindings(self) -> ET.Element | None:
        return self._list("boundDataColumns", self.table) if self.table is not None else None

    def binding_names(self, column: str) -> set[str]:
        """Binding names whose expression is this data set column, in any item bound to the data set."""
        want = {f'dataSetRow["{column}"]', f"dataSetRow['{column}']"}
        out = set()
        for lst in self.root.iter(f"{B}list-property"):
            if lst.get("name") != "boundDataColumns":
                continue
            for s in lst.findall(f"{B}structure"):
                if _text(_prop(s, "expression")) in want:
                    out.add(_text(_prop(s, "name")))
        return out

    def next_id(self) -> int:
        ids = [int(i) for e in self.root.iter() if (i := e.get("id") or "").isascii() and i.isdigit()]
        return max(ids or [0]) + 1


class DA2Files:
    """An uploaded DA2 report or query, with the changes made to it so far."""

    def __init__(self, filename: str, members: dict[str, bytes]):
        self.filename = filename
        self.kind = PurePosixPath(filename).suffix.lower()
        self._raw = dict(members)
        self._trees = {m: _parse(body, m) for m, body in members.items()}
        self._dirty: set[str] = set()
        self.changes: list[str] = []
        self._queries: list[_Query] = []
        self._layouts: dict[str, tuple[str, _Layout]] = {}  # query uuid -> (member, layout)
        for m, root in self._trees.items():
            ext = PurePosixPath(m).suffix.lower()
            want = {".daquery": "cclquery", ".rpt": "dareport", ".rptdesign": f"{B}report"}[ext]
            if root.tag != want:
                raise DA2FileError(f"{shown(m)} isn't a DA2 {ext} file.")
            if ext == ".daquery":
                self._queries.append(_Query(m, root))
        for m, root in self._trees.items():
            if root.tag != f"{B}report":
                continue
            for ds in root.iter(f"{B}oda-data-set"):
                qtext = next((c for c in ds if c.tag == f"{B}xml-property" and c.get("name") == "queryText"), None)
                body = (qtext.text or "").strip() if qtext is not None else ""
                if UUID_RE.fullmatch(body):  # a .dareport's layout names its query by id
                    self._layouts[body] = (m, _Layout(root, ds))
        if not self._queries:
            raise DA2FileError(f"{filename} has no DA2 query in it.")

    # ---- what the files hold -------------------------------------------------------------------------------------

    @property
    def report(self) -> dict[str, str]:
        rpt = next((r for r in self._trees.values() if r.tag == "dareport"), None)
        node = rpt.find("report") if rpt is not None else None
        q = self._queries[0]
        return {"name": node.get("name", "") if node is not None else q.name,
                "owner_group": (node.get("owner_group_display") if node is not None else None)
                or q.head.get("owner_group_display", "")}

    def queries(self) -> list[tuple[str, str, list[Column], list[Filter]]]:
        return [(q.name, q.domain, q.columns(), q.filters()) for q in self._queries]

    def unknown_columns(self) -> list[str]:
        known = _known_uuids()
        return [c.name for q in self._queries for c in q.columns() if c.uuid.lower() not in known]

    def patient_fields(self) -> list[str]:
        """Fields filtered on literal values that identify patients, in any query or sub-query (the values themselves
        are never returned). A field is judged by its own name and by the catalog's name for its DA2 id."""
        out = []
        for q in self._queries:
            for node in q.root.iter():
                el = node.find("element") if str(node.tag).startswith("filter") else None  # a comment's tag isn't text
                if el is None:
                    continue
                values = [t for v in node.iter("value") for t in (*v.attrib.values(), v.text or "")]
                op = node.find("operator")
                op = op.get("type", "") if op is not None else ""
                names = (el.get("name", ""), _uuid_names().get(el.get("uuid", "").lower(), ""))  # its own, its id's
                hit = next((n for n in names if n and _identifies({n}, op, values)), None)
                if hit and hit not in out:
                    out.append(hit)
        return out

    def summary(self) -> str:
        rep = self.report
        owner = shown(rep["owner_group"]) if rep["owner_group"] else "not set"
        lines = [f"**{shown(rep['name'])}** ({shown(self.filename)}), owner group {owner}"]
        unknown = set(self.unknown_columns())
        for q in self._queries:
            cols, filters = q.columns(), q.filters()
            listed = ", ".join(shown(c.name) + (" (not in Endexis's catalog)" if c.name in unknown else "") for c in cols)
            lines += [f"- Query {shown(q.name)}, {shown(q.domain) if q.domain else 'unknown'} domain, {len(cols)} columns: "
                      f"{listed or 'none'}",
                      f"- Filters: {'; '.join(shown(f.text()) for f in filters) or 'none'}"]
            layout = self._layouts.get(q.uuid)
            if layout and layout[1].table is None:
                lines.append(f"- Layout: I can change the query but not the layout, because {layout[1].why_not}.")
        return "\n".join(lines)

    # ---- changes -------------------------------------------------------------------------------------------------

    def _query_for(self, column: str | None = None, domain_of: str | None = None) -> _Query:
        if column is not None:
            hits = [q for q in self._queries if any(c.name.lower() == column.lower() for c in q.columns())]
            if not hits:
                raise DA2FileError(f"The report has no column called {shown(column)}.")
            if len(hits) > 1:
                raise DA2FileError(f"More than one query has {shown(column)}; change it in DA2.")
            return hits[0]
        if len(self._queries) > 1 and domain_of:
            hits = [q for q in self._queries if q.domain == domain_of]
            if len(hits) == 1:
                return hits[0]
        if len(self._queries) > 1:
            raise DA2FileError("This report has more than one query, so I can't tell which one to change. Change it "
                               "in DA2, or upload that query's .daquery.")
        return self._queries[0]

    def _done(self, q: _Query, text: str, *members: str) -> str:
        self._dirty.update({q.member, *members})
        self.changes.append(text)
        return text

    def add_column(self, name: str) -> str:
        """Adds a DA2 element as the query's last column, and to its table when the layout is a plain table."""
        found, why = [], ""
        for cand in self._queries:
            try:
                found.append((cand, element_for(name, cand.domain)))
            except DA2FileError as exc:
                why = why or str(exc)
        if not found:
            raise DA2FileError(why)
        if len(found) > 1:
            raise DA2FileError(f"More than one query here could take {shown(name)}; add it in DA2.")
        q, (ename, euuid, etype) = found[0]
        if any(c.uuid.lower() == euuid.lower() or c.name.lower() == ename.lower() for c in q.columns()):
            raise DA2FileError(f"{shown(ename)} is already in the report.")
        member, layout = self._layouts.get(q.uuid, ("", None))
        if layout is not None and layout.table is not None and layout.binding_names(ename):
            raise DA2FileError(f"The layout already has a {shown(ename)} field; add the column in DA2.")
        ET.SubElement(q.elements, "element", {"index": "0", "name": ename, "type": etype, "uuid": euuid})
        q.renumber()
        note = ""
        if layout is None:
            note = " Add it to the report's layout in DA2." if self.kind != ".daquery" else ""
        elif layout.table is None or not layout.can_add():
            note = f" The layout is unchanged ({layout.why_not or 'its detail row is short'}): add it there in DA2."
        else:
            self._layout_add(layout, ename, euuid, etype)
        return self._done(q, f"Added the column {shown(ename)}.{note}", member)

    def remove_column(self, name: str) -> str:
        q = self._query_for(column=name)
        col = next(c for c in q.columns() if c.name.lower() == name.lower())
        member, layout = self._layouts.get(q.uuid, ("", None))
        if layout is not None:
            self._layout_check_remove(layout, col.name)
        for el in [e for e in q.elements if e.tag == "element" and e.get("name") == col.name]:
            q.elements.remove(el)
        q.renumber()
        if layout is not None:
            self._layout_remove(layout, col.name, col.uuid)
        return self._done(q, f"Removed the column {shown(col.name)}.", member)

    def _filter_on(self, q: _Query, field: str) -> list[ET.Element]:
        return [n for n in q.filter_nodes() if n.find("element").get("name", "").lower() == field.lower()
                and n.find("runtimeprompt") is None]

    def _parents(self, q: _Query) -> dict[ET.Element, ET.Element]:
        return {child: parent for parent in q.main.iter() for child in parent}

    def _put_filter(self, q: _Query, field: str, new: ET.Element, same: str) -> str:
        """Replaces the one `same`-operator filter on `field`, or adds `new` to the query's filters with AND."""
        old = [n for n in self._filter_on(q, field) if (n.find("operator") is not None and
                                                      n.find("operator").get("type") == same)]
        if len(old) > 1:
            raise DA2FileError(f"The query has {len(old)} filters on {shown(field)}; change them in DA2.")
        qual = q.main.find("qualification")
        if old:
            parent = self._parents(q)[old[0]]
            parent[list(parent).index(old[0])] = new
            return "Changed"
        if qual is None:
            qual = ET.Element("qualification")
            out = q.main.find("output")
            q.main.insert(list(q.main).index(out) + 1 if out is not None else len(q.main), qual)
        kids = list(qual)
        if len(kids) > 1:
            raise DA2FileError("The query's filters are laid out in a way I can't add to safely. Add it in DA2.")
        if kids:
            both = ET.Element("operator", {"type": "AND"})
            qual.remove(kids[0])
            both.extend([kids[0], new])
            qual.insert(0, both)
        else:
            qual.append(new)
        return "Added"

    def only_values(self, field: str, displays: list[str]) -> str:
        """Keeps only rows whose coded `field` is one of `displays` (a DA2 IN list), replacing any filter on it."""
        q = self._query_for(domain_of="CWC Encounter")
        if q.domain != "CWC Encounter":
            raise DA2FileError(f"Endexis knows code values for CWC Encounter fields only, and this query is {shown(q.domain)}.")
        try:
            vf = _generator()._resolve_value_filters({field: list(displays)})[0]
        except ValueError as exc:
            raise DA2FileError(f"I can't make that filter: {exc}.") from None
        name = LIVE_CWC_ELEMENT_MAP[vf["name"]][0]
        node = ET.Element("filter", {"joinType": "0", "mode": "0", "prompt_element_uuid": vf["uuid"],
                                     "prompt_type": "0", "useIndex": "1"})
        ET.SubElement(node, "element", {"name": name, "type": "1", "uuid": vf["uuid"]})
        ET.SubElement(node, "operator", {"type": "IN"})
        values = ET.SubElement(node, "values", {"type": "LIST", "xCount": str(len(vf["values"]))})
        for display, code in vf["values"]:
            ET.SubElement(values, "value", {"display": display, "text": f"{code}.00"})
        verb = self._put_filter(q, name, node, "IN")
        return self._done(q, f"{verb} the filter {shown(vf['text'])}.")

    def text_match(self, field: str, pattern: str) -> str:
        """A DA2 Is Like filter on a free-text field, replacing any filter on it."""
        q = self._query_for(domain_of="CWC Encounter")
        if q.domain != "CWC Encounter":
            raise DA2FileError(f"Endexis knows text fields in CWC Encounter only, and this query is {shown(q.domain)}.")
        try:
            lf = _generator()._resolve_like_filters({field: pattern})[0]
        except (ValueError, KeyError) as exc:
            raise DA2FileError(f"I can't make that filter: {exc}.") from None
        name = LIVE_CWC_ELEMENT_MAP[lf["name"]][0]
        if _identifies({name, lf["name"]}, "LIKE", [lf["like"]]):
            raise DA2FileError(f"{shown(name)} identifies patients, and this site can't hold patient data. Set that "
                               "filter in DA2.")
        node = ET.Element("filter", {"joinType": "0", "mode": "0", "useIndex": "1"})
        ET.SubElement(node, "element", {"name": name, "type": "3", "uuid": lf["uuid"]})
        ET.SubElement(node, "operator", {"type": "LIKE"})
        ET.SubElement(ET.SubElement(node, "values", {"type": "LIST", "xCount": "1"}), "value", {"text": lf["like"]})
        verb = self._put_filter(q, name, node, "LIKE")
        return self._done(q, f"{verb} the filter {shown(lf['text'])}.")

    def remove_filter(self, field: str) -> str:
        hits = [(q, n) for q in self._queries for n in self._filter_on(q, field)]
        if not hits:
            raise DA2FileError(f"The report has no filter on {shown(field)}.")
        if len(hits) > 1:
            raise DA2FileError(f"The report has {len(hits)} filters on {shown(field)}; change them in DA2.")
        q, node = hits[0]
        parents = self._parents(q)
        parent = parents[node]
        if parent.tag == "qualification":
            parent.remove(node)
            if not len(parent):
                q.main.remove(parent)
        elif parent.tag == "operator" and parent.get("type") == "AND" and len(parent) == 2:
            sibling = next(c for c in parent if c is not node)
            grand = parents[parent]
            grand[list(grand).index(parent)] = sibling
        else:
            raise DA2FileError(f"The filter on {shown(field)} is part of an either-or group; change that group in DA2.")
        field_name = node.find("element").get("name", field)
        return self._done(q, f"Removed the filter on {shown(field_name)}.")

    def set_owner_group(self, owner: str) -> str:
        owner = str(owner).strip()
        if not owner or len(owner) > 120 or not owner.isprintable():
            raise DA2FileError("That owner group name can't be used.")
        for q in self._queries:
            q.head.set("owner_group_display", owner)
            self._dirty.add(q.member)
        for m, root in self._trees.items():
            node = root.find("report") if root.tag == "dareport" else None
            if node is not None:
                node.set("owner_group_display", owner)
                self._dirty.add(m)
        self.changes.append(f"Owner group set to {shown(owner)}.")
        return self.changes[-1]

    # ---- layout --------------------------------------------------------------------------------------------------

    def _layout_add(self, layout: _Layout, name: str, euuid: str, etype: str) -> None:
        btype = "date-time" if etype in ("2", "4") else "string"
        nid = layout.next_id()

        def struct(parent: ET.Element, *props: tuple[str, str, str]) -> None:
            s = ET.SubElement(parent, f"{B}structure")
            for tag, pname, text in props:
                node = ET.SubElement(s, f"{B}{tag}", {"name": pname})
                if tag == "expression":
                    node.set("type", "javascript")
                node.text = text

        hints = layout._list("columnHints")
        if hints is not None:
            first = _text(_prop(hints[0], "columnName")) if len(hints) else ""
            struct(hints, ("property", "columnName", euuid if UUID_RE.fullmatch(first) else name),
                   ("property", "alias", name), ("text-property", "displayName", name),
                   ("text-property", "heading", name))
        for rs in layout._result_sets():
            struct(rs, ("property", "position", str(len(rs.findall(f"{B}structure")) + 1)),
                   ("property", "name", name), ("property", "dataType", btype))
        bindings = layout.bindings()
        if bindings is None:
            bindings = ET.Element(f"{B}list-property", {"name": "boundDataColumns"})
            layout.table.insert(0, bindings)
        struct(bindings, ("property", "name", name), ("text-property", "displayName", name),
               ("expression", "expression", f'dataSetRow["{name}"]'), ("property", "dataType", btype))
        cols = layout.table.findall(f"{B}column")
        full = [row for row in layout._rows() if layout.width(row) == len(cols)]
        layout.table.insert(list(layout.table).index(cols[-1]) + 1, ET.Element(f"{B}column", {"id": str(nid)}))
        nid += 1
        header, detail = layout.table.find(f"{B}header"), layout.table.find(f"{B}detail")
        first_header = header.find(f"{B}row") if header is not None else None
        first_detail = detail.find(f"{B}row") if detail is not None else None
        for row in full:
            cell = ET.SubElement(row, f"{B}cell", {"id": str(nid)})
            nid += 1
            if row is first_header:
                item = ET.SubElement(cell, f"{B}label", {"id": str(nid)})
                ET.SubElement(item, f"{B}text-property", {"name": "text"}).text = name
                nid += 1
            elif row is first_detail:
                item = ET.SubElement(cell, f"{B}data", {"id": str(nid)})
                ET.SubElement(item, f"{B}property", {"name": "resultSetColumn"}).text = name
                nid += 1

    def _layout_uses(self, layout: _Layout, column: str) -> list[str]:
        """Where the layout uses `column` other than its own binding, detail cell and data set entries."""
        names = layout.binding_names(column) | {column}
        refs = re.compile("|".join(rf"""(?:dataSetRow|row)\[\s*["']{re.escape(n)}["']\s*\]""" for n in names))
        own = set()
        for lst in layout.root.iter(f"{B}list-property"):
            if lst.get("name") == "boundDataColumns":
                own.update(s for s in lst.findall(f"{B}structure") if _text(_prop(s, "name")) in names)
        detail = layout.table.find(f"{B}detail") if layout.table is not None else None
        for cell in detail.iter(f"{B}cell") if detail is not None else []:
            if any(_text(_prop(d, "resultSetColumn")) in names for d in cell.iter(f"{B}data")):
                own.add(cell)
        own_nodes = {n for s in own for n in s.iter()}
        where = []
        for node in layout.root.iter():
            if node in own_nodes:
                continue
            text = node.text or ""
            if refs.search(text):
                where.append(LAYOUT_USES.get(node.get("name") or "", "an expression"))
            elif node.get("name") == "resultSetColumn" and text.strip() in names:
                where.append("a field shown outside the detail row")
        return list(dict.fromkeys(where))

    def _layout_check_remove(self, layout: _Layout, column: str) -> None:
        uses = self._layout_uses(layout, column)
        if uses:
            raise DA2FileError(f"The layout uses {shown(column)} ({', '.join(uses[:3])}), so removing it would break the "
                               "report. Change the layout in DA2 first.")

    def _layout_remove(self, layout: _Layout, column: str, euuid: str) -> None:
        names = layout.binding_names(column) | {column}
        hints = layout._list("columnHints")
        for s in list(hints) if hints is not None else []:
            if _text(_prop(s, "alias")) == column or _text(_prop(s, "columnName")) in (column, euuid):
                hints.remove(s)
        for rs in layout._result_sets():
            for s in [s for s in rs.findall(f"{B}structure") if _text(_prop(s, "name")) == column]:
                rs.remove(s)
            for i, s in enumerate(rs.findall(f"{B}structure"), 1):
                pos = _prop(s, "position")
                if pos is not None:
                    pos.text = str(i)
        for lst in [x for x in layout.root.iter(f"{B}list-property") if x.get("name") == "boundDataColumns"]:
            for s in [s for s in lst.findall(f"{B}structure") if _text(_prop(s, "name")) in names
                      and _text(_prop(s, "expression")) in (f'dataSetRow["{column}"]', f"dataSetRow['{column}']")]:
                lst.remove(s)
        if layout.table is None:
            return
        detail = layout.table.find(f"{B}detail")
        position = None
        for row in detail.findall(f"{B}row") if detail is not None else []:
            at = 0
            for cell in row.findall(f"{B}cell"):
                if any(_text(_prop(d, "resultSetColumn")) in names for d in cell.iter(f"{B}data")):
                    position = at
                at += layout._span(cell, "colSpan")
        if position is None:
            return  # bound but not shown in the table
        cols = layout.table.findall(f"{B}column")
        layout.table.remove(cols[position])
        for row in layout._rows():
            at = 0
            for cell in row.findall(f"{B}cell"):
                span = layout._span(cell, "colSpan")
                if at <= position < at + span:
                    if span > 1:
                        _prop(cell, "colSpan").text = str(span - 1)
                    else:
                        row.remove(cell)
                    break
                at += span

    # ---- writing -------------------------------------------------------------------------------------------------

    def _serialize(self, member: str) -> bytes:
        decl = DECL_RE.match(self._raw[member])
        return (decl.group() if decl else b"") + ET.tostring(self._trees[member], encoding="unicode").encode("utf-8")

    def files(self, new_ids: bool = True) -> dict[str, bytes]:
        """The files to download. With new ids (the default), DA2 imports them as a new report and query instead of
        writing over the ones they came from. The same changes to the same file always give the same ids."""
        members = {m: self._serialize(m) if m in self._dirty else body for m, body in self._raw.items()}
        if new_ids:
            digest = hashlib.sha256("\n".join(self.changes).encode()).hexdigest()
            olds = [q.uuid for q in self._queries] + [r.find("report").get("uuid", "") for r in self._trees.values()
                                                      if r.tag == "dareport" and r.find("report") is not None]
            for old in [o for o in dict.fromkeys(olds) if UUID_RE.fullmatch(o)]:
                new = str(uuid.uuid5(ID_NAMESPACE, f"{old.lower()}|{digest}")).encode()
                members = {m: re.sub(re.escape(old).encode(), new, body, flags=re.I) for m, body in members.items()}
        if self.kind != ".dareport":
            return {self.filename: members[self.filename]}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for m, body in members.items():  # a fixed date: the same edits must give the same bytes
                zf.writestr(zipfile.ZipInfo(m, date_time=(1980, 1, 1, 0, 0, 0)), body, compress_type=zipfile.ZIP_DEFLATED)
        return {self.filename: buf.getvalue()}
