"""Read-only, privacy-preserving inspector for the twelve supplied OOXML exports.

This is a fixed-profile source audit, not a universal XLSX importer, demand oracle,
or procurement calculator. ZIP members are read in memory; no source is extracted,
recalculated or modified. Unknown schemas never become plausible zero inputs.
"""
from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO
import hashlib
import math
from pathlib import Path
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
T = lambda name: "{" + NS + "}" + name

# Exact filenames are intentional: this inspector describes this supplied batch.
PROFILES = (
    ("IEK.zip", "IEK/MOQ  ИЭК.xlsx", "IEK", "moq"),
    ("IEK.zip", "IEK/Динамика продаж_2025-2026.xlsx", "IEK", "detail"),
    ("IEK.zip", "IEK/Ежемесячные остатки продукции за последние 2 года  ИЭК.xlsx", "IEK", "monthly_stock"),
    ("IEK.zip", "IEK/Ежемесячные продажи в количественном выражении за последние 2 года.xlsx", "IEK", "monthly_sales"),
    ("IEK.zip", "IEK/Путь ИЭК 22.09.2026.xlsx", "IEK", "inbound"),
    ("IEK.zip", "IEK/Сезонность ИЭК.xlsx", "IEK", "seasonality"),
    ("Systeme electric.zip", "Systeme electric/MOQ SystemElectric.xlsx", "SE", "moq"),
    ("Systeme electric.zip", "Systeme electric/Динамика продаж_Syseme Electric_2025-2026.xlsx", "SE", "detail"),
    ("Systeme electric.zip", "Systeme electric/Ежемесячные остатки SystemElectric 2024-2026.xlsx", "SE", "monthly_stock"),
    ("Systeme electric.zip", "Systeme electric/Ежемесячные продажи в кол-м выражении SystemElectric 2024-2026.xlsx", "SE", "monthly_sales"),
    ("Systeme electric.zip", "Systeme electric/Товар в пути_SystemElectric на 22.09.2026.xlsx", "SE", "inbound"),
    ("Systeme electric.zip", "Systeme electric/Сезонность SystemElectric 2024-2026.xlsx", "SE", "seasonality"),
)
DETAIL_HEADERS = dict(zip("ABCDEFGH", ("Дата", "Номер", "Документ", "Код", "Номенклатура", "Ед.", "Склад", "Количество")))
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
UNITS = {"шт", "м", "упак", "кг", "л", "компл", "уп", "пог.м", "рул"}
ERRORS = {"#N/A", "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"}


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _date(value):
    value = _clean(value)
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?: (\d{1,2}):(\d{1,2}):(\d{1,2}))?", value)
    try:
        if match:
            day, month, year, hour, minute, second = (int(v or 0) for v in match.groups())
            return datetime(year, month, day, hour, minute, second).isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?", value):
            return datetime.fromisoformat(value).isoformat()
    except ValueError:
        pass
    return None


def _column(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _month(value):
    text = _clean(value).lower()
    match = re.fullmatch(r"([а-яё.]+) (20\d{2})(?: г\.)?", text)
    if not match:
        return None
    prefix = match[1][:3].rstrip(".")
    if prefix not in MONTHS:
        return None
    return f"{match[2]}-{MONTHS.index(prefix)+1:02d}"


def _profile(brand, kind, sheet_index):
    if kind == "seasonality" or sheet_index == 2:
        return {"header_row": 3, "headers": {"A": "год", "N": "ИТОГО"},
                "code_column": None, "role": "seasonality_summary"}
    if kind == "detail":
        return {"header_row": 1, "headers": DETAIL_HEADERS, "code_column": "D", "role": kind}
    if kind == "moq":
        fields = {"A": "№", "B": "Код 1с", "C": "Артикул поставщика", "D": "Наименование", "E": "Мин. разр. к отгр."} if brand == "IEK" else {
            "A": "№", "B": "Номенклатура", "C": "Номенклатура.Код", "D": "Артикул", "E": "Кратность"}
        return {"header_row": 1, "headers": fields, "code_column": "B" if brand == "IEK" else "C", "role": kind}
    if kind == "monthly_stock":
        fields = {"A": "Номенклатура", "B": "Ед.", "C": "Номенклатура.Код"} if brand == "IEK" else {
            "A": "№", "B": "Номенклатура", "C": "Номенклатура.Код", "D": "Ед.изм"}
        return {"header_row": 1, "headers": fields, "code_column": "C", "role": kind,
                "month_start_column": 4 if brand == "IEK" else 5}
    if kind == "monthly_sales":
        fields = {"A": "Номенклатура", "B": "Номенклатура.Код"}
        if brand == "SE":
            fields.update(C="Артикул", D="Кратность")
        return {"header_row": 1, "headers": fields, "code_column": "B", "role": kind,
                "month_start_column": 3 if brand == "IEK" else 5}
    if brand == "IEK":
        return {"header_row": 1, "headers": {"A": "Код 1с", "B": "Артикул ИЭК", "C": "Наименование"},
                "code_column": "A", "role": "inbound", "order_columns": list("DEFGHI")}
    return {"header_row": 2, "headers": {
        "A": "№", "B": "Артикул поставщика", "C": "Код 1с", "D": "Наименование", "E": "Категория 2026",
        "F": "СС реал", "AP": "Сумма последние 12 мес", "AQ": "Ср мес за последние 12 мес",
        "AR": "Кэф. Роста", "AS": "Кэф. Сез-ти", "AX": "Остаток",
        "AY": "Зарезервировано", "AZ": "Свободный остаток", "BA": "Запас", "BB": "Заказ", "BC": "СЭ в пути 24.09"},
        "code_column": "C", "role": "inbound"}


def _parse_cell(cell, shared):
    value_element = cell.find(T("v"))
    raw = value_element.text if value_element is not None else None
    cell_type = cell.get("t", "n")
    if cell_type == "s" and raw is not None:
        raw = shared[int(raw)]
    elif cell_type == "inlineStr":
        inline = cell.find(T("is"))
        raw = "".join(t.text or "" for t in inline.iter(T("t"))) if inline is not None else None
    return raw, cell_type, value_element


def _inspect_sheet(book, member, shared, brand, kind, index):
    profile = _profile(brand, kind, index)
    stats = Counter()
    numeric_columns = defaultdict(Counter)
    errors, missing_cache = [], []
    header = {}
    schema_errors = []
    codes = set()
    units = Counter()
    dates = []
    warehouse_ids = set()
    category_codes = Counter()
    source_id_rows = 0
    month_columns = []
    inbound_eta = []
    formula_observations = []
    for _, element in ET.iterparse(book.open(member), events=("end",)):
        if element.tag != T("row"):
            continue
        row_number = int(element.get("r"))
        stats["rows_in_xml"] += 1
        values = {}
        for cell in element.findall(T("c")):
            address = cell.get("r", "")
            if not re.fullmatch(r"[A-Z]+[1-9]\d*", address):
                raise ValueError("Invalid OOXML cell address")
            col = re.match(r"[A-Z]+", address)[0]
            raw, typ, value_element = _parse_cell(cell, shared)
            values[col] = raw
            stats["cells_in_xml"] += 1
            if typ == "e":
                if raw is None:
                    stats["empty_error_typed_cells"] += 1
                else:
                    stats["excel_error_cells"] += 1
                    if len(errors) < 100:
                        errors.append({"cell": address, "error": raw if raw in ERRORS else "unrecognized_excel_error"})
            formula = cell.find(T("f"))
            if formula is not None:
                stats["formula_cells"] += 1
                if value_element is None or value_element.text is None:
                    stats["formula_cache_absent_or_empty"] += 1
                    if len(missing_cache) < 100:
                        missing_cache.append(address)
                if typ == "e" and raw is not None:
                    stats["formula_cached_errors"] += 1
                # Inspect only fixed, structural references, never print raw formulas.
                if brand == "SE" and kind == "inbound" and index == 1 and address == "AP3":
                    if re.sub(r"\s+", "", formula.text or "").upper() == "SUM(AC3:AO3)":
                        formula_observations.append({"cell": address, "issue": "label_says_12_months_but_formula_sums_13_columns",
                                                     "source_range": "AC3:AO3", "period": "2025-09/2026-09"})
        if row_number == profile["header_row"]:
            for col, expected in profile["headers"].items():
                matched = _clean(values.get(col)).casefold() == expected.casefold()
                header[col] = {"expected": expected, "matched": matched}
                if not matched:
                    schema_errors.append({"cell": f"{col}{row_number}", "issue": "required_header_missing_or_changed", "expected": expected})
            if kind in ("detail", "moq") and index == 1:
                for col, value in values.items():
                    if _clean(value) and col not in profile["headers"]:
                        schema_errors.append({"cell": f"{col}{row_number}", "issue": "unexpected_header_column"})
            if "month_start_column" in profile:
                for n in range(33):
                    col = _column(profile["month_start_column"] + n)
                    expected = f"{2024+n//12}-{n%12+1:02d}"
                    parsed = _month(values.get(col))
                    month_columns.append({"column": col, "period": parsed, "expected_period": expected})
                    if parsed != expected:
                        schema_errors.append({"cell": f"{col}{row_number}", "issue": "month_header_missing_or_changed", "expected": expected})
            for col in profile.get("order_columns", []):
                # An ETA of an existing purchase order is not a new-order lead time.
                found = re.search(r"поступление до\s*(\d{2}\.\d{2}\.\d{4})", values.get(col) or "", re.I)
                eta = _date(found[1]) if found else None
                inbound_eta.append({"column": col, "eta": eta[:10] if eta else None})
                if not eta:
                    schema_errors.append({"cell": f"{col}{row_number}", "issue": "existing_order_eta_missing_or_changed"})
        code_col = profile.get("code_column")
        if row_number > profile["header_row"] and code_col and not _clean(values.get(code_col)):
            stats["rows_without_sku_below_header_including_footers"] += 1
        if row_number > profile["header_row"] and code_col and _clean(values.get(code_col)):
            code = _clean(values[code_col])
            if code in profile["headers"].values():
                element.clear()
                continue
            source_id_rows += 1
            codes.add(code)  # Never emitted; used only for coverage counts.
            for col, value in values.items():
                if col == code_col:
                    continue
                number = _number(value)
                column_stats = numeric_columns[col]
                if number is not None:
                    column_stats["numeric"] += 1
                    column_stats["negative"] += number < 0
                    column_stats["zero"] += number == 0
                    column_stats["fractional"] += not number.is_integer()
            if kind == "detail":
                stamp = _date(values.get("A"))
                if stamp:
                    dates.append(stamp)
                    stats["dated_rows"] += 1
                else:
                    stats["invalid_or_missing_date_rows"] += 1
                quantity = _number(values.get("H"))
                stats["quantity_missing_or_invalid"] += quantity is None
                if quantity is not None:
                    stats["negative_quantity_rows"] += quantity < 0
                    stats["zero_quantity_rows"] += quantity == 0
                    stats["fractional_quantity_rows"] += not quantity.is_integer()
                unit = _clean(values.get("F")).lower().rstrip(".")
                units[unit if unit in UNITS else "unknown_or_missing_unit"] += 1
                if values.get("G"):
                    warehouse_ids.add(values["G"])
            elif kind == "monthly_stock":
                unit_col = "B" if brand == "IEK" else "D"
                unit = _clean(values.get(unit_col)).lower().rstrip(".")
                units[unit if unit in UNITS else "unknown_or_missing_unit"] += 1
            elif kind == "inbound" and brand == "SE":
                category = _clean(values.get("E"))
                category_codes[category if re.fullmatch(r"\d{1,2}", category) else "unknown_or_missing_category"] += 1
                stock, reserve, free = (_number(values.get(c)) for c in ("AX", "AY", "AZ"))
                if all(v is not None for v in (stock, reserve, free)):
                    stats["stock_reserve_free_rows_compared"] += 1
                    stats["stock_minus_reserve_free_mismatches"] += abs(stock-reserve-free) > 1e-8
        element.clear()
    if not header:
        schema_errors.append({"issue": "required_header_row_absent", "row": profile["header_row"]})
    column_output = {}
    for col, counts in sorted(numeric_columns.items()):
        if counts["numeric"]:
            column_output[col] = dict(counts, absent_or_non_numeric=source_id_rows-counts["numeric"])
    return ({"sheet_index": index, "role": profile["role"], "schema_status": "matched" if not schema_errors else "mismatch",
             "headers": header, "schema_errors": schema_errors, "monthly_columns": month_columns,
             "counts": dict(stats), "identified_rows": source_id_rows, "unique_sku_count": len(codes),
             "repeated_sku_rows": source_id_rows-len(codes), "numeric_columns": column_output,
             "units": dict(sorted(units.items())), "date_start": min(dates) if dates else None,
             "date_end": max(dates) if dates else None, "warehouse_count": len(warehouse_ids),
             "category_codes": dict(sorted(category_codes.items())), "existing_order_eta": inbound_eta,
             "excel_errors_first_100": errors, "formula_cache_missing_first_100": missing_cache,
             "formula_observations": formula_observations}, codes)


def _inspect_workbook(data, brand, kind):
    with ZipFile(BytesIO(data)) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ["".join(t.text or "" for t in si.iter(T("t")))
                      for si in ET.fromstring(book.read("xl/sharedStrings.xml")).findall(T("si"))]
        relations = {r.get("Id"): r.get("Target") for r in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheets, codes = [], set()
        for index, sheet in enumerate(workbook.find(T("sheets")), 1):
            target = relations[sheet.attrib[RID]]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            if not target.startswith("xl/worksheets/"):
                raise ValueError("Unexpected worksheet relationship")
            result, found_codes = _inspect_sheet(book, target, shared, brand, kind, index)
            result["visibility"] = sheet.get("state", "visible")
            sheets.append(result)
            if index == 1:
                codes = found_codes
        expected_count = 2 if brand == "SE" and kind in ("monthly_sales", "inbound") else 1
        return {"sheets": sheets, "expected_sheet_count": expected_count,
                "schema_status": "matched" if len(sheets) == expected_count and all(s["schema_status"] == "matched" for s in sheets) else "mismatch",
                "external_link_count": sum(bool(re.fullmatch(r"xl/externalLinks/externalLink\d+\.xml", n)) for n in book.namelist())}, codes


def _readiness(files):
    result = {}
    for brand in ("IEK", "SE"):
        matched = {f["kind"] for f in files if f["brand"] == brand and f.get("schema_status") == "matched"}
        available = lambda kind: "detected" if kind in matched else "unavailable_due_to_missing_or_changed_schema"
        result[brand] = {
            "status": "needs_context_and_data_validation",
            "fields": {
                "observed_sales": available("detail"), "monthly_stock_snapshots": available("monthly_stock"),
                "exact_stockout_intervals": "not_present", "pseudonymous_customer_id": "not_present",
                "sales_price": "not_present", "new_order_lead_time": "not_present",
                "existing_order_eta": available("inbound"),
                "current_stock_reserve_free": available("inbound") if brand == "SE" else "not_present",
                "raw_category_codes": available("inbound") if brand == "SE" else "not_present",
                "category_or_service_policy": "not_present", "external_future_growth_forecast": "not_present",
                "historical_growth_coefficients": available("inbound") if brand == "SE" else "not_present",
                "confirmed_material_requirements": "not_present",
                "economic_costs": "needs_confirmation_of_SS_real_meaning_and_missing_other_costs" if brand == "SE" and "inbound" in matched else "not_present",
                "supplier_moq": "needs_confirmation_of_minimum_shipment_field_and_error_rows" if brand == "IEK" and "moq" in matched else "not_present",
                "order_multiple": available("moq") if brand == "SE" else "needs_confirmation_separate_from_minimum_shipment",
            },
            "calculation_blockers": ["new_order_lead_time", "category_or_service_policy"] +
                (["current_stock_by_warehouse_as_of"] if brand == "IEK" else ["confirm_snapshot_date_and_warehouse_scope"]),
            "mh3_mh4_evidence_gaps": ["exact_stockout_intervals", "pseudonymous_customer_id", "project_ground_truth"],
            "interpretation": ["Monthly zero stock does not establish a full month of stockout.",
                               "Missing quantity is unknown, not zero; negative quantity is not converted to abs().",
                               "Existing-order ETA is not lead time of a new purchase.",
                               "Historical growth coefficients are not an external future growth forecast.",
                               "A source field named multiple does not establish a separate MOQ.",
                               "Raw category codes do not establish category policy."]}
    return result


def inspect_sources(input_dir):
    """Inspect supplied archives; return safe aggregates and unresolved prerequisites.

    Corrupt workbooks, missing files and changed required schemas are retained as
    failures. This function does not import source rows into forecasting inputs.
    """
    directory = Path(input_dir)
    if not directory.is_dir():
        raise ValueError("Source input directory does not exist")
    files, archives, code_sets = [], [], {}
    for archive_name in sorted({p[0] for p in PROFILES}):
        path = directory / archive_name
        wanted = [p for p in PROFILES if p[0] == archive_name]
        if not path.is_file():
            archives.append({"file": archive_name, "status": "missing"})
            files.extend({"file": name, "brand": brand, "kind": kind, "status": "missing_archive"} for _, name, brand, kind in wanted)
            continue
        raw = path.read_bytes()
        archive_record = {"file": archive_name, "sha256": hashlib.sha256(raw).hexdigest(), "status": "read"}
        archives.append(archive_record)
        try:
            with ZipFile(BytesIO(raw)) as outer:
                names = outer.namelist()
                archive_record["unexpected_xlsx_count"] = sum(n.endswith(".xlsx") and n not in {p[1] for p in wanted} for n in names)
                for _, name, brand, kind in wanted:
                    record = {"archive": archive_name, "file": name, "brand": brand, "kind": kind}
                    files.append(record)
                    if names.count(name) != 1:
                        record["status"] = "missing_or_duplicate_member"
                        continue
                    data = outer.read(name)
                    record["sha256"] = hashlib.sha256(data).hexdigest()
                    try:
                        observed, codes = _inspect_workbook(data, brand, kind)
                        record.update(observed, status="inspected")
                        if observed["schema_status"] == "matched":
                            code_sets[(brand, kind)] = codes
                    except (BadZipFile, ET.ParseError, KeyError, IndexError, ValueError, TypeError):
                        record["status"] = "invalid_or_unsupported_ooxml"
        except (BadZipFile, OSError):
            archive_record["status"] = "invalid_archive"
            already = {f["file"] for f in files}
            files.extend({"file": name, "brand": brand, "kind": kind, "status": "invalid_archive"}
                         for _, name, brand, kind in wanted if name not in already)
    joins = []
    for brand in ("IEK", "SE"):
        detail = code_sets.get((brand, "detail"))
        if detail is None:
            continue
        for kind in ("moq", "monthly_stock", "monthly_sales", "inbound"):
            other = code_sets.get((brand, kind))
            if other is not None:
                joins.append({"brand": brand, "source": kind, "detail_unique_skus": len(detail),
                              "source_unique_skus": len(other), "overlap": len(detail & other),
                              "detail_skus_missing_from_source": len(detail - other),
                              "source_skus_absent_from_detail": len(other - detail)})
    issues = sum(f.get("schema_status") != "matched" for f in files)
    pdf_files = [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                  "content_inspected_by_this_function": False}
                 for p in sorted(directory.glob("*.pdf")) if p.is_file()]
    return {"schema_version": "testbed-source-inspection-v1", "mode": "real_observed_source_audit",
            "profile_scope": "fixed supplied twelve workbooks; not a universal importer",
            "archives": archives, "case_pdf_fingerprints": pdf_files, "files": files, "expected_workbooks": len(PROFILES),
            "inspected_workbooks": sum(f.get("status") == "inspected" for f in files),
            "missing_changed_or_invalid_workbooks": issues, "catalog_coverage": joins,
            "readiness": _readiness(files),
            "forecast_evaluation": {"latent_regular_wape_available": False,
                                    "reason": "Observed source exports contain neither latent regular demand nor project ground truth.",
                                    "observed_sales_backtest_is_distinct": True},
            "privacy": "No SKU, product/client/document values or raw rows are emitted; only fixed schema labels, cell addresses and aggregates.",
            "limitations": ["Cached Excel values are inspected without recalculation or freshness proof.",
                            "Formula errors, missing caches and empty cells remain distinct from numeric zero.",
                            "September 2026 is an incomplete month at the supplied September 22 snapshot.",
                            "Source data do not supply ground truth for hidden demand or client project labels.",
                            "Inspection success is not procurement readiness or forecast accuracy."]}
