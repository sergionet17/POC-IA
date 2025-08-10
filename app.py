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
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ========= Dominio (menú) =========
MENU = {
    "pizza": {
        "tamanos": ["personal","mediana","grande"],
        "precio":  {"personal":18000,"mediana":32000,"grande":42000}
    },
    "hamburguesa": {
        "tamanos": ["sencilla","doble"],
        "precio":  {"sencilla":23000,"doble":29000}
    },
    "gaseosa": {
        "tamanos": ["350ml","1.5l"],
        "precio":  {"350ml":6000,"1.5l":9000}
    },
    "papas": {
        "tamanos": ["pequeñas","grandes"],
        "precio":  {"pequeñas":8000,"grandes":12000}
    },
}
ALIASES = {
    "coca cola":"gaseosa","coca-cola":"gaseosa","coca":"gaseosa","refresco":"gaseosa",
    "burger":"hamburguesa","hamburguesas":"hamburguesa","pizzas":"pizza"
}
def norm_item(nombre:str)->str:
    n = (nombre or "").strip().lower()
    return ALIASES.get(n, n)

QTY_RE  = re.compile(r"(\d+)")
SIZE_RE = re.compile(r"\b(personal|mediana|grande|sencilla|doble|350ml|1\.5l|pequeñas|grandes)\b", re.I)

def coerce_items(items:list)->list:
    """Corrige nombres, cantidades y tamaños contra el MENÚ."""
    fixed=[]
    for it in items or []:
        nombre = norm_item(it.get("nombre",""))
        qty    = it.get("cantidad") or 1
        tam    = (it.get("tamano") or "").lower().strip()

        # cantidad desde texto si vino rara
        if isinstance(qty, str):
            m = QTY_RE.search(qty)
            qty = int(m.group(1)) if m else 1

        # si no hay tamaño, intenta sacarlo del nombre ("pizza grande")
        if not tam:
            m = SIZE_RE.search(nombre)
            if m:
                tam = m.group(1).lower()
                nombre = norm_item(SIZE_RE.sub("", nombre)).strip()

        # valida tamaño contra menú
        if nombre in MENU and tam and tam not in MENU[nombre]["tamanos"]:
            tam = ""  # tamaño inválido → pedir confirmación luego

        fixed.append({"nombre": nombre, "cantidad": max(1, int(qty)), "tamano": tam})
    return fixed

# ========= IA (Groq) =========
SYSTEM_PROMPT = (
    "Eres un asistente de restaurante. Extrae intención e items y RESPONDE SOLO JSON válido.\n"
    "Esquema:\n"
    "{"
    "\"intent\":\"pedido|menu|promo|queja|saludo|otro\","
    "\"items\":[{\"nombre\":\"\",\"cantidad\":1,\"tamano\":\"\"}],"
    "\"notas\":\"\","
    "\"reply\":\"\""
    "}\n"
    "Reglas: no inventes tamaños ni productos; si faltan, deja \"tamano\":\"\". "
    "Si solo saludan, intent='saludo'. Sé breve y amable."
)
FEWSHOTS = [
    {"user":"Quiero 2 hamburguesas dobles y una coca cola 1.5L",
     "json":{"intent":"pedido","items":[
        {"nombre":"hamburguesa","cantidad":2,"tamano":"doble"},
        {"nombre":"gaseosa","cantidad":1,"tamano":"1.5l"}],
        "notas":"","reply":"Recibido: 2 hamburguesas dobles y 1 gaseosa 1.5l. ¿Algo más?"}},
    {"user":"hola",
     "json":{"intent":"saludo","items":[],"notas":"","reply":"¡Hola! ¿Quieres ver el menú o repetir tu último pedido?"}},
    {"user":"qué promos hay?",
     "json":{"intent":"promo","items":[],"notas":"","reply":"Tenemos combo pizza + gaseosa con 15% OFF. ¿Te lo envío?"}},
]

def llm_parse(user_text:str, nombre:str="")->dict|None:
    if not groq_client:
        return None
    try:
        examples = "\n".join([
            f"Usuario: {e['user']}\nSalida: {json.dumps(e['json'], ensure_ascii=False)}"
            for e in FEWSHOTS
        ])
        prompt = f"{SYSTEM_PROMPT}\n\nEjemplos:\n{examples}\n\nUsuario:{nombre}\nMensaje:{user_text}\nSalida:"
        r = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role":"user","content":prompt}],
            temperature=0.1,  # determinista
        )
        out = r.choices[0].message.content.strip()
        return json.loads(out)
    except Exception as e:
        print("Groq error:", e)
        return None

# ========= Reglas (backup si IA falla) =========
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
        return {"intent":"menu","items":[],"notas":"","reply":"Menú: pizza, hamburguesa, gaseosa y papas. ¿Qué te antoja?"}
    if PEDIDO_RE.search(t):
        return {"intent":"pedido","items":[],"notas":"","reply":"¡Perfecto! Dime producto, tamaño y cantidad. Ej: '2 hamburguesas dobles y 1 gaseosa 350ml'."}
    return {"intent":"otro","items":[],"notas":"","reply":"¿Te ayudo con menú, promos o un pedido?"}

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
    # fuera de 24h → plantilla
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

# ========= Lógica de respuesta =========
def build_business_reply(parsed:dict)->str:
    intent = parsed.get("intent","otro")
    items  = coerce_items(parsed.get("items",[]))

    # Si es pedido, verificar tamaños y calcular total
    if intent=="pedido" and items:
        faltan = [it for it in items if it["nombre"] in MENU and MENU[it["nombre"]]["tamanos"] and not it["tamano"]]
        if faltan:
            nombres = ", ".join({it["nombre"] for it in faltan})
            # sugerir opciones del primero que falte
            opciones = " / ".join(MENU[faltan[0]["nombre"]]["tamanos"])
            return f"¿Qué tamaño para {nombres}? Opciones: {opciones}."

        total = 0
        lineas=[]
        for it in items:
            n,t,q = it["nombre"], it["tamano"], it["cantidad"]
            if n in MENU:
                precio = MENU[n]["precio"].get(t, 0)
                total += precio * q
                lineas.append(f"{q}x {n} {t}".strip())
            else:
                lineas.append(f"{q}x {n}".strip())
        det = ", ".join(lineas)
        return f"Confirmo: {det}. Total aprox ${total:,.0f}. ¿Confirmas el pedido? (sí/no)"

    # Otras intenciones
    if intent=="menu":
        return "Menú: pizza (personal/mediana/grande), hamburguesa (sencilla/doble), gaseosa (350ml/1.5L) y papas (pequeñas/grandes). ¿Qué te antoja?"
    if intent=="promo":
        return "Hoy: combo pizza mediana + gaseosa 350ml con 15% OFF. ¿Te lo envío?"
    if intent=="queja":
        return "Lamento lo ocurrido. ¿Me compartes tu número de pedido y qué pasó para ayudarte?"
    if intent=="saludo":
        return "¡Hola! ¿Quieres ver el menú o repetir tu último pedido?"

    return parsed.get("reply") or "¿Te ayudo con menú, promos o hacer un pedido?"

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

        # 1) IA → si falla, reglas
        parsed = llm_parse(text, nombre) or rule_parse(text)

        # 2) Guardar historial
        log_event_csv(wa_id, text, parsed)

        # 3) Construir respuesta con lógica de negocio
        reply = build_business_reply(parsed)

        # 4) Enviar
        send_text(wa_id, reply)
        return {"status":"ok"}

    except Exception as e:
        print("Error procesando webhook:", e)
        return {"status":"error","detail":str(e)}
