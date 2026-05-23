# Episode Matcher & Renamer

A self-hosted web app that automatically identifies and renames TV episodes ripped from DVD or Blu-ray discs. Designed to run on Unraid with an NVIDIA GPU, but works on any Docker host.

Episodes from physical media often arrive out of order with generic filenames like `title_t02.mkv`. This tool figures out which episode each file actually is and renames it to Plex-standard format (`Show - S01E02.mkv`).

## How it works

Two-stage identification pipeline, fastest method first:

**Stage 1 — File hash lookup (primary)**
Computes a deterministic hash from the first and last 64 KB of the video file and queries the [OpenSubtitles](https://www.opensubtitles.com) database. Because people upload subtitles for specific disc rips, the same rip produces the same hash. This resolves instantly with no audio processing and is the path most files will take.

*Note on hashing limitations:* The hash method requires an exact, bit-for-bit file match to what was uploaded to OpenSubtitles. If you rip and then re-encode your files (e.g., compressing with Handbrake), or keep a different set of audio tracks, the file size and byte structure change, which guarantees the hash will not match. In these cases, the system will rely entirely on the Stage 2 fallback.

**Stage 2 — Transcript matching (fallback)**
If the hash isn't in the database, the app extracts a 5-minute audio clip (skipping the intro), transcribes it locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) using your GPU, then fuzzy-matches the transcript against cached episode subtitle files. Matching real dialogue against real dialogue (rather than plot summaries) gives a meaningful confidence score. Ambiguous results are skipped rather than guessed.

Episode metadata (season/episode lists) is fetched from [TVmaze](https://www.tvmaze.com) (free, no key) or [TMDB](https://www.themoviedb.org) (optional).

## Requirements

- Docker with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed
- An NVIDIA GPU (CPU fallback works but transcription is slow)
- A free [OpenSubtitles API key](https://www.opensubtitles.com/en/consumers)

## Setup

### 1. Get an OpenSubtitles API key

Register at [opensubtitles.com/en/consumers](https://www.opensubtitles.com/en/consumers). The free tier allows 5 requests/second and 40 subtitle downloads per day. Downloads are cached locally, so each episode's subtitle is only fetched once.

### 2. Configure the compose file

Edit `compose.yaml` and fill in your values:

```yaml
environment:
  - OPENSUBTITLES_API_KEY=your_key_here   # required
  - TMDB_API_KEY=                          # optional; blank = use TVmaze
  - WHISPER_MODEL_SIZE=base               # tiny | base | small | medium | large-v3
  - WHISPER_DEVICE=cuda                   # cuda or cpu

volumes:
  - /mnt/user/media:/data:rw             # map your media share here
```

### 3. Build and start

```bash
docker compose up -d --build
```

The web UI will be available at `http://<your-ip>:8000`.

## Usage

1. Open the web UI.
2. Enter the **show name** exactly as it appears on TVmaze or TMDB (e.g. `The Office`).
3. Enter the **directory path inside the container** where the video files live (e.g. `/data/TV Shows/The Office/Season 1`).
4. Click **Start Batch Processing** and watch the live log.

Supported file types: `.mkv`, `.mp4`, `.avi`, `.m4v`, `.mov`

Files that already match the `SxxExx` naming pattern are skipped automatically.

### Output format

```
The Office - S01E03.mkv
```

This is compatible with Plex, Jellyfin, and Emby without any additional configuration.

## Configuration reference

| Environment variable | Default | Description |
|---|---|---|
| `OPENSUBTITLES_API_KEY` | *(empty)* | Required for hash lookup and subtitle downloads |
| `TMDB_API_KEY` | *(empty)* | Optional; enables TMDB for metadata instead of TVmaze |
| `WHISPER_MODEL_SIZE` | `base` | Whisper model size. `base` is fast and accurate enough for matching; `small` or `medium` improve accuracy on noisy audio |
| `WHISPER_DEVICE` | `cuda` | `cuda` for GPU, `cpu` for CPU-only |

## Notes on subtitle downloads

The OpenSubtitles free tier allows 40 subtitle downloads per day. For large shows this means the subtitle cache for the fallback path builds up over several days. Once cached, the files live in `./subtitle_cache/` and are never re-downloaded.

The hash lookup (Stage 1) does **not** count against the download limit and works regardless of how many episodes you have.

## Volumes

| Host path | Container path | Purpose |
|---|---|---|
| `/mnt/user/media` | `/data` | Your media library (read-write) |
| `./whisper_cache` | `/root/.cache/huggingface` | Whisper model files (persisted across restarts) |
| `./subtitle_cache` | `/app/subtitle_cache` | Downloaded episode subtitle text (persisted) |

## Troubleshooting

**Hash lookup always misses**
The hash lookup requires a bit-perfect match with the original file that someone uploaded subtitles for. If you re-encode your files (like compressing with Handbrake) or strip out certain audio tracks during your rip, the hash will change and the lookup will miss. The fallback transcript path will handle these custom encodes. Alternatively, consider keeping untouched 1:1 disc rips (MakeMKV produces files that match well) if you want the hash lookup to succeed.

**Transcript matching skips everything**
If no subtitles are cached yet, the fallback has nothing to compare against. Run the tool on a few known episodes first to seed the cache, or wait for the daily download quota to accumulate over a few days.

**Wrong episode matched**
Increase the confidence threshold by editing `TRIGRAM_THRESHOLD` in `app/matcher.py` (default `0.08`). A value of `0.12` or higher will be more conservative.

**GPU not detected**
Ensure the NVIDIA Container Toolkit is installed and that `nvidia-smi` works inside Docker. The app falls back to CPU automatically if CUDA fails.
