# THE TOUCHLINE

欧州5大リーグのニュースを日本語で届ける静的サイトのソースコードです。

公開サイト: https://the-touchline.tatsuki-fujii.chatgpt.site

記事生成はCodexの予約実行で行い、OpenAI APIは使用しません。ソースの利用条件と公開審査を満たした記事だけを掲載します。

## ローカル開発

```sh
python -m venv .venv
# 仮想環境を有効化して実行
pip install -r requirements.txt
python -m alembic upgrade head
python -m app.workflow init
python -m unittest discover -s tests -q
```

React: `cd frontend` → `npm install` → `npm run dev`。

## 運用状況

- 本番公開先はSites。ソース保存・デプロイはCodexの認可済みSites連携を使います。
- GitHubのpushではテストとReactビルドを実行します。記事を自動承認・自動公開しません。
- `.github/workflows/static-release.yml` は別のCloudflare Pages向けの無効テンプレートです。SecretsとVariablesを設定するまで稼働しません。
- DB、認証情報、収集原文、未承認草稿をこの公開リポジトリに保存しません。
- 許諾済みソース・承認済み記事がない場合はニュースを生成・公開しません。
