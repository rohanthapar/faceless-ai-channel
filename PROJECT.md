# Autonomous Faceless AI Media Channel — Project Deep Dive

> **Why it was built, how it works, and what every component does.**

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [The Opportunity](#2-the-opportunity)
3. [Business Objective](#3-business-objective)
4. [Why "Faceless"?](#4-why-faceless)
5. [Design Philosophy](#5-design-philosophy)
6. [System Architecture](#6-system-architecture)
7. [Component Breakdown](#7-component-breakdown)
   - [Research Engine](#71-research-engine)
   - [Content Engine](#72-content-engine)
   - [Media Engine](#73-media-engine)
   - [Video Editor](#74-video-editor)
   - [Quality Control](#75-quality-control)
   - [Publishing Layer](#76-publishing-layer)
   - [Analytics Engine](#77-analytics-engine)
   - [Pipeline Orchestrator](#78-pipeline-orchestrator)
   - [Web Dashboard](#79-web-dashboard)
8. [Technology Choices & Rationale](#8-technology-choices--rationale)
9. [Data Model](#9-data-model)
10. [The Daily Pipeline — Step by Step](#10-the-daily-pipeline--step-by-step)
11. [Cost Analysis](#11-cost-analysis)
12. [Failure Modes & Resilience](#12-failure-modes--resilience)
13. [Constraints & Limitations](#13-constraints--limitations)
14. [Future Roadmap](#14-future-roadmap)
15. [Setup Requirements](#15-setup-requirements)

---

## 1. The Problem

Creating consistent, high-quality short-form video content for social media is:

- **Time-intensive** — a single 30-second Reel or Short can take 2–4 hours end-to-end (research, scripting, recording, editing, captioning, uploading)
- **Expensive** — professional editors, voiceover artists, and stock media licensing add up fast
- **Mentally draining** — the need to constantly generate fresh ideas causes "creator burnout"
- **Inconsistent** — human creators miss posting days, lose momentum, and drift in quality

Most creators who fail on YouTube or Instagram don't fail due to lack of talent — they fail due to **lack of consistency**. The algorithm rewards channels that post daily. Humans can't sustainably post daily forever.

---

## 2. The Opportunity

Short-form video is the **fastest-growing content format** on the internet:

- YouTube Shorts: **70 billion daily views** (2024)
- Instagram Reels: **3.5 billion daily shares** (2024)
- TikTok: **1 billion monthly active users**

The barrier to entry is *low* for niches that don't require on-camera presence:
- Psychology facts
- Historical events
- Technology explainers
- Viral storytelling / Reddit stories

These topics are **infinitely scalable** via AI — no filming equipment, no studio, no on-camera talent required.

---

## 3. Business Objective

Build a **zero-touch, fully autonomous content machine** that:

| Goal | Target |
|---|---|
| Output frequency | 1 original video per day |
| Format | Vertical short-form (9:16, 1080×1920) |
| Duration | 15–30 seconds |
| Platforms | YouTube Shorts + Instagram Reels |
| Language | Global English |
| Budget | Free or near-free (utilising free API tiers) |
| Human intervention | Zero for routine operation |

The end result is a **passive digital asset** — a channel that grows its audience and generates ad revenue, sponsorship opportunities, or affiliate income with no daily human input.

---

## 4. Why "Faceless"?

A "faceless" channel deliberately avoids showing any person on screen. Instead, it uses:

- **AI-generated voiceover** (Edge TTS / Piper TTS)
- **Licensed stock footage and images** (Pexels, Pixabay — both royalty-free)
- **Animated text captions** burned directly into the video
- **Brand watermark** and consistent visual style

**Advantages of going faceless:**

1. **No equipment needed** — no camera, no microphone, no lighting
2. **No personal brand dependency** — the channel survives creator absence
3. **Easier to outsource or sell** — the asset is the channel, not the person
4. **Privacy** — no personal identity exposed
5. **Parallelisable** — the same system can run multiple channels simultaneously

---

## 5. Design Philosophy

### 5.1 Fully Autonomous by Default

Every step must work without human input. Where credentials or manual auth are required (OAuth tokens, API keys), the system provides clear one-time setup scripts and then runs unattended forever.

### 5.2 Resume-on-Failure

The pipeline uses a **state machine with persistence**. Each stage saves its output to the database before proceeding. If the system crashes mid-run (power cut, OOM, network drop), the next run picks up exactly where it left off — no wasted API calls, no duplicate uploads.

### 5.3 Free Tier First

Every external service is chosen for its generous free tier:

| Service | Free Tier Limit |
|---|---|
| Gemini API | 15 requests/minute, 1M tokens/day |
| Pexels API | 200 requests/hour, unlimited |
| Pixabay API | 100 requests/minute |
| Edge TTS | Unlimited (Microsoft's Azure Speech, free client) |
| YouTube Data API v3 | 10,000 quota units/day (~6 uploads) |
| Instagram Graph API | No hard rate limit for Reels publishing |

### 5.4 Factual Integrity

The platform is not a misinformation machine. Every topic goes through:
- Source-based fact collection from RSS feeds
- Heuristic reliability scoring (source recency, keyword filtering)
- Optional Gemini-powered fact verification

### 5.5 Modularity

Each engine (research, content, media, editor, publishing) is an **independent Python module** with a clean async interface. Swapping Edge TTS for ElevenLabs, or Pexels for another provider, requires changing one file — not rewriting the pipeline.

---

## 6. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        APScheduler (Cron)                        │
│                    Daily trigger at 09:00 IST                    │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Daily Pipeline State Machine                    │
│   (app/scheduler/daily_pipeline.py — 548 lines)                  │
│                                                                   │
│  SELECT_TOPIC → WRITE_SCRIPT → CREATE_STORYBOARD                 │
│    → GENERATE_VOICE → GENERATE_VISUALS → EDIT_VIDEO              │
│    → GENERATE_METADATA → QUALITY_CHECK                           │
│    → PUBLISH_YOUTUBE → PUBLISH_INSTAGRAM → COMPLETE              │
└──┬──────────┬──────────┬──────────┬──────────┬──────────────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
Research   Content    Media      Editor    Publishing
Engine     Engine     Engine     Engine      Layer
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
RSS Feeds  Gemini AI  Edge TTS  FFmpeg     YouTube API
Trending   Scriptwr.  Pexels    1080×1920  Instagram
Scoring    Storyboard Pixabay   30fps MP4  Graph API
Fact-chk   Metadata   Whisper   Subtitles
                      Captions
                            │
                            ▼
              ┌─────────────────────────┐
              │   SQLite Database        │
              │  (async SQLAlchemy)      │
              │  Topics, Scripts,        │
              │  Videos, Publications,  │
              │  Analytics, Jobs        │
              └─────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │    FastAPI Dashboard     │
              │  http://localhost:8000   │
              │  REST API + Dark UI      │
              └─────────────────────────┘
```

---

## 7. Component Breakdown

### 7.1 Research Engine

**Location:** `app/research/`

#### `discovery.py` — Topic Discovery
- Scrapes **15+ RSS feeds** across 4 content pillars:
  - 🧠 Psychology (PsychologyToday, ScienceDaily, BPS)
  - 📜 History (HistoryExtra, Smithsonian, National Geographic)
  - 💻 Technology (Wired, Ars Technica, MIT Tech Review)
  - 📖 Storytelling (Reddit r/TIFU, r/AskReddit, r/CrazyStories)
- Deduplicates against previously used topics (SHA-256 title hash)
- Returns `RawTopic` dataclass with title, summary, source URL, publication date

#### `trend_analyzer.py` — Trend Scoring
- Scores each topic 0–1 based on:
  - **Recency score**: topics < 24h old score 1.0, decay over 7 days
  - **Keyword virality**: matches against 50+ viral trigger words ("shocking", "never knew", "mind-blowing", etc.)
  - **Source authority**: weighted by domain trust score
  - **Pillar balance**: boosts underrepresented content pillars to maintain configured distribution

#### `fact_checker.py` — Factual Verification
- Extracts factual claims from topic summaries
- Checks claims against source text using keyword overlap scoring
- When Gemini API key is set: performs LLM-based fact verification
- Returns reliability score (0–1) and verification notes

---

### 7.2 Content Engine

**Location:** `app/content/`

#### `topic_selector.py` — Daily Topic Selection
- Runs the full research pipeline (discover → score → verify)
- Applies pillar distribution weighting (Psychology 30%, History 25%, Tech 25%, Storytelling 20%)
- Selects the top-scoring topic that hasn't been used before
- Saves to database with status `SELECTED`

#### `scriptwriter.py` — Script Generation
- Primary: **Gemini 1.5 Flash** API (free tier)
- Fallback: Template-based heuristic scriptwriter using topic title + summary
- Output format:
  - **Hook** (0–3s): grabbing first line optimised for retention
  - **Body** (3–25s): 3–5 punchy facts or story beats
  - **CTA** (25–30s): "Follow for more" / "Comment your thoughts"
- Targets 75–120 words (maps to 15–30s at natural speech pace)
- Scores script on clarity, originality, and retention likelihood

#### `storyboard.py` — Visual Planning
- Splits script into timed scenes (typically 4–6 scenes)
- Assigns visual search queries to each scene
- Chooses between image and video clip assets per scene
- Defines text overlay positions, caption timing, and transition styles
- Outputs JSON scene data persisted to database

#### `metadata_generator.py` — Platform Metadata
- Generates YouTube-optimised title (≤100 chars), description (≤5000 chars), tags (≤500 chars total)
- Generates Instagram caption (≤2200 chars) with 25–30 hashtags
- Uses Gemini API when available; falls back to template + keyword extraction

---

### 7.3 Media Engine

**Location:** `app/media/`

#### `voice_generator.py` — Text-to-Speech
- **Primary:** `edge-tts` (Microsoft Azure Neural TTS, free unofficial API)
  - Default voice: `en-US-AriaNeural` (natural, warm female voice)
  - Outputs MP3 at 192kbps
- **Fallback:** Piper TTS (local, fully offline, requires `.onnx` model download)
- **Last resort:** Silent placeholder audio (pipeline continues without voiceover)
- Measures actual audio duration via FFprobe for downstream timing

#### `image_generator.py` — Stock Asset Fetching
- Queries **Pexels** and **Pixabay** APIs based on storyboard scene keywords
- Prefers video clips over images (more engaging)
- Filters by: landscape orientation → portrait → any (graceful degradation)
- Verifies files are valid (non-zero size, correct MIME type)
- Saves attribution metadata to database for compliance

#### `subtitle_generator.py` — Caption Generation
- **Primary:** `faster-whisper` (Whisper large-v3 running locally on CPU)
  - Transcribes narration audio → word-level timestamps
  - Generates `.SRT` and `.ASS` subtitle files
- **Fallback:** Heuristic timing from script word count + audio duration
- ASS format supports styled karaoke-style captions (bold, coloured, positioned)

---

### 7.4 Video Editor

**Location:** `app/editor/`

#### `video_composer.py` — FFmpeg Pipeline
- Builds a pure **FFmpeg** `filtergraph` — no Python video library overhead
- Output spec: `1080×1920 px`, `30fps`, `4000k` video bitrate, `192k` audio, `H.264 + AAC`
- Filter chain:
  1. Scale/pad each asset to `1080×1920` with black bars (maintains aspect ratio)
  2. Ken Burns effect (slow zoom/pan) on still images for motion feel
  3. Fade in/out transitions between scenes
  4. Mix narration audio + optional background music (music ducked to 20% under voice)
  5. Burn ASS captions directly into video (not as separate track)
  6. Add brand colour overlay and optional watermark
- Runs FFmpeg as async subprocess with timeout guard

---

### 7.5 Quality Control

**Location:** `app/quality/`

#### `video_validator.py` — Technical QA
Runs the following checks before publishing:

| Check | Pass Criteria |
|---|---|
| File exists and is non-zero | ✅ Required |
| Duration | 10s ≤ duration ≤ 35s |
| Resolution | Width = 1080px, Height = 1920px |
| FPS | ≥ 29 fps |
| Video bitrate | ≥ 2000 kbps |
| Audio stream present | ✅ Required |
| Audio bitrate | ≥ 64 kbps |
| Codec | H.264 video, AAC audio |
| File size | ≤ 500 MB |

If any check fails, the pipeline aborts and logs the failing checks. The video is marked `quality_status = FAILED` and not uploaded.

---

### 7.6 Publishing Layer

**Location:** `app/publishing/`

#### `youtube.py` — YouTube Shorts Upload
- Uses **YouTube Data API v3** with **OAuth 2.0** (Desktop app flow)
- One-time token setup via `scripts/setup_youtube_auth.py`
- Token persists via pickle file — refreshes automatically
- Uses **resumable upload** protocol (handles large files, network interruptions)
- Sets video as a Short automatically (vertical + ≤60s = auto-classified as Short)
- Respects `YOUTUBE_PRODUCTION_MODE` flag — in dev mode, logs upload intent without actually uploading

#### `instagram.py` — Instagram Reels Publishing
- Uses **Meta Graph API** (Instagram Professional accounts only)
- Two-step publish flow:
  1. Create media container (POST video URL to Meta's servers)
  2. Poll for processing completion (up to 5 minutes)
  3. Publish the container to the feed
- Requires a publicly reachable HTTPS URL to the video file (Meta fetches it directly)
- Respects `INSTAGRAM_PRODUCTION_MODE` flag

---

### 7.7 Analytics Engine

**Location:** `app/analytics/`

#### `performance.py` — Engagement Tracking
- Scheduled snapshots at: **1h, 24h, 7d, 30d** after publication
- Fetches from YouTube Analytics API: views, likes, comments, watch time, avg view duration, avg view percentage
- Fetches from Instagram Graph API: reach, likes, comments, shares, saves
- Calculates engagement rate: `(likes + comments + shares) / views`
- Generates weekly performance report (top/bottom performers by pillar)
- Surfaces actionable insights: which topics resonate most, optimal posting time

---

### 7.8 Pipeline Orchestrator

**Location:** `app/scheduler/`

#### `daily_pipeline.py` — State Machine (548 lines)
The core of the system. Implements a **resumable, 11-stage production pipeline**:

```
Stage 1:  SELECT_TOPIC         — Research + score + choose topic
Stage 2:  WRITE_SCRIPT         — Generate narration script
Stage 3:  CREATE_STORYBOARD    — Plan visual scenes
Stage 4:  GENERATE_VOICE       — TTS audio narration
Stage 5:  GENERATE_VISUALS     — Fetch stock footage/images
Stage 6:  EDIT_VIDEO           — FFmpeg composition
Stage 7:  GENERATE_METADATA    — YouTube/Instagram titles, tags, captions
Stage 8:  QUALITY_CHECK        — Technical validation
Stage 9a: PUBLISH_YOUTUBE      — Upload to YouTube Shorts
Stage 9b: PUBLISH_INSTAGRAM    — Publish to Instagram Reels
Stage 10: COMPLETE             — Cleanup, notifications, analytics scheduled
```

**Resumption mechanism:** Each stage saves its output to `Job.context` (a JSON column) before advancing. If the process dies at stage 6, the next run:
1. Loads the existing `Job` record
2. Sees `context.audio_path` and `context.scene_assets` already exist
3. Skips stages 1–5
4. Resumes from stage 6 (video composition)

#### `job_manager.py` — APScheduler Integration
- Creates an `AsyncIOScheduler` tied to the FastAPI async event loop
- Registers the daily pipeline job with a `CronTrigger` (configurable time + timezone)
- Provides `start_scheduler()` / `stop_scheduler()` for FastAPI lifespan hooks
- Guards against concurrent duplicate pipeline runs

---

### 7.9 Web Dashboard

**Location:** `app/api/` + `app/templates/`

A dark-mode monitoring and control interface built with **FastAPI + Jinja2**:

| URL | Description |
|---|---|
| `GET /` | Live dashboard — stats, recent topics, recent videos |
| `POST /api/pipeline/run` | Manually trigger a full pipeline run |
| `GET /api/pipeline/status/{job_id}` | Check job progress and current stage |
| `GET /api/pipeline/jobs` | List last 20 pipeline runs |
| `GET /api/topics/` | Browse all discovered topics |
| `GET /api/videos/` | Browse all produced videos |
| `GET /api/videos/{id}/download` | Download a rendered MP4 |
| `GET /api/analytics/summary` | Aggregate engagement stats |
| `GET /docs` | Auto-generated OpenAPI / Swagger UI |

---

## 8. Technology Choices & Rationale

| Technology | Why chosen |
|---|---|
| **Python 3.12** | Best-in-class async support, rich ML/media ecosystem |
| **FastAPI** | Async-native, auto OpenAPI docs, lightning fast |
| **SQLAlchemy async + aiosqlite** | No database server needed; SQLite is sufficient for 1 video/day throughput |
| **APScheduler** | Mature, async-compatible, cron + interval triggers, in-process (no Redis/Celery needed) |
| **FFmpeg** | Industry standard, battle-tested, handles every codec/container, no Python video lib bloat |
| **faster-whisper** | 4× faster than OpenAI Whisper, runs on CPU, word-level timestamps for karaoke captions |
| **edge-tts** | Free, neural-quality TTS using Microsoft's Azure infrastructure, zero cost |
| **Piper TTS** | Fully offline fallback, runs on CPU, good quality for a local model |
| **Pexels + Pixabay** | Both offer royalty-free commercial-use media via free API tiers |
| **Gemini 1.5 Flash** | Most generous free tier of any capable LLM (1M tokens/day free), fast, multimodal |
| **Google YouTube Data API v3** | Official, stable, supports resumable uploads, free quota sufficient for 1 video/day |
| **Meta Graph API** | Official Instagram publishing API for Professional accounts |
| **Pydantic v2 + pydantic-settings** | Type-safe config management from env vars, zero boilerplate |
| **Docker** | One-command deployment, reproducible environment, volume mounts for persistent data |

---

## 9. Data Model

```
Topic (1) ──────────────────── Script (N)
  │                               │
  │                               ▼
  │                          Storyboard (1)
  │                               │
  │                               ▼
  │                           Asset (N)
  │
  └────────────────────────── Video (N)
                                  │
                          ┌───────┴────────┐
                          ▼                ▼
                    VideoMetadata    Publication (N)
                                          │
                                          ▼
                                      Analytics (N)

Job (standalone — tracks pipeline execution state)
```

### Key Design Decisions

- **Topics are reusable** — one topic can generate multiple videos (e.g., re-runs with different style)
- **Publications are platform-specific** — YouTube and Instagram are separate records for independent status tracking
- **Job.context is a JSON blob** — enables the resume-on-failure pattern without schema migrations per stage
- **All timestamps in UTC** — timezone conversion only at display layer (dashboard shows IST)

---

## 10. The Daily Pipeline — Step by Step

Here is exactly what happens every day at 09:00 IST:

### Step 1 — Topic Discovery (≈ 30s)
The system fetches RSS feeds from 15+ sources. Each article is scored for:
- How recent it is (recency decay function)
- How many viral keywords appear in the headline
- Whether the topic has already been used (SHA-256 dedup)
- Which content pillar it belongs to and whether that pillar is underrepresented

**Output:** 1 `Topic` record saved to DB with status `SELECTED`

### Step 2 — Script Writing (≈ 10s)
Gemini 1.5 Flash receives the topic title + summary and generates a 75–120 word script structured as Hook → 3 Facts → CTA. The script is scored and saved.

**Output:** 1 `Script` record

### Step 3 — Storyboard Creation (≈ 5s)
The script is split into 4–6 timed scenes. Each scene gets:
- Visual search queries for stock media
- Text overlay content and position
- Scene duration in seconds

**Output:** 1 `Storyboard` record with scene JSON

### Step 4 — Voice Generation (≈ 20s)
Edge TTS synthesises the script into an MP3 narration file. FFprobe measures exact duration.

**Output:** `output/drafts/{job_id}/narration.mp3`

### Step 5 — Visual Asset Fetching (≈ 60s)
For each scene, Pexels then Pixabay are queried. The best-matching royalty-free video clip or image is downloaded to `output/drafts/{job_id}/assets/`.

**Output:** 4–6 image/video files + `Asset` DB records

### Step 6 — Video Composition (≈ 90s)
FFmpeg assembles:
- Background visuals (scaled/padded to 1080×1920) with Ken Burns motion
- Narration audio track
- Optional background music (from `assets/music/`) mixed at 20%
- ASS caption overlay (karaoke-style word highlighting)
- Fade transitions between scenes

**Output:** `output/rendered/{job_id}.mp4` (~15–30MB)

### Step 7 — Metadata Generation (≈ 10s)
Gemini generates platform-optimised titles, descriptions, tags, and hashtags.

**Output:** 1 `VideoMetadata` record

### Step 8 — Quality Check (≈ 5s)
FFprobe inspects the rendered MP4. All 9 technical checks run. Pipeline aborts if any fail.

### Step 9a — YouTube Upload (≈ 120s)
Resumable upload via YouTube Data API v3. The video goes live as a YouTube Short (vertical + ≤60s = auto Short).

**Output:** 1 `Publication` record with YouTube video ID and URL

### Step 9b — Instagram Publish (≈ 180s)
Meta Graph API two-step flow: create container → poll for processing → publish. Non-blocking — Instagram failure doesn't prevent YouTube success.

**Output:** 1 `Publication` record with Instagram media ID

### Step 10 — Complete
- Video file copied to `output/published/`
- Topic status updated to `PUBLISHED`
- Analytics snapshots scheduled for 1h, 24h, 7d, 30d
- Operator webhook notification sent (if configured)

**Total wall-clock time:** approximately **8–10 minutes** per video

---

## 11. Cost Analysis

| Service | Usage | Monthly Cost |
|---|---|---|
| Gemini 1.5 Flash | ~90 requests/month (3 per video) | **Free** (1M tokens/day free tier) |
| Pexels API | ~180 requests/month | **Free** |
| Pixabay API | ~90 requests/month (fallback) | **Free** |
| Edge TTS | ~30 narrations/month | **Free** |
| faster-whisper | Local CPU inference | **Free** |
| FFmpeg | Local encoding | **Free** |
| YouTube Data API | ~30 uploads × ~1600 quota units = 48,000 units | **Free** (10,000 units/day = 300,000/month) |
| Instagram Graph API | ~30 publishes | **Free** |
| Hosting (VPS/Mac) | 1 CPU, 2 GB RAM sufficient | $0–$6/month |
| **Total** | | **$0–$6/month** |

---

## 12. Failure Modes & Resilience

| Failure | Behaviour |
|---|---|
| RSS feed down | Skips that source, continues with others |
| No suitable topic found | Pipeline exits gracefully, retries next day |
| Gemini API quota exhausted | Falls back to template-based scriptwriting |
| Pexels API down | Tries Pixabay; falls back to coloured placeholder |
| Edge TTS failure | Tries Piper TTS; falls back to silent audio |
| FFmpeg crash | Pipeline marks stage FAILED, saves context for resume |
| Quality check fails | Video NOT uploaded; pipeline reports failure |
| YouTube quota exceeded | Publication marked FAILED; Instagram proceeds |
| Instagram API error | Non-blocking; logged and skipped |
| Process crash mid-pipeline | Next run auto-resumes from last completed stage |
| Network interruption during upload | Resumable upload protocol retries from byte offset |

---

## 13. Constraints & Limitations

### Current Limitations

1. **No A/B testing** — the system picks one topic per day, not multiple candidates for comparison
2. **No thumbnail generation** — YouTube thumbnails are auto-generated by YouTube from video frames
3. **No TikTok** — TikTok's API doesn't support direct video upload from third-party tools reliably
4. **English only** — multi-language support would require multilingual TTS voices and translated scripts
5. **SQLite** — not suitable if scaling to multiple parallel channels (switch to PostgreSQL)
6. **CPU-only inference** — faster-whisper on CPU is 4× faster than real-time but slower than GPU
7. **Instagram requires public URL** — the server must be publicly reachable for Meta to fetch video files

### Known Non-Issues

- **Copyright:** All media is sourced from royalty-free APIs (Pexels, Pixabay) with commercial-use licences
- **Spam/policy:** Videos are original AI-generated content, not reposts — compliant with platform ToS
- **Quality drift:** Quality checks enforce minimum technical standards on every video before upload

---

## 14. Future Roadmap

### Short-term (v1.1)
- [ ] AI thumbnail generation using Imagen 3 or DALL·E
- [ ] A/B test two topic candidates, auto-pick based on historical pillar performance
- [ ] TikTok publishing via unofficial API fallback
- [ ] Multi-channel support (run N channels simultaneously with separate configs)

### Medium-term (v2.0)
- [ ] Voice cloning — use a custom voice trained on 5 minutes of sample audio
- [ ] Dynamic music selection — match music mood to topic emotional tone
- [ ] Trend spikes detection via Google Trends API for reactive content
- [ ] PostgreSQL migration for multi-channel and multi-instance deployment
- [ ] Celery + Redis for distributed task execution

### Long-term (v3.0)
- [ ] Multi-language channels (Spanish, Hindi, Portuguese — 3B+ combined speakers)
- [ ] Comment reply bot (AI-generated replies to increase engagement signals)
- [ ] Sponsor integration — auto-insert 3-second mid-roll sponsor mentions
- [ ] Revenue dashboard — connect YouTube AdSense API and track RPM

---

## 15. Setup Requirements

### One-time Setup (Human Required)

| Task | Time | Instructions |
|---|---|---|
| Get Gemini API key | 2 min | [aistudio.google.com](https://aistudio.google.com/app/apikey) → Create API key |
| Get Pexels API key | 2 min | [pexels.com/api](https://www.pexels.com/api/) → Join |
| Get Pixabay API key | 2 min | [pixabay.com/api/docs](https://pixabay.com/api/docs/) → Sign up |
| YouTube OAuth setup | 10 min | Google Cloud Console → YouTube Data API v3 → OAuth Desktop client → download JSON → run `python scripts/setup_youtube_auth.py` |
| Instagram Graph API | 20 min | developers.facebook.com → Create App → Instagram Graph API → Connect Instagram Pro account → Generate long-lived token |
| Deploy server or run locally | 5 min | `make setup && make dev` or `make docker-up` |

### Ongoing (Zero human input required after setup)

- Pipeline runs daily at configured time
- Tokens auto-refresh (OAuth2 refresh tokens, long-lived Instagram tokens last 60 days)
- Operator webhook alerts on failures (Slack/Discord)

### Minimum Hardware Requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores | 4 cores |
| RAM | 2 GB | 4 GB |
| Disk | 10 GB | 50 GB |
| OS | macOS 12+ / Ubuntu 22+ | Ubuntu 22.04 LTS |
| Python | 3.11+ | 3.12 |
| FFmpeg | 6.0+ | 7.0+ |

---

*This document was written alongside the codebase to serve as a permanent reference for contributors, operators, and anyone evaluating the project.*

*Last updated: November 2026*
