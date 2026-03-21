"""
PLO6 Monte Carlo Equity Calculator
===================================
Regras PLO (Pot-Limit Omaha):
  - Cada jogador recebe 6 cartas na mão.
  - É OBRIGATÓRIO usar exatamente 2 cartas da mão + exatamente 3 do board.
  - Melhor mão de 5 cartas vence.

Codificação de cartas:
  - Inteiro 0-51: rank_idx * 4 + suit_idx
  - Ranks: 0=2, 1=3, ..., 8=T, 9=J, 10=Q, 11=K, 12=A
  - Naipes:  0=c, 1=d, 2=h, 3=s
"""

from __future__ import annotations

from itertools import combinations

import numpy as np


# ── Parsing ───────────────────────────────────────────────────────────────────

_RANK_STR = "23456789TJQKA"
_SUIT_STR = "cdhs"

_RANK_MAP: dict[str, int] = {r: i for i, r in enumerate(_RANK_STR)}
_RANK_MAP["10"] = 8   # aceita "10x" além de "Tx"

_SUIT_MAP: dict[str, int] = {s: i for i, s in enumerate(_SUIT_STR)}


def parse_card(token: str) -> int:
    """Converte string de carta para inteiro 0-51.

    Exemplos aceitos: 'Ah', 'AH', 'ah', 'Td', '10d', '2c', 'Ks'
    """
    t = token.strip()
    suit_ch = t[-1].lower()
    rank_ch = t[:-1].upper()
    if rank_ch not in _RANK_MAP:
        raise ValueError(f"Carta inválida: '{token}'")
    if suit_ch not in _SUIT_MAP:
        raise ValueError(f"Naipe inválido em '{token}'")
    return _RANK_MAP[rank_ch] * 4 + _SUIT_MAP[suit_ch]


def parse_cards(tokens: list[str]) -> np.ndarray:
    return np.array([parse_card(t) for t in tokens], dtype=np.uint8)


# ── Tabela de straights ───────────────────────────────────────────────────────
# Mapeia bitmask de ranks presentes → rank mais alto da sequência (0-12).

_STRAIGHT_HIGH: dict[int, int] = {}

for _s in range(9):   # _s = rank_idx do card mais baixo da sequência
    _mask = (1 << _s) | (1 << (_s + 1)) | (1 << (_s + 2)) | (1 << (_s + 3)) | (1 << (_s + 4))
    _STRAIGHT_HIGH[_mask] = _s + 4   # rank mais alto

# Wheel: A-2-3-4-5  →  ranks 0,1,2,3,12  →  high = rank de '5' = índice 3
_STRAIGHT_HIGH[(1 << 12) | (1 << 3) | (1 << 2) | (1 << 1) | (1 << 0)] = 3


# ── Avaliador de 5 cartas ─────────────────────────────────────────────────────
#
# Score codificado num único inteiro (comparável diretamente):
#   bits 20-22: classe da mão  (0=high card … 8=straight flush)
#   bits 16-19: kicker primário
#   bits 12-15: kicker secundário
#   bits  8-11: kicker terciário
#   bits  4- 7: kicker quaternário
#   bits  0- 3: kicker quinário
#
# Cada kicker ocupa 4 bits → suporta ranks 0-12 (≤ 15). ✓

def _score5(a: int, b: int, c: int, d: int, e: int) -> int:
    """Avalia 5 cartas (inteiros 0-51) e retorna um score comparável (maior = melhor)."""
    # Extrai rank (>>2) e naipe (&3)
    ra, sa = a >> 2, a & 3
    rb, sb = b >> 2, b & 3
    rc, sc = c >> 2, c & 3
    rd, sd = d >> 2, d & 3
    re, se = e >> 2, e & 3

    # ── Flush ──
    flush = (sa == sb == sc == sd == se)

    # ── Straight via bitmask ──
    rmask = (1 << ra) | (1 << rb) | (1 << rc) | (1 << rd) | (1 << re)
    str_high = _STRAIGHT_HIGH.get(rmask, -1)
    straight = str_high >= 0

    # ── Contagem de ranks ──
    cnt = [0] * 13
    cnt[ra] += 1
    cnt[rb] += 1
    cnt[rc] += 1
    cnt[rd] += 1
    cnt[re] += 1

    # Grupos ordenados por (contagem desc, rank desc)
    groups = sorted(
        ((r, cnt[r]) for r in range(13) if cnt[r]),
        key=lambda x: (x[1], x[0]),
        reverse=True,
    )
    gr = [r for r, k in groups for _ in range(k)]   # ranks na ordem de significância

    def enc(hc: int, r1=0, r2=0, r3=0, r4=0, r5=0) -> int:
        return (hc << 20) | (r1 << 16) | (r2 << 12) | (r3 << 8) | (r4 << 4) | r5

    top_count = groups[0][1]

    # ── Classificação ──
    if straight and flush:
        return enc(8, str_high)
    if top_count == 4:                                          # Quadra
        return enc(7, gr[0], gr[4])
    if top_count == 3 and len(groups) > 1 and groups[1][1] == 2:  # Full house
        return enc(6, gr[0], gr[3])
    if flush:
        return enc(5, gr[0], gr[1], gr[2], gr[3], gr[4])
    if straight:
        return enc(4, str_high)
    if top_count == 3:                                          # Trinca
        return enc(3, gr[0], gr[3], gr[4])
    if top_count == 2 and len(groups) > 1 and groups[1][1] == 2:  # Dois pares
        return enc(2, gr[0], gr[2], gr[4])
    if top_count == 2:                                          # Par
        return enc(1, gr[0], gr[2], gr[3], gr[4])
    return enc(0, gr[0], gr[1], gr[2], gr[3], gr[4])           # Carta alta


# ── Índices de combinações PLO ────────────────────────────────────────────────
# Pré-computados uma vez: C(6,2)=15 combos de mão × C(5,3)=10 combos de board = 150

_COMBO_IDX: np.ndarray = np.array(
    [
        [h1, h2, 6 + b1, 6 + b2, 6 + b3]
        for h1, h2 in combinations(range(6), 2)
        for b1, b2, b3 in combinations(range(5), 3)
    ],
    dtype=np.uint8,
)  # shape (150, 5)


def _best_plo6_hand(cards11: np.ndarray) -> int:
    """Melhor mão PLO possível a partir de 11 cartas [6 mão + 5 board].

    Avalia todos os C(6,2)×C(5,3) = 150 combos e retorna o maior score.
    """
    best = 0
    for idx in _COMBO_IDX:
        s = _score5(
            int(cards11[idx[0]]),
            int(cards11[idx[1]]),
            int(cards11[idx[2]]),
            int(cards11[idx[3]]),
            int(cards11[idx[4]]),
        )
        if s > best:
            best = s
    return best


# ── Motor Monte Carlo ─────────────────────────────────────────────────────────

def calculate_equity(
    hero_cards: list[str],
    board_cards: list[str] | None = None,
    n_opponents: int = 2,
    n_simulations: int = 800,
    known_dead_cards: list[str] | None = None,
) -> dict:
    """Calcula a equidade PLO6 via simulação Monte Carlo.

    Parameters
    ----------
    hero_cards        : 6 cartas da mão do herói, e.g. ['Ah','Kd','Qc','Js','Td','9h']
    board_cards       : 0-5 cartas comunitárias já reveladas
    n_opponents       : número de oponentes (padrão 2)
    n_simulations     : número de simulações Monte Carlo (padrão 800)
    known_dead_cards  : cartas conhecidas na sala (mãos dos outros jogadores físicos).
                        São removidas do baralho antes das simulações para garantir
                        que o runout nunca sorteie cartas que já estão na mesa.

    Returns
    -------
    dict com: equity (float 0-1), equity_pct (float 0-100), wins, ties, total
    """
    board_cards = board_cards or []

    if len(hero_cards) != 6:
        raise ValueError(f"PLO6 requer 6 cartas na mão, recebeu {len(hero_cards)}.")
    if len(board_cards) > 5:
        raise ValueError(f"Board não pode ter mais de 5 cartas, recebeu {len(board_cards)}.")

    hero  = parse_cards(hero_cards)
    board = parse_cards(board_cards)

    # Cartas já conhecidas → removidas do baralho restante
    known = set(int(c) for c in list(hero) + list(board))

    # Remove cartas fisicamente na sala (mãos dos outros jogadores do stream).
    # Isso garante equidade real: nenhuma carta que está na mão de um amigo
    # pode aparecer no runout simulado.
    if known_dead_cards:
        for card_str in known_dead_cards:
            try:
                known.add(parse_card(card_str))
            except ValueError:
                pass  # carta malformada é ignorada silenciosamente

    remaining = np.array([c for c in range(52) if c not in known], dtype=np.uint8)

    board_needed  = 5 - len(board)
    cards_per_opp = 6
    total_draw    = board_needed + n_opponents * cards_per_opp

    if total_draw > len(remaining):
        raise ValueError(
            f"Baralho insuficiente: precisa de {total_draw} cartas, "
            f"restam apenas {len(remaining)}."
        )

    rng  = np.random.default_rng()
    wins = 0
    ties = 0

    # Buffers pré-alocados para evitar alloc no loop interno
    hero11 = np.empty(11, dtype=np.uint8)
    opp11  = np.empty(11, dtype=np.uint8)
    hero11[:6] = hero

    for _ in range(n_simulations):
        sample = rng.choice(remaining, size=total_draw, replace=False)

        # Completa o board
        full_board = np.concatenate([board, sample[:board_needed]])
        hero11[6:] = full_board

        hero_score = _best_plo6_hand(hero11)

        # Avalia oponentes
        best_opp = 0
        opp11[6:] = full_board
        for i in range(n_opponents):
            start = board_needed + i * cards_per_opp
            opp11[:6] = sample[start : start + cards_per_opp]
            sc = _best_plo6_hand(opp11)
            if sc > best_opp:
                best_opp = sc

        if hero_score > best_opp:
            wins += 1
        elif hero_score == best_opp:
            ties += 1

    equity = (wins + 0.5 * ties) / n_simulations
    return {
        "equity":     round(equity, 4),
        "equity_pct": round(equity * 100, 1),
        "wins":       wins,
        "ties":       ties,
        "total":      n_simulations,
    }


# ── Nome da mão ───────────────────────────────────────────────────────────────

_HAND_NAMES = ["Alta", "Par", "2Pares", "Trinca", "Seq", "Flush", "Full", "Quadra", "StFl"]


def get_hand_name(hero_cards: list[str], board_cards: list[str]) -> str:
    """Retorna nome abreviado da melhor mão PLO6 do herói (exige board >= 3).

    Avalia todas as combinações C(n_mão,2) × C(n_board,3) e retorna a classe
    da mão mais forte encontrada.
    """
    if len(board_cards) < 3 or len(hero_cards) < 2:
        return ""
    try:
        hero  = parse_cards(hero_cards)
        board = parse_cards(board_cards)
    except ValueError:
        return ""
    best = 0
    n_h, n_b = len(hero), len(board)
    for h1, h2 in combinations(range(n_h), 2):
        for b1, b2, b3 in combinations(range(n_b), 3):
            s = _score5(int(hero[h1]), int(hero[h2]),
                        int(board[b1]), int(board[b2]), int(board[b3]))
            if s > best:
                best = s
    cls = (best >> 20) & 0xF
    return _HAND_NAMES[min(cls, 8)]


# ── Teste rápido ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    print("=== Teste PLO6 Monte Carlo ===\n")

    cases = [
        {
            "desc":  "Mão premium preflop (A-A-K-K double suited)",
            "hero":  ["Ah", "Ad", "Kh", "Kd", "Qh", "Jd"],
            "board": [],
        },
        {
            "desc":  "Mão fraca preflop",
            "hero":  ["2c", "3d", "7h", "8s", "4c", "5d"],
            "board": [],
        },
        {
            "desc":  "Flush draw no flop",
            "hero":  ["Ah", "Kh", "Qh", "Jd", "Tc", "9s"],
            "board": ["2h", "7h", "8c"],
        },
        {
            "desc":  "Mão feita no river (full house)",
            "hero":  ["Ah", "Ad", "Kh", "Kd", "Qh", "Jd"],
            "board": ["As", "Ks", "2c", "3d", "5h"],
        },
    ]

    for case in cases:
        t0 = time.perf_counter()
        result = calculate_equity(case["hero"], case["board"], n_opponents=2, n_simulations=800)
        elapsed = time.perf_counter() - t0
        print(f"[{case['desc']}]")
        print(f"  Equidade: {result['equity_pct']}%  "
              f"| Vitórias: {result['wins']}  "
              f"| Empates: {result['ties']}  "
              f"| Tempo: {elapsed:.2f}s")
        print()
