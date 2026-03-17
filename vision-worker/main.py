import json
import time
from typing import List

import mss
import numpy as np
import websocket


WEBSOCKET_URL = "ws://localhost:3000"


def process_frame(frame: np.ndarray) -> List[str]:
    """
    Função mock de processamento de frame.
    Simula a detecção de uma mão de cartas.
    """
    _ = frame  # placeholder para uso futuro
    return ["Ah", "Kd"]


def capture_main_monitor() -> np.ndarray:
    """
    Captura uma imagem do monitor principal.
    """
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # monitor principal
        screenshot = sct.grab(monitor)
        img = np.array(screenshot)
    return img


def main() -> None:
    ws = websocket.WebSocket()
    ws.connect(WEBSOCKET_URL)

    # Envia estado inicial/hand a cada 2 segundos.
    try:
        while True:
            frame = capture_main_monitor()
            detected_cards = process_frame(frame)

            message = {
                "event": "update_hands",
                "payload": detected_cards,
            }

            ws.send(json.dumps(message))
            time.sleep(2.0)
    except KeyboardInterrupt:
        pass
    finally:
        ws.close()


if __name__ == "__main__":
    main()

