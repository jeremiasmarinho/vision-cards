import express, { Application } from "express";
import http from "http";
import cors from "cors";
import { Server as SocketIOServer } from "socket.io";
import { WebSocketServer, WebSocket } from "ws";

type CardSuit = "C" | "D" | "H" | "S";
type CardRank = "A" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "10" | "J" | "Q" | "K";
export type CardCode = `${CardRank}${CardSuit}`;

class GameState {
  private liveDeck: CardCode[];

  constructor() {
    this.liveDeck = this.createFullDeck();
  }

  private createFullDeck(): CardCode[] {
    const suits: CardSuit[] = ["C", "D", "H", "S"];
    const ranks: CardRank[] = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"];

    const deck: CardCode[] = [];
    for (const suit of suits) {
      for (const rank of ranks) {
        deck.push(`${rank}${suit}` as CardCode);
      }
    }

    return deck;
  }

  public getDeck(): CardCode[] {
    return [...this.liveDeck];
  }

  public removeCards(cards: string[]): void {
    if (!Array.isArray(cards)) return;

    const normalized = cards.map((c) => c.trim().toUpperCase()) as CardCode[];
    this.liveDeck = this.liveDeck.filter((card) => !normalized.includes(card));
  }
}

const PORT = 3000;

const app: Application = express();
app.use(cors());
app.use(express.json());

const httpServer = http.createServer(app);

const io = new SocketIOServer(httpServer, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"]
  }
});

const wss = new WebSocketServer({ server: httpServer });

const gameState = new GameState();

function handleUpdateHands(cards: string[]): void {
  gameState.removeCards(cards);
  io.emit("deck_state", gameState.getDeck());

  const payload = JSON.stringify({
    event: "deck_state",
    payload: gameState.getDeck()
  });

  wss.clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(payload);
    }
  });
}

io.on("connection", (socket) => {
  socket.emit("deck_state", gameState.getDeck());

  socket.on("update_hands", (cards: string[]) => {
    handleUpdateHands(cards);
  });
});

wss.on("connection", (socket) => {
  const initPayload = JSON.stringify({
    event: "deck_state",
    payload: gameState.getDeck()
  });
  socket.send(initPayload);

  socket.on("message", (data) => {
    try {
      const parsed = JSON.parse(data.toString());
      if (parsed && parsed.event === "update_hands" && Array.isArray(parsed.payload)) {
        handleUpdateHands(parsed.payload);
      }
    } catch {
      // Ignora mensagens malformadas para robustez.
    }
  });
});

app.get("/health", (_req, res) => {
  res.json({ status: "ok", deckSize: gameState.getDeck().length });
});

httpServer.listen(PORT, () => {
  console.log(`Cerebro-central ouvindo na porta ${PORT}`);
});

