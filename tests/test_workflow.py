import json, tempfile, unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import select, func, text
from app import v3_models as m
from app import workflow as w

class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.engine,Session=m.connect('sqlite:///'+str(self.root/'test.db'));m.Base.metadata.create_all(self.engine);self.db=Session();w.bootstrap(self.db)
        self.now=m.utcnow();self.fixture=self.root/'feed.xml'
        self.fixture.write_text('<rss><channel><item><title>Arsenal signs Example Player</title><link>https://www.bbc.co.uk/sport/example</link><description>Arsenal confirms a signing.</description><pubDate>'+self.now.strftime('%a, %d %b %Y %H:%M:%S +0000')+'</pubDate></item></channel></rss>')
    def tearDown(self): self.db.close();self.engine.dispose();self.tmp.cleanup()
    def count(self,model): return self.db.scalar(select(func.count()).select_from(model))
    def collect(self): return w.collect(self.db,str(self.fixture))
    def generated(self):
        self.collect();e=self.db.scalar(select(m.NewsEvent));r=self.db.scalar(select(m.RawArticle))
        self.payload=dict(article_id='test-article',article_type='individual',title='アーセナルの移籍発表を整理する',slug='arsenal-signing',league='premier-league',teams=['arsenal'],topic='transfer',published_at=(self.now-timedelta(minutes=1)).isoformat(),body='これは架空のフィクスチャーによるテスト記事です。アーセナルの発表として入力された情報を整理しています。'*5,summary='これは架空のフィクスチャーを使用し、日本語記事の取り込み工程を確認するための要約です。',source_urls=[r.canonical_url],event_ids=[e.id],claims=[dict(event_id=e.id,text='アーセナルが加入を発表した',source_url=r.canonical_url,evidence=r.title,certainty='confirmed')],verification_status='pending',publication_status='draft')
        return self.write()
    def write(self):
        p=self.root/'article.json';p.write_text(json.dumps(self.payload,ensure_ascii=False),encoding='utf-8');return p
    def permitted(self):
        s=self.db.scalar(select(m.Source));s.commercial_reuse_status='permitted';s.license_reference='fixture://test-only-license';s.checked_at=self.now;s.reviewed_by='test-only';self.db.commit()
        from unittest.mock import Mock
        response=Mock(status_code=200,url='https://www.bbc.co.uk/sport/example')
        with patch.object(w.requests,'head',return_value=response): w.check_sources(self.db,'arsenal-signing')
    def build(self):
        path=self.root/'dist';path.mkdir(exist_ok=True);(path/'index.html').write_text('<html><head><title>test</title></head><body><div id="root"></div></body></html>');return path
    def test_16_tables_and_foreign_keys(self):
        self.assertEqual(len(m.Base.metadata.tables),16)
        self.assertEqual(self.db.execute(text('PRAGMA foreign_keys')).scalar(),1)
    def test_repeat_collection(self):
        self.assertEqual(self.collect()['new'],1);self.assertEqual(self.collect()['duplicates'],1)
        self.assertEqual(self.count(m.RawArticle),1);self.assertEqual(self.count(m.NewsEvent),1)
    def test_distinct_source_same_headline_merges(self):
        self.collect();self.fixture.write_text(self.fixture.read_text().replace('bbc.co.uk/sport/example','bbc.co.uk/sport/another'))
        self.collect();self.assertEqual(self.count(m.RawArticle),2);self.assertEqual(self.count(m.NewsEvent),1)
    def test_unknown_rights_blocks(self):
        result=w.ingest(self.db,self.generated());self.assertEqual(result['rights'],'blocked')
        with self.assertRaises(ValueError): w.approve(self.db,'arsenal-signing','editor','Human confirms facts and all source permissions.')
    def test_unapproved_and_future_not_exported(self):
        w.ingest(self.db,self.generated());self.permitted();dist=self.build()
        self.assertEqual(w.export(self.db,dist,'https://example.org')['articles'],0)
        w.approve(self.db,'arsenal-signing','editor','Human confirms facts and all source permissions.')
        a=self.db.scalar(select(m.Article));a.scheduled_at=self.now+timedelta(days=1);self.db.commit()
        (dist/'release.json').unlink()
        self.assertEqual(w.export(self.db,dist,'https://example.org')['articles'],0)
        self.assertNotIn('arsenal-signing',(dist/'articles.json').read_text())
    def test_approved_export_and_idempotent_import(self):
        file=self.generated();w.ingest(self.db,file);self.assertEqual(w.ingest(self.db,file)['status'],'unchanged');self.assertEqual(self.count(m.ArticleVersion),1)
        self.permitted();w.approve(self.db,'arsenal-signing','editor','Human confirms facts and all source permissions.');dist=self.build()
        self.assertEqual(w.export(self.db,dist,'https://example.org')['articles'],1)
        html=(dist/'news/arsenal-signing/index.html').read_text(encoding='utf-8')
        self.assertIn('NewsArticle',html);self.assertIn('rel="canonical"',html)
        self.assertIn('/news/arsenal-signing/',(dist/'sitemap.xml').read_text())
    def test_wrong_team_and_fabricated_evidence(self):
        self.generated();self.payload['teams']=['liverpool'];self.payload['claims'][0]['evidence']='invented evidence';result=w.ingest(self.db,self.write())
        self.assertIn('team_mismatch',result['problems']);self.assertIn('unsupported_evidence',result['problems'])
    def test_invalid_json_no_persistence(self):
        self.generated();self.payload['published_at']='2026-01-01';self.payload['publication_status']='published'
        with self.assertRaises(ValueError): w.ingest(self.db,self.write())
        self.assertEqual(self.count(m.Article),0)
    def test_duplicate_event_identity_rejected(self):
        w.ingest(self.db,self.generated());self.payload['article_id']='duplicate';self.payload['slug']='duplicate'
        with self.assertRaises(ValueError): w.ingest(self.db,self.write())
    def test_failure_log_and_retry(self):
        with patch.object(w.requests,'get',side_effect=TimeoutError('fixture timeout')):
            self.assertEqual(w.collect(self.db)['failures'],2)
        self.assertEqual(self.count(m.PipelineError),2);self.assertEqual(self.collect()['new'],1)
    def test_backup_preserves_original(self):
        p=self.root/'legacy.db'
        import sqlite3
        conn=sqlite3.connect(p);conn.execute('create table important (id integer)');conn.execute('insert into important values (1)');conn.commit();conn.close()
        before=p.read_bytes();target=w.backup(p);self.assertEqual(before,p.read_bytes())
        conn=sqlite3.connect(target);self.assertEqual(conn.execute('select id from important').fetchone()[0],1);conn.close()
    def test_aliases_avoid_player_and_madrid_false_positive(self):
        self.assertEqual(w.classify(self.db,'Madrid signs a player'),[])
        self.assertTrue(w.classify(self.db,'Arsenal signs a player'))
    def test_canonical_preserves_semantic_query(self):
        self.assertEqual(w.canonical('https://example.org/story?id=2&utm_source=x#test'),'https://example.org/story?id=2')
    def test_production_does_not_fetch_unlicensed_sources(self):
        import os
        with patch.dict(os.environ,{'APP_ENV':'production'}),patch.object(w.requests,'get') as fetch:
            result=w.collect(self.db)
            fetch.assert_not_called();self.assertEqual(result['eligible_sources'],0);self.assertEqual(result['blocked_sources'],2)
    def test_stale_bundle_requires_clean_build(self):
        dist=self.build();w.export(self.db,dist,'https://example.org')
        with self.assertRaises(ValueError): w.export(self.db,dist,'https://example.org')
    def test_women_youth_are_held(self):
        self.assertEqual(w.classify(self.db,"Arsenal Women's Champions League"),[])
        self.assertEqual(w.classify(self.db,'Liverpool U21 wins'),[])
    def test_stale_release_does_not_mark_live(self):
        from app.release import confirm
        dist=self.build();(dist/'release.json').write_text(json.dumps({'built_at':'test','articles':[]}))
        from unittest.mock import Mock
        response=Mock();response.json.return_value={'built_at':'older','articles':[]}
        with patch('app.release.requests.get',return_value=response):
            with self.assertRaises(ValueError): confirm(self.db,dist,'https://example.org')
        self.assertEqual(self.count(m.Publication),0)
    def test_current_roster_all_five_leagues(self):
        self.assertEqual(self.count(m.Team),96);self.assertEqual(self.count(m.League),5)
    def test_past_and_missing_dates_are_skipped(self):
        self.fixture.write_text('<rss><channel><item><title>Arsenal signs Example</title><link>https://example.org/a</link></item></channel></rss>')
        self.assertEqual(self.collect()['skipped'],1);self.assertEqual(self.count(m.RawArticle),0)
    def test_reviewed_event_merge_preserves_observations(self):
        self.collect();self.fixture.write_text(self.fixture.read_text().replace('Arsenal signs Example Player','Arsenal signing of Example Player').replace('/example','/another'));self.collect()
        self.assertEqual(self.count(m.NewsEvent),2)
        events=self.db.scalars(select(m.NewsEvent)).all();f=self.root/'merge.json';f.write_text(json.dumps([{'primary':events[0].id,'duplicate':events[1].id}]))
        w.merge_events(self.db,f,'editor','The same club, player and transfer announcement were checked.')
        self.assertEqual(self.count(m.RawArticle),2);self.assertEqual(events[1].status,'merged')
        self.assertEqual(len(self.db.scalars(select(m.EventSource).where(m.EventSource.event_id==events[0].id)).all()),2)
    def test_legacy_migration_holds_published_content(self):
        from app.migrate_legacy import migrate
        import sqlite3
        f=self.root/'legacy.db';conn=sqlite3.connect(f)
        conn.executescript("CREATE TABLE teams(id INTEGER,name TEXT,slug TEXT,league TEXT); CREATE TABLE sources(id INTEGER,name TEXT,url TEXT); CREATE TABLE raw_articles(id INTEGER,source_id INTEGER,url TEXT,title TEXT); CREATE TABLE articles(id INTEGER,team_id INTEGER,slug TEXT,kind TEXT,title TEXT,summary TEXT,body TEXT,status TEXT); INSERT INTO teams VALUES(1,'Arsenal','arsenal','プレミアリーグ'); INSERT INTO articles VALUES(1,1,'old','individual','old title','old summary','preserved body','published');")
        conn.commit();conn.close();original=f.read_bytes()
        self.assertEqual(migrate(self.db,f)['imported'],1);self.assertEqual(migrate(self.db,f)['imported'],0);self.assertEqual(f.read_bytes(),original)
        a=self.db.scalar(select(m.Article));self.assertEqual(a.status,'needs_review');self.assertEqual(self.db.get(m.ArticleVersion,a.current_version_id).body_markdown,'preserved body')
    def test_inter_milan_does_not_match_ac_milan(self):
        ids=w.classify(self.db,'Inter Milan signs Example Player')
        self.assertEqual([self.db.get(m.Team,i).slug for i in ids],['inter'])
    def test_invalid_generated_file_is_logged_then_resolved(self):
        f=self.generated();self.payload['publication_status']='published';self.write()
        with self.assertRaises(ValueError): w.import_with_log(self.db,f)
        error=self.db.scalar(select(m.PipelineError));self.assertIsNone(error.resolved_at)
        self.payload['publication_status']='draft';self.write();w.import_with_log(self.db,f)
        self.db.refresh(error);self.assertIsNotNone(error.resolved_at);self.assertEqual(error.retry_count,1)

if __name__=='__main__':unittest.main()

class TranslationTest(unittest.TestCase):
    setUp=WorkflowTest.setUp
    tearDown=WorkflowTest.tearDown
    collect=WorkflowTest.collect
    generated=WorkflowTest.generated
    write=WorkflowTest.write
    permitted=WorkflowTest.permitted
    def test_translation_requires_original_article(self):
        self.generated();self.payload['content_mode']='translation'
        with self.assertRaises(ValueError): w.ingest(self.db,self.write())
    def test_translation_requires_licensed_fulltext(self):
        self.generated();raw=self.db.scalar(select(m.RawArticle))
        self.payload.update(content_mode='translation',original_title=raw.title,original_publisher='Fixture Publisher',original_published_at=w.utc(raw.source_published_at).isoformat(),original_text=(raw.title+'. Fixture original article for translation testing. ')*5)
        self.payload['claims'][0]['certainty']='reported'
        result=w.ingest(self.db,self.write())
        self.assertIn('translation_permission_required',result['problems'])
        self.assertNotIn('uncertain_claim',result['problems'])
        self.permitted();source=self.db.scalar(select(m.Source));source.access_mode='licensed_fulltext';self.db.commit()
        p=w.Generated.model_validate(self.payload);result=w.verify(self.db,p)
        self.assertEqual(result['rights'],'pass');self.assertNotIn('translation_permission_required',result['problems'])
        self.assertNotIn('original_text',w.public_attribution(p))

