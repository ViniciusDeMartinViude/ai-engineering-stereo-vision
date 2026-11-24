"""Round-trip tests for the shared visual timestamp protocol (stdlib unittest)."""
import os
os.environ.setdefault('MKL_THREADING_LAYER', 'SEQUENTIAL')  # before NumPy: see client/yolo_detector.py
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'common'))

from stamp_protocol import (  # noqa: E402
    FOOTER_H, STRIP_WIDTH, Stamp, crc16_ccitt, decode_strip, encode_strip,
    extract_strip, split_stereo, video_height,
)
from lan_ntp import decode_ntp, encode_ntp  # noqa: E402

LEFT_NS = 1794834000123_000_000


def blank_frame(w=1280, video_h=480):
    return np.full((video_h + FOOTER_H, w, 3), 35, np.uint8)


class StampProtocolTest(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(crc16_ccitt(b'123456789'), 0x29B1)

    def test_round_trip(self):
        frame = blank_frame()
        delta = encode_strip(frame, LEFT_NS, LEFT_NS + 22_450_000, 7351)
        self.assertEqual(delta, 22450)
        stamp, error = decode_strip(frame)
        self.assertIsNone(error)
        self.assertEqual(stamp, Stamp(LEFT_NS // 1_000_000, 7351, 22450))

    def test_negative_skew_and_sequence_wrap(self):
        frame = blank_frame()
        encode_strip(frame, LEFT_NS, LEFT_NS - 5_000_000, (1 << 32) + 3)
        stamp, error = decode_strip(frame)
        self.assertIsNone(error)
        self.assertEqual((stamp.sequence, stamp.right_minus_left_us), (3, -5000))

    def test_survives_jpeg(self):
        frame = blank_frame()
        encode_strip(frame, LEFT_NS, LEFT_NS + 1_000_000, 1)
        _, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        stamp, error = decode_strip(cv2.imdecode(jpg, cv2.IMREAD_COLOR))
        self.assertIsNone(error)
        self.assertEqual(stamp.sequence, 1)

    def test_corruption_is_rejected(self):
        frame = blank_frame()
        encode_strip(frame, LEFT_NS, LEFT_NS, 1)
        x0, y0 = 12, frame.shape[0] - 27
        # Invert one bit cell in the middle of the payload.
        cell = frame[y0:y0 + 16, x0 + 50 * 6:x0 + 51 * 6]
        frame[y0:y0 + 16, x0 + 50 * 6:x0 + 51 * 6] = 255 - cell
        stamp, error = decode_strip(frame)
        self.assertIsNone(stamp)
        self.assertIn('CRC', error)

    def test_too_small_frame(self):
        stamp, error = decode_strip(np.zeros((200, 300, 3), np.uint8))
        self.assertIsNone(stamp)
        self.assertIn('too small', error)

    def test_split_and_extract(self):
        frame = blank_frame(w=1280, video_h=480)
        left, right = split_stereo(frame)
        self.assertEqual(left.shape, (480, 640, 3))
        self.assertEqual(right.shape, (480, 640, 3))
        self.assertTrue(np.shares_memory(left, frame))
        self.assertEqual(video_height(frame.shape[0]), 480)
        encode_strip(frame, LEFT_NS, LEFT_NS, 1)
        self.assertEqual(extract_strip(frame).shape, (16, STRIP_WIDTH, 3))
        self.assertIsNone(extract_strip(np.zeros((120, 100, 3), np.uint8)))


class NtpCodecTest(unittest.TestCase):
    def test_encode_decode(self):
        now = 1_794_834_000.123456
        self.assertAlmostEqual(decode_ntp(encode_ntp(now)), now, places=6)


if __name__ == '__main__':
    unittest.main()
