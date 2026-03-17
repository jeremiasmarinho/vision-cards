(() => {
  const statusEl = document.getElementById("connection-status");
  const statusLabelEl = statusEl.querySelector(".status-label");
  const deckCountEl = document.getElementById("deck-count");
  const cardsContainerEl = document.getElementById("cards-container");
  const emptyStateEl = document.getElementById("empty-state");

  function setConnected(isConnected) {
    statusEl.classList.toggle("connected", isConnected);
    statusEl.classList.toggle("disconnected", !isConnected);
    statusLabelEl.textContent = isConnected ? "Conectado" : "Desconectado";
  }

  function renderDeck(cards) {
    cardsContainerEl.innerHTML = "";

    if (!cards || cards.length === 0) {
      emptyStateEl.style.display = "block";
      deckCountEl.textContent = "0";
      return;
    }

    emptyStateEl.style.display = "none";
    deckCountEl.textContent = String(cards.length);

    cards.forEach((code) => {
      const chip = document.createElement("div");
      chip.className = "card-chip";
      chip.textContent = code;
      cardsContainerEl.appendChild(chip);
    });
  }

  const socket = io("http://localhost:3000", {
    transports: ["websocket"],
  });

  socket.on("connect", () => {
    setConnected(true);
  });

  socket.on("disconnect", () => {
    setConnected(false);
    renderDeck([]);
  });

  socket.on("deck_state", (cards) => {
    renderDeck(Array.isArray(cards) ? cards : []);
  });
})();

