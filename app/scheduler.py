"""Optional local collection only; Codex handles generation separately."""
import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from .v3_models import connect
from .workflow import collect,packet,ROOT
logging.basicConfig(level=logging.INFO)
scheduler=BlockingScheduler(timezone='Asia/Tokyo')
def collect_packet():
    _,Session=connect()
    with Session() as db:
        result=collect(db)
        logging.info('collection: %s',result)
        if result['failures']: raise RuntimeError('collection incomplete; inspect pipeline_errors')
        logging.info('packet: %s',packet(db,ROOT/'data'/'generated'))
scheduler.add_job(collect_packet,'cron',hour=5,minute=30,id='collect',max_instances=1,coalesce=True,misfire_grace_time=3600)
if __name__=='__main__': scheduler.start()
