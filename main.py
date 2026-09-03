from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from chat import router as chat_router
import re

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ZONES = [
    "Santa Rosa","Sta. Rosa","Los Baños","San Pablo","San Pedro",
    "Santa Cruz","Sta. Cruz","Calamba","Cabuyao","Biñan","Calauan",
    "Alaminos","Victoria","Pila","Bay","Nagcarlan","Liliw","Lumban","Pagsanjan"
]

class ParseRequest(BaseModel):
    text: str

def clean(v):
    if not v: return None
    v = v.replace(":", "").replace("-", "").strip()
    if not v or v.lower() in ["n/a", "na", "none", "-", "wala"]: return None
    return v

def grab(text, pattern):
    m = re.search(pattern, text, re.IGNORECASE)
    return clean(m.group(1)) if m else None

def find_phone(text):
    text = re.sub(r'[\s\-]', '', text)
    m = re.search(r'(?:\+?63|0)\d{10}', text)
    if not m: return None
    r = m.group()
    if r.startswith("+63"): r = "0" + r[3:]
    elif r.startswith("63") and len(r) == 12: r = "0" + r[2:]
    return r

def find_zone(addr):
    if not addr: return None
    for z in ZONES:
        if z.lower() in addr.lower():
            return z
    return None

@app.post("/parse")
def parse_order(req: ParseRequest):
    text = req.text
    if not text.strip():
        return {"error": "No text provided"}

    lines = text.split("\n")
    si = re.search(r'sender\s*(details|name)', text, re.I)
    head = text[:si.start()] if si else text
    tail = text[si.start():] if si else ""

    date = grab(head, r'delivery\s*date\s*[:\-\t]+\s*(.+)')
    if date:
        date = re.split(r'\t|time\s*[\(\[]', date, flags=re.I)[0].strip()

    time_ = grab(head, r'time[^:]*[:\-\t]+\s*(.+)')
    if time_:
        time_ = re.sub(r'^[^)]*\)\s*:?\s*', '', time_).strip() or None

    addr = grab(head, r'(?:complete\s*)?address\s*[:\-\t]+\s*(.+)')

    total = None
    tm = re.search(r'total\s*[:\-]?\s*[₱P]?\s*([\d,]+)', text, re.I)
    if tm:
        total = "₱" + tm.group(1).replace(",", "")
    else:
        pm = re.search(r'^[₱P]?([\d,]+)\s*[-–]', text, re.M)
        if pm:
            total = "₱" + pm.group(1).replace(",", "")

    arr_match = re.search(r'^[₱P]?[\d,]+\s*[-–]+\s*(.+)', text, re.MULTILINE)
    arr = arr_match.group(1).strip() if arr_match else None
    if arr:
        arr = re.sub(r'\(.*?\)', '', arr).strip()

    pay = None
    up = text.upper()
    for method in [("GCASH","GCash"),("MAYA","Maya"),("BANK","Bank transfer"),("COD","COD"),("CASH","Cash")]:
        if method[0] in up:
            pay = method[1]
            break
    if pay and "PAID" in up:
        pay += " · paid"

    is_pickup = bool(re.search(r'pick\s*-?\s*up|kukuha', text, re.I))
    is_delivery = bool(re.search(r'for deliveries|delivery date', text, re.I))
    order_type = "Pick up" if is_pickup and not is_delivery else "Delivery"

    return {
        "order_type":   order_type,
        "deliver_on":   date,
        "time_window":  time_,
        "recipient":    grab(head, r'(?:name of receiver|receiver|recipient)\s*[:\-\t]+\s*(.+)'),
        "contact":      find_phone(head),
        "address":      addr,
        "zone":         find_zone(addr),
        "landmark":     grab(head, r'landmark\s*[:\-\t]+\s*(.+)'),
        "card_to":      grab(text, r'(?:^|\n)\s*to\s*[:\-\t]+\s*(.+)'),
        "card_message": grab(text, r'(?:short\s+)?card\s*message\s*[💌:\-\t]+\s*(.+)'),
        "card_from":    grab(text, r'(?:^|\n)\s*from\s*[:\-\t]+\s*(.+)'),        "arrangement":  arr,
        "total":        total,
        "payment":      pay,
        "sender":       grab(tail, r'sender\s*name\s*[:\-\t]+\s*(.+)') or clean(lines[0]),
        "sender_phone": find_phone(tail),
    }

@app.options("/chat")
async def chat_options():
    return {}

app.include_router(chat_router)

@app.get("/")
def root():
    return {"status": "ok", "message": "Order Parser API"}