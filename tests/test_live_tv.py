import importlib.util
import unittest
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "Backend" / "helper" / "live_tv.py"
_SPEC = importlib.util.spec_from_file_location("live_tv_test_module", _MODULE_PATH)
_LIVE_TV = importlib.util.module_from_spec(_SPEC)
assert _SPEC is not None and _SPEC.loader is not None
_SPEC.loader.exec_module(_LIVE_TV)

LiveTVConfigError = _LIVE_TV.LiveTVConfigError
live_channel_meta = _LIVE_TV.live_channel_meta
live_channel_stream = _LIVE_TV.live_channel_stream
normalize_live_tv_channels = _LIVE_TV.normalize_live_tv_channels
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

    def test_watch_and_bare_youtube_ids_are_supported(self):
        self.assertEqual(youtube_video_id("https://www.youtube.com/watch?v=XPy9T3ePqoQ"), "XPy9T3ePqoQ")
        self.assertEqual(youtube_video_id("XPy9T3ePqoQ"), "XPy9T3ePqoQ")

    def test_direct_https_m3u8_is_supported(self):
        channels = normalize_live_tv_channels([
            {"name": "Official Stream", "source": "https://media.example.org/live/channel.m3u8?token=abc"}
        ])
        self.assertEqual(
            live_channel_stream(channels[0])["url"],
            "https://media.example.org/live/channel.m3u8?token=abc",
        )

    def test_non_https_or_non_direct_source_is_rejected(self):
        for source in (
            "http://media.example.org/live/channel.m3u8",
            "https://media.example.org/watch/live",
            "https://notyoutube.com/live/XPy9T3ePqoQ",
        ):
            with self.subTest(source=source):
                with self.assertRaises(LiveTVConfigError):
                    normalize_live_tv_channels([{"name": "Test", "source": source}])

    def test_duplicate_channel_ids_are_rejected(self):
        with self.assertRaises(LiveTVConfigError):
            normalize_live_tv_channels([
                {"name": "News", "source": "https://www.youtube.com/live/XPy9T3ePqoQ"},
                {"name": "News", "source": "https://www.youtube.com/live/XPy9T3ePqoQ"},
            ])


if __name__ == "__main__":
    unittest.main()
