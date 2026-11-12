import asyncio, threading, time, os, secrets
from collections import deque
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, DisconnectEvent, CommentEvent, GiftEvent, SystemEvent, RoomEvent

app = FastAPI(title="TikTok Goody Bag Scanner")
ACCESS_PASSWORD = os.getenv("SCANNER_PASSWORD", "")
def require_password(x_scanner_password: str = Header(default="", alias="X-Scanner-Password")):
    if not ACCESS_PASSWORD:
        raise HTTPException(503, "SCANNER_PASSWORD belum ditetapkan pada server")
    if not secrets.compare_digest(x_scanner_password, ACCESS_PASSWORD):
        raise HTTPException(401, "Kata laluan scanner salah")
events = deque(maxlen=300)
state = {"connected": False, "username": None, "message": "Belum disambungkan", "room_id": None}
client: Optional[TikTokLiveClient] = None
runner_thread = None
lock = threading.Lock()

def push(kind, text, raw=None):
    events.append({"id": int(time.time()*1000), "time": time.strftime("%H:%M:%S"), "type": kind, "text": str(text), "raw": raw})

class ConnectBody(BaseModel):
    username: str

@app.get("/", response_class=HTMLResponse)
def home():
    return Path("static/index.html").read_text(encoding="utf-8")

@app.post("/api/connect")
def connect(body: ConnectBody, _auth: None = Depends(require_password)):
    global client, runner_thread
    username = body.username.strip().replace("https://www.tiktok.com/", "").replace("https://vt.tiktok.com/", "")
    if "/@" in username:
        username = username.split("/@",1)[1].split("/",1)[0]
    username = username.lstrip("@").split("/")[0].strip()
    if not username or len(username) > 50:
        raise HTTPException(400, "Username tidak sah")
    if state["connected"]:
        raise HTTPException(409, "Putuskan sambungan semasa dahulu")
    state.update(connected=False, username=username, message="Sedang menyambung…", room_id=None)
    events.clear()
    def worker():
        global client
        try:
            client = TikTokLiveClient(unique_id=username)
            @client.on(ConnectEvent)
            async def on_connect(event):
                state.update(connected=True, message="LIVE disambungkan", room_id=getattr(event, "room_id", None))
                push("status", f"Disambungkan ke @{username}")
            @client.on(DisconnectEvent)
            async def on_disconnect(event):
                state.update(connected=False, message="Sambungan terputus")
                push("status", "Sambungan LIVE terputus")
            @client.on(CommentEvent)
            async def on_comment(event):
                user = getattr(getattr(event, "user", None), "nickname", "Penonton")
                comment = getattr(event, "comment", "")
                push("komen", f"{user}: {comment}", {"comment": comment})
                # Only surface possible instruction-like system/chat text; this is NOT verified Goody Bag data.
                low = comment.lower()
                if any(word in low for word in ("goody bag", "goodie bag", "kirim komentar", "comment required")):
                    push("calon-syarat", f"Teks berkaitan Goody Bag: {user}: {comment}")
            @client.on(GiftEvent)
            async def on_gift(event):
                user = getattr(getattr(event, "user", None), "nickname", "Penonton")
                gift = getattr(getattr(event, "gift", None), "name", "Hadiah")
                count = getattr(event, "repeat_count", 1)
                push("hadiah", f"{user} menghantar {gift} x{count}")
            @client.on(SystemEvent)
            async def on_system(event):
                push("sistem", str(event))
            @client.on(RoomEvent)
            async def on_room(event):
                push("bilik", str(event))
            client.run()
        except Exception as exc:
            state.update(connected=False, message=f"Gagal: {type(exc).__name__}: {exc}")
            push("ralat", state["message"])
    runner_thread = threading.Thread(target=worker, daemon=True)
    runner_thread.start()
    return {"ok": True, "username": username}

@app.post("/api/disconnect")
def disconnect(_auth: None = Depends(require_password)):
    global client
    if client:
        try:
            client.stop()
        except Exception:
            try: client.disconnect()
            except Exception: pass
    state.update(connected=False, message="Sambungan dihentikan")
    push("status", "Scanner dihentikan")
    return {"ok": True}

@app.get("/api/status")
def status(_auth: None = Depends(require_password)):
    return {**state, "event_count": len(events)}

@app.get("/api/events")
def get_events(since: int = 0, _auth: None = Depends(require_password)):
    return [e for e in list(events) if e["id"] > since]
