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
        self.assertEqual(result["variants"], ["640x360", "1280x720"])

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

    def test_duplicate_channel_ids_are_rejected(self):
        with self.assertRaises(LiveTVConfigError):
            normalize_live_tv_channels([
                {"name": "News", "source": "https://www.youtube.com/live/XPy9T3ePqoQ"},
                {"name": "News", "source": "https://www.youtube.com/live/XPy9T3ePqoQ"},
            ])


if __name__ == "__main__":
    unittest.main()
