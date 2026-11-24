"""Object speed: Ultralytics ByteTrack IDs per camera, and speed from capture times."""
import os
os.environ.setdefault('MKL_THREADING_LAYER', 'SEQUENTIAL')  # before NumPy: see client/yolo_detector.py
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'common'), str(ROOT / 'client')]

from speed_estimation import SpeedEstimator, format_speed  # noqa: E402
from yolo_detector import Detection, assign_track_ids, make_tracker  # noqa: E402

SHAPE = (480, 640, 3)


def box(x, y=240.0, side='LEFT', label='Galaxy', conf=0.9, track_id=-1, half=30.0):
    return Detection(side, label, conf, (x - half, y - half, x + half, y + half), 2, track_id)


class TrackingTest(unittest.TestCase):
    def test_ids_stay_with_moving_objects_and_cameras_are_separate(self):
        left, right = make_tracker(), make_tracker()
        self.assertIsNotNone(left, 'Ultralytics ByteTrack is available')
        seen = []
        for step in range(8):  # two objects moving right 12 px per processed frame
            tracked = assign_track_ids(left, [box(100 + 12 * step), box(400 + 12 * step, y=120)], SHAPE)
            seen.append(tuple(d.track_id for d in tracked))
            assign_track_ids(right, [box(300 - 12 * step, side='RIGHT')], SHAPE)
        ids = seen[-1]
        self.assertTrue(all(i >= 0 for i in ids) and ids[0] != ids[1], seen)
        self.assertEqual(seen[-3:], [ids] * 3, 'IDs are stable while the objects move')
        right_ids = assign_track_ids(right, [box(300 - 12 * 8, side='RIGHT')], SHAPE)
        self.assertGreaterEqual(right_ids[0].track_id, 0)

    def test_empty_frames_and_no_tracker(self):
        tracker = make_tracker()
        self.assertEqual(assign_track_ids(tracker, [], SHAPE), [])
        plain = assign_track_ids(None, [box(100)], SHAPE)
        self.assertEqual(plain[0].track_id, -1)


class SpeedTest(unittest.TestCase):
    def test_speed_from_capture_times_with_skipped_frames(self):
        estimator = SpeedEstimator()
        # 200 px/s to the right; frames processed at irregular times (YOLO skips frames).
        velocities = {}
        for t in (0, 100, 300, 400, 700):
            velocities = estimator.update(1_000_000 + t, [box(100 + 0.2 * t, track_id=7)])
        vx, vy = velocities[7]
        self.assertAlmostEqual(vx, 200.0, places=6)
        self.assertAlmostEqual(vy, 0.0, places=6)

    def test_needs_some_history_then_follows_changes(self):
        estimator = SpeedEstimator()
        self.assertEqual(estimator.update(0, [box(100, track_id=1)]), {})
        self.assertEqual(estimator.update(100, [box(110, track_id=1)]), {}, 'under 150 ms of history')
        self.assertAlmostEqual(estimator.update(200, [box(120, track_id=1)])[1][0], 100.0)
        for t in range(300, 2000, 100):  # the belt stops at x = 120
            estimator.update(t, [box(120, track_id=1)])
        self.assertAlmostEqual(estimator.velocities()[1][0], 0.0, msg='rolling window, not locked')

    def test_untracked_unreadable_time_and_forgetting(self):
        estimator = SpeedEstimator()
        estimator.update(0, [box(100, track_id=-1)])
        self.assertEqual(estimator.history, {}, 'untracked detections are ignored')
        estimator.update(0, [box(100, track_id=3)])
        estimator.update(None, [box(400, track_id=3)])
        self.assertEqual(len(estimator.history[3]), 1, 'no capture time, no new point')
        estimator.update(5000, [box(100, track_id=4)])
        self.assertNotIn(3, estimator.history, 'unseen for over 2 s')

    def test_text(self):
        self.assertEqual(format_speed((168.0, 0.0)), '168 px/s')
        self.assertEqual(format_speed((120.0, 160.0), mm_per_px=0.5), '100 mm/s')
        self.assertEqual(format_speed((1.0, 1.0), mm_per_px=0.5), 'still')


if __name__ == '__main__':
    unittest.main()
