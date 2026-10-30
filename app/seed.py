from datetime import datetime, timezone
from .db import Team, Article, SessionLocal, init_db

TEAMS = [
 ('バルセロナ','barcelona','ラ・リーガ','#a12446'),('レアル・マドリード','real-madrid','ラ・リーガ','#7886b8'),('アトレティコ・マドリード','atletico','ラ・リーガ','#c33149'),
 ('アーセナル','arsenal','プレミアリーグ','#d84742'),('リヴァプール','liverpool','プレミアリーグ','#cb4640'),('マンチェスター・シティ','man-city','プレミアリーグ','#7aa9c9'),
 ('インテル','inter','セリエA','#3864a5'),('ユヴェントス','juventus','セリエA','#646975'),('ミラン','ac-milan','セリエA','#c34c4b'),
 ('バイエルン','bayern','ブンデスリーガ','#c7445a'),('ドルトムント','dortmund','ブンデスリーガ','#d4ac3c'),
 ('PSG','psg','リーグ・アン','#31487b'),('マルセイユ','marseille','リーグ・アン','#66a5c9')]

# All sample headlines are deliberately fictional illustrative placeholders, NOT actual football reporting.
EXAMPLES = [
('barcelona','daily','朝刊','バルセロナ｜今日のクラブニュースまとめ','練習・戦術・次戦の準備をテーマに、朝のニュースを読みやすく整理。','これは画面確認用の架空のサンプル記事です。実際の移籍、怪我、試合結果を伝えるものではありません。\n\n【トピック1】チーム練習に関する架空の更新\n複数のニュースをまとめる記事形式を確認するためのサンプル文章です。\n\n【トピック2】次戦に向けた戦術の見どころ\nフォーメーションや選手配置などについての解説を配置する想定です。'),
('arsenal','daily','朝刊','アーセナル｜本日のニュースダイジェスト','トレーニング、試合準備、チーム動向をコンパクトに確認。','【デモ記事】この文章はダミーデータです。ニュースソースを接続すると、出典付きの日本語ダイジェストに置き換わります。'),
('real-madrid','individual','戦術','レアル・マドリード｜注目の戦術テーマを詳しく解説','チームの戦い方を解説する個別記事の表示例。','【デモ記事】実際の最新ニュースではありません。独立したニュース記事のレイアウト確認用です。'),
('inter','daily','朝刊','インテル｜今朝のニュース一覧','イタリアのクラブニュースを日本語で整理するイメージ。','【デモ記事】セリエAの記事表示を確認するための架空のコンテンツです。'),
('bayern','individual','チーム情報','バイエルン｜注目テーマの個別ニュース例','重要ニュースを単独記事として公開する際のデザイン例。','【デモ記事】実際の出来事を報じているものではありません。'),
('psg','daily','朝刊','PSG｜本日のチームニュース','フランスの現地情報を毎朝まとめる記事のレイアウト例。','【デモ記事】実際のニュースではありません。'),
]

def seed():
    init_db()
    with SessionLocal() as db:
        for name, slug, league, color in TEAMS:
            if not db.query(Team).filter_by(slug=slug).first(): db.add(Team(name=name,slug=slug,league=league,color=color))
        db.commit()
        for slug,kind,topic,title,summary,body in EXAMPLES:
            article_slug = f'demo-{slug}-{kind}'
            if db.query(Article).filter_by(slug=article_slug).first(): continue
            team = db.query(Team).filter_by(slug=slug).one()
            db.add(Article(slug=article_slug,title=title,summary=summary,body=body,team_id=team.id,kind=kind,topic=topic,status='published',is_demo=True,published_at=datetime.now(timezone.utc)))
        db.commit()

if __name__ == '__main__': seed()
