import logging
import requests
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    tmdb_api_key: str = ""

settings = Settings()

class EpisodeInfo:
    def __init__(self, season: int, episode: int, title: str, summary: str):
        self.season = season
        self.episode = episode
        self.title = title
        self.summary = summary

    def __repr__(self):
        return f"S{self.season:02d}E{self.episode:02d} - {self.title}"

def fetch_show_metadata(show_name: str) -> list[EpisodeInfo]:
    """
    Fetches all episodes with their summaries for a given show name.
    Prioritizes TMDB if an API key is present, otherwise falls back to TVmaze.
    """
    if settings.tmdb_api_key:
        logger.info(f"Using TMDB API for '{show_name}'")
        return _fetch_from_tmdb(show_name)
    else:
        logger.info(f"Using TVmaze API (Fallback) for '{show_name}'")
        return _fetch_from_tvmaze(show_name)

def _fetch_from_tvmaze(show_name: str) -> list[EpisodeInfo]:
    episodes_list = []

    # 1. Search for the show to get its TVmaze ID
    search_url = "https://api.tvmaze.com/search/shows"
    try:
        search_resp = requests.get(search_url, params={"q": show_name})
        search_resp.raise_for_status()
        search_results = search_resp.json()

        if not search_results:
            logger.error(f"TVmaze: Show '{show_name}' not found.")
            return []

        show_id = search_results[0]['show']['id']
        logger.info(f"TVmaze: Found show ID {show_id} for '{show_name}'")

        # 2. Fetch all episodes for that show ID
        episodes_url = f"https://api.tvmaze.com/shows/{show_id}/episodes"
        episodes_resp = requests.get(episodes_url)
        episodes_resp.raise_for_status()
        episodes_data = episodes_resp.json()

        for ep in episodes_data:
            summary = ep.get('summary', '')
            if summary:
                # Remove HTML tags returned by TVmaze
                import re
                summary = re.sub(r'<[^>]+>', '', summary)

            episodes_list.append(EpisodeInfo(
                season=ep['season'],
                episode=ep['number'],
                title=ep['name'],
                summary=summary or "No summary available."
            ))

        logger.info(f"TVmaze: Retrieved {len(episodes_list)} episodes.")
        return episodes_list

    except requests.exceptions.RequestException as e:
        logger.error(f"TVmaze API request failed: {e}")
        return []

def _fetch_from_tmdb(show_name: str) -> list[EpisodeInfo]:
    episodes_list = []
    headers = {
        "accept": "application/json"
    }

    # Ensure any trailing spaces/newlines are stripped from the key
    api_key = settings.tmdb_api_key.strip()

    try:
        # 1. Search for the show
        search_url = "https://api.themoviedb.org/3/search/tv"
        search_params = {
            "query": show_name,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
            "api_key": api_key
        }
        search_resp = requests.get(search_url, headers=headers, params=search_params)
        search_resp.raise_for_status()
        search_results = search_resp.json().get('results', [])

        if not search_results:
             logger.error(f"TMDB: Show '{show_name}' not found.")
             return []

        show_id = search_results[0]['id']

        # Fetch detailed show info to get exact number of seasons
        details_url = f"https://api.themoviedb.org/3/tv/{show_id}"
        details_params = {
            "language": "en-US",
            "api_key": api_key
        }
        details_resp = requests.get(details_url, headers=headers, params=details_params)
        details_resp.raise_for_status()
        seasons_data = details_resp.json().get('seasons', [])

        # 2. Fetch episodes for each season
        for season in seasons_data:
            season_num = season['season_number']
            if season_num == 0:
                continue # Skip specials (usually season 0)

            season_url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season_num}"
            season_params = {
                "language": "en-US",
                "api_key": api_key
            }
            season_resp = requests.get(season_url, headers=headers, params=season_params)
            season_resp.raise_for_status()
            episodes_data = season_resp.json().get('episodes', [])

            for ep in episodes_data:
                episodes_list.append(EpisodeInfo(
                    season=ep['season_number'],
                    episode=ep['episode_number'],
                    title=ep['name'],
                    summary=ep.get('overview', "No summary available.")
                ))

        logger.info(f"TMDB: Retrieved {len(episodes_list)} episodes.")
        return episodes_list

    except requests.exceptions.RequestException as e:
        logger.error(f"TMDB API request failed: {e}")
        return []