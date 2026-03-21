"""
overlay.py — Janelas de overlay Tkinter arrastáveis.
Porta direta do gui_widgets.py extraído do sistema original.
"""

import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk


class DraggableOverlayWindow(tk.Toplevel):
    """Janela topmost, sem borda, que pode ser arrastada pelo usuário."""

    def __init__(self, master, overlay_type: str, title: str = ""):
        super().__init__(master)
        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        self.config(bg="white")
        self.overlay_type = overlay_type
        self._start_x: int | None = None
        self._start_y: int | None = None

        self.main_frame = tk.Frame(self, bg="white")
        self.main_frame.pack(fill="both", expand=True)

        self.bind("<ButtonPress-1>",  self._start_move)
        self.bind("<B1-Motion>",      self._on_motion)
        self.bind("<ButtonRelease-1>",self._stop_move)

    # ── Drag ─────────────────────────────────────────────────────────────────

    def _start_move(self, event):
        self.master.pause_monitoring()
        self._start_x = event.x
        self._start_y = event.y

    def _on_motion(self, event):
        x = self.winfo_pointerx() - self._start_x
        y = self.winfo_pointery() - self._start_y
        self.geometry(f"+{x}+{y}")

    def _stop_move(self, event):
        self.master.resume_monitoring()
        self.master.save_overlay_position(self.overlay_type,
                                          self.winfo_x(), self.winfo_y())

    # ── Conteúdo ──────────────────────────────────────────────────────────────

    def _clear(self):
        for w in self.main_frame.winfo_children():
            w.destroy()

    def _bind_drag(self, widget):
        widget.bind("<ButtonPress-1>",  self._start_move)
        widget.bind("<B1-Motion>",      self._on_motion)
        widget.bind("<ButtonRelease-1>",self._stop_move)

    def update_cards(self, results: list[dict]):
        """Exibe cartas coloridas por naipe."""
        self._clear()
        color_map = {"h": "red", "d": "blue", "c": "#32CD32", "s": "black"}
        for card_info in results:
            rank = card_info.get("rank", "") or "?"
            suit = card_info.get("suit", "")
            display = rank if rank != "_" else ""
            fg = color_map.get(suit, "gray")
            lbl = tk.Label(self.main_frame, text=display,
                           font=("Arial", 14, "bold"), fg=fg, bg="white")
            lbl.pack(side=tk.LEFT, padx=0)
            self._bind_drag(lbl)
            lbl.bind("<Button-3>",
                     lambda e, c=card_info: self.master.handle_card_click(e, c))

    def update_text(self, text: str, bg_color: str = "black", text_color: str = "white"):
        """Exibe texto simples (ex: força/equidade)."""
        self._clear()
        lbl = tk.Label(self.main_frame, text=text,
                       font=("Arial", 14, "bold"), fg=text_color, bg=bg_color)
        lbl.pack(side=tk.LEFT, padx=5, pady=2)
        self._bind_drag(lbl)

    def update_tip(self, text: str, color: str = "black"):
        """Exibe dica/conselho."""
        self._clear()
        lbl = tk.Label(self.main_frame, text=text,
                       font=("Arial", 10), fg=color, bg="#FFFFAA",
                       wraplength=160, justify=tk.CENTER)
        lbl.pack(side=tk.LEFT, padx=5, pady=2, fill=tk.X, expand=True)
        self._bind_drag(lbl)


# ── Janela de cartas vivas ────────────────────────────────────────────────────

class JanelaCartasVivas(tk.Toplevel):
    """Mapa 4×13 mostrando quais cartas do baralho ainda estão vivas."""

    NAIPES = ("s", "h", "c", "d")
    RANKS  = ("2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A")
    CORES  = {"s": "black", "h": "red", "c": "#32CD32", "d": "blue"}

    def __init__(self, master):
        super().__init__(master)
        self.title("Cartas Vivas")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.configure(bg="white")

        self.frame_grid = tk.Frame(self, bg="white")
        self.frame_grid.pack(padx=2, pady=1)

        self.mapa_labels: dict[str, tk.Label] = {}
        self.cartas_selecionadas: set[str] = set()

        for i, naipe in enumerate(self.NAIPES):
            for j, rank in enumerate(self.RANKS):
                card = f"{rank}{naipe}"
                txt  = "10" if rank == "T" else rank
                cor  = self.CORES[naipe]
                lbl  = tk.Label(self.frame_grid, text=txt,
                                font=("Consolas", 12, "bold"),
                                fg=cor, bg="white", width=3, cursor="hand2")
                lbl.grid(row=i, column=j, padx=0, pady=0)
                lbl.bind("<Button-1>", lambda e, c=card: self.toggle_selecao(c))
                self.mapa_labels[card] = lbl

    def toggle_selecao(self, card: str):
        if card in self.cartas_selecionadas:
            self.cartas_selecionadas.discard(card)
        else:
            self.cartas_selecionadas.add(card)
        self._atualizar_visual(card)
        if hasattr(self.master, "on_carta_viva_clicada"):
            self.master.on_carta_viva_clicada(card)

    def limpar_selecoes(self):
        self.cartas_selecionadas.clear()
        for card in self.mapa_labels:
            self._atualizar_visual(card)

    def _atualizar_visual(self, card: str):
        lbl   = self.mapa_labels.get(card)
        if lbl is None:
            return
        naipe = card[-1]
        if card in self.cartas_selecionadas:
            lbl.config(fg=self.CORES.get(naipe, "black"), bg="#FFFF99")
        elif lbl.cget("fg") == "#E0E0E0":
            lbl.config(fg="#E0E0E0", bg="white")
        else:
            lbl.config(fg=self.CORES.get(naipe, "black"), bg="white")

    def atualizar_cartas_vivas(self, cartas_mortas: set[str]):
        """Pinta de cinza as cartas mortas; restaura as vivas."""
        for c in list(self.cartas_selecionadas):
            if c in cartas_mortas:
                self.cartas_selecionadas.discard(c)

        for naipe in self.NAIPES:
            for rank in self.RANKS:
                card = f"{rank}{naipe}"
                lbl  = self.mapa_labels.get(card)
                if lbl is None:
                    continue
                if card in cartas_mortas:
                    lbl.config(fg="#E0E0E0", bg="white")
                elif card in self.cartas_selecionadas:
                    lbl.config(fg=self.CORES.get(naipe, "black"), bg="#FFFF99")
                else:
                    lbl.config(fg=self.CORES.get(naipe, "black"), bg="white")


# ── Diálogo de correção de template ──────────────────────────────────────────

def prompt_for_template(app, card_info: dict, save_fn):
    """Diálogo para o usuário corrigir um rank lido errado."""
    win = tk.Toplevel(app)
    win.title("Corrigir Carta")
    win.geometry("320x200")
    win.attributes("-topmost", True)
    win.lift()
    win.focus_force()

    tk.Label(win, text="Qual é este rank?").pack(pady=(20, 5))

    img = card_info.get("image")
    if img is not None:
        try:
            photo = img.resize((60, 48), Image.NEAREST)
            photo = ImageTk.PhotoImage(photo)
            il = tk.Label(win, image=photo)
            il.image = photo
            il.pack(pady=(0, 5))
        except Exception:
            pass

    frame = tk.Frame(win)
    frame.pack(pady=(0, 5), padx=5)

    ranks = [("A","A"),("K","K"),("Q","Q"),("J","J"),("10","T"),
             ("9","9"),("8","8"),("7","7"),("6","6"),
             ("5","5"),("4","4"),("3","3"),("2","2")]

    def on_rank(val):
        try:
            if img is None:
                messagebox.showerror("Erro", "Sem imagem para salvar.", parent=win)
                return
            save_fn(img, val)
            win.destroy()
        except Exception as exc:
            messagebox.showerror("Erro", str(exc), parent=win)

    row1 = tk.Frame(frame); row1.pack(pady=2)
    row2 = tk.Frame(frame); row2.pack(pady=2)
    for i, (disp, val) in enumerate(ranks):
        p = row1 if i < 7 else row2
        tk.Button(p, text=disp, width=3, height=2,
                  command=lambda v=val: on_rank(v)).pack(side=tk.LEFT, padx=2)

    tk.Button(win, text="Cancelar", command=win.destroy).pack(side=tk.BOTTOM, pady=5)
    win.transient(app)
    win.grab_set()
    win.wait_window()
