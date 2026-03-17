import configparser
import glob
import json
import os
import threading
import time

import cv2
import mss
import numpy as np
import websocket

from equity_calc import calculate_equity
from advice_engine import get_advice


WEBSOCKET_URL      = "ws://localhost:3000"
TEMPLATE_THRESHOLD = 0.85
ANCHOR_THRESHOLD   = 0.80          # confiança mínima para aceitar a posição da âncora
LAYOUT_SECTION     = "Layout_2ancoras"
EQUITY_SIMULATIONS = 800
EQUITY_OPPONENTS   = 2

_STREET_MAP: dict[int, str] = {0: "Preflop", 3: "Flop", 4: "Turn", 5: "River"}

# ── Variáveis globais de offset (atualizadas pelo radar de âncora) ─────────────
GLOBAL_OFFSET_X: int = 0
GLOBAL_OFFSET_Y: int = 0

# ── Estado da âncora (carregado uma vez em on_open) ────────────────────────────
_anchor_img:    np.ndarray | None = None
_anchor_std_cx: int = 0
_anchor_std_cy: int = 0


# ── Equity cache (thread-safe) ────────────────────────────────────────────────

class _EquityCache:
    """Mantém o último resultado de equidade calculado em background."""

    def __init__(self) -> None:
        self._data: dict | None = None
        self._lock = threading.Lock()
        self._busy = False

    def get(self) -> dict | None:
        with self._lock:
            return dict(self._data) if self._data else None

    def launch(self, hand: list[str], board: list[str]) -> None:
        """Dispara cálculo em thread daemon; ignora se já estiver rodando."""
        with self._lock:
            if self._busy:
                return
            self._busy = True

        def _worker() -> None:
            try:
                result = calculate_equity(
                    hand[:], board[:],
                    n_opponents=EQUITY_OPPONENTS,
                    n_simulations=EQUITY_SIMULATIONS,
                )
                street = _STREET_MAP.get(len(board), "Preflop")
                advice = get_advice(result["equity"], street)
                with self._lock:
                    self._data = {**result, **advice}
            except Exception as exc:
                print(f"[EQUITY] Erro: {exc}")
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def clear(self) -> None:
        with self._lock:
            self._data = None


_equity_cache = _EquityCache()


# ── Config / regiões ──────────────────────────────────────────────────────────

def _get_base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _load_regions(prefix: str, count: int) -> list[dict]:
    base_dir    = _get_base_dir()
    config_path = os.path.join(base_dir, "config.ini")

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    if LAYOUT_SECTION not in parser:
        raise ValueError(f"Seção [{LAYOUT_SECTION}] não encontrada em config.ini")

    section = parser[LAYOUT_SECTION]
    regions: list[dict] = []

    for i in range(1, count + 1):
        key = f"{prefix}{i}"
        if key not in section:
            break
        raw   = section.get(key, "").strip()
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(f"Valor inválido para {key}: '{raw}'")
        left, top, width, height = map(int, parts)
        regions.append({"top": top, "left": left, "width": width, "height": height})

    return regions


def load_hand_regions()  -> list[dict]: return _load_regions("hand_card",  6)
def load_board_regions() -> list[dict]: return _load_regions("board_card", 5)


def load_anchor_config() -> tuple[np.ndarray | None, int, int]:
    """Lê anchor_template, anchor_std_cx e anchor_std_cy da seção atual do config.ini.

    Retorna (imagem_ancora_grayscale | None, std_cx, std_cy).
    """
    base_dir    = _get_base_dir()
    config_path = os.path.join(base_dir, "config.ini")

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    if LAYOUT_SECTION not in parser:
        print(f"[ANCHOR] Seção [{LAYOUT_SECTION}] não encontrada — tracking desabilitado.")
        return None, 0, 0

    section = parser[LAYOUT_SECTION]

    tpl_name = section.get("anchor_template", "").strip()
    std_cx   = int(float(section.get("anchor_std_cx", "0")))
    std_cy   = int(float(section.get("anchor_std_cy", "0")))

    if not tpl_name:
        print("[ANCHOR] Chave 'anchor_template' vazia — tracking desabilitado.")
        return None, std_cx, std_cy

    tpl_path = os.path.join(base_dir, tpl_name)
    img = cv2.imread(tpl_path, cv2.IMREAD_GRAYSCALE)

    if img is None:
        print(f"[ANCHOR] Template '{tpl_name}' não encontrado em {base_dir} — tracking desabilitado.")
        return None, std_cx, std_cy

    print(f"[ANCHOR] Âncora carregada: '{tpl_name}'  std=({std_cx}, {std_cy})")
    return img, std_cx, std_cy


# ── Radar de âncora ───────────────────────────────────────────────────────────

def update_offset(sct: mss.mss) -> None:
    """Captura o desktop inteiro, procura a âncora e atualiza GLOBAL_OFFSET_X/Y.

    Se a âncora não for encontrada com confiança ≥ ANCHOR_THRESHOLD, o offset
    anterior é mantido (fail-safe: não piora uma calibração anterior).
    """
    global GLOBAL_OFFSET_X, GLOBAL_OFFSET_Y

    if _anchor_img is None:
        return

    # Monitor 0 = virtual desktop completo (cobre todos os monitores)
    full_screen = sct.monitors[0]
    screenshot  = sct.grab(full_screen)
    gray        = cv2.cvtColor(np.array(screenshot), cv2.COLOR_BGRA2GRAY)

    result = cv2.matchTemplate(gray, _anchor_img, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= ANCHOR_THRESHOLD:
        found_x, found_y = max_loc
        new_ox = found_x - _anchor_std_cx
        new_oy = found_y - _anchor_std_cy

        if new_ox != GLOBAL_OFFSET_X or new_oy != GLOBAL_OFFSET_Y:
            print(f"[ANCHOR] Offset atualizado: ({GLOBAL_OFFSET_X}, {GLOBAL_OFFSET_Y})"
                  f" -> ({new_ox}, {new_oy})  confiança={max_val:.3f}")
            GLOBAL_OFFSET_X = new_ox
            GLOBAL_OFFSET_Y = new_oy
    else:
        print(f"[ANCHOR] Âncora não encontrada (max={max_val:.3f} < {ANCHOR_THRESHOLD}) — offset mantido.")


# ── Templates ─────────────────────────────────────────────────────────────────

def load_templates() -> dict[str, np.ndarray]:
    base_dir  = _get_base_dir()
    templates: dict[str, np.ndarray] = {}
    for path in glob.glob(os.path.join(base_dir, "*.png")):
        name = os.path.splitext(os.path.basename(path))[0]
        img  = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            templates[name] = img
    return templates


# ── Detecção ──────────────────────────────────────────────────────────────────

def _match_region(
    sct: mss.mss,
    region: dict,
    templates: dict[str, np.ndarray],
    preview_title: str | None = None,
) -> str | None:
    screenshot = sct.grab(region)
    gray       = cv2.cvtColor(np.array(screenshot), cv2.COLOR_BGRA2GRAY)

    best_name  = None
    best_score = -1.0

    for name, template in templates.items():
        if gray.shape[0] < template.shape[0] or gray.shape[1] < template.shape[1]:
            continue
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val > best_score:
            best_score = max_val
            best_name  = name

    if preview_title:
        cv2.imshow(preview_title, gray)

    return best_name if best_name and best_score >= TEMPLATE_THRESHOLD else None


def _apply_offset(region: dict) -> dict:
    """Retorna uma cópia da região com GLOBAL_OFFSET_X/Y somados a left/top."""
    return {
        "top":    region["top"]    + GLOBAL_OFFSET_Y,
        "left":   region["left"]   + GLOBAL_OFFSET_X,
        "width":  region["width"],
        "height": region["height"],
    }


def process_frame(
    sct: mss.mss,
    hand_regions: list[dict],
    board_regions: list[dict],
    templates: dict[str, np.ndarray],
) -> tuple[list[str], list[str]]:
    hand_cards: list[str] = []
    for idx, region in enumerate(hand_regions, start=1):
        card = _match_region(sct, _apply_offset(region), templates, f"Olho do Bot - Carta {idx}")
        if card:
            hand_cards.append(card)

    board_cards: list[str] = []
    for region in board_regions:
        card = _match_region(sct, _apply_offset(region), templates)
        if card:
            board_cards.append(card)

    cv2.waitKey(1)
    return hand_cards, board_cards


# ── WebSocket callbacks ───────────────────────────────────────────────────────

def on_open(ws: websocket.WebSocketApp) -> None:
    global _anchor_img, _anchor_std_cx, _anchor_std_cy

    hand_regions  = load_hand_regions()
    board_regions = load_board_regions()
    templates     = load_templates()
    _anchor_img, _anchor_std_cx, _anchor_std_cy = load_anchor_config()

    if not templates:
        print("[VISION] Nenhum template .png encontrado — template matching desabilitado.")

    prev_hand:  frozenset[str] = frozenset()
    prev_board: frozenset[str] = frozenset()

    with mss.mss() as sct:
        try:
            while True:
                # ── Recalibra o offset antes de ler as cartas ──────────────────
                update_offset(sct)

                hand_cards, board_cards = process_frame(
                    sct, hand_regions, board_regions, templates
                )

                # ── Dispara equidade quando cartas mudam e mão está completa ──
                hand_set  = frozenset(hand_cards)
                board_set = frozenset(board_cards)

                if len(hand_cards) == 6 and (hand_set != prev_hand or board_set != prev_board):
                    if hand_set != prev_hand:
                        _equity_cache.clear()   # nova mão → descarta resultado anterior
                    prev_hand  = hand_set
                    prev_board = board_set
                    _equity_cache.launch(hand_cards, board_cards)

                # ── Envia eventos ──
                ws.send(json.dumps({"event": "update_hands", "payload": hand_cards}))
                ws.send(json.dumps({"event": "update_board", "payload": board_cards}))

                equity = _equity_cache.get()
                if equity:
                    ws.send(json.dumps({"event": "equity_state", "payload": equity}))

                time.sleep(2)

        except KeyboardInterrupt:
            pass
        finally:
            cv2.destroyAllWindows()
            ws.close()


def main() -> None:
    ws_app = websocket.WebSocketApp(WEBSOCKET_URL, on_open=on_open)
    ws_app.run_forever()


if __name__ == "__main__":
    main()
