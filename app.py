# app.py
import os
import requests
from fastapi import FastAPI, Request, Query

# ===== Config =====
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mipoc123")
WA_TOKEN     = os.getenv("WA_TOKEN")                    # tu token de Meta
PHONE_ID     = os.getenv("PHONE_ID")                    # ej: 716079928258789

app = FastAPI()

# ===== Webhook verification (GET) =====
@app.get("/webhook")
def verify(
    mode: str = Query("", alias="hub.mode"),
    challenge: str = Query("", alias="hub.challenge"),
    verify_token: str = Query("", alias="hub.verify_token"),
):
    if mode == "subscribe" and verify_token == VERIFY_TOKEN:
        # Meta espera que devolvamos el challenge tal cual
        try:
            return int(challenge)
        except Exception:
            return challenge
    return {"error": "not verified"}

# ===== Receive messages (POST) =====
@app.post("/webhook")
async def receive(request: Request):
    body = await request.json()
    print("WEBHOOK BODY:", body)  # log útil en Render

    try:
        entry   = (body.get("entry") or [])[0]
        change  = (entry.get("changes") or [])[0]
        value   = change.get("value", {})
        msgs    = value.get("messages", [])
        if not msgs:
            # Son status / acks / delivery receipts; no hay que responder
            return {"status": "ok"}

        msg  = msgs[0]
        wa_id = msg.get("from")
        text  = (msg.get("text") or {}).get("body", "")

        # Respuesta simple (eco). OJO: solo llega si estás dentro de 24h.
        reply = f"Recibido ✅: {text}"
        send_text(wa_id, reply)

        return {"status": "ok"}

    except Exception as e:
        print("Error procesando webhook:", e)
        return {"status": "error", "detail": str(e)}

# ===== Send helper =====
def send_text(to_wa: str, body: str):
    if not (WA_TOKEN and PHONE_ID):
        print("Faltan WA_TOKEN o PHONE_ID en variables de entorno.")
        return

    url = f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_wa,
        "type": "text",
        "text": {"body": body},
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=20)
        print("SEND RESP:", r.status_code, r.text)
    except Exception as e:
        print("Error enviando mensaje:", e)
