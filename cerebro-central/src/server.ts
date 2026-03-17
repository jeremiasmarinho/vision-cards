import express, { Application } from "express";
import http from "http";
import cors from "cors";
import { Server as SocketIOServer } from "socket.io";
import { WebSocketServer, WebSocket } from "ws";

// ─── Types ───────────────────────────────────────────────────────────────────

type CardSuit = "C" | "D" | "H" | "S";
type CardRank = "A" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "10" | "J" | "Q" | "K";
export type CardCode = `${CardRank}${CardSuit}`;

interface EquityPayload {
  equity_pct:  number;
  equity:      number;
  wins:        number;
  ties:        number;
  total:       number;
  action:      string;   // "RAISE" | "CALL" | "FOLD"
  label:       string;
  street:      string;
  speak_text?: string;
}

// ─── Portuguese card names ────────────────────────────────────────────────────

const RANK_PT: Record<string, string> = {
  A: "Ás", 2: "Dois", 3: "Três", 4: "Quatro", 5: "Cinco",
  6: "Seis", 7: "Sete", 8: "Oito", 9: "Nove", 10: "Dez",
  J: "Valete", Q: "Dama", K: "Rei",
};

const SUIT_PT: Record<string, string> = {
  C: "de Paus", D: "de Ouros", H: "de Copas", S: "de Espadas",
};

function cardToPortuguese(code: string): string {
  const upper = code.toUpperCase().trim();
  const rank  = upper.startsWith("10") ? "10" : upper[0];
  const suit  = upper.slice(rank.length);
  return `${RANK_PT[rank] ?? rank} ${SUIT_PT[suit] ?? suit}`;
}

// ─── Game state ───────────────────────────────────────────────────────────────

const STREET_NAMES: Record<number, string> = { 3: "Flop", 4: "Turn", 5: "River" };

class GameState {
  private liveDeck:     CardCode[] = [];
  private currentHand:  string[]   = [];
  private currentBoard: string[]   = [];
  private lastEquity:   EquityPayload | null = null;

  constructor() {
    this.liveDeck = this._buildDeck();
  }

  private _buildDeck(): CardCode[] {
    const suits: CardSuit[] = ["C", "D", "H", "S"];
    const ranks: CardRank[] = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"];
    const deck: CardCode[] = [];
    for (const suit of suits)
      for (const rank of ranks)
        deck.push(`${rank}${suit}` as CardCode);
    return deck;
  }

  getDeck():        CardCode[]          { return [...this.liveDeck];      }
  getCurrentHand(): string[]            { return [...this.currentHand];   }
  getCurrentBoard():string[]            { return [...this.currentBoard];  }
  getLastEquity():  EquityPayload | null { return this.lastEquity;         }

  /** Retorna frase TTS ou null se nada relevante mudou. */
  updateHand(incoming: string[]): string | null {
    if (!Array.isArray(incoming)) return null;

    const normalized = incoming.map(c => c.trim().toUpperCase()).filter(Boolean);
    const prevSet    = new Set(this.currentHand);
    const overlap    = normalized.filter(c => prevSet.has(c)).length;
    const isNewHand  = prevSet.size > 0 && normalized.length > 0 && overlap === 0;

    if (isNewHand) {
      this.liveDeck    = this._buildDeck();
      this.currentBoard = [];
      this.lastEquity  = null;
    }

    const newCards = normalized.filter(c => !prevSet.has(c));
    this.currentHand = normalized;
    this._removeFromDeck([...normalized, ...this.currentBoard]);

    if (normalized.length === 0 || (newCards.length === 0 && !isNewHand)) return null;

    const names = normalized.map(cardToPortuguese).join(", ");
    return isNewHand ? `Nova mão. Sua mão: ${names}` : `Sua mão: ${names}`;
  }

  /** Retorna frase TTS ou null se nada relevante mudou. */
  updateBoard(incoming: string[]): string | null {
    if (!Array.isArray(incoming)) return null;

    const normalized = incoming.map(c => c.trim().toUpperCase()).filter(Boolean);
    const prevBoard  = this.currentBoard;
    const newCards   = normalized.filter(c => !prevBoard.includes(c));

    if (newCards.length === 0) return null;

    this.currentBoard = normalized;
    this._removeFromDeck(normalized);

    const street = STREET_NAMES[normalized.length];
    if (!street) return null;

    if (prevBoard.length === 0 && normalized.length === 3) {
      const boardNames = normalized.map(cardToPortuguese).join(", ");
      const handNames  = this.currentHand.map(cardToPortuguese).join(", ");
      return `Flop: ${boardNames}${handNames ? `. Sua mão: ${handNames}` : ""}`;
    }

    return `${street}: ${newCards.map(cardToPortuguese).join(", ")}`;
  }

  updateEquity(payload: EquityPayload): string | null {
    const prev = this.lastEquity;
    this.lastEquity = payload;
    // Fala apenas quando a ação recomendada muda
    if (prev?.action === payload.action) return null;
    return payload.speak_text
      ?? `${payload.equity_pct} por cento. ${payload.action}. ${payload.label}`;
  }

  reset(): void {
    this.liveDeck    = this._buildDeck();
    this.currentHand  = [];
    this.currentBoard = [];
    this.lastEquity  = null;
  }

  private _removeFromDeck(cards: string[]): void {
    const known = new Set(cards);
    this.liveDeck = this.liveDeck.filter(c => !known.has(c));
  }
}

// ─── Server setup ─────────────────────────────────────────────────────────────

const PORT = 3000;
const app: Application = express();
app.use(cors());
app.use(express.json());

const httpServer = http.createServer(app);

const io = new SocketIOServer(httpServer, {
  cors: { origin: "*", methods: ["GET", "POST"] },
});

const wss = new WebSocketServer({ server: httpServer });
const gameState = new GameState();

// ─── Broadcast helpers ────────────────────────────────────────────────────────

function broadcastDeckState(): void {
  const deck = gameState.getDeck();
  io.emit("deck_state", deck);
  const msg = JSON.stringify({ event: "deck_state", payload: deck });
  wss.clients.forEach(c => { if (c.readyState === WebSocket.OPEN) c.send(msg); });
}

function broadcastHandState(): void {
  io.emit("hand_state", {
    hand:  gameState.getCurrentHand(),
    board: gameState.getCurrentBoard(),
  });
}

function broadcastSpeak(text: string): void {
  io.emit("speak", text);
}

// ─── Event handlers ───────────────────────────────────────────────────────────

function handleUpdateHands(cards: string[]): void {
  const ann = gameState.updateHand(cards);
  broadcastDeckState();
  broadcastHandState();
  if (ann) broadcastSpeak(ann);
}

function handleUpdateBoard(cards: string[]): void {
  const ann = gameState.updateBoard(cards);
  broadcastDeckState();
  broadcastHandState();
  if (ann) broadcastSpeak(ann);
}

function handleEquityState(payload: EquityPayload): void {
  io.emit("equity_state", payload);
  const ann = gameState.updateEquity(payload);
  if (ann) broadcastSpeak(ann);
}

function handleReset(): void {
  gameState.reset();
  broadcastDeckState();
  broadcastHandState();
  io.emit("equity_state", null);
  broadcastSpeak("Baralho resetado. Nova mão.");
}

// ─── Socket.IO ────────────────────────────────────────────────────────────────

io.on("connection", socket => {
  socket.emit("deck_state", gameState.getDeck());
  socket.emit("hand_state", {
    hand:  gameState.getCurrentHand(),
    board: gameState.getCurrentBoard(),
  });
  const eq = gameState.getLastEquity();
  if (eq) socket.emit("equity_state", eq);

  socket.on("update_hands",   (cards: string[])      => handleUpdateHands(cards));
  socket.on("update_board",   (cards: string[])      => handleUpdateBoard(cards));
  socket.on("equity_state",   (p: EquityPayload)     => handleEquityState(p));
  socket.on("reset_deck",     ()                     => handleReset());
});

// ─── Raw WebSocket ────────────────────────────────────────────────────────────

wss.on("connection", socket => {
  socket.send(JSON.stringify({ event: "deck_state", payload: gameState.getDeck() }));

  socket.on("message", data => {
    try {
      const parsed = JSON.parse(data.toString());
      if (!parsed?.event) return;

      switch (parsed.event) {
        case "update_hands":
          if (Array.isArray(parsed.payload)) handleUpdateHands(parsed.payload);
          break;
        case "update_board":
          if (Array.isArray(parsed.payload)) handleUpdateBoard(parsed.payload);
          break;
        case "equity_state":
          if (parsed.payload) handleEquityState(parsed.payload as EquityPayload);
          break;
        case "reset_deck":
          handleReset();
          break;
      }
    } catch {
      // Ignora mensagens malformadas
    }
  });
});

// ─── REST ─────────────────────────────────────────────────────────────────────

app.get("/", (_req, res) => {
  res.json({
    status:   "ok",
    deckSize: gameState.getDeck().length,
    deck:     gameState.getDeck(),
    hand:     gameState.getCurrentHand(),
    board:    gameState.getCurrentBoard(),
    equity:   gameState.getLastEquity(),
  });
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok", deckSize: gameState.getDeck().length });
});

app.post("/reset", (_req, res) => {
  handleReset();
  res.json({ status: "ok" });
});

// ─── Start ────────────────────────────────────────────────────────────────────

httpServer.listen(PORT, () => {
  console.log(`Cerebro-central ouvindo na porta ${PORT}`);
});
