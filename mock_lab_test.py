"""
mock_lab_test.py -- Teste de laboratório end-to-end sem captura de tela.

Simula dois vision-workers conectados ao cerebro-central e verifica:
  1. P1 envia mão → server atualiza local_players["1"]
  2. P2 envia mão → server atualiza local_players["2"]
  3. Server calcula shared_dead_cards = board + P1 + P2
  4. Server faz broadcast shared_state para ambos os workers
  5. Motor de equidade de P1 usa dead_cards (exclui mão de P2 do Monte Carlo)
  6. Equity com dead_cards ≠ equity sem dead_cards (prova que funciona)

Pré-requisito: cerebro-central rodando em ws://localhost:3000/ws
    cd cerebro-central && python server.py

Uso:
    python mock_lab_test.py
"""

import json
import sys
import threading
import time

import websocket

sys.path.insert(0, "vision-worker")
try:
    from equity_calc import calculate_equity  # type: ignore
    print("[OK] equity_calc importado de vision-worker/")
except ImportError:
    print("[ERRO] Nao foi possivel importar equity_calc.")
    print("       Execute este script a partir de C:/Jeremias/")
    sys.exit(1)

SERVER_URL = "ws://localhost:3000/ws"

# Mãos da screenshot
P1_HAND = ["Kh", "Ks", "Jh", "Td", "8d", "7s"]   # palmeirass (esq)
P2_HAND = ["Qh", "Jd", "9d", "8h", "7d", "5s"]   # minas GR   (dir)
BOARD   = []                                         # preflop


# -- Resultados compartilhados -------------------------------------------------

results: dict = {
    "p1_dead_cards": [],
    "p2_dead_cards": [],
    "p1_shared_received": threading.Event(),
    "p2_shared_received": threading.Event(),
}


# -- Worker mock ---------------------------------------------------------------

def make_worker(player_id: str, hand: list[str], board: list[str]):
    """Cria uma função que age como um vision-worker simplificado."""

    def on_open(ws):
        print(f"[P{player_id}] Conectado ao servidor.")
        ws.send(json.dumps({
            "event":     "update_hands",
            "player_id": player_id,
            "payload":   hand,
        }))
        ws.send(json.dumps({
            "event":   "update_board",
            "payload": board,
        }))
        print(f"[P{player_id}] Mao enviada: {hand}")

    my_cards_upper = {c.upper() for c in hand}

    def on_message(ws, message):
        try:
            data = json.loads(message)
            if data.get("event") == "shared_state":
                dead = data.get("dead_cards", [])
                results[f"p{player_id}_dead_cards"] = dead
                dead_set = {c.upper() for c in dead}
                # Only mark done when the server has confirmed our own hand is in dead_cards
                if my_cards_upper & dead_set:
                    results[f"p{player_id}_shared_received"].set()
                    print(f"[P{player_id}] shared_state confirmado: {len(dead)} cartas mortas -> {dead}")
                    ws.close()
        except Exception as exc:
            print(f"[P{player_id}] on_message erro: {exc}")

    def on_error(ws, error):
        print(f"[P{player_id}] Erro WS: {error}")

    def on_close(ws, code, msg):
        print(f"[P{player_id}] Desconectado.")

    return websocket.WebSocketApp(
        SERVER_URL,
        on_open    = on_open,
        on_message = on_message,
        on_error   = on_error,
        on_close   = on_close,
    )


def run_worker(ws_app):
    ws_app.run_forever()


# -- Main ----------------------------------------------------------------------

def main():
    print("=" * 60)
    print("TESTE DE LABORATORIO -- Multi-player shared dead cards")
    print("=" * 60)
    print(f"P1 mao: {P1_HAND}")
    print(f"P2 mao: {P2_HAND}")
    print(f"Board : {BOARD}")
    print()

    # Lança os dois workers em threads separadas
    ws_p1 = make_worker("1", P1_HAND, BOARD)
    ws_p2 = make_worker("2", P2_HAND, BOARD)

    t1 = threading.Thread(target=run_worker, args=(ws_p1,), daemon=True)
    t2 = threading.Thread(target=run_worker, args=(ws_p2,), daemon=True)

    t1.start()
    time.sleep(0.3)   # pequeno delay para garantir ordem de conexão
    t2.start()

    # Aguarda ambos receberem shared_state (timeout 10s)
    ok1 = results["p1_shared_received"].wait(timeout=10)
    ok2 = results["p2_shared_received"].wait(timeout=10)

    t1.join(timeout=2)
    t2.join(timeout=2)

    print()
    print("-" * 60)
    print("RESULTADO DA COMUNICACAO:")
    print(f"  P1 recebeu shared_state: {'SIM' if ok1 else 'NAO (timeout)'}")
    print(f"  P2 recebeu shared_state: {'SIM' if ok2 else 'NAO (timeout)'}")

    if not ok1 or not ok2:
        print("\nERRO: Servidor nao respondeu. Certifique-se de que o cerebro-central esta rodando:")
        print("  cd cerebro-central && python server.py")
        sys.exit(1)

    dead_from_server = results["p1_dead_cards"]

    print()
    print("-" * 60)
    print("CARTAS MORTAS CONSOLIDADAS PELO SERVIDOR:")
    for c in sorted(dead_from_server):
        print(f"  {c}")

    # Verifica que as mãos de ambos os jogadores estão nas dead_cards
    p1_set = {c.upper() for c in P1_HAND}
    p2_set = {c.upper() for c in P2_HAND}
    dead_set = {c.upper() for c in dead_from_server}

    missing_p1 = p1_set - dead_set
    missing_p2 = p2_set - dead_set

    print()
    if missing_p1:
        print(f"[FALHA] Cartas de P1 faltando nas dead_cards: {missing_p1}")
    else:
        print("[OK] Todas as cartas de P1 estao nas dead_cards")

    if missing_p2:
        print(f"[FALHA] Cartas de P2 faltando nas dead_cards: {missing_p2}")
    else:
        print("[OK] Todas as cartas de P2 estao nas dead_cards")

    # -- Teste do motor de equidade ---------------------------------------------
    print()
    print("-" * 60)
    print("TESTE DO MOTOR DE EQUIDADE (600 simulacoes):")

    n_sims = 600

    # Equidade SEM dead_cards (modo solo -- P1 não sabe as cartas de P2)
    eq_solo = calculate_equity(P1_HAND, BOARD, n_opponents=1, n_simulations=n_sims)

    # Equidade COM dead_cards (modo streaming -- P1 sabe que P2 tem aquelas cartas)
    eq_shared = calculate_equity(
        P1_HAND, BOARD,
        n_opponents=1,
        n_simulations=n_sims,
        dead_cards=dead_from_server,
    )

    print(f"  Sem dead_cards (solo)   : {eq_solo['equity_pct']:5.1f}%  "
          f"({eq_solo['wins']}V / {eq_solo['ties']}E / {n_sims}sim)")
    print(f"  Com dead_cards (overlay): {eq_shared['equity_pct']:5.1f}%  "
          f"({eq_shared['wins']}V / {eq_shared['ties']}E / {n_sims}sim)")

    diff = abs(eq_solo["equity_pct"] - eq_shared["equity_pct"])
    print(f"  Diferenca              : {diff:.1f}pp")

    if diff > 0.1:
        print("\n[OK] Dead_cards estao alterando o calculo -- integracao funcionando!")
    else:
        print("\n[AVISO] Diferenca muito pequena -- pode ser variancia normal com poucas sims.")

    print()
    print("=" * 60)
    print("TESTE CONCLUIDO")
    print("=" * 60)


if __name__ == "__main__":
    main()
