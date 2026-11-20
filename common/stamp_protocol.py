"""Visual timestamp protocol shared by the camera server and every client.

A combined stereo frame is [LEFT | RIGHT] on top of a FOOTER_H pixel metadata
footer. The footer carries a 168-bit packet drawn as 6x16 pixel black/white
cells (black=1, white=0, MSB first):

    > H B Q I i H
      magic(0xA55A), version(1), left UTC ms, sequence,
      signed right-minus-left capture time in microseconds, CRC16-CCITT.

Both encoder and decoder live here so the two sides cannot drift apart.
"""
from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass

import cv2
import numpy as np

MAGIC = 0xA55A
VERSION = 1
FOOTER_H = 100
CELL_W = 6
CELL_H = 16
BIT_COUNT = 168
STRIP_X0 = 12
STRIP_BOTTOM_OFFSET = 11 + CELL_H
STRIP_WIDTH = BIT_COUNT * CELL_W
HEADER_STRUCT = struct.Struct('>HBQIi')   # packet without CRC
PACKET_STRUCT = struct.Struct('>HBQIiH')  # packet with CRC


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for octet in data:
        crc ^= octet << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Stamp:
    left_utc_ms: int
    sequence: int
    right_minus_left_us: int

    @property
    def right_utc_ms(self):
        return self.left_utc_ms + self.right_minus_left_us / 1000.0


def iso_utc(milliseconds):
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc).strftime('%H:%M:%S.%f')[:-3] + ' UTC'


def video_height(frame_height: int) -> int:
    """Height of the camera area, excluding the metadata footer."""
    return max(1, frame_height - FOOTER_H)


def split_stereo(frame: np.ndarray):
    """Return (left, right) camera views as slices of ``frame`` (no copies)."""
    source_h = video_height(frame.shape[0])
    half = frame.shape[1] // 2
    return frame[:source_h, :half], frame[:source_h, half:]


def strip_origin(frame_height: int):
    """Top-left pixel (x, y) of the 168-bit strip in a frame of this height."""
    return STRIP_X0, frame_height - STRIP_BOTTOM_OFFSET


def extract_strip(frame: np.ndarray):
    """Raw strip pixels (CELL_H x STRIP_WIDTH x 3) or None if out of bounds."""
    x0, y0 = strip_origin(frame.shape[0])
    roi = frame[y0:y0 + CELL_H, x0:x0 + STRIP_WIDTH, :]
    return roi if roi.shape == (CELL_H, STRIP_WIDTH, 3) else None


def encode_strip(image: np.ndarray, left_ns: int, right_ns: int, seq: int) -> int:
    """Draw the packet into ``image`` in place; returns the clamped skew in us."""
    left_ms = left_ns // 1_000_000
    delta_us = max(-2147483648, min(2147483647, (right_ns - left_ns) // 1000))
    payload = HEADER_STRUCT.pack(MAGIC, VERSION, left_ms, seq % (1 << 32), delta_us)
    payload += struct.pack('>H', crc16_ccitt(payload))
    bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
    h, w = image.shape[:2]
    x0 = STRIP_X0
    y0 = h - CELL_H - 11
    if x0 + len(bits) * CELL_W + 12 > w:
        raise RuntimeError('Frame too narrow for binary strip')
    image[y0 - 3:y0 + CELL_H + 3, x0 - 5:x0 + len(bits) * CELL_W + 5] = (45, 45, 45)
    for i, bit in enumerate(bits):
        x = x0 + i * CELL_W
        image[y0:y0 + CELL_H, x:x + CELL_W] = (0, 0, 0) if bit else (255, 255, 255)
    return delta_us


def decode_strip(frame: np.ndarray):
    """Returns (Stamp or None, error_string or None), using raw decoded pixels.

    Requires the unscaled original frame.
    """
    h, w = frame.shape[:2]
    x0, y0 = strip_origin(h)
    if w < x0 + STRIP_WIDTH or y0 < 2:
        return None, f'frame too small: {w}x{h}'
    # Sample central 3x8 pixels in each cell, avoiding H264 block edges.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    region = gray[y0 + 4:y0 + 12, x0:x0 + STRIP_WIDTH]
    if region.shape != (8, STRIP_WIDTH):
        return None, 'strip outside image'
    cells = region.reshape(8, BIT_COUNT, CELL_W)
    centers = cells[:, :, 2:5].mean(axis=(0, 2))
    # A per-frame adaptive mid-point tolerates changed brightness/compression.
    black, white = float(np.percentile(centers, 8)), float(np.percentile(centers, 92))
    if white - black < 45:
        return None, f'insufficient strip contrast ({white - black:.0f})'
    bits = (centers < (black + white) / 2).astype(np.uint8)
    raw = np.packbits(bits).tobytes()
    try:
        magic, version, left_ms, seq, delta_us, crc = PACKET_STRUCT.unpack(raw)
    except struct.error as exc:
        return None, str(exc)
    if magic != MAGIC:
        return None, f'magic mismatch: 0x{magic:04X}'
    if version != VERSION:
        return None, f'protocol version {version}, expected {VERSION}'
    expected = crc16_ccitt(raw[:-2])
    if crc != expected:
        return None, f'CRC mismatch: {crc:04X} != {expected:04X}'
    return Stamp(left_ms, seq, delta_us), None
