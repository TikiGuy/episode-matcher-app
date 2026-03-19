import os
import glob
import logging
from typing import Callable, Any
from app.audio import extract_audio_chunk, transcribe_audio
from app.metadata import fetch_show_metadata
from app.matcher import match_episode

logger = logging.getLogger(__name__)

def sanitize_filename(name: str) -> str:
    """Removes invalid characters from a string so it can be used as a filename."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')
    return name.strip()

def process_directory(directory_path: str, show_name: str, progress_callback: Callable[[str, str], None] = None):
    """
    Main orchestration function.
    Iterates through all .mkv files in a directory, extracts audio, transcribes, matches, and renames.
    """
    if not os.path.isdir(directory_path):
        msg = f"Error: Directory '{directory_path}' does not exist or is not a directory."
        logger.error(msg)
        if progress_callback: progress_callback(directory_path, msg)
        return

    logger.info(f"Starting batch processing for directory: {directory_path}, Show: {show_name}")
    if progress_callback: progress_callback(directory_path, f"Fetching metadata for '{show_name}'...")

    # 1. Fetch metadata once for the entire batch
    episodes_metadata = fetch_show_metadata(show_name)
    if not episodes_metadata:
        msg = f"Failed to retrieve any episode metadata for '{show_name}'. Aborting batch."
        logger.error(msg)
        if progress_callback: progress_callback(directory_path, msg)
        return

    # 2. Find all MKV files
    mkv_files = glob.glob(os.path.join(directory_path, "*.mkv"))
    if not mkv_files:
        msg = f"No .mkv files found in '{directory_path}'."
        logger.warning(msg)
        if progress_callback: progress_callback(directory_path, msg)
        return

    logger.info(f"Found {len(mkv_files)} .mkv files to process.")

    # 3. Process each file
    for index, file_path in enumerate(mkv_files):
        filename = os.path.basename(file_path)
        
        # Check if it already looks like it's renamed (e.g., "The Office - S01E01.mkv")
        # A simple check: if the show name and "S" and "E" are in it, skip it.
        if show_name.lower() in filename.lower() and "s0" in filename.lower() and "e0" in filename.lower():
             logger.info(f"Skipping '{filename}' - appears to already be renamed.")
             continue
             
        msg = f"[{index+1}/{len(mkv_files)}] Processing '{filename}'..."
        logger.info(msg)
        if progress_callback: progress_callback(filename, msg)

        try:
            # Step A: Extract Audio
            if progress_callback: progress_callback(filename, "Extracting 5-minute audio sample...")
            # We extract from minute 4 to minute 9 to skip most intros/theme songs
            audio_path = extract_audio_chunk(file_path, start_time="00:04:00", duration="00:05:00")
            
            # Step B: Transcribe
            if progress_callback: progress_callback(filename, "Transcribing audio using GPU...")
            transcript = transcribe_audio(audio_path)
            
            if not transcript or len(transcript.split()) < 20:
                logger.warning(f"Transcript too short or empty for '{filename}'. Skipping.")
                if progress_callback: progress_callback(filename, "Error: Transcript too short or empty. Skipping.")
                continue

            # Step C: Match via LLM
            if progress_callback: progress_callback(filename, "Asking Ollama to match transcript to episode...")
            matched_episode = match_episode(transcript, episodes_metadata)
            
            # Step D: Rename File
            if matched_episode:
                # Plex standard: Show Name - SxxExx - Optional Title.ext
                # We will stick to Show Name - SxxExx.ext for simplicity and robust matching
                new_filename = f"{show_name} - S{matched_episode.season:02d}E{matched_episode.episode:02d}.mkv"
                # Remove any invalid characters just in case the show name contains a colon or something
                new_filename = sanitize_filename(new_filename)
                
                new_filepath = os.path.join(directory_path, new_filename)
                
                if os.path.exists(new_filepath):
                    msg = f"Cannot rename '{filename}' to '{new_filename}' because file already exists!"
                    logger.error(msg)
                    if progress_callback: progress_callback(filename, msg)
                else:
                    os.rename(file_path, new_filepath)
                    msg = f"Successfully renamed '{filename}' to '{new_filename}'!"
                    logger.info(msg)
                    if progress_callback: progress_callback(new_filename, msg) # Send back the new name
            else:
                 msg = f"Failed to find a confident match for '{filename}'. File left unchanged."
                 logger.warning(msg)
                 if progress_callback: progress_callback(filename, msg)
                 
        except Exception as e:
             msg = f"An error occurred while processing '{filename}': {str(e)}"
             logger.error(msg)
             if progress_callback: progress_callback(filename, msg)

    msg = "Batch processing complete."
    logger.info(msg)
    if progress_callback: progress_callback("System", msg)