"""Remembered ROIs: saved with their image size, fitted to the image at the next start."""
import sys
import tempfile
import unittest
from pathlib import Path

from PySide6 import QtCore

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client'))

from roi_settings import fit_roi, load_roi, save_roi  # noqa: E402


class RoiSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = QtCore.QSettings(str(Path(self.tmp.name) / 'client.ini'), QtCore.QSettings.Format.IniFormat)

    def tearDown(self):
        del self.settings
        self.tmp.cleanup()

    def test_save_load_and_forget(self):
        self.assertIsNone(load_roi(self.settings, 'LEFT'))
        save_roi(self.settings, 'LEFT', (96, 168, 351, 359), (640, 480))
        save_roi(self.settings, 'RIGHT', (10, 20, 300, 400), (1920, 1080))
        self.settings.sync()
        reread = QtCore.QSettings(self.settings.fileName(), QtCore.QSettings.Format.IniFormat)
        self.assertEqual(load_roi(reread, 'LEFT'), ((96, 168, 351, 359), (640, 480)))
        self.assertEqual(load_roi(reread, 'RIGHT'), ((10, 20, 300, 400), (1920, 1080)))
        save_roi(self.settings, 'LEFT', None, (640, 480))
        self.assertIsNone(load_roi(self.settings, 'LEFT'))

    def test_damaged_values_are_ignored(self):
        for value in ('', 'abc', '1,2,3', '1,2,3,4,0,480'):
            self.settings.setValue('roi/LEFT', value)
            self.assertIsNone(load_roi(self.settings, 'LEFT'), value)

    def test_fit_same_size_and_same_shape(self):
        self.assertEqual(fit_roi((96, 168, 351, 359), (640, 480), (640, 480)), ((96, 168, 351, 359), None))
        bounds, reason = fit_roi((96, 168, 351, 359), (640, 480), (1280, 960))
        self.assertEqual(bounds, (192, 336, 702, 718))
        self.assertIsNone(reason)

    def test_different_shape_is_not_applied(self):
        bounds, reason = fit_roi((96, 168, 351, 359), (640, 480), (1920, 1080))
        self.assertIsNone(bounds)
        self.assertIn('640×480', reason)
        self.assertIn('1920×1080', reason)

    def test_clamped_or_dropped_outside_the_image(self):
        self.assertEqual(fit_roi((600, 400, 900, 700), (1000, 750), (640, 480))[0], (384, 256, 576, 448))
        self.assertIsNone(fit_roi((630, 470, 640, 480), (640, 480), (640, 480))[0])


if __name__ == '__main__':
    unittest.main()
