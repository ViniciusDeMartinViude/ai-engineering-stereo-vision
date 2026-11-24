"""Per-camera levelling: rotate each camera image so the conveyor runs straight.

The operator draws a line along the conveyor on one camera. That camera's image
is then rotated about its centre so the line becomes horizontal, or vertical
when it was closer to vertical, whichever needs the smaller turn. The image
keeps its size, so the corners the rotation uncovers are black.

The levelled images are what the client shows, what ROIs are drawn on and what
YOLO sees. The raw frame, and with it the timestamp strip, is never rotated.
"""
import math

import cv2

from stamp_protocol import split_stereo

SIDES = ('LEFT', 'RIGHT')
MIN_LINE_PX = 40      # shorter drags are treated as accidental clicks
EPSILON_DEG = 0.01    # smaller angles count as level (no rotation, no copy)


def line_correction(x0, y0, x1, y1):
    """Degrees to add to a camera's rotation so this line becomes level.

    Coordinates are image pixels with y pointing down, as displayed. The result
    follows OpenCV: positive turns the image counter-clockwise on screen. A line
    nearer to vertical is made vertical, so the turn is never more than 45°.
    Returns None for a line shorter than MIN_LINE_PX.
    """
    dx, dy = x1 - x0, y1 - y0
    if math.hypot(dx, dy) < MIN_LINE_PX:
        return None
    # Clockwise-positive on screen; the direction the line was drawn in doesn't matter.
    theta = math.degrees(math.atan2(dy, dx))
    while theta > 90:
        theta -= 180
    while theta <= -90:
        theta += 180
    if theta > 45:
        theta -= 90
    elif theta < -45:
        theta += 90
    return theta


def normalize_angle(angle):
    """Angle in (-180, 180], rounded to 0.01°; 0.0 when it is effectively level."""
    angle = math.fmod(float(angle), 360.0)
    if angle > 180:
        angle -= 360
    elif angle <= -180:
        angle += 360
    angle = round(angle, 2)
    return 0.0 if abs(angle) < EPSILON_DEG else angle


def format_angle(angle):
    """'3.2°' / '−0.75°' with a real minus sign, for headers and tooltips."""
    text = f'{abs(angle):.2f}'.rstrip('0').rstrip('.')
    return ('−' if angle < 0 else '') + text + '°'


def rotation_matrix(width, height, angle):
    return cv2.getRotationMatrix2D(((width - 1) / 2.0, (height - 1) / 2.0), angle, 1.0)


def level_image(image, angle):
    """``image`` rotated by ``angle`` degrees about its centre, same size, black corners."""
    h, w = image.shape[:2]
    return cv2.warpAffine(image, rotation_matrix(w, h, angle), (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def level_stereo_frame(frame, angles):
    """Rotate the LEFT / RIGHT camera regions of a stereo frame by their angles.

    Returns ``frame`` itself when no camera needs turning. Otherwise returns a
    copy in the same layout: rotated camera regions, the footer unchanged.
    """
    active = {side: angles.get(side, 0.0) for side in SIDES
              if abs(angles.get(side, 0.0) or 0.0) >= EPSILON_DEG}
    if not active:
        return frame
    out = frame.copy()
    for side, source, target in zip(SIDES, split_stereo(frame), split_stereo(out)):
        if side in active:
            target[...] = level_image(source, active[side])
    return out


def load_angles(settings):
    """Saved per-camera angles from QSettings (missing or unreadable values count as 0)."""
    angles = {}
    for side in SIDES:
        try:
            angles[side] = normalize_angle(settings.value(f'level/{side}', 0.0) or 0.0)
        except (TypeError, ValueError):
            angles[side] = 0.0
    return angles


def save_angle(settings, side, angle):
    if angle:
        settings.setValue(f'level/{side}', float(angle))
    else:
        settings.remove(f'level/{side}')
