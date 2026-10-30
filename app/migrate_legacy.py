"""Read-only legacy import after a SQLite online backup. All items held for review."""
import argparse,json,sqlite3
from pathlib import Path
from contextlib import closing
from sqlalchemy import select
from . import v3_models as m
from .workflow import backup,digest,canonical

def migrate(db,path):
    path=Path(path).resolve();saved=backup(path)
    if not saved: return {'imported':0,'backup':None}
    counts=0
    with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as old:
        old.row_factory=sqlite3.Row
        source_map={}
        for row in old.execute('SELECT * FROM sources'):
            source=db.scalar(select(m.Source).where(m.Source.rss_url==row['url']))
            if not source:
                source=m.Source(name=row['name'],rss_url=row['url'],active=False,access_mode='rss_metadata',commercial_reuse_status='unknown');db.add(source);db.flush()
            source_map[row['id']]=source.id
        for row in old.execute('SELECT * FROM teams'):
            if db.scalar(select(m.Team).where(m.Team.slug==row['slug'])): continue
            league=db.scalar(select(m.League).where(m.League.name_ja==row['league']))
            if not league:
                league=m.League(slug='legacy-'+digest(row['league'])[:12],name_ja=row['league'],season='legacy-unverified');db.add(league);db.flush()
            db.add(m.Team(slug=row['slug'],league_id=league.id,name_ja=row['name'],name_original=row['name'],active=False))
        db.flush()
        for row in old.execute('SELECT * FROM raw_articles'):
            try: url=canonical(row['url'])
            except ValueError:
                db.add(m.PipelineError(record_type='legacy_raw',record_id=str(row['id']),stage='migration',error_code='invalid_source_url',details=json.dumps(dict(row),ensure_ascii=False)));continue
            if not db.scalar(select(m.RawArticle).where(m.RawArticle.canonical_url==url)):
                db.add(m.RawArticle(source_id=source_map.get(row['source_id']),canonical_url=url,title=row['title'],content_hash=digest(row['title']),ingest_status='error',last_error='Legacy publication date and permissions require review'))
        db.flush()
        for row in old.execute('SELECT articles.*, teams.slug AS team_slug FROM articles JOIN teams ON teams.id=articles.team_id'):
            key='legacy:'+str(row['id'])
            if db.scalar(select(m.Article).where(m.Article.article_id==key)): continue
            team=db.scalar(select(m.Team).where(m.Team.slug==row['team_slug']))
            if not team:
                # Preserve unmapped content as archival payload instead of guessing club identity.
                db.add(m.PipelineError(record_type='legacy_article',record_id=str(row['id']),stage='migration',error_code='unmapped_team',details=json.dumps(dict(row),ensure_ascii=False)));continue
            a=m.Article(article_id=key,slug='legacy-'+row['slug'],article_type=row['kind'],primary_team_id=team.id,status='needs_review',dedupe_key=key);db.add(a);db.flush()
            v=m.ArticleVersion(article_id=a.id,version_no=1,title=row['title'],summary=row['summary'],body_markdown=row['body'],model_id='legacy-unknown',prompt_version='legacy',content_hash=digest(row['body']),payload_json=json.dumps({'legacy':dict(row)},ensure_ascii=False));db.add(v);db.flush();a.current_version_id=v.id;counts+=1
    db.commit();return {'imported':counts,'backup':saved,'status':'needs_review; legacy verification not inherited'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--file',default='data/football.db');a=p.parse_args();_,Session=m.connect()
    with Session() as db: print(json.dumps(migrate(db,a.file),ensure_ascii=False))
