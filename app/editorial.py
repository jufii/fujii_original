"""Editorial assembly, conservative approval gate and local publication.
No third-party full text is reproduced. Auto approval is opt-in.
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from hashlib import sha256
from sqlalchemy import select
from .db import SessionLocal, Team, Article, PipelineRun, init_db

JST = ZoneInfo('Asia/Tokyo')

def now_utc():
    return datetime.now(timezone.utc)

def local_day(s=None):
    return (datetime.fromisoformat(s).date() if s else datetime.now(JST).date())

def eligible(a):
    """Conservative deterministic gate: no demo, no placeholder, source required.
    Manual review still required by default, see review_article().
    """
    problems=[]
    if a.is_demo: problems.append('demo_or_unverified_content')
    if not a.source_url or not a.source_url.startswith('https://'): problems.append('missing_source')
    if a.title.startswith('【要確認') or '全文取得・日本語翻訳はしていません' in a.summary: problems.append('source_only_preview')
    if len(a.summary.strip()) < 55 or len(a.body.strip()) < 150: problems.append('insufficient_original_content')
    if not (a.title.strip() and a.summary.strip() and a.body.strip()): problems.append('empty_content')
    return problems

def build_dailies(date=None, min_items=2):
    """Make summary drafts from existing individual entries, repeatably.
    Articles remain demo until each source and content has been verified.
    """
    init_db()
    day=local_day(date)
    from_local=datetime.combine(day,datetime.min.time(),tzinfo=JST).astimezone(timezone.utc)
    to_local=from_local+timedelta(days=1)
    report={'date':str(day),'created':0,'updated':0,'teams':[]}
    with SessionLocal() as db:
        teams=db.scalars(select(Team)).all()
        for team in teams:
            entries=db.scalars(select(Article).where(Article.team_id==team.id,Article.kind=='individual',Article.published_at>=from_local-timedelta(days=1), Article.published_at<to_local,Article.source_url.is_not(None)).order_by(Article.id.desc())).all()
            seen=set(); chosen=[]
            for a in entries:
                if a.source_url in seen: continue
                seen.add(a.source_url);chosen.append(a)
            if len(chosen)<min_items: continue
            slug=f'{team.slug}-daily-{day:%Y%m%d}'
            heading=f'{team.name}ニュースまとめ｜{day:%Y年%m月%d日}'
            # Only quote limited headline metadata and link to sources; no copied full-text.
            paragraphs=[]
            for a in chosen[:8]:
                title=re.sub(r'^【要確認・原題】','',a.title)
                paragraphs.append(f'・{title}\n出典：{a.source_url}')
            summary=f'{team.name}について、参照元が確認できるニュース{len(chosen[:8])}件をまとめたローカル確認用の索引です。本文は編集・事実確認後に公開します。'
            body='\n\n'.join(paragraphs)+'\n\n※これはローカル確認用の索引です。元媒体記事の全文翻訳ではありません。'
            existing=db.scalar(select(Article).where(Article.slug==slug))
            if existing:
                if existing.status=='published': continue
                existing.title=heading;existing.summary=summary;existing.body=body
                report['updated']+=1
            else:
                db.add(Article(slug=slug,title=heading,summary=summary,body=body,team_id=team.id,kind='daily',topic='朝刊',status='draft',is_demo=True,source_url=chosen[0].source_url,published_at=from_local+timedelta(hours=7)))
                report['created']+=1
            report['teams'].append(team.slug)
        db.commit()
    return report

def mark_checked(slug, reviewer='local-editor'):
    """Manual attestation AFTER confirming factual accuracy, source and rights.
    Must not be exposed as a public endpoint.
    """
    init_db()
    with SessionLocal() as db:
        a=db.scalar(select(Article).where(Article.slug==slug))
        if not a: raise ValueError('unknown article slug')
        if a.title.startswith('【要確認') or '全文取得・日本語翻訳はしていません' in a.summary:
            raise ValueError('replace source-only preview with independently reviewed Japanese article first')
        if not a.source_url: raise ValueError('missing source')
        a.is_demo=False
        db.add(PipelineRun(run_key='checked-'+sha256((slug+str(now_utc().timestamp())).encode()).hexdigest()[:24],status='checked',detail=json.dumps({'slug':slug,'reviewer':reviewer},ensure_ascii=False)))
        db.commit()
    return {'slug':slug,'verified_by':reviewer,'status':'checked_needs_approval'}

def review_article(slug, approved, reviewer='local-editor'):
    """Explicit human approval; no public endpoint to mutate state."""
    init_db()
    with SessionLocal() as db:
        article=db.scalar(select(Article).where(Article.slug==slug))
        if not article: raise ValueError('unknown article slug')
        if approved:
            failures=eligible(article)
            if failures: raise ValueError('cannot approve: '+', '.join(failures))
            article.status='approved'
        else:
            article.status='draft'
        db.add(PipelineRun(run_key='review-'+sha256((slug+str(now_utc().timestamp())).encode()).hexdigest()[:24],status='approved' if approved else 'rejected',detail=json.dumps({'article':slug,'reviewer':reviewer},ensure_ascii=False)))
        db.commit()
    return {'slug':slug,'status':'approved' if approved else 'draft'}

def publish_due(at=None, dry_run=True):
    """Publish only human-approved, non-demo articles with valid source.
    Scheduled timestamp is UTC in DB. Idempotent on repeat calls.
    """
    init_db()
    now=at or now_utc()
    result={'time':now.isoformat(),'dry_run':dry_run,'published':[],'blocked':[],'pending':0}
    with SessionLocal() as db:
        articles=db.scalars(select(Article).where(Article.status=='approved')).all()
        for a in articles:
            due=a.published_at.replace(tzinfo=timezone.utc) if a.published_at.tzinfo is None else a.published_at
            if due>now:
                result['pending']+=1;continue
            problems=eligible(a)
            if problems:
                result['blocked'].append({'slug':a.slug,'reasons':problems});continue
            result['published'].append(a.slug)
            if not dry_run: a.status='published'
        if not dry_run: db.commit()
    return result

def list_review():
    init_db()
    with SessionLocal() as db:
        articles=db.scalars(select(Article).where(Article.status.in_(['draft','approved'])).order_by(Article.id.desc())).all()
        return [{'slug':a.slug,'title':a.title,'type':a.kind,'state':a.status,'blocks':eligible(a)} for a in articles]

def cli():
    p=argparse.ArgumentParser(description='Local editorial review and publication')
    p.add_argument('command',choices=['daily','review-list','mark-checked','approve','reject','publish'])
    p.add_argument('--date');p.add_argument('--slug');p.add_argument('--at');p.add_argument('--commit',action='store_true')
    args=p.parse_args()
    if args.command=='daily': result=build_dailies(args.date)
    elif args.command=='review-list': result=list_review()
    elif args.command=='mark-checked':
        if not args.slug: p.error('--slug required')
        result=mark_checked(args.slug)
    elif args.command in ['approve','reject']:
        if not args.slug: p.error('--slug required')
        result=review_article(args.slug,args.command=='approve')
    else: result=publish_due(datetime.fromisoformat(args.at) if args.at else None,dry_run=not args.commit)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__': cli()
