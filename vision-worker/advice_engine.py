"""
PLO6 Advice Engine
==================
Motor de recomendação de ação baseado em regras hierárquicas (strategies.json).
Mantém fallback threshold-based para quando o JSON não estiver disponível.
"""

from __future__ import annotations

import json
import os
from typing import Any

# ── Análise de mão ────────────────────────────────────────────────────────────

_RANK_ORDER = "23456789TJQKA"


def analisar_flush_draw(hero_hand: list[str], board: list[str]) -> dict:
    """Retorna informações sobre flush draw do herói.

    Returns dict com chaves:
        has_flush_draw  – bool
        flush_draw_rank – "Nut" | "Rei" | "Dama" | "Medio" | "Baixo" | ""
        is_nut_flush_draw – bool
        draw_quality    – "NUT" | "STRONG" | "WEAK" | "NONE"
        target_suit     – char do naipe alvo ou ""
    """
    result = {
        "has_flush_draw": False,
        "flush_draw_rank": "",
        "is_nut_flush_draw": False,
        "draw_quality": "NONE",
        "target_suit": "",
    }
    if not board:
        return result

    hero_suits  = [c[-1] for c in hero_hand if len(c) >= 2]
    board_suits = [c[-1] for c in board      if len(c) >= 2]

    target_suit = None
    for suit in "shdc":
        hero_count  = hero_suits.count(suit)
        board_count = board_suits.count(suit)
        if hero_count >= 2 and hero_count + board_count >= 4:
            target_suit = suit
            break

    if target_suit is None:
        return result

    flush_cards = [c for c in hero_hand if len(c) >= 2 and c[-1] == target_suit]
    top_rank    = max((c[0] for c in flush_cards),
                      key=lambda r: _RANK_ORDER.index(r) if r in _RANK_ORDER else -1,
                      default="")

    if top_rank == "A":
        rank_str  = "Nut"
        is_nut    = True
        quality   = "NUT"
    elif top_rank == "K":
        rank_str  = "Rei"
        is_nut    = False
        quality   = "STRONG"
    elif top_rank == "Q":
        rank_str  = "Dama"
        is_nut    = False
        quality   = "STRONG"
    elif top_rank in ("J", "T"):
        rank_str  = "Medio"
        is_nut    = False
        quality   = "WEAK"
    else:
        rank_str  = "Baixo"
        is_nut    = False
        quality   = "WEAK"

    return {
        "has_flush_draw":     True,
        "flush_draw_rank":    rank_str,
        "is_nut_flush_draw":  is_nut,
        "draw_quality":       quality,
        "target_suit":        target_suit,
    }


def descrever_mao_preflop(hero_hand: list[str]) -> str:
    """Retorna descrição curta da mão preflop (ex: 'Par A, Double-S')."""
    ranks = [c[0] for c in hero_hand if c]
    suits = [c[-1] for c in hero_hand if len(c) >= 2]

    rank_counts: dict[str, int] = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1

    suit_counts: dict[str, int] = {}
    for s in suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1

    quads  = [r for r, c in rank_counts.items() if c >= 4]
    trips  = [r for r, c in rank_counts.items() if c >= 3]
    pairs  = [r for r, c in rank_counts.items() if c >= 2]
    is_qs  = any(c >= 4 for c in suit_counts.values())
    is_ds  = any(c >= 2 for c in suit_counts.values())

    desc = []
    if quads:
        desc.append(f"Quadra {quads[0]}")
    elif trips:
        desc.append(f"Trinca {trips[0]}")
    elif pairs:
        for p in sorted(pairs, key=lambda x: _RANK_ORDER.index(x) if x in _RANK_ORDER else -1, reverse=True):
            desc.append(f"Par {p}")

    if is_qs:
        desc.append("Quad-S")
    elif is_ds:
        desc.append("Double-S")

    if not desc:
        if any(r in "AKQ" for r in ranks):
            desc.append("Cartas Altas")
        else:
            desc.append("Mão Fraca")

    return ", ".join(desc)


# ── AdviceEngine ──────────────────────────────────────────────────────────────

class AdviceEngine:
    """Motor de conselhos baseado em regras JSON hierárquicas."""

    def __init__(self, strategies_path: str):
        self.strategies_path = strategies_path
        self.rules: list[dict] = []
        self._load()

    def _load(self):
        if not os.path.exists(self.strategies_path):
            return
        try:
            with open(self.strategies_path, encoding="utf-8") as f:
                data = json.load(f)
            self.rules = sorted(data, key=lambda r: r.get("priority", 0), reverse=True)
            print(f"[ADVICE] {len(self.rules)} regras carregadas")
        except Exception as exc:
            print(f"[ADVICE] Erro ao carregar regras: {exc}")
            self.rules = []

    def get_advice(self, ctx: dict[str, Any]) -> tuple[str, str]:
        """Retorna (mensagem, color_level) para o contexto dado."""
        for rule in self.rules:
            try:
                if self._match(rule.get("conditions", {}), ctx):
                    msg = rule["message"].format(**ctx)
                    return msg, rule.get("color_level", "medium")
            except (KeyError, ValueError):
                continue
        return self._fallback(ctx)

    def _match(self, conditions: dict, ctx: dict) -> bool:
        for key, expected in conditions.items():
            if key.endswith("_min"):
                if ctx.get(key[:-4], 0) < expected:
                    return False
            elif key.endswith("_max"):
                if ctx.get(key[:-4], 0) > expected:
                    return False
            elif isinstance(expected, list):
                if ctx.get(key) not in expected:
                    return False
            else:
                if ctx.get(key) != expected:
                    return False
        return True

    def _fallback(self, ctx: dict) -> tuple[str, str]:
        eq  = ctx.get("hero_equity", 0)
        mao = ctx.get("mao_feita", "")
        if eq >= 70:
            return f"{mao} forte ({eq}%)", "strong"
        if eq >= 45:
            return f"{mao} jogável ({eq}%)", "medium"
        return f"{mao} fraco ({eq}%)", "fold"


# ── Singleton & API pública ───────────────────────────────────────────────────

_engine: AdviceEngine | None = None


def _get_engine() -> AdviceEngine | None:
    global _engine
    if _engine is None:
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "strategies.json")
        _engine = AdviceEngine(path)
    return _engine


def build_advice_context(
    hero_hand:   list[str],
    board_cards: list[str],
    street:      str,
    hero_equity: float,
    n_opponents: int,
    hand_name:   str = "",
) -> dict[str, Any]:
    """Constrói contexto completo para o AdviceEngine."""
    total_players = n_opponents + 1
    fair_share    = round(100.0 / total_players, 2) if total_players > 0 else 33.33
    eq_int        = round(hero_equity * 100, 1)
    equity_ratio  = round(eq_int / fair_share, 2) if fair_share > 0 else 0

    fd = analisar_flush_draw(hero_hand, board_cards)

    board_ranks = [c[0] for c in board_cards if c]
    rank_counts: dict[str, int] = {}
    for r in board_ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1
    is_paired_board = len(board_cards) >= 3 and any(c >= 2 for c in rank_counts.values())

    if street == "Preflop":
        mao_feita = descrever_mao_preflop(hero_hand)
    else:
        mao_feita = hand_name or "?"

    return {
        "street":            street,
        "hero_equity":       eq_int,
        "fair_share":        fair_share,
        "equity_ratio":      equity_ratio,
        "total_players":     total_players,
        "mao_feita":         mao_feita,
        "is_nuts":           eq_int >= 99.9,
        "is_paired_board":   is_paired_board,
        **fd,
    }


def get_rich_advice(ctx: dict[str, Any]) -> dict:
    """Retorna dict com action, label, color_level, speak_text."""
    engine = _get_engine()
    if engine and engine.rules:
        label, color_level = engine.get_advice(ctx)
    else:
        label, color_level = _threshold_fallback(ctx)

    eq    = ctx.get("hero_equity", 0)
    ratio = ctx.get("equity_ratio", 0)
    if ratio >= 1.7 or ctx.get("is_nuts"):
        action = "RAISE"
    elif ratio >= 0.85:
        action = "CALL"
    else:
        action = "FOLD"

    speak = f"{eq} por cento. {action}. {label}."
    return {
        "action":      action,
        "label":       label,
        "color_level": color_level,
        "equity_pct":  eq,
        "speak_text":  speak,
    }


def _threshold_fallback(ctx: dict) -> tuple[str, str]:
    """Fallback quando não há regras carregadas."""
    eq     = ctx.get("hero_equity", 0)
    street = ctx.get("street", "Preflop")
    mao    = ctx.get("mao_feita", "")

    thresholds = {
        "Preflop": [(62, "strong", "Preflop premium"), (47, "medium", "Especulativa"),
                    (0,  "fold",   "Fraca — descarte")],
        "Flop":    [(65, "strong", "Muito forte"), (50, "strong", "Favorito"),
                    (38, "medium", "Continue com cautela"), (0, "fold", "Equidade insuficiente")],
        "Turn":    [(60, "strong", "Sólido — pressione"), (42, "medium", "Defenda barato"),
                    (0, "fold",  "Sem equidade")],
        "River":   [(55, "strong", "Extraia valor"), (45, "medium", "Call justificado"),
                    (0, "fold",  "Fold")],
    }
    for min_eq, cl, lbl in thresholds.get(street, thresholds["Flop"]):
        if eq >= min_eq:
            txt = f"{mao} — {lbl} ({eq}%)" if mao else f"{lbl} ({eq}%)"
            return txt, cl
    return f"Fold ({eq}%)", "fold"


# ── Compatibilidade (chamado por código legado) ───────────────────────────────

def get_advice(equity: float, street: str = "Preflop") -> dict:
    """API legada: aceita equity 0-1 e street, retorna dict padronizado."""
    ctx = {
        "hero_equity": round(equity * 100, 1),
        "equity_ratio": equity * 3,   # estimativa com 2 oponentes
        "street": street,
        "mao_feita": "",
        "is_nuts": equity >= 0.999,
        "has_flush_draw": False,
        "draw_quality": "NONE",
        "is_paired_board": False,
    }
    return get_rich_advice(ctx)
