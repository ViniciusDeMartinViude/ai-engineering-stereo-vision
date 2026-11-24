"""Remember each camera's YOLO ROI on this PC, and fit it to the image at the next start.

The ROI is saved with the image size it was drawn on. Camera mode negotiation
can give a different resolution at the next start:

* same shape (e.g. 640×480 -> 1280×960): the ROI is scaled to the new size;
* different shape (e.g. 640×480 -> 1920×1080): the camera shows a different
  field of view, so a scaled ROI would cover the wrong area. It isn't applied,
  and stays saved for when that resolution returns.
"""
from __future__ import annotations

ASPECT_TOLERANCE = 0.01
MIN_SIZE = 16


def save_roi(settings, side, bounds, image_size):
    """Store ``bounds`` (x1, y1, x2, y2) drawn on an image of ``image_size`` (w, h); None forgets it."""
    key = f'roi/{side}'
    if bounds is None:
        settings.remove(key)
        return
    x1, y1, x2, y2 = (int(v) for v in bounds)
    w, h = (int(v) for v in image_size)
    settings.setValue(key, f'{x1},{y1},{x2},{y2},{w},{h}')


def load_roi(settings, side):
    """((x1, y1, x2, y2), (w, h)) saved for this camera, or None."""
    text = settings.value(f'roi/{side}', '')
    if isinstance(text, (list, tuple)):  # tolerate a value written as a list
        text = ','.join(str(v) for v in text)
    try:
        values = [int(float(v)) for v in str(text or '').split(',')]
    except ValueError:
        return None
    if len(values) != 6 or values[4] <= 0 or values[5] <= 0:
        return None
    return tuple(values[:4]), (values[4], values[5])


def fit_roi(bounds, saved_size, image_size):
    """The saved ROI on an image of ``image_size``: (bounds, None) or (None, reason)."""
    sw, sh = saved_size
    w, h = image_size
    x1, y1, x2, y2 = bounds
    if (sw, sh) != (w, h):
        if abs(sw / sh - w / h) > ASPECT_TOLERANCE * (w / h):
            return None, (f'Saved ROI not applied: it was drawn at {sw}×{sh}, and the camera now '
                          f'sends {w}×{h}, a different view. Draw it again, or it returns at {sw}×{sh}.')
        scale = w / sw
        x1, y1, x2, y2 = (round(v * scale) for v in (x1, y1, x2, y2))
    x1, x2 = max(0, min(w, x1)), max(0, min(w, x2))
    y1, y2 = max(0, min(h, y1)), max(0, min(h, y2))
    if x2 - x1 < MIN_SIZE or y2 - y1 < MIN_SIZE:
        return None, 'Saved ROI not applied: it falls outside the current image.'
    return (x1, y1, x2, y2), None
