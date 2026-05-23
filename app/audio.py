import os
import subprocess
import tempfile
import logging

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    whisper_model_size: str = "base"
    whisper_device: str = "cuda"

settings = Settings()

# Lazy-loaded — only instantiated the first time transcribe_audio() is called.
# This means if every file is matched by hash, Whisper never loads and startup
# is much faster.
_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    from faster_whisper import WhisperModel

    logger.info(f"Loading Whisper model '{settings.whisper_model_size}' on '{settings.whisper_device}'...")
    try:
        _whisper_model = WhisperModel(
            settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type="float16" if settings.whisper_device == "cuda" else "int8",
        )
        logger.info("Whisper model loaded.")
    except Exception as e:
        logger.error(f"Failed to load Whisper on {settings.whisper_device}: {e}")
        logger.info("Falling back to CPU for Whisper...")
        _whisper_model = WhisperModel(settings.whisper_model_size, device="cpu", compute_type="int8")
        logger.info("Whisper model loaded on CPU.")

    return _whisper_model


def extract_audio_chunk(video_path: str, start_time: str = "00:04:00", duration: str = "00:05:00") -> str:
    """
    Extract a chunk of audio from a video file via FFmpeg.
    Returns the path to a temporary WAV file (16kHz mono, suitable for Whisper).
    """
    logger.info(f"Extracting audio from {video_path}  start={start_time}  duration={duration}")
    temp_fd, temp_path = tempfile.mkstemp(suffix=".wav")
    os.close(temp_fd)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", start_time,
        "-t", duration,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        temp_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            os.remove(temp_path)
            logger.error(f"FFmpeg error: {result.stderr}")
            raise RuntimeError("FFmpeg failed to extract audio. Check logs.")
        logger.info(f"Audio extracted to {temp_path}")
        return temp_path
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e


def transcribe_audio(audio_path: str) -> str:
    """
    Transcribe an audio file with faster-whisper. Cleans up the temp file when done.
    """
    logger.info(f"Transcribing {audio_path}")
    try:
        model = _get_whisper_model()
        segments, info = model.transcribe(audio_path, beam_size=5)
        logger.info(f"Detected language '{info.language}' (p={info.language_probability:.2f})")
        transcript = ' '.join(seg.text for seg in segments).strip()
        logger.info(f"Transcription complete ({len(transcript.split())} words)")
        return transcript
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise
    finally:
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
