# Changelog

All notable changes to this plugin, in build order.

## Unreleased

- Top-bar button: register through the declarative action bar and fall back to the legacy button only
  when no action bar container shows up, instead of sniffing a frontend version the frontend never
  exposes. Current frontends no longer import the deprecated `/scripts/ui/components/button.js`.

## 0.5.0 — stage 5 (acceptance and polish)

- Bilingual diff summaries: `summarize(..., lang="en")` plus `lang` on the detail and
  query endpoints; stored summaries stay Chinese and are re-rendered on read.
- Update banner: the page polls `/api/gallery/hello` and offers a refresh when the backend version
  changes, so editing static files or templates no longer needs a guessed hard reload.
- Acceptance suite (`tools/accept_test.py`): QoS ladder, thumbnail quota pressure, 100k-row scale
  and i18n — 24 checks.
- Packaging for the ComfyUI registry: `pyproject.toml`, `LICENSE`, this changelog.

## 0.4.0 — stage 4 (search, batch operations, trash)

- Search: relevance ranking (FTS5 bm25), regex mode over the merged text (SQLite `regexp` UDF),
  and search history.
- Batch rename with a template (`{name} / {n} / {ext}`, zero padding, preview) and move-to-folder;
  both update the index and FTS rows in the same operation.
- Trash: per-batch file list, selective restore, permanent purge, and automatic purge of batches older
  than `trash_keep_days` (default 30 days) from the background worker.
- Compare view for 2–4 selected images; UI language switch for the static chrome.

## 0.3.0 — stage 3 (viewer rework, video)

- Paging direction now matches the grid (left = newer, right = older); `older_id` / `newer_id` in the API.
- Double-click opens an in-page maximize lightbox; double-click or Esc closes it without closing the
  detail drawer; clicking the left or right half of the image pages back and forth.
- Sidebar: draggable width, collapsible, persisted; the grid re-flows with a transition and spreads the
  leftover space across the column gaps; thumbnail-size slider.
- Logo (`static/icon.svg`) as favicon, in-page mark and top-bar button icon.
- Videos: duration and cover frame through `imageio_ffmpeg`; files without workflow metadata no
  longer break the diff chain.
- Keyboard shortcuts with a help panel; tag and favorite export/import.

## 0.2.0 — stage 2 (filtering and organization)

- Draft folders hidden by default (configurable keywords, toolbar toggle).
- Favorites and custom tags, both protected from thumbnail recycling.
- Thumbnail recycling by value score (recency, hits, favorite, tag) under a byte and file quota.
- Side-by-side compare in the detail drawer; settings for quota and draft keywords.

## 0.1.0 — stage 1 (M0–M7)

- /gallery page, static assets, top-bar button (declarative action bar with a legacy fallback).
- Full-library scan and incremental indexing; virtualized grid; two-tier WebP thumbnails.
- PNG chunk extraction: merged searchable text, model list, structure fingerprint, dimensions.
- FTS5 search, model facets, directory tree, sorting, keyset pagination.
- Family chaining and four-signal diff with node-level detail.
- Send workflow back to ComfyUI, save-as with manifest, soft delete and restore.
