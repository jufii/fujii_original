# ソース調査・権利状況（2026-10-29）

|媒体|公開記事の閲覧|RSS|全文の商用翻訳/再利用|
|---|---|---|---|
|BBC Sport|無料の記事あり（記事による）|公式RSSあり|許諾が必要。RSS見出しの掲載も条件・表示形式に注意|
|The Guardian|無料で閲覧できる記事多数|公式RSSあり|個人非商用RSSを明記。商用API等は別ライセンス|
|FC Barcelona公式|公開ニュースページを閲覧可能|RSSの公式提供は未確認|商用再利用の許諾は未確認|
|Real Madrid公式|公開ニュースを閲覧可能|RSSの公式提供は未確認|商用再利用の許諾は未確認|
|Bundesliga.com公式|公開ニュースページを閲覧可能|RSSは未確認|商用再利用の許諾は未確認|
|FC Barcelona Noticias（非公式メディア）|公開ニュースあり|RSS一覧ページあり|商用再利用権利は未確認|
|ESPN|公開記事と有料記事が混在する可能性あり|RSSあり、取得制限の報告もあり|商用翻訳・再利用権利は未確認|
|The Athletic|購読限定記事が多い|RSS存在|無断全文取得・再配信をしない|

**重要:** 自分でブラウザで無料全文を読めること ≠ 無料で機械取得・翻訳・自サイト公開できること。ローカル版はRSSの見出し・概要のみに限定し、公開はしない。原文全文は取り込まない。

確認した資料:
- https://www.bbc.co.uk/sport/articles/cqllxj2n4kyo
- https://www.bbc.co.uk/sport/22958371
- https://www.theguardian.com/help/feeds
- https://open-platform.theguardian.com/
- https://www.theguardian.com/open-platform/terms-and-conditions
- https://www.fcbarcelonanoticias.com/rss.html
- https://www.fcbarcelona.com/en/football/first-team/news
- https://www.realmadrid.com/en-US/news
- https://www.bundesliga.com/en/bundesliga/
- https://developers.google.com/search/docs/essentials/spam-policies

v3収集設定: config/sources.yaml（旧互換版のみsources.json）。BBCとGuardianのみ初期ON。商用本番での利用は別途許諾・条件を確認してから切り替えること。


v3では全ソースをcommercial_reuse_status=unknownとして初期登録し、AIには許諾確定権限を持たせません。利用許諾の証拠・人間の確認者・確認日時を登録しない限り承認しません。クラブ名簿96件の確認は記事の商用転載許諾と無関係です。
