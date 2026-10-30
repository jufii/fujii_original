"""Render a shareable static HTML preview (uses DB to render)."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    response=client.get('/')
    assert response.status_code==200
    html=response.text.replace('href="/static/site.css"','href="site.css"')
    html=html.replace('href="/','href="https://example.invalid/')
    html=html.replace('href="https://example.invalid/"','href="./preview.html"')
    output=Path('preview.html')
    output.write_text(html,encoding='utf8')
    Path('site.css').write_bytes(Path('static/site.css').read_bytes())
print('preview.html created')
