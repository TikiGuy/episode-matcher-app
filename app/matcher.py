import json
import logging
from typing import Optional, Tuple
from ollama import Client
from pydantic_settings import BaseSettings
from app.metadata import EpisodeInfo

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "mistral"

settings = Settings()

# Ensure URL does not have a trailing slash for the python client
if settings.ollama_url.endswith('/'):
    settings.ollama_url = settings.ollama_url[:-1]

ollama_client = Client(host=settings.ollama_url)

def match_episode(transcript: str, episodes: list[EpisodeInfo]) -> Optional[EpisodeInfo]:
    """
    Uses an LLM via Ollama to match a transcript against a list of episode summaries.
    Returns the matching EpisodeInfo object, or None if no match is found.
    """
    logger.info(f"Attempting to match transcript against {len(episodes)} episodes using model '{settings.ollama_model}' at '{settings.ollama_url}'")
    
    if not episodes:
        logger.error("No episodes provided to match against.")
        return None

    # Construct the context mapping Season/Episode to the Summary
    episodes_context = ""
    for ep in episodes:
        # Limit summary length to save token space if needed, though usually fine
        summary = ep.summary[:500] + "..." if len(ep.summary) > 500 else ep.summary
        episodes_context += f"Season {ep.season}, Episode {ep.episode}:\n{summary}\n\n"

    prompt = f"""You are an expert TV show metadata matcher. I will provide you with a raw transcript extracted from a 5-minute audio chunk of a TV show episode. 
I will also provide you with a list of episode summaries for the entire series.

Your task is to analyze the transcript, identify key character names, plot points, or unique dialogue, and match it to EXACTLY ONE episode from the provided summaries.

Here is the transcript:
\"\"\"{transcript}\"\"\"

Here are the episode summaries:
\"\"\"{episodes_context}\"\"\"

You MUST output your response in valid JSON format. Do not include any other text, markdown formatting, or explanation. Just the JSON object.
If you are confident in a match, return the exact Season number and Episode number as integers.
If you cannot find a match, or the transcript is too vague, return null for both.

Example output format for a match:
{{
  "season": 1,
  "episode": 4
}}

Example output format for no match:
{{
  "season": null,
  "episode": null
}}
"""

    try:
        response = ollama_client.chat(model=settings.ollama_model, messages=[
            {
                'role': 'user',
                'content': prompt
            }
        ], format='json')
        
        response_text = response['message']['content'].strip()
        logger.debug(f"LLM Response: {response_text}")
        
        # Parse the JSON response
        result = json.loads(response_text)
        
        season = result.get('season')
        episode = result.get('episode')
        
        if season is not None and episode is not None:
            # Find the matching EpisodeInfo object
            for ep in episodes:
                if ep.season == int(season) and ep.episode == int(episode):
                    logger.info(f"Match found! Season {season}, Episode {episode}")
                    return ep
            
            logger.warning(f"LLM returned S{season}E{episode}, but this episode does not exist in the metadata.")
            return None
        else:
            logger.info("LLM could not determine a match.")
            return None
            
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        logger.error(f"Raw response: {response_text}")
        return None
    except Exception as e:
        logger.error(f"Error communicating with Ollama: {e}")
        return None