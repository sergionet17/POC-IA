# app.py
import os, json, csv, requests
from datetime import datetime
from fastapi import FastAPI, Request, Query
from openai import OpenAI

# ========= Config =========
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mipoc123")
WA_TOKEN     = os.getenv("WA_TOKEN")                 # token de WhatsApp
PHONE_ID     = os.getenv("PHONE_ID")                 # ej: 7160799...
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")         # clave de OpenAI
FALLBACK_TEMPLATE = os.getenv("FALLBACK_TEMPLATE")   # ej: hello_world o tu plantilla aprobada
FALLBACK_LANG     = os.getenv("FALLBACK_LANG", "en_US")  # es_ES, es_MX, en_US, etc.

app = FastAPI()

# ========= LLM (OpenAI) =========
client = OpenAI() if OPENAI_API_KEY else None

SYSTEM_PROMPT = (
    "Eres un asistente de un restaurante. "
    "Analiza el mensaje y devuelve SOLO JSON con esta forma exacta:\n"
    "{"
    "\"intent\":\"pedido|menu|promo|queja|otro\","
    "\"items\":[{\"nombre\":\"\",\"cantidad\":1,\"tamano\":\"\"}],"
    "\"notas\":\"\","
    "\"reply\":\"<texto que enviaremos al cliente>\""
    "}\n"
    "Si solo saludan, sugiere ver el menú o repetir el último pedido. "
    "Sé breve y amable."
)

def nlp_parse_and_reply(user_text: str, nombre: str = "") -> dict:
    if not client:
        # Sin clave de OpenAI: respuesta básica
        return {
            "intent": "otro",
            "items": [],
            "notas": "",
            "reply": f"¡Hola {nombre or ''}! 👋 ¿Quieres ver el menú o repetir tu último pedido?"
        }

    resp = client.responses.create(
        model="gpt-5-mini",       # rápido y económico para esta POC
        instructions=SYSTEM_PROMPT,
        input=f"Usuario: {nombre}\nMensaje: {user_text}\nDevuelve SOLO JSON."
    )
    out = resp.output_text
    try:
        data = json.loads(out)
    except Exception:
        data = {"intent": "otro", "items": [], "notas": "", "reply": out[:500]}
    return data

# ========= Utilidades WhatsApp =========
def wa_api_url():
    return f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"

def send_text(to_wa: str, body: str) -> requests.Response | None:
    if not (WA_TOKEN and PHONE_ID):
        print("Faltan WA_TOKEN o PHONE_ID en env.")
        return None
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_wa,
        "type": "text",
        "text": {"body": body}
    }
    r = requests.post(wa_api_url(), headers=headers, json=data, timeout=20)
    print("SEND TEXT RESP:", r.status_code, r.text)
    # Si estamos fuera de ventana de 24h, Meta responde 400 con error code 470
    try:
        if r.status_code == 400:
            err = r.json().get("error", {})
            if err.get("code") == 470 and FALLBACK_TEMPLATE:
                print("Fuera de 24h. Enviando plantilla fallback:", FALLBACK_TEMPLATE)
                send_template(to_wa, FALLBACK_TEMPLATE, FALLBACK_LANG)
    except Exception:
        pass
    return r

def send_template(to_wa: str, template_name: str, lang_code: str = "en_US"):
    if not (WA_TOKEN and PHONE_ID):
        print("Faltan WA_TOKEN o PHONE_ID en env.")
        return None
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": to_wa,
        "type": "template",
        "template": {"name": template_name, "language": {"code": lang_code}}
    }
    r = requests.post(wa_api_url(), headers=headers, json=data, timeout=20)
    print("SEND TEMPLATE RESP:", r.status_code, r.text)
    return r

def log_event_csv(wa_id: str, text: str, parsed: dict):
    try:
        row = [datetime.utcnow().isoformat(), wa_id, text, json.dumps(parsed, ensure_ascii=False)]
        with open("events.csv", "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
    except Exception as e:
        print("No se pudo escribir CSV:", e)

# ========= Webhook Verification (GET) =========
@app.get("/webhook")
def verify(
    mode: str = Query("", alias="hub.mode"),
    challenge: str = Query("", alias="hub.challenge"),
    verify_token: str = Query("", alias="hub.verify_token"),
):
    if mode == "subscribe" and verify_token == VERIFY_TOKEN:
        try:
            return int(challenge)
        except Exception:
            return challenge
    return {"error": "not verified"}

# ========= Webhook Messages (POST) =========
@app.post("/webhook")
async def receive(request: Request):
    body = await request.json()
    print("WEBHOOK BODY:", body)

    try:
        entry  = (body.get("entry") or [])[0]
        change = (entry.get("changes") or [])[0]
        value  = change.get("value", {})
        msgs   = value.get("messages", [])
        if not msgs:
            return {"status": "ok"}  # status updates / acks

        msg   = msgs[0]
        wa_id = msg.get("from")
        text  = (msg.get("text") or {}).get("body", "")
        nombre = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")

        # --- IA: parsear intención + generar respuesta ---
        parsed = nlp_parse_and_reply(text, nombre)

        # Guardar historial simple (CSV). Para DB cambia esta línea por INSERTs.
        log_event_csv(wa_id, text, parsed)

        # Enviar respuesta (si fuera de 24h, se activará plantilla fallback si configuraste FALLBACK_TEMPLATE)
        reply = parsed.get("reply") or "¡Listo!"
        send_text(wa_id, reply)

        return {"status": "ok"}
    except Exception as e:
        print("Error procesando webhook:", e)
        return {"status": "error", "detail": str(e)}
