"""Fail closed before uploading any public bundle."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
root=Path(sys.argv[1]).resolve()
rows=json.loads((root/'articles.json').read_text(encoding='utf-8'))
for row in rows:
    if datetime.fromisoformat(row['published_at']).astimezone(timezone.utc)>datetime.now(timezone.utc): raise SystemExit('future article in public bundle')
    if any(key in row for key in ('original_text','payload_json','verification_results')): raise SystemExit('private article fields in public bundle')
for path in root.rglob('*'):
    if not path.is_file(): continue
    if path.suffix in ('.db','.sqlite','.py','.toml') or path.name.startswith('.env'): raise SystemExit('private file in public bundle')
    if path.suffix in ('.html','.json','.js','.txt','.xml'):
        text=path.read_text(encoding='utf-8')
        if any(marker in text for marker in ('postgresql+psycopg://','OPENAI_API_KEY=','CLOUDFLARE_API_TOKEN=','art_v2_')): raise SystemExit('credential marker in public bundle')
print(json.dumps({'public_articles':len(rows),'checked_files':sum(p.is_file() for p in root.rglob('*'))}))
