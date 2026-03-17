import json
import time

import cv2
import mss
import numpy as np
import websocket


WEBSOCKET_URL = "ws://localhost:3000"

# Região cirúrgica da tela para a carta 1 (coordenadas em pixels).
CARD_1_REGION = {
    "top": 781,
    "left": 1286,
    "width": 15,
    "height": 18,
}


def process_frame(sct: mss.mss) -> list[str]:
    """
    Captura apenas a região da carta 1, exibe em uma janela de debug
    e retorna as cartas detectadas (ainda mockadas).
    """
    screenshot = sct.grab(CARD_1_REGION)

    # mss retorna BGRA; convertemos para BGR para o OpenCV.
    img_bgra = np.array(screenshot)
    img_bgr = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

    cv2.imshow("Olho do Bot - Carta 1", img_bgr)
    cv2.waitKey(1)

    # Placeholder: em breve será substituído por template matching real.
    return ["Ah", "Kd"]


def on_open(ws: websocket.WebSocketApp) -> None:
    """
    Callback de abertura do WebSocket.
    Mantém um loop infinito capturando a região da carta,
    processando e enviando o estado ao servidor.
    """
    with mss.mss() as sct:
        try:
            while True:
                detected_cards = process_frame(sct)

                payload = {
                    "event": "update_hands",
                    "payload": detected_cards,
                }

                ws.send(json.dumps(payload))
                time.sleep(2)
        except KeyboardInterrupt:
            pass
        finally:
            cv2.destroyAllWindows()
            ws.close()


def main() -> None:
    ws_app = websocket.WebSocketApp(
        WEBSOCKET_URL,
        on_open=on_open,
    )

    # run_forever bloqueia e mantém a conexão ativa.
    ws_app.run_forever()


if __name__ == "__main__":
    main()

