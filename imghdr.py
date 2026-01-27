"""Minimal imghdr shim for environments where stdlib `imghdr` is missing.

This implements a small subset of the stdlib `imghdr.what()` behaviour
that `python-telegram-bot` expects (common image types). It's intentionally
small and only used as a fallback when the standard module is absent.
"""
from __future__ import annotations

from typing import Optional

def _read_header(filename_or_bytes):
    if isinstance(filename_or_bytes, (bytes, bytearray)):
        return bytes(filename_or_bytes[:64])
    try:
        with open(filename_or_bytes, 'rb') as f:
            return f.read(64)
    except Exception:
        return None

def what(filename: str | bytes | bytearray | None, h: bytes | None = None) -> Optional[str]:
    """Return image type string for the given file or header bytes.

    Recognizes: jpeg, png, gif, webp, bmp, tiff, ico, ppm/pgm.
    """
    header = h if h is not None else _read_header(filename)
    if not header:
        return None

    # JPEG
    if header.startswith(b"\xff\xd8\xff"):
        return 'jpeg'

    # PNG
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return 'png'

    # GIF
    if header[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'

    # WebP (RIFF....WEBP)
    if header.startswith(b'RIFF') and b'WEBP' in header[8:16]:
        return 'webp'

    # BMP
    if header.startswith(b'BM'):
        return 'bmp'

    # TIFF (II = little endian, MM = big endian)
    if header.startswith(b'II') or header.startswith(b'MM'):
        return 'tiff'

    # ICO
    if header.startswith(b'\x00\x00\x01\x00'):
        return 'ico'

    # Netpbm formats (P6/P5/P4/P3/P2)
    if header.startswith(b'P6') or header.startswith(b'P5') or header.startswith(b'P4') or header.startswith(b'P3') or header.startswith(b'P2'):
        return 'pbm'

    return None

__all__ = ['what']
