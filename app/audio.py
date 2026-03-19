import os
import subprocess
import tempfile
import logging
from faster_whisper import WhisperModel
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    whisper_model_size: str = "base"
    whisper_device: str = "cuda"

settings = Settings()

# Initialize the model outside the function so it stays loaded in memory
try:
    logger.info(f"Loading Whisper model '{settings.whisper_model_size}' on '{settings.whisper_device}'...")
    whisper_model = WhisperModel(settings.whisper_model_size, device=settings.whisper_device, compute_type="float16" if settings.whisper_device == "cuda" else "int8")
    logger.info("Whisper model loaded successfully.")
except Exception as e:
    logger.error(f"Failed to load Whisper model: {e}")
    # Fallback to CPU if CUDA fails (e.g. during local testing without GPU)
    logger.info("Falling back to CPU for Whisper model...")
    whisper_model = WhisperModel(settings.whisper_model_size, device="cpu", compute_type="int8")
    logger.info("Whisper model loaded on CPU.")

def extract_audio_chunk(video_path: str, start_time: str = "00:04:00", duration: str = "00:05:00") -> str:
    """
    Extracts a chunk of audio from a video file using FFmpeg.
    Returns the path to a temporary WAV file containing the extracted audio.
    """
    logger.info(f"Extracting audio from {video_path} (Start: {start_time}, Duration: {duration})")
    
    # Create a temporary file to store the extracted audio
    temp_fd, temp_path = tempfile.mkstemp(suffix=".wav")
    os.close(temp_fd) # Close the file descriptor, we just need the path
    
    try:
        # Construct the FFmpeg command
        cmd = [
            "ffmpeg",
            "-y",
            "-i", video_path,
            "-ss", start_time,
            "-t", duration,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            temp_path
        ]
        
        # Run the command and capture output for error reporting if it fails
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg failed with return code {result.returncode}")
            logger.error(f"FFmpeg stderr: {result.stderr}")
            os.remove(temp_path)
            raise RuntimeError(f"Failed to extract audio using FFmpeg. See logs.")
            
        logger.info(f"Audio extracted successfully to {temp_path}")
        return temp_path
        
    except Exception as e:
        logger.error(f"Error extracting audio: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

def transcribe_audio(audio_path: str) -> str:
    """
    Transcribes an audio file using faster-whisper.
    """
    logger.info(f"Transcribing audio file: {audio_path}")
    try:
        segments, info = whisper_model.transcribe(audio_path, beam_size=5)
        
        logger.info(f"Detected language '{info.language}' with probability {info.language_probability}")
        
        transcript = ""
        for segment in segments:
            transcript += segment.text + " "
            
        logger.info("Transcription complete.")
        return transcript.strip()
    except Exception as e:
        logger.error(f"Error during transcription: {e}")
        raise e
    finally:
        # Cleanup the temporary audio file
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
                logger.debug(f"Deleted temporary audio file: {audio_path}")
            except OSError as e:
                logger.warning(f"Failed to delete temporary audio file {audio_path}: {e}")