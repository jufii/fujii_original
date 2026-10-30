from pathlib import Path
import os
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from .db import SessionLocal, Team, Article, PipelineRun, init_db
from .seed import seed

BASE = Path(__file__).resolve().parent.parent
app = FastAPI(title='The Touchline', version='1.0.0', docs_url=None if os.getenv('APP_ENV') == 'production' else '/docs', redoc_url=None)
app.mount('/static', StaticFiles(directory=str(BASE/'static')), name='static')
templates = Jinja2Templates(directory=str(BASE/'templates'))

@app.on_event('startup')
def startup():
    init_db()
    if os.getenv("SEED_DEMO", "false").lower() == "true":
        seed()

def context(request, **kwargs):
    with SessionLocal() as db:
        teams = db.scalars(select(Team).order_by(Team.league, Team.name)).all()
        leagues = ['すべて','プレミアリーグ','ラ・リーガ','セリエA','ブンデスリーガ','リーグ・アン']
    return dict(request=request, all_teams=teams, leagues=leagues, **kwargs)

@app.get('/', response_class=HTMLResponse)
def home(request: Request, league: str = Query(default='すべて')):
    with SessionLocal() as db:
        q = select(Article).options(selectinload(Article.team)).join(Team).where(Article.status=='published').order_by(desc(Article.published_at), desc(Article.id))
        if league != 'すべて': q = q.where(Team.league==league)
        articles = db.scalars(q).all()
    return templates.TemplateResponse('index.html',context(request,articles=articles, active_league=league))

@app.get('/team/{slug}/', response_class=HTMLResponse)
def team_page(request: Request,slug: str):
    with SessionLocal() as db:
        team = db.scalar(select(Team).where(Team.slug==slug))
        if not team: raise HTTPException(404,'チームが見つかりません')
        articles = db.scalars(select(Article).options(selectinload(Article.team)).where(Article.team_id==team.id,Article.status=='published').order_by(desc(Article.published_at))).all()
    return templates.TemplateResponse('team.html',context(request,team=team,articles=articles))

@app.get('/news/{slug}/', response_class=HTMLResponse)
def news_page(request: Request, slug: str):
    with SessionLocal() as db:
        article=db.scalar(select(Article).options(selectinload(Article.team)).where(Article.slug==slug, Article.status=='published'))
        if not article: raise HTTPException(404,'記事が見つかりません')
    return templates.TemplateResponse('article.html',context(request,article=article))

@app.get('/health')
def health(): return {'status':'ok'}


# React frontend reads the same database as the existing server-rendered prototype.
@app.get('/api/articles')
def api_articles():
    from . import v3_models as m
    from .workflow import Generated,utc,public_attribution
    from datetime import datetime,timezone
    _,Session=m.connect()
    with Session() as db:
        states=['published']
        if os.getenv('LOCAL_PREVIEW')=='true' and os.getenv('APP_ENV')!='production': states+=['draft','needs_review','approved']
        rows=[]
        for a in db.scalars(select(m.Article).where(m.Article.status.in_(states))):
            v=db.get(m.ArticleVersion,a.current_version_id)
            if not v or not v.payload_json or v.prompt_version=='legacy': continue
            p=Generated.model_validate_json(v.payload_json)
            if states==['published'] and utc(a.scheduled_at)>datetime.now(timezone.utc): continue
            t=db.get(m.Team,a.primary_team_id);league=db.get(m.League,t.league_id)
            rows.append(dict(**public_attribution(p),id=a.id,slug=a.slug,title=v.title,summary=v.summary,body=v.body_markdown,kind=a.article_type,topic=p.topic,published_at=utc(a.scheduled_at).isoformat(),status=a.status,source_urls=p.source_urls,source_url=p.source_urls[0],team=dict(name=t.name_ja,slug=t.slug,league=league.name_ja,color='#324454')))
        return rows

@app.get('/api/teams')
def api_teams():
    from . import v3_models as m
    _,Session=m.connect()
    with Session() as db:
        return [dict(id=t.id,slug=t.slug,name=t.name_ja,league=db.get(m.League,t.league_id).name_ja,color='#324454') for t in db.scalars(select(m.Team).where(m.Team.active==True))]

@app.get('/api/status')
def api_status():
    from sqlalchemy import text
    from .v3_models import connect
    _,Session=connect()
    with Session() as db:
        db.execute(text('SELECT 1'))
    return {'service':'the-touchline','database':'ok','production':os.getenv('APP_ENV')=='production'}

@app.get('/api/pipeline/status')
def pipeline_status():
    from . import v3_models as m
    _,Session=m.connect()
    with Session() as db:
        row=db.scalar(select(m.PipelineRun).order_by(desc(m.PipelineRun.id)))
        return dict(run_key=row.run_key,status=row.status,stage=row.stage,counts=row.counts_json) if row else {'status':'not_started'}
