"""
scan_window_offset.py — Mede o offset X entre duas janelas do emulador na tela.

Captura faixas horizontais nas posições das cartas da mão de P1 e desloca em
incrementos de 1px até achar a posição com máximo conteúdo visual (não-branco,
não-preto) — que indica onde estão as cartas de P2.

Uso:
    python scan_window_offset.py
    python scan_window_offset.py --step 2 --range 700

Resultado: imprime o offset X recomendado e salva imagens de comparação.
"""

import argparse
import configparser
import os

import cv2
import mss
import numpy as np
from PIL import Image

CONFIG = os.path.join(os.path.dirname(__file__), "config.ini")
OUT_DIR = os.path.join(os.path.dirname(__file__), "debug_offset")


def _load_hand_region() -> dict:
    parser = configparser.ConfigParser()
    parser.read(CONFIG, encoding="utf-8")
    selected = parser["CurrentProfile"].get("selected", "").strip()
    sec = parser[selected]
    parts = [p.strip() for p in sec["hand_card1"].split(",")]
    left, top, w, h = map(int, parts)
    # Usa uma faixa mais larga englobando todas as 6 cartas
    all_x = []
    for i in range(1, 7):
        k = f"hand_card{i}"
        if k in sec:
            x = int(sec[k].split(",")[0].strip())
            all_x.append(x)
    x_min = min(all_x) - 5
    x_max = max(all_x) + 20
    return {"left": x_min, "top": top - 5, "width": x_max - x_min, "height": h + 10}


def _visual_content(region: dict, sct: mss.mss) -> float:
    """Retorna a quantidade de pixels 'úteis' (nem brancos nem pretos) na região."""
    raw  = sct.grab(region)
    bgra = np.array(raw)
    gray = bgra[:, :, :3].max(axis=2)
    useful = np.sum((gray > 40) & (gray < 240))
    return float(useful) / max(gray.size, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step",  type=int, default=1,   help="Incremento de busca em px (padrao 1)")
    parser.add_argument("--range", type=int, default=700, help="Amplitude maxima de busca em px (padrao 700)")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    base_region = _load_hand_region()
    print(f"Regiao base P1: left={base_region['left']} top={base_region['top']} "
          f"w={base_region['width']} h={base_region['height']}")

    scores: list[tuple[int, float]] = []

    with mss.mss() as sct:
        # Score da posição P1 (offset 0)
        score_p1 = _visual_content(base_region, sct)
        print(f"Score P1 (offset=0): {score_p1:.3f}")

        print(f"Varrendo offsets 1..{args.range} (step={args.step})...")
        for dx in range(1, args.range + 1, args.step):
            shifted = {**base_region, "left": base_region["left"] + dx}
            s = _visual_content(shifted, sct)
            scores.append((dx, s))
            if dx % 50 == 0:
                print(f"  dx={dx:4d}  score={s:.3f}")

    # Pico de conteúdo fora do intervalo [−20, +20] em torno de dx=0
    best_dx, best_score = max(scores, key=lambda x: x[1])
    print(f"\nMelhor offset encontrado: dx={best_dx}  score={best_score:.3f}")

    if best_score < score_p1 * 0.5:
        print("AVISO: score do pico é bem menor que P1 — a janela P2 pode não estar visível.")
    else:
        print(f"\nComando para P2:")
        print(f"  python main.py --player 2 --offset-x {best_dx}")

    # Salva as capturas P1 e P2 para inspeção visual
    with mss.mss() as sct:
        for label, dx in [("P1_offset0", 0), (f"P2_offset{best_dx}", best_dx)]:
            r = {**base_region, "left": base_region["left"] + dx}
            raw = sct.grab(r)
            img = Image.fromarray(np.array(raw), "RGBA").convert("RGB")
            scale = max(1, 120 // max(img.height, 1))
            img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
            path = os.path.join(OUT_DIR, f"{label}.png")
            img.save(path)
            print(f"Salvo: {path}")


if __name__ == "__main__":
    main()
