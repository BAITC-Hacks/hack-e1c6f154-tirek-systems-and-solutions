"""Read partner archives without extracting or committing their records.

The output contains file hashes, schemas and aggregate coverage, not sales rows.
Excel XML is streamed with the standard library; workbooks are never modified.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def rows(book, path, strings):
    with book.open(path) as handle:
        for _, element in ET.iterparse(handle, events=('end',)):
            if element.tag != '{'+NS['m']+'}row':
                continue
            values = {}
            for cell in element.findall('m:c', NS):
                column = re.sub(r'\d+', '', cell.attrib['r'])
                node = cell.find('m:v', NS)
                value = node.text if node is not None else ''
                if cell.attrib.get('t') == 's' and value:
                    value = strings[int(value)]
                elif cell.attrib.get('t') == 'inlineStr':
                    value = ''.join(cell.find('m:is', NS).itertext())
                values[column] = (value or '').strip()
            yield int(element.attrib['r']), values
            element.clear()


def inspect_workbook(data, filename):
    result = {'file': filename, 'sha256': hashlib.sha256(data).hexdigest(), 'sheets': []}
    with ZipFile(BytesIO(data)) as book:
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            strings = [''.join(x.itertext()) for x in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('m:si', NS)]
        definitions = ET.fromstring(book.read('xl/workbook.xml')).findall('m:sheets/m:sheet', NS)
        relations = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
        targets = {x.attrib['Id']: x.attrib['Target'] for x in relations}
        for sheet in definitions:
            rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = targets[rid]
            path = target.lstrip('/') if target.startswith('/') else 'xl/'+target
            headers, count, days, units, signs = [], 0, Counter(), Counter(), Counter()
            for index, values in rows(book, path, strings):
                count += 1
                # Only header rows; never copy article names/codes or transactions.
                header_row = 2 if 'Товар в пути' in filename and sheet.attrib['name'] == 'TDSheet' else 1
                if index == header_row:
                    headers = list(values.values())
                    if '/Путь ' in filename:
                        headers = headers[:3]+['Плановое поступление с датой ETA']*max(0,len(headers)-3)
                if 'Динамика' in filename and index > 1:
                    try:
                        stamp = datetime.strptime(values.get('A',''), '%d.%m.%Y %H:%M:%S').date().isoformat()
                        quantity = float(values.get('H',''))
                    except ValueError:
                        continue
                    days[stamp] += 1
                    units[values.get('F','')] += 1
                    signs['negative' if quantity < 0 else 'positive' if quantity > 0 else 'zero'] += 1
            meta = {'name': sheet.attrib['name'], 'xml_rows': count, 'header': headers}
            if days:
                meta.update(date_min=min(days), date_max=max(days), distinct_transaction_dates=len(days),
                            transaction_rows=sum(days.values()), units=dict(units), quantity_sign_rows=dict(signs),
                            rows_before_2025=sum(n for d,n in days.items() if d < '2025-01-01'),
                            client_id_column_present=any('клиент' in h.lower() for h in headers),
                            price_column_present=any('цен' in h.lower() for h in headers))
            result['sheets'].append(meta)
    return result


def inspect_sources(folder, previous=None):
    files=[]
    for archive in sorted(Path(folder).glob('*.zip')):
        with ZipFile(archive) as handle:
            for name in sorted(handle.namelist()):
                if not name.endswith('.xlsx'):
                    continue
                data=handle.read(name)
                meta=inspect_workbook(data,name)
                meta['archive']=archive.name
                if previous:
                    old=Path(previous)/name
                    meta['same_as_previous_input']=old.exists() and hashlib.sha256(old.read_bytes()).hexdigest()==meta['sha256']
                files.append(meta)
    pdfs=[{'file': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(Path(folder).glob('*.pdf'))]
    return {'kind':'partner_source_inventory_not_forecast_evaluation', 'workbooks':files, 'pdfs':pdfs,
            'raw_records_exported':False,
            'interpretation': {
                'monthly_sales':'Separate monthly observations, not daily labels; overlapping periods cannot be added to detail.',
                'monthly_stock':'Opening-month stock snapshot, not stockout dates or availability fractions.',
                'detail':'Document identifiers are not anonymized client IDs. Signed transactions require documented semantics; negatives are not silently clipped.',
                'inbound':'Snapshot dated 2026-09-22, not a feature for earlier forecast origins; existing ETA is not lead time for a new order. SE contains stock/reserve/free-stock and category headers; date and warehouse semantics need confirmation.',
                'seasonality':'Published coefficients include later months; derive historical coefficients from past-only quantities.',
                'moq':'SE header is order multiple; IEK minimum allowed shipment. Neither establishes both MOQ and multiple.',
                'units':'Pieces, metres and packages must not be pooled as one quantity score.',
                'readiness':'Missing client identity, exact availability, current inventory context and procurement policy prevent claiming validated real procurement.'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',required=True)
    parser.add_argument('--previous-inputs')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=inspect_sources(args.folder,args.previous_inputs)
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'workbooks':len(result['workbooks']), 'raw_records_exported':False}))


if __name__ == '__main__':
    main()
