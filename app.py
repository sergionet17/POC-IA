import os, requests
from fastapi import FastAPI, Request

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mipoc123")
WA_TOKEN = os.getenv("WA_TOKEN")           # Pega aquí un token válido (o usa --set-env-vars al desplegar)
PHONE_ID   = os.getenv("PHONE_ID")         # 716079928258789 (ejemplo)

app = FastAPI()

@app.get("/webhook")
def verify(mode: str = "", challenge: str = "", verify_token: str = ""):
    # Meta valida el webhook haciendo GET con challenge
    if mode == "subscribe" and verify_token == VERIFY_TOKEN:
        try:
            return int(challenge)
        except Exception:
            return challenge
    return {"error": "not verified"}

@app.post("/webhook")
async def receive(request: Request):
    body = await request.json()
    try:
        entry = body.get("entry", [])[0].get("changes", [])[0].get("value", {})
        messages = entry.get("messages", [])
        if not messages:
            return {"status": "ok"}  # son acks o status updates

        msg = messages[0]
        wa_id = msg.get("from")
        text = (msg.get("text") or {}).get("body", "")

        reply = f"Recibido ✅: {text}"
        send_text(wa_id, reply)

    except Exception as e:
        print("Error procesando webhook:", e)

    return {"status": "ok"}

def send_text(to, body):
    url = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body}
    }
    try:
        requests.post(url, headers=headers, json=data, timeout=15)
    except Exception as e:
        print("Error enviando mensaje:", e)
