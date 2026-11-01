import asyncio
import unittest

from Backend.helper.settings_manager import Settings, SettingsManager


class _FailingSettingsDB:
    async def save_settings(self, settings):
        return False


class SettingsManagerTests(unittest.TestCase):
    def test_failed_database_write_does_not_flip_in_memory_settings(self):
        previous = SettingsManager._current
        old_settings = Settings({"admin_username": "before", "live_tv_channels": []})
        SettingsManager._current = old_settings

        async def update():
            await SettingsManager.update(
                _FailingSettingsDB(),
                {"admin_username": "after", "live_tv_channels": []},
            )

        try:
            with self.assertRaisesRegex(RuntimeError, "could not be saved"):
                asyncio.run(update())
            self.assertIs(SettingsManager._current, old_settings)
            self.assertEqual(SettingsManager.current().admin_username, "before")
        finally:
            SettingsManager._current = previous


if __name__ == "__main__":
    unittest.main()
