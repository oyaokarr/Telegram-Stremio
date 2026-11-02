from __future__ import annotations

import ipaddress
import json
import re
import socket
import unicodedata
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse


_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_MAX_CHANNELS = 100
_MAX_SOURCES_PER_CHANNEL = 10
_MAX_PROBE_BYTES = 64 * 1024


def _quality_rank(label: str) -> int | None:
    value = str(label or "").strip().lower()
    if value == "4k":
        return 2160
    match = re.fullmatch(r"([0-9]{3,4})p", value)
    return int(match.group(1)) if match else None


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


def _assert_public_https_url(value: str) -> None:
    """Reject non-HTTPS, nonstandard ports, and non-public IP destinations."""
    if not _valid_https_url(value):
        raise LiveTVConfigError("Only public HTTPS source URLs can be tested.")
    parsed = urlparse(value)
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise LiveTVConfigError("The source URL has an invalid port.") from exc
    if port != 443:
        raise LiveTVConfigError("Only the standard HTTPS port can be tested.")

    host = parsed.hostname or ""
    try:
        addresses = {
            ipaddress.ip_address(result[4][0].split("%", 1)[0])
            for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise LiveTVConfigError("The source host could not be resolved.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise LiveTVConfigError("The source must resolve only to public internet addresses.")


class _PublicHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 3

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        destination = urljoin(request.full_url, new_url)
        _assert_public_https_url(destination)
        return super().redirect_request(request, file_pointer, code, message, headers, destination)


def _fetch_small_public_response(url: str) -> tuple[int, str, str, bytes]:
    """Fetch a small public HTTPS response with bounded body and checked redirects."""
    _assert_public_https_url(url)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PublicHttpsRedirectHandler(),
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; LiveTVSourceCheck/1.0)",
            "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, application/json, */*",
            "Accept-Encoding": "identity",
        },
    )
    with opener.open(request, timeout=8) as response:
        body = response.read(_MAX_PROBE_BYTES + 1)
        if len(body) > _MAX_PROBE_BYTES:
            raise LiveTVConfigError("The response is too large to safely inspect as a playlist.")
        return response.status, response.geturl(), response.headers.get("Content-Type", ""), body


def _parse_hls_master_variants(text: str, master_url: str) -> list[dict[str, Any]]:
    """Extract selectable variant playlist URLs from an HLS master playlist."""
    lines = text.splitlines()
    variants: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, line in enumerate(lines):
        line = line.strip()
        if not line.upper().startswith("#EXT-X-STREAM-INF:"):
            continue
        attributes = line.split(":", 1)[1]
        resolution_match = re.search(r"(?:^|,)RESOLUTION=([0-9]{2,5})x([0-9]{2,5})(?:,|$)", attributes, re.I)
        bandwidth_match = re.search(r"(?:^|,)(?:AVERAGE-BANDWIDTH|BANDWIDTH)=([0-9]+)(?:,|$)", attributes, re.I)
        uri = next((candidate.strip() for candidate in lines[index + 1:] if candidate.strip() and not candidate.lstrip().startswith("#")), "")
        if not uri:
            continue
        variant_url = urljoin(master_url, uri)
        if not _valid_https_url(variant_url) or not urlparse(variant_url).path.lower().endswith(".m3u8"):
            continue
        if variant_url in seen_urls:
            continue
        seen_urls.add(variant_url)

        resolution = ""
        label = "HLS kalite"
        if resolution_match:
            width, height = map(int, resolution_match.groups())
            resolution = f"{width}x{height}"
            label = "4K" if height >= 2160 else f"{height}p"
        bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
        variants.append({
            "name": label,
            "source": variant_url,
            "resolution": resolution,
            "bandwidth": bandwidth,
        })
    variants.sort(
        key=lambda variant: (
            _quality_rank(variant["name"]) or 0,
            int(variant.get("bandwidth") or 0),
        ),
        reverse=True,
    )
    return variants[:8]


def _hls_quality_label(source_url: str, variants: list[dict[str, Any]]) -> str | None:
    """Return declared HLS variant qualities, or an explicit quality token in its URL."""
    labels = {
        str(variant.get("name") or "").strip()
        for variant in variants
        if str(variant.get("name") or "").strip()
    }
    labels = {
        "4K" if label.lower() == "4k" else label.lower()
        for label in labels
        if label.lower() == "4k" or re.fullmatch(r"[0-9]{3,4}p", label, re.I)
    }
    if labels:
        return " · ".join(
            sorted(labels, key=lambda label: _quality_rank(label) or 0, reverse=True)
        )

    parsed = urlparse(source_url)
    url_text = f"{unquote(parsed.path)} {unquote(parsed.query)}"
    match = re.search(
        r"(?<![a-z0-9])(?:4k|2160p|1440p|1080p|720p|576p|540p|480p|360p|240p)(?![a-z0-9])",
        url_text,
        re.I,
    )
    if not match:
        return None
    label = match.group(0)
    return "4K" if label.lower() == "4k" else label.lower()


def probe_live_tv_source(source: str) -> dict[str, Any]:
    """Check a public YouTube/HLS URL without downloading media segments."""
    try:
        channel = normalize_live_tv_channels([{"name": "Source check", "source": source}])[0]
        normalized_source = channel["sources"][0]
        yt_id = normalized_source.get("yt_id")
        if yt_id:
            canonical_url = f"https://www.youtube.com/watch?v={yt_id}"
            oembed_url = "https://www.youtube.com/oembed?" + urlencode(
                {"url": canonical_url, "format": "json"}
            )
            try:
                status, _, _, body = _fetch_small_public_response(oembed_url)
                if status < 200 or status >= 300:
                    raise LiveTVConfigError(f"YouTube metadata endpoint returned HTTP {status}.")
                metadata = json.loads(body.decode("utf-8", "replace"))
                return {
                    "ok": True,
                    "kind": "youtube",
                    "video_id": yt_id,
                    "title": str(metadata.get("title") or "")[:300],
                    "author_name": str(metadata.get("author_name") or "")[:200],
                    "thumbnail_url": str(metadata.get("thumbnail_url") or "")[:2048],
                    "message": "YouTube link recognized; public title and thumbnail metadata were retrieved. Playback still depends on Nuvio and the broadcaster's availability.",
                }
            except Exception:
                return {
                    "ok": True,
                    "kind": "youtube",
                    "video_id": yt_id,
                    "message": "YouTube link format is valid, but public metadata could not be retrieved. This does not confirm playback availability.",
                }

        url = normalized_source["source"]
        status, final_url, content_type, body = _fetch_small_public_response(url)
        if status < 200 or status >= 300:
            return {"ok": False, "kind": "hls", "message": f"The source returned HTTP {status}."}
        text = body.decode("utf-8", "replace").lstrip("\ufeff \r\n\t")
        if not text.startswith("#EXTM3U"):
            return {"ok": False, "kind": "hls", "message": "The URL responded, but the response is not an HLS #EXTM3U playlist."}
        variant_sources = _parse_hls_master_variants(text, final_url)
        resolutions = [variant["resolution"] for variant in variant_sources if variant["resolution"]]
        if variant_sources:
            labels = ", ".join(variant["name"] for variant in variant_sources)
            message = f"HLS ana listesi erişilebilir; {len(variant_sources)} kalite seçeneği bulundu: {labels}. İstersen aşağıdan Nuvio kaynaklarına ekleyebilirsin."
        else:
            message = "Valid HLS media playlist is reachable; media segments were not downloaded."
        return {
            "ok": True,
            "kind": "hls",
            "final_host": urlparse(final_url).hostname or "",
            "content_type": content_type[:160],
            "variants": resolutions[:8],
            "variant_sources": variant_sources,
            "quality_label": _hls_quality_label(final_url, variant_sources),
            "message": message,
        }
    except LiveTVConfigError as exc:
        return {"ok": False, "message": str(exc)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "message": f"The source returned HTTP {exc.code}."}
    except (TimeoutError, socket.timeout):
        return {"ok": False, "message": "The source check timed out."}
    except Exception:
        return {"ok": False, "message": "The source could not be reached or safely inspected."}


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
    logo = channel.get("logo")
    if not logo:
        yt_id = channel.get("yt_id") or next(
            (source.get("yt_id") for source in channel.get("sources", []) if source.get("yt_id")),
            None,
        )
        if yt_id:
            logo = f"https://i.ytimg.com/vi/{yt_id}/hqdefault.jpg"
    if logo:
        meta["poster"] = logo
        meta["logo"] = logo
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
    source_labels = [str(source.get("name") or "").strip() for source in raw_sources]
    if multiple and all(_quality_rank(label) is not None for label in source_labels):
        raw_sources = sorted(raw_sources, key=lambda source: _quality_rank(source.get("name")) or 0, reverse=True)
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


def live_tv_logo_extension(data: bytes) -> str | None:
    """Return a safe raster-image extension based on file signatures, not its filename."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None
