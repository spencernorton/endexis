"""Reading, changing and writing DA2 report files in place (discern_intelligence.da2_files)."""

import copy
import io
import os
import pathlib
import re
import time
import zipfile
import xml.etree.ElementTree as ET

import pytest
from unittest import mock

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
    ("q.daquery", b"<cclquery><queries>", "well-formed"),
    ("q.daquery", b"<report/>", "isn't a DA2"),
    ("q.daquery", b"<cclquery><queries/></cclquery>", "no output columns"),
])
def test_refused_files(name, data, why):
    with pytest.raises(DA2FileError, match=why):
        read_da2_file(data, name)


def test_refused_zip_members():
    members = _members(_dareport())
    with pytest.raises(DA2FileError, match="isn't part of a DA2 report"):
        read_da2_file(_zip({**members, "../evil.daquery": b"<cclquery/>"}), "r.dareport")
    with pytest.raises(DA2FileError, match="isn't part of a DA2 report"):
        read_da2_file(_zip({**members, "patients.csv": b"a,b"}), "r.dareport")
    with pytest.raises(DA2FileError, match="unpacks to more than 2,000,000 bytes"):
        read_da2_file(_zip({**members, "big.daquery": b" " * 2_000_001}), "r.dareport")


def test_patient_identifier_filters_are_refused_without_echoing_values():
    daquery = _bundle(value_filters={"Enc Type": ["Inpatient"]}).queries[0].daquery_xml
    fin = re.sub(r'(<filter [^>]*><element name=")Enc Type(")', r"\1Enc Financial Num\2", daquery, count=1)
    with pytest.raises(DA2FileError, match="`Enc Financial Num`") as exc:
        read_da2_file(fin.encode(), "q.daquery")
    assert "Inpatient" not in str(exc.value)
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
    (dict(name="Person Name Last", op="BETWEEN", values="<value text='SMITH'/><value text='SMITHZ'/>"),
     "Person Name Last"),
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
])
def test_codes_ranges_and_patterns_on_patient_fields_are_kept(change):
    assert read_da2_file(_refiltered(**change), "q.daquery").summary()


def test_text_match_refuses_patient_identifiers():
    files = read_da2_file(_dareport(), "census.dareport")
    with pytest.raises(DA2FileError, match="identifies patients"):
        files.text_match("Enc Financial Num", "%12345678%")
    with pytest.raises(DA2FileError, match="identifies patients"):
        files.text_match("Person Name Full Format", "smith, john")
    assert not files.changes


def test_comments_and_odd_layout_ids_are_tolerated():
    members = _members(_dareport())
    rpt = next(n for n in members if n.endswith(".rptdesign"))
    root = ET.fromstring(members[rpt])
    next(e for e in root.iter() if e.get("id")).set("id", "²")  # isdigit() but not int()
    members[rpt] = ET.tostring(root)
    query = next(n for n in members if n.endswith(".daquery"))
    members[query] = members[query].replace(b"<qualification>", b"<qualification><!-- a note -->", 1)
    files = read_da2_file(_zip(members), "census.dareport")
    assert files.add_column("Enc Admit Source").startswith("Added the column")
    assert files.queries()[0][3]


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


def test_element_lookup_ignores_case_and_knows_only_catalog_names():
    assert known_element("  enc ADMIT source ") == "Enc Admit Source" in element_names()
    assert known_element("Favourite Colour") is None
