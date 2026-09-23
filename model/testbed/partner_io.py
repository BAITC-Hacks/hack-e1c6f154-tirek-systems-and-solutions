"""Read-only XLSX and private-output helpers, without third-party dependencies."""
from decimal import Decimal, InvalidOperation
import csv
import hashlib
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from .source_inventory import NS, rows

SUPPLIERS = {"SE": "Systeme electric", "IEK": "IEK"}
UNITS = {"шт": "pieces", "м": "metres", "упак": "packs"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def one_file(folder, pattern):
    matches = sorted(Path(folder).glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {pattern} in {folder}")
    return matches[0]


def workbook_rows(path, sheet_name=None):
    """Yield column-letter maps of cached cell values; never evaluate formulas."""
    with ZipFile(path) as book:
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            strings = ["".join(x.itertext()) for x in ET.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", NS)]
        sheets = ET.fromstring(book.read("xl/workbook.xml")).findall("m:sheets/m:sheet", NS)
        matches = sheets[:1] if sheet_name is None else [s for s in sheets if s.attrib["name"] == sheet_name]
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous worksheet")
        sheet = matches[0]
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        targets = {x.attrib["Id"]: x.attrib["Target"] for x in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        target = targets[rid]
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
        yield from rows(book, member, strings)


def number(value, *, signed=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing or invalid number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid number") from exc
    if not result.is_finite() or (not signed and result < 0):
        raise ValueError("Nonfinite or negative number")
    return result


def private_path(path):
    """Reject raw/prediction output in ANY Git worktree, also through symlinks."""
    path = Path(path).resolve()
    markers = [parent / ".git" for parent in (path, *path.parents)]
    # An empty .git scratch directory is not an initialized repository.
    if any(marker.is_file() or (marker.is_dir() and (marker / "HEAD").exists()) for marker in markers):
        raise ValueError("Partner rows and predictions must stay outside every Git worktree")
    return path


def csv_records(path, required):
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not set(required).issubset(fields):
            raise ValueError("Missing or duplicate CSV columns")
        records = list(reader)
    if not records or any(None in row or any(row[key] is None for key in fields) for row in records):
        raise ValueError("Empty or ragged CSV rows")
    return records
