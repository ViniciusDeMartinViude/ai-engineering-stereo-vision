"""Pixel-coordinate ROI helpers for two-camera stereo client.

ROI coordinates are in each original camera's coordinate space, never in
resized display coordinates. The 100px transport metadata strip is excluded.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageLayout:
    left: int
    top: int
    width: int
    height: int
    source_width: int
    source_height: int
    mode: str

    def point(self, x, y):
        if not (self.left <= x < self.left + self.width and
                self.top <= y < self.top + self.height):
            return None
        source_x = min(self.source_width - 1, max(0,
                       int((x - self.left) * self.source_width / self.width)))
        source_y = min(self.source_height - 1, max(0,
                       int((y - self.top) * self.source_height / self.height)))
        return source_x, source_y

    def rectangle(self, bounds):
        x1, y1, x2, y2 = bounds
        sx = self.width / self.source_width
        sy = self.height / self.source_height
        return (self.left + round(x1 * sx), self.top + round(y1 * sy),
                self.left + round(x2 * sx), self.top + round(y2 * sy))


def normalized_roi(start, end, width, height, min_size=16):
    x1, x2 = sorted((int(start[0]), int(end[0])))
    y1, y2 = sorted((int(start[1]), int(end[1])))
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 - x1 < min_size or y2 - y1 < min_size:
        return None
    return x1, y1, x2, y2


def preview_layout(frame_shape, screen_size, display_mode):
    """Identical video geometry to the client Pillow preview implementation."""
    h, w = frame_shape[:2]
    video_h = max(1, h - 100)
    half = w // 2
    sw, sh = screen_size
    target_w = max(500, sw - 65)
    target_h = max(400, sh - 115)
    hud_height = 350
    source_w = half if display_mode in ('left', 'right') else w
    scale = min(target_w / source_w, max(120, target_h - hud_height) / video_h, 1.0)
    new_w = max(1, round(source_w * scale))
    new_h = max(1, round(video_h * scale))
    output_width = max(new_w, min(target_w, 1040))
    video_left = (output_width - new_w) // 2
    return ImageLayout(video_left, 0, new_w, new_h,
                       source_w, video_h, display_mode), output_width, new_h
