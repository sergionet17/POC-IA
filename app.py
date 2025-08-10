# app.py
import os, json, csv, requests
from datetime import datetime
from fastapi import FastAPI, Request, Query

# ===== Config =====
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mipoc123")
WA_TOKEN     = os.getenv("WA_TOKEN")               # token WhatsApp
PHONE_ID     = os.getenv("PHONE_ID")               # p.ej. 7160799...
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")       # clave OpenAI (opcional)
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")         # clave Groq (opcional)
FALLBACK_TEMPLATE = os.getenv("FALLBACK_TEMPLATE") # p.ej. hello_world
FALLBACK_LANG     = os.getenv("FALLBACK_LANG", "es_ES")

app = FastAPI()

# ===== IA: OpenAI (opcional) =====
openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        openai_client = OpenAI()
    except Exception as e:
        print("OpenAI no disponible:", e)

# ===== IA: Groq (fallback gratis) =====
groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        print("Groq no disponible:", e)

SYSTEM_PROMPT = (
    "Eres un asistente de un restaurante. "
    "Devuelve SOLO JSON con esta forma exacta:\n"
    "{"
    "\"intent\":\"pedido|menu|promo|queja|otro\","
    "\"items\":[{\"nombre\":\"\",\"cantidad\":1,\"tamano\":\"\"}],"
    "\"notas\":\"\","
    "\"reply\":\"<texto breve para el cliente>\""
    "}\n"
    "Si solo saludan, sugiere ver el menú o repetir el último pedido. Sé amable y directo."
)

def nlp_parse_and_reply(user_text: str, nombre: str = "") -> dict:
    # 1) Intento OpenAI
    if openai_client:
        try:
            resp = openai_client.responses.create(
                model="gpt-5-mini",  # rápido y barato; cambia a gpt-4o si quieres
                instructions=SYSTEM_PROMPT,
                input=f"Usuario: {nombre}\nMensaje: {user_text}\nDevuelve SOLO JSON."
            )
            out = resp.output_text
            return json.loads(out)
        except Exception as e:
            print("OpenAI error, probando Groq:", e)

    # 2) Fallback Groq (gratis)
    if groq_client:
        try:
            prompt = (
                "Sigue las instrucciones al pie de la letra.\n\n" + SYSTEM_PROMPT +
                f"\nUsuario: {nombre}\nMensaje: {user_text}\nDevuelve SOLO JSON."
            )
            r = groq_client.chat.completions.create(
                model="llama3-8b-8192",  # o "llama3-70b-8192" si tienes cuota
                messages=[
                    {"role": "system", "content": "Eres preciso y devuelves JSON válido."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
            )
            out = r.choices[0].message.content
            return json.loads(out)
        except Exception as e:
            print("Groq error:", e)

    # 3) Fallback sin IA
    return {
        "intent": "otro",
        "items": [],
        "notas": "",
        "reply": f"¡Hola {nombre or ''}! 👋 ¿Quieres ver el menú o repetir tu último pedido?"
    }

# ===== Utilidades WhatsApp =====
def wa_api_url():
    return f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"

def send_text(to_wa: str, body: str):
    if not (WA_TOKEN and PHONE_ID):
        print("Faltan WA_TOKEN o PHONE_ID.")
        return None
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_wa, "type": "text", "text": {"body": body}}
    r = requests.post(wa_api_url(), headers=headers, json=data, timeout=20)
    print("SEND TEXT RESP:", r.status_code, r.text)

    # Si fuera de 24h (error code 470), intentar plantilla si configuraste una
    try:
        if r.status_code == 400:
            err = r.json().get("error", {})
            if err.get("code") == 470 and FALLBACK_TEMPLATE:
                print("Fuera de 24h → plantilla:", FALLBACK_TEMPLATE)
                send_template(to_wa, FALLBACK_TEMPLATE, FALLBACK_LANG)
    except Exception:
        pass
    return r

def send_template(to_wa: str, template_name: str, lang_code: str = "es_ES"):
    if not (WA_TOKEN and PHONE_ID):
        print("Faltan WA_TOKEN o PHONE_ID.")
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

# ===== Webhook verification (GET) =====
from fastapi import Query
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

# ===== Webhook messages (POST) =====
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
            return {"status": "ok"}  # statuses / acks

        msg    = msgs[0]
        wa_id  = msg.get("from")
        text   = (msg.get("text") or {}).get("body", "")
        nombre = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")

        # IA: parsear + generar respuesta
        parsed = nlp_parse_and_reply(text, nombre)

        # Log simple (dataset para entrenar luego)
        log_event_csv(wa_id, text, parsed)

        # Enviar respuesta (si fuera de 24h, cae a plantilla si la configuraste)
        reply = parsed.get("reply") or "¡Listo!"
        send_text(wa_id, reply)

        return {"status": "ok"}
    except Exception as e:
        print("Error procesando webhook:", e)
        return {"status": "error", "detail": str(e)}
