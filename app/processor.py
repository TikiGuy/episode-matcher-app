import os
import re
import glob
import logging
from typing import Callable, Optional

from app.audio import extract_audio_chunk, transcribe_audio
from app.metadata import fetch_show_metadata
from app.matcher import match_episode
from app.opensubtitles import settings as os_settings

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = ("*.mkv", "*.mp4", "*.avi", "*.m4v", "*.mov")
ALREADY_RENAMED_RE = re.compile(r'[sS]\d{2}[eE]\d{2}')
FILENAME_TRANSLATION_TABLE = str.maketrans('', '', '<>:"/\\|?*')


def sanitize_filename(name: str) -> str:
    # Use str.translate for faster sanitization of multiple characters
    return name.translate(FILENAME_TRANSLATION_TABLE).strip()


def _find_video_files(directory: str) -> list[str]:
    files = []
    for pattern in VIDEO_EXTENSIONS:
        files.extend(glob.glob(os.path.join(directory, pattern)))
    return sorted(files)


def process_directory(
    directory_path: str,
    show_name: str,
    progress_callback: Optional[Callable[[str, str], None]] = None,
):
    """
    Main orchestration loop.

    For each video file:
      1. Try OpenSubtitles hash lookup  (fast, no audio needed)
      2. If no hash match, transcribe with Whisper and fuzzy-match against
         cached episode subtitle text.
      3. Rename matched files to Plex-standard format: Show - SxxExx.ext
    """
    def emit(file: str, msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(file, msg)

    if not os.path.isdir(directory_path):
        emit(directory_path, f"Error: '{directory_path}' does not exist or is not a directory.")
        return

    if not os_settings.opensubtitles_api_key:
        emit("System", (
            "Warning: OPENSUBTITLES_API_KEY is not set. "
            "Hash lookup and subtitle downloading are disabled. "
            "Register for a free API key at opensubtitles.com."
        ))

    emit(directory_path, f"Fetching episode metadata for '{show_name}'...")
    episodes = fetch_show_metadata(show_name)
    if not episodes:
        emit(directory_path, f"Error: No episode metadata found for '{show_name}'. Aborting.")
        return

    video_files = _find_video_files(directory_path)
    if not video_files:
        emit(directory_path, f"No video files found in '{directory_path}'.")
        return

    emit(directory_path, f"Found {len(video_files)} video file(s). Starting identification...")

    for index, file_path in enumerate(video_files, start=1):
        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1]

        # Skip files that already look renamed (contain SxxExx pattern and show name)
        if show_name.lower() in filename.lower() and ALREADY_RENAMED_RE.search(filename):
            emit(filename, "Skipping — file appears to already be renamed.")
            continue

        emit(filename, f"[{index}/{len(video_files)}] Trying hash lookup...")

        transcript: Optional[str] = None
        result = match_episode(file_path, episodes, show_name)

        if not result:
            # Hash didn't match — fall back to Whisper transcription
            emit(filename, "Hash not in database. Extracting audio for transcript matching...")
            try:
                audio_path = extract_audio_chunk(file_path, start_time="00:04:00", duration="00:05:00")
                emit(filename, "Transcribing audio with Whisper...")
                transcript = transcribe_audio(audio_path)
            except Exception as e:
                emit(filename, f"Audio extraction/transcription failed: {e}. Skipping.")
                continue

            if not transcript or len(transcript.split()) < 20:
                emit(filename, "Transcript too short or empty. Skipping.")
                continue

            emit(filename, "Matching transcript against episode subtitles...")
            result = match_episode(file_path, episodes, show_name, transcript=transcript, skip_hash=True)

        if result:
            matched_ep, method = result
            new_filename = sanitize_filename(
                f"{show_name} - S{matched_ep.season:02d}E{matched_ep.episode:02d}{ext}"
            )
            new_filepath = os.path.join(directory_path, new_filename)

            if os.path.exists(new_filepath):
                emit(filename, f"Cannot rename: '{new_filename}' already exists.")
            else:
                os.rename(file_path, new_filepath)
                emit(new_filename, f"Renamed via {method}: '{filename}' → '{new_filename}'")
        else:
            emit(filename, "Could not identify episode. File left unchanged.")

    emit("System", "Batch processing complete.")
