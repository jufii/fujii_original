"""Confirm a deployed manifest before recording live publication."""
import argparse,json,os
from pathlib import Path
import requests
from sqlalchemy import select
from . import v3_models as m
from .workflow import canonical,utc

def confirm(db,dist,origin):
    manifest=json.loads((Path(dist)/'release.json').read_text())
    origin=canonical(origin).rstrip('/')
    response=requests.get(origin+'/release.json',timeout=20,headers={'Cache-Control':'no-cache'});response.raise_for_status()
    if response.json()!=manifest: raise ValueError('stale deployment manifest; no publication state changed')
    for slug in manifest['articles']:
        article=db.scalar(select(m.Article).where(m.Article.slug==slug))
        if not article or article.status not in ('approved','published') or utc(article.scheduled_at)>m.utcnow(): raise ValueError('article changed or not due')
        version=db.get(m.ArticleVersion,article.current_version_id)
        if manifest.get('versions',{}).get(slug)!=version.content_hash: raise ValueError('version changed after build')
        response=requests.get(origin+'/news/'+slug+'/',timeout=20);response.raise_for_status()
        import html
        if html.escape(version.title) not in response.text: raise ValueError('article URL does not contain expected title')
    for slug in manifest['articles']:
        article=db.scalar(select(m.Article).where(m.Article.slug==slug))
        pub=db.scalar(select(m.Publication).where(m.Publication.article_id==article.id))
        if not pub:
            pub=m.Publication(article_id=article.id,target='cloudflare-pages',scheduled_at=article.scheduled_at);db.add(pub)
        article.status='published';pub.status='live';pub.released_at=pub.released_at or m.utcnow();pub.deployment_id=manifest['built_at'];pub.canonical_url=origin+'/news/'+slug+'/'
    db.add(m.PipelineRun(run_key='release:'+manifest['built_at'],stage='release',status='completed',finished_at=m.utcnow(),counts_json=json.dumps(manifest))) if not db.scalar(select(m.PipelineRun).where(m.PipelineRun.run_key=='release:'+manifest['built_at'])) else None
    db.commit();return {'confirmed':len(manifest['articles'])}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['confirm']);p.add_argument('--dist',default='frontend/dist');p.add_argument('--origin',default=os.getenv('SITE_URL'));a=p.parse_args()
    _,Session=m.connect()
    with Session() as db: print(json.dumps(confirm(db,a.dist,a.origin)))
