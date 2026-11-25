"""Read DA2 report and query files, change them where they stand, and write them back.

A `.dareport` or `.daquery` (from DA2 or from Endexis) is edited in place. Only the XML members a
change touches are written again: every other member keeps its bytes, and whatever this module doesn't model (joins,
sub-queries, scripts, styles, other layout items) stays as it was. A change that can't be made safely raises
DA2FileError with a reason a DA2 user can act on, and nothing is changed.
"""

from __future__ import annotations

import bisect
import collections
import functools
import hashlib
import io
import itertools
import re
import struct
import threading
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
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
# A zip entry's extra field: DA2 writes none, and zipfile decodes one in time quadratic in its records (65 KB, 13 ms)
MAX_EXTRA_BYTES = 256
# What zipfile raises on a zip it can't read (UnicodeDecodeError for a name is a ValueError; NotImplementedError for a
# version it doesn't know)
ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError, struct.error, NotImplementedError,
              RuntimeError)
# Runtime-prompt texts parsed in one file: each takes a parser of its own (~6 us); real exports have 7 at most
MAX_PROMPT_PARSES = 100
MAX_DEPTH = 200
# Reading a member and each change walk all of it, so this bounds what they cost (a change adds a few dozen elements at
# most). DA2's own layouts have one to three thousand.
MAX_ELEMENTS = 20_000
# Each attribute name is built, decoded and kept per element, up to ~500 bytes for one in a long namespace. Counted as
# its = before parsing, with each # (a &#61; or &#60; becomes = or < in an embedded prompt): real exports have 4,100 at
# most, a 900 KB report of many styles 23,500.
MAX_ATTRIBUTES = 40_000
MAX_NAME_CHARS = 1_000  # an attribute or a <property>'s text: DA2's names and settings are under a hundred characters
MAX_TABLE_ROWS = 20  # an added column puts a cell in each of a table's rows; DA2's own tables have up to eight
MAX_LISTED = 100  # columns, and filters, a summary lists for each query (real exports: 51 columns at most)
MAX_TABLE_COLUMNS = 200  # a removal finds each row's cell at each place; real exports' tables have up to 51 columns
# Fixed, so the same changes to the same file always give the same new ids.
ID_NAMESPACE = uuid.UUID("4f1b8f8e-8d0c-5b9a-9a51-0e5d2f3c7a10")
# A value on one of these fields picks out a patient (a FIN, an MRN or other alias, a phone number, a street, a birth
# date), and the site holds no patient data, so a file that filters on one is refused. Codes such as an alias's type
# or pool, or a financial class, aren't values of that kind.
PATIENT_ID_FIELD_RE = re.compile(
    r"(?i)\b(?:c?mrn|ssn|dob|e-?mail)\b|\bfin\b(?! class)|medical record|social security|national health"
    r"|financial (?:num|number|id)\b|\b(?:encounter|preadmit|member|account|acct|phone|phn) (?:num|number|nbr)\b"
    r"|\bstreet\b|\bbirth (?:dt|date)|alias\b(?! (?:pool|type|active|beg|end|update|id|desc)\b)"
    r"|\baddr string\b|^(?!.*\b(?:prsn[il]|phys|provider|prvd|org|send|sender|updt|update|create)\b)"
    r".*\b(?:person|per|patient|pat|enc|encntr|encounter) id$")
# Patient names: a name, or a list of them, is patient data. A pattern ("contains O'B", which Endexis writes) or a range
# (DA2's own test-patient rule, Person Name Last < ZZ) is not, unless it spells two pieces of a name: "DOE%JANE%" in one
# value, or Last like DOE% and First like JANE% across the file's filters. "Name Hx La" is the history's last name.
PATIENT_NAME_FIELD_RE = re.compile(
    r"(?i)^(?:person|per|patient|pat)\b(?!.*\b(?:org|prsnl|phys|provider)\b).*\bname\b"
    r"|^name$|^name (?:first|last|middle|full|hx (?:full|last|first|la\b))|\b(?:card|guarantor|maiden|patient|person|sub per) name\b"
    r"|^ib rx p\b.*\bname\b")
NAME_PART_FIELD_RE = re.compile(r"(?i)\b(?:last|first|middle)\b|\bla$")  # a field holding one part of a name
RANGE_OPS = frozenset({"LESS THAN", "GREATER THAN", "LESS THAN OR EQUAL", "GREATER THAN OR EQUAL"})
# A piece of a name: letters, digits, apostrophes and hyphens. Anything else (a wildcard, a space, a comma, a dot)
# separates two pieces.
NAME_RUN_RE = re.compile(r"(?:[^\W_]|['’-])+")
NAME_LETTER_RE = re.compile(r"[^\W\d_zZ]")  # a letter other than Z: a run of Zs is DA2's test-patient marker
# Where a layout can use a column, by the BIRT property that holds the reference.
LAYOUT_USES = {"keyExpr": "a group", "expressionValue": "a group's contents entry", "onFetch": "a script",
               "beforeOpen": "a script", "afterOpen": "a script", "onCreate": "a script", "onRender": "a script",
               "testExpr": "a highlight rule", "expr": "a highlight rule", "expression": "a computed column",
               "valueExpr": "a visibility rule", "filterExpr": "a table filter", "sortExpr": "a sort"}
# A column reference in a layout expression. One pattern for every name: an alternation of thousands is too slow.
LAYOUT_REF_RE = re.compile(r"""(?:dataSetRow|row)(?:\s*\.\s*_outer)*\s*\[\s*(?:"([^"\\]*)"|'([^'\\]*)')\s*\]""")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DECL_RE = re.compile(rb"<\?xml[^>]*\?>\s*")
# Latin lookalikes from other scripts, folded before a field's name is matched: the catalog spells "PI Alias Type" with
# a Cyrillic р.
LOOKALIKES = str.maketrans("АВЕЅІЈКМНОРСТХУаеѕіјорсухԁԛԝΑΒΕΖΗΙΚΜΝΟΡΤΥΧαιοκνρτυχ",
                           "ABESIJKMHOPCTXYaesijopcyxdqwABEZHIKMNOPTYXaiokvptux")
# The layout lists whose rules test a column against values.
RULE_LISTS = ("filter", "highlightRules", "mapRules")
LAYOUT_DOT_REF_RE = re.compile(r"\b(?:dataSetRow|row)(?:\s*\.\s*_outer)*\s*\.\s*(?!_outer\b)(\w+)")
# A column read by a name worked out in the expression (row[3], row["Person" + " Id"], an escape): it could be any.
LAYOUT_COMPUTED_REF_RE = re.compile(r"""\b(?:dataSetRow|row)(?:\s*\.\s*_outer)*\s*\[(?!\s*(?:"[^"\\]*"|'[^'\\]*')\s*\])""")
_PARAM = (r"""\bparams\s*(?:\[\s*(?:"([^"]*)"|'([^']*)')\s*\]|\.\s*(\w+))"""
          r"""|\breportContext\s*\.\s*getParameterValue\s*\(\s*(?:"([^"]*)"|'([^']*)')\s*\)""")
PARAM_REF_RE = re.compile(_PARAM)
LAYOUT_ANY_REF_RE = re.compile(r"""\b(?:dataSetRow|row)(?:\s*\.\s*_outer)*\s*(?:\[\s*(?:"[^"]*"|'[^']*')\s*\]|\.\s*\w+)"""
                               rf"""|(?:{_PARAM})(?:\s*\.\s*value)?""")
# What a layout expression holds besides code, found in one walk (_lex): its comments, its strings (any escape, a line
# continuation too; an unclosed one runs to the end) and its regular expressions (a / where an operand starts).
_QUOTED = {q: re.compile(rf"{q}((?:[^{q}\\]|\\[\s\S])*)(?:{q}|\\?\Z)") for q in "\"'`"}
_REGEX = re.compile(r"/(?![/*])((?:[^/\\\n\r\u2028\u2029\[]|\\[^\n\r\u2028\u2029]|"
                    r"\[(?:[^\]\\\n\r\u2028\u2029]|\\[^\n\r\u2028\u2029])*+\])++)/")  # a / in a [class] is no end
_LEX_START = re.compile(r"""//|/\*|["'`/]""")
_LINE_END_RE = re.compile(r"[\n\r\u2028\u2029]")  # where JavaScript ends a // comment, a CR alone too
_WALK_RE = re.compile(r"[()<{]")
_WORD_END_RE = re.compile(r"(?<![\w$])[A-Za-z_$][\w$]*\Z")
_OPERAND_END = re.compile(r"[\w$)\]@\\.]")  # a / after one of these divides, unless the word is one of these:
_REGEX_AFTER = frozenset({"typeof", "return", "case", "do", "else", "in", "instanceof", "new", "delete", "void",
                          "throw"})
_UNSURE_AFTER = frozenset({"yield", "await", "of"})  # a keyword or a name, by where it is
_HEAD_WORDS = frozenset({"if", "while", "for", "with", "each", "let"})  # a / after the ) of their ( starts a regular
# expression (for each, a let expression and catch (e if …), in Rhino), and so may one after the word itself
_CLOSURE_RE = re.compile(r"\b(?:function|get|set)\b")  # Rhino's function () /re/: a / after any ) is unsure
# A { is a block after these words, after the ) of these words' (, after =>, at the start, after a { or }, or after a
# ; outside brackets. Any other is taken for an object literal, whose m() /re/ is a method with an expression body in
# Rhino 1.7.14: a / after any ) is then unsure (a let expression's ), a catch guard's if, a for head's ; too).
_BLOCK_AFTER = frozenset({"else", "do", "try", "finally"})
_BLOCK_HEADS = frozenset({"if", "while", "for", "with", "each", "switch", "catch"})
_KEYWORDS = _REGEX_AFTER | _UNSURE_AFTER | _HEAD_WORDS
NUMBER_RE = re.compile(r"(?<![\w.])\.?\d[\w.:/-]*")  # a number in any spelling (0xBC614E, .5e8)
# Code JavaScript reads differently in Rhino: a number glued to a name (0in, 1.5in: Rhino ends the number and reads the
# keyword), and E4X's wildcards and attributes (x.*, x.@*, ns::*), whose * is an operand.
GLUED_RE = re.compile(r"(?<![\w$])(?>0[xX][\da-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)[A-Za-z_$]|@|[.:]\s*\*")
# Text run as code or the global object (eval("dataSet" + "Row"), this["Total"], with): it could read any column.
CODE_RE = re.compile(r"\b(?:eval|evaluate|Function|this|globalThis|constructor|prototype|__proto__|with)\b|\\u|`")
# The row or parameter object read some other way ((row)["Person Id"], [row][0], r\u006fw): it could be any column.
BARE_REF_RE = re.compile(rf"\b(?:dataSetRow|row)\b|{CODE_RE.pattern}")
# ... or a member read by a computed name ([][k][k]("Total = …")), which DA2's own lookups use (strength[id]): in a
# layout with any, Total could have a value of its own. Strings are blanked first.
DYNAMIC_RE = re.compile(rf"{CODE_RE.pattern}|(?<=[\w$\])])\s*\[(?![\s\d]*\])")
MEMBER_KEY_RE = re.compile(r"""(?<=[\w$\])@])\s*\[[^\][]*["'`+(]""")  # x["k"], x["a" + "b"], x[f()]: strings keep a quote
# ... or by any computed name on a value that is no name: [][k], f()[k], "s"[k], 1[k] (strings and patterns read as 0)
LITERAL_MEMBER_RE = re.compile(r"(?:[\])}@]|(?<![\w$.])\.?\d[\w.]*)\s*\[(?![\s\d]*\])")
# JavaScript DA2 doesn't write, which reads more than one way: an HTML-style comment, a template string, a backslash
# before a line end (Rhino 1.7R3 runs a regular expression on past it)
UNREAD_RE = re.compile(r"<!--|-->|`|\\[\n\r\u2028\u2029]")
UNREAD = ("A layout expression in this report uses JavaScript that DA2 doesn't write and that can be read more than one "
          "way (an HTML-style comment, a template string, an XML literal, an invisible or non-ASCII character in its "
          "code, a backslash outside a string, a column reference that runs across strings, or a / that could divide "
          "or start a pattern), so I can't check it.")
# What working out an expression's pieces (_lex) or a blanked copy (_blank) costs, in DA2Files.work's units (about
# 0.1 us at 1 CPU), counted each time one is worked out: the checks read each expression in up to eight spellings.
LEX_CALL, LEX_STEP, LEX_WORD, LEX_PIECE, BLANK_CALL, BLANK_PIECE = 20, 4, 9, 3, 8, 2  # and 1 for 10 characters
REF_CALL, REF_FOUND = 10, 15  # finding the column reads in an expression, and following each one (1 for 20 characters)
FILTER_WORK = 50  # a filter, judged on every read and turn; one on a name field this much more, and each of its values
NAME_FILTER, NAME_VALUE = 80, 15
FIELD_CHAR, BINDING_CHAR = 7, 3  # a character of a filter's field names, folded and matched on every read and turn, and
# of a binding's or column hint's names, once a read
NFKC_MOST = 18  # the most characters NFKC spells one out to (U+FDFA)
NAME_CHAR = 8  # a character of a filter value on a name field, split into pieces on every read and once more a turn (up to
# four pieces a character once NFKC has spelled it out: ⒇ is (20))
MAX_SCAN_HELD = 30_000_000  # about the bytes of pieces and blanked copies one read keeps before it starts again
MAX_SCAN_WORK = 5_000_000  # what working them out may cost in one read (0.5 s); a real export's takes under 90,000
BARE_PARAM_RE = re.compile(r"\b(?:params|reportContext)\b")
VALUE_CHAR_RE = re.compile(r"[^\W_zZ]")
# An aggregation: Total.countDistinct(row["Enc Id"], filter). BIRT reads only the bare name, not x.Total or $Total.
TOTAL_CALL_RE = re.compile(r"(?<![\w.$])Total\s*\.\s*\w+\s*$")
TOTAL_RE = re.compile(r"(?<![\w.$])Total\s*\.\s*(\w+)\s*\(")
TOTAL_NAME_RE = re.compile(r"(?<![\w.$])Total(?![\w$])")
# Total named other than at the head of a call, strings included (eval("var Total = …"), {"Total": …}, a sibling Total = …)
TOTAL_SHADOW_RE = re.compile(r"(?<![\w$])Total(?![\w$])(?!\s*\.\s*\w+\s*\()|\.\s+Total\b")
# A name as code uses it, a member (x.name, ...name) too: each use could be one that gives it a value (=, for-in, …)
NAME_USE_RE = re.compile(r"(?<![\w$])[A-Za-z_$][\w$]*")
# A name given a string once (var UUID_COL = "..."), which the code then reads a column by: row[UUID_COL].
KEY_NAME_RE = re.compile(r"\b(?:dataSetRow|row)(?:\s*\.\s*_outer)*\s*\[\s*([A-Za-z_$][\w$]*)\s*\]")
CONST_VAR_RE = re.compile(r"""\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*("[^"\\\n]*"|'[^'\\\n]*')"""
                          r"(?=[ \t]*(?:[;,]|\Z|[\r\n\u2028\u2029]\s*(?!in(?:stanceof)?\b)[A-Za-z_$]))")  # its whole value
# A rule's own test that a parameter is unset or left at a catch-all (params["p"].value == "ALL", as Endexis writes).
_UNSET = r"""(?:"(?:all|any|none|\*|)"|'(?:all|any|none|\*|)'|null)"""
UNSET = frozenset({"all", "any", "none", "*", ""})
PARAM_TEST_RE = re.compile(rf"""(?i)(?:{_PARAM})(?:\s*\.\s*value)?\s*[!=]==?\s*{_UNSET}"""
                           rf"""|{_UNSET}\s*[!=]==?\s*(?:{_PARAM})(?:\s*\.\s*value)?""")
# Code that can move a value about: an assignment or a function. A rule with any gets no exemption at all.
RULE_CODE_RE = re.compile(r"(?<![=!<>])=(?!=)|\bfunction\b")
MAX_RULE_CHARS = 20_000  # DA2's own rules are a few hundred characters
MAX_LAYOUT_EXPR_CHARS = 200_000  # and a layout's, a few thousand in all
BIRTH_FIELD_RE = re.compile(r"(?i)\bbirth (?:dt|date)|\bdob\b")
COMPUTED = "a column read by a computed name"
# A test of a value: an expression outside a filter, highlight or map rule with none of these only shows or works one
# out ("FIN: " + row["Enc Financial Num"], Formatter.format(row["Person Birth Dt Tm"], "MM/dd/yyyy")).
# A test outside a rule list; arithmetic is _arith_on's
COMPARE_RE = re.compile(r"[=!]=|[<>]|\b(?:case|in)\b|\bBirtComp\s*\.\s*\w+\s*\(|\.\s*(?:match|test|search"
                        r"|exec|indexOf|lastIndexOf|includes|startsWith|endsWith|equals\w*|localeCompare|compareTo"
                        r"|contains|matches)\s*\(")
# Arithmetic on a patient field is a test too (pid - 12345678 is 0 for one patient), but not on its length
# (FIN.substring(FIN.length - 4)) or in a negative argument (slice(-10)).
ARITH_RE = re.compile(r"[-%^/]")
NEGATIVE_RE = re.compile(r"[-+*/%^]\s*-")  # pid + -12345678
# A call whose first argument is a pattern (name.replace("DOE, JANE", "x")): that argument and what it is called on
# ("DOE, JANE".split(name)) are a test, its other arguments are not.
PATTERN_CALL_RE = re.compile(r"\.\s*(?:replace|replaceAll|split)\s*\(")
# What in a regular expression is no character of a value: a class escape (\d, \s). A class ([1]) or a count ({8})
# can spell one, so it counts.
REGEX_SYNTAX_RE = re.compile(r"\\[dDwWsSbB]")
LENGTH_RE = re.compile(r"\s*\.\s*length\b(?!\s*\()")  # a field's length is no value of it
# Aggregations that count or rank what they read rather than pass one of its values on. A sum, an average or an NPV
# of one row (Total.movingAve(row["Person Id"], 1)) is its value, so those read it.
COUNTING = frozenset({"count", "countdistinct", "runningcount", "rank", "percentrank", "istopn", "isbottomn",
                      "istopnpercent", "isbottomnpercent"})
AGE_CALL_RE = re.compile(r"(?<![\w.$])BirtDateTime\s*\.\s*diff(?:Year|Month)\s*$")  # an age, worked out from a birth date


class DA2FileError(ValueError):
    """A file or change that was refused, with the reason for the user. `work` is what a refused read had counted (in
    DA2Files.work's units) when it stopped, so a caller can charge it."""
    work = 0


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


def _fold(name: str) -> str:
    return unicodedata.normalize("NFKC", name).translate(LOOKALIKES)


def _pieces(text: str):
    """Each piece of a name in one value text, in order: (the piece, whether no wildcard comes before it, whether none
    comes after it). A piece after _______ is no start: the wildcards stand for another piece. A run of Zs is no
    piece: it is DA2's test-patient marker."""
    text = unicodedata.normalize("NFKC", text)
    found = [i for i in (text.find("%"), text.find("_")) if i >= 0]
    first, last = (min(found), max(text.rfind("%"), text.rfind("_"))) if found else (-1, -1)
    for run in NAME_RUN_RE.finditer(text):
        if NAME_LETTER_RE.search(run[0]):
            yield run[0].casefold(), first < 0 or first > run.start(), last < run.end()


def _spells_two(value: tuple[str, ...]) -> bool:
    """Whether one filter value (its texts) spells two pieces of a name, a whole name: "DOE%JANE%", "D_E%J%", the
    same word twice ("LEE__LEE%" matches "LEE, LEE"), or one piece in each of its texts."""
    seen: set[str] = set()
    for text in value:
        for i, (piece, _, _) in enumerate(_pieces(text)):
            seen.add(piece)
            if i or len(seen) > 1:
                return True
    return False


def _identifies(fields: list[str], operator: str, values: list[tuple[str, ...]]) -> bool:
    """Whether a filter on a field with these names, with these values (each value's texts), would pick out a patient."""
    texts = [t for v in values for t in v if t.strip()]
    if not texts:
        return False
    if any(PATIENT_ID_FIELD_RE.search(f) for f in fields):
        return True
    if not any(PATIENT_NAME_FIELD_RE.search(f) for f in fields):  # a field's name decides first: values can be long
        return False
    like = operator in ("LIKE", "NOT LIKE")
    if not like and operator not in RANGE_OPS:
        return True  # a name, or a list of them
    return any("," in t or (like and "%" not in t) for t in texts) or any(map(_spells_two, values))


def _name_slots(fields: list[str], operator: str, values: list[tuple[str, ...]]) -> set[str]:
    """What a kept name filter tells of a name. On a field holding one part, that part ("last name"). On a whole-name
    field, its start (a range bound, or a pattern fixed at the start: LAST, FIRST sorts by the last name), its end, or a
    piece found anywhere in it. Two across a file's filters are a whole name. An exclusion (NOT LIKE) tells none."""
    fields = [f for f in fields if PATIENT_NAME_FIELD_RE.search(f)]
    pieces = [p for v in values for t in v for p in _pieces(t)] if fields and operator != "NOT LIKE" else []
    if not pieces:
        return set()
    slots = {{"la": "last"}.get(k := m[0].casefold(), k) + " name" for f in fields if (m := NAME_PART_FIELD_RE.search(f))}
    if any(not NAME_PART_FIELD_RE.search(f) for f in fields):  # a piece has no space, so it can't be a slot's name
        slots |= {"a full name's start" if operator != "LIKE" or start else "a full name's end" if end else piece
                  for piece, start, end in pieces}
    return slots

def _value_texts(node: ET.Element) -> list[tuple[str, ...]]:
    """The texts of each of a filter's values, without those of filters nested in it, which are judged on their own:
    gathering every descendant once per enclosing filter is quadratic in a deep nest."""
    out, stack = [], list(node)
    while stack:
        n = stack.pop()
        if str(n.tag).startswith("filter") and n.find("element") is not None:
            continue
        if n.tag == "value":
            out.append((*(v for _, v in n.items()), n.text or ""))
        stack.extend(n)
    return out


# What may come before an embedded text's first <: Java's trim() drops every character up to a space, and its parser a
# byte order mark (and Python's strip() more, which reads a text DA2 may not as XML, the safe way round)
XML_LEAD_RE = re.compile(r"[\x00-\x20\s\ufeff]*+(?=<)")


def _embedded(text: str, member: str, parses: int = MAX_PROMPT_PARSES) -> list[ET.Element]:
    """The XML in a layout's runtime-prompt defaults, read as safely as a member. DA2 escapes each prompt's
    qualification inside the prompt list, so an element whose text is XML is read too, two levels down at most.
    `parses` is how many texts the file may still have parsed."""
    label, trees, level = f"{member}'s runtime-prompt defaults", [], [text.strip()] if text.strip() else []
    for _ in range(3):
        if len(trees) + len(level) > parses:
            raise DA2FileError(f"{shown(label)} hold more prompts than DA2 writes (over {MAX_PROMPT_PARSES} a file).")
        level = [_parse(t.rstrip().encode("utf-8"), label) for t in level]
        trees += level
        level = [e.text[m.end():] for root in level for e in root.iter() if (m := XML_LEAD_RE.match(e.text or ""))]
        if not level:
            return trees
    raise DA2FileError(f"{shown(label)} are nested deeper than DA2 writes them.")


def _refs(text: str) -> list[re.Match]:
    """Where a layout expression reads a column or binding: row["Person Id"], dataSetRow['X'], row.X. Counted with the
    scan (_Scan): the checks find them in each expression several times over and follow each one they find."""
    found = [*LAYOUT_REF_RE.finditer(text), *LAYOUT_DOT_REF_RE.finditer(text)]
    _SCAN.charge(REF_CALL + len(text) // 20 + REF_FOUND * len(found))
    return found


def _ref_name(m: re.Match) -> str:
    return next(g for g in m.groups() if g is not None)


def _value_chars(text: str) -> int:
    """How many of a literal's characters could be part of a patient's value, counted up to 2: a blank, one character
    or a run of Zs (DA2's test-patient marker) can't hold one."""
    return len(list(itertools.islice(VALUE_CHAR_RE.finditer(text), 2)))


def _holds_value(lit: str) -> bool:
    return _value_chars(lit) > 1


class _Scan(threading.local):
    """One read's expressions as _lex and _blank worked them out, per thread (the chat reads up to three files at
    once), and the work that took (DA2Files.work's units). The checks read each expression in up to eight spellings, so
    work is counted where it is done, not per expression. Past MAX_SCAN_HELD the caches start again, and refilling
    them is counted too."""

    def __init__(self) -> None:
        self.lex: dict = {}
        self.blank: dict = {}
        self.work = self.held = 0
        self.limit = float("inf")  # the work at which a read stops (MAX_SCAN_WORK)

    def charge(self, work: int) -> None:
        if (w := self.work + work) > self.limit:
            raise DA2FileError("The layout's expressions take far longer to check than DA2's own, so I can't check "
                               "them.")
        self.work = w

    def keep(self, cache: dict, key, value, work: int, size: int):
        self.charge(work)
        self.held += size
        if self.held > MAX_SCAN_HELD:
            self.clear()
            self.held = size
        cache[key] = value
        return value

    def clear(self) -> None:
        self.lex.clear()
        self.blank.clear()
        self.held = 0


_SCAN = _Scan()


def _lex(text: str) -> tuple[tuple[str, int, int, str], ...]:
    """The comments ("c"), strings ("s") and regular expressions ("r") in a layout expression, each as (kind, start,
    end, what it holds). One walk: each piece is read from its start to its end once, and the code between them once,
    so no text costs more than its length. A / that JavaScript reads as a division or a regular expression by what the
    walk can't see (after a }, a ++ or --, a yield, await or of, an if, while, for, with or let, the ) of their (, or
    any ) in a text with a function, get or set in it, or after an object literal's {) is marked
    "?" at it, read as a regular expression, and so is a < where an operand starts (an XML literal, in Rhino); _code
    refuses an expression with one."""
    if (got := _SCAN.lex.get(text)) is not None:
        return got
    out, i, n, plain, steps, words = [], 0, len(text), -1, 0, 0
    prev, at, heads, head_close = "start", -1, [], -1  # prev: "code" (its last character at `at`), "operand" or "start"
    closure = bool(_CLOSURE_RE.search(text))
    closed: dict[int, str] = {}  # a )'s place -> the word before its (
    notes: dict[int, int] = {}  # a comment's end -> its start

    def word(j: int) -> str:  # the name that ends at j, unless it is a member (x.if, x./**/if); a long one is no keyword.
        # "?" for a keyword after a number's dot (0. in): a member or the keyword, by how the number is read
        nonlocal words
        if not (text[j].isascii() and (text[j].isalnum() or text[j] in "_$")):
            return ""
        words += 1
        w = _WORD_END_RE.search(text, max(0, j - 30), j + 1)
        k = w.start() - 1 if w else -1
        while k >= 0 and (k + 1 in notes or text[k].isspace()):
            k = notes[k + 1] - 1 if k + 1 in notes else k - 1
        if w and k >= 0 and text[k] == ".":
            return "?" if k and text[k - 1].isdigit() and w[0] in _KEYWORDS else ""
        return w[0] if w else ""

    def opens(pv: str, j: int, k: int) -> tuple[bool, bool]:  # whether an operand starts at k after the code at j,
        # and if unsure. After a name and a line end (var x, break, debugger), JavaScript may end the statement there.
        if pv != "code":
            return pv == "start", False
        c, w = text[j], word(j)
        unsure = c == "}" or text[j - 1:j + 1] in ("++", "--") or c == ")" and (j == head_close or closure) \
            or w in _UNSURE_AFTER or w in _HEAD_WORDS or w == "?" or (c.isalnum() or c in "_$") and w not in _REGEX_AFTER and bool(_LINE_END_RE.search(text, j, k))
        return unsure or not _OPERAND_END.match(c) or w in _REGEX_AFTER, unsure

    def block(pv: str, j: int) -> bool:  # whether a { after the code at j opens a block, not an object literal
        if pv != "code":
            return pv == "start"
        c = text[j]
        return c in "{}" or c == ";" and not heads or text[j - 1:j + 1] == "=>" or word(j) in _BLOCK_AFTER \
            or c == ")" and closed.get(j) in _BLOCK_HEADS

    def walk(a: int, b: int) -> None:  # the code from a to b: its brackets, its <s and its last character
        nonlocal prev, at, head_close, steps, closure
        for m in _WALK_RE.finditer(text, a, b):
            steps += 1
            j = m.start() - 1
            while j >= a and text[j].isspace():  # each run of spaces is passed once: one bracket or < follows it
                j -= 1
            pv, j = ("code", j) if j >= a else (prev, at)
            if m[0] == "(":
                w = word(j) if pv == "code" else ""
                if w == "each":  # Rhino's for each (…) {; a constructor named each takes an object: new each(1) {…}
                    k = j - 4
                    while k >= 0 and (k + 1 in notes or text[k].isspace()):
                        k = notes[k + 1] - 1 if k + 1 in notes else k - 1
                    w = {"for": w, "?": "?"}.get(word(k) if k >= 0 else "", "")  # "?": 0.\nfor each (…) may be one
                heads.append(w)
            elif m[0] == ")":
                closed[m.start()] = w = heads.pop() if heads else ""
                if w in _HEAD_WORDS or w == "?":  # 0.\nif (…): a keyword after a number's dot may be one
                    head_close = m.start()
            elif m[0] == "{":
                closure = closure or not block(pv, j)
            elif opens(pv, j, m.start())[0]:
                out.append(("?", m.start(), m.start(), ""))
        if (last := a + len(text[a:b].rstrip()) - 1) >= a:
            prev, at = "code", last

    while m := _LEX_START.search(text, i):
        steps += 1
        s, t = m.start(), m[0]
        walk(i, s)
        if t in ("//", "/*"):
            e = (e.start() if (e := _LINE_END_RE.search(text, s + 2)) else -1) if t == "//" else text.find("*/", s + 2)
            out.append(("c", s, n if e < 0 else e + 2 * (t == "/*"), ""))
            notes[out[-1][2]] = s
        elif t != "/":
            q = _QUOTED[t].match(text, s)
            out.append(("s", s, q.end(), q[1]))
            prev = "operand"
        else:
            starts, unsure = opens(prev, at, s)
            if unsure:
                out.append(("?", s, s, ""))
            if not (r := (starts := s > plain and starts) and _REGEX.match(text, s)):
                if starts:  # unended on its line: the line's other slashes divide, so none is read twice
                    plain = n if (e := _LINE_END_RE.search(text, s)) is None else e.start()
                i = s + 1
                prev, at = "code", s
                continue
            out.append(("r", s, r.end(), r[1]))
            prev = "operand"
        i = out[-1][2]
    walk(i, n)
    pieces = tuple(out)
    return _SCAN.keep(_SCAN.lex, text, pieces, LEX_CALL + n // 10 + LEX_STEP * steps + LEX_WORD * words
                      + LEX_PIECE * len(pieces),
                      2 * n + 200 * len(pieces))


def _blank(text: str, kinds: str = "csr", keep: str = "`", fill: str = " ") -> str:
    """The text with its pieces of these kinds (comments, strings, regular expressions) blanked, its length kept. A
    string opened by one of keep keeps that quote: a template's ${} runs code, which CODE_RE then sees."""
    if (got := _SCAN.blank.get(key := (text, kinds, keep, fill))) is not None:
        return got
    parts, last, pieces = [], 0, _lex(text)
    for kind, s, e, _ in pieces:
        if kind in kinds:
            parts += [text[last:s], (text[s] if kind == "s" and text[s] in keep else fill) + fill * (e - s - 1)]
            last = e
    return _SCAN.keep(_SCAN.blank, key, "".join(parts) + text[last:], BLANK_CALL + len(text) // 10
                      + BLANK_PIECE * len(pieces), 2 * len(text))


def _straddles(text: str) -> bool:
    """Whether a column or parameter reference starts or ends inside a string or pattern (['row[', 1, ']'][1]): the
    checks would take the strings' quotes for the reference's."""
    pieces = [(s, e) for kind, s, e, _ in _lex(text) if kind in "sr"]
    starts = [s for s, _ in pieces]

    def inside(x: int) -> bool:
        k = bisect.bisect_right(starts, x) - 1
        return k >= 0 and pieces[k][0] < x < pieces[k][1]
    return any(inside(m.start()) or inside(m.end()) for rx in (LAYOUT_ANY_REF_RE, LAYOUT_REF_RE, LAYOUT_DOT_REF_RE,
                                                               PARAM_REF_RE) for m in rx.finditer(text))


def _code(text: str, total: list[int]) -> str:
    """An expression's code: its comments blanked, and a column read by a name the code gives a string once (var
    COL = "X"; row[COL]) written as a read of that column, row["X"]. What that adds is charged to total (_too_long).
    JavaScript DA2 doesn't write and that reads more than one way is refused (UNREAD)."""
    if UNREAD_RE.search(text) or "\x7f" in text or not text.isascii() and any(unicodedata.category(c) == "Cf" for c in text) \
            or any(kind == "?" for kind, *_ in _lex(text)) or not (masked := _blank(text)).isascii() or "\\" in masked \
            or "..." in masked or GLUED_RE.search(masked) or _straddles(text := _blank(text, "c")):  # ... spreads
        raise DA2FileError(UNREAD)
    masked = _blank(text)
    keyed = list(KEY_NAME_RE.finditer(masked))  # row[NAME], outside strings
    names = {r[1] for r in keyed}
    given = {}
    for m in CONST_VAR_RE.finditer(text):
        if m[1] in names and masked[m.start():m.start(2)] == text[m.start():m.start(2)]:
            given.setdefault(m[1], m[2])
    if given:  # named only where it is declared and where a column is read by it: no other value given anywhere
        uses, reads = collections.Counter(NAME_USE_RE.findall(masked)), collections.Counter(r[1] for r in keyed)
        given = {n: v for n, v in list(given.items())[:20]  # shortcut: 20 names, DA2's scripts use one or two
                 if uses[n] == reads[n] + 1}
    if (grown := sum(len(given[r[1]]) - len(r[1]) for r in keyed if r[1] in given)) > len(text):
        return text  # a long value read many times: left a computed read, so any column
    _too_long(max(grown, 0), total)  # a long name given a short value takes nothing off
    parts, last = [], 0
    for r in keyed:
        if r[1] in given:
            parts += [text[last:r.start(1)], given[r[1]]]
            last = r.end(1)
    return "".join(parts) + text[last:]


def _unrefed(text: str) -> str:
    """The text with its column and parameter references blanked."""
    return LAYOUT_ANY_REF_RE.sub(lambda m: "@" * len(m[0]), text)


def _bare(text: str) -> bool:
    """Whether code could read a column other than by a reference: the row object or code named some other way
    (BARE_REF_RE), or a member read by a key built from a string, a sum or a call ([]["con" + "structor"]), or by any
    computed key on a value that is no name ([][k]), which DA2's own lookups (strength[id]) never are."""
    text = _unrefed(text)
    return bool(BARE_REF_RE.search(_blank(text)) or "[" in text and (MEMBER_KEY_RE.search(_blank(text, keep="\"'`"))
                                                                     or LITERAL_MEMBER_RE.search(_blank(text, "sr", "", "0"))))


def _literal_spans(text: str) -> list[tuple[int, str]]:
    """The literals in a layout expression, its column and parameter references taken out, with where each starts: a
    string, a regular expression or a number."""
    bare = _unrefed(text)
    return sorted([(s, REGEX_SYNTAX_RE.sub("", inner) if kind == "r" else inner) for kind, s, _, inner in _lex(bare)
                   if kind in "sr"]
                  + [(m.start(), m[0]) for m in NUMBER_RE.finditer(_blank(bare))])


def _literals(text: str) -> list[str]:
    return [lit for _, lit in _literal_spans(text)]


def _arith_on(text: str, masked: str, spans: list[tuple[int, int]]) -> bool:
    """Whether arithmetic in a text (masked: its strings blanked) works on one of these spans, a patient field's
    reads: beside an operator, or in brackets beside one. One walk."""
    if not spans or not ARITH_RE.search(masked):
        return False
    if NEGATIVE_RE.search(masked):
        return True
    starts, begins, ends = sorted(a for a, _ in spans), {a for a, _ in spans}, {b for _, b in spans}
    partner, opened = {}, []
    for m in re.finditer(r"[(\[{]|[)\]}]", masked):
        if m[0] in "([{":
            opened.append(m.start())
        elif opened:
            o = opened.pop()
            partner[o], partner[m.start()] = m.start(), o

    def holds(a: int, b: int) -> bool:  # a field read between these
        i = bisect.bisect_right(starts, a)
        return i < len(starts) and starts[i] < b
    for m in ARITH_RE.finditer(masked):
        j, k = m.start() - 1, m.end()
        while j >= 0 and text[j].isspace():
            j -= 1
        while k < len(text) and text[k].isspace():
            k += 1
        if j + 1 in ends or k in begins or j in partner and masked[j] in ")]}" and holds(partner[j], j) \
                or k in partner and masked[k] in "([{" and holds(k, partner[k]):
            return True
    return False


def _pattern_args(masked: str) -> list[tuple[int, int]]:
    """Where each pattern call (replace, split) reads a value, in a text with its strings blanked: what it is called on
    and its first argument. One walk; pieces that overlap are merged."""
    spans, opened, seg = [], [], [0]  # opened: for each open bracket, where its pattern argument starts (or -1); seg:
    calls = {m.end() - 1: m.start() for m in PATTERN_CALL_RE.finditer(masked)}  # where each depth's operand starts
    for m in re.finditer(r"[(\[{]|[)\]}]|,|[-+*/%^&|~!=<>?:;]", masked):
        if m[0] in "([{":
            if (dot := calls.get(m.start())) is not None:
                spans.append((seg[-1], dot))  # what it is called on
            opened.append(m.end() if dot is not None else -1)
            seg.append(m.end())
        elif m[0] in ")]},":
            if opened and (a := opened[-1]) >= 0:  # its pattern argument ends here
                spans.append((a, m.start()))
                opened[-1] = -1
            if m[0] == ",":
                seg[-1] = m.end()
            elif opened:
                opened.pop()
                seg.pop()
        else:
            seg[-1] = m.end()
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _uncounted(text: str) -> str:
    """The text with each counting aggregation in it (Total.countDistinct(row["Enc Id"], row["Age"] >= 65)) read as
    null: a count of patients holds none of their values. Text that names Total other than in a call, or that assigns
    or defines a function, could give Total a value of its own ({Total: {count: String}}), so it is left as it is, and
    so is text with a regular expression, which could hide where the call ends. One walk."""
    masked = _blank(text)
    found = list(TOTAL_RE.finditer(masked))
    if RULE_CODE_RE.search(masked) or len(found) != len(TOTAL_NAME_RE.findall(masked)) or \
            any(kind == "r" for kind, *_ in _lex(text)):  # a / read as a regular expression could hide the call's end
        return text
    calls = {m.end() - 1: m.start() for m in found if m[1].casefold() in COUNTING}
    parts, last, opened, inside = [], 0, [], -1  # inside: the depth of the outermost counting call open, or -1
    for m in re.finditer(r"[(\[{]|[)\]}]", masked) if calls else ():
        if m[0] in "([{":
            if inside < 0 and m.start() in calls:
                inside, begin = len(opened), calls[m.start()]
                parts.append(text[last:begin])
                last = begin
            opened.append(m.start())
        elif opened:
            opened.pop()
            if inside == len(opened):
                parts.append("null")
                last, inside = m.end(), -1
    return "".join(parts) + text[last:]


def _clauses(text: str, ternary: bool = False) -> list[tuple[str, bool]]:
    """A rule's clauses: its text split at each && and || (and, if asked, ? and :) outside brackets and strings, and
    in an aggregation's arguments (Total.count(row["Enc Id"], row["Age"] >= 65) counts by one column and filters on
    another). Each comes with whether it is a condition: whether the &&, || or ? after it tests its truth."""
    masked = _blank(text)
    out, opened, last = [], [], 0  # opened: for each open bracket, whether it holds an aggregation's arguments
    for m in re.finditer(r"[(\[{]|[)\]}]|&&|\|\||,|\?\??|:" if ternary else r"[(\[{]|[)\]}]|&&|\|\||,", masked):
        if m[0] in "([{":
            opened.append(m[0] == "(" and bool(TOTAL_CALL_RE.search(masked[max(0, m.start() - 40):m.start()])))
        elif m[0] in ")]}":
            if opened:
                opened.pop()
        elif (not opened or opened[-1]) and (m[0] != "," or opened):
            out.append((text[last:m.start()], m[0] in ("&&", "||", "?")))
            last = m.end()
    return out + [(text[last:], False)]


def _ages(text: str, refs: list[re.Match]) -> set[int]:
    """Where a reference is an argument of an age call with no literal in it, BirtDateTime.diffYear(row["Person Birth
    Dt Tm"], BirtDateTime.today()): one walk over the parentheses, with the literals found once."""
    masked = _blank(text)
    lits = [at for at, _ in _literal_spans(text)]
    events = sorted([(m.start(), 1) for m in refs] + [(p.start(), 0) for p in re.finditer(r"[()]", masked)])
    stack, close, inner = [], {}, []
    for at, kind in events:
        if kind:
            inner.append((at, stack[-1] if stack else None))
        elif masked[at] == "(":
            stack.append(at)
        elif stack:
            close[stack.pop()] = at
    out = set()
    for at, opened in inner:
        if opened is not None and AGE_CALL_RE.search(text[max(0, opened - 40):opened]):
            i = bisect.bisect_right(lits, opened)
            if i == len(lits) or lits[i] >= close.get(opened, len(text)):
                out.add(at)
    return out


def _derived(expr: str, refs: list[re.Match], column=lambda name: True) -> set[int]:
    """Where a binding reads a column only to work out an age from it: in an age call, or as one side of a
    subtraction of two data set columns ((row["Enc Discharge Dt/Tm"] - row["Person Birth Dt Tm"]) / ...). A binding or
    computed column of its own can hold a constant, so a subtraction with one (column(name) is False) is no age."""
    out, refs = _ages(expr, refs), sorted(refs, key=lambda m: m.start())
    for a, b in zip(refs, refs[1:]):
        if expr[a.end():b.start()].strip(" ()") == "-" and column(_ref_name(a)) and column(_ref_name(b)):
            out |= {a.start(), b.start()}
    return out


def _too_long(size: int, total: list[int]) -> None:
    """Refuses an expression, or a layout's expressions in all, far longer than DA2 writes: reading them costs time."""
    total[0] += size
    _SCAN.work += 5 * size  # counted where it is read: a refusal later in the layout is charged it (DA2FileError.work)
    if size > MAX_RULE_CHARS:
        raise DA2FileError(f"A layout expression in this report is {size:,} characters long; DA2's are a few hundred, "
                           "so I can't check it.")
    if total[0] > MAX_LAYOUT_EXPR_CHARS:
        raise DA2FileError(f"The layout's expressions run to more than {MAX_LAYOUT_EXPR_CHARS:,} characters; DA2's run "
                           "to a few thousand, so I can't check them.")


def _rule_fields(root: ET.Element) -> tuple[list[str], int]:
    """Patient fields a layout expression compares with a literal value (a table filter row["Person Id"] eq "123", or
    String(row["Person Id"]) == "123"), directly or through a binding, computed column or data-set alias that reads
    them. Each filter, highlight or map rule is judged with its values, and every other expression on its own: a
    binding's, a computed column's, a visibility rule's, an aggregation's filter. The row object read some other way
    (row[3], (row)["X"]) could be any column, so it counts as one. A literal counts when it is in the field's clause
    (split at && and || outside brackets), or in a clause with no column or parameter of its own; one-character pieces
    count together ("1" + "2"). Outside a rule list a clause (split at ? and : too) counts only if it tests something:
    a comparison (COMPARE_RE), arithmetic on the field, a pattern call's pattern and what it is called on, a condition
    (before &&, || or ?), or a visibility rule or aggregation filter, which are tested for their truth. "FIN: " +
    row["Enc Financial Num"] only shows the field, and a field's length is no value of it. A binding reads its
    expression or an aggregation's arguments (a count of patients reads none of their values), and holds its literals
    for whatever tests it. A report parameter's defaults count as
    literals unless they are a catch-all (ALL), and a test that a parameter is unset or a catch-all
    (params["p"].value == "ALL", as Endexis writes) is no literal. An age worked out from the birth date is kept. An
    expression that assigns or defines a function gets none of these exemptions: any literal in it counts against any
    patient field. Comments are not read. Returns the fields and the work done (DA2Files.work's units: 5 for each
    character of expression, 20 for each rule, 30 for each clause or rule value judged, 60 for each binding or
    computed column, and what working out their pieces and finding their column reads took: _Scan)."""
    rules, seen = [], set()  # each rule's elements; a rule nested in another is part of it, so each is read once
    for lst in root.iter(f"{B}list-property"):
        if lst.get("name") in RULE_LISTS:
            for s in lst.findall(f"{B}structure"):
                if s not in seen:
                    rules.append(list(s.iter()))
                    seen.update(rules[-1])
    listed = len(rules)  # the rules before this are a list's, which test a column against values
    for e in root.iter(f"{B}expression"):  # an expression inside another (DA2 writes none) is part of it
        if e not in seen:
            rules.append(list(e.iter()))
            seen.update(rules[-1])
    total, code, parts, nvals, scanned = [0], {}, [], 0, _SCAN.work  # code: each expression's text, comments blanked
    for rule in rules:
        vals, stack = [], [rule[0]]  # each value once: a value1 inside another walks no value twice
        while stack:
            if (e := stack.pop()).get("name") in ("value1", "value2"):
                vals += e.iter(f"{B}value")
            else:
                stack += reversed(e)
        nvals += len(vals)
        exprs = [e for e in rule if e.tag == f"{B}expression"]  # not its style, nor a map rule's display
        _too_long(sum(len(e.text or "") for e in exprs) + sum(len(v.text or "") for v in vals), total)
        code.update((e, _code(e.text or "", total)) for e in exprs + [v for v in vals if v.get("type") != "constant"])
        parts.append((exprs, vals))
    sets = [s for lst in root.iter(f"{B}list-property") if lst.get("name") in ("boundDataColumns", "computedColumns")
            for s in lst.findall(f"{B}structure")]
    made_exprs, through, const, given = set(), {}, set(), []  # bindings that pass one column on, and the others
    read = []  # each binding or computed column and what it reads: its expression, or an aggregation's arguments
    for s in sets:
        els = [e for e in [_prop(s, "expression")] + [e for a in s.findall(f"{B}list-property")
               if a.get("name") == "arguments" for arg in a.findall(f"{B}structure")
               for e in arg.findall(f"{B}expression")] if e is not None]
        for el in els:
            if el not in code:
                _too_long(len(el.text or ""), total)
                code[el] = '"' + re.sub(r'["\\\r\n]', " ", el.text or "") + '"' if el.get("type") == "constant" \
                    else _code(el.text or "", total)  # a constant's text, as a string
        made_exprs.update(els)
        read.append((s, els))
    # Code anywhere in the layout that could give Total a value of its own (Total = …, eval, this[…]) ends the
    # count exemption for every expression in it. A script that does is a known gap, as a script is for any value.
    counted = not any(TOTAL_SHADOW_RE.search(c) or DYNAMIC_RE.search(_blank(c)) for c in code.values()) and \
        not any(TOTAL_SHADOW_RE.search(m.text or "") for m in root.iter(f"{B}method"))  # unlexed: scripts aren't charged
    for s, els in read:
        name, expr = _text(_prop(s, "name")), " ;\n".join(code[e] for e in els)
        if _text(_prop(s, "aggregateFunction")).casefold() in COUNTING:
            expr = ""  # a count of patients holds none of their values
        expr = _uncounted(expr) if counted else expr
        given.append((name, expr))
        refs = _refs(expr)
        if len(refs) == 1 and not _unrefed(expr).strip("@() \t\r\n"):
            through[name] = _ref_name(refs[0])
        else:
            const.add(name)
    ends: dict[str, str] = {}  # name -> where a chain of pass-on bindings ends ("" for a loop), each walked once

    def end(name: str) -> str:
        path, on = [], set()
        while through.get(name, name) != name and name not in ends and name not in on:  # named for its column: an end
            path.append(name)
            on.add(name)
            name = through[name]
        last = ends.get(name, "" if name in on else name)
        ends.update(dict.fromkeys(path, last))
        return last

    def column(name: str) -> bool:  # whether a name reads a data set column, through bindings that pass one on
        return (last := end(name)) != "" and last not in const
    reads: dict[str, list[tuple[str, bool]]] = {}  # binding, computed column or alias -> (a column it reads, derived)
    for name, expr in given:
        derived = _derived(expr, refs := _refs(expr), column)
        reads.setdefault(name, []).extend(
            [(_ref_name(m), m.start() in derived) for m in refs]
            + [(COMPUTED, False)] * bool(LAYOUT_COMPUTED_REF_RE.search(expr) or _bare(expr)))
    for lst in root.iter(f"{B}list-property"):
        if lst.get("name") in ("columnHints", "resultSet"):  # a data set's own name for a DA2 column
            kind = lst.get("name")
            for s in lst.findall(f"{B}structure"):
                new, old = (_text(_prop(s, k)) for k in (("alias", "columnName") if kind == "columnHints"
                                                         else ("name", "nativeName")))
                if new and old and new != old:
                    reads.setdefault(new, []).append((old, False))
    readers: dict[str, list[tuple[str, bool]]] = {}
    for name, refs in reads.items():
        for ref, derived in refs:
            readers.setdefault(ref, []).append((name, derived))
    names = set(reads) | set(readers) | {_ref_name(m) for c in code.values() for m in _refs(c)}  # what is judged
    _SCAN.charge(BINDING_CHAR * sum(len(n) * (1 if n.isascii() else NFKC_MOST) for n in names))  # charged before folding
    todo = [(n, hit) for n in names for hit in (_fold(n), _fold(_uuid_names().get(n.lower(), "")))
            if hit and (PATIENT_ID_FIELD_RE.search(hit) or PATIENT_NAME_FIELD_RE.search(hit))]
    todo += [(COMPUTED, COMPUTED)] * (COMPUTED in readers)
    patient: dict[str, str] = {}  # column or binding -> the patient field it reads
    while todo:  # one pass back along the reads, so a chain of bindings costs no more than its length
        name, hit = todo.pop()
        if name not in patient:
            patient[name] = hit
            todo += [(r, hit) for r, derived in readers.get(name, ()) if not (derived and BIRTH_FIELD_RE.search(hit))]
    # How many characters of each parameter's defaults could hold a value, counted once (up to 2).
    defaults = {p.get("name", ""): _value_chars("".join(
        t for lst in p.findall(f"{B}simple-property-list") if lst.get("name") == "defaultValue" for v in lst
        if (t := (v.text or "").strip()).casefold() not in UNSET)) for p in root.iter(f"{B}scalar-parameter")}

    all_defaults = sum(defaults.values())

    def lit_count(text: str) -> int:  # value characters in its literals and the defaults of the parameters it reads
        params = {_ref_name(m) for m in PARAM_REF_RE.finditer(text)}
        return _value_chars("".join(_literals(text))) + (all_defaults if BARE_PARAM_RE.search(_blank(_unrefed(text)))
                                                         else sum(defaults.get(p, 0) for p in params))
    # How many characters of a value (up to 2) a binding or computed column holds for whatever tests it, with what the
    # ones it reads hold (String(row["tgt"])): all its literals, a label joined to a column too ("FIN: " + row["X"]
    # holds a value: a test can split it back out). Each definition of a name is judged on its own. Linear: a name's count grows at most twice, and
    # each growth is passed once to each definition that reads it.
    computed = {_text(_prop(s, "name")) for lst in root.iter(f"{B}list-property") if lst.get("name") == "computedColumns"
                for s in lst.findall(f"{B}structure")}
    defs = []
    for n, e in given:
        refs = set()
        for m in _refs(e):  # dataSetRow["X"], or a binding's read of its own name, is the data set's column X
            r = _ref_name(m)
            refs.add(r if r in computed or not (m[0].startswith("dataSetRow") or r == n) else COMPUTED)
        if LAYOUT_COMPUTED_REF_RE.search(e) or _bare(e):
            refs.add(COMPUTED)
        defs.append((n, refs, e))
    named = {n for n, _, _ in defs}
    by_ref: dict[str, list[int]] = {}  # name -> the definitions that read it
    left: dict[str, int] = {}  # name -> its definitions not yet known to read a column
    for i, (n, refs, _) in enumerate(defs):
        left[n] = left.get(n, 0) + 1
        for r in refs:
            by_ref.setdefault(r, []).append(i)
    dead = [False] * len(defs)  # reads a data set column (or row[3]), directly or through names that only do
    todo = [i for i, (_, refs, _) in enumerate(defs) if not refs <= named]
    while todo:
        if not dead[i := todo.pop()]:
            dead[i] = True
            left[n := defs[i][0]] -= 1
            if not left[n]:
                todo += by_ref.get(n, ())
    held: dict[str, int] = {}
    acc = [lit_count(e) for _, _, e in defs]
    todo = list(range(len(defs)))
    while todo:
        i = todo.pop()
        n = defs[i][0]
        if (v := min(2, acc[i])) > (was := held.get(n, 0)):
            held[n] = v
            for j in by_ref.get(n, ()):
                acc[j] += v - was
                todo.append(j)

    def value_count(text: str) -> int:  # lit_count, and what the bindings it reads hold
        return lit_count(text) + sum(held.get(_ref_name(m), 0) for m in _refs(text))
    tests = {e for lst in root.iter(f"{B}list-property") if lst.get("name") == "visibility"  # tested for their truth
             for e in lst.iter(f"{B}expression")} | {e for e in root.iter(f"{B}expression") if e.get("name") == "filterExpr"}
    out: list[str] = []
    clauses = [0]
    for at, (exprs, vals) in enumerate(parts):
        texts = [code[e] for e in exprs]
        is_code = any(RULE_CODE_RE.search(_blank(t)) for t in texts + [code[v] for v in vals if v in code])
        derive = any(e in made_exprs for e in exprs)  # a binding's own expression: an age at discharge is kept
        tested = any(e in tests for e in exprs)

        def judged(text: str, cond: bool) -> tuple[list[str], bool]:  # (its patient fields, whether its literals could
            # hold a value); cond: whether its truth is tested
            clauses[0] += 1
            refs = _refs(text)
            births = {m.start() for m in refs if BIRTH_FIELD_RE.search(patient.get(_ref_name(m), ""))}
            ages = births & (_derived(text, refs, column) if derive else _ages(text, refs)) if births and not is_code else set()
            hit = [m for m in refs if patient.get(_ref_name(m)) and m.start() not in ages
                   and not LENGTH_RE.match(text, m.end())]
            hits = [patient[_ref_name(m)] for m in hit]
            hits += [COMPUTED] * bool(LAYOUT_COMPUTED_REF_RE.search(text) or _bare(text))
            if not (hits or at < listed or is_code):  # outside a rule list, a literal counts only beside a field
                return hits, False
            if at < listed or is_code or cond or COMPARE_RE.search(masked := _blank(text)) \
                    or _arith_on(text, masked, [m.span() for m in hit]):
                return hits, value_count(text) > 1
            return hits, sum(value_count(text[a:b]) for a, b in _pattern_args(masked)) > 1
        fields, free, found = [], False, []
        for text in texts if is_code else [PARAM_TEST_RE.sub(" true ", t) for t in texts]:
            for clause, cond in [(text, True)] if is_code else _clauses(text, ternary=at >= listed):
                hits, lits = judged(clause, tested or cond)
                fields += hits
                if hits and lits:
                    found += hits
                elif lits and (is_code or not (_refs(clause) or PARAM_REF_RE.search(clause))):
                    free = True
        valued = sum(_value_chars(v.text or "") if v.get("type") == "constant" else value_count(code[v])
                     for v in vals) > 1
        if fields and (free or valued):
            found += fields
        out += [h for h in dict.fromkeys(found) if h not in out]
    return out, 20 * len(rules) + 30 * (clauses[0] + nvals) + 60 * len(sets) + _SCAN.work - scanned  # 5 a char: _too_long


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


# A namespace declaration over 64 bytes, which XML puts after a space: expat copies its URI into every name it
# qualifies, all through the member, even after a callback refuses it. Each xmlns is measured on its own (a lookahead),
# so an earlier one in a value or text (` xmlns="`) can't swallow it.
MAX_NAMESPACE_BYTES = 64  # real exports: 57 at most
NAMESPACE_RE = re.compile(rb"""(?<=\s)xmlns(?=(?::[^\s=>]*+)?\s*+=\s*+(?:"[^"]{%d}|'[^']{%d}))"""
                          % (MAX_NAMESPACE_BYTES + 1, MAX_NAMESPACE_BYTES + 1))
# A layout's settings: DA2 writes each once per element, and which of two BIRT would use can't be told for certain
SETTING_TAGS = frozenset(f"{B}{t}" for t in ("property", "text-property", "expression", "list-property",
                                              "simple-property-list", "method", "xml-property", "encrypted-property"))
# Every tag BIRT's parser names (IDesignSchemaConstants, and an ex-property's <name>). It matches a tag by the Java
# hashCode of its lower case alone, so <EXPRESSION>, or <expressiq0> of the same hash, is read as an <expression>.
BIRT_TAGS = frozenset("""
    access-control auto-text body browser-control cascading-parameter-group cell choice column column-header
    column-hint column-hints components contents cube cubes custom-color data data-group data-sets data-sources
    default default-value derived-data-set detail dimension dynamic-filter-parameter encrypted-property
    ex-property expression extended-item filter-condition-element filter-parameter footer free-form
    graphic-master-page grid group header headline hierarchy html-property image import include include-library
    include-script includes joint-data-set label level library line list list-parameter list-property margins
    measure measure-group member-value method multi-line-data multi-views name oda-cube oda-data-set
    oda-data-source oda-dimension oda-hierarchy oda-level oda-measure oda-measure-group overridden-values
    page-footer page-header page-sequence page-setup parameter parameter-group parameters property property-defn
    property-mask property-value rectangle ref-entry report report-item-theme report-items resource row
    scalar-parameter scratch-pad script-data-set script-data-source simple-master-page simple-property-list
    sort-element structure style styles table table-parameter tabular-cube tabular-dimension tabular-hierarchy
    tabular-level tabular-measure tabular-measure-group template template-data-set template-parameter-definition
    template-parameter-definitions template-report-item text text-data text-property theme themes toc translation
    translations type value value-access-control variable-element xml-property
""".split())
# What the checks read as one child (the first), where DA2 may read another: DA2 writes each once, and numbers its
# repeats (filter2, query_list_item2). BIRT keeps an ex-property's last <value>.
ONE_CHILD = {f"{B}ex-property": frozenset({f"{B}name", f"{B}value"})}
QUERY_ONCE = frozenset({"queries", "query_list", "domain", "output", "elements", "qualification", "runtimeprompt",
                        "operator", "values", "element"})
QUERY_MANY = {"elements": "element", "prompts": "qualification"}  # a column each, a prompt each
# BIRT's expression-typed settings (rom.def) and the tags DA2 writes them with. BIRT reads one as an expression whatever
# its tag (<property name="expr">), and the checks read <expression> (a list's <value>s): any other tag is refused. A
# selection list's value (a literal) is a <property>.
EXPR_TAGS = dict.fromkeys("""ACLExpression altText bookmark displayColumnName displayExpre expr expression
    expressionValue filterExpr headers key keyExpr labelExpr leftExpression measureExpression memberACLExpression
    refDateValue rightExpression rowACLExpression ruleExpre sortByColumn targetBookmark testExpr typeExpr uri value
    value2 valueExpr""".split(), frozenset({f"{B}expression"})) | dict.fromkeys(
    ("value1", "defaultValue"), frozenset({f"{B}expression", f"{B}simple-property-list"}))
CHOICE_TAGS = frozenset({f"{B}expression", f"{B}property"})


def _parse(body: bytes, member: str) -> ET.Element:
    if b"\x00" in body:  # UTF-16 would hide a DOCTYPE from the check below
        raise DA2FileError(f"{shown(member)} isn't UTF-8 text, as DA2 files are.")
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise DA2FileError(f"{shown(member)} has a DOCTYPE, which DA2 files never have.")
    if NAMESPACE_RE.search(body):
        raise DA2FileError(f"{shown(member)} names an XML namespace over {MAX_NAMESPACE_BYTES} bytes; DA2's are under 60.")
    if body.count(b"<") > 2 * MAX_ELEMENTS + 16:  # each element opens with one; the tree takes ~200 bytes an element
        raise DA2FileError(f"{shown(member)} has over {MAX_ELEMENTS:,} XML elements; DA2 files have a few thousand.")
    if body.count(b"=") + body.count(b"#") > MAX_ATTRIBUTES:
        raise DA2FileError(f"{shown(member)} has over {MAX_ATTRIBUTES:,} XML attributes and character references; DA2 "
                           "files have a few thousand.")
    head = body.removeprefix(b"\xef\xbb\xbf")  # expat reads 1.1 as 1.0, and BIRT's parser doesn't: a NEL ends a line
    if head.startswith(b"<?xml") and not re.match(rb"""<\?xml\s+version\s*=\s*(["'])1\.0\1""", head):
        raise DA2FileError(f"{shown(member)} isn't declared XML 1.0, as DA2 files are.")
    enc = re.match(rb"""\s*<\?xml[^>]*?\bencoding\s*=\s*["']([^"']*)""", head)
    if enc and enc[1].lower() not in (b"utf-8", b"utf8"):  # it would be written back as UTF-8 under the old name
        raise DA2FileError(f"{shown(member)} is in {shown(enc[1].decode('ascii', 'replace'))} text; DA2 files are UTF-8.")
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        root = ET.fromstring(body, parser=parser)
    except ET.ParseError as exc:
        raise DA2FileError(f"{shown(member)} isn't well-formed XML ({exc}).") from None
    ns = root.tag[:root.tag.find("}") + 1]  # "" for no namespace
    choices = {s for lst in root.iter(f"{B}list-property") if lst.get("name") == "selectionList" for s in lst}
    depth, count, level = 0, 0, [root]
    while level:  # writing it back recurses once per level
        for n in level:
            # BIRT reads XML as it streams: a comment or PI splits an element's text, text after a child joins it,
            # and an element in another namespace (xmlns="") may still be read by its name. DA2 writes none of these.
            if not isinstance(n.tag, str) or (n.tail or "").strip() or not n.tag.startswith(ns) or "}" in n.tag[len(ns):] \
                    or not (tag := n.tag[len(ns):]).isascii() or tag != tag.lower() or ns == B and tag not in BIRT_TAGS:
                raise DA2FileError(f"{shown(member)} has XML that DA2 doesn't write (a comment, processing instruction, "
                                   "text between elements, another namespace or a tag DA2 doesn't use).")
            if any(len(v) > MAX_NAME_CHARS for _, v in n.items()) \
                    or n.tag[len(ns):] == "property" and n.get("name") != "data" \
                    and len(n.text or "") > MAX_NAME_CHARS:  # each is folded, matched (an embedded image's data isn't)
                raise DA2FileError(f"{shown(member)} has a name or setting over {MAX_NAME_CHARS:,} characters; DA2's "
                                   "are under a hundred.")  # and split on every read
            if len(names := [c.get("name") for c in n if c.tag in SETTING_TAGS]) != len(set(names)):
                raise DA2FileError(f"{shown(member)} gives one of its settings two values; DA2 writes each once.")
            if ns == B and any(c.tag in SETTING_TAGS and c.tag not in (CHOICE_TAGS if n in choices and name == "value"
                                                                        else tags)
                               for c in n if (tags := EXPR_TAGS.get(name := c.get("name")))):
                raise DA2FileError(f"{shown(member)} writes an expression as a plain setting, which BIRT reads as an "
                                   "expression and DA2 never writes.")
            if n.tag == f"{B}ex-property" and (any(c.tag not in ONE_CHILD[n.tag] for c in n)
                                               or not (n.findtext(f"{B}name") or "").isascii()):
                raise DA2FileError(f"{shown(member)} has an extended property other than an ASCII name and a value, "
                                   "as DA2 writes it.")  # a <property name="value"> in it sets its value
            if len(n) > 1 and (once := ONE_CHILD.get(n.tag, QUERY_ONCE if not ns else None)):
                kids = set()
                for c in n:
                    if c.tag in once and QUERY_MANY.get(n.tag) != c.tag:
                        if c.tag in kids:
                            raise DA2FileError(f"{shown(member)} has two {shown(c.tag[len(ns):])} in one element, "
                                               "which DA2 writes once.")
                        kids.add(c.tag)
        depth, count, level = depth + 1, count + len(level), [c for n in level for c in n]
    if depth > MAX_DEPTH:
        raise DA2FileError(f"{shown(member)} is nested {depth} levels deep; DA2 files are far shallower.")
    if count > MAX_ELEMENTS:
        raise DA2FileError(f"{shown(member)} has {count:,} XML elements; DA2 files have a few thousand.")
    return root


def _check_directory(data: bytes, name: str) -> None:
    """The zip's central directory walked as zipfile will walk it, before zipfile decodes it: it reads every entry
    before the count, and each extra field in time quadratic in its records."""
    try:
        end = zipfile._EndRecData(io.BytesIO(data))  # zipfile's own reader of the end record, so both find the same one
    except ZIP_ERRORS:
        raise DA2FileError(f"{name} doesn't open as a .dareport.") from None
    if not end:
        return  # zipfile refuses it
    if end[zipfile._ECD_SIGNATURE] != zipfile.stringEndArchive:
        raise DA2FileError(f"{name} is a ZIP64 archive; a .dareport is a plain zip.")
    at, stop, count = end[zipfile._ECD_LOCATION] - end[zipfile._ECD_SIZE], end[zipfile._ECD_LOCATION], 0
    while 0 <= at < stop and data[at:at + 4] == b"PK\x01\x02" and at + 46 <= len(data):
        names, extra, comment = struct.unpack_from("<3H", data, at + 28)
        if (count := count + 1) > MAX_MEMBERS:
            raise DA2FileError(f"{name} holds over {MAX_MEMBERS} files; a .dareport holds a few.")
        if extra > MAX_EXTRA_BYTES:
            raise DA2FileError(f"{name} gives a file {extra:,} bytes of zip extra fields; DA2 writes none.")
        at += 46 + names + extra + comment


def _unzip(data: bytes, name: str) -> dict[str, bytes]:
    _check_directory(data, name)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except ZIP_ERRORS:
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
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):  # DA2 deflates; bzip2 or LZMA would
            raise DA2FileError(f"{name} is packed in a way DA2 doesn't pack a report.")  # unpack unbounded first
        try:
            with zf.open(info) as fh:
                body = fh.read(MAX_UNZIPPED_BYTES + 1)
        except ZIP_ERRORS:
            raise DA2FileError(f"{name} can't be unpacked (is it password-protected?).") from None
        total += len(body)
        if total > MAX_UNZIPPED_BYTES:
            raise DA2FileError(f"{name} unpacks to more than {MAX_UNZIPPED_BYTES:,} bytes.")
        out[mname] = body
    return out


def _swap_ids(body: bytes, news: dict[bytes, bytes]) -> bytes:
    """Each old id (lower case, matched in any case) replaced by its new one in one splice: a re.sub per id walked every
    member once for each id (20 queries' ids over 2 MB took 0.26 s)."""
    low, hits = body.lower(), []
    for old, new in news.items():
        i = low.find(old)
        while i >= 0:
            hits.append((i, old, new))
            i = low.find(old, i + len(old))
    out, at = [], 0
    for i, old, new in sorted(hits):
        if i >= at:  # two ids can't overlap in DA2's own files; a crafted overlap keeps the first
            out += [body[at:i], new]
            at = i + len(old)
    return b"".join(out) + body[at:]


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
        exc = DA2FileError(f"{name} filters on patient identifiers ({', '.join(map(shown, named))}). This site "
                           "can't hold patient data: take those values out in DA2, then upload it again.")
        exc.work = files.work  # the whole read
        raise exc
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

    def columns(self, limit: int | None = None) -> list[Column]:
        return [Column(e.get("name", ""), e.get("uuid", ""), e.get("type", ""), e.get("joinParent", ""))
                for e in itertools.islice((e for e in self.elements if e.tag == "element"), limit)]

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

    def __init__(self, root: ET.Element, dataset: ET.Element, bound: list[ET.Element]):
        """`bound` is every layout item whose dataSet is this one's name."""
        self.root, self.ds = root, dataset
        self.ds_name = dataset.get("name", "")
        tables = [e for e in bound if e.tag == f"{B}table"]
        self.table = tables[0] if len(bound) == 1 and len(tables) == 1 else None
        self.why_not = "" if self.table is not None else (
            "its layout shows that data in more than one place" if bound else "its layout has no table for it")
        if self.table is not None and (why := self._why_not_plain()):
            self.table, self.why_not = None, why

    def _rows(self) -> list[ET.Element]:
        """The table's own rows: a grid or table nested in a cell has rows of its own."""
        return [r for path in ("header", "group/header", "detail", "group/footer", "footer")
                for r in self.table.findall(f"{B}{path.replace('/', '/' + B)}/{B}row")]

    def width(self, row: ET.Element) -> int:
        """How many table columns the row's cells cover. BIRT pads a shorter row with empty cells at the end."""
        return sum(self._span(c, "colSpan") for c in row.findall(f"{B}cell"))

    def row_cells(self) -> list[tuple[ET.Element, list[int], list[ET.Element], list[int]]]:
        """Each of the table's rows, the table column each of its cells starts at, its cells, and how many columns each
        covers (a removal keeps these up to date)."""
        out = []
        for row in self._rows():
            cells = row.findall(f"{B}cell")
            spans = [self._span(cell, "colSpan") for cell in cells]
            out.append((row, list(itertools.accumulate(spans, initial=0))[:-1], cells, spans))
        return out

    @staticmethod
    def cell_at(starts: list[int], spans: list[int], position: int) -> int:
        """The index of a row's cell (from row_cells) that covers a table column, or -1. Places taken last first keep
        the starts of the places before them."""
        i = bisect.bisect_right(starts, position) - 1
        return i if i >= 0 and position < starts[i] + spans[i] else -1

    def positions(self, names: set[str]) -> list[int]:
        """The table columns whose detail cells show any of these columns or bindings, last first."""
        out = set()
        for row in self.table.findall(f"{B}detail/{B}row"):
            at = 0
            for cell in row.findall(f"{B}cell"):
                if any(_text(_prop(d, "resultSetColumn")) in names for d in cell.iter(f"{B}data")):
                    out.add(at)
                at += self._span(cell, "colSpan")
        return sorted(out, reverse=True)

    def _span(self, cell: ET.Element, name: str) -> int:
        if not len(cell):  # no settings: one column, one row
            return 1
        value = _text(_prop(cell, name))
        if not (value.isascii() and value.isdigit()):
            return 1
        return int(value) or 10 ** 9 if len(value) < 10 else 10 ** 9  # int() refuses 4,300 digits; 0 or that merges

    def _why_not_plain(self) -> str:
        width = len(self.table.findall(f"{B}column"))
        if width > MAX_TABLE_COLUMNS:
            return f"its table has more than {MAX_TABLE_COLUMNS} columns"
        for i, row in enumerate(self._rows()):
            if i == MAX_TABLE_ROWS:
                return f"its table has more than {MAX_TABLE_ROWS} rows"
            cells = row.findall(f"{B}cell")
            if any(self._span(c, "rowSpan") > 1 for c in cells) or self.width(row) > width:
                return "its table merges cells"
        return "" if width else "its table merges cells"

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
        ids = [i for e in self.root.iter() if (i := e.get("id") or "").isascii() and i.isdigit()]
        if any(len(i) > 18 for i in ids):  # DA2's have a few digits, and reading thousands as a number is slow
            raise DA2FileError("Its layout has element ids far longer than DA2 writes, so I can't add to it. Add it in "
                               "DA2.")
        return max(map(int, ids), default=0) + 1


class DA2Files:
    """An uploaded DA2 report or query, with the changes made to it so far."""

    def __init__(self, filename: str, members: dict[str, bytes]):
        self.size = sum(map(len, members.values()))  # bytes, unzipped: what a change walks
        self.work, scan = self.size // 4, _SCAN.work  # each byte is read before any refusal
        try:
            self._read(filename, members)
        except DA2FileError as exc:  # the scan's work is added to self.work per member: this counts a member's twice
            exc.work = self.work + _SCAN.work - scan
            raise

    def _read(self, filename: str, members: dict[str, bytes]) -> None:
        self.filename = filename
        self.kind = PurePosixPath(filename).suffix.lower()
        self._raw = dict(members)
        self._trees = {}
        for m, body in members.items():  # what a refusal while parsing costs: 10 an element (each opens with <) and
            # an attribute (one in a long namespace takes 0.7 us)
            self.work += 10 * min(body.count(b"<"), 2 * MAX_ELEMENTS + 16) + 10 * min(body.count(b"="), MAX_ATTRIBUTES)
            self._trees[m] = _parse(body, m)
        # What reading the files took, counted the same way every time, in units of about 0.1 us on one CPU: measured
        # 2026-11-24, a byte costs about 0.023 us, an element up to 1.4 us, a distinct attribute name up to 1.2 us (one
        # in a prefix's namespace is built as {uri}name) and a character of layout expression 0.5 us.
        self.work = self.size // 4 + 10 * sum(1 for root in self._trees.values() for _ in root.iter()) \
            + 10 * sum(len({k for n in root.iter() for k in n.keys()}) for root in self._trees.values())
        self._dirty: set[str] = set()
        self.changes: list[str] = []
        self._queries: list[_Query] = []
        self._layouts: dict[str, tuple[str, _Layout]] = {}  # query uuid -> (member, layout)
        self._prompts: list[ET.Element] = []  # the layouts' runtime-prompt defaults, read only
        self._rule_hits: list[str] = []  # patient fields the layouts' own filters and rules test
        self._slots: dict[str, set[ET.Element]] | None = None  # name slot -> the kept name filters that tell it
        for m, root in self._trees.items():
            ext = PurePosixPath(m).suffix.lower()
            want = {".daquery": "cclquery", ".rpt": "dareport", ".rptdesign": f"{B}report"}[ext]
            if root.tag != want:
                raise DA2FileError(f"{shown(m)} isn't a DA2 {ext} file.")
            if ext == ".daquery":
                self._queries.append(_Query(m, root))
        nsets, scan, parses = 0, _SCAN.work + MAX_SCAN_WORK, MAX_PROMPT_PARSES
        for m, root in self._trees.items():
            if root.tag != f"{B}report":
                continue
            bound: dict[str, list[ET.Element]] = {}  # one pass: a data set per query times every element is too slow
            for e in root.iter():
                if name := _text(_prop(e, "dataSet")):
                    bound.setdefault(name, []).append(e)
            sets = []
            for ds in root.iter(f"{B}oda-data-set"):
                qtext = next((c for c in ds if c.tag == f"{B}xml-property" and c.get("name") == "queryText"), None)
                body = (qtext.text or "").strip() if qtext is not None else ""
                if UUID_RE.fullmatch(body):  # a .dareport's layout names its query by id
                    sets.append((body, ds))
                elif body:  # a query written into the layout, which the filter check would never see
                    raise DA2FileError(f"{shown(m)} has a data set with its own query, not one of the report's, so I "
                                       "can't check it. Export the report from DA2 again.")
            if (nsets := nsets + len(sets)) > MAX_MEMBERS:
                raise DA2FileError(f"The report's layouts have {nsets:,} query data sets; a DA2 report has one for each "
                                   "query.")
            if len({ds.get("name", "") for _, ds in sets}) < len(sets):  # BIRT binds a table by the name
                raise DA2FileError(f"{shown(m)} has two data sets of one name, so which one a table shows can't be told.")
            for body, ds in sets:
                self._layouts[body] = (m, _Layout(root, ds, bound.get(ds.get("name", ""), [])))
            for prop in root.iter(f"{B}ex-property"):
                if _text(prop.find(f"{B}name")).lower() == "defaultruntimeprompts":  # as DA2 may (ASCII: _parse)
                    text = prop.findtext(f"{B}value") or ""
                    try:
                        trees = _embedded(text, m, parses)
                    except DA2FileError:  # what parsing it took, at most: each element and attribute of each level
                        # comes from a <, = or an escape of one (&lt; or a &#60;)
                        self.work += 10 * sum(map(text.count, ("<", "lt;", "#", "=")))
                        raise
                    self._prompts += trees
                    parses -= len(trees)  # each its own parser, and read on every check
                    self.work += 100 * len(trees) + 10 * sum(1 + len(n.keys()) for t in trees for n in t.iter())
            try:
                _SCAN.limit = scan
                hits, work = _rule_fields(root)
            finally:  # an upload's expressions are no use to the next one, and a long one's pieces take megabytes
                _SCAN.clear()
                _SCAN.limit = float("inf")
            self._rule_hits += [h for h in hits if h not in self._rule_hits]
            self.work += work
        if not self._queries:
            raise DA2FileError(f"{filename} has no DA2 query in it.")
        # A name filter's values are split into pieces character by character (_pieces), however long they are.
        self.work += sum(FILTER_WORK + FIELD_CHAR * sum(map(len, names)) + (NAME_FILTER + sum(NAME_VALUE + NAME_CHAR * sum(map(len, v)) for v in values)
                                        if any(PATIENT_NAME_FIELD_RE.search(n) for n in names) else 0)
                         for _, names, _, values in self._all_filters())
        # Each attribute of a filter value is a text the patient checks judge, on every read and once more a turn.
        self.work += 40 * sum(len(n.keys()) for root in [q.root for q in self._queries] + self._prompts
                              for n in root.iter("value"))

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

    def _all_filters(self):
        """Every filter in every query and sub-query, and in the layouts' runtime-prompt defaults: (node, its field's
        names, operator, each value's texts). A field is judged by its own name and by the catalog's name for its id."""
        for root in [q.root for q in self._queries] + self._prompts:
            for node in root.iter():
                el = node.find("element") if str(node.tag).startswith("filter") else None  # a comment's tag isn't text
                if el is not None:
                    op = node.find("operator")
                    names = [_fold(n) for n in (el.get("name", ""), _uuid_names().get(el.get("uuid", "").lower(), ""))
                             if n]
                    yield node, names, op.get("type", "") if op is not None else "", _value_texts(node)

    def patient_fields(self) -> list[str]:
        """Fields filtered on literal values that identify patients, in any query, sub-query or prompt default (the
        values themselves are never returned), the name fields whose patterns together spell a whole name, and the
        patient fields a layout's own filter or rule tests."""
        out, slots, named = list(self._rule_hits), {}, []
        for node, names, op, values in self._all_filters():
            if _identifies(names, op, values):
                hit = (next((n for n in names if PATIENT_ID_FIELD_RE.search(n)), None)
                       or next(n for n in names if PATIENT_NAME_FIELD_RE.search(n)))
                if hit not in out:
                    out.append(hit)
            elif kept := _name_slots(names, op, values):
                for slot in kept:
                    slots.setdefault(slot, set()).add(node)
                named += [n for n in names if PATIENT_NAME_FIELD_RE.search(n)]
        self._slots = slots
        return out + [n for n in dict.fromkeys(named) if n not in out] if len(slots) > 1 else out

    def summary(self) -> str:
        rep = self.report
        owner = shown(rep["owner_group"]) if rep["owner_group"] else "not set"
        lines = [f"**{shown(rep['name'])}** ({shown(self.filename)}), owner group {owner}"]
        known = _known_uuids()
        for q in self._queries:  # the first MAX_LISTED of each: a forged query of 20,000 columns made a 6.8 MB reply
            cols, filters = q.columns(MAX_LISTED), q.filters()
            n = sum(e.tag == "element" for e in q.elements)
            listed = ", ".join(shown(c.name) + ("" if c.uuid.lower() in known else " (not in Endexis's catalog)")
                               for c in cols) + (f", and {n - len(cols):,} more" if n > len(cols) else "")
            said = "; ".join(shown(f.text()) for f in filters[:MAX_LISTED])
            lines += [f"- Query {shown(q.name)}, {shown(q.domain) if q.domain else 'unknown'} domain, {n} columns: "
                      f"{listed or 'none'}",
                      f"- Filters: {said or 'none'}" + (f", and {len(filters) - MAX_LISTED:,} more"
                                                        if len(filters) > MAX_LISTED else "")]
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
        names, values = [_fold(name), _fold(lf["name"])], [(lf["like"],), (str(pattern).strip(),)]  # as typed too
        if _identifies(names, "LIKE", values):
            raise DA2FileError(f"{shown(name)} identifies patients, and this site can't hold patient data. Set that "
                               "filter in DA2.")
        replaced = [n for n in self._filter_on(q, name) if n.find("operator") is not None
                    and n.find("operator").get("type") == "LIKE"]
        if self._slots is None:
            self.patient_fields()
        kept = {slot for slot, nodes in self._slots.items() if any(n not in replaced for n in nodes)}
        new = _name_slots(names, "LIKE", values[:1])
        if len(kept | new) > 1:
            raise DA2FileError("With the report's other name filters, that would spell a whole patient name, and this "
                               "site can't hold patient data. Set that filter in DA2.")
        node = ET.Element("filter", {"joinType": "0", "mode": "0", "useIndex": "1"})
        ET.SubElement(node, "element", {"name": name, "type": "3", "uuid": lf["uuid"]})
        ET.SubElement(node, "operator", {"type": "LIKE"})
        ET.SubElement(ET.SubElement(node, "values", {"type": "LIST", "xCount": "1"}), "value", {"text": lf["like"]})
        verb = self._put_filter(q, name, node, "LIKE")
        for nodes in self._slots.values():
            nodes.difference_update(replaced)
        for slot in new:
            self._slots.setdefault(slot, set()).add(node)
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
        for nodes in (self._slots or {}).values():
            nodes.discard(node)
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
        detail_rows = set(detail.findall(f"{B}row")) if detail is not None else set()
        rows = layout.row_cells() if layout.table is not None else []
        for position in layout.positions(names) if layout.table is not None else []:
            for row, starts, cells, spans in rows:  # removing the column removes each row's cell at its place
                i = layout.cell_at(starts, spans, position)
                if i < 0 or spans[i] > 1 or not len(cell := cells[i]):  # an empty cell holds nothing
                    continue
                if any(next(cell.iter(f"{B}{t}"), None) is not None for t in ("grid", "table", "list")) or any(
                        _text(_prop(d, "resultSetColumn")) not in names for d in cell.iter(f"{B}data")):
                    where.append("a cell it shares with other fields" if row in detail_rows
                                 else "a group or header row's cell above it")
        for node in layout.root.iter():
            if node in own_nodes:
                continue
            text = node.text or ""
            if any(m[1] in names or m[2] in names for m in LAYOUT_REF_RE.finditer(text)):
                where.append(LAYOUT_USES.get(node.get("name") or "", "an expression"))
            elif node.get("name") == "resultSetColumn" and text.strip() in names:
                where.append("a field shown outside the detail row")
        return list(dict.fromkeys(where))

    def _layout_check_remove(self, layout: _Layout, column: str) -> None:
        uses = self._layout_uses(layout, column)
        if uses and layout.table is None and uses == ["a field shown outside the detail row"]:
            raise DA2FileError(f"The layout shows {shown(column)}, and I can't change the layout because "
                               f"{layout.why_not}. Remove it in DA2.")
        if uses and layout.table is None:
            raise DA2FileError(f"The layout uses {shown(column)} ({', '.join(uses[:3])}), and I can't change the layout "
                               f"because {layout.why_not}. Remove it in DA2.")
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
        columns, rows, gone = layout.table.findall(f"{B}column"), layout.row_cells(), set()
        for position in layout.positions(names):  # last first, so each place is still where it was
            gone.add(columns[position])
            for row, starts, cells, spans in rows:
                if (i := layout.cell_at(starts, spans, position)) < 0:
                    continue
                if spans[i] > 1:
                    spans[i] -= 1
                    _prop(cells[i], "colSpan").text = str(spans[i])
                else:
                    gone.add(cells[i])
        for parent in [layout.table] + [row for row, *_ in rows]:  # each remove() would look through all the children
            if any(c in gone for c in parent):
                parent[:] = [c for c in parent if c not in gone]

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
            news = {o.lower().encode(): str(uuid.uuid5(ID_NAMESPACE, f"{o.lower()}|{digest}")).encode()
                    for o in olds if UUID_RE.fullmatch(o)}
            members = {m: _swap_ids(body, news) for m, body in members.items()}
        if self.kind != ".dareport":
            return {self.filename: members[self.filename]}
        buf = io.BytesIO()
        # Level 1: level 6 wrote 2 MB of four-letter text at 7.6 MB/s at 1 CPU; level 1 writes any data at 45 MB/s or more
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for m, body in members.items():  # a fixed date: the same edits must give the same bytes
                zf.writestr(zipfile.ZipInfo(m, date_time=(1980, 1, 1, 0, 0, 0)), body, compress_type=zipfile.ZIP_DEFLATED,
                            compresslevel=1)
        return {self.filename: buf.getvalue()}
