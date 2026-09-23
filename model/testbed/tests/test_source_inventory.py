from io import BytesIO
import unittest
from zipfile import ZipFile

from model.testbed.source_inventory import inspect_workbook


class SourceInventoryTests(unittest.TestCase):
    def test_private_records_not_exported_and_negative_sign_is_preserved(self):
        buffer=BytesIO()
        with ZipFile(buffer,'w') as book:
            book.writestr('xl/workbook.xml','''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="data" sheetId="1" r:id="r1"/></sheets></workbook>''')
            book.writestr('xl/_rels/workbook.xml.rels','''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>''')
            book.writestr('xl/worksheets/sheet1.xml','''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>Дата</t></is></c><c r="H1" t="inlineStr"><is><t>Количество</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>20.07.2023 16:15:38</t></is></c><c r="D2" t="inlineStr"><is><t>private-product-code</t></is></c><c r="F2" t="inlineStr"><is><t>шт</t></is></c><c r="H2"><v>-10</v></c></row>
</sheetData></worksheet>''')
        summary=inspect_workbook(buffer.getvalue(),'Динамика.xlsx')
        self.assertNotIn('private-product-code',str(summary))
        sheet=summary['sheets'][0]
        self.assertEqual(sheet['quantity_sign_rows'],{'negative':1})
        self.assertFalse(sheet['client_id_column_present'])
        self.assertEqual(sheet['rows_before_2025'],1)


if __name__=='__main__':unittest.main()
