import os
import tempfile
import unittest
from datetime import datetime,timezone,timedelta

class EditorialTest(unittest.TestCase):
    def test_approval_and_idempotent_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            # Set env before importing DB models
            os.environ['DATABASE_URL']='sqlite:///'+folder+'/db.sqlite'
            from app.db import init_db,SessionLocal,Team,Article
            from app.editorial import eligible,review_article,publish_due,build_dailies
            init_db()
            with SessionLocal() as db:
                t=Team(name='Test Team',slug='test-team',league='Test',color='#112233');db.add(t);db.flush()
                article=Article(slug='valid',title='確認した公式発表',summary='公式に公表された事実について、出典と公開日を確認し、独自に簡潔に整理した日本語の説明文です。追加の説明も含めて要点を明確に整理しています。',body='根拠に基づいた背景説明。'*20,team_id=t.id,kind='individual',topic='ニュース',status='draft',is_demo=False,source_url='https://example.org/official',published_at=datetime.now(timezone.utc)-timedelta(minutes=10))
                db.add(article);db.commit()
            self.assertEqual(review_article('valid',True)['status'],'approved')
            self.assertEqual(len(publish_due(dry_run=True)['published']),1)
            self.assertEqual(len(publish_due(dry_run=False)['published']),1)
            self.assertEqual(len(publish_due(dry_run=False)['published']),0)
            from app.db import engine
            engine.dispose()

if __name__=='__main__':unittest.main()

