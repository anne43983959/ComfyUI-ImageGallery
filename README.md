# ComfyUI-ImageGallery

![icon](static/icon.svg)

A same-origin image gallery page for ComfyUI: browse, search and compare everything you have ever
generated in `output/` (and `temp/`), with workflow-aware families and metadata extracted
straight from the PNG chunks — no sidecar files, no re-upload, no extra service.

ComfyUI 的原生图片画廊页：直接读取 `output` 与 `temp` 目录，从 PNG 块里抽取提示词 / 模型 /
结构指纹，提供全文搜索、模型筛选、同源族链与差异对比、收藏标签与回收站。零构建前端、零新增依赖。

## Features / 功能

| | |
|---|---|
| **Index / 索引** | Full scan of the library; 6.5k files in ~0.8 s, incremental afterwards. SQLite (WAL) + FTS5 trigram. |
| **Search / 搜索** | Prompt + filename + model full-text search (Chinese-friendly substring match), regex mode, relevance ranking, search history. |
| **Facets / 筛选** | Model facets (any/all), directory tree, date/size/name sorting, draft-folder hiding, favorites, custom tags. |
| **Families & diff / 族链与差异** | Four-signal similarity (structure 0.40 / edges 0.20 / input keys 0.15 / text 0.25), gap-aware session splitting, node-level diff (added, removed, number, text fragments, random fields). |
| **Viewer / 浏览** | Virtualized grid with a thumbnail-size slider, in-page maximize lightbox (click the left/right half of the image to page), detail drawer, side-by-side compare, active-card outline. |
| **Actions / 操作** | Send workflow back to ComfyUI, save-as (batch, with manifest), soft delete to trash with per-file restore, batch rename (template), move to folder, JSON export/import of tags and favorites. |
| **Media / 媒体** | Videos are indexed too: duration and cover frame through the bundled `imageio_ffmpeg`. |
| **Cost control / 资源占用** | One background worker with a four-tier duty ladder plus AIMD back-off, so indexing never fights an interactive request or a running generation. Thumbnails are value-score recycled under a quota. |

## Install / 安装

```
cd ComfyUI/custom_nodes
git clone https://github.com/anne43983959/ComfyUI-ImageGallery
```

Restart ComfyUI, then click the gallery icon in the top bar, or open
`http://127.0.0.1:8188/gallery` directly.

No dependencies beyond what ComfyUI already ships (aiohttp, Pillow, Jinja2, imageio_ffmpeg).
The index and thumbnails live in `cache/` inside the plugin folder; delete it to rebuild from scratch.

## Tests / 测试

```
python tools\smoke_test.py     # page, static assets and routes, no ComfyUI needed
python tools\backend_test.py   # scanner / extractor / diff against a real library (read-only)
python tools\api_test.py       # 40+ API checks on a synthetic workspace
python tools\accept_test.py    # QoS ladder, thumbnail quota pressure, 100k-row scale, i18n
python tools\dev_server.py 39420 [--synthetic]   # standalone dev server for UI work
```

Measured on an NVMe library: first page 0.5 ms, folder page 0.4 ms, FTS substring query 1.9 ms,
folder aggregation 46.5 ms over 100 000 rows, and 100 000 rows insert in 1.7 s.

## Development / 开发

Source of truth is this repository; the copy inside `custom_nodes/` is a deployment target.

```
pwsh -File tools\deploy.ps1            # deploy a copy into custom_nodes
pwsh -File tools\deploy.ps1 -Remove    # remove the copy
pwsh -File tools\deploy.ps1 -WhatIf    # list what would be copied/removed
```

Editing `static/` or `templates/` only needs a page refresh; editing `py/` needs a ComfyUI restart.

## Layout / 目录

```
__init__.py           ComfyUI entry: WEB_DIRECTORY + route registration (fully guarded)
py/config.py          paths, version, session window
py/store.py           SQLite schema, FTS5, per-thread connections, regexp UDF
py/meta.py            PNG chunk reader and metadata extraction
py/diff.py            structure fingerprint, similarity signals, node-level diff, summaries
py/scanner.py         directory walk, incremental indexing, family chaining
py/thumbs.py          two-tier WebP pipeline, value score, quota recycling
py/video.py           duration + cover frame for videos
py/jobs.py            single background worker, QoS ladder, hourly maintenance
py/routes/            pages, health, static mount, REST API
templates/            Jinja2 page shell
static/               zero-build ES module frontend
web/gallery_button.js top-bar button, workflow injection channel
tools/                deploy + test scripts
```

## Design notes / 设计要点

- Metadata is read **read-only** from PNG chunks; the workflow JSON itself is never copied into the
  database — only its byte range, and it is read back on demand.
- Families are built from consecutive images in time order with hard break rules (time gap, size
  ratio, structure distance); diff summaries are regenerated on read so they can be localized.
- The background worker is a single daemon thread: scan, parse, chain and thumbnail recycling never
  run concurrently, and its pacing follows the four-tier ladder in `py/jobs.py`:
  idle 60 ms / idle+viewer 10 ms / generating+viewer 60 ms / generating+no-viewer 200 ms, plus AIMD
  back-off when the request p95 exceeds 30 ms.
- Deletions go to `.gallery_trash/` with a manifest, renames and moves update the index in the same
  operation, and nothing outside the configured roots is ever touched.

## Progress / 进度

See [CHANGELOG.md](CHANGELOG.md) for the staged build history.

## License

MIT — see [LICENSE](LICENSE).
