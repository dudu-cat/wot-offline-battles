import unittest
from unittest import mock

import i18n


class LanguageTest(unittest.TestCase):
    def test_chinese_locale_variants_select_chinese(self):
        for value in ("zh", "zh-CN", "zh_Hant_TW", "ZH-sg.UTF-8"):
            self.assertEqual(
                i18n.LANGUAGE_CHINESE, i18n.language_for_locale(value))

    def test_unsupported_or_missing_locale_falls_back_to_english(self):
        for value in (None, "", "en-US", "fr_FR.UTF-8", "ja-JP"):
            self.assertEqual(
                i18n.LANGUAGE_ENGLISH, i18n.language_for_locale(value))

    def test_environment_locale_is_used_outside_windows(self):
        self.assertEqual(i18n.LANGUAGE_CHINESE, i18n.detect_system_language(
            platform_name="darwin", environment={"LANG": "zh_CN.UTF-8"},
            locale_getter=lambda: ("en_US", "UTF-8")))

    def test_locale_api_is_the_last_non_windows_fallback(self):
        self.assertEqual(i18n.LANGUAGE_CHINESE, i18n.detect_system_language(
            platform_name="linux", environment={},
            locale_getter=lambda: ("zh_TW", "UTF-8")))

    def test_locale_detection_failure_falls_back_to_english(self):
        def fail():
            raise OSError("locale unavailable")

        self.assertEqual(i18n.LANGUAGE_ENGLISH, i18n.detect_system_language(
            platform_name="linux", environment={}, locale_getter=fail))

    def test_windows_prefers_the_ui_language(self):
        with mock.patch.object(
                i18n, "_windows_ui_language",
                return_value=i18n.LANGUAGE_CHINESE):
            self.assertEqual(
                i18n.LANGUAGE_CHINESE,
                i18n.detect_system_language(
                    platform_name="win32", environment={"LANG": "en_US"}))

    def test_explicit_language_does_not_consult_the_system(self):
        with mock.patch.object(i18n, "detect_system_language") as detect:
            self.assertEqual(
                i18n.LANGUAGE_CHINESE,
                i18n.resolve_language(i18n.LANGUAGE_CHINESE))
            self.assertEqual(
                i18n.LANGUAGE_ENGLISH,
                i18n.resolve_language(i18n.LANGUAGE_ENGLISH))
        detect.assert_not_called()

    def test_invalid_preference_falls_back_to_english(self):
        self.assertEqual(
            i18n.LANGUAGE_ENGLISH,
            i18n.resolve_language("unsupported", detected="zh_CN"))


if __name__ == "__main__":
    unittest.main()
