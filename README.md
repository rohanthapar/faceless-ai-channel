# Autonomous Faceless AI Media Channel

> **One original short-form video, every day — fully automated.**

A production-grade Python platform that researches trending topics, writes scripts, synthesises voiceover, edits vertical video and publishes to YouTube Shorts & Instagram Reels — all without human intervention.

---

## ✨ Feature Overview

| Stage | What it does |
|---|---|
| **Research** | Scrapes RSS feeds, scores trending topics, heuristic fact-checking |
| **Content** | Gemini-powered scriptwriter, storyboard planner, metadata generator |
| **Media** | Edge TTS / Piper voice, Pexels/Pixabay stock footage, faster-whisper captions |
| **Compose** | FFmpeg 9:16 vertical video (1080×1920 @ 30 fps, 15–30 s) |
| **QC** | Technical validation (resolution, duration, bitrate, audio levels) |
| **Publish** | YouTube Shorts (OAuth resumable upload) + Instagram Reels (Graph API) |
| **Analytics** | Automatic engagement snapshots at 1 h / 24 h / 7 d / 30 d |
| **Dashboard** | Dark-mode FastAPI web UI + REST API + pipeline trigger |

---

## 🚀 Quick Start

### 1. Prerequisites

```bash
# macOS (Homebrew)
brew install ffmpeg python@3.12

# Ubuntu/Debian
sudo apt install ffmpeg python3.12 python3.12-venv
```

### 2. Setup

```bash
git clone https://github.com/akankshabawa27/faceless-ai-channel.git
cd faceless-ai-channel

make setup          # creates .venv, copies .env.example → .env, makes dirs
```

Edit `.env` and add your API keys (see [Configuration](#configuration)).

### 3. Run

```bash
make dev            # starts FastAPI at http://localhost:8000
```

Open `http://localhost:8000` to see the dashboard. Click **▶ Run Pipeline** to trigger a full cycle immediately.

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey) — free |
| `PEXELS_API_KEY` | [pexels.com/api](https://www.pexels.com/api/) — free |
| `PIXABAY_API_KEY` | [pixabay.com/api/docs](https://pixabay.com/api/docs/) — free |
| `YOUTUBE_CLIENT_SECRET_FILE` | [Google Cloud Console](https://console.cloud.google.com) → YouTube Data API v3 → OAuth 2.0 Desktop client |
| `INSTAGRAM_ACCESS_TOKEN` | [Meta for Developers](https://developers.facebook.com) → Instagram Graph API long-lived token |
| `INSTAGRAM_USER_ID` | Same Meta app — your Instagram Professional account ID |
| `INSTAGRAM_MEDIA_BASE_URL` | Public HTTPS URL your server is reachable at (Meta needs to fetch the video file) |

Set `YOUTUBE_PRODUCTION_MODE=true` / `INSTAGRAM_PRODUCTION_MODE=true` to enable real uploads (default is dry-run).

---

## 🗂️ Project Structure

```
faceless-ai-channel/
├── app/
│   ├── main.py                  # FastAPI entry-point
│   ├── config.py                # Pydantic settings
│   ├── database.py              # Async SQLAlchemy models
│   ├── logger.py                # Structured logging
│   ├── api/                     # REST + dashboard routers
│   ├── research/                # RSS discovery, trend scoring, fact-checking
│   ├── content/                 # Topic selection, scriptwriter, storyboard, metadata
│   ├── media/                   # TTS voice, stock assets, subtitles
│   ├── editor/                  # FFmpeg video composer
│   ├── quality/                 # Video validator
│   ├── publishing/              # YouTube & Instagram publishers
│   ├── scheduler/               # APScheduler cron + daily pipeline state machine
│   ├── analytics/               # Engagement snapshot collector
│   └── templates/               # Jinja2 dashboard HTML
├── assets/                      # Fonts, music, TTS models (not committed)
├── output/                      # drafts / rendered / published (not committed)
├── migrations/                  # Alembic DB migrations
├── tests/                       # pytest test suite
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── .env.example
```

---

## 🐳 Docker

```bash
# Build and run
make docker-up

# Tail logs
make docker-logs

# Stop
make docker-down
```

> Mount `./credentials` read-only so your OAuth tokens persist across container restarts.

---

## 🧪 Testing

```bash
make test           # run full test suite
make test-cov       # with HTML coverage report
```

---

## 📡 API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web dashboard |
| `POST` | `/api/pipeline/run` | Trigger pipeline now |
| `GET` | `/api/pipeline/status/{job_id}` | Job status |
| `GET` | `/api/topics/` | List topics |
| `GET` | `/api/videos/` | List videos |
| `GET` | `/api/videos/{id}/download` | Download rendered MP4 |
| `GET` | `/api/analytics/summary` | Aggregate engagement stats |
| `GET` | `/docs` | Auto-generated OpenAPI docs |

---

## 🗓️ Daily Pipeline Stages

```
discover → select_topic → write_script → create_storyboard
  → fetch_assets → generate_voice → generate_subtitles
  → compose_video → validate_quality → generate_metadata
  → publish_youtube → publish_instagram → collect_analytics
```

Each stage saves its output to the Job's `context` column so the pipeline can **resume from failure** without redoing completed work.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
