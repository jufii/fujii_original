"""Initial additive v3 schema; run against the separate v3 database only."""
from alembic import op
from sqlalchemy import inspect
from app.v3_models import Base
revision='0001_v3'
down_revision=None
def upgrade():
    existing=set(inspect(op.get_bind()).get_table_names())-{'alembic_version'}
    if existing: raise RuntimeError('Refusing non-empty database: choose a new TOUCHLINE_DATABASE_URL')
    Base.metadata.create_all(op.get_bind())
def downgrade(): raise RuntimeError('Destructive downgrades are disabled; restore a backup instead')
