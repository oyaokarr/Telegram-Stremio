from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlparse


_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


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


def normalize_live_tv_channels(raw: Any) -> list[dict[str, str]]:
    """Validate and normalize admin-configured YouTube or direct-HLS channels.

    The helper never fetches or extracts media. For YouTube, it stores the public
    video ID for the client-side player resolver. For a direct stream, only HTTPS
    URLs ending in .m3u8 are accepted.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LiveTVConfigError("Live TV channels must be a list.")
    if len(raw) > 100:
        raise LiveTVConfigError("At most 100 live TV channels can be configured.")

    normalized: list[dict[str, str]] = []
    used_ids: set[str] = set()

    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise LiveTVConfigError(f"Live TV channel {index} must be an object.")

        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or item.get("url") or "").strip()
        logo = str(item.get("logo") or item.get("poster") or "").strip()
        description = str(item.get("description") or "").strip()

        if not name or len(name) > 100:
            raise LiveTVConfigError(f"Live TV channel {index} needs a name of 1–100 characters.")
        if not source or len(source) > 2048:
            raise LiveTVConfigError(f"Live TV channel '{name}' needs a source URL.")

        yt_id = youtube_video_id(source)
        if not yt_id:
            if not _valid_https_url(source):
                raise LiveTVConfigError(
                    f"Source for '{name}' must be an HTTPS YouTube link or a direct .m3u8 URL."
                )
            if not urlparse(source).path.lower().endswith(".m3u8"):
                raise LiveTVConfigError(
                    f"Source for '{name}' is not a direct .m3u8 stream. YouTube watch/live pages are supported directly."
                )

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

        channel = {
            "id": channel_id,
            "name": name,
            "source": source,
            "description": description[:500],
        }
        if yt_id:
            channel["yt_id"] = yt_id
        if logo:
            channel["logo"] = logo
        normalized.append(channel)

    return normalized


def live_channel_meta(channel: dict[str, str]) -> dict[str, Any]:
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


def live_channel_stream(channel: dict[str, str]) -> dict[str, Any]:
    stream: dict[str, Any] = {
        "name": channel["name"],
        "description": "Live channel",
    }
    if channel.get("yt_id"):
        stream["ytId"] = channel["yt_id"]
    else:
        stream["url"] = channel["source"]
    return stream
