"""Serve a built SPA and API on one origin for the single-instance MVP."""
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .contracts import ROOT


def install_frontend(app):
    directory = Path(os.getenv('TIREK_STATIC_DIR', str(ROOT / 'frontend' / 'dist'))).resolve()
    if not (directory / 'index.html').is_file():
        return

    @app.get('/{path:path}', include_in_schema=False)
    def frontend(path: str):
        # Reject Windows UNC/device/drive paths before resolve(), which can itself
        # touch a remote share. Keep the containment check below for symlinks.
        if (path.startswith('/') or '\\' in path or ':' in path or '\x00' in path
                or '..' in path.split('/')):
            raise HTTPException(404)
        if path == 'api' or path.startswith('api/'):
            raise HTTPException(404)
        candidate = (directory / path).resolve()
        if not candidate.is_relative_to(directory):
            raise HTTPException(404)
        if not candidate.is_file():
            if Path(path).suffix:
                raise HTTPException(404)
            candidate = directory / 'index.html'
        headers = {'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin',
                   'X-Frame-Options': 'DENY',
                   'Cache-Control': 'public, max-age=31536000, immutable' if path.startswith('assets/') else 'no-cache',
                   'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                              "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
                                              "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"}
        return FileResponse(candidate, headers=headers)
