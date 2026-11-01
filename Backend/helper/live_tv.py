from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlparse


_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_MAX_CHANNELS = 100
_MAX_SOURCES_PER_CHANNEL = 10


class LiveTVConfigError(ValueError):
    """Raised when an admin-provided live channel entry is invalid."""


def _slugify(value: str) -> str:
    turkish = str.maketrans({"ı": "i", "İ": "I", "ğ": "g", "Ğ": "G", "ü": "u", "Ü": "U", "ş": "s", "Ş": "S", "ö": "o", "Ö": "O", "ç": "c", "Ç": "C"})
    normalized = unicodedata.normalize("NFKD", value.translate(turkish))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", ascii_value).strip("-_")
    return slug[:64]


def youtube_video_id(source: str) -> str | None:
    """Extract a YouTube video ID from a normal watch/live/embed URL or bare ID."""
    value = str(source or "").strip()
    if _YOUTUBE_ID_RE.fullmatch(value):
        return value

    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in _YOUTUBE_HOSTS:
        return None

    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif parsed.path.rstrip("/") == "/watch":
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"live", "embed", "shorts"}:
            candidate = parts[1]

    return candidate if _YOUTUBE_ID_RE.fullmatch(candidate) else None


def _valid_https_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _normalize_source(raw_source: Any, channel_name: str, source_index: int) -> dict[str, str]:
    if isinstance(raw_source, str):
        source_value = raw_source
        source_name = ""
    elif isinstance(raw_source, dict):
        source_value = raw_source.get("source") or raw_source.get("url") or ""
        source_name = str(raw_source.get("name") or raw_source.get("label") or "").strip()
    else:
        raise LiveTVConfigError(
            f"Source {source_index} for '{channel_name}' must be a URL or an object."
        )

    source = str(source_value or "").strip()
    if not source or len(source) > 2048:
        raise LiveTVConfigError(
            f"Source {source_index} for '{channel_name}' needs a valid URL."
        )
    if len(source_name) > 100:
        raise LiveTVConfigError(
            f"Source label {source_index} for '{channel_name}' must be 1–100 characters."
        )

    yt_id = youtube_video_id(source)
    if not yt_id:
        if not _valid_https_url(source):
            raise LiveTVConfigError(
                f"Source {source_index} for '{channel_name}' must be an HTTPS YouTube link or a direct HTTPS .m3u8 URL."
            )
        if not urlparse(source).path.lower().endswith(".m3u8"):
            raise LiveTVConfigError(
                f"Source {source_index} for '{channel_name}' is not a direct .m3u8 stream. YouTube watch/live pages are supported directly."
            )

    normalized = {"source": source}
    if source_name:
        normalized["name"] = source_name
    if yt_id:
        normalized["yt_id"] = yt_id
    return normalized


def normalize_live_tv_channels(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalize admin-configured channels and their source URLs.

    YouTube links are stored as video IDs for client-side playback resolution.
    Direct HLS links are passed through without fetching or extracting media.
    Legacy single-source entries remain supported.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LiveTVConfigError("Live TV channels must be a list.")
    if len(raw) > _MAX_CHANNELS:
        raise LiveTVConfigError(f"At most {_MAX_CHANNELS} live TV channels can be configured.")

    normalized: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise LiveTVConfigError(f"Live TV channel {index} must be an object.")

        name = str(item.get("name") or "").strip()
        if not name or len(name) > 100:
            raise LiveTVConfigError(f"Live TV channel {index} needs a name of 1–100 characters.")

        raw_sources = item.get("sources")
        if raw_sources is None:
            source = item.get("source") or item.get("url") or ""
            if not source:
                raise LiveTVConfigError(f"Live TV channel '{name}' needs at least one source URL.")
            raw_sources = [{
                "source": source,
                "name": item.get("source_name") or item.get("stream_name") or "",
            }]
        if not isinstance(raw_sources, list) or not raw_sources:
            raise LiveTVConfigError(f"Live TV channel '{name}' needs at least one source URL.")
        if len(raw_sources) > _MAX_SOURCES_PER_CHANNEL:
            raise LiveTVConfigError(
                f"Live TV channel '{name}' can have at most {_MAX_SOURCES_PER_CHANNEL} sources."
            )

        sources = [
            _normalize_source(source_item, name, source_index)
            for source_index, source_item in enumerate(raw_sources, start=1)
        ]

        logo = str(item.get("logo") or item.get("poster") or "").strip()
        description = str(item.get("description") or "").strip()
        if len(description) > 500:
            raise LiveTVConfigError(f"Description for '{name}' must be at most 500 characters.")
        if logo and (len(logo) > 2048 or not _valid_https_url(logo)):
            raise LiveTVConfigError(f"Logo for '{name}' must be a valid HTTPS URL.")

        raw_id = str(item.get("id") or _slugify(name)).strip().lower()
        raw_id = raw_id.removeprefix("live:")
        if not _CHANNEL_SLUG_RE.fullmatch(raw_id):
            raise LiveTVConfigError(f"Channel ID for '{name}' contains unsupported characters.")
        channel_id = f"live:{raw_id}"
        if channel_id in used_ids:
            raise LiveTVConfigError(f"Duplicate live TV channel ID: {channel_id}.")
        used_ids.add(channel_id)

        channel: dict[str, Any] = {
            "id": channel_id,
            "name": name,
            "source": sources[0]["source"],
            "description": description[:500],
            "sources": sources,
        }
        if sources[0].get("yt_id"):
            channel["yt_id"] = sources[0]["yt_id"]
        if logo:
            channel["logo"] = logo
        normalized.append(channel)

    return normalized


def live_channel_meta(channel: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "id": channel["id"],
        "type": "channel",
        "name": channel["name"],
        "posterShape": "square",
        "behaviorHints": {"isLive": True},
    }
    if channel.get("description"):
        meta["description"] = channel["description"]
    if channel.get("logo"):
        meta["poster"] = channel["logo"]
        meta["logo"] = channel["logo"]
    return meta


def live_channel_streams(channel: dict[str, Any]) -> list[dict[str, Any]]:
    """Build one named Stremio stream entry for each configured channel source."""
    raw_sources = channel.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        legacy_source = channel.get("source")
        if not legacy_source:
            return []
        raw_sources = [{
            "source": legacy_source,
            "name": channel.get("source_name") or "",
            "yt_id": channel.get("yt_id"),
        }]

    multiple = len(raw_sources) > 1
    streams: list[dict[str, Any]] = []
    for index, source in enumerate(raw_sources, start=1):
        label = str(source.get("name") or "").strip()
        if not label:
            label = f"Kaynak {index}" if multiple else channel["name"]
        stream: dict[str, Any] = {"name": label}
        if multiple:
            stream["description"] = channel["name"]
        if source.get("yt_id"):
            stream["ytId"] = source["yt_id"]
        elif source.get("source"):
            stream["url"] = source["source"]
        else:
            continue
        streams.append(stream)
    return streams


def live_channel_stream(channel: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible first stream helper for callers expecting one source."""
    streams = live_channel_streams(channel)
    return streams[0] if streams else {}
