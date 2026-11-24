"""Drag magnifier: which pixels it enlarges, how much, and where it sits."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'common'), str(ROOT / 'client')]

from loupe import SIZE, corner_for, corner_rect, loupe_zoom, magnify, zoom_text  # noqa: E402


class LoupeTest(unittest.TestCase):
    def setUp(self):
        # 100 x 80 image; every pixel's value encodes its column, a marked pixel at (40, 30).
        self.image = np.zeros((80, 100, 3), np.uint8)
        self.image[:, :, 0] = np.arange(100, dtype=np.uint8)[None, :]
        self.image[30, 40] = (255, 255, 255)

    def test_centre_of_the_view_is_the_pixel_under_the_pointer(self):
        view = magnify(self.image, 40.5, 30.5, 8.0)  # pointer in the middle of pixel (40, 30)
        self.assertEqual(view.shape, (SIZE, SIZE, 3))
        self.assertTrue((view[SIZE // 2, SIZE // 2] == 255).all())
        # Native pixels are enlarged without smoothing: an 8 x 8 block of the same colour.
        block = view[SIZE // 2 - 3:SIZE // 2 + 4, SIZE // 2 - 3:SIZE // 2 + 4]
        self.assertTrue((block == 255).all())
        # One native pixel to the right is 8 view pixels to the right.
        self.assertEqual(view[SIZE // 2 + 3, SIZE // 2 + 8, 0], 41)

    def test_outside_the_image_is_black(self):
        view = magnify(self.image, 1.0, 1.0, 4.0)
        self.assertTrue((view[:SIZE // 2 - 8, :SIZE // 2 - 8] == 0).all())
        self.assertTrue(view[SIZE // 2 + 8:, SIZE // 2 + 8:].any())

    def test_zoom_follows_the_screen_scale(self):
        self.assertAlmostEqual(loupe_zoom(0.47), 1.88)  # 1080p on a ~900 px tile: 4x of what is shown
        self.assertEqual(loupe_zoom(0.1), 1.0)    # never smaller than native
        self.assertEqual(loupe_zoom(5.0), 12.0)   # already zoomed far in: capped
        self.assertEqual(zoom_text(4.0), '4×')
        self.assertEqual(zoom_text(1.88), '1.9×')

    def test_it_sits_away_from_the_pointer_and_the_drag_start(self):
        area, loupe = (900, 600), (204, 228)
        self.assertEqual(corner_for([(100, 100)], *area, *loupe), 'bottom-right')
        self.assertEqual(corner_for([(450, 550)], *area, *loupe), 'top-left', 'ties prefer the top')
        # Pointer bottom right, drag started top left: neither corner is covered.
        self.assertIn(corner_for([(700, 500), (100, 100)], *area, *loupe), ('top-right', 'bottom-left'))
        # During a drag it stays put while the pointer is clear of it, and moves when needed.
        self.assertEqual(corner_for([(450, 300)], *area, *loupe, current='top-right'), 'top-right')
        self.assertNotEqual(corner_for([(700, 120)], *area, *loupe, current='top-right'), 'top-right')
        self.assertEqual(corner_rect('bottom-right', *area, *loupe), (900 - 204 - 12, 600 - 228 - 12, 204, 228))


if __name__ == '__main__':
    unittest.main()
