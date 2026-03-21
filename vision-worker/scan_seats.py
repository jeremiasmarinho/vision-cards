"""
scan_seats.py — Diagnóstico das coordenadas de assentos do vilão.
Captura cada região e mostra brilho + salva imagem para inspeção visual.
"""

import configparser
import os
import sys
import mss
import numpy as np
from PIL import Image

CONFIG = os.path.join(os.path.dirname(__file__), "config.ini")
OUT_DIR = os.path.join(os.path.dirname(__file__), "debug_seats")
OPP_BRIGHT_PIXEL = 150

def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    parser = configparser.ConfigParser()
    parser.read(CONFIG, encoding="utf-8")
    selected = parser["CurrentProfile"].get("selected", "").strip()
    sec = parser[selected]
    print(f"Layout: [{selected}]")

    seats = []
    for i in range(1, 6):
        key = f"opponent_seat{i}"
        if key not in sec:
            continue
        parts = [p.strip() for p in sec[key].split(",")]
        left, top, w, h = map(int, parts)
        seats.append((i, {"left": left, "top": top, "width": w, "height": h}))

    with mss.mss() as sct:
        for idx, region in seats:
            raw  = sct.grab(region)
            data = np.array(raw)
            brightness = data[:, :, :3].max(axis=2)
            n_bright   = int(np.sum(brightness > OPP_BRIGHT_PIXEL))
            n_total    = brightness.size
            pct        = n_bright / n_total * 100 if n_total else 0
            status     = "ATIVO" if pct >= 30 else "vazio"
            print(f"  seat{idx}: left={region['left']} top={region['top']}"
                  f" bright={pct:.0f}% [{status}]")

            # Salva imagem ampliada para inspeção
            img_bgra = Image.fromarray(data, "RGBA")
            img_rgb  = img_bgra.convert("RGB")
            scale    = max(1, 60 // max(region["width"], region["height"]))
            img_big  = img_rgb.resize(
                (img_rgb.width * scale, img_rgb.height * scale),
                Image.NEAREST
            )
            img_big.save(os.path.join(OUT_DIR, f"seat{idx}.png"))

    print(f"\nImagens salvas em: {OUT_DIR}")

if __name__ == "__main__":
    main()
