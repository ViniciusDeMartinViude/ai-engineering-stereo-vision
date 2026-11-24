"""Colour themes: complete, switchable, and readable (WCAG AA contrast) in every theme."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client'))

import theme  # noqa: E402


def luminance(hex_color):
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# (foreground token, background token, minimum ratio). 4.5:1 is WCAG AA for text;
# 3:1 for large text and UI parts (marks, the accent as a fill or border).
RULES = [
    ('text', 'background', 4.5), ('text', 'surface', 4.5), ('text', 'raised', 4.5), ('text', 'hover', 4.5),
    ('secondary', 'surface', 4.5), ('secondary', 'raised', 4.5),
    ('muted', 'surface', 4.5), ('muted', 'raised', 4.0),
    ('accent', 'surface', 4.5), ('accent', 'raised', 3.0), ('on_accent', 'accent', 4.5),
    ('good', 'surface', 4.5), ('warning', 'surface', 4.5), ('bad', 'surface', 4.5), ('info', 'surface', 4.5),
    ('good', 'raised', 3.0), ('warning', 'raised', 3.0), ('bad', 'raised', 3.0),
    ('neutral', 'surface', 4.5), ('skew', 'surface', 3.0),
]


class ThemeTest(unittest.TestCase):
    def tearDown(self):
        theme.select_theme(theme.DEFAULT_THEME)

    def test_every_theme_sets_every_token(self):
        self.assertGreaterEqual(len(theme.THEMES), 10)
        for key, entry in theme.THEMES.items():
            self.assertEqual(set(entry['colors']), set(theme.THEME_TOKENS), key)
            self.assertTrue(entry['name'] and entry['inspired'], key)
            for value in entry['colors'].values():
                self.assertRegex(value, r'^#[0-9A-Fa-f]{6}$', key)

    def test_contrast(self):
        failures = []
        for key, entry in theme.THEMES.items():
            c = entry['colors']
            for fg, bg, minimum in RULES:
                ratio = contrast(c[fg], c[bg])
                if ratio < minimum:
                    failures.append(f'{key}: {fg} {c[fg]} on {bg} {c[bg]} = {ratio:.2f} < {minimum}')
        self.assertEqual(failures, [], '\n' + '\n'.join(failures))

    def test_select_updates_colors_in_place(self):
        colors, states = theme.COLORS, theme.STATE_COLORS
        self.assertEqual(theme.select_theme('Light Modern'), 'light-modern')
        self.assertIs(theme.COLORS, colors, 'same dict: modules that imported it see the change')
        self.assertEqual(colors['surface'], '#FFFFFF')
        self.assertEqual(states['bad'], colors['bad'])
        self.assertEqual(states['stale'], colors['neutral'])
        self.assertEqual(colors['roi'], '#F4F7FB', 'video overlay colours are not themed')
        self.assertIn('#FFFFFF', theme.style_sheet())
        self.assertEqual(theme.select_theme('no-such-theme'), theme.DEFAULT_THEME)
        self.assertEqual(colors['surface'], '#121A23')

    def test_ids_and_names(self):
        self.assertEqual(theme.theme_id('Dracula'), 'dracula')
        self.assertEqual(theme.theme_id(' NORD '), 'nord')
        self.assertEqual(theme.theme_id(None), theme.DEFAULT_THEME)


if __name__ == '__main__':
    unittest.main()
