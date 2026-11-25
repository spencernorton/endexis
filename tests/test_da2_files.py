"""Reading, changing and writing DA2 report files in place (discern_intelligence.da2_files)."""

import copy
import io
import os
import pathlib
import re
import string
import struct
import time
import zipfile
import xml.etree.ElementTree as ET

import pytest
from unittest import mock

from discern_intelligence import da2_files
from discern_intelligence.da2_files import B, DA2FileError, element_names, known_element, read_da2_file, shown
from discern_intelligence.da2_generator import LIVE_CWC_ELEMENT_MAP, DA2ReportGenerator

CENSUS = "Active inpatient census with attending physician and nurse unit, prompt on admit date"
REQUESTS = [
    CENSUS,
    "Rehab therapy productivity report",
    "discharges last month with discharge disposition and payer",
    "lab results for inpatients",
    "orders placed by physician with order status",
    "daily appointments by unit with appointment state",
]


def _bundle(request=CENSUS, **kw):
    return DA2ReportGenerator().generate(request, owner_group_display="Test_Group", **kw)


def _dareport(request=CENSUS, **kw):
    return DA2ReportGenerator.build_dareport_zip_bytes(_bundle(request, **kw), owner_group_display="Test_Group")


def _members(data):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _member(data, ext):
    return next(body for name, body in _members(data).items() if name.endswith(ext))


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _columns(daquery):
    root = ET.fromstring(daquery)
    return [e.get("name") for e in root.find("query_list/query_list_item1/output/elements")]


def _table(rptdesign):
    return ET.fromstring(rptdesign).find(f".//{B}table")


def _well_formed_qualification(daquery):
    qual = ET.fromstring(daquery).find("query_list/query_list_item1/qualification")
    return qual is None or all(len(op) == 2 for op in qual.iter("operator") if op.get("type") in ("AND", "OR"))


@pytest.mark.parametrize("request_text", REQUESTS)
def test_every_generated_report_reads_back_unchanged(request_text):
    bundle = _bundle(request_text)
    data = DA2ReportGenerator.build_dareport_zip_bytes(bundle, owner_group_display="Test_Group")
    files = read_da2_file(data, "r.dareport")
    assert [q[0] for q in files.queries()] == [q.query_name for q in bundle.queries]
    for (_, _, cols, _), q in zip(files.queries(), bundle.queries):
        assert [c.name for c in cols] == _columns(q.daquery_xml)
    assert _members(files.files(new_ids=False)["r.dareport"]) == _members(data)


def test_new_ids_are_consistent_across_members_and_repeatable():
    data = _dareport()
    files = read_da2_file(data, "census.dareport")
    files.add_column("Enc Admit Source")
    out = files.files()["census.dareport"]
    again = read_da2_file(data, "census.dareport")
    again.add_column("Enc Admit Source")
    with mock.patch("time.time", return_value=time.time() + 86400):  # a day later, the same edit gives the same bytes
        assert again.files()["census.dareport"] == out
    rpt = ET.fromstring(_member(out, ".rpt"))
    query = ET.fromstring(_member(out, ".daquery")).find("queries")
    text = ET.fromstring(_member(out, ".rptdesign")).find(f".//{B}xml-property[@name='queryText']").text.strip()
    old = ET.fromstring(_member(data, ".rpt"))
    assert rpt.find("queries/query").get("uuid") == query.get("uuid") == text
    assert query.get("uuid") != old.find("queries/query").get("uuid")
    assert rpt.find("report").get("uuid") != old.find("report").get("uuid")
    for body in _members(out).values():
        assert old.find("report").get("uuid").encode() not in body


def test_ids_are_swapped_in_one_pass_whatever_their_case():
    a, b = "0f8fad5b-d9cb-469f-a165-70867728950e", "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    body = f'<q uuid="{a.upper()}">{b}{a}x{b.upper()}</q>'.encode()
    out = da2_files._swap_ids(body, {a.encode(): b"A" * 36, b.encode(): b"B" * 36})
    assert out == b'<q uuid="' + b"A" * 36 + b'">' + b"B" * 36 + b"A" * 36 + b"x" + b"B" * 36 + b"</q>"
    assert da2_files._swap_ids(body, {}) == body


def test_add_column_reaches_query_and_plain_table():
    files = read_da2_file(_dareport(), "census.dareport")
    width = len(_table(_member(_dareport(), ".rptdesign")).findall(f"{B}column"))
    assert files.add_column("enc admit source") == "Added the column `Enc Admit Source`."
    out = files.files()["census.dareport"]
    daquery = ET.fromstring(_member(out, ".daquery"))
    elements = daquery.find("query_list/query_list_item1/output/elements")
    assert elements[-1].get("name") == "Enc Admit Source" and elements[-1].get("index") == str(len(elements))
    assert elements.get("xCount") == str(len(elements))
    table = _table(_member(out, ".rptdesign"))
    assert len(table.findall(f"{B}column")) == width + 1
    header = table.find(f"{B}header/{B}row")
    detail = table.find(f"{B}detail/{B}row")
    assert header.findall(f"{B}cell")[-1].find(f"{B}label/{B}text-property").text == "Enc Admit Source"
    assert detail.findall(f"{B}cell")[-1].find(f"{B}data/{B}property").text == "Enc Admit Source"
    exprs = [e.text for e in table.iter(f"{B}expression")]
    assert 'dataSetRow["Enc Admit Source"]' in exprs
    ids = [e.get("id") for e in ET.fromstring(_member(out, ".rptdesign")).iter() if e.get("id")]
    assert len(ids) == len(set(ids))


def test_add_column_refuses_unknown_wrong_domain_and_duplicates():
    files = read_da2_file(_dareport(), "census.dareport")
    with pytest.raises(DA2FileError, match="doesn't know"):
        files.add_column("Favourite Colour")
    with pytest.raises(DA2FileError, match="already in the report"):
        files.add_column("Enc Loc Room")
    with pytest.raises(DA2FileError, match="domain"):
        files.add_column("GL Account Alias Desc")
    assert files.changes == []


@pytest.mark.parametrize("digits, adds", [(18, True), (19, False), (999, False)])
def test_add_refuses_a_layout_with_ids_longer_than_da2_writes(digits, adds):
    members = _members(_dareport())
    name = next(n for n in members if n.endswith(".rptdesign"))
    body = members[name].decode()
    at = body.index(">", body.index("<report")) + 1
    members[name] = (body[:at] + f'<label id="{"9" * digits}"/>' * 400 + body[at:]).encode()
    files = read_da2_file(_zip(members), "census.dareport")
    start = time.process_time()
    if adds:
        assert files.add_column("Enc Admit Source").startswith("Added")
    else:
        with pytest.raises(DA2FileError, match="ids far longer than DA2 writes"):
            files.add_column("Enc Admit Source")
        assert files.changes == []
    assert time.process_time() - start < 0.5


def test_remove_column_everywhere_unless_the_layout_uses_it():
    data = _dareport()
    files = read_da2_file(data, "census.dareport")
    assert files.remove_column("Enc Loc Bed") == "Removed the column `Enc Loc Bed`."
    out = files.files()["census.dareport"]
    assert "Enc Loc Bed" not in _columns(_member(out, ".daquery"))
    rptdesign = _member(out, ".rptdesign").decode()
    assert "Enc Loc Bed" not in rptdesign
    table = _table(rptdesign.encode())
    width = len(table.findall(f"{B}column"))
    assert width == len(_table(_member(data, ".rptdesign")).findall(f"{B}column")) - 1
    assert len(table.find(f"{B}detail/{B}row").findall(f"{B}cell")) == width
    with pytest.raises(DA2FileError, match="a group"):
        files.remove_column("Enc Loc Nurse Unit")
    with pytest.raises(DA2FileError, match="a script"):
        files.remove_column("Enc Id")
    with pytest.raises(DA2FileError, match="no column"):
        files.remove_column("Enc Loc Bed")


def test_value_filters_replace_only_an_in_list_and_keep_the_tree_well_formed():
    files = read_da2_file(_dareport(value_filters={"Enc Type": ["Inpatient"]}), "census.dareport")
    assert files.only_values("Enc Type", ["Inpatient", "Observation"]) == \
        "Changed the filter `Enc Type in (Inpatient, Observation)`."
    assert files.only_values("Enc Status", ["Active"]).startswith("Added the filter `Enc Status in (Active)`")
    texts = [f.text() for f in files.queries()[0][3]]
    assert "Enc Status not in (Discharged)" in texts and "Enc Status in (Active)" in texts
    with pytest.raises(DA2FileError, match="can't make that filter"):
        files.only_values("Enc Type", ["Not A Real Type"])
    daquery = _member(files.files()["census.dareport"], ".daquery")
    assert _well_formed_qualification(daquery)
    values = ET.fromstring(daquery).find(".//filter/element[@name='Enc Type']/..").find("values")
    assert values.get("xCount") == "2" and all(v.get("text", "").endswith(".00") for v in values)


def test_text_match_does_not_replace_the_test_patient_rule():
    files = read_da2_file(_dareport(), "census.dareport")
    assert files.text_match("Person Name Last", "o'b%") == "Added the filter `Person Name Last is like 'OB%'`."
    texts = [f.text() for f in files.queries()[0][3]]
    assert "Person Name Last less than 'ZZ'" in texts and "Person Name Last is like 'OB%'" in texts
    assert files.text_match("Person Name Last", "smi%").startswith("Changed")


def test_remove_filter_and_refusals():
    files = read_da2_file(_dareport(), "census.dareport")
    assert files.remove_filter("enc status") == "Removed the filter on `Enc Status`."
    assert all("Enc Status" not in f.text() for f in files.queries()[0][3])
    assert _well_formed_qualification(_member(files.files()["census.dareport"], ".daquery"))
    with pytest.raises(DA2FileError, match="no filter"):
        files.remove_filter("Enc Status")


def test_owner_group_is_set_in_report_and_query():
    files = read_da2_file(_dareport(), "census.dareport")
    files.set_owner_group("Corporate_Reporting")
    out = files.files()["census.dareport"]
    assert ET.fromstring(_member(out, ".rpt")).find("report").get("owner_group_display") == "Corporate_Reporting"
    assert ET.fromstring(_member(out, ".daquery")).find("queries").get("owner_group_display") == "Corporate_Reporting"
    assert files.report["owner_group"] == "Corporate_Reporting"


def test_a_bare_daquery():
    files = read_da2_file(_bundle().queries[0].daquery_xml.encode(), "q.daquery")
    assert files.add_column("Enc Admit Source") == "Added the column `Enc Admit Source`."
    assert _columns(files.files()["q.daquery"])[-1] == "Enc Admit Source"


def test_a_report_with_two_queries_asks_which():
    bundle = _bundle("orders placed by physician with order status")
    assert len(bundle.queries) == 2
    files = read_da2_file(DA2ReportGenerator.build_dareport_zip_bytes(bundle), "o.dareport")
    with pytest.raises(DA2FileError, match="More than one query"):
        files.add_column("Enc Admit Source")


@pytest.mark.parametrize("name, data, why", [
    ("export.csv", b"a,b\n1,2\n", "isn't a DA2 report"),
    ("r.rpt", b"<dareport/>", "only part of a report"),
    ("r.rptdesign", b"<report/>", "only part of a report"),
    ("r.dareport", b"not a zip", "doesn't open"),
    ("q.daquery", b"<cclquery><queries/></cclquery>" + b" " * 1_000_001, "can be up to"),
    ("q.daquery", b'<!DOCTYPE x [<!ENTITY a "b">]><cclquery/>', "DOCTYPE"),
    ("q.daquery", '<!DOCTYPE x [<!ENTITY a "b">]><cclquery/>'.encode("utf-16"), "isn't UTF-8"),
    ("q.daquery", b'<?xml version="1.0" encoding="ISO-8859-1"?><cclquery/>', "DA2 files are UTF-8"),
    ("q.daquery", b"<cclquery>" + b"<a>" * 300 + b"</a>" * 300 + b"</cclquery>", "nested 301 levels"),
    pytest.param("q.daquery", b"<cclquery>" + b"<a/>" * 20_000 + b"</cclquery>", "has 20,001 XML elements",
                 id="too-many-elements"),
    pytest.param("q.daquery", b"<cclquery>" + b"<a/>" * 40_100 + b"</cclquery>", "over 20,000 XML elements",
                 id="too-many-tags"),  # counted before it is parsed
    pytest.param("q.daquery", b"<cclquery" + b"".join(b' a%d=""' % i for i in range(40_001)) + b"/>",
                 "over 40,000 XML attributes", id="too-many-attributes"),
    ("q.daquery", b'<?xml version="1.1"?><cclquery/>', "isn't declared XML 1.0"),
    ("q.daquery", b"<cclquery><queries>", "well-formed"),
    ("q.daquery", b"<report/>", "isn't a DA2"),
    ("q.daquery", b"<cclquery><queries/></cclquery>", "no output columns"),
])
def test_refused_files(name, data, why):
    with pytest.raises(DA2FileError, match=why):
        read_da2_file(data, name)


def test_a_refused_read_is_charged_what_it_parsed():
    """A file refused for its elements or attributes, before or after parsing them, is charged 10 each it could have
    parsed, and so is a runtime prompt refused at any level of its escaped XML (&#60; is a < one level down)."""
    for n in (30_000, 240_000):  # parsed then refused, and refused by its count of <
        data = b"<cclquery>" + b"<a/>" * n + b"</cclquery>"
        with pytest.raises(DA2FileError, match="XML elements") as exc:
            read_da2_file(data, "q.daquery")
        assert exc.value.work >= 10 * min(n, 2 * da2_files.MAX_ELEMENTS)
    data = b"<cclquery" + b"".join(b' a%d=""' % i for i in range(39_000)) + b' z="' + b"v" * 1_001 + b'"/>'
    with pytest.raises(DA2FileError, match="over 1,000 characters") as exc:
        read_da2_file(data, "q.daquery")
    assert exc.value.work >= 10 * 39_000

    def escaped(root):
        prop = next(p for p in root.iter(f"{B}ex-property") if p.findtext(f"{B}name") == "defaultRuntimePrompts")
        prop.find(f"{B}value").text = "<r><a>&#60;r>" + "&#60;a/>" * 5_000 + "&#60;b></a></r>"
    with pytest.raises(DA2FileError, match="isn't well-formed") as exc:
        read_da2_file(_rptdesign_with(escaped), "r.dareport")
    assert exc.value.work >= 10 * 5_000


def test_only_xml_1_0_is_read():
    """Expat reads XML 1.1 as 1.0, and BIRT's parser doesn't: there a NEL (U+0085) ends a line, and so a // comment
    that hides a filter from this reader. A runtime prompt's escaped XML is read the same way."""
    members = _members(_dareport())
    rpt = next(n for n in members if n.endswith(".rptdesign"))
    body = members[rpt].split(b"?>", 1)[-1] if members[rpt].startswith(b"<?xml") else members[rpt]
    for decl in (b'<?xml version="1.1" encoding="UTF-8"?>', b"<?xml version='1.1'?>", b'<?xml-stylesheet href="x"?>'):
        with pytest.raises(DA2FileError, match="isn't declared XML 1.0"):
            read_da2_file(_zip({**members, rpt: decl + body}), "r.dareport")
    assert read_da2_file(_zip({**members, rpt: b"<?xml version='1.0'?>" + body}), "r.dareport").size

    def prompt_1_1(root):
        value = next(p for p in root.iter(f"{B}ex-property")
                     if p.findtext(f"{B}name") == "defaultRuntimePrompts").find(f"{B}value")
        value.text = '<?xml version="1.1"?>' + (value.text.split("?>", 1)[-1] if value.text.startswith("<?xml")
                                                 else value.text)
    with pytest.raises(DA2FileError, match="isn't declared XML 1.0"):
        read_da2_file(_rptdesign_with(prompt_1_1), "r.dareport")


def test_xml_da2_may_read_another_way_is_refused():
    """BIRT matches a tag by the Java hashCode of its lower case alone, so <EXPRESSION>, or <expressiq0> of the same
    hash, is an <expression> there, and it keeps an ex-property's last <value>. The checks read a tag as written and a
    child once, so these are refused; a query's tags, and its parts read once, likewise."""
    java_hash = lambda t: sum(ord(c) * 31 ** i for i, c in enumerate(reversed(t))) % 2 ** 32  # noqa: E731
    assert java_hash("expressiq0") == java_hash("expression")
    rule = _RULE('row["Person Id"] == 12345678')
    for tag in ("EXPRESSION", "Expression", "expressiq0"):
        with pytest.raises(DA2FileError, match="a tag DA2 doesn't use"):
            read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule.replace("expression", tag))), "r.dareport")
    with pytest.raises(DA2FileError, match="a tag DA2 doesn't use"):
        read_da2_file(b"<cclquery><QUERIES/></cclquery>", "q.daquery")
    for tag in ("property", "xml-property", "text-property"):  # BIRT reads an expression setting whatever its tag
        with pytest.raises(DA2FileError, match="writes an expression as a plain setting"):
            read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule.replace("expression", tag))), "r.dareport")

    def prompt_name(how):
        def change(root):
            prop = next(p for p in root.iter(f"{B}ex-property") if p.findtext(f"{B}name") == "defaultRuntimePrompts")
            if how == "property":  # BIRT sets the ex-property's name (or value) from it
                prop.remove(prop.find(f"{B}name"))
                ET.SubElement(prop, f"{B}property", name="name").text = "defaultRuntimePrompts"
            else:  # Java's equalsIgnoreCase matches a dotless i with i
                prop.find(f"{B}name").text = "defaultRunt\u0131mePrompts"
        return change
    for how in ("property", "dotless"):
        with pytest.raises(DA2FileError, match="extended property other than an ASCII name and a value"):
            read_da2_file(_rptdesign_with(prompt_name(how)), "r.dareport")

    def two_values(root):
        prop = next(p for p in root.iter(f"{B}ex-property") if p.findtext(f"{B}name") == "defaultRuntimePrompts")
        ET.SubElement(prop, f"{B}value").text = "<prompts/>"
    with pytest.raises(DA2FileError, match="two `value` in one element"):
        read_da2_file(_rptdesign_with(two_values), "r.dareport")
    members = _members(_dareport())
    q = next(n for n in members if n.endswith(".daquery"))
    body = members[q].replace(b"<qualification>", b'<qualification><filter2><element name="x"/><operator type="EQUAL"/>'
                              b'<operator type="LIKE"/><values><value text="1"/></values></filter2>', 1)
    assert body != members[q]
    with pytest.raises(DA2FileError, match="two `operator` in one element"):
        read_da2_file(_zip({**members, q: body}), "r.dareport")
    started = time.perf_counter()  # the repeat is named in one pass: counting each took 0.8 s at 1 CPU
    with pytest.raises(DA2FileError, match="two `output` in one element"):
        read_da2_file(b"<cclquery>" + b"<output/>" * 40_000 + b"</cclquery>", "q.daquery")
    assert time.perf_counter() - started < 0.15


def test_a_prompt_text_da2_reads_as_xml_is_read():
    """Java trims a runtime prompt's text and skips a byte order mark before its XML, so a prompt that opens with one
    is read; and a file's prompt texts are capped, each taking a parser of its own."""
    for lead in ("\ufeff", " \ufeff ", "\ufeff\n"):
        def bom(root):
            _prompt_default(root, "Enc Financial Num", "12345678")
            prop = next(p for p in root.iter(f"{B}ex-property") if p.findtext(f"{B}name") == "defaultRuntimePrompts")
            prompts = ET.fromstring(prop.find(f"{B}value").text)
            qual = next(q for q in prompts.iter("qualification") if (q.text or "").strip())
            qual.text = lead + qual.text.lstrip()
            prop.find(f"{B}value").text = ET.tostring(prompts, encoding="unicode")
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(bom), "r.dareport")

    def many(n):
        def change(root):
            prop = next(p for p in root.iter(f"{B}ex-property") if p.findtext(f"{B}name") == "defaultRuntimePrompts")
            prop.find(f"{B}value").text = "<prompts>" + "<qualification>&lt;a/></qualification>" * n + "</prompts>"
        return _rptdesign_with(change)
    assert read_da2_file(many(da2_files.MAX_PROMPT_PARSES - 1), "r.dareport").size
    with pytest.raises(DA2FileError, match="more prompts than DA2 writes"):
        read_da2_file(many(da2_files.MAX_PROMPT_PARSES), "r.dareport")


def test_refused_zip_members():
    members = _members(_dareport())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:  # zipfile decodes an extra field in time quadratic in its records
        for n, body in members.items():
            (info := zipfile.ZipInfo(n)).extra = struct.pack("<HH", 0xCAFE, 0) * 100
            zf.writestr(info, body)
    with pytest.raises(DA2FileError, match="400 bytes of zip extra fields"):
        read_da2_file(buf.getvalue(), "r.dareport")
    with pytest.raises(DA2FileError, match="holds over 20 files"):  # counted before zipfile reads them
        read_da2_file(_zip({**members, **{f"q{i}.daquery": b"<cclquery/>" for i in range(20)}}), "r.dareport")
    with pytest.raises(DA2FileError, match="isn't part of a DA2 report"):
        read_da2_file(_zip({**members, "../evil.daquery": b"<cclquery/>"}), "r.dareport")
    with pytest.raises(DA2FileError, match="isn't part of a DA2 report"):
        read_da2_file(_zip({**members, "patients.csv": b"a,b"}), "r.dareport")
    with pytest.raises(DA2FileError, match="unpacks to more than 2,000,000 bytes"):
        read_da2_file(_zip({**members, "big.daquery": b" " * 2_000_001}), "r.dareport")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_BZIP2) as zf:  # unpacked unbounded before anything counts it
        for n, body in members.items():
            zf.writestr(n, body)
    with pytest.raises(DA2FileError, match="packed in a way DA2 doesn't"):
        read_da2_file(buf.getvalue(), "r.dareport")
    one = _zip({"q.daquery": b"<cclquery>" + bytes(range(32, 127)) * 4 + b"</cclquery>"})
    at = 30 + sum(struct.unpack_from("<HH", one, 26))
    with pytest.raises(DA2FileError, match="can't be unpacked"):  # a deflate stream of garbage: zlib.error
        read_da2_file(one[:at] + b"\xff" * 16 + one[at + 16:], "r.dareport")
    data = _zip(members)  # a ZIP64 locator of two disks before the end record: zipfile's BadZipFile
    with pytest.raises(DA2FileError, match="doesn't open as a .dareport"):
        read_da2_file(data[:-22] + struct.pack("<4sLQL", b"PK\x06\x07", 1, 0, 2) + data[-22:], "r.dareport")


def test_patient_identifier_filters_are_refused_without_echoing_values():
    daquery = _bundle(value_filters={"Enc Type": ["Inpatient"]}).queries[0].daquery_xml
    fin = re.sub(r'(<filter [^>]*><element name=")Enc Type(")', r"\1Enc Financial Num\2", daquery, count=1)
    with pytest.raises(DA2FileError, match="`Enc Financial Num`") as exc:
        read_da2_file(fin.encode(), "q.daquery")
    assert "Inpatient" not in str(exc.value)
    assert exc.value.work >= len(fin) // 4  # what the refused read counted, so the chat charges it
    names = re.sub(r'(<filter [^>]*><element name=")Enc Type(")', r"\1Person Name Last\2", daquery, count=1)
    with pytest.raises(DA2FileError, match="patient identifiers"):
        read_da2_file(names.encode(), "q.daquery")
    files = read_da2_file(_dareport(), "census.dareport")  # Person Name Last < ZZ names nobody
    files.text_match("Person Name Last", "%OB%")
    read_da2_file(files.files()["census.dareport"], "census.dareport")  # a pattern names nobody either


def _refiltered(name, uuid=None, op=None, values=None, inline=False):
    """The census query with its Enc Type filter changed: the element's name or id, the operator, the value nodes, or
    the filter moved into a copy of the query that is an inline sub-query."""
    root = ET.fromstring(_bundle(value_filters={"Enc Type": ["Inpatient"]}).queries[0].daquery_xml)
    item = root.find("query_list/query_list_item1")
    if inline:
        item = copy.deepcopy(item)
        item.tag = "query_list_item2"
        item.set("inline_ind", "1")
        root.find("query_list").append(item)
    node = next(n for n in item.iter() if n.find("element") is not None and n.find("element").get("name") == "Enc Type")
    node.find("element").set("name", name)
    if uuid:
        node.find("element").set("uuid", uuid)
    if op:
        node.find("operator").set("type", op)
    if values is not None:
        node.remove(node.find("values"))
        node.append(ET.fromstring(f"<values type='LIST'>{values}</values>"))
    return ET.tostring(root)


@pytest.mark.parametrize("change, named", [
    (dict(name="Enc Alias"), "Enc Alias"),
    (dict(name="CMRN"), "CMRN"),
    (dict(name="Encounter ID"), "Encounter ID"),
    (dict(name="Enc Encounter Number"), "Enc Encounter Number"),
    (dict(name="Enc Preadmit Number"), "Enc Preadmit Number"),
    (dict(name="HP P R Member Num"), "HP P R Member Num"),
    (dict(name="Per Phn Phone Number"), "Per Phn Phone Number"),
    (dict(name="Enc Loc Nurse Unit", uuid=LIVE_CWC_ELEMENT_MAP["Enc Financial Num"][1]), "Enc Financial Num"),
    (dict(name="Enc Financial Num", values="<value>12345678</value>"), "Enc Financial Num"),
    (dict(name="Enc Financial Num", values="<value id='12345678'/>"), "Enc Financial Num"),
    (dict(name="Enc Financial Num", inline=True), "Enc Financial Num"),
    (dict(name="Person Name Full Format", op="LIKE", values="<value text='SMITH, JOHN'/>"), "Person Name Full Format"),
    (dict(name="Person Name Full Format", op="LIKE", values="<value text='%SMITH, JOHN%'/>"), "Person Name Full Format"),
    (dict(name="Person Name Full Format", op="LIKE", values="<value text='SMITH%JOHN%'/>"), "Person Name Full Format"),
    (dict(name="Person Name Last", op="BETWEEN", values="<value text='SMITH'/><value text='SMITHZ'/>"),
     "Person Name Last"),
    # two pieces of a name, however they're spelled: wildcards (% and _), single letters, either attribute of a value
    *[(dict(name="Person Name Full Format", op="LIKE", values=f"<value text='{v}'/>"), "Person Name Full Format")
      for v in ("S_I_H%JOHN%", "SMITH%J_H_%", "S%M%I%T%H%J%", "SMITH%J%", "SMITH_JOHN%", "%smith%john%")],
    (dict(name="Person Name Full Format", op="LIKE", values="<value display='SMITH%' text='%JOHN%'/>"),
     "Person Name Full Format"),
    # the same word twice ("LEE__LEE%" matches "LEE, LEE"), and pieces split by a dot or a zero-width space
    *[(dict(name="Person Name Full Format", op="LIKE", values=f"<value text='{v}'/>"), "Person Name Full Format")
      for v in ("LEE__LEE%", "JAMES%JAMES%", "SMITH.JOHN%", "SMITH&#8203;JOHN%")],
    # catalog fields holding a patient's id, home address or history last name
    *[(dict(name=n), n) for n in ("Sch Pat Person Id", "Ib Rx P Patient Id", "CE Enc Id", "Hm Addr String", "Name Hx La",
                                  "Name")],
    (dict(name="Person Name Full Format", op="LIKE", values="<value text='SMITH%'/><value text='%JOHN%'/>"),
     "Person Name Full Format"),
    # a range is a pattern only while it holds one piece of a name and no "Last, First"
    (dict(name="Person Name Full Format", op="LESS THAN", values="<value text='SMITH, JOHN'/>"), "Person Name Full Format"),
    (dict(name="Person Name Last", op="GREATER THAN OR EQUAL", values="<value text='SMITH JOHN'/>"), "Person Name Last"),
])
def test_filters_that_pick_out_a_patient_are_refused(change, named):
    with pytest.raises(DA2FileError, match="patient identifiers") as exc:
        read_da2_file(_refiltered(**change), "q.daquery")
    assert shown(named) in str(exc.value)
    assert not re.search(r"12345678|SMITH|Inpatient", str(exc.value))


@pytest.mark.parametrize("change", [
    dict(name="Enc Alias Type"),  # an alias's type (FIN NBR) is a code, not a patient's number
    dict(name="BO Fin Class"),
    dict(name="Person Name Last", op="LESS THAN", values="<value text='ZZ'/>"),  # DA2's test-patient rule
    dict(name="Person Name Last", op="LIKE", values="<value text='%OB%'/>"),
    *[dict(name="Person Name Full Format", op=op, values=f"<value text=\"{v}\"/>")
      for op, v in (("LIKE", "SMI%"), ("LIKE", "%O'B%"), ("LIKE", "SMITH-JONES%"), ("NOT LIKE", "ZZ%"),
                    ("NOT LIKE", "%ZZ%TEST%"), ("GREATER THAN OR EQUAL", "SMI"), ("LIKE", "LEE__%"))],
    dict(name="Prsnl Person Id"),  # a clinician's id
    dict(name="PI Alias Type", uuid="c5f91e3a-8251-4bf5-acbe-8cf706540cab"),  # the catalog spells it with a Cyrillic р
])
def test_codes_ranges_and_patterns_on_patient_fields_are_kept(change):
    assert read_da2_file(_refiltered(**change), "q.daquery").summary()


def test_bounds_on_the_last_name_alone_are_kept():
    split = _refiltered("Person Name Last", op="GREATER THAN OR EQUAL", values="<value text='A'/>")
    split = split.replace(b'<value text="ZZ"', b'<value text="M"', 1)  # last names from A to L, as a worklist splits
    assert "Person Name Last less than 'M'" in read_da2_file(split, "q.daquery").summary()


def test_what_name_filters_tell_of_a_name():
    def slots(fields, op, *values):
        return da2_files._name_slots(fields, op, [(v,) for v in values])
    full, last, first = ["Person Name Full Format"], ["Person Name Last"], ["Person Name First"]
    assert slots(last, "NOT LIKE", "TEST%") == slots(first, "NOT LIKE", "PATIENT%") == slots(full, "NOT LIKE", "%X%") == set()
    # LAST, FIRST sorts by the last name, so a bound or a pattern fixed at the start says only where the name starts
    assert slots(full, "GREATER THAN OR EQUAL", "A") == slots(full, "LESS THAN", "M") == slots(full, "LIKE", "SMI%")
    assert len(slots(full, "LIKE", "LEE__%") | slots(full, "LIKE", "%__LEE")) == 2  # both ends of one name
    assert len(slots(full, "LIKE", "%SMI%") | slots(full, "LIKE", "%JOH%")) == 2
    # _______ stands for a piece too: SMITH% and _______JOHN% are SMITH, JOHN
    assert len(slots(full, "LIKE", "SMITH%") | slots(full, "LIKE", "_______JOHN%")) == 2
    assert len(slots(full, "LIKE", "%JOHN") | slots(full, "LIKE", "%SMITH______")) == 2
    assert slots(["Name Hx La"], "LIKE", "SMI%") == slots(last, "LIKE", "JO%") == {"last name"}


def test_a_whole_name_across_two_filters_is_refused():
    files = read_da2_file(_refiltered("Person Name Full Format", op="LIKE", values="<value text='%JOHN%'/>"), "q.daquery")
    with mock.patch.object(da2_files.DA2Files, "_all_filters", side_effect=AssertionError("walked the whole file")):
        with pytest.raises(DA2FileError, match="whole patient name"):
            files.text_match("Person Name Last", "smi%")
        assert not files.changes
        files.remove_filter("Person Name Full Format")
        assert files.text_match("Person Name Last", "smi%").startswith("Added")  # the first-name piece is gone
    for exclusion in ("Person Name Full Format", "Person Name First"):  # an exclusion tells nothing of a name
        kept = read_da2_file(_refiltered(exclusion, op="NOT LIKE", values="<value text='%TEST%'/>"), "q.daquery")
        kept.text_match("Person Name Last", "smi%")
        read_da2_file(kept.files()["q.daquery"], "q.daquery")
    both = _bundle(like_filters={"Person Name Last": "smi%", "Person Name Full Format": "%john%"}).queries[0].daquery_xml
    with pytest.raises(DA2FileError, match="patient identifiers") as exc:
        read_da2_file(both.encode(), "q.daquery")
    assert shown("Person Name Last") in str(exc.value) and shown("Person Name Full Format") in str(exc.value)
    files = read_da2_file(_dareport(), "census.dareport")
    files.text_match("Person Name Last", "smi%")
    assert files.text_match("Person Name Last", "jo%").startswith("Changed")  # it replaces the last-name pattern


def test_text_match_refuses_patient_identifiers():
    files = read_da2_file(_dareport(), "census.dareport")
    with pytest.raises(DA2FileError, match="identifies patients"):
        files.text_match("Enc Financial Num", "%12345678%")
    with pytest.raises(DA2FileError, match="identifies patients"):
        files.text_match("Person Name Full Format", "smith, john")
    with pytest.raises(DA2FileError, match="identifies patients"):
        files.text_match("Person Name Full Format", "smith%john")
    with pytest.raises(DA2FileError, match="identifies patients"):
        files.text_match("Person Name Last", "smith john%")  # written as SMITHJOHN%, but typed as a whole name
    for typed in ("smith.john%", "lee__lee%"):
        with pytest.raises(DA2FileError, match="identifies patients"):
            files.text_match("Person Name Full Format", typed)
    assert not files.changes


def _rptdesign_with(change):
    """The census .dareport with its layout's XML changed by change(root)."""
    members = _members(_dareport())
    rpt = next(n for n in members if n.endswith(".rptdesign"))
    root = ET.fromstring(members[rpt])
    change(root)
    return _zip({**members, rpt: ET.tostring(root)})


def _prompt_default(root, name, value):
    """Puts a default value on the layout's first runtime prompt, on the field `name`, as DA2 writes it: the prompt's
    qualification escaped inside the prompt list, escaped inside the data set's property."""
    prop = next(p for p in root.iter(f"{B}ex-property") if p.findtext(f"{B}name") == "defaultRuntimePrompts")
    prompts = ET.fromstring(prop.find(f"{B}value").text)
    qual = next(q for q in prompts.iter("qualification") if (q.text or "").strip())
    inner = ET.fromstring(qual.text.split("?>", 1)[-1])
    inner.find(".//element").set("name", name)
    values = inner.find(".//values")
    values.clear()
    ET.SubElement(values, "value", {"text": value})
    qual.text = ET.tostring(inner, encoding="unicode")
    prop.find(f"{B}value").text = ET.tostring(prompts, encoding="unicode")


def _table_rule(root, rule):
    table = root.find(f".//{B}table")
    if (lst := table.find(f"{B}list-property[@name='filter']")) is None:  # DA2 writes one filter list a table
        table.insert(0, lst := ET.Element(f"{B}list-property", name="filter"))
    lst.append(ET.fromstring(f'<structure xmlns="{da2_files.BIRT_NS}">{rule}</structure>'))


_RULE = '<property name="operator">is-true</property><expression name="expr">{}</expression>'.format


def _bound(root, binds, test=None, computed=()):
    """Adds bindings (name, expression) to the layout's table, computed columns to its data set, and a table filter
    whose expression is test."""
    if test:
        _table_rule(root, _RULE(test))
    host = root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']")
    for n, expr in binds:
        host.append(ET.fromstring(f'<structure xmlns="{da2_files.BIRT_NS}"><property name="name">{n}</property>'
                                  f'<expression name="expression">{expr}</expression></structure>'))
    for n, expr in computed:
        root.find(f".//{B}list-property[@name='computedColumns']").append(ET.fromstring(
            f'<structure xmlns="{da2_files.BIRT_NS}"><property name="name">{n}</property>'
            f'<expression name="expression">{expr}</expression></structure>'))


def test_values_in_the_layout_are_judged_too():
    for name, value in (("Enc Financial Num", "12345678"), ("Person Name Full Format", "SMITH, JOHN")):
        with pytest.raises(DA2FileError, match="patient identifiers") as exc:
            read_da2_file(_rptdesign_with(lambda r: _prompt_default(r, name, value)), "r.dareport")
        assert shown(name) in str(exc.value) and value not in str(exc.value)
    eq = '<property name="operator">eq</property><expression name="expr">row["{}"]</expression>' \
         '<simple-property-list name="value1"><value>"12345678"</value></simple-property-list>'
    for rule in (eq.format("Person Id"), '<property name="operator">is-true</property>'
                 '<expression name="expr">row["Person Name Last"] == "SMITH"</expression>'):
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule)), "r.dareport")
    def renamed(root):  # a binding that only renames the patient's id is the id
        _table_rule(root, eq.format("pid"))
        root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']").append(ET.fromstring(
            f'<structure xmlns="{da2_files.BIRT_NS}"><property name="name">pid</property>'
            '<expression name="expression">dataSetRow["Person Id"]</expression></structure>'))
    with pytest.raises(DA2FileError, match="`Person Id`"):
        read_da2_file(_rptdesign_with(renamed), "r.dareport")
    def age(root):  # a value worked out from one isn't it: adults only, by an age from the birth date
        _table_rule(root, eq.format("Age"))
        root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']").append(ET.fromstring(
            f'<structure xmlns="{da2_files.BIRT_NS}"><property name="name">Age</property><expression name="expression">'
            'BirtDateTime.diffYear(dataSetRow["Person Birth Dt Tm"], new Date())</expression></structure>'))
    read_da2_file(_rptdesign_with(age), "r.dareport")
    pid = 'row["Person Id"]'
    for rule in (  # other spellings of the same comparison
            f'<property name="operator">is-true</property><expression name="expr">BirtComp.equalTo({pid}, "12345678")'
            '</expression>',
            f'<property name="operator">is-true</property><expression name="expr">(String({pid}) || "") + "" == '
            '"12345678" &amp;&amp; true</expression>',
            '<property name="operator">match</property><expression name="expr">row["Person Name Full Format"]'
            '</expression><simple-property-list name="value1"><value>/^SMITH, JOHN/</value></simple-property-list>',
            eq.format('Person Id"].toString() + row._outer["Person Id')):
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule)), "r.dareport")
    def aliased(root):  # the data set's own name for the column, then a rename in parentheses
        hint = next(s for s in root.iter(f"{B}structure") if (s.findtext(f"{B}property[@name='alias']") or "") == "Person Id")
        hint.find(f"{B}property[@name='alias']").text = "PID"
        _table_rule(root, eq.format("pid"))
        root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']").append(ET.fromstring(
            f'<structure xmlns="{da2_files.BIRT_NS}"><property name="name">pid</property>'
            '<expression name="expression">( dataSetRow["PID"] )</expression></structure>'))
    with pytest.raises(DA2FileError, match="`Person Id`"):
        read_da2_file(_rptdesign_with(aliased), "r.dareport")
    def defaulted(root, default):  # a report parameter compared with the id, as Endexis compares the nurse unit
        _table_rule(root, '<property name="operator">is-true</property><expression name="expr">params["p_NurseUnit"]'
                          f'.value == "ALL" || {pid} == params["p_NurseUnit"].value</expression>')
        next(v for v in root.iter(f"{B}value") if v.text == "ALL").text = default
    read_da2_file(_rptdesign_with(lambda r: defaulted(r, "ALL")), "r.dareport")  # the rule tests for its default
    with pytest.raises(DA2FileError, match="`Person Id`"):
        read_da2_file(_rptdesign_with(lambda r: defaulted(r, "12345678")), "r.dareport")
    for rule in (  # an age in BIRT's operator form, and a test for a blank: neither holds a patient's value
            '<property name="operator">ge</property><expression name="expr">BirtDateTime.diffYear(row["Person Birth Dt '
            'Tm"], BirtDateTime.today())</expression><simple-property-list name="value1"><value>18</value>'
            '</simple-property-list>',
            f'<property name="operator">is-true</property><expression name="expr">{pid} == null || {pid} == ""'
            '</expression>'):
        read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule)), "r.dareport")
    def own_query(root):
        next(c for c in root.iter(f"{B}xml-property") if c.get("name") == "queryText").text = "<cclquery/>"
    with pytest.raises(DA2FileError, match="its own query"):
        read_da2_file(_rptdesign_with(own_query), "r.dareport")


def test_other_spellings_of_a_layout_value_are_refused():
    """Numbers, strings, clauses, column references and exemptions written so the clause check would miss them."""
    pid, name = 'row["Person Id"]', 'row["Person Name Full Format"]'
    rule = '<property name="operator">is-true</property><expression name="expr">{}</expression>'.format
    eq = ('<property name="operator">eq</property><expression name="expr">{}</expression>'
          '<simple-property-list name="value1"><value{}>{}</value></simple-property-list>').format
    for r in (rule(f"{pid} == 0xBC614E"), rule(f"{pid} == .12345678e8"), rule(f'{pid} == "1"+"2"+"3"+"4"'),
              eq(name, ' type="constant"', "DOE, JANE"),
              rule(f'{pid} == (false || (params["p_NurseUnit"].value, "12345678"))'),
              rule(f'(v = {pid}) != null &amp;&amp; v == "12345678"'), rule("row[3] == 12345678"),
              rule('row["Person" + " Id"] == 12345678'), rule('row . _outer ["Person Id"] == 12345678'),
              rule(f"BirtDateTime.diffYear({pid}, BirtDateTime.today()) == 12345678")):
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(lambda root: _table_rule(root, r)), "r.dareport")
    with pytest.raises(DA2FileError, match="read more than one way"):  # a template string: DA2 writes none
        read_da2_file(_rptdesign_with(lambda root: _table_rule(root, rule(f"{name} == `DOE, JANE`"))), "r.dareport")
    def bound(root, expr, test):
        _table_rule(root, rule(test))
        root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']").append(ET.fromstring(
            f'<structure xmlns="{da2_files.BIRT_NS}"><property name="name">x</property>'
            f'<expression name="expression">{expr}</expression></structure>'))
    with pytest.raises(DA2FileError, match="`Person Id`"):  # a binding that reads the id, not only renames it
        read_da2_file(_rptdesign_with(lambda r: bound(r, 'String(dataSetRow["Person Id"])', 'row["x"] == "12345678"')),
                      "r.dareport")
    read_da2_file(_rptdesign_with(lambda r: bound(  # an age at discharge, as DA2 reports work it out
        r, '((dataSetRow["Enc Discharge Dt/Tm"]-dataSetRow["Person Birth Dt Tm"])/(1000*60*60*24*365.25))',
        'row["x"] &gt;= 65')), "r.dareport")
    def defaulted(root, test):
        _table_rule(root, rule(test))
        next(v for v in root.iter(f"{B}value") if v.text == "ALL").text = "12345678"
    for test in (f"{pid} == params.p_NurseUnit.value",
                 f'params["p_NurseUnit"].value != "12345678" || {pid} == params["p_NurseUnit"].value'):
        with pytest.raises(DA2FileError, match="`Person Id`"):
            read_da2_file(_rptdesign_with(lambda r: defaulted(r, test)), "r.dareport")
    style = '<property name="color">#991B1B</property><property name="fontSize">10pt</property>'
    for r in (rule(f"{pid} == null") + style,  # a style or a map rule's display text is no value
              eq(pid, "", '""') + '<text-property name="display">Pending - 24h</text-property>'):
        read_da2_file(_rptdesign_with(lambda root: _table_rule(root, r)), "r.dareport")
    with pytest.raises(DA2FileError, match="20,001 characters long"):
        read_da2_file(_rptdesign_with(lambda root: _table_rule(root, rule("1" * 20_001))), "r.dareport")
    ages = 'BirtDateTime.diffYear(row["Person Birth Dt Tm"], ' * 190 + "BirtDateTime.today()" + ")" * 190 + " &gt;= 18"
    def many(root, n):
        for _ in range(n):
            _table_rule(root, rule(ages))
    data, start = _rptdesign_with(lambda r: many(r, 19)), time.process_time()
    assert read_da2_file(data, "r.dareport").size > len(data)  # what a read costs is the unzipped size
    assert time.process_time() - start < 0.5
    with pytest.raises(DA2FileError, match="run to more than 200,000 characters"):
        read_da2_file(_rptdesign_with(lambda r: many(r, 21)), "r.dareport")


def test_values_in_comments_bindings_and_visibility_are_judged():
    """A value is found past a comment or an escape, in a binding, a visibility rule or an aggregation's filter, and a
    comment, an aggregation's own filter or a column named by a constant is no value."""
    pid, name = 'row["Person Id"]', 'row["Person Name Full Format"]'
    rule = '<property name="operator">is-true</property><expression name="expr">{}</expression>'.format
    def on_table(root, xml):
        root.find(f".//{B}table").insert(0, ET.fromstring(xml.replace("<lp", f'<list-property xmlns="{da2_files.BIRT_NS}"')))
    def bound(root, binds, test=None):
        if test:
            _table_rule(root, rule(test))
        host = root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']")
        for n, expr in binds:
            host.append(ET.fromstring(f'<structure xmlns="{da2_files.BIRT_NS}"><property name="name">{n}</property>'
                                      f'<expression name="expression">{expr}</expression></structure>'))
    refused = [lambda r, t=t: _table_rule(r, rule(t)) for t in (
        f'/* " */ {name} == "DOE, JANE"', f'typeof/"/ &amp;&amp; {name} == "DOE, JANE"',
        '(row)["Person Id"] == 12345678', 'row/**/["Person Id"] == 12345678',
        f'({pid} /* ) */ || "12345678") == {pid}')]
    refused += [lambda r: bound(r, [("hit", 'dataSetRow["Person Id"] == 12345678')], 'row["hit"]'),
                lambda r: bound(r, [("x", 'dataSetRow["Person Birth Dt Tm"] - row["zero"]'), ("zero", "0")],
                                'row["x"] == 321926400000'),
                lambda r: bound(r, [("n", f'Total.count({pid}, {pid} == 12345678)')]),
                lambda r: on_table(r, f'<lp name="visibility"><structure><property name="format">all</property>'
                                      f'<expression name="valueExpr">{pid} != 12345678</expression></structure></list-property>')]
    for change in refused:
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(change), "r.dareport")
    for e in (f'{name} == "DOE\\\nJANE"', 'r\\u006fw["Person Id"] == 12345678'):  # a backslash outside a string
        with pytest.raises(DA2FileError, match="read more than one way"):
            read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule(e))), "r.dareport")
    for change in (lambda r: _table_rule(r, rule(f"{pid} != null // don't count \"test\" ones")),
                   lambda r: bound(r, [("n", 'Total.countDistinct(row["Enc Id"], row["Enc Type"] == "Inpatient")')]),
                   lambda r: bound(r, [("u", 'var COL = "Enc Type"; row[COL] == "Inpatient" ? 1 : 0')])):
        read_da2_file(_rptdesign_with(change), "r.dareport")
    slow = 'row["Person Birth Dt Tm"] == "' + '\\"' * 9_980 + "\\"  # every quote starts a string, none ends
    data, start = _rptdesign_with(lambda r: [_table_rule(r, rule(slow)) for _ in range(9)]), time.process_time()
    read_da2_file(data, "r.dareport")
    assert time.process_time() - start < 0.5



def test_display_expressions_are_kept_and_held_values_are_judged():
    """An expression outside a rule list that only shows a patient field is kept; one that tests it against a value,
    an aggregation passing a field on, or a binding holding a value is refused; the scan stays linear."""
    pid, name, ns = 'row["Person Id"]', 'row["Person Name Full Format"]', da2_files.BIRT_NS
    rule = '<property name="operator">is-true</property><expression name="expr">{}</expression>'.format
    def bound(root, binds, test=None, agg=None):
        if test:
            _table_rule(root, rule(test))
        host = root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']")
        for n, expr in binds:
            host.append(ET.fromstring(f'<structure xmlns="{ns}"><property name="name">{n}</property>'
                                      f'<expression name="expression">{expr}</expression></structure>'))
        if agg:
            host.append(ET.fromstring(
                f'<structure xmlns="{ns}"><property name="name">agg</property><property name="aggregateFunction">{agg[0]}'
                '</property><list-property name="arguments"><structure><property name="name">Expression</property>'
                f'<expression name="value">{agg[1]}</expression></structure></list-property></structure>'))
    for expr in ('"FIN: " + row["Enc Financial Num"]', f'{name} || "Unknown"', f'{name}.substring(0, 25)',
                 'Formatter.format(row["Person Birth Dt Tm"], "MM/dd/yyyy")', f'{name}.length &gt; 40',
                 f'Total.count(row["Enc Type"] == "Inpatient" &amp;&amp; {pid} != null)',
                 f'{name} == null ? "Unknown" : {name}'):
        read_da2_file(_rptdesign_with(lambda r, e=expr: bound(r, [("shown", e)])), "r.dareport")
    with pytest.raises(DA2FileError, match="patient identifiers"):  # a test can split a label back out of the field
        read_da2_file(_rptdesign_with(lambda r: bound(r, [("x", f'{pid} + "AB"')], 'row["x"] != ""')), "r.dareport")
    fin, mrn = 'row["Enc Financial Num"]', 'row["Person Mrn"]'
    for expr in (f'"XXXX" + {fin}.substring({fin}.length - 4)', f'("0000000000" + {mrn}).slice(-10)',
                 f'{name}.split(",")[1] + " " + {name}.split(",")[0]', f'{name}.replace(/^([^,]+),\\s*(.+)$/, "$2 $1")',
                 f'String({fin}).replace(/(\\d{{3}})(?=\\d)/g, "$1-")'):  # masks and reshapes only show the field
        read_da2_file(_rptdesign_with(lambda r, e=expr: bound(r, [("shown", e)])), "r.dareport")
    read_da2_file(_rptdesign_with(lambda r: bound(r, [], 'row["agg"] &gt; 10', ("COUNTDISTINCT", pid))), "r.dareport")
    for change in (lambda r: bound(r, [("hit", f'{name}.indexOf("DOE") &gt;= 0 ? 1 : 0')]),
                   lambda r: bound(r, [], 'row["agg"] == 12345678', ("MAX", pid)),
                   lambda r: bound(r, [("tgt", '"12345678"'), ("t2", 'row["tgt"]')], f'{pid} == row["t2"]'),
                   lambda r: bound(r, [("tgt", '"1234"'), ("t2", 'String(row["tgt"]) + "5678"')], f'{pid} == row["t2"]'),
                   lambda r: _table_rule(r, rule(f'{name}.replace(/[/"]/g, "") == "DOE, JANE"')),
                   lambda r: bound(r, [("d", f'{pid} - 12345678')], 'row["d"] == 0'),  # 0 for one patient
                   lambda r: bound(r, [("d", f'{pid} + -12345678')], 'row["d"] == 0'),
                   lambda r: bound(r, [("d", f'String({pid}) % 12345678')], 'row["d"] == 0'),
                   lambda r: bound(r, [("shown", f'{name}.replace("DOE, JANE", "CONFIDENTIAL")')]),
                   lambda r: bound(r, [("tgt", '"12345678"'), ("tgt", 'row["Enc Type"] + ""')], f'{pid} == row["tgt"]')):
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(change), "r.dareport")
    def params(root):  # many parameters, each clause reading the params object
        host = root.find(f"{B}parameters")
        for i in range(3_000):
            host.append(ET.fromstring(f'<scalar-parameter xmlns="{ns}" name="q{i}"><simple-property-list '
                                      'name="defaultValue"><value type="constant">ALL</value></simple-property-list>'
                                      '</scalar-parameter>'))
        _table_rule(root, rule(" || ".join(["params"] * 2_000)))
    chain = [("c0", 'dataSetRow["Enc Id"]')] + [(f"c{i}", f'row["c{i - 1}"]') for i in range(1, 2_000)]
    held = [a + b for a in string.ascii_letters for b in string.ascii_letters + string.digits][:2_857]
    wide = [(n, '"12"') for n in held] + [(f"r{i}", "+".join(f"row.{n}" for n in held)) for i in range(9)]
    for change in (lambda r: [_table_rule(r, rule("(/" + "[/]" * 6_660)) for _ in range(9)], params,
                   lambda r: bound(r, chain + [("d", " - ".join(f'row["c{i}"]' for i in range(1_900, 2_000)))]),
                   lambda r: bound(r, wide)):
        data, start = _rptdesign_with(change), time.process_time()
        read_da2_file(data, "r.dareport")
        assert time.process_time() - start < 1.5  # the wide case: 0.26 s on a Mac, 0.6 s on a GitHub runner


def test_values_spelled_in_classes_receivers_sums_and_truth_tests_are_judged():
    """A value spelled in a regular expression's classes or counts, called on by replace or split, held by a binding
    or computed column that works on the field (pid - 12345678 is 0 for one patient), or in an expression tested for
    its truth (a visibility rule, a condition) is found; a label, a count of patients and a display are not."""
    pid, name, ns = 'row["Person Id"]', 'row["Person Name Full Format"]', da2_files.BIRT_NS
    rule, bound = _RULE, _bound
    def vis(root, expr):
        next(root.find(f".//{B}table/{B}detail").iter(f"{B}data")).insert(0, ET.fromstring(
            f'<list-property xmlns="{ns}" name="visibility"><structure><property name="format">all</property>'
            f'<expression name="valueExpr">{expr}</expression></structure></list-property>'))
    for change in (lambda r: vis(r, f'String({pid}).match(/^[1][2][3][4][5][6][7][8]$/)'),
                   lambda r: vis(r, f'String({pid}).match(/^1{{8}}$/)'),
                   lambda r: vis(r, f'{name}.match(/^[D][O][E], [J][A][N][E]$/)'),
                   lambda r: bound(r, [("pidc", 'dataSetRow["cc"]')], f'{pid} == row["pidc"]', [("cc", '"12345678"')]),
                   lambda r: bound(r, [("b", '"12345678".replace(String(dataSetRow["Person Id"]), "")')],
                                   'row["b"] == ""'),
                   lambda r: vis(r, f'"DOE, JANE".split({name})[1]'),
                   lambda r: bound(r, [("d", 'dataSetRow["Person Id"] + (-12345678)')], 'row["d"] == 0'),
                   lambda r: bound(r, [("d", 'dataSetRow["Person Id"] * 1 - 12345678')], 'row["d"] == 0'),
                   lambda r: bound(r, [("d", 'Number(dataSetRow["Person Id"]) + Number("-12345678")')], 'row["d"] == 0'),
                   lambda r: vis(r, f'Number({pid}) + (-12345678)'),
                   lambda r: bound(r, [("shown", f'Number({pid}) + (-12345678) ? "" : "TARGET"')]),
                   lambda r: bound(r, [("d", 'dataSetRow["Person Id"] + Number("-1234" + "5678")')], 'row["d"] == 0'),
                   lambda r: bound(r, [("d", 'dataSetRow["Person Id"] + "-12345678"')],
                                   'row["d"].split("-")[0] == row["d"].split("-")[1]'),
                   lambda r: bound(r, [("x", f'"FIN " + {pid} + " of " + row["Enc Type"]')], 'row["x"] != ""'),
                   *(lambda r, f=f: bound(r, [("m", f'Total.{f}(dataSetRow["Person Id"], 1)')], 'row["m"] == 12345678')
                     for f in ("movingAve", "ave", "sum", "weightedAve", "runningSum", "npv")),
                   lambda r: bound(r, [("m", '({Total: {count: String}}).Total.count(dataSetRow["Person Id"])')],
                                   'row["m"] == 12345678'),
                   lambda r: bound(r, [("m", 'var $Total = {count: String}; $Total.count(dataSetRow["Person Id"])')],
                                   'row["m"] == 12345678'),
                   lambda r: bound(r, [("m", 'Total = {count: String}; Total.count(dataSetRow["Person Id"])')],
                                   'row["m"] == 12345678'),
                   # Total given a value through a string, or in another binding: no count is trusted then
                   *(lambda r, e=e: bound(r, [("m", e + 'Total.count(dataSetRow["Person Id"])')], 'row["m"] == 12345678')
                     for e in ('with ({"Total": {count: String}}) ', 'eval("var Total = {count: String}"); ',
                               'Object.defineProperty(this, "Total", {value: {count: String}}); ')),
                   lambda r: bound(r, [("a", "Total = {count: String}"), ("m", 'Total.count(dataSetRow["Person Id"])')],
                                   'row["m"] == 12345678'),
                   lambda r: bound(r, [("m", 'eval("dataSet" + "Row")["Person Id"]')], 'row["m"] == 12345678')):
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(change), "r.dareport")
    with pytest.raises(DA2FileError, match="read more than one way"):  # {} / 1: a division or a regular expression
        read_da2_file(_rptdesign_with(lambda r: bound(r, [("m", '(Total.count({} / 1), dataSetRow["Person Id"] /1/ 1)')],
                                                      'row["m"] == 12345678')), "r.dareport")
    with pytest.raises(DA2FileError, match="two values"):  # which one BIRT would use can't be told
        read_da2_file(_rptdesign_with(lambda r: bound(r, [("m", '"none"</expression><expression name="expression">'
                                                                'dataSetRow["Person Id"]')], 'row["m"] == 1')),
                      "r.dareport")
    counts = [(n, f'Total.countDistinct(dataSetRow["Enc Id"], dataSetRow["Enc Type"] == "Inpatient"{extra})')
              for n, extra in (("den", ""), ("num", ' &amp;&amp; dataSetRow["Age"] &gt;= 65'))]
    for change in (lambda r: bound(r, counts + [("pct", 'row["num"] / row["den"] * 100')]),
                   lambda r: bound(r, [("n", 'Total.count(dataSetRow["Person Id"])')], 'row["n"] &gt; 1'),
                   lambda r: bound(r, [("shown", f'{pid} ? "FIN " + {pid} : "none"')])):
        read_da2_file(_rptdesign_with(change), "r.dareport")
    data = _rptdesign_with(lambda r: [_table_rule(r, rule("/{" + "1" * 19_987 + "/")) for _ in range(9)])  # a count
    start = time.process_time()  # left open
    read_da2_file(data, "r.dareport")
    assert time.process_time() - start < 0.5


def test_removing_a_column_keeps_the_other_cells():
    """A column's cell in every row goes with it, so a cell another field shares, or a group row's cell above it, stops
    the removal; a column shown twice loses both cells."""
    with pytest.raises(DA2FileError, match="a group or header row's cell above it"):  # the unit's patient count
        read_da2_file(_dareport(), "r.dareport").remove_column("Enc Financial Num")
    def nested(root):
        cell = next(c for c in root.iter(f"{B}cell") if c.findtext(f".//{B}property[@name='resultSetColumn']")
                    == "Enc Loc Bed")
        cell.append(ET.fromstring(f'<grid xmlns="{da2_files.BIRT_NS}"><column/><row><cell><data><property '
                                  'name="resultSetColumn">Enc Loc Nurse Unit</property></data></cell></row></grid>'))
    with pytest.raises(DA2FileError, match="a cell it shares with other fields"):
        read_da2_file(_rptdesign_with(nested), "r.dareport").remove_column("Enc Loc Bed")
    def twice(root):
        table = root.find(f".//{B}table")
        cols = table.findall(f"{B}column")
        table.insert(list(table).index(cols[-1]) + 1, ET.Element(f"{B}column"))
        for path in ("header", "group/header", "detail", "group/footer", "footer"):
            for row in table.findall(f"{B}{path.replace('/', '/' + B)}/{B}row"):
                if sum(int(c.findtext(f"{B}property[@name='colSpan']") or 1) for c in row.findall(f"{B}cell")) \
                        == len(cols):
                    ET.SubElement(row, f"{B}cell")
        ET.SubElement(ET.SubElement(table.find(f"{B}detail/{B}row")[-1], f"{B}data"), f"{B}property",
                      {"name": "resultSetColumn"}).text = "Enc Loc Bed"
    data = _rptdesign_with(twice)
    files = read_da2_file(data, "r.dareport")
    assert files.remove_column("Enc Loc Bed").startswith("Removed")
    table = _table(_member(files.files()["r.dareport"], ".rptdesign"))
    assert len(table.findall(f"{B}column")) == len(_table(_member(data, ".rptdesign")).findall(f"{B}column")) - 2
    assert len(table.find(f"{B}detail/{B}row").findall(f"{B}cell")) == len(table.findall(f"{B}column"))
    assert "Enc Loc Bed" not in ET.tostring(table, encoding="unicode")


def test_a_table_with_many_rows_keeps_its_layout():
    """Each added column puts a cell in every full-width row: a forged table with thousands of rows grew past the memory
    limit in a few changes."""
    def rows(root):
        header = root.find(f".//{B}table/{B}header")
        header.extend(ET.fromstring(f'<row xmlns="{da2_files.BIRT_NS}"><cell/></row>') for _ in range(20))
    files = read_da2_file(_rptdesign_with(rows), "r.dareport")
    assert "more than 20 rows" in files.summary()
    assert "The layout is unchanged (its table has more than 20 rows)" in files.add_column("enc admit source")
    with pytest.raises(DA2FileError, match="more than 20 rows. Remove it in DA2"):
        files.remove_column("Enc Loc Bed")
    def wide(root):  # a removal finds each row's cell at each place the column is shown
        table = root.find(f".//{B}table")
        table.extend(ET.fromstring(f'<column xmlns="{da2_files.BIRT_NS}"/>') for _ in range(200))
    files = read_da2_file(_rptdesign_with(wide), "r.dareport")
    assert "more than 200 columns" in files.summary()
    with pytest.raises(DA2FileError, match="more than 200 columns. Remove it in DA2"):
        files.remove_column("Enc Loc Bed")
    def zero(root):  # a span of 0 merges (it covers no column): before, its removal looked past the last column
        root.find(f".//{B}table/{B}detail/{B}row").append(ET.fromstring(
            f'<cell xmlns="{da2_files.BIRT_NS}"><property name="colSpan">0</property><data><property '
            'name="resultSetColumn">Enc Loc Bed</property></data></cell>'))
    files = read_da2_file(_rptdesign_with(zero), "r.dareport")
    with pytest.raises(DA2FileError, match="merges cells. Remove it in DA2"):
        files.remove_column("Enc Loc Bed")
    def grid(root):  # a grid in a header cell has rows of its own, which an added column doesn't touch
        cell = root.find(f".//{B}table/{B}header/{B}row/{B}cell")
        cell.append(ET.fromstring(f'<grid xmlns="{da2_files.BIRT_NS}"><column/>' + "<row><cell/></row>" * 30 + "</grid>"))
    files = read_da2_file(_rptdesign_with(grid), "r.dareport")
    assert files.remove_column("Enc Loc Bed").startswith("Removed")


def test_extra_data_sets_are_refused_and_a_big_layout_reads_quickly():
    members = _members(_dareport())
    rpt = next(n for n in members if n.endswith(".rptdesign"))
    sets = [b'<oda-data-set name="d%d"><xml-property name="queryText">00000000-0000-4000-8000-000000000000'
            b'</xml-property></oda-data-set>' % i for i in range(20)]
    with pytest.raises(DA2FileError, match="have 21 query data sets"):
        read_da2_file(_zip({**members, rpt: members[rpt].replace(b"</report>", b"".join(sets) + b"</report>", 1)}),
                      "r.dareport")
    with pytest.raises(DA2FileError, match="two data sets of one name"):
        read_da2_file(_zip({**members, rpt: members[rpt].replace(b"</report>", sets[0] * 2 + b"</report>", 1)}),
                      "r.dareport")
    big = members[rpt].replace(b"</report>", b"".join(sets[:19]) + b"<grid>" + b"<cell/>" * 18_000 + b"</grid></report>", 1)
    with mock.patch.object(da2_files, "_prop", wraps=da2_files._prop) as prop:
        read_da2_file(_zip({**members, rpt: big}), "r.dareport")
    assert prop.call_count < 40_000  # one look at each of the ~20,000 elements, not one per data set


def test_a_deep_nest_of_filters_reads_quickly():
    """Each value is judged by the filter it's in: judging it again for every enclosing filter took 20 seconds."""
    members = _members(_dareport())
    query = next(n for n in members if n.endswith(".daquery"))
    uid = LIVE_CWC_ELEMENT_MAP["Person Name Full Format"][1]
    nest = (f'<filter><element name="x" uuid="{uid}"/><operator type="LIKE"/>' * 190
            + '<values><value>%' + "zz " * 500_000 + 'ab%</value></values>' + "</filter>" * 190)  # one piece
    members[query] = members[query].replace(b"<qualification>", b"<qualification>" + nest.encode(), 1)
    started = time.perf_counter()
    read_da2_file(_zip(members), "r.dareport")
    assert time.perf_counter() - started < 3


def test_removing_a_column_bound_many_times_is_quick():
    """One pattern finds every column reference: one alternative per binding name took a minute on a file like this."""
    members = _members(_dareport())
    rpt = next(n for n in members if n.endswith(".rptdesign"))
    column = read_da2_file(_dareport(), "r.dareport").queries()[0][2][-1].name
    binds = "".join(f'<structure><property name="name">b{i}</property><expression name="expression">'
                    f'dataSetRow["{column}"]</expression></structure>' for i in range(1000))
    text = "row['" * 300_000  # a reference opened 300,000 times and never closed
    grid = (f'<grid id="99999"><list-property name="boundDataColumns">{binds}</list-property>'
            f'<text-property name="content">{text}</text-property></grid>')
    members[rpt] = members[rpt].replace(b"</report>", grid.encode() + b"</report>", 1)
    files = read_da2_file(_zip(members), "r.dareport")
    started = time.perf_counter()
    assert files.remove_column(column).startswith("Removed the column")
    assert time.perf_counter() - started < 2


def test_odd_layout_ids_are_tolerated_and_xml_da2_never_writes_is_refused():
    members = _members(_dareport())
    rpt = next(n for n in members if n.endswith(".rptdesign"))
    root = ET.fromstring(members[rpt])
    next(e for e in root.iter() if e.get("id")).set("id", "²")  # isdigit() but not int()
    members[rpt] = ET.tostring(root)
    files = read_da2_file(_zip(members), "census.dareport")
    assert files.add_column("Enc Admit Source").startswith("Added the column")
    assert files.queries()[0][3]
    # BIRT reads XML as it streams, so a comment or PI splits a filter's text and xmlns="" may not hide one
    query = next(n for n in members if n.endswith(".daquery"))
    _table_rule(root, _RULE("MARK"))
    body = ET.tostring(root)
    for name, xml in [(query, members[query].replace(b"<qualification>", b"<qualification><!-- a note -->", 1))] + [
            (rpt, body.replace(b'<expression name="expr">MARK</expression>', x)) for x in (
                b'<expression name="expr">row["Person Id"]<!-- x --> == 12345678</expression>',
                b'<expression name="expr">row["Person Id"]<?x y?> == 12345678</expression>',
                b'<expression name="expr">row["Person Id"]<b/> == 12345678</expression>',
                b'<expression xmlns="" name="expr">row["Person Id"] == 12345678</expression>',
                b'<expression name="expr">true</expression>'
                b'<expression xmlns="" name="expr">row["Person Id"] == 12345678</expression>')]:
        assert name == query or xml != body
        with pytest.raises(DA2FileError, match="doesn't write"):
            read_da2_file(_zip({**members, name: xml}), "census.dareport")


def test_code_that_hides_a_read_from_the_word_checks_is_judged():
    """A name given another value by for-in, catch, a parameter, destructuring, a spread or &&=, a string that is only
    part of a name's value, a member read by a key built from strings or by a name on a value that is no name,
    reportContext.evaluate and a Total read after ". " are all judged as reads. A template string is refused."""
    t = 'row["m"] == 12345678'
    for change in (
            lambda r: _bound(r, [("a", '`${this["T" + "otal"] = {count: String}}`'),
                                 ("m", 'Total.count(dataSetRow["Person Id"])')], t),
            *(lambda r, e=e: _bound(r, [("m", e)], t) for e in (
                '`${eval("dataSet" + "Row")["Person Id"]}`', 'var k = "Person Id"; `${(dataSetRow)[k]}`',
                'var C = "Enc Type"; [...C] = ["Person Id"]; dataSetRow[C]'))):  # a spread, which Rhino can't read
        with pytest.raises(DA2FileError, match="read more than one way"):
            read_da2_file(_rptdesign_with(change), "r.dareport")
    for e in ('var C = "" + "Person Id"; dataSetRow[C]',
                'var C = "Enc Type"\nin {}; dataSetRow[C]',
                'var k = "con" + "structor"; [][k][k]("return dataSet" + "Row")()["Person Id"]',
                'var k = "con" + "structor"; ""[k][k]("return dataSet" + "Row")()["Person Id"]',
                'var C = "Enc Type"; for (C in {"Person Id": 1}); dataSetRow[C]',
                'var C = "Enc Type"; try { throw "Person Id" } catch (C) { dataSetRow[C] }',
                'var C = "Enc Type"; (function (C) { return dataSetRow[C] })("Person Id")',
                'var C = "Enc Type"; [C] = ["Person Id"]; dataSetRow[C]',
                'var C = "Enc Type"; C &amp;&amp;= "Person Id"; dataSetRow[C]',
                '[]["con" + "structor"]["con" + "structor"]("return dataSet" + "Row")()["Person Id"]',
                'reportContext.evaluate("dataSet" + "Row")["Person Id"]',
                '({"T\\x6ftal": {count: String}}). Total.count(dataSetRow["Person Id"])'):
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(lambda r: _bound(r, [("m", e)], t)), "r.dareport")
    total = [0]  # what the given name adds is charged
    assert da2_files._code('var C = "Enc Type"; dataSetRow[C]', total).endswith('dataSetRow["Enc Type"]')
    assert total == [len('"Enc Type"') - 1]
    start = time.process_time()  # a long name read many times stays a computed read; a long blank key reads once
    huge = 'var N = "' + "q" * 10_000 + '"; ' + "+".join(["row[N]"] * 1_400)
    assert da2_files._code(huge, total) == huge and total == [9]
    assert da2_files.DYNAMIC_RE.search("a[" + " " * 50_000 + "x]")
    assert time.process_time() - start < 0.5


def test_code_is_read_where_javascript_ends_a_line_and_unclear_slashes_are_refused():
    """A // comment ends at a CR as at a LF, so the code after it is read, and a keyword after a dot and a comment is
    a member (x./**/throw / 2 divides). JavaScript that reads more than one way is refused: an HTML-style comment, a /
    that is a division or a regular expression by what came before (}, ++, the ) of an if or a for each), an XML
    literal, an invisible or non-ASCII character in code (U+2028 ends a line), a backslash before a line end, and a
    column reference that runs across strings. So is a / or < after a name and a line end, where JavaScript may end
    the statement (var x, break, debugger), a keyword after a number's dot (0. in, 0.&#10;if (…) /…/), of used as a
    name, a / right after if, while, for, with or let, and Rhino's own forms: catch (e if /…/), let (…) /…/ and an
    expression closure, function () /…/ (any ) before a / in a text with function, get, set or an object literal, whose
    m() /…/ is a method). So is a spread, a number glued to a name (0in: Rhino ends the number and reads the keyword),
    and E4X's wildcards and attributes (x.*, x.@a), whose * is an operand. A block's f() / 2 divides."""
    rule = '<property name="operator">is-true</property><expression name="expr">{}</expression>'.format
    for e in ('true // a note&#13;&amp;&amp; row["Person Id"] == 12345678',
              '({})./**/throw /"/ + 1 // "; row["Person Id"] == 12345678',
              '({}).//&#10;return /"/ + 1 // "; row["Person Id"] == 12345678'):
        with pytest.raises(DA2FileError, match="patient identifiers"):
            read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule(e))), "r.dareport")
    for e in ('row["Person Id"] == 12345678 &lt;!-- x', '--&gt; row["Enc Type"] == "x"', 'row["Enc Type"]++ /1/ 1',
              'if (row["Enc Type"]) /x/.test(row["Enc Type"])', 'true // a note&#x2028;&amp;&amp; row["Enc Type"] == 1',
              'true // a note&#x2029;&amp;&amp; row["Enc Type"] == 1', 'row["Person Id"] == [\'row[\', 12345678, \']\'][1]',
              'row["Person Id"] == ["dataSetRow[", 12345678, "]"][1]', 'true /* *&#x200b;/ &amp;&amp; row["Enc Type"] == 1 /* */',
              'row["Enc Type"] == "In&#x200b;patient"', 'row["Enc Type"] == "In&#x7f;patient"',
              'x&#x7f;in [1] || row["Enc Type"] == 1', 'for each (k in [1]) /[//]/.test(k); row["Enc Type"] == 1',
              '/a\\&#10;[//]/; row["Enc Type"] == 1', '&lt;a&gt;"&lt;/a&gt; &amp;&amp; row["Enc Type"] == 1 &amp;&amp; &lt;b&gt;"&lt;/b&gt;',
              'row["Enc Type"] == 1 ? &lt;a/&gt; : 0', 'row["Enc Type"] == "x" &amp;&amp; \\u0074rue',
              'var x&#10;/a/*1 || row["Enc Type"] == 1 /**/', 'debugger&#13;/[//]/; row["Enc Type"] == 1',
              'var x&#10;&lt;a&gt;//&lt;/a&gt;; row["Enc Type"] == 1', '0. in /[//]/ || row["Enc Type"] == 1',
              '0.&#10;if (1) /[//]/; row["Enc Type"] == 1', '0.&#10;for each (k in [1]) /[//]/; row["Enc Type"] == 1',
              'var of = 1; of /(row)["Enc Type"] == 1 || 0/ 1',
              'try { throw 1 } catch (e if /[//]/) {} row["Enc Type"] == 1', 'let (k = 1) /[//]/; row["Enc Type"] == 1',
              '(function () /[//]/)(); row["Enc Type"] == 1', '[...typeof /[//]/]; row["Enc Type"] == 1',
              '0in /[//]/; row["Enc Type"] == 1', '1.5in /[//]/; row["Enc Type"] == 1', '0x1in /[//]/; row["Enc Type"] == 1',
              'var x = 1; x.* /(row)["Enc Type"] == 1 || 0/ 1', 'x.@a', 'x.n::* /a/ 1', '({m() /[//]/}); row["Enc Type"] == 1',
              'x = // c&#10;{m() /[//]/}; row["Enc Type"] == 1', 'for (;{m() /[//]/};) break; row["Enc Type"] == 1',
              'x = let (k = 1) {m() /[//]/}; row["Enc Type"] == 1',
              'try { throw 1 } catch (e if {m() /[//]/}) {} row["Enc Type"] == 1',
              'var each = Object; new each(1) {m() /[//]/}; row["Enc Type"] == 1'):  # Rhino: an object after new X(…)
        with pytest.raises(DA2FileError, match="read more than one way"):
            read_da2_file(_rptdesign_with(lambda r: _table_rule(r, rule(e))), "r.dareport")
    def kinds(text):
        return [k for k, *_ in da2_files._lex(text)]
    assert kinds("return /a/.test(s)") == ["r"] and kinds("x.return /a/ 1") == kinds("(a) /b/ 1") == []
    assert kinds("a" + "1" * 40 + "return /a/ 1") == [] and kinds('"s" /a/ 1') == ["s"] and kinds("a // b\rc / d") == ["c"]
    assert kinds("}/a/") == kinds("x++ /a/") == kinds("yield /a/") == kinds("for each (k in x) /a/") == ["?", "r"]
    assert kinds("x./**/throw /a/ 1") == kinds("x.//\nreturn /a/ 1") == ["c"] and kinds("a < b") == []
    assert kinds("(<a/>)") == kinds("return <a/>") == ["?"]
    assert kinds("var x\n/a/ 1") == kinds("0. in /a/ 1") == ["?", "r"] and kinds("0./**/in /a/ 1") == ["c", "?", "r"]
    assert kinds("(a)\n/b/ 1") == kinds("x.in /a/ 1") == kinds("0 .in /a/ 1") == kinds("x = 1\n+ 2 /a/ 1") == []
    assert kinds("0.\nif (1) /a/") == kinds("0.\nfor each (k in x) /a/") == kinds("of /a/ 1") == kinds("if /a/") == kinds("let (k) /a/") == ["?", "r"]
    assert kinds("function f() /a/") == kinds("({get x() /a/})") == ["?", "r"] and kinds("f(x) / 2 / 3") == []
    assert kinds("({m() /a/})") == kinds("x = {m() /a/}") == ["?", "r"] and kinds("1e10 / 2 / 3") == []
    assert kinds("if (a) {\n b.s(b.g() / 6 / 1);\n} else {\n null;\n}") == kinds("a => { f() / 2 / 3 }") == []
    assert kinds("x = // c\n{m() /a/}") == kinds("x = /* c */ {m() /a/}") == ["c", "?", "r"]  # an object, by its code
    assert kinds("try { f() / 2 / 3 } catch (e) { g() / 2 } finally { h() / 2 }") == kinds("for (;;) { f() / 2 }") == []
    assert kinds("do { f() / 2 / 3 } while (a)") == kinds("switch (a) { case 1: f() / 2 / 3 }") == []


def test_long_names_are_refused_and_names_are_charged():
    """DA2's names and settings are under a hundred characters: a longer attribute or <property> text (a span of 5,000
    digits too) is refused. The characters of filter field names, and of binding names, are charged, as is each
    filter."""
    members = _members(_dareport())
    q, rpt = (next(n for n in members if n.endswith(s)) for s in (".daquery", ".rptdesign"))
    for name, body in ((q, members[q].replace(b'<value text="ZZ"/>', b'<value text="' + b"Z" * 1_001 + b'"/>')),
                       (rpt, members[rpt].replace(b"</report>", b'<label id="99"><property name="rowSpan">'
                                                  + b"9" * 5_000 + b"</property></label></report>"))):
        assert body != members[name]
        with pytest.raises(DA2FileError, match="over 1,000 characters"):
            read_da2_file(_zip({**members, name: body}), "r.dareport")
    logo = members[rpt].replace(b"</report>", b'<list-property name="images"><structure><property name="name">logo.png'
                                b'</property><property name="data">' + b"iVBO" * 5_000 + b"</property></structure>"
                                b"</list-property></report>")  # an embedded image is base64 in its data property
    assert logo != members[rpt] and read_da2_file(_zip({**members, rpt: logo}), "r.dareport")
    base = read_da2_file(_zip(members), "r.dareport").work
    more = members[q].replace(b"<qualification>", b'<qualification><filter2><element name="' + b"x" * 900
                              + b'"/><operator type="LESS THAN"/><value>1</value></filter2>', 1)
    assert read_da2_file(_zip({**members, q: more}), "r.dareport").work - base \
        >= da2_files.FILTER_WORK + da2_files.FIELD_CHAR * 900
    short, long = (read_da2_file(_rptdesign_with(lambda r: _bound(r, [(n, "1")])), "r.dareport").work
                   for n in ("b", "b" * 900))
    assert long - short >= da2_files.BINDING_CHAR * 899
    wide = read_da2_file(_rptdesign_with(lambda r: _bound(r, [("\ufdfa" * 90, "1")])), "r.dareport").work
    assert wide - short >= da2_files.BINDING_CHAR * da2_files.NFKC_MOST * 89  # NFKC spells U+FDFA out to 18
    for span in ("text-property", "expression"):  # past int()'s 4,300 digits, given another way than <property>
        assert read_da2_file(_zip({**members, rpt: members[rpt].replace(b"</report>", b'<label id="98"><' + span.encode()
                                   + b' name="rowSpan">' + b"9" * 5_000 + b"</" + span.encode() + b"></label></report>")}),
                             "r.dareport")
    for uri, kept in ((b"u" * 64, True), (b"u" * 65, False), ("\U0001F600".encode() * 16, True),
                      ("\U0001F600".encode() * 17, False)):  # each prefixed name the parser builds copies its bytes
        body = members[rpt].replace(b"<report ", b'<report xmlns:p="' + uri + b'" ', 1)
        assert body != members[rpt]
        if kept:
            read_da2_file(_zip({**members, rpt: body}), "r.dareport")
        else:
            with pytest.raises(DA2FileError, match="XML namespace over 64"):
                read_da2_file(_zip({**members, rpt: body}), "r.dareport")
    for decoy in (b"<report d=' xmlns=\"' ", b"<report> xmlns=\"</report><report "):  # an earlier xmlns can't swallow it
        body = members[rpt].replace(b"<report ", decoy + b'xmlns:p="' + b"u" * 65 + b'" ', 1)
        with pytest.raises(DA2FileError, match="XML namespace over 64"):
            read_da2_file(_zip({**members, rpt: body}), "r.dareport")
    base = read_da2_file(_zip(members), "r.dareport").work  # each distinct attribute name is built and kept
    names = "".join(f'<label p:a{i}=""/>' for i in range(2_000))
    body = members[rpt].replace(b"<report ", b'<report xmlns:p="u" ', 1).replace(b"</report>", names.encode() + b"</report>")
    assert read_da2_file(_zip({**members, rpt: body}), "r.dareport").work >= base + 10 * 2 * 2_000
    started = time.process_time()  # the check reads each byte about once: a run of xmlns with no value is no slower
    with pytest.raises(DA2FileError, match="well-formed"):
        read_da2_file(_zip({**members, rpt: b"<report " + b"xmlns" * 300_000 + b"/>"}), "r.dareport")
    assert time.process_time() - started < 1


def test_reading_expressions_is_charged_where_it_is_done_and_stops_when_it_runs_long(monkeypatch):
    """Lexing and blanking are counted each time one is worked out, a read stops when that passes MAX_SCAN_WORK, and a
    long name given a short value takes nothing off the layout's length."""
    rule = '<property name="operator">is-true</property><expression name="expr">{}</expression>'.format
    data = _rptdesign_with(lambda r: [_table_rule(r, rule(f"x{i}" + "/a" * 9_000)) for i in range(10)])
    assert read_da2_file(data, "r.dareport").work > 1_000_000  # 90,000 slashes, each judged by the word before it
    monkeypatch.setattr(da2_files, "MAX_SCAN_WORK", 500_000)
    with pytest.raises(DA2FileError, match="far longer to check"):
        read_da2_file(data, "r.dareport")
    work = da2_files._SCAN.work
    da2_files._lex("a/b/c")
    assert da2_files._SCAN.work > work and da2_files._SCAN.limit == float("inf")
    total = [0]
    da2_files._code(f'var {"N" * 50} = ""; ' + f'row[{"N" * 50}]' * 3, total)
    assert total == [0]
    work = da2_files._SCAN.work
    assert len(da2_files._refs("row.a + row.b")) == 2 and da2_files._SCAN.work > work  # column reads are followed
    members = _members(_dareport())
    q = next(n for n in members if n.endswith(".daquery"))
    long = {**members, q: members[q].replace(b'<value text="ZZ"/>', b'<value text="ZZ">' + b"1 " * 5_000 + b'</value>')}
    assert members[q] != long[q]  # a name filter's value is split into pieces character by character
    assert read_da2_file(_zip(long), "census.dareport").work - read_da2_file(_zip(members), "census.dareport").work \
        >= da2_files.NAME_CHAR * 10_000


@pytest.mark.skipif(not os.environ.get("ENDEXIS_DA2_FIXTURES"), reason="real DA2 exports are kept outside the repo")
def test_real_da2_exports_read_and_round_trip():
    paths = [p for p in pathlib.Path(os.environ["ENDEXIS_DA2_FIXTURES"]).iterdir()
             if p.suffix.lower() in (".dareport", ".daquery")]
    assert paths
    for path in paths:
        data = path.read_bytes()
        files = read_da2_file(data, path.name)
        assert files.queries() and files.summary()
        out = files.files(new_ids=False)[path.name]
        if path.suffix.lower() == ".dareport":
            assert _members(out) == _members(data)
        else:
            assert out == data


def test_names_from_a_file_are_shown_as_plain_text():
    root = ET.fromstring(_member(_dareport(), ".daquery"))
    root.find("queries").set("name", "Census`\n![x](https://e.test/x.png) <img src=x>")
    files = read_da2_file(ET.tostring(root), "census.daquery")
    assert "- Query `Census' ![x](https://e.test/x.png) ‹img src=x›`," in files.summary()
    assert shown("Unit</details>") == "`Unit‹/details›`"  # Open WebUI ends a <details> there, code span or not
    assert shown("") == "`(blank)`" and shown("x" * 300) == f"`{'x' * 199}…`"


def test_a_summary_lists_the_first_columns_and_filters_of_a_query():
    files = read_da2_file(_dareport(), "census.dareport")
    n = len(files.queries()[0][2])
    with mock.patch.object(da2_files, "MAX_LISTED", 1):
        said = files.summary()
    assert f"{n} columns: `Enc Id`, and {n - 1:,} more\n" in said and re.search(r"Filters: `[^`]*`, and \d+ more$", said)
    assert " more" not in files.summary()


def test_element_lookup_ignores_case_and_knows_only_catalog_names():
    assert known_element("  enc ADMIT source ") == "Enc Admit Source" in element_names()
    assert known_element("Favourite Colour") is None


def test_reads_are_charged_for_nested_values_bindings_and_value_attributes():
    """A rule's values are read once however its value1 elements nest, and DA2Files.work counts each binding and each
    attribute of a filter value, which the checks read on their own."""
    def nest(root):
        at = ET.SubElement(ET.SubElement(root, f"{B}list-property", name="filter"), f"{B}structure")
        for _ in range(195):
            at = ET.SubElement(at, f"{B}simple-property-list", name="value1")
        for _ in range(5_000):
            ET.SubElement(at, f"{B}value")
    def nest_expr(root):  # an expression inside another, as no DA2 layout has, around one value list
        at = root
        for _ in range(195):
            at = ET.SubElement(at, f"{B}expression", name="e")
        ET.SubElement(at, f"{B}simple-property-list", name="value1").extend(ET.Element(f"{B}value") for _ in range(5_000))
    def reads(root):  # a rule's other settings hold no code, so their text is not searched for reads
        ET.SubElement(ET.SubElement(ET.SubElement(root, f"{B}list-property", name="filter"), f"{B}structure"),
                      f"{B}text-property", name="p").text = ".".join(f"row.a{i}" for i in range(150_000))
    for change in (nest, nest_expr, reads):
        data, start = _rptdesign_with(change), time.process_time()
        read_da2_file(data, "r.dareport")
        assert time.process_time() - start < 0.5
    base = read_da2_file(_dareport(), "r.dareport").work
    def empty(root):
        host = root.find(f".//{B}table/{B}list-property[@name='boundDataColumns']")
        host.extend(ET.Element(f"{B}structure") for _ in range(5_000))
    assert read_da2_file(_rptdesign_with(empty), "r.dareport").work - base >= 70 * 5_000
    members = _members(_dareport())
    name = next(n for n in members if n.endswith(".daquery"))
    body = members[name].decode()
    at = body.index("<value ") + len("<value ")
    members[name] = (body[:at] + " ".join(f'a{i}="AB%"' for i in range(5_000)) + " " + body[at:]).encode()
    assert read_da2_file(_zip(members), "r.dareport").work - base >= 40 * 5_000
