"""
cerebro-central/server.py  —  Orquestrador central (Python)
============================================================
Porta direta do server.ts para Python puro.

Protocolo:
  - Vision-worker  → WebSocket raw  ws://localhost:3000/ws
  - HUD / frontend → Socket.IO      http://localhost:3000
  - REST           → GET /, GET /health, POST /reset
"""

import asyncio
import json
import logging
from typing import Optional

import socketio
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

PORT = 3000

# ── Nomes em português ────────────────────────────────────────────────────────

RANK_PT: dict[str, str] = {
    "A": "Ás",     "2": "Dois",   "3": "Três",   "4": "Quatro", "5": "Cinco",
    "6": "Seis",   "7": "Sete",   "8": "Oito",   "9": "Nove",   "T": "Dez",
    "J": "Valete", "Q": "Dama",   "K": "Rei",
}

SUIT_PT: dict[str, str] = {
    "c": "de Paus", "d": "de Ouros", "h": "de Copas", "s": "de Espadas",
    "C": "de Paus", "D": "de Ouros", "H": "de Copas", "S": "de Espadas",
}

STREET_NAMES: dict[int, str] = {3: "Flop", 4: "Turn", 5: "River"}

SUITS = ["C", "D", "H", "S"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]


def card_to_portuguese(code: str) -> str:
    code = code.strip()
    rank = code[:-1].upper()
    suit = code[-1]
    return f"{RANK_PT.get(rank, rank)} {SUIT_PT.get(suit, suit)}"


def _build_deck() -> list[str]:
    return [f"{r}{s}" for s in SUITS for r in RANKS]


# ── GameState ─────────────────────────────────────────────────────────────────

class GameState:
    def __init__(self) -> None:
        self.live_deck:     list[str]      = _build_deck()
        self.current_hand:  list[str]      = []
        self.current_board: list[str]      = []
        self.last_equity:   Optional[dict] = None

    def _remove_from_deck(self, cards: list[str]) -> None:
        known = {c.upper() for c in cards}
        self.live_deck = [c for c in self.live_deck if c not in known]

    def update_hand(self, incoming: list[str]) -> Optional[str]:
        if not isinstance(incoming, list):
            return None
        normalized = [c.strip().upper() for c in incoming if c.strip()]
        prev_set   = set(self.current_hand)
        overlap    = sum(1 for c in normalized if c in prev_set)
        is_new     = bool(prev_set) and bool(normalized) and overlap == 0

        if is_new:
            self.live_deck     = _build_deck()
            self.current_board = []
            self.last_equity   = None

        new_cards         = [c for c in normalized if c not in prev_set]
        self.current_hand = normalized
        self._remove_from_deck(normalized + self.current_board)

        if not normalized or (not new_cards and not is_new):
            return None

        names = ", ".join(card_to_portuguese(c) for c in normalized)
        return f"Nova mão. Sua mão: {names}" if is_new else f"Sua mão: {names}"

    def update_board(self, incoming: list[str]) -> Optional[str]:
        if not isinstance(incoming, list):
            return None
        normalized = [c.strip().upper() for c in incoming if c.strip()]
        new_cards  = [c for c in normalized if c not in self.current_board]
        if not new_cards:
            return None

        prev_board         = self.current_board[:]
        self.current_board = normalized
        self._remove_from_deck(normalized)

        street = STREET_NAMES.get(len(normalized))
        if not street:
            return None

        if not prev_board and len(normalized) == 3:
            board_names = ", ".join(card_to_portuguese(c) for c in normalized)
            hand_names  = ", ".join(card_to_portuguese(c) for c in self.current_hand)
            suffix = f". Sua mão: {hand_names}" if hand_names else ""
            return f"Flop: {board_names}{suffix}"

        return f"{street}: {', '.join(card_to_portuguese(c) for c in new_cards)}"

    def update_equity(self, payload: dict) -> Optional[str]:
        prev = self.last_equity
        self.last_equity = payload
        if prev and prev.get("action") == payload.get("action"):
            return None
        if payload.get("speak_text"):
            return payload["speak_text"]
        pct    = payload.get("equity_pct", 0)
        action = payload.get("action", "")
        label  = payload.get("label", "")
        return f"{pct} por cento. {action}. {label}"

    def reset(self) -> None:
        self.live_deck     = _build_deck()
        self.current_hand  = []
        self.current_board = []
        self.last_equity   = None

    def snapshot(self) -> dict:
        return {
            "deck":   self.live_deck,
            "hand":   self.current_hand,
            "board":  self.current_board,
            "equity": self.last_equity,
        }


# ── Setup ─────────────────────────────────────────────────────────────────────

game = GameState()
sio  = socketio.AsyncServer(cors_allowed_origins="*", async_mode="aiohttp")
app  = web.Application()
sio.attach(app)


# ── Broadcast helpers ─────────────────────────────────────────────────────────

async def broadcast_deck() -> None:
    await sio.emit("deck_state", game.live_deck)

async def broadcast_hand() -> None:
    await sio.emit("hand_state", {"hand": game.current_hand, "board": game.current_board})

async def broadcast_speak(text: str) -> None:
    await sio.emit("speak", text)


# ── Handlers de evento ────────────────────────────────────────────────────────

async def handle_update_hands(cards: list[str]) -> None:
    ann = game.update_hand(cards)
    await broadcast_deck()
    await broadcast_hand()
    if ann:
        await broadcast_speak(ann)

async def handle_update_board(cards: list[str]) -> None:
    ann = game.update_board(cards)
    await broadcast_deck()
    await broadcast_hand()
    if ann:
        await broadcast_speak(ann)

async def handle_equity_state(payload: dict) -> None:
    await sio.emit("equity_state", payload)
    ann = game.update_equity(payload)
    if ann:
        await broadcast_speak(ann)

async def handle_reset() -> None:
    game.reset()
    await broadcast_deck()
    await broadcast_hand()
    await sio.emit("equity_state", None)
    await broadcast_speak("Baralho resetado. Nova mão.")


# ── Socket.IO (HUD) ───────────────────────────────────────────────────────────

@sio.on("connect")
async def sio_connect(sid, environ):
    snap = game.snapshot()
    await sio.emit("deck_state",  snap["deck"],                                   to=sid)
    await sio.emit("hand_state",  {"hand": snap["hand"], "board": snap["board"]}, to=sid)
    if snap["equity"]:
        await sio.emit("equity_state", snap["equity"], to=sid)
    log.info(f"[SIO] Cliente conectado: {sid}")

@sio.on("update_hands")
async def sio_update_hands(sid, cards):
    await handle_update_hands(cards)

@sio.on("update_board")
async def sio_update_board(sid, cards):
    await handle_update_board(cards)

@sio.on("equity_state")
async def sio_equity_state(sid, payload):
    await handle_equity_state(payload)

@sio.on("reset_deck")
async def sio_reset(sid):
    await handle_reset()

@sio.on("disconnect")
async def sio_disconnect(sid):
    log.info(f"[SIO] Cliente desconectado: {sid}")


# ── WebSocket raw (vision-worker) — rota /ws ──────────────────────────────────

async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    log.info("[WS] Vision-worker conectado.")
    await ws.send_str(json.dumps({"event": "deck_state", "payload": game.live_deck}))

    async for msg in ws:
        if msg.type != web.WSMsgType.TEXT:
            continue
        try:
            data    = json.loads(msg.data)
            event   = data.get("event")
            payload = data.get("payload")

            if event == "update_hands" and isinstance(payload, list):
                await handle_update_hands(payload)
            elif event == "update_board" and isinstance(payload, list):
                await handle_update_board(payload)
            elif event == "equity_state" and payload:
                await handle_equity_state(payload)
            elif event == "reset_deck":
                await handle_reset()
        except (json.JSONDecodeError, KeyError):
            continue

    log.info("[WS] Vision-worker desconectado.")
    return ws


# ── REST ──────────────────────────────────────────────────────────────────────

async def rest_root(request: web.Request) -> web.Response:
    snap = game.snapshot()
    return web.json_response({
        "status":   "ok",
        "deckSize": len(snap["deck"]),
        **snap,
    })

async def rest_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "deckSize": len(game.live_deck)})

async def rest_reset(request: web.Request) -> web.Response:
    await handle_reset()
    return web.json_response({"status": "ok"})

app.router.add_get( "/ws",     ws_handler)
app.router.add_get( "/",       rest_root)
app.router.add_get( "/health", rest_health)
app.router.add_post("/reset",  rest_reset)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info(f"[INIT] Cerebro-central ouvindo na porta {PORT}")
    log.info(f"[INIT]   WS raw    → ws://localhost:{PORT}/ws")
    log.info(f"[INIT]   Socket.IO → http://localhost:{PORT}")
    log.info(f"[INIT]   REST      → http://localhost:{PORT}/health")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)
