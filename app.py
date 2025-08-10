# app.py
import os, json, csv, re, requests
from datetime import datetime
from fastapi import FastAPI, Request, Query
from groq import Groq

# ========= Config =========
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mipoc123")
WA_TOKEN     = os.getenv("WA_TOKEN")                 # token WhatsApp
PHONE_ID     = os.getenv("PHONE_ID")                 # ej: 7160799...
GROQ_API_KEY = os.getenv("GROQ_API_KEY")             # clave Groq
FALLBACK_TEMPLATE = os.getenv("FALLBACK_TEMPLATE")   # ej: hello_world
FALLBACK_LANG     = os.getenv("FALLBACK_LANG", "es_ES")

app = FastAPI()

# ========= IA: Groq =========
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

SYSTEM_PROMPT = (
    "Eres un asistente de un restaurante. Devuelve SOLO JSON con la forma exacta:\n"
    "{"
    "\"intent\":\"pedido|menu|promo|queja|saludo|otro\","
    "\"items\":[{\"nombre\":\"\",\"cantidad\":1,\"tamano\":\"\"}],"
    "\"notas\":\"\","
    "\"reply\":\"<texto breve y amable>\""
    "}\n"
    "Interpreta jerga coloquial. Si solo saludan: intent='saludo' y sugiere ver menú o repetir pedido."
)

def llm_parse(user_text: str, nombre: str = "") -> dict | None:
    """Parsea con Groq (Llama3-8B). Devuelve dict o None si falla/no hay API key."""
    if not groq_client:
        return None
    try:
        prompt = f"{SYSTEM_PROMPT}\nUsuario:{nombre}\nMensaje:{user_text}\nDevuelve SOLO JSON."
        r = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[
                {"role": "system", "content": "Devuelve JSON válido exactamente con el esquema indicado."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        print("Groq error:", e)
        return None

# ========= Reglas (backup) =========
GREET_RE = re.compile(r"\b(hola|buen[oa]s|qué tal|que tal|hello|hi)\b", re.I)
PROMO_RE = re.compile(r"\b(promo|promoción|descuento|oferta|cup[oó]n)\b", re.I)
QUEJA_RE = re.compile(r"\b(mal[o]|reclamo|queja|tarde|fr[ií]o|demorad[oa])\b", re.I)
MENU_RE  = re.compile(r"\b(men[uú]|carta|opciones|platos)\b", re.I)
PEDIDO_RE= re.compile(r"\b(pido|quiero|ordena[rs]?|trae|env[ií]a|llevar|domicilio)\b", re.I)

def rule_parse(txt: str) -> dict:
    t = txt.strip().lower()
    if GREET_RE.search(t):
        return {"intent":"saludo","items":[],"notas":"","reply":"¡Hola! 👋 ¿Quieres ver el menú o repetir tu último pedido?"}
    if QUEJA_RE.search(t):
        return {"intent":"queja","items":[],"notas":"","reply":"Lamento lo ocurrido. ¿Me das tu número de pedido y qué pasó para ayudarte? 🙏"}
    if PROMO_RE.search(t):
        return {"intent":"promo","items":[],"notas":"","reply":"Hoy tenemos combo 🍕 + 🥤 con 15% OFF. ¿Te lo envío?"}
    if MENU_RE.search(t):
        return {"intent":"menu","items":[],"notas":"","reply":"Menú: pizzas, hamburguesas, bebidas y postres. ¿Qué se te antoja?"}
    if PEDIDO_RE.search(t):
        return {"intent":"pedido","items":[],"notas":"","reply":"¡Perfecto! Dime producto, tamaño y cantidad. Ej: '2 hamburguesas grandes y 1 gaseosa'."}
    return {"intent":"otro","items":[],"notas":"","reply":"¿Te gustaría ver el menú, conocer las promos o hacer un pedido?"}

# ========= WhatsApp helpers =========
def wa_api_url():
    return f"https://graph.facebook.com/v22.0/{PHONE_ID}/messages"

def send_text(to_wa: str, body: str):
    if not (WA_TOKEN and PHONE_ID):
        print("Faltan WA_TOKEN o PHONE_ID.")
        return None
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to_wa,"type":"text","text":{"body":body}}
    r = requests.post(wa_api_url(), headers=headers, json=data, timeout=20)
    print("SEND TEXT RESP:", r.status_code, r.text)
    # fuera de 24h → plantilla fallback (code 470)
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
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to_wa,"type":"template",
            "template":{"name":template_name,"language":{"code":lang_code}}}
    r = requests.post(wa_api_url(), headers=headers, json=data, timeout=20)
    print("SEND TEMPLATE RESP:", r.status_code, r.text)
    return r

def log_event_csv(wa_id: str, text: str, parsed: dict):
    try:
        with open("events.csv","a",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow([datetime.utcnow().isoformat(), wa_id, text, json.dumps(parsed, ensure_ascii=False)])
    except Exception as e:
        print("No se pudo escribir CSV:", e)

# ========= Webhook verify =========
@app.get("/webhook")
def verify(
    mode: str = Query("", alias="hub.mode"),
    challenge: str = Query("", alias="hub.challenge"),
    verify_token: str = Query("", alias="hub.verify_token"),
):
    if mode == "subscribe" and verify_token == VERIFY_TOKEN:
        try: return int(challenge)
        except Exception: return challenge
    return {"error":"not verified"}

# ========= Webhook receive =========
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
            return {"status":"ok"}

        msg    = msgs[0]
        wa_id  = msg.get("from")
        text   = (msg.get("text") or {}).get("body", "")
        nombre = value.get("contacts", [{}])[0].get("profile", {}).get("name", "")

        # 1) IA con Groq → si falla, reglas
        parsed = llm_parse(text, nombre) or rule_parse(text)

        # 2) Guardar historial
        log_event_csv(wa_id, text, parsed)

        # 3) Responder
        reply = parsed.get("reply") or "¿Te ayudo con menú, promos o un pedido?"
        send_text(wa_id, reply)

        return {"status":"ok"}

    except Exception as e:
        print("Error procesando webhook:", e)
        return {"status":"error","detail":str(e)}
