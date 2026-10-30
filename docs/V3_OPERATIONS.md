# THE TOUCHLINE v3 ローカル実装・運用ガイド

2026-10-29 JST。外部デプロイ、課金、予約タスクの有効化、GitHubへの送信は実行していない。OpenAI API呼出しは削除・無効化した。現時点は**ローカルで実行できる編集・静的公開基盤**であり、完全自動本番運用の完成版ではない。

## 構成と監査結果

既存は6テーブル、13クラブ、RSS原題プレビュー、自己申告による承認、FastAPI依存のUIだった。v3は独立した16テーブルとAlembicへ移行した。旧app/db.py・editorial.py・Jinjaテンプレートは互換用として残す。v3の公開経路はapp/workflow.pyだけを使用する。旧mark-checkedや旧publishedをv3の検証証拠として取り込まない。旧Docker/Nginx構成はv3本番構成として未更新・未検証なので使用しない。

`app/v3_models.py`：リーグ、チーム、別名、ソース、取得記事、イベント、関連ソース・チーム、事実、記事・バージョン・イベント関連、検証、公開、定期実行、エラーの16テーブル。SQLiteで外部キーを有効化。PostgreSQL用DDLのコンパイルを確認したが、実PostgreSQL接続は未検証。

`config/teams.yaml`：2026/27の96クラブと日本語名・別表記。名前の長い一致を優先してInter MilanをAC Milanへ誤分類しない。選手名や監督名からの推定を止め、女子・育成年代の明示がある記事は保留する。完全な意味理解・誤分類防止は保証しない。

`config/sources.yaml`：初期ONはBBC・GuardianのRSSメタデータのみ。商用再利用権利は全てunknown。クラブ・リーグ公式のニュース取得は、RSS提供状況と取得・商用利用条件を人が確認して追加する必要がある。公式クラブ名簿の確認は記事本文の転載許諾を意味しない。

イベントは同日・同種別・同チーム・正規化見出しの一致で統合。意味が同じ別見出しは自動では統合しない。Codexと編集者が同一イベントと判断した場合、生成前にmergeを実行する。重要度は保守的なキーワード判定であり、高精度な報道価値判定は今後の改善対象。

## Windowsでの初回実行

Python 3.12+、Node.js 22+、npmを用意し、touchline-local-completeで実行する。`.env`は自動読込しないので環境変数をPowerShellで設定する。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:TOUCHLINE_DATABASE_URL='sqlite:///./data/touchline-v3.db'
$env:SITE_URL='https://example.invalid' # 本番前に実際のpages.dev URLへ変更
python -m app.workflow backup --file data/football.db
python -m alembic upgrade head
python -m app.workflow init
python -m app.migrate_legacy --file data/football.db
python -m unittest discover -s tests -v
python -m app.workflow collect --hours 24
python -m app.workflow packet
```

旧DBがある場合はSQLiteオンラインバックアップを同じdataフォルダーへ作成してから読み取り専用で移行する。存在しない場合はbackup=null。新規v3 DBへ移行し、旧DBを変更・削除しない。旧記事は本文を保存してneeds_reviewへ移す。記事ID、URL、日付、権利を再審査し、新スキーマで生成し直す。Alembicは既存テーブルのあるDBへの初期適用や破壊的downgradeを拒否する。

実装した移行で保存するのは旧チーム・ソース・取得記事・記事本文。旧イベントや実行履歴の自動変換は未実装で、旧DBとバックアップに保持する。

```powershell
# 同じ出来事だと確認した二つのイベント。生成前にだけ実行できる。
# merges.json: [{"primary":1,"duplicate":2}]
python -m app.workflow merge --file merges.json --reviewer '編集者名' --note '同じ選手・クラブ・発表日時の出来事と出典を照合した記録'
python -m app.workflow reclassify # クラブ設定を変更した後
```

## Codexによる日本語生成と取込

`docs/CODEX_DAILY_PROMPT.md`をCodexの指示に使う。Codexがpacket.jsonとschema.jsonを読み、独立した日本語の個別記事と朝刊を記事ごとのJSONへ保存する。Pythonがモデルを呼ぶ仕組みはない。生成に失敗した場合もAPIへ切り替えない。

```powershell
python -m app.workflow ingest --file data/generated/YYYY-MM-DD/individual.json
python -m app.workflow ingest --file data/generated/YYYY-MM-DD/daily.json
python -m app.workflow check-sources --slug ARTICLE_SLUG
```

取込はPydanticで型・日本語・長さ・タイムゾーン・出典・イベント・チーム・リーグを確認する。生成側がapproved/publishedを指定しても拒否する。同内容は再取込しても増殖せず、変更は新バージョンになる。別IDで同じ個別イベントや同じクラブ・日付の朝刊を登録すると拒否する。失敗はpipeline_errorsへ記録し、同じファイルを修正して再取込すれば解消記録を残す。

出典URLのHEAD応答を記録するが、HTTP 200や根拠文字列の一致だけで意味的な正確性・日付・全ての本文事実を検証できたとは扱わない。最終的な照合は人間が行う。現状の検証は全記事をneeds_reviewから始める。

```powershell
# 人間が、当該利用方法の商用許諾を実際に確認できたときだけ実行。
python -m app.workflow source-policy --source-id 1 --rights permitted --reviewer '確認者名' --license-reference '許諾文書URLまたは保管場所' --note '許可された利用目的・範囲・期限を具体的に記録する'
python -m app.workflow check-sources --slug ARTICLE_SLUG
python -m app.workflow approve --slug ARTICLE_SLUG --reviewer '校閲者名' --note '主要事実、日時、出典、チーム分類と許諾範囲を実際に照合した記録'
```

rights=permittedはAIに判断させない。権利の根拠・確認者・日時が欠ける記事、低重要度の個別記事、出典不一致、24時間範囲外の朝刊、未確定のclaim等は承認できない。法的な利用許諾をソフトウェアが保証するものではない。

## ローカル画面・静的HTML

```powershell
$env:LOCAL_PREVIEW='true'
$env:APP_ENV='development'
python -m uvicorn app.main:app --host 127.0.0.1 --reload
# 別のPowerShell
cd frontend
npm install
npm run dev
```

localhost:5173で未承認草稿を確認できる。FastAPIのルートHTMLは旧デモ用のままなので、React画面を利用する。

```powershell
# プロジェクトルートで実行。毎回必ず新しくViteビルドする。
cd frontend
npm run build
cd ..
python -m app.workflow export --dist frontend/dist --origin https://YOUR-PROJECT.pages.dev
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/dist
```

静的公開は承認済み/公開済みかつ公開時刻到来、最新検証が全passのものに限定する。未来記事はJSON・HTML・サイトマップのどこにも含めない。既存公開記事は毎回DB全体から再出力する。古い記事ファイルが残るのを防ぐため、release.jsonがある出力先への再出力を拒否し、新規Viteビルドを要求する。

記事、クラブ、リーグ、朝刊一覧の直接URL、title/description/canonical、記事NewsArticle JSON-LD、sitemap.xml、robots.txtを生成する。Reactの既存CSS・レイアウトと広告枠を維持する。公開バンドルはfrontend/distのみであり、DB・.env・data/generatedをアップロードしない。

```powershell
python -m app.workflow withdraw --slug ARTICLE_SLUG
# 修正時は新JSONを同じidentityで再取込し、再校閲・再ビルド・再デプロイ。
# withdrawだけでは既に公開済みのページは消えない。再デプロイが必要。
```

## GitHub Actions / Cloudflare / Supabase

元フォルダーはGitリポジトリではなく、リモートURLも存在しなかった。Git初期化・push・PR・外部リソース作成はしていない。まず公開先とリポジトリを決める必要がある。草稿やRSSスニペットをGitへ保存するなら非公開リポジトリとする。デフォルトではdata/をignoreする。

Supabase等の永続PostgreSQLを準備した場合、`TOUCHLINE_DATABASE_URL=postgresql+psycopg://...`をローカルとActionsに設定し、空の新DBへAlembicとinitを実行する。既存データを持つSupabaseプロジェクトへの適用は禁止。認証情報はブラウザへ渡さない。無料枠にバックアップやSLAがあると仮定しない。

`.github/workflows/static-release.yml`は、07:00 JST (= 前日22:00 UTC)以降にビルドしてCloudflare PagesへDirect Uploadする構成。`TOUCHLINE_DEPLOY_ENABLED`がtrueでない限り起動しない。production Environmentを使用し、必要に応じてGitHubの保護ルールを設定する。`SITE_URL`は実際の公開originと完全に一致させる。

必要なGitHub Secrets：TOUCHLINE_DATABASE_URL、CLOUDFLARE_API_TOKEN、CLOUDFLARE_ACCOUNT_ID。Variables：SITE_URL、CLOUDFLARE_PAGES_PROJECT、TOUCHLINE_DEPLOY_ENABLED。APIトークンは対象Pagesプロジェクトに必要な最小権限で用意する。Cloudflare PagesのGit自動ビルドとは併用せず、公開経路を一本化する。AdSenseキー・Google広告設定は未追加。

Actionsはconcurrencyで同時デプロイを直列化。ビルド失敗ならデプロイしないので従来サイトを維持する。デプロイ後にrelease.json・バージョンハッシュ・各記事URLを確認してからDBをpublished/liveにする。ネットワーク障害・古いmanifestならpublishedを更新しない。デプロイ成功後の確認失敗では、新サイトが既に見える可能性があり、前日版への自動ロールバックは未実装。Actionsログと失敗artifact・GitHub標準の通知を利用し、専用メール/Slack通知は未接続。

## Codex予約実行の確認結果と設定

公式資料確認日：2026-10-29。資料は現在の統合Scheduled UIへ転送される。

|項目|結果|
|---|---|
|毎日05:30の起動|Scheduledで設定可能。今回の実時刻トリガー試験は未実行|
|ネットワーク|既定のサンドボックスと許可に依存。今回、権限付き実行でBBC/Guardian取得成功。無人実行時の同等許可は未検証|
|日本語生成|このCodexセッションで個別記事・朝刊を作成。API不使用|
|JSON保存|ローカル保存・Pydantic取込成功|
|GitHub変更|git/接続ツールの認証と対象リポジトリ権限が必要。リポジトリ未設定のため未検証|
|PC停止中|ローカルフォルダーを使う予約実行はPC電源とアプリ起動が必要|
|利用枠・費用|通常のCodex/Work利用枠を消費。無制限・追加費用ゼロは保証できない|

設定手順：対象フォルダーをCodexのプロジェクトとして開く → Scheduledで毎日05:30、Asia/Tokyoを指定 → docs/CODEX_DAILY_PROMPT.mdをプロンプトに設定 → ローカルプロジェクトで実行 → 手動Run nowでネットワーク・ファイル保存を検証 → 数回の朝の実行を確認してから本番用を有効化する。今回のチャットの作業ルートは別ディレクトリなので、そのまま予約して対象フォルダーへの書込権限が持続すると仮定しない。

Web側Scheduledは接続ツール等で動作できるが、このPCのフォルダーをそのまま永続作業領域として使うことはできない。PC停止中を必須にするなら、クラウドの予約タスクとGitHub接続の組合せを別途実証するか、電源を維持するローカル/既存マシンを使用する。クラウド経路をこの実装で動作検証済みとは扱わない。APIへの切替案は採用しない。

05:30〜06:45の収集・生成・審査は一つのCodex実行内の目標時間であり、各時刻の処理完了は保証しない。07:00公開はActionsで分離。GitHubのscheduleには遅延・キュー破棄の可能性があり、ビルドと配信反映もあるため7時ちょうどは保証できない。精密な時刻制御には予約記事を非公開ストレージへ置く配信時ゲート等が必要で、今回の純静的構成には含めない。07:10の定時監視も未設定で、現在はデプロイ直後の確認を実装した。

## 無料枠・料金

利用枠・追加費用は契約と実行量に依存します。購入・契約変更を自動では行いません。

Cloudflare Pages Freeは月500ビルド等の制限がありpages.devを利用できる。Supabase FreeはDB 500MB、2アクティブプロジェクト、非活動1週間で停止等の制限がある。GitHub Actionsの無料利用枠はリポジトリの公開状態・契約・ランナーに依存する。無料枠の変更、通信量、ストレージ、記事数増加による課金の可能性を運用前に確認する。

## 実行したテストと残課題

実RSS：初回37件保存、再取得で37件重複排除＋新着1件。全クラブ設定後に再分類。Codex生成の個別記事・朝刊各1本をneeds_reviewで保存し、出典HEAD応答200も確認。権利未確認・報道ベースのclaimにより公開不可。静的出力は公開記事0件、104ページで、未来の未承認記事URLは404。

SQLite/Alembic upgrade head・23件のunittest・Python compile・Vite本番ビルド・FastAPI API応答・ブラウザー画面とクラブ直接URLを検証。PostgreSQLはDDLコンパイルのみ。模擬ソースの許諾はテスト用であり実媒体の許諾ではない。

未完了：意味的な全自動イベント統合、全クラブ公式の取得網羅、信頼度の高度判定、全本文の自動事実検証、校閲管理画面、クラウド予約の実行、GitHub送信、Supabase実接続、Cloudflare実配信、07:10定期監視、実サイト失敗時の自動ロールバック、運営者・プライバシー・問い合わせページ、AdSense申請。本番サイトと収益化の完成を主張しない。

## 確認した公式資料

- [OpenAI Scheduled tasks](https://learn.chatgpt.com/docs/automations)
- [OpenAI pricing / usage](https://learn.chatgpt.com/docs/pricing)
- [GitHub schedule制限](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Cloudflare Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
- [Supabase pricing](https://supabase.com/pricing)
- クラブ名簿の出典はconfig/teams.yamlの各roster_referenceに記録。
