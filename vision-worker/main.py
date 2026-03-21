"""
vision-worker/main.py  —  Versão de produção  (arquitetura corrigida por engenharia reversa)
==============================================================================================

CIFRA ORIGINAL DESCOBERTA:
  - Template names: {RANK}_{variant_id}.png  →  prefixo antes de '_' é o rank
  - Suit:  determinado por análise de COR dos pixels não-brancos da carta
      dr = r - max(g, b) > 40  → 'h'  (Vermelho  = Copas    ♥)
      dg = g - max(r, b) > 25  → 'c'  (Verde     = Paus     ♣)
      db = b - max(r, g) > 25  → 'd'  (Azul      = Ouros    ♦)
      else                     → 's'  (Escuro    = Espadas  ♠)
  - Threshold de rank matching: 0.65 (interno ao _rank_from_template original)
  - Threshold de match geral:   0.85 (MATCH_THRESHOLD configurável)
"""

import argparse
import configparser
import glob
import json
import os
import sys
import threading
import time

import cv2
import mss
import numpy as np
import websocket

from equity_calc import calculate_equity
from advice_engine import get_advice


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO GLOBAL
# ══════════════════════════════════════════════════════════════════════════════

WEBSOCKET_URL      = "ws://localhost:3000/ws"
RANK_MATCH_THRESHOLD = 0.65   # limiar para aceitar rank (extraído do bytecode original)
MATCH_THRESHOLD      = 0.85   # limiar geral de detecção de carta (ajustável)
ANCHOR_THRESHOLD     = 0.80
SHOW_DEBUG_WINDOWS   = False
EQUITY_SIMULATIONS   = 800
EQUITY_OPPONENTS     = 2

# ── ID do jogador local (definido via --player na CLI) ────────────────────────
PLAYER_ID: str = "1"

# ── Estado compartilhado recebido do cerebro-central ─────────────────────────
# Lista consolidada de todas as cartas conhecidas na sala (mãos dos 3 jogadores + board).
# Atualizada via evento "shared_state" enviado pelo servidor.
_known_dead_cards: list[str] = []
_dead_cards_lock   = threading.Lock()

_STREET_MAP: dict[int, str] = {0: "Preflop", 3: "Flop", 4: "Turn", 5: "River"}

# Pixels acima deste valor em todos os canais RGB são considerados "brancos" (ignorados)
_WHITE_THRESHOLD = 240

# Limiares da dominância de canal para determinar naipe (extraídos de _map_color_to_suit)
_RED_DOM_THRESHOLD   = 40    # dr > 40 → Copas   'h'
_GREEN_DOM_THRESHOLD = 25    # dg > 25 → Paus    'c'
_BLUE_DOM_THRESHOLD  = 25    # db > 25 → Ouros   'd'


# ══════════════════════════════════════════════════════════════════════════════
# ESTADO GLOBAL DE OFFSET (anchor tracking)
# ══════════════════════════════════════════════════════════════════════════════

GLOBAL_OFFSET_X: int = 0
GLOBAL_OFFSET_Y: int = 0

_anchor_img:    np.ndarray | None = None
_anchor_std_cx: int = 0
_anchor_std_cy: int = 0


# ══════════════════════════════════════════════════════════════════════════════
# EQUITY CACHE (thread-safe)
# ══════════════════════════════════════════════════════════════════════════════

class _EquityCache:
    def __init__(self) -> None:
        self._data: dict | None = None
        self._lock  = threading.Lock()
        self._busy  = False

    def get(self) -> dict | None:
        with self._lock:
            return dict(self._data) if self._data else None

    def launch(self, hand: list[str], board: list[str]) -> None:
        with self._lock:
            if self._busy:
                return
            self._busy = True

        # Captura snapshot das dead cards no momento do lançamento (thread-safe)
        with _dead_cards_lock:
            dead_snapshot = list(_known_dead_cards)

        def _worker() -> None:
            try:
                result = calculate_equity(
                    hand[:], board[:],
                    n_opponents=EQUITY_OPPONENTS,
                    n_simulations=EQUITY_SIMULATIONS,
                    known_dead_cards=dead_snapshot,
                )
                street = _STREET_MAP.get(len(board), "Preflop")
                advice = get_advice(result["equity"], street)
                with self._lock:
                    self._data = {**result, **advice}
            except Exception as exc:
                print(f"[EQUITY] Erro: {exc}", flush=True)
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=_worker, daemon=True).start()

    def clear(self) -> None:
        with self._lock:
            self._data = None


_equity_cache = _EquityCache()


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG / REGIÕES
# ══════════════════════════════════════════════════════════════════════════════

def _get_base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_layout_section(parser: configparser.ConfigParser) -> str:
    """Resolve a seção de layout ativa via [CurrentProfile].selected.

    Fallback automático para a primeira seção que comece com 'Layout_' ou 'liga_'
    caso a seção apontada não exista no arquivo.
    """
    selected = ""
    if "CurrentProfile" in parser:
        selected = parser["CurrentProfile"].get("selected", "").strip()

    if selected and selected in parser:
        return selected

    # Busca fallback
    fallback = next(
        (s for s in parser.sections()
         if s.lower().startswith("layout_") or s.lower().startswith("liga_")),
        None,
    )

    if not fallback:
        raise ValueError(
            "[CONFIG] Nenhuma seção de layout válida (Layout_* / liga_*) "
            "encontrada no config.ini."
        )

    if selected:
        print(
            f"[ALERTA] Layout '{selected}' não encontrado no config.ini. "
            f"Usando fallback automático: [{fallback}]",
            flush=True,
        )
    else:
        print(
            f"[ALERTA] [CurrentProfile].selected não definido. "
            f"Usando fallback automático: [{fallback}]",
            flush=True,
        )

    return fallback


def _load_regions(prefix: str, count: int) -> list[dict]:
    base_dir    = _get_base_dir()
    config_path = os.path.join(base_dir, "config.ini")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"[CONFIG] config.ini não encontrado em: {base_dir}")

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    layout_section = _resolve_layout_section(parser)
    section        = parser[layout_section]
    regions: list[dict] = []

    for i in range(1, count + 1):
        key = f"{prefix}{i}"
        if key not in section:
            break
        raw   = section.get(key, "").strip()
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(f"[CONFIG] Valor inválido para '{key}': '{raw}'")
        left, top, width, height = map(int, parts)
        regions.append({"top": top, "left": left, "width": width, "height": height})
        print(f"[CONFIG] {key} => left={left} top={top} w={width} h={height}", flush=True)

    return regions


def load_hand_regions()  -> list[dict]: return _load_regions("hand_card",  6)
def load_board_regions() -> list[dict]: return _load_regions("board_card", 5)


# ══════════════════════════════════════════════════════════════════════════════
# ÂNCORA
# ══════════════════════════════════════════════════════════════════════════════

def load_anchor_config() -> tuple[np.ndarray | None, int, int]:
    base_dir    = _get_base_dir()
    config_path = os.path.join(base_dir, "config.ini")
    parser      = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    try:
        layout_section = _resolve_layout_section(parser)
    except ValueError as exc:
        print(f"[ANCHOR] {exc} — tracking desabilitado.", flush=True)
        return None, 0, 0

    section = parser[layout_section]
    tpl_name = section.get("anchor_template", "").strip()
    std_cx   = int(float(section.get("anchor_std_cx", "0")))
    std_cy   = int(float(section.get("anchor_std_cy", "0")))

    if not tpl_name:
        print("[ANCHOR] anchor_template vazia — tracking desabilitado.", flush=True)
        return None, std_cx, std_cy

    tpl_path = os.path.join(base_dir, tpl_name)
    img = cv2.imread(tpl_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        print(f"[ANCHOR] '{tpl_name}' não encontrado — tracking desabilitado.", flush=True)
        return None, std_cx, std_cy

    print(f"[ANCHOR] Âncora: '{tpl_name}'  std=({std_cx}, {std_cy})", flush=True)
    return img, std_cx, std_cy


def update_offset(sct: mss.mss) -> None:
    global GLOBAL_OFFSET_X, GLOBAL_OFFSET_Y
    if _anchor_img is None:
        return
    screenshot = sct.grab(sct.monitors[0])
    gray       = cv2.cvtColor(np.array(screenshot), cv2.COLOR_BGRA2GRAY)
    result     = cv2.matchTemplate(gray, _anchor_img, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val >= ANCHOR_THRESHOLD:
        new_ox = max_loc[0] - _anchor_std_cx
        new_oy = max_loc[1] - _anchor_std_cy
        if new_ox != GLOBAL_OFFSET_X or new_oy != GLOBAL_OFFSET_Y:
            print(f"[ANCHOR] Offset ({GLOBAL_OFFSET_X},{GLOBAL_OFFSET_Y})->"
                  f"({new_ox},{new_oy}) conf={max_val:.3f}", flush=True)
            GLOBAL_OFFSET_X = new_ox
            GLOBAL_OFFSET_Y = new_oy
    else:
        print(f"[ANCHOR] Não encontrada (conf={max_val:.3f}) — offset mantido.", flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATES DE RANK
#
# Arquitetura original (extraída por engenharia reversa):
#   - load_templates() agrupa por RANK: {'A': [img1, img2, ...], 'K': [...], ...}
#   - Regra de nome: split('_')[0].upper()  →  'A_3.png' → rank 'A'
#   - Exclui arquivos que começam com 'template_' ou 'tpl_'
# ══════════════════════════════════════════════════════════════════════════════

# Ranks válidos (único conjunto aceito — evita lixo de outros PNGs)
_VALID_RANKS = frozenset({'A', 'K', 'Q', 'J', 'T', '9', '8', '7', '6', '5', '4', '3', '2'})


def load_templates() -> dict[str, list[np.ndarray]]:
    """Carrega templates agrupados por rank char.

    Retorna: {'A': [img, img, ...], 'K': [...], ...}
    """
    base_dir   = _get_base_dir()
    templates: dict[str, list[np.ndarray]] = {}
    skipped    = 0

    for path in sorted(glob.glob(os.path.join(base_dir, "*.png"))):
        basename = os.path.splitext(os.path.basename(path))[0]

        # Excluir âncoras e templates de UI
        if basename.startswith("tpl_") or basename.startswith("template_"):
            skipped += 1
            continue

        # Extrair rank: parte antes de '_', maiúscula
        rank = basename.split("_")[0].upper()
        if rank not in _VALID_RANKS:
            skipped += 1
            continue

        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue

        templates.setdefault(rank, []).append(img)

    if len(templates) == 0:
        print("[ERRO FATAL] Nenhum template PNG encontrado em vision-worker/.", flush=True)
        sys.exit(1)

    total_imgs  = sum(len(v) for v in templates.values())
    total_ranks = len(templates)
    print(f"[TEMPLATES] {total_ranks} ranks carregados, {total_imgs} imagens no total "
          f"({skipped} arquivos ignorados)", flush=True)

    for rank, imgs in sorted(templates.items()):
        print(f"  rank '{rank}': {len(imgs)} template(s)", flush=True)

    return templates


# ══════════════════════════════════════════════════════════════════════════════
# DETECÇÃO DE NAIPE POR COR  (_map_color_to_suit original, reconstruída)
# ══════════════════════════════════════════════════════════════════════════════

def _map_color_to_suit(r: float, g: float, b: float) -> str:
    """Determina o naipe pela dominância de canal de cor.

    Limiares extraídos do bytecode original (consts = [40, 'h', 25, 'c', 'd', 's']):
      dr = r - max(g, b) > 40 → 'h'  (Vermelho  → Copas    ♥)
      dg = g - max(r, b) > 25 → 'c'  (Verde     → Paus     ♣)
      db = b - max(r, g) > 25 → 'd'  (Azul      → Ouros    ♦)
      else                    → 's'  (Escuro    → Espadas  ♠)
    """
    dr = r - max(g, b)
    dg = g - max(r, b)
    db = b - max(r, g)
    if dr > _RED_DOM_THRESHOLD:   return 'h'
    if dg > _GREEN_DOM_THRESHOLD: return 'c'
    if db > _BLUE_DOM_THRESHOLD:  return 'd'
    return 's'


def _average_rgb_nonwhite(bgra_img: np.ndarray) -> tuple[float, float, float]:
    """Calcula a média RGB dos pixels relevantes na região central da carta.

    Filtros aplicados (fundo e bordas excluídos):
      - Recorta 15%–85% da imagem (remove bordas)
      - Ignora pixels muito claros  (todos canais > 240) — fundo branco da carta
      - Ignora pixels muito escuros (todos canais <  30) — bordas pretas / sombra
    """
    h, w = bgra_img.shape[:2]
    if h < 4 or w < 4:
        return 0.0, 0.0, 0.0

    y0, y1 = int(h * 0.15), int(h * 0.85)
    x0, x1 = int(w * 0.15), int(w * 0.85)
    crop = bgra_img[y0:y1, x0:x1]

    # Converte BGRA → RGB
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGRA2RGB).astype(np.float32)
    r_ch, g_ch, b_ch = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]

    # Máscara: exclui branco (fundo) E preto (bordas)
    is_white = (r_ch > _WHITE_THRESHOLD) & (g_ch > _WHITE_THRESHOLD) & (b_ch > _WHITE_THRESHOLD)
    is_dark  = (r_ch < 30) & (g_ch < 30) & (b_ch < 30)
    mask     = ~(is_white | is_dark)

    n = int(mask.sum())
    if n == 0:
        return 0.0, 0.0, 0.0

    return float(r_ch[mask].mean()), float(g_ch[mask].mean()), float(b_ch[mask].mean())


# ══════════════════════════════════════════════════════════════════════════════
# DETECÇÃO DE RANK POR TEMPLATE MATCHING  (_rank_from_template original)
# ══════════════════════════════════════════════════════════════════════════════

def _rank_from_template(
    gray_region: np.ndarray,
    templates:   dict[str, list[np.ndarray]],
) -> tuple[str | None, float]:
    """Compara a região binarizada contra todos os templates de rank.

    Lógica extraída de _rank_from_template original:
      - Binariza com threshold 150/255
      - Redimensiona template para caber na região (se necessário)
      - Retorna (best_rank, best_score) ou (None, 0.0)
      - Threshold interno original: 0.65
    """
    # Binarização como no original (threshold=150, THRESH_BINARY)
    _, binary = cv2.threshold(gray_region, 150, 255, cv2.THRESH_BINARY)
    rh, rw    = binary.shape[:2]

    best_rank  = None
    best_score = -1.0

    for rank, img_list in templates.items():
        for tpl in img_list:
            th, tw = tpl.shape[:2]

            # Redimensiona se necessário (proteção de dimensão)
            if th > rh or tw > rw:
                scale = min(rh / th, rw / tw)
                tpl   = cv2.resize(tpl, (max(1, int(tw*scale)), max(1, int(th*scale))),
                                   interpolation=cv2.INTER_AREA)
                th, tw = tpl.shape[:2]

            if th > rh or tw > rw:
                continue

            res = cv2.matchTemplate(binary, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score = max_val
                best_rank  = rank

    if best_rank and best_score >= RANK_MATCH_THRESHOLD:
        return best_rank, best_score
    return None, best_score


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE COMPLETO: CAPTURA → RANK + NAIPE → CÓDIGO DE CARTA
# ══════════════════════════════════════════════════════════════════════════════

def _apply_offset(region: dict) -> dict:
    return {
        "top":    region["top"]    + GLOBAL_OFFSET_Y,
        "left":   region["left"]   + GLOBAL_OFFSET_X,
        "width":  region["width"],
        "height": region["height"],
    }


def _detect_card(
    sct:          mss.mss,
    region:       dict,
    templates:    dict[str, list[np.ndarray]],
    label:        str  = "?",
    show_preview: bool = True,
) -> tuple[str | None, str, float, str, float, float, float]:
    """Captura uma região e executa os dois estágios de detecção.

    Retorna:
        (card_code | None, rank, rank_score, suit, dr, dg, db)
    """
    screenshot = sct.grab(region)
    bgra       = np.array(screenshot)
    gray       = cv2.cvtColor(bgra, cv2.COLOR_BGRA2GRAY)

    if np.sum(gray) == 0:
        print(f"[ERRO] Captura preta detetada em '{label}'. "
              "Verifica o motor gráfico do emulador ou o posicionamento da janela.", flush=True)

    if SHOW_DEBUG_WINDOWS and show_preview:
        reg_h, reg_w = gray.shape[:2]
        scale    = max(1, min(8, 200 // max(reg_h, reg_w, 1)))
        enlarged = cv2.resize(gray, (reg_w * scale, reg_h * scale),
                              interpolation=cv2.INTER_NEAREST)
        cv2.imshow(f"Carta {label}", enlarged)

    # ── Estágio 1: Rank por template matching ────────────────────────────────
    rank, rank_score = _rank_from_template(gray, templates)

    # ── Estágio 2: Naipe por análise RGB da imagem colorida original ─────────
    r, g, b = _average_rgb_nonwhite(bgra)           # BGR capturado como BGRA
    dr = r - max(g, b)
    dg = g - max(r, b)
    db = b - max(r, g)
    suit = _map_color_to_suit(r, g, b)

    if rank is None:
        return None, "?", rank_score, suit, dr, dg, db

    return rank + suit, rank, rank_score, suit, dr, dg, db


def process_frame(
    sct:           mss.mss,
    hand_regions:  list[dict],
    board_regions: list[dict],
    templates:     dict[str, list[np.ndarray]],
) -> tuple[list[str], list[str]]:
    """Detecta cartas na mão e no board e retorna listas de códigos de carta."""
    hand_cards: list[str] = []

    for idx, region in enumerate(hand_regions, start=1):
        card_code, rank, score, suit, dr, dg, db = _detect_card(
            sct, _apply_offset(region), templates,
            label=str(idx), show_preview=True,
        )

        # Decide qual delta é dominante para o log
        dom_label = (f"dr={dr:.0f}" if dr > dg and dr > db else
                     f"dg={dg:.0f}" if dg > db else f"db={db:.0f}")

        if card_code:
            hand_cards.append(card_code)
            print(f"[MÃO] Região {idx}: Rank '{rank}' (match {score:.2f})"
                  f" | Cor {dom_label} -> '{card_code}'", flush=True)
        else:
            print(f"[MÃO] Região {idx}: Rank '?' (match {score:.2f} < {RANK_MATCH_THRESHOLD})"
                  f" | Cor {dom_label} -> '{suit}' (descartado)", flush=True)

    board_cards: list[str] = []

    for idx, region in enumerate(board_regions, start=1):
        card_code, rank, score, suit, dr, dg, db = _detect_card(
            sct, _apply_offset(region), templates,
            label=f"B{idx}", show_preview=False,
        )
        dom_label = (f"dr={dr:.0f}" if dr > dg and dr > db else
                     f"dg={dg:.0f}" if dg > db else f"db={db:.0f}")
        if card_code:
            board_cards.append(card_code)
            print(f"[BOARD] Região {idx}: Rank '{rank}' (match {score:.2f})"
                  f" | Cor {dom_label} -> '{card_code}'", flush=True)

    if SHOW_DEBUG_WINDOWS:
        cv2.waitKey(1)
    return hand_cards, board_cards


# ══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET
# ══════════════════════════════════════════════════════════════════════════════

def _detection_loop(
    ws:            websocket.WebSocketApp,
    hand_regions:  list[dict],
    board_regions: list[dict],
    templates:     dict[str, list[np.ndarray]],
) -> None:
    """Loop principal de captura/detecção.  Roda em thread daemon separada para
    que o thread principal do websocket-client possa processar on_message()."""
    prev_hand:  frozenset[str] = frozenset()
    prev_board: frozenset[str] = frozenset()

    with mss.mss() as sct:
        try:
            while True:
                print("\n" + "-" * 60, flush=True)
                update_offset(sct)

                hand_cards, board_cards = process_frame(
                    sct, hand_regions, board_regions, templates
                )

                print(f"[DETECT] Mão={hand_cards}  Board={board_cards}", flush=True)

                hand_set  = frozenset(hand_cards)
                board_set = frozenset(board_cards)

                if len(hand_cards) == 6 and (hand_set != prev_hand or board_set != prev_board):
                    if hand_set != prev_hand:
                        _equity_cache.clear()
                    prev_hand  = hand_set
                    prev_board = board_set
                    _equity_cache.launch(hand_cards, board_cards)

                # Inclui player_id no payload de mão para o servidor consolidar
                ws.send(json.dumps({
                    "event":     "update_hands",
                    "player_id": PLAYER_ID,
                    "payload":   hand_cards,
                }))
                ws.send(json.dumps({"event": "update_board", "payload": board_cards}))

                equity = _equity_cache.get()
                if equity:
                    ws.send(json.dumps({"event": "equity_state", "payload": equity}))

                time.sleep(2)

        except KeyboardInterrupt:
            print("\n[WS] Encerrando.", flush=True)
        except Exception as exc:
            print(f"[DETECT] Erro no loop: {exc}", flush=True)
        finally:
            cv2.destroyAllWindows()
            ws.close()


def on_open(ws: websocket.WebSocketApp) -> None:
    global _anchor_img, _anchor_std_cx, _anchor_std_cy

    print(f"[WS] Conectado ao cerebro-central. (Jogador {PLAYER_ID})", flush=True)

    try:
        hand_regions  = load_hand_regions()
        board_regions = load_board_regions()
    except (FileNotFoundError, ValueError) as exc:
        print(exc, flush=True)
        ws.close()
        return

    templates = load_templates()
    if not templates:
        print("[TEMPLATES] ERRO CRÍTICO: nenhum template carregado! "
              f"Verifique os PNGs em {_get_base_dir()}", flush=True)

    _anchor_img, _anchor_std_cx, _anchor_std_cy = load_anchor_config()

    # Inicia detecção em thread daemon → libera este thread para processar on_message()
    threading.Thread(
        target=_detection_loop,
        args=(ws, hand_regions, board_regions, templates),
        daemon=True,
        name=f"detection-player{PLAYER_ID}",
    ).start()


def on_message(ws: websocket.WebSocketApp, message: str) -> None:
    """Recebe eventos do servidor — em especial 'shared_state' com dead cards consolidadas."""
    global _known_dead_cards
    try:
        data = json.loads(message)
        event = data.get("event")

        if event == "shared_state":
            dead = data.get("dead_cards", [])
            with _dead_cards_lock:
                _known_dead_cards = list(dead)
            print(f"[SHARED] Dead cards recebidas ({len(dead)}): {dead}", flush=True)

    except (json.JSONDecodeError, KeyError) as exc:
        print(f"[WS] Mensagem inválida ignorada: {exc}", flush=True)


def on_error(ws: websocket.WebSocketApp, error: Exception) -> None:
    print(f"[WS] Erro: {error}", flush=True)


def on_close(ws: websocket.WebSocketApp, code: int, msg: str) -> None:
    print(f"[WS] Conexão encerrada (code={code}).", flush=True)


def main() -> None:
    global PLAYER_ID

    parser = argparse.ArgumentParser(
        description="Vision Worker — PLO6 Overlay de Transmissão",
    )
    parser.add_argument(
        "--player",
        default="1",
        metavar="ID",
        help="ID deste jogador na sala (1=streamer, 2-3=convidados). Padrão: 1",
    )
    args = parser.parse_args()
    PLAYER_ID = args.player

    print(f"[INIT] Vision-Worker iniciado como Jogador {PLAYER_ID}", flush=True)
    print(f"[INIT] Conectando a {WEBSOCKET_URL} ...", flush=True)

    ws_app = websocket.WebSocketApp(
        WEBSOCKET_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws_app.run_forever(reconnect=5)


if __name__ == "__main__":
    main()
