(() => {
  // ── Elements ──────────────────────────────────────────────────────────────

  const connStatusEl   = document.getElementById("conn-status");
  const connLabelEl    = document.getElementById("conn-label");
  const handChipsEl    = document.getElementById("hand-chips");
  const boardChipsEl   = document.getElementById("board-chips");
  const streetBadgeEl  = document.getElementById("street-badge");
  const deckChipsEl    = document.getElementById("deck-chips");
  const deckCountEl    = document.getElementById("deck-count");
  const ttsToggleEl    = document.getElementById("tts-toggle");
  const ttsLastEl      = document.getElementById("tts-last");
  const voiceSelectEl  = document.getElementById("voice-select");
  const speedSelectEl  = document.getElementById("speed-select");

  // Equity elements
  const equityPctEl    = document.getElementById("equity-pct");
  const actionBadgeEl  = document.getElementById("action-badge");
  const equityLabelEl  = document.getElementById("equity-label");
  const equityBarEl    = document.getElementById("equity-bar");
  const equityMetaEl   = document.getElementById("equity-meta");
  const equityStreetEl = document.getElementById("equity-street");

  // ── TTS ───────────────────────────────────────────────────────────────────

  let ttsEnabled  = false;
  let voices      = [];
  let speechQueue = [];
  let isSpeaking  = false;

  function loadVoices() {
    const all = speechSynthesis.getVoices();
    voices = all.filter(v => v.lang.startsWith("pt"));
    if (voices.length === 0) voices = all;

    voiceSelectEl.innerHTML = "";
    voices.forEach((v, i) => {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = v.name.slice(0, 28);
      if (v.lang === "pt-BR") opt.selected = true;
      voiceSelectEl.appendChild(opt);
    });
  }

  if (typeof speechSynthesis.onvoiceschanged !== "undefined")
    speechSynthesis.onvoiceschanged = loadVoices;
  loadVoices();

  function speak(text) {
    if (!ttsEnabled) return;
    speechQueue.push(text);
    processQueue();
  }

  function processQueue() {
    if (isSpeaking || speechQueue.length === 0) return;
    const text   = speechQueue.shift();
    ttsLastEl.textContent = text;

    const utter   = new SpeechSynthesisUtterance(text);
    utter.lang    = "pt-BR";
    utter.rate    = parseFloat(speedSelectEl.value) || 1.0;
    const vIdx    = parseInt(voiceSelectEl.value, 10);
    if (!isNaN(vIdx) && voices[vIdx]) utter.voice = voices[vIdx];

    isSpeaking    = true;
    utter.onend   = () => { isSpeaking = false; processQueue(); };
    utter.onerror = () => { isSpeaking = false; processQueue(); };
    speechSynthesis.speak(utter);
  }

  function toggleTts() {
    ttsEnabled = !ttsEnabled;
    ttsToggleEl.textContent = ttsEnabled ? "Voz Ligada" : "Voz Desligada";
    ttsToggleEl.classList.toggle("on", ttsEnabled);
    if (!ttsEnabled) {
      speechSynthesis.cancel();
      speechQueue = [];
      isSpeaking  = false;
    } else {
      speak("Voz ativada");
    }
  }

  window.toggleTts = toggleTts;

  // ── Card helpers ──────────────────────────────────────────────────────────

  function suitOf(code) {
    const u = code.toUpperCase();
    return (u.startsWith("10") ? u[2] : u[u.length - 1]).toLowerCase();
  }

  const STREET_LABELS = { 3: "Flop", 4: "Turn", 5: "River" };

  function makeChip(code, cls) {
    const chip = document.createElement("span");
    chip.className = `chip ${cls} ${suitOf(code)}`;
    chip.textContent = code.toUpperCase();
    return chip;
  }

  function renderHandChips(cards) {
    handChipsEl.innerHTML = "";
    if (!cards || cards.length === 0) {
      handChipsEl.innerHTML = '<span class="empty">Aguardando cartas...</span>';
      return;
    }
    cards.forEach(c => handChipsEl.appendChild(makeChip(c, "chip-hand")));
  }

  function renderBoardChips(cards) {
    boardChipsEl.innerHTML = "";
    const count = cards ? cards.length : 0;

    if (count === 0) {
      boardChipsEl.innerHTML = '<span class="empty">Preflop</span>';
      streetBadgeEl.style.display = "none";
      return;
    }

    const label = STREET_LABELS[count];
    if (label) { streetBadgeEl.textContent = label; streetBadgeEl.style.display = ""; }
    else        { streetBadgeEl.style.display = "none"; }

    cards.forEach(c => boardChipsEl.appendChild(makeChip(c, "chip-board")));
  }

  function renderDeck(cards) {
    deckChipsEl.innerHTML = "";
    deckCountEl.textContent = String(cards ? cards.length : 0);
    if (!cards || cards.length === 0) return;
    cards.forEach(c => {
      const chip = document.createElement("span");
      chip.className = "chip chip-deck";
      chip.textContent = c.toUpperCase();
      deckChipsEl.appendChild(chip);
    });
  }

  // ── Equity ────────────────────────────────────────────────────────────────

  const ACTION_COLORS = { RAISE: "#4ade80", CALL: "#fde047", FOLD: "#f87171" };
  const ACTION_BARS   = { RAISE: "#22c55e", CALL: "#eab308", FOLD: "#ef4444" };

  function renderEquity(data) {
    if (!data) {
      equityPctEl.textContent  = "—";
      equityPctEl.className    = "equity-pct pending";
      actionBadgeEl.textContent = "Calculando...";
      actionBadgeEl.className  = "action-badge pending";
      equityLabelEl.textContent = "Aguardando detecção de cartas";
      equityBarEl.style.width  = "0%";
      equityBarEl.style.background = "#374151";
      equityMetaEl.textContent  = "";
      equityStreetEl.style.display = "none";
      return;
    }

    const cls = (data.action || "pending").toLowerCase();
    const pct = data.equity_pct ?? 0;

    equityPctEl.textContent   = `${pct}%`;
    equityPctEl.className     = `equity-pct ${cls}`;

    actionBadgeEl.textContent = data.action || "—";
    actionBadgeEl.className   = `action-badge ${cls}`;

    equityLabelEl.textContent = data.label || "";

    equityBarEl.style.width      = `${Math.min(pct, 100)}%`;
    equityBarEl.style.background = ACTION_BARS[data.action] ?? "#374151";

    if (data.wins != null && data.total) {
      equityMetaEl.textContent =
        `${data.wins}V / ${data.ties}E / ${data.total - data.wins - data.ties}D  de ${data.total} sims`;
    }

    if (data.street) {
      equityStreetEl.textContent    = data.street;
      equityStreetEl.style.display  = "";
    } else {
      equityStreetEl.style.display  = "none";
    }
  }

  // ── Connection ────────────────────────────────────────────────────────────

  function setConnected(connected) {
    connStatusEl.classList.toggle("connected",    connected);
    connStatusEl.classList.toggle("disconnected", !connected);
    connLabelEl.textContent = connected ? "Conectado" : "Desconectado";
  }

  function resetDeck() { socket.emit("reset_deck"); }
  window.resetDeck = resetDeck;

  // ── Socket.IO ─────────────────────────────────────────────────────────────

  const socket = io("http://localhost:3000", { transports: ["polling", "websocket"] });

  socket.on("connect", () => setConnected(true));

  socket.on("disconnect", () => {
    setConnected(false);
    renderHandChips([]);
    renderBoardChips([]);
    renderDeck([]);
    renderEquity(null);
  });

  socket.on("deck_state",   cards          => renderDeck(Array.isArray(cards) ? cards : []));
  socket.on("hand_state",   ({ hand, board }) => {
    renderHandChips(Array.isArray(hand) ? hand : []);
    renderBoardChips(Array.isArray(board) ? board : []);
  });
  socket.on("equity_state", data           => renderEquity(data));
  socket.on("speak",        text           => speak(String(text)));
})();
