import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine, ForeignKey, String, Text, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DB_URL = os.getenv('DATABASE_URL', 'sqlite:///./data/football.db')
if DB_URL.startswith('sqlite:///'):
    Path(DB_URL.replace('sqlite:///', '', 1)).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args={'check_same_thread': False} if DB_URL.startswith('sqlite:') else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase): pass
class Team(Base):
    __tablename__ = 'teams'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    league: Mapped[str] = mapped_column(String(100), index=True)
    color: Mapped[str] = mapped_column(String(20), default='#233d5a')

class Source(Base):
    __tablename__ = 'sources'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

class RawArticle(Base):
    __tablename__ = 'raw_articles'
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey('sources.id'))
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class NewsEvent(Base):
    __tablename__ = 'news_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(40), default='pending')

class Article(Base):
    __tablename__ = 'articles'
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(180), unique=True)
    title: Mapped[str] = mapped_column(String(250))
    summary: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    team_id: Mapped[int] = mapped_column(ForeignKey('teams.id'), index=True)
    team: Mapped[Team] = relationship()
    kind: Mapped[str] = mapped_column(String(30), default='daily')
    topic: Mapped[str] = mapped_column(String(60), default='ニュース')
    status: Mapped[str] = mapped_column(String(30), default='draft')
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class PipelineRun(Base):
    __tablename__ = 'pipeline_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str] = mapped_column(Text, default='')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

def init_db(): Base.metadata.create_all(engine)
