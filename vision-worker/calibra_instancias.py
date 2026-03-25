# -*- coding: utf-8 -*-
"""
calibra_instancias.py - Calibracao das 3 instancias/mesas do emulador PPPoker.
================================================================================
Abre uma janela com preview da captura e regiões overlay. Use as teclas para
ajustar o offset entre mesas até que as regiões encaixem nas cartas.

Teclas:
  A/D ou ←/→  : ajustar offset horizontal entre mesas (delta_x)
  W/S ou ↑/↓  : ajustar offset vertical (delta_y)
  Q/E         : fine-tune delta_x (-5 / +5)
  R/F         : fine-tune delta_y (-5 / +5)
  ESPAÇO      : capturar screenshot e salvar presets no config
  ESC         : sair
"""

import configparser
import os
import sys

# UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import cv2
import mss
import numpy as np

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")
OUT_CONFIG = os.path.join(os.path.dirname(__file__), "config.instancias.ini")


def _load_base_regions() -> tuple[list[dict], list[dict]]:
    """Carrega regiões do layout base (Layout_emu_proxy ou selected)."""
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")

    selected = ""
    if "CurrentProfile" in parser:
        selected = parser["CurrentProfile"].get("selected", "").strip()
    if not selected or selected not in parser:
        selected = "Layout_emu_proxy"
    if selected not in parser:
        raise ValueError(f"Layout '{selected}' não encontrado no config.ini")

    sec = parser[selected]

    def read_regions(prefix: str, count: int) -> list[dict]:
        out = []
        for i in range(1, count + 1):
            key = f"{prefix}{i}"
            if key not in sec:
                break
            parts = [p.strip() for p in sec[key].split(",")]
            if len(parts) != 4:
                break
            left, top, w, h = map(int, parts)
            out.append({"left": left, "top": top, "width": w, "height": h})
        return out

    hand  = read_regions("hand_card", 6)
    board = read_regions("board_card", 5)
    return hand, board


def _region_to_screen(region: dict, base_x: int, base_y: int) -> tuple[int, int, int, int]:
    """Converte região (left, top, width, height) para coordenadas na captura.
    base_x, base_y = offset da instância (0, delta, 2*delta)."""
    left   = region["left"] + base_x
    top    = region["top"] + base_y
    width  = region["width"]
    height = region["height"]
    return left, top, left + width, top + height


def _draw_regions(frame: np.ndarray, hand: list, board: list,
                  base_x: int, base_y: int, color: tuple, label: str) -> None:
    """Desenha regiões sobre o frame."""
    for i, r in enumerate(hand):
        x1, y1, x2, y2 = _region_to_screen(r, base_x, base_y)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
        cv2.putText(frame, f"H{i+1}", (x1, y1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
    for i, r in enumerate(board):
        x1, y1, x2, y2 = _region_to_screen(r, base_x, base_y)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
        cv2.putText(frame, f"B{i+1}", (x1, y1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
    cv2.putText(frame, label, (base_x if base_x >= 0 else 0, base_y - 10 if base_y > 20 else 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def _get_instance_bbox(hand: list, board: list, base_x: int, base_y: int,
                       padding: int = 80) -> tuple[int, int, int, int]:
    """Retorna (x1, y1, x2, y2) da caixa que engloba hand+board + padding."""
    all_regions = hand + board
    if not all_regions:
        return 0, 0, 100, 100
    min_x = min(r["left"] + base_x for r in all_regions) - padding
    min_y = min(r["top"] + base_y for r in all_regions) - padding
    max_x = max(r["left"] + base_x + r["width"] for r in all_regions) + padding
    max_y = max(r["top"] + base_y + r["height"] for r in all_regions) + padding
    return min_x, min_y, max_x, max_y


def _crop_instance(screenshot: np.ndarray, hand: list, board: list,
                   base_x: int, base_y: int, color: tuple, label: str,
                   mon_bounds: tuple[int, int, int, int]) -> np.ndarray:
    """Recorta e retorna a area de uma instancia (mao+board) com overlays.
    mon_bounds = (mon_left, mon_top, mon_right, mon_bottom) em coords de tela.
    O screenshot do mss tem (0,0) = (mon_left, mon_top)."""
    mon_l, mon_t, mon_r, mon_b = mon_bounds
    x1, y1, x2, y2 = _get_instance_bbox(hand, board, base_x, base_y)
    # Clip para dentro do screenshot (coords de tela)
    x1 = max(mon_l, min(x1, mon_r - 50))
    y1 = max(mon_t, min(y1, mon_b - 50))
    x2 = max(x1 + 50, min(x2, mon_r))
    y2 = max(y1 + 50, min(y2, mon_b))
    # Converter para indices da imagem: screenshot usa origem em (mon_l, mon_t)
    i1 = max(0, x1 - mon_l)
    j1 = max(0, y1 - mon_t)
    i2 = min(screenshot.shape[1], x2 - mon_l)
    j2 = min(screenshot.shape[0], y2 - mon_t)
    if i2 <= i1 or j2 <= j1:
        return np.zeros((100, 100, 3), dtype=np.uint8)
    crop = screenshot[j1:j2, i1:i2].copy()
    # Origem do crop em coords de tela
    crop_ox = mon_l + i1
    crop_oy = mon_t + j1
    for i, r in enumerate(hand):
        lx = r["left"] + base_x - crop_ox
        ly = r["top"] + base_y - crop_oy
        cv2.rectangle(crop, (lx, ly), (lx + r["width"], ly + r["height"]), color, 2)
        cv2.putText(crop, f"H{i+1}", (lx, max(12, ly - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    for i, r in enumerate(board):
        lx = r["left"] + base_x - crop_ox
        ly = r["top"] + base_y - crop_oy
        cv2.rectangle(crop, (lx, ly), (lx + r["width"], ly + r["height"]), color, 2)
        cv2.putText(crop, f"B{i+1}", (lx, max(12, ly - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.putText(crop, label, (5, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    return crop


def _shift_regions(regions: list[dict], dx: int, dy: int) -> list[dict]:
    """Retorna cópia das regiões com offset aplicado."""
    return [
        {"left": r["left"] + dx, "top": r["top"] + dy, "width": r["width"], "height": r["height"]}
        for r in regions
    ]


def _write_layout_section(parser: configparser.ConfigParser, section: str,
                          hand: list, board: list, opp: list | None = None) -> None:
    """Escreve seção de layout no parser."""
    parser[section] = {}
    for i, r in enumerate(hand, 1):
        parser[section][f"hand_card{i}"] = f"{r['left']}, {r['top']}, {r['width']}, {r['height']}"
    for i, r in enumerate(board, 1):
        parser[section][f"board_card{i}"] = f"{r['left']}, {r['top']}, {r['width']}, {r['height']}"
    if opp:
        for i, r in enumerate(opp, 1):
            parser[section][f"opponent_seat{i}"] = f"{r['left']}, {r['top']}, {r['width']}, {r['height']}"


def main():
    hand_regions, board_regions = _load_base_regions()
    if not hand_regions or not board_regions:
        print("[ERRO] Layout base sem regiões hand/board. Verifique config.ini.")
        sys.exit(1)

    # Offsets entre instâncias (pixels). Ajuste fino via teclas.
    delta_x = 400
    delta_y = 0

    print("Calibracao de 3 instancias")
    print("  A/D ou setas : delta_x +-20")
    print("  W/S ou setas : delta_y +-20")
    print("  Q/E        : delta_x +-5")
    print("  R/F        : delta_y +-5")
    print("  ESPACO     : salvar presets no config")
    print("  ESC        : sair")
    print()

    with mss.mss() as sct:
        monitor = sct.monitors[0]
        mon_l = monitor["left"]
        mon_t = monitor["top"]
        mon_r = monitor["left"] + monitor["width"]
        mon_b = monitor["top"] + monitor["height"]
        mon_bounds = (mon_l, mon_t, mon_r, mon_b)

        colors = [(0, 255, 0), (255, 165, 0), (0, 255, 255)]

        while True:
            screenshot = sct.grab(monitor)
            img = np.array(screenshot)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

            # Recorta 3 paineis separados - um por instancia
            panels = []
            for i in range(3):
                bx = i * delta_x
                by = i * delta_y
                crop = _crop_instance(
                    img, hand_regions, board_regions,
                    bx, by, colors[i], f"Mesa {i+1}",
                    mon_bounds
                )
                panels.append(crop)

            # Junta os 3 paineis lado a lado
            h_max = max(p.shape[0] for p in panels)
            for i, p in enumerate(panels):
                if p.shape[0] < h_max:
                    pad = np.zeros((h_max - p.shape[0], p.shape[1], 3), dtype=np.uint8)
                    pad[:] = (40, 40, 40)
                    panels[i] = np.vstack([p, pad])
            frame = np.hstack(panels)

            # Borda entre paineis
            sep = frame.shape[1] // 3
            cv2.line(frame, (sep, 0), (sep, frame.shape[0]), (100, 100, 100), 2)
            cv2.line(frame, (sep * 2, 0), (sep * 2, frame.shape[0]), (100, 100, 100), 2)

            # Info
            info = f"delta_x={delta_x} delta_y={delta_y} [A/D W/S] ESP=salvar ESC=sair"
            cv2.putText(frame, info, (10, frame.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            # Reduz se necessario
            h, w = frame.shape[:2]
            if w > 1400 or h > 700:
                scale = min(1400 / w, 700 / h)
                nw, nh = int(w * scale), int(h * scale)
                frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)

            cv2.imshow("Calibracao 3 instancias", frame)
            key = cv2.waitKey(100) & 0xFF

            if key == 27:  # ESC
                break
            elif key == ord(" "):
                # Salvar presets
                parser = configparser.ConfigParser()
                parser.read(CONFIG_PATH, encoding="utf-8")

                base = "Layout_emu_proxy"
                if "CurrentProfile" in parser:
                    base = parser["CurrentProfile"].get("selected", base).strip()
                if base not in parser:
                    base = "Layout_emu_proxy"
                base_sec = parser[base]

                opp_regions = []
                for i in range(1, 6):
                    k = f"opponent_seat{i}"
                    if k not in base_sec:
                        break
                    parts = [p.strip() for p in base_sec[k].split(",")]
                    if len(parts) != 4:
                        break
                    left, top, w, h = map(int, parts)
                    opp_regions.append({"left": left, "top": top, "width": w, "height": h})

                for i in range(3):
                    dx = i * delta_x
                    dy = i * delta_y
                    sec_name = f"Layout_instancia_{i+1}"
                    h_shift = _shift_regions(hand_regions, dx, dy)
                    b_shift = _shift_regions(board_regions, dx, dy)
                    o_shift = _shift_regions(opp_regions, dx, dy) if opp_regions else None
                    _write_layout_section(parser, sec_name, h_shift, b_shift, o_shift)

                if "InstanciasCalibracao" not in parser:
                    parser["InstanciasCalibracao"] = {}
                parser["InstanciasCalibracao"]["delta_x"] = str(delta_x)
                parser["InstanciasCalibracao"]["delta_y"] = str(delta_y)

                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    parser.write(f)
                print(f"[OK] Salvos Layout_instancia_1, 2, 3 em config.ini (delta_x={delta_x}, delta_y={delta_y})")
            elif key in (ord("a"), ord("A"), 81):  # A ou Left
                delta_x = max(0, delta_x - 20)
            elif key in (ord("d"), ord("D"), 83):  # D ou Right
                delta_x += 20
            elif key in (ord("w"), ord("W"), 82):  # W ou Up
                delta_y = max(-500, delta_y - 20)
            elif key in (ord("s"), ord("S"), 84):  # S ou Down
                delta_y += 20
            elif key == ord("q"):
                delta_x = max(0, delta_x - 5)
            elif key == ord("e"):
                delta_x += 5
            elif key == ord("r"):
                delta_y = max(-500, delta_y - 5)
            elif key == ord("f"):
                delta_y += 5

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
