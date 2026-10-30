"""Independent v3 database. Legacy tables are never modified."""
import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import (create_engine, event, Column, Integer, String, Text, Boolean,
                        DateTime, Float, ForeignKey, UniqueConstraint, Index)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
def utcnow(): return datetime.now(timezone.utc)
def table(name, *columns, **kwargs):
    return type(''.join(x.title() for x in name.split('_')), (Base,),
                dict(__tablename__=name, **{c.name:c for c in columns}, **kwargs))
def pk(): return Column('id', Integer, primary_key=True)
def s(name, default=None, unique=False): return Column(name, String(250), default=default, unique=unique)
def t(name, default=None): return Column(name, Text, default=default)
def fk(name, target, primary=False): return Column(name, Integer, ForeignKey(target), primary_key=primary)
def dt(name, default=None): return Column(name, DateTime(timezone=True), default=default)

League=table('leagues',pk(),s('slug',unique=True),s('name_ja'),s('season'))
Team=table('teams',pk(),fk('league_id','leagues.id'),s('slug',unique=True),s('name_ja'),s('name_original'),Column('active',Boolean,default=True))
TeamAlias=table('team_aliases',pk(),fk('team_id','teams.id'),s('alias'),s('language'),__table_args__=(UniqueConstraint('team_id','alias'),))
Source=table('sources',pk(),s('name'),t('rss_url'),s('base_url'),s('language'),s('access_mode'),s('commercial_reuse_status','unknown'),t('license_reference'),s('reviewed_by'),dt('checked_at'),dt('last_success_at'),Column('active',Boolean,default=False),__table_args__=(UniqueConstraint('rss_url'),))
RawArticle=table('raw_articles',pk(),fk('source_id','sources.id'),t('canonical_url'),s('external_id'),t('title'),t('snippet'),s('language'),dt('source_published_at'),dt('collected_at',utcnow),s('content_hash'),s('ingest_status','new'),t('last_error'),__table_args__=(UniqueConstraint('canonical_url'),Index('ix_raw_source_date','source_id','source_published_at')))
NewsEvent=table('news_events',pk(),s('fingerprint',unique=True),t('subject'),s('event_type'),dt('event_time'),Column('importance_score',Integer),Column('confidence',Float),s('status','candidate'),dt('created_at',utcnow),dt('updated_at',utcnow))
EventSource=table('event_sources',fk('event_id','news_events.id',True),fk('raw_article_id','raw_articles.id',True),Column('relevance',Float,default=1))
EventTeam=table('event_teams',fk('event_id','news_events.id',True),fk('team_id','teams.id',True),s('role','subject'),__table_args__=(Index('ix_event_team','team_id','event_id'),))
EventFact=table('event_facts',pk(),fk('event_id','news_events.id'),t('fact_text'),s('certainty'),s('fact_type'),t('evidence_ref'),s('verification_status','pending'))
Article=table('articles',pk(),s('article_id',unique=True),s('slug',unique=True),s('article_type'),fk('primary_team_id','teams.id'),s('edition_date'),s('status','needs_review'),dt('scheduled_at'),fk('current_version_id','article_versions.id'),s('dedupe_key',unique=True),dt('created_at',utcnow),__table_args__=(Index('ix_article_due','status','scheduled_at'),))
ArticleVersion=table('article_versions',pk(),fk('article_id','articles.id'),Column('version_no',Integer),t('title'),t('summary'),t('body_markdown'),s('model_id'),s('prompt_version'),dt('generated_at',utcnow),s('content_hash'),t('payload_json'),__table_args__=(UniqueConstraint('article_id','version_no'),))
ArticleEvent=table('article_events',fk('article_id','articles.id',True),fk('event_id','news_events.id',True))
VerificationResult=table('verification_results',pk(),fk('article_version_id','article_versions.id'),s('checker_version'),s('factual_status'),s('rights_status'),s('quality_status'),dt('checked_at',utcnow),t('details_json'))
Publication=table('publications',pk(),fk('article_id','articles.id'),s('target'),s('status'),dt('scheduled_at'),dt('released_at'),s('deployment_id'),t('canonical_url'),t('error_message'),__table_args__=(UniqueConstraint('article_id'),))
PipelineRun=table('pipeline_runs',pk(),s('run_key',unique=True),s('stage'),dt('started_at',utcnow),dt('finished_at'),s('status'),t('counts_json'),__table_args__=(Index('ix_run_stage_date','stage','started_at'),))
PipelineError=table('pipeline_errors',pk(),fk('run_id','pipeline_runs.id'),s('record_type'),s('record_id'),s('stage'),s('error_code'),t('details'),Column('retry_count',Integer,default=0),dt('resolved_at'))

def connect(url=None):
    url=url or os.getenv('TOUCHLINE_DATABASE_URL','sqlite:///./data/touchline-v3.db')
    if url.startswith('sqlite:///'): Path(url[10:]).parent.mkdir(parents=True,exist_ok=True)
    engine=create_engine(url,pool_pre_ping=True)
    if url.startswith('sqlite:'):
        @event.listens_for(engine,'connect')
        def constraints(conn, _): conn.execute('PRAGMA foreign_keys=ON')
    return engine,sessionmaker(engine,expire_on_commit=False)
