# THE TOUCHLINE v3 — database and editorial pipeline

## Target architecture
- Local: SQLite + Python ingestion/generation/QA + React static build.
- Production: Supabase Postgres for processing records; GitHub Actions for scheduled jobs; Cloudflare Pages for prerendered public pages.
- Do not expose Supabase service-role keys in the browser. Public content is exported only after verification and release.

## Source policy
- `sources`: `id`, `name`, `rss_url`, `base_url`, `language`, `active`, `access_mode` (`rss_metadata`, `licensed_fulltext`, `official_facts`), `commercial_reuse_status` (`unknown`, `restricted`, `permitted`), `license_reference`, `checked_at`, `last_success_at`.
- Acquisition of RSS titles and snippets does not confer rights to publish a full translation. Unknown rights -> metadata/facts only; block verbatim copyrighted text and source photography.
- Publisher terms and permissions should be reviewed for each source before public production usage.

## Database (16 tables)
1. `leagues(id, slug UNIQUE, name_ja, season)`
2. `teams(id, league_id FK, slug UNIQUE, name_ja, name_original, active)`
3. `team_aliases(id, team_id FK, alias, language, UNIQUE(team_id,alias))`
4. `sources(id, name, rss_url UNIQUE, access_mode, commercial_reuse_status, license_reference, checked_at, active)`
5. `raw_articles(id, source_id FK, canonical_url UNIQUE, external_id, title, snippet, language, source_published_at UTC, collected_at UTC, content_hash, ingest_status, last_error)`
6. `news_events(id, fingerprint UNIQUE, subject, event_type, event_time UTC, importance_score, confidence, status, created_at UTC, updated_at UTC)`
7. `event_sources(event_id FK, raw_article_id FK, relevance, PRIMARY KEY(event_id,raw_article_id))`
8. `event_teams(event_id FK, team_id FK, role, PRIMARY KEY(event_id,team_id))`
9. `event_facts(id, event_id FK, fact_text, certainty, fact_type, evidence_ref, verification_status)`
10. `articles(id, slug UNIQUE, article_type, primary_team_id FK NULL, edition_date DATE NULL, status, scheduled_at UTC NULL, current_version_id NULL, created_at UTC)`
11. `article_versions(id, article_id FK, version_no, title, summary, body_markdown, model_id, prompt_version, generated_at UTC, content_hash, UNIQUE(article_id,version_no))`
12. `article_events(article_id FK, event_id FK, PRIMARY KEY(article_id,event_id))`
13. `verification_results(id, article_version_id FK, checker_version, factual_status, rights_status, quality_status, checked_at UTC, details_json)`
14. `publications(id, article_id FK UNIQUE, target, status, scheduled_at UTC, released_at UTC, deployment_id, canonical_url, error_message)`
15. `pipeline_runs(id, run_key UNIQUE, stage, started_at UTC, finished_at UTC, status, counts_json)`
16. `pipeline_errors(id, run_id FK, record_type, record_id, stage, error_code, details, retry_count, resolved_at UTC)`

Foreign key constraints and uniqueness must be enforced in SQLite (`PRAGMA foreign_keys=ON`) and PostgreSQL. Add indexes on `raw_articles(source_id, source_published_at)`, `event_teams(team_id,event_id)`, `articles(status,scheduled_at)`, `pipeline_runs(stage,started_at)`. Treat raw records as immutable source observations, and keep versioned generated text.

## Article state machine
`collected -> classified -> clustered -> facts_extracted -> generated -> checked -> approved -> scheduled -> published` is a *workflow* composed from independent source/event/article/publication states. Do not force all into one status column.

- `raw_articles.ingest_status`: `new`, `normalized`, `skipped`, `error`.
- `news_events.status`: `candidate`, `clustered`, `facts_ready`, `held`.
- `articles.status`: `draft`, `needs_review`, `verified`, `approved`, `scheduled`, `published`, `rejected`, `superseded`.
- `publications.status`: `queued`, `deployed`, `live`, `failed`, `withdrawn`.

Only allow `verified -> approved` if latest `verification_results` is all pass, rights policy is permitted for underlying uses, and article has grounded fact references. High-risk/uncertain material requires manual review. An arbitrary button press cannot set verification flags.

## Scheduled workflow in JST
- 05:30 collect (RSS/allowed sources), dedupe by canonical URL and publisher ID.
- 05:40 resolve clubs, cluster events using entities/date/type and semantic similarity; ambiguous cases held.
- 05:50 extract factual claims and link to permitted evidence.
- 06:00 select important events for individual stories; generate daily editions only where sufficient verified news exists.
- 06:15 AI drafts in Japanese; generate one article per important event plus digest per team.
- 06:30 automated fact/rights checks; blocked items stay unpublished.
- 06:40 prepare immutable snapshot and pre-render article HTML + metadata + sitemap.
- 06:50 upload preview/build artifact; publish at or after 07:00 (GitHub scheduling not guaranteed exact).
- 07:10 verify live URLs and log outcomes; alert on failure.

For public Cloudflare Pages, never publish planned future articles in build assets before their intended release. A deployment that includes articles is itself the visibility gate; publishing a JSON flag alone is not sufficient.

## Idempotency keys
- raw: normalized canonical URL (unique) and optional `source_id + external_id`.
- event: stable normalized fact tuple plus entity/time, with a human-review path for collisions.
- daily: unique `(primary_team_id, edition_date, article_type)` where `article_type='daily'` (partial unique index in PostgreSQL; application logic for SQLite).
- individual: unique primary event association or explicit editorial override.
- publication: one active publication per article; keep deployment history elsewhere if desired.
- run: date + stage + attempt; no silent destructive overwrite.

## Failure and recovery
- Fetch timeout: exponential retry; no duplicate article.
- AI invalid JSON: retry with constrained schema; isolate error, do not mark as verified.
- Missing/low-quality source: `needs_review`, not publicly deployed.
- DB error: transaction rollback and error log.
- Deploy failure: do not update `published` until production URL is confirmed.
- Partial schedule overrun: publish only fully approved content, no placeholder pages.

## Migration plan from editorial-v2
1. Back up `data/football.db`; do not change in-place without migration.
2. Create Alembic initial schema for v3, support SQLite and PostgreSQL.
3. Import `teams`, `sources`, `raw_articles` with canonical URL; source-only title/summary remains metadata, not verified facts.
4. Import legacy `articles` as `draft` or `needs_review`; preserve body in `article_versions` and source reference in `event_sources` only where verified mapping is possible.
5. Never inherit old `is_demo=False` or `approved/published` as proof of rights/fact checking. Re-review all legacy items.
6. Rebuild daily digests from clustered events rather than using preexisting article-index paragraphs.
7. Add regression tests: duplicate RSS, duplicate event, retry, AI failure, source-with-restricted-rights, verification block, UTC/JST boundary, scheduled publication, stale deployment, site visibility.

## Acceptance gates for deployment
- New RSS item and repeated RSS item give the same persisted counts after retry.
- Two different source URLs for one story produce one event with two sources.
- Failed AI generation can be retried without refetching the article.
- A restricted source never appears as a full-text translation in published output.
- A daily story references only checked event facts.
- Publication is idempotent, and an unpublished draft cannot be read from static build artifacts.
- `article/:slug` resolves on direct load, has canonical/OG tags, and appears in sitemap only when published.
