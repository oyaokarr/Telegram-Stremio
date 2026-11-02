import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


_MODULE_PATH = Path(__file__).resolve().parents[1] / "Backend" / "helper" / "live_tv.py"
_SPEC = importlib.util.spec_from_file_location("live_tv_test_module", _MODULE_PATH)
_LIVE_TV = importlib.util.module_from_spec(_SPEC)
assert _SPEC is not None and _SPEC.loader is not None
_SPEC.loader.exec_module(_LIVE_TV)

LiveTVConfigError = _LIVE_TV.LiveTVConfigError
live_channel_meta = _LIVE_TV.live_channel_meta
live_channel_stream = _LIVE_TV.live_channel_stream
live_channel_streams = _LIVE_TV.live_channel_streams
normalize_live_tv_channels = _LIVE_TV.normalize_live_tv_channels
probe_live_tv_source = _LIVE_TV.probe_live_tv_source
youtube_video_id = _LIVE_TV.youtube_video_id
live_tv_logo_extension = _LIVE_TV.live_tv_logo_extension


class LiveTVTests(unittest.TestCase):
    def test_youtube_live_url_is_normalized_to_yt_id(self):
        channels = normalize_live_tv_channels([
            {
                "name": "Behzat Ç. Canlı Yayın",
                "source": "https://www.youtube.com/live/XPy9T3ePqoQ",
            }
        ])
        self.assertEqual(channels[0]["id"], "live:behzat-c-canli-yayin")
        self.assertEqual(channels[0]["yt_id"], "XPy9T3ePqoQ")
        self.assertEqual(live_channel_meta(channels[0])["type"], "channel")
        self.assertEqual(live_channel_stream(channels[0])["ytId"], "XPy9T3ePqoQ")
        self.assertEqual(len(live_channel_streams(channels[0])), 1)

    def test_youtube_thumbnail_is_used_when_channel_has_no_custom_logo(self):
        channel = normalize_live_tv_channels([
            {"name": "Behzat Ç.", "source": "https://www.youtube.com/live/XPy9T3ePqoQ"}
        ])[0]
        meta = live_channel_meta(channel)
        thumbnail = "https://i.ytimg.com/vi/XPy9T3ePqoQ/hqdefault.jpg"

        self.assertEqual(meta["poster"], thumbnail)
        self.assertEqual(meta["logo"], thumbnail)

    def test_watch_and_bare_youtube_ids_are_supported(self):
        self.assertEqual(youtube_video_id("https://www.youtube.com/watch?v=XPy9T3ePqoQ"), "XPy9T3ePqoQ")
        self.assertEqual(youtube_video_id("XPy9T3ePqoQ"), "XPy9T3ePqoQ")

    def test_direct_https_m3u8_is_supported_for_legacy_single_source(self):
        channels = normalize_live_tv_channels([
            {"name": "Official Stream", "source": "https://media.example.org/live/channel.m3u8?token=abc"}
        ])
        self.assertEqual(
            live_channel_stream(channels[0])["url"],
            "https://media.example.org/live/channel.m3u8?token=abc",
        )
        self.assertEqual(len(live_channel_streams(channels[0])), 1)

    def test_multiple_sources_and_channel_metadata_are_preserved(self):
        logo = "https://media.example.org/logo.png"
        description = "Türkiye ve dünyadan canlı haber yayını."
        channels = normalize_live_tv_channels([
            {
                "name": "TRT Haber",
                "id": "live:trt-haber",
                "logo": logo,
                "description": description,
                "sources": [
                    {"name": "Uyarlanabilir ana yayın", "source": "https://media.example.org/master.m3u8"},
                    {"name": "360p alternatif", "source": "https://media.example.org/master_360.m3u8"},
                ],
            }
        ])
        channel = channels[0]
        meta = live_channel_meta(channel)
        streams = live_channel_streams(channel)

        self.assertEqual(channel["id"], "live:trt-haber")
        self.assertEqual(meta["logo"], logo)
        self.assertEqual(meta["poster"], logo)
        self.assertEqual(meta["description"], description)
        self.assertEqual(len(streams), 2)
        self.assertEqual(streams[0]["name"], "Uyarlanabilir ana yayın")
        self.assertEqual(streams[0]["description"], "TRT Haber")
        self.assertEqual(streams[0]["url"], "https://media.example.org/master.m3u8")
        self.assertEqual(streams[1]["name"], "360p alternatif")
        self.assertEqual(streams[1]["url"], "https://media.example.org/master_360.m3u8")

    def test_non_https_or_non_direct_source_is_rejected(self):
        for source in (
            "http://media.example.org/live/channel.m3u8",
            "https://media.example.org/watch/live",
            "https://notyoutube.com/live/XPy9T3ePqoQ",
        ):
            with self.subTest(source=source):
                with self.assertRaises(LiveTVConfigError):
                    normalize_live_tv_channels([{"name": "Test", "source": source}])

    def test_invalid_url_in_any_source_is_rejected(self):
        with self.assertRaises(LiveTVConfigError):
            normalize_live_tv_channels([
                {
                    "name": "News",
                    "sources": [
                        {"name": "Primary", "source": "https://media.example.org/live.m3u8"},
                        {"name": "Invalid", "source": "http://media.example.org/live.m3u8"},
                    ],
                }
            ])

    def test_too_many_sources_are_rejected(self):
        with self.assertRaises(LiveTVConfigError):
            normalize_live_tv_channels([
                {
                    "name": "News",
                    "sources": [
                        {"name": f"Source {i}", "source": f"https://media.example.org/{i}.m3u8"}
                        for i in range(11)
                    ],
                }
            ])

    def test_hls_source_probe_reports_variants_without_downloading_segments(self):
        url = "https://media.example.org/master.m3u8"
        playlist = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\nhigh.m3u8\n"
        with patch.object(_LIVE_TV, "_fetch_small_public_response", return_value=(200, url, "application/x-mpegURL", playlist)):
            result = probe_live_tv_source(url)
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "hls")
        self.assertEqual(result["variants"], ["1280x720", "640x360"])
        self.assertEqual(
            [variant["source"] for variant in result["variant_sources"]],
            ["https://media.example.org/high.m3u8", "https://media.example.org/low.m3u8"],
        )
        self.assertEqual([variant["name"] for variant in result["variant_sources"]], ["720p", "360p"])
        self.assertEqual([variant["bandwidth"] for variant in result["variant_sources"]], [2500000, 800000])
        self.assertEqual(result["quality_label"], "720p · 360p")

        channel = normalize_live_tv_channels([{
            "name": "News",
            "sources": [
                {"name": variant["name"], "source": variant["source"]}
                for variant in result["variant_sources"]
            ],
        }])[0]
        streams = live_channel_streams(channel)
        self.assertEqual([stream["name"] for stream in streams], ["720p", "360p"])
        self.assertEqual([stream["url"] for stream in streams], [
            "https://media.example.org/high.m3u8",
            "https://media.example.org/low.m3u8",
        ])

    def test_existing_quality_sources_are_returned_highest_to_lowest(self):
        channel = normalize_live_tv_channels([{
            "name": "TRT1",
            "sources": [
                {"name": "720p", "source": "https://media.example.org/720.m3u8"},
                {"name": "480p", "source": "https://media.example.org/480.m3u8"},
                {"name": "360p", "source": "https://media.example.org/360.m3u8"},
                {"name": "1080p", "source": "https://media.example.org/1080.m3u8"},
                {"name": "1440p", "source": "https://media.example.org/1440.m3u8"},
            ],
        }])[0]

        streams = live_channel_streams(channel)
        self.assertEqual([stream["name"] for stream in streams], [
            "1440p", "1080p", "720p", "480p", "360p",
        ])
        self.assertEqual([stream["url"] for stream in streams], [
            "https://media.example.org/1440.m3u8",
            "https://media.example.org/1080.m3u8",
            "https://media.example.org/720.m3u8",
            "https://media.example.org/480.m3u8",
            "https://media.example.org/360.m3u8",
        ])

    def test_hls_quality_is_inferred_from_single_quality_url(self):
        url = "https://media.example.org/live/master_1440p.m3u8"
        playlist = b"#EXTM3U\n#EXTINF:6,\nsegment.ts\n"
        with patch.object(_LIVE_TV, "_fetch_small_public_response", return_value=(200, url, "application/x-mpegURL", playlist)):
            result = probe_live_tv_source(url)
        self.assertTrue(result["ok"])
        self.assertEqual(result["quality_label"], "1440p")

    def test_hls_variant_resolution_uses_final_redirect_url_and_skips_non_https(self):
        playlist = (
            b"#EXTM3U\n"
            b"#EXT-X-STREAM-INF:BANDWIDTH=5400000,RESOLUTION=1920x1080\n"
            b"variants/fullhd.m3u8\n"
            b"#EXT-X-STREAM-INF:BANDWIDTH=700000,RESOLUTION=640x360\n"
            b"http://insecure.example.org/low.m3u8\n"
        )
        with patch.object(
            _LIVE_TV,
            "_fetch_small_public_response",
            return_value=(200, "https://cdn.example.org/live/master.m3u8", "application/x-mpegURL", playlist),
        ):
            result = probe_live_tv_source("https://media.example.org/start.m3u8")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["variant_sources"]), 1)
        self.assertEqual(result["variant_sources"][0]["name"], "1080p")
        self.assertEqual(
            result["variant_sources"][0]["source"],
            "https://cdn.example.org/live/variants/fullhd.m3u8",
        )

    def test_youtube_source_probe_returns_public_oembed_metadata(self):
        payload = {"title": "Live news", "author_name": "Example News", "thumbnail_url": "https://i.ytimg.com/vi/XPy9T3ePqoQ/hqdefault.jpg"}
        body = json.dumps(payload).encode("utf-8")
        with patch.object(_LIVE_TV, "_fetch_small_public_response", return_value=(200, "https://www.youtube.com/oembed", "application/json", body)):
            result = probe_live_tv_source("https://www.youtube.com/live/XPy9T3ePqoQ")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "youtube")
        self.assertEqual(result["author_name"], "Example News")
        self.assertIn("thumbnail_url", result)

    def test_probe_refuses_private_ip_destinations(self):
        with self.assertRaises(LiveTVConfigError):
            _LIVE_TV._assert_public_https_url("https://127.0.0.1/live.m3u8")

    def test_gallery_logo_upload_accepts_only_supported_raster_signatures(self):
        self.assertEqual(live_tv_logo_extension(b"\x89PNG\r\n\x1a\nrest"), ".png")
        self.assertEqual(live_tv_logo_extension(b"\xff\xd8\xffrest"), ".jpg")
        self.assertEqual(live_tv_logo_extension(b"GIF89arest"), ".gif")
        self.assertEqual(live_tv_logo_extension(b"RIFF0000WEBPrest"), ".webp")
        self.assertIsNone(live_tv_logo_extension(b"<svg xmlns='http://www.w3.org/2000/svg'>"))
        self.assertIsNone(live_tv_logo_extension(b"not an image"))

    def test_duplicate_channel_ids_are_rejected(self):
        with self.assertRaises(LiveTVConfigError):
            normalize_live_tv_channels([
                {"name": "News", "source": "https://www.youtube.com/live/XPy9T3ePqoQ"},
                {"name": "News", "source": "https://www.youtube.com/live/XPy9T3ePqoQ"},
            ])


if __name__ == "__main__":
    unittest.main()
