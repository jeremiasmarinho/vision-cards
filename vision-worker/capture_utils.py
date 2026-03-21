"""
capture_utils.py — Utilitário de captura de tela via mss.
Equivalente ao utils_windows.py extraído do sistema original.
"""

import threading
import mss
import numpy as np
from PIL import Image


def capture_to_pil(region: dict, lock: threading.Lock) -> Image.Image:
    """Captura uma região e retorna como PIL.Image (RGBA)."""
    with lock:
        with mss.mss() as sct:
            raw = sct.grab(region)
            return Image.frombytes("RGBA", (raw.width, raw.height), raw.raw)


def capture_region(region: dict, filename: str, lock: threading.Lock) -> None:
    """Captura uma região e salva em arquivo PNG."""
    img = capture_to_pil(region, lock)
    img.save(filename)
