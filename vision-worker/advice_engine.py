"""
PLO6 Advice Engine
==================
Motor de recomendação de ação baseado em equidade e rua.
Zero dependências externas — lógica puramente threshold-based.

Filosofia PLO6:
  - Equity "fair share" com 2 oponentes ≈ 33 %
  - Equity "fair share" com 3 oponentes ≈ 25 %
  - PLO6 produz mãos maiores do que PLO4; os limiares refletem isso.
"""

from __future__ import annotations

# ── Thresholds por rua ────────────────────────────────────────────────────────
# Cada entrada: (equity_mínima, ação, mensagem_pt)
# Avaliado de cima para baixo; o primeiro match vence.

_THRESHOLDS: dict[str, list[tuple[float, str, str]]] = {
    "Preflop": [
        (0.62, "RAISE", "Mão premium — abra forte ou 3-bet"),
        (0.47, "CALL",  "Mão especulativa — veja o flop barato"),
        (0.00, "FOLD",  "Mão fraca — descarte sem rodeios"),
    ],
    "Flop": [
        (0.65, "RAISE", "Muito forte no flop — construa o pot"),
        (0.50, "RAISE", "Favorito no flop — aposte por valor"),
        (0.38, "CALL",  "Continue com cautela — avalie o turn"),
        (0.00, "FOLD",  "Equidade insuficiente — saia do pot"),
    ],
    "Turn": [
        (0.60, "RAISE", "Sólido na turn — pressione"),
        (0.42, "CALL",  "Defenda barato na turn"),
        (0.00, "FOLD",  "Sem equidade para continuar"),
    ],
    "River": [
        (0.55, "RAISE", "Showdown forte — extraia valor máximo"),
        (0.45, "CALL",  "Call justificado no river"),
        (0.00, "FOLD",  "Mão perdedora — fold"),
    ],
}

# Fallback para ruas inesperadas
_DEFAULT = _THRESHOLDS["Flop"]

# Mapa de ação para texto pronunciável em PT-BR
_ACTION_PT: dict[str, str] = {
    "RAISE": "Raise",
    "CALL":  "Call",
    "FOLD":  "Fold",
}


def get_advice(equity: float, street: str = "Preflop") -> dict:
    """Retorna recomendação de ação para dada equidade e rua.

    Parameters
    ----------
    equity : float 0.0–1.0  (probabilidade de vitória do herói)
    street : "Preflop" | "Flop" | "Turn" | "River"

    Returns
    -------
    dict com chaves:
        action      – "RAISE" | "CALL" | "FOLD"
        label       – frase explicativa em PT-BR
        equity_pct  – equity * 100 arredondado (float)
        street      – rua recebida
        speak_text  – string pronta para TTS
    """
    thresholds = _THRESHOLDS.get(street, _DEFAULT)

    action = "FOLD"
    label  = "Equidade insuficiente"

    for min_eq, act, lbl in thresholds:
        if equity >= min_eq:
            action = act
            label  = lbl
            break

    equity_pct = round(equity * 100, 1)
    speak_text = f"{equity_pct} por cento. {_ACTION_PT[action]}. {label}."

    return {
        "action":     action,
        "label":      label,
        "equity_pct": equity_pct,
        "street":     street,
        "speak_text": speak_text,
    }


# ── Teste rápido ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cases = [
        (0.72, "Preflop"),
        (0.50, "Preflop"),
        (0.35, "Preflop"),
        (0.68, "Flop"),
        (0.52, "Flop"),
        (0.40, "Flop"),
        (0.30, "Flop"),
        (0.61, "Turn"),
        (0.45, "Turn"),
        (0.28, "Turn"),
        (0.58, "River"),
        (0.46, "River"),
        (0.30, "River"),
    ]

    print("=== Teste Advice Engine ===\n")
    for eq, street in cases:
        adv = get_advice(eq, street)
        print(f"  eq={eq:.0%}  {street:<8}  =>  {adv['action']:<5}  |  {adv['label']}")
