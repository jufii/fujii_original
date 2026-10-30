"""Local ingestion and preview. Never republishes publisher full text."""
import argparse, hashlib, json, os, re, sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import requests, xml.etree.ElementTree as ET
from types import SimpleNamespace
from sqlalchemy import select
from .db import SessionLocal, Article, RawArticle, Source, Team, PipelineRun, init_db

BASE = Path(__file__).resolve().parent.parent
SOURCES = BASE / "config" / "sources.json"
TEAM_RULES = {
    'barcelona':('FCバルセロナ','ラ・リーガ',['barcelona','barça','barca','blaugrana','lamine yamal','hansi flick'], '#a12c50'),
    'real-madrid':('レアル・マドリード','ラ・リーガ',['real madrid','madrid','mbappé','vinicius'], '#a9a3a3'),
    'atletico':('アトレティコ','ラ・リーガ',['atlético','atletico','simeone'], '#be3542'),
    'arsenal':('アーセナル','プレミアリーグ',['arsenal','arteta'], '#d83b3b'),
    'liverpool':('リヴァプール','プレミアリーグ',['liverpool','anfield'], '#d32c3b'),
    'man-city':('マンチェスター・シティ','プレミアリーグ',['manchester city','man city','guardiola'], '#65add9'),
    'inter':('インテル','セリエA',['inter milan','internazionale'], '#3265ae'),
    'juventus':('ユヴェントス','セリエA',['juventus','juve'], '#20252c'),
    'ac-milan':('ACミラン','セリエA',['ac milan','rossoneri'], '#9c2d3b'),
    'bayern':('バイエルン','ブンデスリーガ',['bayern','bavarians'], '#c73545'),
    'dortmund':('ドルトムント','ブンデスリーガ',['borussia dortmund','dortmund','bvb'], '#dfbc29'),
    'psg':('パリ・サンジェルマン','リーグ・アン',['paris saint-germain','paris saint germain','psg'], '#3c4d91'),
    'marseille':('マルセイユ','リーグ・アン',['marseille','olympique de marseille'], '#61b2dd')
}
def clean(value):
    import html
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(value or ''))).strip()[:1600]
def normalize_url(u):
    s=urlsplit(u); return urlunsplit((s.scheme,s.netloc,s.path.rstrip('/'),'',''))
def classify(title,desc):
    txt=(title+' '+desc).lower()
    return [slug for slug,(_,_,words,_) in TEAM_RULES.items() if any(w in txt for w in words)]
def ai_rewrite(title,desc,team,source):
    raise RuntimeError('API generation disabled. Use app.workflow packet and Codex JSON handoff.')


def parse_feed(payload):
    """Parse basic RSS2/Atom feeds without additional pip dependencies."""
    if len(payload)>2_000_000 or b'<!DOCTYPE' in (payload.encode() if isinstance(payload,str) else payload).upper(): raise ValueError('unsafe or oversized RSS')
    root=ET.fromstring(payload)
    ns={'atom':'http://www.w3.org/2005/Atom','dc':'http://purl.org/dc/elements/1.1/','content':'http://purl.org/rss/1.0/modules/content/'}
    items=root.findall('./channel/item')
    atom=False
    if not items:
        items=root.findall('atom:entry',ns);atom=True
    result=[]
    for it in items:
        def get(name): return (it.findtext(name,default='',namespaces=ns) or '').strip()
        if atom:
            links=it.findall('atom:link',ns)
            link=next((l.get('href','') for l in links if l.get('rel','alternate')=='alternate'),links[0].get('href','') if links else '')
            desc=get('atom:summary') or get('atom:content')
            title=get('atom:title')
            published=get('atom:published') or get('atom:updated')
        else:
            link=get('link');title=get('title');desc=get('description')
            published=get('pubDate') or get('dc:date')
        result.append({'title':title,'summary':desc,'link':link,'published':published})
    return result

def parse_published(value):
    if not value: return None
    try:
        d=parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try: d=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError: return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

def run_daily(demo=False,max_entries=20,days=3):
    if not demo: raise RuntimeError('Legacy daily generation disabled. Use app.workflow collect/packet and Codex.')
    init_db(); now=datetime.now(timezone.utc); counters={'sources_ok':0,'sources_failed':0,'raw_new':0,'drafts_new':0,'skipped':0,'errors':[]}
    sources=json.loads(SOURCES.read_text(encoding='utf-8'))
    with SessionLocal() as db:
        for slug,(name,league,_,color) in TEAM_RULES.items():
            if not db.scalar(select(Team).where(Team.slug==slug)): db.add(Team(name=name,slug=slug,league=league,color=color))
        db.commit()
        for cfg in sources:
            if not cfg.get('enabled'): continue
            source=db.scalar(select(Source).where(Source.url==cfg['feed']))
            if not source: source=Source(name=cfg['name'],url=cfg['feed'],enabled=True); db.add(source);db.flush();db.commit()
            try:
                response=requests.get(cfg['feed'],timeout=15,headers={'User-Agent':'TouchlineLocalTester/0.1 (contact: local-development)'})
                response.raise_for_status()
                feed_entries=parse_feed(response.content)
                if not feed_entries: raise ValueError('No RSS/Atom entries')
                counters['sources_ok']+=1
            except Exception as exc:
                counters['sources_failed']+=1;counters['errors'].append(f"{cfg['name']}: {exc}");continue
            for entry in feed_entries[:max_entries]:
                published=parse_published(entry.get('published'))
                if published is not None and published < now-timedelta(days=days):
                    counters['skipped']+=1;continue
                title=clean(entry.get('title',''));desc=clean(entry.get('summary',''))
                url=normalize_url(entry.get('link',''))
                if not title or not url.startswith('https://'): counters['skipped']+=1;continue
                if db.scalar(select(RawArticle).where(RawArticle.url==url)): counters['skipped']+=1;continue
                db.add(RawArticle(source_id=source.id,url=url,title=title));db.commit();counters['raw_new']+=1
                clubs=classify(title,desc)
                if not clubs: continue
                for slug in clubs:
                    team=db.scalar(select(Team).where(Team.slug==slug))
                    article_slug='rss-'+hashlib.sha256((url+'|'+slug).encode()).hexdigest()[:18]
                    if db.scalar(select(Article).where(Article.slug==article_slug)):continue
                    result=None
                    if not demo:
                        try: result=ai_rewrite(title,desc,team.name,cfg['name'])
                        except Exception as exc: counters['errors'].append(f'AI {url}: {exc}')
                    if not result:
                        # Explicitly source-title preview, NOT a fabricated Japanese translation.
                        result={'title':f'【要確認・原題】{title}'[:245],
                                'summary':f'RSSから取得した原題：{title}。出典：{cfg["name"]}。全文取得・日本語翻訳はしていません。',
                                'body':f'出典：{cfg["name"]}\n原題：{title}\nRSS概要：{desc}\n\nこのページはローカル検証用の下書きです。権利確認と記事校閲前に公開しないでください。'}
                    db.add(Article(slug=article_slug,title=result['title'][:245],summary=result['summary'],body=result['body'],team_id=team.id,
                                   kind='individual',topic='RSSニュース',status='draft',is_demo=True,source_url=url,published_at=now))
                    counters['drafts_new']+=1
                    db.commit()
        key='local-'+now.strftime('%Y%m%d-%H%M%S-%f')
        db.add(PipelineRun(run_key=key,status='completed' if counters['sources_ok'] else 'failed',detail=json.dumps(counters,ensure_ascii=False)))
        db.commit()
    print(json.dumps(counters,indent=2,ensure_ascii=False))
    (BASE/'data').mkdir(exist_ok=True)
    (BASE/'data'/'last_run.json').write_text(json.dumps(counters,indent=2,ensure_ascii=False),encoding='utf-8')
    return counters

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['daily','test']);p.add_argument('--max-entries',type=int,default=20);p.add_argument('--days',type=int,default=3);a=p.parse_args()
    run_daily(demo=(a.command=='test'),max_entries=a.max_entries,days=a.days)
