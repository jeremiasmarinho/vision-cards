"""
hud_app.py — HUD PLO6 Assistente de Acessibilidade
====================================================
Suporte a múltiplas mesas (2-3 telas/janelas simultâneas).

Para ativar múltiplas mesas:
  1. Execute calibra_instancias.py e pressione ESPAÇO
     → gera Layout_instancia_1/2/3 no config.ini automaticamente
  OU
  2. Adicione selected_2 / selected_3 em [CurrentProfile] do config.ini
     apontando para seções de layout diferentes
"""

import configparser
import glob
import json
import io
import os
import queue
import re
import sys
import threading
import tkinter as tk

# Força UTF-8 no terminal Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from tkinter import messagebox, ttk
from typing import Optional

import cv2
import mss
import numpy as np

from equity_calc  import calculate_equity, get_hand_name
from advice_engine import build_advice_context, get_rich_advice
from overlay      import DraggableOverlayWindow, JanelaCartasVivas, prompt_for_template

# ── Constantes ────────────────────────────────────────────────────────────────

MONITOR_INTERVAL_MS  = 400
QUEUE_POLL_MS        = 100
RANK_THRESHOLD       = 0.65
WHITE_THRESHOLD      = 240
RED_DOM              = 40
GREEN_DOM            = 25
BLUE_DOM             = 25
DEFAULT_SIMS         = 1000
DEFAULT_OPPONENTS    = 2

VALID_RANKS = frozenset({"A","K","Q","J","T","9","8","7","6","5","4","3","2"})
STREET_MAP  = {0: "Preflop", 3: "Flop", 4: "Turn", 5: "River"}

EQUITY_COLORS = {
    "nuts":   ("#4CAF50", "white"),
    "strong": ("#8BC34A", "white"),
    "medium": ("#FF9800", "black"),
    "weak":   ("#F44336", "white"),
    "fold":   ("#607D8B", "white"),
}

# Gap horizontal (pixels) entre conjuntos de overlays de mesas diferentes
TABLE_OVERLAY_GAP = 360


# ── Detecção de cartas ────────────────────────────────────────────────────────

def _load_templates(base_dir: str) -> dict[str, list[np.ndarray]]:
    templates: dict[str, list[np.ndarray]] = {}
    skipped = 0
    for path in sorted(glob.glob(os.path.join(base_dir, "*.png"))):
        base = os.path.splitext(os.path.basename(path))[0]
        if base.startswith(("tpl_", "template_")):
            skipped += 1
            continue
        rank = base.split("_")[0].upper()
        if rank not in VALID_RANKS:
            skipped += 1
            continue
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        templates.setdefault(rank, []).append(img)
    total = sum(len(v) for v in templates.values())
    print(f"[TEMPLATES] {len(templates)} ranks, {total} imagens ({skipped} ignorados)")
    return templates


def _suit_from_rgb(r: float, g: float, b: float) -> str:
    if r - max(g, b) > RED_DOM:   return "h"
    if g - max(r, b) > GREEN_DOM: return "c"
    if b - max(r, g) > BLUE_DOM:  return "d"
    return "s"


def _avg_rgb_nonwhite(bgra: np.ndarray) -> tuple[float, float, float]:
    h, w = bgra.shape[:2]
    if h < 4 or w < 4:
        return 0.0, 0.0, 0.0
    y0, y1 = int(h * 0.15), int(h * 0.85)
    x0, x1 = int(w * 0.15), int(w * 0.85)
    crop = bgra[y0:y1, x0:x1]
    rgb  = cv2.cvtColor(crop, cv2.COLOR_BGRA2RGB).astype(np.float32)
    r_ch, g_ch, b_ch = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]
    is_white = (r_ch > WHITE_THRESHOLD) & (g_ch > WHITE_THRESHOLD) & (b_ch > WHITE_THRESHOLD)
    is_dark  = (r_ch < 30) & (g_ch < 30) & (b_ch < 30)
    mask = ~(is_white | is_dark)
    n = int(mask.sum())
    if n == 0:
        return 0.0, 0.0, 0.0
    return float(r_ch[mask].mean()), float(g_ch[mask].mean()), float(b_ch[mask].mean())


def _rank_from_template(gray: np.ndarray,
                         templates: dict[str, list[np.ndarray]]) -> tuple[Optional[str], float]:
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    rh, rw = binary.shape[:2]
    best_rank, best_score = None, -1.0
    for rank, imgs in templates.items():
        for tpl in imgs:
            th, tw = tpl.shape[:2]
            if th > rh or tw > rw:
                scale = min(rh / th, rw / tw)
                tpl   = cv2.resize(tpl, (max(1, int(tw*scale)), max(1, int(th*scale))),
                                   interpolation=cv2.INTER_AREA)
                th, tw = tpl.shape[:2]
            if th > rh or tw > rw:
                continue
            res = cv2.matchTemplate(binary, tpl, cv2.TM_CCOEFF_NORMED)
            _, mv, _, _ = cv2.minMaxLoc(res)
            if mv > best_score:
                best_score = mv
                best_rank  = rank
    if best_rank and best_score >= RANK_THRESHOLD:
        return best_rank, best_score
    return None, best_score


def _remove_yellow_border(bgra: np.ndarray) -> np.ndarray:
    """Converte pixels amarelos (borda de carta selecionada) para branco."""
    r, g, b = bgra[:, :, 2], bgra[:, :, 1], bgra[:, :, 0]
    mask = (r > 60) & (g > 150) & (b < 60)
    out = bgra.copy()
    out[mask] = [255, 255, 255, 255]
    return out


def _is_slider_open(hand_regions: list[dict], sct: mss.mss) -> bool:
    """Detecta slider de aposta verificando se a região da 6ª carta está muito escura."""
    if len(hand_regions) < 6:
        return False
    raw  = sct.grab(hand_regions[5])
    data = np.array(raw)
    gray = data[:, :, :3].max(axis=2)
    dark  = int(np.sum(gray < 50))
    total = gray.size
    return total > 0 and dark / total > 0.70


def _detect_card(region: dict, templates: dict, sct: mss.mss,
                 offset_x: int = 0, offset_y: int = 0,
                 expand: int = 8) -> Optional[dict]:
    """Captura região expandida para tolerar desalinhamento de poucos pixels."""
    re_ = {
        "top":    region["top"]    + offset_y - expand,
        "left":   region["left"]   + offset_x - expand,
        "width":  region["width"]  + expand * 2,
        "height": region["height"] + expand * 2,
    }
    ro = {
        "top":    region["top"]    + offset_y,
        "left":   region["left"]   + offset_x,
        "width":  region["width"],
        "height": region["height"],
    }

    raw_e  = sct.grab(re_)
    bgra_e = _remove_yellow_border(np.array(raw_e))
    gray_e = cv2.cvtColor(bgra_e, cv2.COLOR_BGRA2GRAY)

    raw_o  = sct.grab(ro)
    bgra_o = np.array(raw_o)

    rank, score = _rank_from_template(gray_e, templates)
    rv, gv, bv  = _avg_rgb_nonwhite(bgra_o)
    suit = _suit_from_rgb(rv, gv, bv)

    if rank is None:
        return None

    return {"rank": rank, "suit": suit, "card_str": rank + suit, "score": score}


# ── Configuração ──────────────────────────────────────────────────────────────

def _load_regions_from_section(parser: configparser.ConfigParser,
                                section_name: str) -> tuple[list, list, list]:
    """Carrega (hand_regions, board_regions, opp_regions) de uma seção do config."""
    if section_name not in parser:
        return [], [], []
    sec = parser[section_name]

    def read_region(key: str) -> Optional[dict]:
        if key not in sec:
            return None
        parts = [p.strip() for p in sec[key].split(",")]
        if len(parts) != 4:
            return None
        left, top, w, h = map(int, parts)
        return {"left": left, "top": top, "width": w, "height": h}

    hand_regions  = [r for i in range(1, 7)
                     if (r := read_region(f"hand_card{i}")) is not None]
    board_regions = [r for i in range(1, 6)
                     if (r := read_region(f"board_card{i}")) is not None]
    opp_regions   = [r for i in range(1, 6)
                     if (r := read_region(f"opponent_seat{i}")) is not None]
    return hand_regions, board_regions, opp_regions


def _load_all_tables(config_path: str) -> list[tuple[list, list, list]]:
    """Retorna lista de (hand, board, opp) para cada mesa configurada.

    Ordem de prioridade:
      1. Layout_instancia_1/2/3  — gerados por calibra_instancias.py
      2. selected / selected_2 / selected_3 em [CurrentProfile]
      3. Fallback: único layout selecionado (comportamento anterior)
    """
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    tables = []

    # ── 1. Layout_instancia_N (gerados por calibra_instancias.py) ─────────────
    instancia_keys = sorted(
        s for s in parser.sections()
        if s.lower().startswith("layout_instancia_")
    )
    if instancia_keys:
        for key in instancia_keys:
            hand, board, opp = _load_regions_from_section(parser, key)
            if hand:
                tables.append((hand, board, opp))
                print(f"[CONFIG] Mesa {len(tables)}: [{key}] — "
                      f"{len(hand)} mão, {len(board)} board, {len(opp)} assentos")
        if tables:
            return tables

    # ── 2. selected / selected_2 / selected_3 em [CurrentProfile] ─────────────
    if "CurrentProfile" in parser:
        cp = parser["CurrentProfile"]
        for key in ("selected", "selected_2", "selected_3"):
            sec_name = cp.get(key, "").strip()
            if sec_name and sec_name in parser:
                hand, board, opp = _load_regions_from_section(parser, sec_name)
                if hand:
                    tables.append((hand, board, opp))
                    print(f"[CONFIG] Mesa {len(tables)}: [{sec_name}] — "
                          f"{len(hand)} mão, {len(board)} board")
    if tables:
        return tables

    # ── 3. Fallback: único layout (comportamento original) ─────────────────────
    selected = ""
    if "CurrentProfile" in parser:
        selected = parser["CurrentProfile"].get("selected", "").strip()
    if not selected or selected not in parser:
        selected = next(
            (s for s in parser.sections()
             if s.lower().startswith(("layout_", "liga_"))),
            None,
        )
    if not selected:
        raise ValueError("Nenhuma seção de layout válida no config.ini")

    hand, board, opp = _load_regions_from_section(parser, selected)
    print(f"[CONFIG] Mesa 1: [{selected}] — {len(hand)} mão, {len(board)} board")
    return [(hand, board, opp)]


def _load_positions(config_path: str) -> dict[str, str]:
    """Lê posições salvas das janelas overlay."""
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    if "OverlayPositions" not in parser:
        return {}
    return dict(parser["OverlayPositions"])


def _save_positions(config_path: str, positions: dict[str, str]) -> None:
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    parser["OverlayPositions"] = positions
    with open(config_path, "w", encoding="utf-8") as f:
        parser.write(f)


def _shift_geometry(pos: str, dx: int, dy: int = 0) -> str:
    """Desloca posição Tkinter '+X+Y' por (dx, dy).
    Suporta '+-X+Y' (formato do config.ini com coordenadas negativas).
    """
    normalized = pos.replace("+-", "-")
    m = re.match(r'([+-]?\d+)([+-]\d+)', normalized)
    if not m:
        return pos
    x = int(m.group(1)) + dx
    y = int(m.group(2)) + dy
    return f"+{x}+{y}"


# ── Detecção de vilões ────────────────────────────────────────────────────────

OPP_BRIGHT_THRESHOLD = 0.30   # >30% pixels brilhantes → assento ativo
OPP_BRIGHT_PIXEL     = 150    # valor mínimo para considerar pixel "brilhante"


def _count_active_opponents(opp_regions: list[dict], sct: mss.mss) -> int:
    """Conta assentos de vilões ativos por análise de brilho."""
    count = 0
    for region in opp_regions:
        raw  = sct.grab(region)
        data = np.array(raw)
        brightness = data[:, :, :3].max(axis=2)
        n_bright   = int(np.sum(brightness > OPP_BRIGHT_PIXEL))
        n_total    = brightness.size
        if n_total > 0 and n_bright / n_total >= OPP_BRIGHT_THRESHOLD:
            count += 1
    return count


# ── Estado por mesa ───────────────────────────────────────────────────────────

class TableMonitor:
    """Mantém estado completo (regiões, máquina de estados, equity, overlays) de uma mesa."""

    def __init__(self, table_id: int,
                 hand_regions: list, board_regions: list, opp_regions: list):
        self.table_id      = table_id
        self.hand_regions  = hand_regions
        self.board_regions = board_regions
        self.opp_regions   = opp_regions

        # Máquina de estados
        self.current_state:      str       = "IDLE"
        self._prev_hand:         frozenset = frozenset()
        self._prev_board:        frozenset = frozenset()
        self._prev_board_count:  int       = -1
        self._empty_hand_frames: int       = 0
        self._detected_opps:     int       = 0

        # Equity
        self.calc_queue:   queue.Queue                 = queue.Queue()
        self.calc_thread:  Optional[threading.Thread]  = None
        self._last_equity: Optional[dict]              = None

        # Overlays (atribuídos pelo App após criação)
        self.hand_overlay:     Optional[DraggableOverlayWindow] = None
        self.board_overlay:    Optional[DraggableOverlayWindow] = None
        self.strength_overlay: Optional[DraggableOverlayWindow] = None
        self.advice_overlay:   Optional[DraggableOverlayWindow] = None


# ── App principal ─────────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("HUD PLO6 – Acessibilidade")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.configure(bg="#f0f0f0")

        base_dir          = os.path.dirname(os.path.abspath(__file__))
        self._config_path = os.path.join(base_dir, "config.ini")

        self._capture_lock = threading.Lock()
        self.templates     = _load_templates(base_dir)

        # Carrega todas as mesas configuradas
        tables_data = _load_all_tables(self._config_path)
        self.tables: list[TableMonitor] = [
            TableMonitor(i + 1, hand, board, opp)
            for i, (hand, board, opp) in enumerate(tables_data)
        ]
        print(f"[CONFIG] {len(self.tables)} mesa(s) ativa(s)")

        # Parâmetros compartilhados
        self.is_monitoring: bool = False
        self._after_id           = None
        self._queue_after_id     = None
        self.sim_var  = tk.StringVar(value=str(DEFAULT_SIMS))
        self.opp_var  = tk.IntVar(value=DEFAULT_OPPONENTS)

        # Cartas vivas (agregadas de todas as mesas)
        self.cartas_vivas_selecionadas: set[str] = set()

        self._build_ui()
        self._build_overlays()
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ── UI de controle ────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {"padx": 4, "pady": 3}

        row1 = tk.Frame(self, bg="#f0f0f0"); row1.pack(fill=tk.X, **pad)
        self.btn_monitor = tk.Button(row1, text="▶ Iniciar", width=12,
                                     bg="#4CAF50", fg="white",
                                     command=self.toggle_monitoring)
        self.btn_monitor.pack(side=tk.LEFT, **pad)

        tk.Button(row1, text="Cartas Vivas", width=12,
                  command=self._toggle_vivas).pack(side=tk.LEFT, **pad)

        tk.Button(row1, text="Reset Mãos", width=10,
                  command=self._reset_all_hands).pack(side=tk.LEFT, **pad)

        row2 = tk.Frame(self, bg="#f0f0f0"); row2.pack(fill=tk.X, **pad)
        tk.Label(row2, text="Sims:", bg="#f0f0f0").pack(side=tk.LEFT)
        tk.Entry(row2, textvariable=self.sim_var, width=7).pack(side=tk.LEFT, **pad)
        tk.Label(row2, text="Vilões:", bg="#f0f0f0").pack(side=tk.LEFT)
        tk.Spinbox(row2, from_=1, to=9, textvariable=self.opp_var,
                   width=3).pack(side=tk.LEFT, **pad)

        n = len(self.tables)
        mesa_txt = f"({n} mesa{'s' if n > 1 else ''})"
        tk.Label(row2, text=mesa_txt, bg="#f0f0f0", fg="#555",
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=6)
        self.opp_detected_label = tk.Label(row2, text="(auto: ?)",
                                           bg="#f0f0f0", fg="#888",
                                           font=("Arial", 8))
        self.opp_detected_label.pack(side=tk.LEFT)

        self.status_label = tk.Label(self, text="Parado", bg="#f0f0f0",
                                     fg="#888", font=("Arial", 9))
        self.status_label.pack(fill=tk.X, padx=4)

    def _build_overlays(self):
        saved = _load_positions(self._config_path)

        for table in self.tables:
            tid = table.table_id
            gap = (tid - 1) * TABLE_OVERLAY_GAP

            def _make(key: str, default: str, _tid=tid, _gap=gap) -> DraggableOverlayWindow:
                # Mesa 1 usa chaves simples ("hand"), mesas 2+ usam sufixo ("hand_2")
                save_key = key if _tid == 1 else f"{key}_{_tid}"
                pos      = saved.get(save_key, _shift_geometry(default, _gap))
                ov       = DraggableOverlayWindow(self, overlay_type=key,
                                                  save_key=save_key)
                ov.geometry(pos)
                return ov

            table.hand_overlay     = _make("hand",     "+100+800")
            table.board_overlay    = _make("board",    "+100+750")
            table.strength_overlay = _make("strength", "+300+800")
            table.advice_overlay   = _make("advice",   "+300+750")

            table.hand_overlay.update_cards(
                [{"rank": "?", "suit": ""} for _ in range(6)])
            table.board_overlay.update_cards([])
            table.strength_overlay.update_text("-/-", "#607D8B", "white")
            table.advice_overlay.update_tip("...", "gray")

        self.janela_vivas = JanelaCartasVivas(self)
        self.janela_vivas.withdraw()

    # ── Monitoramento ─────────────────────────────────────────────────────────

    def toggle_monitoring(self):
        if self.is_monitoring:
            self._stop_monitoring()
        else:
            self._start_monitoring()

    def _start_monitoring(self):
        self.is_monitoring = True
        self.btn_monitor.config(text="⏹ Parar", bg="#F44336")
        self.status_label.config(text="Monitorando...", fg="#4CAF50")
        self._reset_all_hands()
        self.monitor_loop()
        self._queue_after_id = self.after(QUEUE_POLL_MS, self.process_calc_queue)

    def _stop_monitoring(self):
        self.is_monitoring = False
        if self._after_id:
            self.after_cancel(self._after_id)
        if self._queue_after_id:
            self.after_cancel(self._queue_after_id)
        self.btn_monitor.config(text="▶ Iniciar", bg="#4CAF50")
        self.status_label.config(text="Parado", fg="#888")

    def pause_monitoring(self):
        if self.is_monitoring and self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None

    def resume_monitoring(self):
        if self.is_monitoring and self._after_id is None:
            self._after_id = self.after(MONITOR_INTERVAL_MS, self.monitor_loop)

    # ── Loop principal ────────────────────────────────────────────────────────

    def monitor_loop(self):
        """Captura e processa todas as mesas em um único lock de tela."""
        all_known: set[str] = set()
        try:
            with self._capture_lock:
                with mss.mss() as sct:
                    for table in self.tables:
                        hand_cards, board_cards, opp_count, slider_open = \
                            self._detect_frame_for(table, sct)
                        if slider_open:
                            continue
                        self._update_opps(table, opp_count)
                        self._update_overlays_for(table, hand_cards, board_cards)
                        self._update_state_machine_for(table, hand_cards, board_cards)
                        all_known |= set(hand_cards) | set(board_cards)
        except Exception as exc:
            print(f"[LOOP] Erro: {exc}")

        # Atualiza cartas vivas com a união de todas as mesas
        if all_known:
            self.janela_vivas.atualizar_cartas_vivas(all_known)

        self._after_id = self.after(MONITOR_INTERVAL_MS, self.monitor_loop)

    def _detect_frame_for(self, table: TableMonitor,
                           sct: mss.mss) -> tuple[list, list, int, bool]:
        """Detecta cartas e assentos de uma mesa específica."""
        if _is_slider_open(table.hand_regions, sct):
            return [], [], 0, True

        hand_cards: list[str] = []
        for region in table.hand_regions:
            r = _detect_card(region, self.templates, sct)
            if r:
                hand_cards.append(r["card_str"])

        board_cards: list[str] = []
        for region in table.board_regions:
            r = _detect_card(region, self.templates, sct, expand=12)
            if r:
                board_cards.append(r["card_str"])

        opp_count = (_count_active_opponents(table.opp_regions, sct)
                     if table.opp_regions else 0)
        return hand_cards, board_cards, opp_count, False

    def _update_opps(self, table: TableMonitor, count: int):
        """Atualiza contagem de vilões da mesa e sincroniza o spinbox."""
        if count != table._detected_opps:
            table._detected_opps = count
            print(f"[Mesa {table.table_id}] Vilões detectados: {count}")

        # Monta label: uma mesa → "(auto: N)", múltiplas → "(auto: N1/N2/N3)"
        if len(self.tables) == 1:
            label = f"(auto: {count})" if count > 0 else "(auto: ?)"
        else:
            parts = [
                str(t._detected_opps) if t._detected_opps > 0 else "?"
                for t in self.tables
            ]
            label = f"(auto: {'/'.join(parts)})"
        self.opp_detected_label.config(text=label)

        # Sincroniza spinbox apenas pela mesa 1
        if table.table_id == 1 and count > 0 and abs(count - self.opp_var.get()) > 1:
            self.opp_var.set(count)

    def _update_overlays_for(self, table: TableMonitor,
                              hand_cards: list[str], board_cards: list[str]):
        """Atualiza overlays de mão e board de uma mesa."""
        hand_dicts = [{"rank": c[:-1], "suit": c[-1]} for c in hand_cards]
        while len(hand_dicts) < len(table.hand_regions):
            hand_dicts.append({"rank": "_", "suit": ""})
        table.hand_overlay.update_cards(hand_dicts)

        board_dicts = [{"rank": c[:-1], "suit": c[-1]} for c in board_cards]
        table.board_overlay.update_cards(board_dicts)

    def _update_state_machine_for(self, table: TableMonitor,
                                   hand_cards: list[str], board_cards: list[str]):
        """Executa máquina de estados para uma mesa específica."""
        hand_set = frozenset(hand_cards)
        board_set = frozenset(board_cards)
        board_n  = len(board_cards)

        # Debounce: acumula frames sem cartas antes de declarar fim de mão
        if table.current_state != "IDLE" and len(hand_cards) == 0:
            table._empty_hand_frames += 1
            if table._empty_hand_frames >= 5:  # 5 × 400ms = 2s
                print(f"[Mesa {table.table_id}] Fim de mão "
                      f"({table._empty_hand_frames} frames) → IDLE")
                table.current_state      = "IDLE"
                table._prev_hand         = frozenset()
                table._prev_board        = frozenset()
                table._empty_hand_frames = 0
                table._prev_board_count  = -1
                self.janela_vivas.limpar_selecoes()
                self.cartas_vivas_selecionadas.clear()
                table.strength_overlay.update_text("-/-", "#607D8B", "white")
                table.advice_overlay.update_tip("...", "gray")
            return
        else:
            table._empty_hand_frames = 0

        # Nova mão detectada
        if table.current_state == "IDLE" and len(hand_cards) >= 4:
            print(f"[Mesa {table.table_id}] Nova mão → PREFLOP")
            table.current_state = "PREFLOP"
            table._prev_hand    = hand_set
            table._prev_board   = frozenset()
            table._last_equity  = None
            self._trigger_equity_for(table, hand_cards, board_cards)

        # Re-deal (mão completamente diferente)
        elif (table.current_state != "IDLE"
              and len(hand_cards) >= 4
              and hand_set != table._prev_hand
              and len(table._prev_hand) > 0
              and len(hand_set & table._prev_hand) == 0):
            print(f"[Mesa {table.table_id}] Re-deal → PREFLOP")
            table.current_state = "PREFLOP"
            table._prev_hand    = hand_set
            table._prev_board   = frozenset()
            table._last_equity  = None
            self.janela_vivas.limpar_selecoes()
            self.cartas_vivas_selecionadas.clear()
            self._trigger_equity_for(table, hand_cards, board_cards)

        # Board mudou (novo street)
        elif board_n != table._prev_board_count and board_n in STREET_MAP:
            street     = STREET_MAP[board_n]
            prev_state = table.current_state
            table.current_state = street.upper() if street != "Preflop" else "PREFLOP"
            print(f"[Mesa {table.table_id}] {prev_state} → {table.current_state} ({street})")
            table._prev_board = board_set
            self._trigger_equity_for(table, hand_cards, board_cards)

        table._prev_hand        = hand_set
        table._prev_board       = board_set
        table._prev_board_count = board_n

    # ── Equity ────────────────────────────────────────────────────────────────

    def _trigger_equity_for(self, table: TableMonitor,
                             hand_cards: list[str], board_cards: list[str]):
        """Dispara cálculo de equity em thread daemon para uma mesa."""
        if len(hand_cards) < 2:
            return
        if table.calc_thread and table.calc_thread.is_alive():
            return

        try:
            n_sims = int(self.sim_var.get())
            if not (100 <= n_sims <= 100_000):
                n_sims = DEFAULT_SIMS
        except ValueError:
            n_sims = DEFAULT_SIMS

        n_opp = table._detected_opps if table._detected_opps > 0 else self.opp_var.get()
        n_opp = max(1, n_opp)

        table.strength_overlay.update_text("...", "#E0E0E0", "black")
        table.advice_overlay.update_tip("calculando...", "gray")

        table.calc_thread = threading.Thread(
            target=self._equity_worker,
            args=(table.calc_queue, hand_cards[:], board_cards[:], n_opp, n_sims),
            daemon=True,
        )
        table.calc_thread.start()

    def _equity_worker(self, result_queue: queue.Queue,
                       hand: list[str], board: list[str],
                       n_opp: int, n_sims: int):
        """Worker de equity (roda em thread daemon); deposita resultado na fila."""
        try:
            result    = calculate_equity(hand, board,
                                         n_opponents=n_opp, n_simulations=n_sims)
            street    = STREET_MAP.get(len(board), "Preflop")
            hand_name = get_hand_name(hand, board)
            ctx       = build_advice_context(
                hero_hand   = hand,
                board_cards = board,
                street      = street,
                hero_equity = result.get("equity", 0.0),
                n_opponents = n_opp,
                hand_name   = hand_name,
            )
            advice = get_rich_advice(ctx)
            result_queue.put({
                "success":   True,
                "equity":    result,
                "advice":    advice,
                "street":    street,
                "hand_name": hand_name,
                "ctx":       ctx,
            })
        except Exception as exc:
            print(f"[EQUITY] Erro: {exc}")
            result_queue.put({"success": False, "error": str(exc)})

    def process_calc_queue(self):
        """Drena a fila de equity de todas as mesas."""
        for table in self.tables:
            try:
                msg = table.calc_queue.get_nowait()
                if msg.get("success"):
                    self._handle_calc_success(table, msg)
            except queue.Empty:
                pass
        if self.is_monitoring:
            self._queue_after_id = self.after(QUEUE_POLL_MS, self.process_calc_queue)

    def _handle_calc_success(self, table: TableMonitor, msg: dict):
        """Atualiza overlays de força e conselho para a mesa."""
        table._last_equity = msg
        eq        = msg["equity"]
        advice    = msg["advice"]
        hand_name = msg.get("hand_name", "")

        pct    = advice.get("equity_pct", round(eq.get("equity", 0) * 100, 1))
        action = advice.get("action", "FOLD")
        label  = advice.get("label", "")
        speak  = advice.get("speak_text", f"{label} — {pct}%")
        cl     = advice.get("color_level", "medium")

        _cl_map = {"top": "nuts", "strong": "strong", "medium": "medium",
                   "weak": "weak", "fold": "fold"}
        color_key = _cl_map.get(cl, "medium")
        bg, fg    = EQUITY_COLORS[color_key]

        parts = [f"{pct}%"]
        if hand_name:
            parts.append(hand_name)
        parts.append(action)
        table.strength_overlay.update_text("  ".join(parts), bg, fg)

        advice_fg = {"nuts": "#2E7D32", "strong": "#558B2F", "medium": "#E65100",
                     "weak": "#B71C1C", "fold": "#546E7A"}.get(color_key, "black")
        table.advice_overlay.update_tip(speak, advice_fg)

        print(f"[Mesa {table.table_id}] {pct}% | "
              f"{hand_name or msg.get('street','')} | {action} | {label}")

    # ── Cartas vivas ──────────────────────────────────────────────────────────

    def _toggle_vivas(self):
        if self.janela_vivas.winfo_viewable():
            self.janela_vivas.withdraw()
        else:
            self.janela_vivas.deiconify()

    def on_carta_viva_clicada(self, card_str: str):
        if card_str in self.janela_vivas.cartas_selecionadas:
            self.cartas_vivas_selecionadas.add(card_str)
        else:
            self.cartas_vivas_selecionadas.discard(card_str)
        print(f"[VIVAS] Selecionadas: {self.cartas_vivas_selecionadas}")

    # ── Utilitários ───────────────────────────────────────────────────────────

    def _reset_all_hands(self):
        """Reseta estado de todas as mesas."""
        for table in self.tables:
            table.current_state      = "IDLE"
            table._prev_hand         = frozenset()
            table._prev_board        = frozenset()
            table._prev_board_count  = -1
            table._empty_hand_frames = 0
            table._last_equity       = None
            table._detected_opps     = 0
            table.hand_overlay.update_cards(
                [{"rank": "?", "suit": ""} for _ in range(6)])
            table.board_overlay.update_cards([])
            table.strength_overlay.update_text("-/-", "#607D8B", "white")
            table.advice_overlay.update_tip("...", "gray")
        self.cartas_vivas_selecionadas.clear()
        self.janela_vivas.limpar_selecoes()

    def handle_card_click(self, event, card_info: dict):
        """Clique direito numa carta → corrigir template."""
        def save_fn(img, rank):
            out_dir  = os.path.dirname(os.path.abspath(__file__))
            existing = glob.glob(os.path.join(out_dir, f"{rank}_*.png"))
            idx      = len(existing) + 1
            path     = os.path.join(out_dir, f"{rank}_{idx}.png")
            img.save(path)
            print(f"[TEMPLATE] Salvo: {path}")
            self.templates = _load_templates(out_dir)
        prompt_for_template(self, card_info, save_fn)

    def save_overlay_position(self, save_key: str, x: int, y: int):
        """Persiste posição de um overlay no config.ini (identificado por save_key)."""
        positions = _load_positions(self._config_path)
        positions[save_key] = f"+{x}+{y}"
        try:
            _save_positions(self._config_path, positions)
        except Exception as exc:
            print(f"[POS] Erro ao salvar: {exc}")

    def _on_closing(self):
        self._stop_monitoring()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
