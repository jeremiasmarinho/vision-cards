"""
start_3instancias.py — Inicia 3 vision-workers, um para cada mesa.
====================================================================
Lê delta_x e delta_y de config.ini (InstanciasCalibracao) e lança:
  - Worker 1 (P1): offset 0, 0
  - Worker 2 (P2): offset delta_x, delta_y
  - Worker 3 (P3): offset 2*delta_x, 2*delta_y

Execute calibra_instancias.py antes para calibrar e salvar os offsets.

Uso: python start_3instancias.py [--fg]
  Sem --fg: abre 3 janelas CMD separadas e sai.
  Com --fg: mantém no terminal atual (Ctrl+C para encerrar todos).
"""

import configparser
import os
import subprocess
import sys

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")
MAIN_PY = os.path.join(os.path.dirname(__file__), "main.py")

CREATE_NEW_CONSOLE = 0x00000010  # Windows: nova janela CMD


def main():
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="utf-8")

    delta_x = 400
    delta_y = 0
    if "InstanciasCalibracao" in parser:
        try:
            delta_x = int(parser["InstanciasCalibracao"].get("delta_x", "400"))
            delta_y = int(parser["InstanciasCalibracao"].get("delta_y", "0"))
        except ValueError:
            pass

    fg = "--fg" in sys.argv
    creation_flags = CREATE_NEW_CONSOLE if (sys.platform == "win32" and not fg) else 0

    procs = []
    for i in range(3):
        ox = i * delta_x
        oy = i * delta_y
        cmd = [
            sys.executable, MAIN_PY,
            "--player", str(i + 1),
            "--server", "ws://localhost:3000/ws",
            "--offset-x", str(ox),
            "--offset-y", str(oy),
        ]
        p = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(MAIN_PY),
            creationflags=creation_flags,
        )
        procs.append(p)
        print(f"[OK] Instância {i+1} (P{i+1}) — offset=({ox}, {oy})")

    if fg:
        print("\n3 workers rodando. Ctrl+C para encerrar.")
        try:
            for p in procs:
                p.wait()
        except KeyboardInterrupt:
            for p in procs:
                p.terminate()
            print("Encerrado.")
    else:
        print("\n3 janelas abertas. Feche-as manualmente para encerrar.")
