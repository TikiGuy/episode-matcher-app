import logging
from typing import Optional

from app.metadata import EpisodeInfo
from app.opensubtitles import compute_hash, os_client

logger = logging.getLogger(__name__)

# Minimum fraction of transcript trigrams that must appear in an episode's
# subtitle text to count as a confident match. Tune upward if you get false
# positives; lower it if correct episodes are being skipped.
TRIGRAM_THRESHOLD = 0.08


def _trigrams(text: str) -> set[str]:
    words = text.lower().split()
    return {' '.join(words[i:i + 3]) for i in range(len(words) - 2)}


def match_by_hash(filepath: str) -> Optional[dict]:
    """
    Primary path: compute the OpenSubtitles file hash and look up the episode.
    Returns {season, episode, title} on success, None otherwise.
    No audio extraction or transcription needed.
    """
    try:
        file_hash, filesize = compute_hash(filepath)
        logger.info(f"File hash: {file_hash}  size: {filesize}")
        return os_client.hash_lookup(file_hash, filesize)
    except Exception as e:
        logger.error(f"Hash computation/lookup failed: {e}")
        return None


def match_by_transcript(
    transcript: str,
    episodes: list[EpisodeInfo],
    show_name: str,
) -> Optional[tuple[EpisodeInfo, float]]:
    """
    Fallback path: compare a Whisper transcript against cached episode subtitle
    text using trigram overlap. Returns (EpisodeInfo, score_0_to_100) or None.

    Why trigrams: 3-word sequences from real dialogue are distinctive enough
    to discriminate between episodes while tolerating ~1 word of ASR error per
    trigram. Common stop words appear in every episode and naturally wash out.
    """
    if not transcript or len(transcript.split()) < 20:
        return None

    transcript_trigrams = _trigrams(transcript)
    if not transcript_trigrams:
        return None

    scores: list[tuple[float, EpisodeInfo]] = []

    for ep in episodes:
        subtitle_text = os_client.get_episode_subtitle_text(show_name, ep.season, ep.episode)
        if not subtitle_text:
            continue

        ep_trigrams = _trigrams(subtitle_text)
        if not ep_trigrams:
            continue

        overlap = len(transcript_trigrams & ep_trigrams) / len(transcript_trigrams)
        scores.append((overlap, ep))

    if not scores:
        logger.warning("No subtitle text available for any episode; transcript matching skipped.")
        return None

    scores.sort(key=lambda x: x[0], reverse=True)
    best_score, best_ep = scores[0]

    if best_score < TRIGRAM_THRESHOLD:
        logger.info(f"Best transcript score {best_score:.3f} is below threshold {TRIGRAM_THRESHOLD}; no match.")
        return None

    # Require the winner to be clearly ahead of the runner-up to avoid guessing
    if len(scores) >= 2 and scores[1][0] > 0:
        runner_up = scores[1][0]
        if best_score / runner_up < 1.4:
            logger.warning(
                f"Ambiguous match: best={best_score:.3f} ({best_ep}), "
                f"runner-up={runner_up:.3f} ({scores[1][1]}). Skipping."
            )
            return None

    logger.info(f"Transcript match: {best_ep} (score={best_score:.3f})")
    return best_ep, round(best_score * 100, 1)


def match_episode(
    filepath: str,
    episodes: list[EpisodeInfo],
    show_name: str,
    transcript: Optional[str] = None,
) -> Optional[tuple[EpisodeInfo, str]]:
    """
    Full matching pipeline. Returns (EpisodeInfo, method_description) or None.

    Stage 1 — Hash lookup: instant, no audio needed, deterministic.
    Stage 2 — Transcript matching: requires Whisper transcript, uses subtitle cache.
    """
    # Stage 1: hash lookup
    hash_result = match_by_hash(filepath)
    if hash_result:
        season, episode = hash_result["season"], hash_result["episode"]
        for ep in episodes:
            if ep.season == season and ep.episode == episode:
                return ep, "hash lookup"
        logger.warning(f"Hash returned S{season:02d}E{episode:02d} but that episode isn't in the metadata.")

    # Stage 2: transcript-based subtitle matching
    if transcript:
        result = match_by_transcript(transcript, episodes, show_name)
        if result:
            ep, score = result
            return ep, f"transcript match ({score}% confidence)"

    return None
