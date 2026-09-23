"""Authenticated, generated sample downloads contain no partner data."""
from io import BytesIO
from tempfile import TemporaryDirectory
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.responses import Response


def install_samples(app):
    @app.get('/api/v1/samples')
    def sample(supplier_id: Literal['systeme-electric', 'iek'] = 'systeme-electric'):
        from scripts.generate_sample_data import generate, generate_iek
        with TemporaryDirectory(prefix='tirek-synthetic-') as directory:
            paths = generate_iek(directory) if supplier_id == 'iek' else generate(directory)
            stream = BytesIO()
            with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
                for path in paths.values():
                    archive.write(path, arcname=path.name)
                archive.writestr('READ-ME.txt',
                    'SYNTHETIC Tirek sample. Not partner data or forecast accuracy evidence.\n'
                    'Upload all XLSX files with the selected supplier. Load additional-context.SYNTHETIC.json.\n'
                    'Start a scenario calculation at 2026-09-22 using the category policy from calculation-request.SYNTHETIC.json.\n')
        return Response(stream.getvalue(), media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="tirek-{supplier_id}-SYNTHETIC.zip"'})
