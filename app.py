import os
import re
import json
import requests
from flask import Flask, request
from groq import Groq

# ================== CONFIG ==================
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mipoc123")
WA_TOKEN = os.getenv("WA_TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Menú básico
MENU = {
    "pizza": {"precio": {"personal": 15000, "mediana": 32000, "grande": 45000}, "tamanos": ["personal", "mediana", "grande"]},
    "hamburguesa": {"precio": {"sencilla": 12000, "doble": 18000}, "tamanos": ["sencilla", "doble"]},
    "gaseosa": {"precio": {"350ml": 3000, "1.5l": 8000}, "tamanos": ["350ml", "1.5l"]}
}

ALIASES = {
    "coca cola": "gaseosa",
    "coca": "gaseosa",
    "cola": "gaseosa"
}

# Sesiones simples en memoria
SESSIONS = {}  # wa_id -> {"pending": {...}}

def get_session(wa_id):
    return SESSIONS.setdefault(wa_id, {"pending": None})

def set_pending_size(wa_id, item):
    SESSIONS[wa_id] = {"pending": {"field": "size", "item": item}}

def clear_pending(wa_id):
    if wa_id in SESSIONS:
        SESSIONS[wa_id]["pending"] = None

# Regex auxiliares
ONLY_SIZE_RE = re.compile(r"^(personal|mediana|grande|sencilla|doble|350ml|1\.5l|pequeñas|grandes)$", re.I)
YES_RE = re.compile(r"^(si|sí|claro|dale)$", re.I)
NO_RE = re.compile(r"^(no|cancelar)$", re.I)

# ================== IA (Groq) ==================
client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = (
    "Eres un asistente de restaurante. Devuelve SOLO JSON con:\n"
    "{"
    "\"intent\":\"pedido|menu|promo|queja|saludo|otro\","
    "\"items\":[{\"nombre\":\"\",\"cantidad\":1,\"tamano\":\"\"}],"
    "\"notas\":\"\","
    "\"reply\":\"\""
    "}\n"
    "No inventes tamaños ni productos. Si faltan, deja \"tamano\":\"\"."
)

def norm_item(nombre):
    nombre = nombre.lower()
    if nombre in MENU:
        return nombre
    if nombre in ALIASES:
        return ALIASES[nombre]
    return nombre

def coerce_items(items):
    fixed = []
    for it in items:
        nombre = norm_item(it.get("nombre", ""))
        qty = int(it.get("cantidad", 1))
        tam = it.get("tamano", "").lower()
        fixed.append({"nombre": nombre, "cantidad": qty, "tamano": tam})
    return fixed

def parse_with_llm(text):
    fewshots = [
        ("Quiero 2 hamburguesas dobles y una coca cola 1.5L",
         {"intent":"pedido","items":[
            {"nombre":"hamburguesa","cantidad":2,"tamano":"doble"},
            {"nombre":"gaseosa","cantidad":1,"tamano":"1.5l"}],
          "notas":"","reply":"Recibido: 2 hamburguesas dobles y 1 gaseosa 1.5l. ¿Algo más?"}),
        ("La pizza",
         {"intent":"pedido","items":[{"nombre":"pizza","cantidad":1,"tamano":""}],
          "notas":"","reply":"¿Qué tamaño para la pizza? personal/mediana/grande."})
    ]
    examples = "\n".join([f"Usuario: {u}\nSalida: {json.dumps(j,ensure_ascii=False)}" for u,j in fewshots])

    prompt = f"""{SYSTEM_PROMPT}

Ejemplos:
{examples}

Usuario: {text}
Salida:"""

    resp = client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role":"user","content":prompt}],
        temperature=0.1
    )
    out = resp.choices[0].message.content.strip()
    try:
        return json.loads(out)
    except:
        return {"intent":"otro","items":[],"notas":"","reply":out}

# ================== WhatsApp ==================
def send_text(to, body):
    url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":body}}
    r = requests.post(url, headers=headers, json=data)
    return r.status_code, r.text

# ================== Flask app ==================
app = Flask(__name__)

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "error", 403

@app.route("/webhook", methods=["POST"])
def receive():
    body = request.get_json()

    # Extraer datos de forma segura
    entry_list = body.get("entry") or []
    if not entry_list or not isinstance(entry_list[0], dict):
        return {"status": "ok"}

    change_list = entry_list[0].get("changes") or []
    if not change_list or not isinstance(change_list[0], dict):
        return {"status": "ok"}

    value = change_list[0].get("value") or {}
    msgs = value.get("messages") or []
    if not msgs or not isinstance(msgs[0], dict):
        return {"status": "ok"}

    msg = msgs[0]
    wa_id = msg.get("from")
    contacts = value.get("contacts") or []
    nombre = ""
    if contacts and isinstance(contacts[0], dict):
        nombre = (contacts[0].get("profile") or {}).get("name", "") or ""

    text = ""
    mtype = msg.get("type")
    if mtype == "text":
        text = (msg.get("text") or {}).get("body", "") or ""
    elif mtype == "interactive":
        inter = msg.get("interactive") or {}
        text = (inter.get("button_reply") or {}).get("title") or (inter.get("list_reply") or {}).get("title") or ""
    elif mtype == "button":
        text = (msg.get("button") or {}).get("text", "") or ""
    else:
        text = (msg.get("text") or {}).get("body", "") or ""
    text = text.strip()
    if not text:
        return {"status": "ok"}

    # Manejo de estado
    sess = get_session(wa_id)
    pending = sess.get("pending") or {}

    # Si espera tamaño
    m_size = ONLY_SIZE_RE.match(text.lower())
    if pending.get("field") == "size" and m_size:
        size = m_size.group(1).lower()
        item = pending["item"]
        item["tamano"] = size
        clear_pending(wa_id)
        items = coerce_items([item])
        n, t, q = items[0]["nombre"], items[0]["tamano"], items[0]["cantidad"]
        precio = MENU[n]["precio"].get(t, 0)
        total = precio * q
        reply = f"Perfecto: {q}x {n} {t}. Total aprox ${total:,.0f}. ¿Confirmas el pedido? (sí/no)"
        send_text(wa_id, reply)
        return {"status": "ok"}

    if YES_RE.match(text.lower()):
        clear_pending(wa_id)
        send_text(wa_id, "¡Pedido confirmado! 🧾 En breve te llegará el resumen.")
        return {"status": "ok"}
    if NO_RE.match(text.lower()):
        clear_pending(wa_id)
        send_text(wa_id, "Entendido. ¿Quieres ver el menú?")
        return {"status": "ok"}

    # IA
    parsed = parse_with_llm(text)
    intent = parsed.get("intent", "otro")
    items = coerce_items(parsed.get("items", []))
    reply = parsed.get("reply", "")

    # Si es pedido y falta tamaño
    faltan = [it for it in items if it["nombre"] in MENU and MENU[it["nombre"]]["tamanos"] and not it["tamano"]]
    if intent == "pedido" and faltan:
        set_pending_size(wa_id, faltan[0])
        opciones = " / ".join(MENU[faltan[0]["nombre"]]["tamanos"])
        reply = f"¿Qué tamaño para {faltan[0]['nombre']}? Opciones: {opciones}."

    send_text(wa_id, reply or "No entendí bien, ¿puedes repetirlo?")
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(port=5000)
