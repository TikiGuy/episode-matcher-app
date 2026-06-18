import re
import logging
import requests
import concurrent.futures
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
    Fetches all episodes for a given show name.
    Uses TMDB if a key is set, falling back to TVmaze on auth failure or if no key provided.
    """
    if settings.tmdb_api_key:
        logger.info(f"Using TMDB API for '{show_name}'")
        episodes = _fetch_from_tmdb(show_name)
        if episodes:
            return episodes
        logger.warning("TMDB returned no results; falling back to TVmaze.")

    logger.info(f"Using TVmaze API for '{show_name}'")
    return _fetch_from_tvmaze(show_name)

def _fetch_from_tvmaze(show_name: str) -> list[EpisodeInfo]:
    episodes_list = []

    try:
        search_resp = requests.get(
            "https://api.tvmaze.com/search/shows",
            params={"q": show_name},
        )
        search_resp.raise_for_status()
        search_results = search_resp.json()

        if not search_results:
            logger.error(f"TVmaze: Show '{show_name}' not found.")
            return []

        show_id = search_results[0]['show']['id']
        logger.info(f"TVmaze: Found show ID {show_id} for '{show_name}'")

        episodes_resp = requests.get(f"https://api.tvmaze.com/shows/{show_id}/episodes")
        episodes_resp.raise_for_status()

        for ep in episodes_resp.json():
            summary = re.sub(r'<[^>]+>', '', ep.get('summary') or '')
            episodes_list.append(EpisodeInfo(
                season=ep['season'],
                episode=ep['number'],
                title=ep['name'],
                summary=summary or "No summary available.",
            ))

        logger.info(f"TVmaze: Retrieved {len(episodes_list)} episodes.")
        return episodes_list

    except requests.exceptions.RequestException as e:
        logger.error(f"TVmaze API request failed: {e}")
        return []

def _fetch_from_tmdb(show_name: str) -> list[EpisodeInfo]:
    episodes_list = []
    api_key = settings.tmdb_api_key.strip()
    headers = {"accept": "application/json"}

    try:
        search_resp = requests.get(
            "https://api.themoviedb.org/3/search/tv",
            headers=headers,
            params={"query": show_name, "include_adult": "false", "language": "en-US", "page": 1, "api_key": api_key},
        )
        search_resp.raise_for_status()
        search_results = search_resp.json().get('results', [])

        if not search_results:
            logger.error(f"TMDB: Show '{show_name}' not found.")
            return []

        show_id = search_results[0]['id']

        details_resp = requests.get(
            f"https://api.themoviedb.org/3/tv/{show_id}",
            headers=headers,
            params={"language": "en-US", "api_key": api_key},
        )
        details_resp.raise_for_status()
        seasons_data = details_resp.json().get('seasons', [])

        def fetch_season(season_num):
            season_resp = requests.get(
                f"https://api.themoviedb.org/3/tv/{show_id}/season/{season_num}",
                headers=headers,
                params={"language": "en-US", "api_key": api_key},
            )
            season_resp.raise_for_status()

            season_episodes = []
            for ep in season_resp.json().get('episodes', []):
                season_episodes.append(EpisodeInfo(
                    season=ep['season_number'],
                    episode=ep['episode_number'],
                    title=ep['name'],
                    summary=ep.get('overview') or "No summary available.",
                ))
            return season_episodes

        valid_seasons = [s['season_number'] for s in seasons_data if s['season_number'] != 0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # Map returns results in the same order as valid_seasons
            results = executor.map(fetch_season, valid_seasons)

            for season_episodes in results:
                episodes_list.extend(season_episodes)

        logger.info(f"TMDB: Retrieved {len(episodes_list)} episodes.")
        return episodes_list

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            logger.error(
                "TMDB returned 401 Unauthorized. Your TMDB_API_KEY may be invalid. "
                "Make sure you are using the v3 API Key (not the Read Access Token) "
                "from https://www.themoviedb.org/settings/api"
            )
        else:
            logger.error(f"TMDB API request failed: {e}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDB API request failed: {e}")
        return []
