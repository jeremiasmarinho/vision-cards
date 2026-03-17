import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    print(f"[{data['event']}] {data['payload']}")

def on_open(ws):
    print("Ligado ao servidor. A aguardar mensagens...\n")

websocket.WebSocketApp(
    "ws://localhost:3000",
    on_message=on_message,
    on_open=on_open,
).run_forever()
