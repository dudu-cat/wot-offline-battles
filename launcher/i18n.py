"""Small language helper for the desktop launcher."""

from __future__ import annotations

import locale
import os
import sys


LANGUAGE_AUTO = "auto"
LANGUAGE_ENGLISH = "en"
LANGUAGE_CHINESE = "zh"
LANGUAGES = (LANGUAGE_AUTO, LANGUAGE_ENGLISH, LANGUAGE_CHINESE)

LANGUAGE_CHOICES = (
    (LANGUAGE_AUTO, "Auto / 自动"),
    (LANGUAGE_ENGLISH, "English"),
    (LANGUAGE_CHINESE, "中文"),
)


def language_for_locale(locale_name):
    """Map a locale name to a supported UI language, defaulting to English."""
    value = str(locale_name or "").strip().lower().replace("_", "-")
    return (LANGUAGE_CHINESE
            if value == "zh" or value.startswith("zh-")
            else LANGUAGE_ENGLISH)


def _windows_ui_language():
    try:
        import ctypes

        language_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    # The low ten bits are the Windows primary language identifier. Chinese is
    # 0x04 for both simplified and traditional UI languages.
    return (LANGUAGE_CHINESE if language_id & 0x3ff == 0x04
            else LANGUAGE_ENGLISH)


def detect_system_language(platform_name=None, environment=None,
                           locale_getter=None):
    """Best-effort OS UI language detection with an English fallback."""
    platform_name = sys.platform if platform_name is None else platform_name
    if str(platform_name).lower().startswith("win"):
        detected = _windows_ui_language()
        if detected is not None:
            return detected

    environment = os.environ if environment is None else environment
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = environment.get(name)
        if value:
            return language_for_locale(value)

    getter = locale.getlocale if locale_getter is None else locale_getter
    try:
        value = getter()[0]
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        value = None
    return language_for_locale(value)


def resolve_language(preference, detected=None):
    """Resolve a saved auto/en/zh preference to one active language."""
    if preference in (LANGUAGE_ENGLISH, LANGUAGE_CHINESE):
        return preference
    if preference != LANGUAGE_AUTO:
        return LANGUAGE_ENGLISH
    return (detect_system_language() if detected is None
            else language_for_locale(detected))


def choice_for_language(preference):
    for value, label in LANGUAGE_CHOICES:
        if value == preference:
            return label
    return LANGUAGE_CHOICES[1][1]


def language_for_choice(choice):
    for value, label in LANGUAGE_CHOICES:
        if label == choice:
            return value
    return LANGUAGE_ENGLISH
