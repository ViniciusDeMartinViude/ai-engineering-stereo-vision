"""Camera levelling: angle from a drawn line, and rotating one camera of a stereo frame."""
import math
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'common'), str(ROOT / 'client')]

from camera_level import (  # noqa: E402
    format_angle, level_stereo_frame, line_correction, normalize_angle,
)
from stamp_protocol import FOOTER_H, decode_strip, encode_strip, split_stereo  # noqa: E402


def tilted_line(degrees, length=600, origin=(100, 100)):
    """End points of a line drawn `degrees` clockwise from horizontal on screen (y down)."""
    x0, y0 = origin
    t = math.radians(degrees)
    return x0, y0, x0 + length * math.cos(t), y0 + length * math.sin(t)


def line_slope_degrees(image):
    """Angle of the bright line in a grayscale-able image, from a least-squares fit."""
    gray = image if image.ndim == 2 else image[:, :, 1]
    ys, xs = np.nonzero(gray > 128)
    slope = np.polyfit(xs, ys, 1)[0]
    return math.degrees(math.atan(slope))


class LineCorrectionTest(unittest.TestCase):
    def test_tilts_either_way_and_either_direction(self):
        self.assertAlmostEqual(line_correction(*tilted_line(0)), 0.0)
        self.assertAlmostEqual(line_correction(*tilted_line(5)), 5.0)
        self.assertAlmostEqual(line_correction(*tilted_line(-7.5)), -7.5)
        x0, y0, x1, y1 = tilted_line(5)
        self.assertAlmostEqual(line_correction(x1, y1, x0, y0), 5.0, msg='drawn right to left')

    def test_near_vertical_lines_are_made_vertical(self):
        self.assertAlmostEqual(line_correction(*tilted_line(85)), -5.0)
        self.assertAlmostEqual(line_correction(*tilted_line(-88)), 2.0)
        self.assertAlmostEqual(abs(line_correction(*tilted_line(45))), 45.0)

    def test_short_drag_is_ignored(self):
        self.assertIsNone(line_correction(10, 10, 30, 20))

    def test_angle_normalisation_and_text(self):
        self.assertEqual(normalize_angle(370), 10.0)
        self.assertEqual(normalize_angle(-190), 170.0)
        self.assertEqual(normalize_angle(0.004), 0.0)
        self.assertEqual(format_angle(3.2), '3.2°')
        self.assertEqual(format_angle(-0.75), '−0.75°')
        self.assertEqual(format_angle(2.0), '2°')


class LevelFrameTest(unittest.TestCase):
    W, H = 960, 540

    def stereo_frame(self, left_tilt, right_tilt):
        frame = np.zeros((self.H + FOOTER_H, 2 * self.W, 3), np.uint8)
        left, right = split_stereo(frame)
        for view, tilt in ((left, left_tilt), (right, right_tilt)):
            x0, y0, x1, y1 = tilted_line(tilt, length=700, origin=(130, 200))
            cv2.line(view, (round(x0), round(y0)), (round(x1), round(y1)), (255, 255, 255), 3)
        encode_strip(frame, 1794834000123_000_000, 1794834000124_000_000, 7)  # real footer
        return frame

    def test_drawn_line_becomes_level_on_that_camera_only(self):
        frame = self.stereo_frame(6.0, -3.0)
        before = frame.copy()
        left, _ = split_stereo(frame)
        self.assertAlmostEqual(line_slope_degrees(left), 6.0, delta=0.3)
        correction = line_correction(*tilted_line(6.0))
        levelled = level_stereo_frame(frame, {'LEFT': correction, 'RIGHT': 0.0})
        self.assertTrue(np.array_equal(frame, before), 'the raw frame is never modified')
        new_left, new_right = split_stereo(levelled)
        self.assertAlmostEqual(line_slope_degrees(new_left), 0.0, delta=0.3)
        self.assertTrue(np.array_equal(new_right, split_stereo(before)[1]), 'other camera untouched')
        self.assertTrue(np.array_equal(levelled[self.H:], before[self.H:]), 'footer untouched')
        self.assertIsNotNone(decode_strip(before)[0])
        self.assertEqual(decode_strip(levelled)[0], decode_strip(before)[0])
        self.assertEqual(levelled.shape, frame.shape)

    def test_both_cameras_and_no_copy_when_level(self):
        frame = self.stereo_frame(-4.0, 2.5)
        levelled = level_stereo_frame(frame, {'LEFT': -4.0, 'RIGHT': 2.5})
        for view in split_stereo(levelled):
            self.assertAlmostEqual(line_slope_degrees(view), 0.0, delta=0.3)
        self.assertIs(level_stereo_frame(frame, {'LEFT': 0.0, 'RIGHT': 0.0}), frame)
        self.assertIs(level_stereo_frame(frame, {}), frame)


if __name__ == '__main__':
    unittest.main()
