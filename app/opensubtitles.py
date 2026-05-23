import os
import re
import struct
import time
import logging
from pathlib import Path
from typing import Optional

import requests
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

SUBTITLE_CACHE_DIR = Path("./subtitle_cache")
BASE_URL = "https://api.opensubtitles.com/api/v1"


class Settings(BaseSettings):
    opensubtitles_api_key: str = ""
    opensubtitles_app_name: str = "EpisodeMatcherApp"
    opensubtitles_app_version: str = "2.0"

settings = Settings()


def compute_hash(filepath: str) -> tuple[str, int]:
    """
    Compute the OpenSubtitles movie hash for a video file.
    Algorithm: sum of file size + all 8-byte LE integers from first and last 64KB,
    masked to 64-bit unsigned, formatted as a 16-char hex string.
    """
    filesize = os.path.getsize(filepath)
    fmt = '<q'  # little-endian signed 64-bit
    chunk_size = 65536
    byte_size = struct.calcsize(fmt)

    hash_value = filesize
    with open(filepath, 'rb') as f:
        for _ in range(chunk_size // byte_size):
            chunk = f.read(byte_size)
            if len(chunk) < byte_size:
                break
            hash_value += struct.unpack(fmt, chunk)[0]

        f.seek(max(0, filesize - chunk_size))
        for _ in range(chunk_size // byte_size):
            chunk = f.read(byte_size)
            if len(chunk) < byte_size:
                break
            hash_value += struct.unpack(fmt, chunk)[0]

    return format(hash_value & 0xFFFFFFFFFFFFFFFF, '016x'), filesize


class OpenSubtitlesClient:
    def __init__(self):
        self.api_key = settings.opensubtitles_api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": f"{settings.opensubtitles_app_name} {settings.opensubtitles_app_version}",
        })
        self._last_request = 0.0
        self._quota_exceeded = False
        SUBTITLE_CACHE_DIR.mkdir(exist_ok=True)

    def _throttle(self):
        """Keep requests spaced at least 500ms apart (free tier: 5 req/sec max)."""
        elapsed = time.time() - self._last_request
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)
        self._last_request = time.time()

    def hash_lookup(self, file_hash: str, filesize: int) -> Optional[dict]:
        """
        Query OpenSubtitles by file hash. Returns {season, episode, title} or None.
        This is the fastest path — no audio extraction or transcription needed.
        """
        if not self.api_key:
            logger.warning("OPENSUBTITLES_API_KEY not set; skipping hash lookup.")
            return None

        self._throttle()
        try:
            resp = self.session.get(
                f"{BASE_URL}/subtitles",
                params={"moviehash": file_hash, "moviebytesize": filesize, "type": "episode"},
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
        except requests.RequestException as e:
            logger.error(f"Hash lookup request failed: {e}")
            return None

        for item in results:
            attrs = item.get("attributes", {})
            feat = attrs.get("feature_details", {})
            season = feat.get("season_number")
            episode = feat.get("episode_number")
            if season is not None and episode is not None:
                logger.info(f"Hash lookup: S{int(season):02d}E{int(episode):02d} — {feat.get('title', '?')}")
                return {"season": int(season), "episode": int(episode), "title": feat.get("title", "")}

        logger.info(f"Hash lookup: no episode match for hash {file_hash}")
        return None

    def _cache_path(self, show_name: str, season: int, episode: int) -> Path:
        slug = re.sub(r'[^\w\s-]', '', show_name).strip().lower().replace(' ', '_')
        return SUBTITLE_CACHE_DIR / slug / f"S{season:02d}E{episode:02d}.txt"

    def get_episode_subtitle_text(self, show_name: str, season: int, episode: int) -> Optional[str]:
        """
        Return plain-text dialogue for one episode. Downloads from OpenSubtitles on first
        call and caches locally so subsequent runs are instant.
        """
        cache = self._cache_path(show_name, season, episode)
        if cache.exists():
            return cache.read_text(encoding="utf-8")

        if not self.api_key:
            return None

        if self._quota_exceeded:
            logger.debug(f"Skipping subtitle download for S{season:02d}E{episode:02d} because quota was exceeded.")
            return None

        # Search for English subtitles for this episode
        self._throttle()
        try:
            resp = self.session.get(
                f"{BASE_URL}/subtitles",
                params={
                    "query": show_name,
                    "season_number": season,
                    "episode_number": episode,
                    "type": "episode",
                    "languages": "en",
                },
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("data", [])
        except requests.RequestException as e:
            logger.error(f"Subtitle search failed for S{season:02d}E{episode:02d}: {e}")
            if e.response is not None and e.response.status_code in (403, 429):
                logger.error("OpenSubtitles API quota exceeded or rate limited. Disabling further downloads for this run.")
                self._quota_exceeded = True
            return None

        if not results:
            logger.info(f"No subtitles found for {show_name} S{season:02d}E{episode:02d}")
            return None

        # Pick the subtitle with the most downloads (most vetted/accurate)
        try:
            best = max(results, key=lambda x: x["attributes"].get("download_count", 0))
            file_id = best["attributes"]["files"][0]["file_id"]
        except (KeyError, IndexError) as e:
            logger.error(f"Could not extract file_id from search results: {e}")
            return None

        # Request download link
        self._throttle()
        try:
            dl_resp = self.session.post(
                f"{BASE_URL}/download",
                json={"file_id": file_id},
                timeout=15,
            )
            dl_resp.raise_for_status()
            download_url = dl_resp.json().get("link")
            if not download_url:
                logger.error("Download response missing 'link' field.")
                return None
        except requests.RequestException as e:
            logger.error(f"Subtitle download request failed: {e}")
            if e.response is not None and e.response.status_code in (403, 429):
                logger.error("OpenSubtitles API quota exceeded or rate limited on download. Disabling further downloads for this run.")
                self._quota_exceeded = True
            return None

        # Fetch the SRT file content
        try:
            srt_resp = requests.get(download_url, timeout=30)
            srt_resp.raise_for_status()
            text = self._parse_srt(srt_resp.text)
        except requests.RequestException as e:
            logger.error(f"SRT file fetch failed: {e}")
            return None

        # Cache the plain text
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
        logger.info(f"Cached subtitle for {show_name} S{season:02d}E{episode:02d} ({len(text)} chars)")
        return text

    def _parse_srt(self, content: str) -> str:
        """Extract plain dialogue lines from SRT subtitle content."""
        blocks = re.split(r'\n\n+', content.strip())
        lines = []
        for block in blocks:
            parts = block.strip().splitlines()
            # SRT block: index line, timing line, then dialogue
            for line in parts[2:]:
                line = re.sub(r'<[^>]+>', '', line).strip()
                if line:
                    lines.append(line)
        return ' '.join(lines)


# Module-level singleton
os_client = OpenSubtitlesClient()
