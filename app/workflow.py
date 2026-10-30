"""Codex handoff and release CLI. No model API or hidden generation fallback."""
import argparse, hashlib, html, json, os, re, shutil, sqlite3, tempfile, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import requests, yaml
from contextlib import closing
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, field_validator, model_validator
from sqlalchemy import select, func
from . import v3_models as m

ROOT=Path(__file__).resolve().parents[1]
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def utc(d): return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
def canonical(url):
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password: raise ValueError('https source URL required')
    return urlunsplit(('https',p.netloc.lower(),p.path.rstrip('/'),urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.startswith('utm_') and k not in ('fbclid','gclid')]),''))
def clean(s): return re.sub(r'\s+',' ',re.sub('<[^>]*>',' ',html.unescape(s or ''))).strip()[:1600]

class Claim(BaseModel):
    model_config=ConfigDict(extra='forbid')
    event_id:int
    text:str=Field(min_length=5)
    source_url:str
    evidence:str=Field(min_length=5)
    certainty:str=Field(pattern='^(confirmed|reported|uncertain)$')

class Generated(BaseModel):
    model_config=ConfigDict(extra='forbid')
    article_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    article_type:str=Field(pattern='^(daily|individual)$')
    title:str=Field(min_length=8,max_length=180)
    slug:str=Field(pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$',max_length=160)
    league:str
    teams:list[str]=Field(min_length=1)
    topic:str
    published_at:AwareDatetime
    body:str=Field(min_length=150,max_length=20000)
    summary:str=Field(min_length=30,max_length=500)
    source_urls:list[str]=Field(min_length=1)
    event_ids:list[int]=Field(min_length=1)
    claims:list[Claim]=Field(min_length=1)
    verification_status:str=Field(pattern='^(pending|needs_review)$')
    publication_status:str=Field(pattern='^(draft|needs_review)$')
    content_mode:str=Field(default='summary',pattern='^(summary|translation)$')
    original_title:str|None=None
    original_publisher:str|None=None
    original_author:str|None=None
    original_published_at:AwareDatetime|None=None
    original_text:str|None=Field(default=None,max_length=100000)
    @model_validator(mode='after')
    def translation_metadata(self):
        if self.content_mode=='translation':
            if self.article_type!='individual' or len(self.source_urls)!=1 or len(self.event_ids)!=1:
                raise ValueError('translation requires one individual article and one original source/event')
            if not all((self.original_title,self.original_publisher,self.original_published_at,self.original_text)):
                raise ValueError('original title, publisher, publication date and licensed original text required')
            if len(self.original_text.strip())<100: raise ValueError('RSS headline/snippet is not a complete original article')
        return self
    model_id:str='codex-session' 
    prompt_version:str='v3.1'
    @field_validator('source_urls')
    @classmethod
    def urls(cls, v): return sorted(set(canonical(u) for u in v))
    @field_validator('body','title','summary')
    @classmethod
    def japanese(cls,v):
        if not re.search('[ぁ-んァ-ヶ一-龠]',v): raise ValueError('Japanese text required')
        return v

def bootstrap(db):
    config=yaml.safe_load((ROOT/'config/sources.yaml').read_text(encoding='utf-8'))
    for cfg in config['sources']:
        source=db.scalar(select(m.Source).where(m.Source.rss_url==cfg['rss_url']))
        if source is None:
            source=m.Source(**cfg);db.add(source)
        # Existing human-reviewed policy is deliberately never overwritten by config.
    roster=yaml.safe_load((ROOT/'config/teams.yaml').read_text(encoding='utf-8'))
    for league in roster['leagues']:
        row=db.scalar(select(m.League).where(m.League.slug==league['slug']))
        if not row: row=m.League(slug=league['slug'],name_ja=league['name_ja'],season=roster['season']);db.add(row);db.flush()
        for team in league['teams']:
            t=db.scalar(select(m.Team).where(m.Team.slug==team['slug']))
            if not t: t=m.Team(slug=team['slug'],league_id=row.id,name_ja=team['name_ja'],name_original=team['name']);db.add(t);db.flush()
            for alias in set([team['name'],team['name_ja']]+team.get('aliases',[])):
                if not db.scalar(select(m.TeamAlias).where(m.TeamAlias.team_id==t.id,m.TeamAlias.alias==alias)):
                    db.add(m.TeamAlias(team_id=t.id,alias=alias,language='mixed'))
    db.commit()

def classify(db,title):
    text=title.casefold()
    if re.search(r"women|wsl|fémin|frauen|femen|女子|u-?\d{2}|under-?\d{2}|youth|academy",text): return []
    matches=[]
    for a in db.scalars(select(m.TeamAlias)):
        for match in re.finditer(r'(?<!\w)'+re.escape(a.alias.casefold())+r'(?!\w)',text): matches.append((match.start(),match.end(),a.team_id))
    chosen=[]
    for start,end,tid in sorted(matches,key=lambda x:x[1]-x[0],reverse=True):
        if not any(start<other_end and end>other_start for other_start,other_end,_ in chosen): chosen.append((start,end,tid))
    return sorted({tid for _,_,tid in chosen})

def topic(title):
    for kind,score,words in [('transfer',90,r'(?i)\b(signs|signing|transfer|joins)\b'),('manager',90,r'(?i)\b(sacked|appointed|appointment)\b'),('injury',80,r'(?i)\b(injury|injured|return)\b'),('result',80,r'(?i)\b(wins?|beats?|defeat|shootout|hat-trick)\b|\d-\d'),('conference',65,r'(?i)\b(conference|speaks)\b')]:
        if re.search(words,title): return kind,score
    return 'news',40

def collect(db, fixture=None, hours=24, limit=30):
    from .pipeline import parse_feed,parse_published
    now=m.utcnow(); counts={'new':0,'duplicates':0,'skipped':0,'failures':0,'blocked_sources':0}
    run=m.PipelineRun(run_key=str(uuid.uuid4()),stage='collect',status='running');db.add(run);db.commit()
    sources=db.scalars(select(m.Source).where(m.Source.active==True)).all()
    if os.getenv('APP_ENV')=='production':
        eligible=[source for source in sources if source.commercial_reuse_status=='permitted' and source.license_reference and source.checked_at and source.reviewed_by]
        counts['blocked_sources']=len(sources)-len(eligible);sources=eligible
    counts['eligible_sources']=len(sources)
    if fixture: sources=sources[:1]
    for source in sources:
        try:
            if fixture: payload=Path(fixture).read_bytes()
            else:
                response=requests.get(source.rss_url,timeout=20,headers={'User-Agent':'Touchline/3.1 RSS-metadata-local-test'})
                response.raise_for_status();payload=response.content
            entries=parse_feed(payload)
            if not entries: raise ValueError('empty feed')
            for item in entries[:limit]:
                published=parse_published(item['published'])
                if not published or not now-timedelta(hours=hours)<=published<=now: counts['skipped']+=1;continue
                url=canonical(item['link']); title=clean(item['title'])
                if not fixture and urlsplit(url).hostname!=urlsplit(source.base_url).hostname: counts['skipped']+=1;continue
                if db.scalar(select(m.RawArticle).where(m.RawArticle.canonical_url==url)): counts['duplicates']+=1;continue
                raw=m.RawArticle(source_id=source.id,canonical_url=url,title=title,snippet=clean(item['summary']),source_published_at=published,language=source.language,content_hash=digest(title),ingest_status='normalized')
                db.add(raw);db.flush();counts['new']+=1
                teams=classify(db,title+' '+raw.snippet);kind,score=topic(title)
                # Conservative: exact normalized headline merge only; uncertain matches stay separate/held.
                normalized=re.sub(r'[^\w ]','',title.casefold())
                key=digest(json.dumps([teams,kind,published.date().isoformat(),normalized]))
                event=db.scalar(select(m.NewsEvent).where(m.NewsEvent.fingerprint==key))
                if not event:
                    event=m.NewsEvent(fingerprint=key,subject=title,event_type=kind,event_time=published,importance_score=score,confidence=0.5,status='clustered' if teams else 'held');db.add(event);db.flush()
                    for tid in teams: db.add(m.EventTeam(event_id=event.id,team_id=tid))
                db.add(m.EventSource(event_id=event.id,raw_article_id=raw.id))
            source.last_success_at=now;db.commit()
            for error in db.scalars(select(m.PipelineError).where(m.PipelineError.record_type=='source',m.PipelineError.record_id==str(source.id),m.PipelineError.resolved_at.is_(None))):
                error.retry_count=(error.retry_count or 0)+1;error.resolved_at=now
            db.commit()
        except Exception as exc:
            db.rollback();counts['failures']+=1
            db.add(m.PipelineError(run_id=run.id,record_type='source',record_id=str(source.id),stage='collect',error_code=type(exc).__name__,details=str(exc)[:1000]));db.commit()
    run=db.get(m.PipelineRun,run.id);run.status='failed' if counts['failures'] else 'needs_configuration' if not sources else 'completed';run.finished_at=m.utcnow();run.counts_json=json.dumps(counts);db.commit()
    return counts

def reclassify(db):
    """Re-evaluate raw observations after catalog/policy changes; preserve event IDs."""
    from sqlalchemy import delete
    count=0
    for event in db.scalars(select(m.NewsEvent)):
        if event.status=='merged': continue
        raws=db.scalars(select(m.RawArticle).join(m.EventSource,m.EventSource.raw_article_id==m.RawArticle.id).where(m.EventSource.event_id==event.id)).all()
        if os.getenv('APP_ENV')=='production' and any(db.get(m.Source,r.source_id).commercial_reuse_status!='permitted' for r in raws): continue
        teams=set()
        for raw in raws: teams.update(classify(db,raw.title+' '+raw.snippet))
        db.execute(delete(m.EventTeam).where(m.EventTeam.event_id==event.id))
        for tid in teams: db.add(m.EventTeam(event_id=event.id,team_id=tid))
        event.event_type,event.importance_score=topic(event.subject);event.status='clustered' if teams else 'held';event.updated_at=m.utcnow();count+=1
    db.commit();return {'reclassified':count}

def packet(db, output):
    now=m.utcnow(); events=[]
    for event in db.scalars(select(m.NewsEvent).where(m.NewsEvent.status=='clustered',m.NewsEvent.event_time>=now-timedelta(hours=24)).order_by(m.NewsEvent.importance_score.desc())):
        raws=db.scalars(select(m.RawArticle).join(m.EventSource,m.EventSource.raw_article_id==m.RawArticle.id).where(m.EventSource.event_id==event.id)).all()
        if os.getenv('APP_ENV')=='production' and any(db.get(m.Source,r.source_id).commercial_reuse_status!='permitted' for r in raws): continue
        teams=db.scalars(select(m.Team).join(m.EventTeam,m.EventTeam.team_id==m.Team.id).where(m.EventTeam.event_id==event.id)).all()
        events.append({'event_id':event.id,'topic':event.event_type,'importance':event.importance_score,'individual_candidate':event.importance_score>=80,'teams':[t.slug for t in teams],'sources':[{'url':r.canonical_url,'title':r.title,'snippet':r.snippet,'published_at':utc(r.source_published_at).isoformat(),'reuse_status':db.get(m.Source,r.source_id).commercial_reuse_status} for r in raws]})
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'packet.json').write_text(json.dumps({'generated_at':now.isoformat(),'events':events},ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'schema.json').write_text(json.dumps(Generated.model_json_schema(),ensure_ascii=False,indent=2),encoding='utf-8')
    return {'events':len(events),'output':str(output)}

def merge_events(db,path,reviewer,note):
    """Explicit reviewed merge; retain original observations and audit record."""
    decisions=json.loads(Path(path).read_text(encoding='utf-8'))
    if not reviewer or len(note or '')<20: raise ValueError('reviewer and substantive event identity evidence required')
    merged=0
    for decision in decisions:
        primary=db.get(m.NewsEvent,decision['primary']);other=db.get(m.NewsEvent,decision['duplicate'])
        if not primary or not other or primary.id==other.id: raise ValueError('invalid merge event IDs')
        if db.scalar(select(m.ArticleEvent).where(m.ArticleEvent.event_id.in_([primary.id,other.id]))): raise ValueError('merge before drafting; existing articles require editorial resolution')
        teams=lambda eid:set(db.scalars(select(m.EventTeam.team_id).where(m.EventTeam.event_id==eid)))
        if not teams(primary.id) or teams(primary.id)!=teams(other.id) or primary.event_type!=other.event_type or abs((utc(primary.event_time)-utc(other.event_time)).total_seconds())>86400: raise ValueError('event identity constraints do not match')
        for link in db.scalars(select(m.EventSource).where(m.EventSource.event_id==other.id)):
            if not db.get(m.EventSource,(primary.id,link.raw_article_id)): db.add(m.EventSource(event_id=primary.id,raw_article_id=link.raw_article_id))
        other.status='merged';primary.updated_at=m.utcnow();merged+=1
    db.add(m.PipelineRun(run_key=str(uuid.uuid4()),stage='event-merge',status='completed',finished_at=m.utcnow(),counts_json=json.dumps({'decisions':decisions,'reviewer':reviewer,'note':note})));db.commit()
    return {'merged':merged}

def verify(db,p):
    problems=[]; linked={};allowed_teams=set(); rights=True
    for eid in p.event_ids:
        event=db.get(m.NewsEvent,eid)
        if not event: problems.append('unknown_event');continue
        if p.article_type=='individual' and event.importance_score<80: problems.append('below_importance_threshold')
        if p.article_type=='daily' and not utc(p.published_at)-timedelta(hours=24)<=utc(event.event_time)<=utc(p.published_at): problems.append('outside_daily_window')
        allowed_teams.update(db.scalars(select(m.Team.slug).join(m.EventTeam,m.EventTeam.team_id==m.Team.id).where(m.EventTeam.event_id==eid)).all())
        for r in db.scalars(select(m.RawArticle).join(m.EventSource,m.EventSource.raw_article_id==m.RawArticle.id).where(m.EventSource.event_id==eid)):
            linked[(eid,r.canonical_url)]=r
            source=db.get(m.Source,r.source_id)
            if source.commercial_reuse_status!='permitted' or not source.license_reference or not source.checked_at or not source.reviewed_by: rights=False
    if not set(p.teams)<=allowed_teams: problems.append('team_mismatch')
    for slug in p.teams:
        team=db.scalar(select(m.Team).where(m.Team.slug==slug))
        if not team or db.get(m.League,team.league_id).slug!=p.league: problems.append('league_mismatch')
    if set(p.source_urls)!={u for _,u in linked}: problems.append('source_mismatch')
    if set(p.event_ids)!={c.event_id for c in p.claims}: problems.append('missing_event_claim')
    if p.content_mode=='translation':
        for raw in linked.values():
            source=db.get(m.Source,raw.source_id)
            if source.access_mode!='licensed_fulltext': rights=False;problems.append('translation_permission_required')
            if not raw.source_published_at or utc(raw.source_published_at)!=utc(p.original_published_at): problems.append('original_date_mismatch')
    for claim in p.claims:
        raw=linked.get((claim.event_id,canonical(claim.source_url)))
        evidence_text=p.original_text if p.content_mode=='translation' else (raw.title+' '+(raw.snippet or '') if raw else '')
        if not raw or clean(claim.evidence) not in re.sub(r'\s+',' ',re.sub('<[^>]*>',' ',html.unescape(evidence_text or ''))).strip(): problems.append('unsupported_evidence')
        if claim.certainty=='uncertain' or (p.content_mode!='translation' and claim.certainty!='confirmed'): problems.append('uncertain_claim')
    # Lexical evidence matching does not establish semantic truth or validate all body claims.
    return {'problems':sorted(set(problems)), 'rights':'pass' if rights else 'blocked','factual':'needs_review','quality':'pass' if not problems else 'blocked'}

def ingest(db,path):
    p=Generated.model_validate_json(Path(path).read_text(encoding='utf-8'))
    result=verify(db,p)
    team=db.scalar(select(m.Team).where(m.Team.slug==p.teams[0]))
    if not team: raise ValueError('unknown primary team')
    key=('daily:'+p.teams[0]+':'+p.published_at.astimezone(timezone(timedelta(hours=9))).date().isoformat()) if p.article_type=='daily' else 'event:'+','.join(map(str,sorted(p.event_ids)))
    if p.article_type=='individual' and len(p.event_ids)!=1: raise ValueError('individual requires exactly one event')
    a=db.scalar(select(m.Article).where(m.Article.dedupe_key==key))
    if a and (a.article_id!=p.article_id or a.slug!=p.slug): raise ValueError('duplicate event or edition under different identity')
    content_hash=digest(p.model_dump_json())
    if a and db.get(m.ArticleVersion,a.current_version_id).content_hash==content_hash: return {'status':'unchanged','id':a.id}
    if a and a.status in ('published','approved','scheduled'): raise ValueError('withdraw before revising an approved article')
    if a:
        from sqlalchemy import delete
        db.execute(delete(m.ArticleEvent).where(m.ArticleEvent.article_id==a.id))
        for eid in p.event_ids:
            if db.get(m.NewsEvent,eid): db.add(m.ArticleEvent(article_id=a.id,event_id=eid))
    if not a:
        a=m.Article(article_id=p.article_id,slug=p.slug,article_type=p.article_type,primary_team_id=team.id,scheduled_at=p.published_at,edition_date=p.published_at.date().isoformat(),dedupe_key=key);db.add(a);db.flush()
        for eid in p.event_ids:
            if db.get(m.NewsEvent,eid): db.add(m.ArticleEvent(article_id=a.id,event_id=eid))
    version_no=(db.scalar(select(func.max(m.ArticleVersion.version_no)).where(m.ArticleVersion.article_id==a.id)) or 0)+1
    v=m.ArticleVersion(article_id=a.id,version_no=version_no,title=p.title,summary=p.summary,body_markdown=p.body,model_id=p.model_id,prompt_version=p.prompt_version,content_hash=content_hash,payload_json=p.model_dump_json());db.add(v);db.flush()
    for claim in p.claims:
        if db.get(m.NewsEvent,claim.event_id) and not db.scalar(select(m.EventFact).where(m.EventFact.event_id==claim.event_id,m.EventFact.fact_text==claim.text)):
            db.add(m.EventFact(event_id=claim.event_id,fact_text=claim.text,certainty=claim.certainty,fact_type=p.topic,evidence_ref=json.dumps({'url':claim.source_url,'excerpt':claim.evidence},ensure_ascii=False),verification_status='pending'))
    a.current_version_id=v.id;a.status='needs_review';a.scheduled_at=p.published_at
    db.add(m.VerificationResult(article_version_id=v.id,checker_version='v3.1',factual_status=result['factual'],rights_status=result['rights'],quality_status=result['quality'],details_json=json.dumps(result)))
    db.commit();return {'id':a.id,'status':a.status,**result}

def approve(db,slug,reviewer,note):
    a=db.scalar(select(m.Article).where(m.Article.slug==slug))
    if not a: raise ValueError('unknown article')
    v=db.get(m.ArticleVersion,a.current_version_id);p=Generated.model_validate_json(v.payload_json);result=verify(db,p)
    if result['problems'] or result['rights']!='pass': raise ValueError('approval blocked: '+json.dumps(result))
    probe=db.scalar(select(m.VerificationResult).where(m.VerificationResult.article_version_id==v.id,m.VerificationResult.checker_version=='source-url-head').order_by(m.VerificationResult.id.desc()))
    if not probe or probe.quality_status!='pass' or m.utcnow()-utc(probe.checked_at)>timedelta(hours=24): raise ValueError('successful source URL check within 24 hours required')
    if not reviewer or len(note or '')<20: raise ValueError('human reviewer and substantive evidence/rights review note required')
    db.add(m.VerificationResult(article_version_id=v.id,checker_version='human:'+reviewer,factual_status='pass',rights_status='pass',quality_status='pass',details_json=json.dumps({'note':note,'version_hash':v.content_hash})))
    a.status='approved';db.commit();return {'status':'approved'}

def check_sources(db,slug):
    a=db.scalar(select(m.Article).where(m.Article.slug==slug))
    if not a: raise ValueError('unknown article')
    version=db.get(m.ArticleVersion,a.current_version_id);p=Generated.model_validate_json(version.payload_json)
    result=verify(db,p);statuses=[]
    for url in p.source_urls:
        try:
            response=requests.head(url,timeout=15,allow_redirects=True,headers={'User-Agent':'Touchline/3.1 source-link-check'})
            ok=response.status_code==200 and urlsplit(response.url).scheme=='https'
            statuses.append({'url':url,'status':response.status_code,'ok':ok})
        except requests.RequestException as exc: statuses.append({'url':url,'ok':False,'error':type(exc).__name__})
    passed=all(item['ok'] for item in statuses)
    db.add(m.VerificationResult(article_version_id=version.id,checker_version='source-url-head',factual_status='needs_review',rights_status=result['rights'],quality_status='pass' if passed else 'blocked',details_json=json.dumps({'urls':statuses,'note':'URL reachability alone proves neither publication date nor article facts.'})))
    db.commit();return {'urls':statuses,'passed':passed}

def import_with_log(db,path):
    run=m.PipelineRun(run_key=str(uuid.uuid4()),stage='ingest',status='running');db.add(run);db.commit();run_id=run.id
    record=digest(str(Path(path).resolve()))
    try:
        result=ingest(db,path)
    except Exception as exc:
        db.rollback();run=db.get(m.PipelineRun,run_id);run.status='failed';run.finished_at=m.utcnow()
        db.add(m.PipelineError(run_id=run_id,record_type='generated_file',record_id=record,stage='ingest',error_code=type(exc).__name__,details='Rejected generated JSON: '+Path(path).name));db.commit();raise
    run=db.get(m.PipelineRun,run_id);run.status='completed';run.finished_at=m.utcnow();run.counts_json=json.dumps(result)
    for error in db.scalars(select(m.PipelineError).where(m.PipelineError.record_type=='generated_file',m.PipelineError.record_id==record,m.PipelineError.resolved_at.is_(None))):
        error.retry_count=(error.retry_count or 0)+1;error.resolved_at=m.utcnow()
    db.commit();return result

def public_attribution(p):
    return dict(content_mode=p.content_mode,original_title=p.original_title,
                original_publisher=p.original_publisher,original_author=p.original_author,
                original_published_at=p.original_published_at.isoformat() if p.original_published_at else None,
                translation_note='原記事の日本語訳です。原文の見解・報道上の確度を保ち、翻訳・掲載に関するお問い合わせはTHE TOUCHLINEが受け付けます。' if p.content_mode=='translation' else '元媒体の報道内容を要約した記事です。全文の日本語訳ではありません。')

def export(db,dist,origin,at=None):
    now=at or m.utcnow(); origin=canonical(origin).rstrip('/')
    dist=Path(dist).resolve()
    if (dist/'release.json').exists(): raise ValueError('fresh React build required before every export; stale pages must never be reused')
    if not (dist/'index.html').exists(): raise ValueError('build React first')
    template=(dist/'index.html').read_text(encoding='utf-8')
    rows=[];versions={}
    for a in db.scalars(select(m.Article).where(m.Article.status.in_(['approved','published']),m.Article.scheduled_at<=now)):
        v=db.get(m.ArticleVersion,a.current_version_id);p=Generated.model_validate_json(v.payload_json);check=verify(db,p)
        latest=db.scalar(select(m.VerificationResult).where(m.VerificationResult.article_version_id==v.id).order_by(m.VerificationResult.id.desc()))
        if check['problems'] or check['rights']!='pass' or not latest or any(getattr(latest,k)!='pass' for k in ('factual_status','rights_status','quality_status')): raise ValueError('previously approved article no longer passes; deployment aborted')
        t=db.get(m.Team,a.primary_team_id);league=db.get(m.League,t.league_id)
        rows.append(dict(**public_attribution(p),id=a.id,slug=a.slug,title=v.title,summary=v.summary,body=v.body_markdown,kind=a.article_type,topic=p.topic,published_at=utc(a.scheduled_at).isoformat(),source_url=p.source_urls[0],source_urls=p.source_urls,teams=p.teams,event_ids=p.event_ids,team=dict(slug=t.slug,name=t.name_ja,league=league.name_ja,color='#324454')))
        versions[a.slug]=v.content_hash
    urls=[]
    def page(path,title,description,content,structured=None):
        canonical_url=origin+path; markup=template
        markup=re.sub(r'<title>.*?</title>','<title>'+html.escape(title)+'</title>',markup,flags=re.S)
        meta='<meta name="description" content="'+html.escape(description,quote=True)+'"><link rel="canonical" href="'+html.escape(canonical_url,quote=True)+'"><meta property="og:title" content="'+html.escape(title,quote=True)+'">'
        if structured: meta+='<script type="application/ld+json">'+json.dumps(structured,ensure_ascii=False).replace('<','\\u003c')+'</script>'
        markup=markup.replace('</head>',meta+'</head>').replace('<div id="root"></div>','<div id="root"></div><section id="static-content">'+content+'</section>')
        target=dist/path.lstrip('/')/'index.html';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(markup,encoding='utf-8');urls.append(canonical_url)
    def links(items): return ''.join('<p><a href="/news/'+a['slug']+'/">'+html.escape(a['title'])+'</a></p>' for a in items)
    page('/','THE TOUCHLINE','欧州サッカーニュースを日本語で','<h1>THE TOUCHLINE</h1>'+links(rows)+('' if rows else '<p>公開準備中です。掲載条件を満たした記事から順次公開します。</p>'))
    for a in rows:
        content='<article><h1>'+html.escape(a['title'])+'</h1>'+''.join('<p>'+html.escape(p)+'</p>' for p in a['body'].split('\n'))+''.join('<p><a href="'+html.escape(u,quote=True)+'">出典</a></p>' for u in a['source_urls'])+'</article>'
        attribution='<aside><h2>原記事・翻訳について</h2><p>'+html.escape(a['translation_note'])+'</p>'
        for label,key in [('原媒体','original_publisher'),('原題','original_title'),('原著者','original_author'),('原記事公開日時','original_published_at')]:
            if a.get(key): attribution+='<p>'+label+'：'+html.escape(a[key])+'</p>'
        content+=attribution+'</aside>'
        page('/news/'+a['slug']+'/',a['title'],a['summary'],content,{'@context':'https://schema.org','@type':'NewsArticle','headline':a['title'],'datePublished':a['published_at'],'dateModified':a['published_at'],'mainEntityOfPage':origin+'/news/'+a['slug']+'/','publisher':{'@type':'Organization','name':'THE TOUCHLINE'}})
    for team in db.scalars(select(m.Team)):
        page('/team/'+team.slug+'/',team.name_ja+' ニュース',team.name_ja+'の最新情報',links([a for a in rows if team.slug in a['teams']]))
    for league in db.scalars(select(m.League)):
        page('/league/'+league.slug+'/',league.name_ja+' ニュース',league.name_ja+'の最新情報',links([a for a in rows if a['team']['league']==league.name_ja]))
    page('/daily/','チーム別朝刊','チーム別の朝刊まとめ',links([a for a in rows if a['kind']=='daily']))
    page('/about/','編集方針 | THE TOUCHLINE','出典と確度を重視する編集方針','<h1>編集方針</h1><p>未確認情報は断定せず、利用条件と事実の確認を経て公開します。訂正・取り下げは編集履歴に記録します。</p>')
    catalog=[dict(slug=t.slug,name=t.name_ja,league=db.get(m.League,t.league_id).name_ja,color='#324454') for t in db.scalars(select(m.Team).where(m.Team.active==True))]
    (dist/'teams.json').write_text(json.dumps(catalog,ensure_ascii=False),encoding='utf-8')
    (dist/'articles.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
    (dist/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join('<url><loc>'+html.escape(u)+'</loc></url>' for u in urls)+'</urlset>',encoding='utf-8')
    (dist/'robots.txt').write_text('User-agent: *\nAllow: /\nSitemap: '+origin+'/sitemap.xml\n',encoding='utf-8')
    (dist/'404.html').write_text('<!doctype html><html lang="ja"><title>404</title><h1>ページが見つかりません</h1><a href="/">ホーム</a></html>',encoding='utf-8')
    (dist/'release.json').write_text(json.dumps({'built_at':now.isoformat(),'articles':[a['slug'] for a in rows],'versions':versions}),encoding='utf-8')
    return {'articles':len(rows),'pages':len(urls)}

def backup(path):
    path=Path(path).resolve()
    if not path.exists(): return None
    target=path.with_name(path.name+'.'+m.utcnow().strftime('%Y%m%d%H%M%S%f')+'.backup')
    with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target)) as dest: source.backup(dest)
    return str(target)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['init','collect','reclassify','merge','packet','ingest','check-sources','approve','withdraw','export','backup','source-policy'])
    parser.add_argument('--file');parser.add_argument('--fixture');parser.add_argument('--hours',type=int,default=24);parser.add_argument('--output',default='data/generated/'+m.utcnow().strftime('%Y-%m-%d'));parser.add_argument('--slug');parser.add_argument('--reviewer');parser.add_argument('--note');parser.add_argument('--source-id',type=int);parser.add_argument('--access-mode',choices=['rss_metadata','official_facts','licensed_fulltext']);parser.add_argument('--rights',choices=['unknown','restricted','permitted']);parser.add_argument('--license-reference');parser.add_argument('--origin',default=os.getenv('SITE_URL','https://example.invalid'));parser.add_argument('--dist',default='frontend/dist')
    args=parser.parse_args()
    if args.command=='backup': print(backup(args.file or 'data/football.db'));return
    _,Session=m.connect()
    with Session() as db:
        if args.command=='init': bootstrap(db);result={'status':'initialized'}
        elif args.command=='collect': result=collect(db,args.fixture,args.hours)
        elif args.command=='reclassify': result=reclassify(db)
        elif args.command=='merge': result=merge_events(db,args.file,args.reviewer,args.note)
        elif args.command=='source-policy':
            if not args.reviewer or not args.rights or not args.license_reference or len(args.note or '')<20: raise ValueError('human reviewer, rights, license reference and substantive note required')
            source=db.get(m.Source,args.source_id)
            if not source: raise ValueError('unknown source')
            if args.access_mode: source.access_mode=args.access_mode
            source.commercial_reuse_status=args.rights;source.license_reference=args.license_reference;source.reviewed_by=args.reviewer;source.checked_at=m.utcnow()
            db.add(m.PipelineRun(run_key=str(uuid.uuid4()),stage='rights-policy',status='reviewed',finished_at=m.utcnow(),counts_json=json.dumps({'source_id':source.id,'reviewer':args.reviewer,'rights':args.rights,'access_mode':source.access_mode,'license':args.license_reference,'note':args.note},ensure_ascii=False)));db.commit();result={'source_id':source.id,'rights':args.rights}
        elif args.command=='packet': result=packet(db,args.output)
        elif args.command=='ingest': result=import_with_log(db,args.file)
        elif args.command=='approve': result=approve(db,args.slug,args.reviewer,args.note)
        elif args.command=='check-sources': result=check_sources(db,args.slug)
        elif args.command=='withdraw':
            a=db.scalar(select(m.Article).where(m.Article.slug==args.slug))
            if not a: raise ValueError('unknown article')
            a.status='superseded';publication=db.scalar(select(m.Publication).where(m.Publication.article_id==a.id))
            if publication: publication.status='withdrawn'
            db.commit();result={'status':'withdrawn','action':'rebuild and deploy to remove public page'}
        else: result=export(db,args.dist,args.origin)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.command=='collect' and result['failures']: raise SystemExit(1)
if __name__=='__main__': main()

