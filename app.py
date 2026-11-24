import asyncio, threading, time, os, secrets
from collections import deque
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from TikTokLive import TikTokLiveClient
from TikTokLive.events import ConnectEvent, DisconnectEvent, CommentEvent, GiftEvent, SystemEvent, RoomEvent, UnknownEvent, WebsocketResponseEvent

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

def _get_attr(obj, name):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)

def _find_instruction_text(obj, depth=0, seen=None):
    """Cari medan arahan Goody Bag dalam objek/protobuf bersarang, tanpa membaca komen biasa."""
    if obj is None or depth > 9:
        return ""
    if seen is None:
        seen = set()
    marker = id(obj)
    if marker in seen:
        return ""
    seen.add(marker)

    if isinstance(obj, dict):
        items = obj.items()
    elif isinstance(obj, (list, tuple)):
        items = enumerate(obj)
    else:
        try:
            items = obj.ListFields()
        except Exception:
            try:
                items = vars(obj).items()
            except Exception:
                return ""

    try:
        for key, value in items:
            field_name = getattr(key, "name", str(key)).lower()
            if field_name == "participate_method_content" and value:
                return str(value)
            found = _find_instruction_text(value, depth + 1, seen)
            if found:
                return found
    except Exception:
        return ""
    return ""

def _goodybag_text(obj):
    # Semak terus dahulu, kemudian cari dalam struktur RoomEvent/protobuf bersarang.
    direct = _get_attr(obj, "participate_method_content")
    if direct:
        return str(direct)
    for root_name in ("data", "message", "payload", "event", "common", "common_message"):
        nested = _get_attr(obj, root_name)
        if nested is not None:
            found = _find_instruction_text(nested)
            if found:
                return found
    return _find_instruction_text(obj)

def _decode_goodybag_payload(payload, method=""):
    """
    Nyahkod WebcastGoodyBagMessage secara khusus.
    TikTokLive v6 menggunakan betterproto2 dan tidak mendaftarkan mesej
    Goody Bag ini dalam EVENT_MAPPINGS. Oleh itu, descriptor protobuf kecil
    dibina daripada nombor medan rasmi yang diperlukan sahaja.
    """
    if not payload:
        return None
    if "goodybag" not in str(method).lower().replace("_", ""):
        return None
    try:
        from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

        file_proto = descriptor_pb2.FileDescriptorProto()
        file_proto.name = "scanner_goodybag_payload.proto"
        file_proto.package = "scanner.goodybag"
        file_proto.syntax = "proto3"

        detail = file_proto.message_type.add()
        detail.name = "GoodyBagCommonDetail"
        field = detail.field.add()
        field.name = "participate_method_content"
        field.number = 4
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_STRING

        base = file_proto.message_type.add()
        base.name = "GoodyBagBaseInfo"
        field = base.field.add()
        field.name = "common_detail"
        field.number = 4
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
        field.type_name = ".scanner.goodybag.GoodyBagCommonDetail"

        root = file_proto.message_type.add()
        root.name = "WebcastGoodyBagMessage"
        field = root.field.add()
        field.name = "base_info"
        field.number = 2
        field.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
        field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
        field.type_name = ".scanner.goodybag.GoodyBagBaseInfo"

        pool = descriptor_pool.DescriptorPool()
        pool.Add(file_proto)
        descriptor = pool.FindMessageTypeByName(
            "scanner.goodybag.WebcastGoodyBagMessage"
        )
        message = message_factory.GetMessageClass(descriptor)()
        message.ParseFromString(payload)
        text = _goodybag_text(message)
        return text or None
    except Exception as exc:
        push("debug", f"Payload Goody Bag gagal dinyahkod ({type(exc).__name__}).")
        return None

class ConnectBody(BaseModel):
    username: str

@app.get("/", response_class=HTMLResponse)
def home():
    return Path("index.html").read_text(encoding="utf-8")
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
                # Komen penonton sengaja tidak dipaparkan.
                pass
            @client.on(GiftEvent)
            async def on_gift(event):
                # Hadiah LIVE biasa bukan arahan Goody Bag; tidak dipaparkan.
                pass
            @client.on(SystemEvent)
            async def on_system(event):
                push("sistem", str(event))
            @client.on(RoomEvent)
            async def on_room(event):
                text = _goodybag_text(event)
                if text:
                    push("goodybag", text)
                else:
                    # Ringkasan sahaja; elakkan memaparkan dump RoomEvent yang terlalu panjang.
                    push("debug", "RoomEvent diterima; tiada medan arahan Goody Bag yang dikenal pasti.")

            try:
                import TikTokLive.events as tiktok_events
                goody_event = getattr(tiktok_events, "GoodyBagEvent", None)
                if goody_event is not None:
                    async def on_goodybag(event):
                        text = _goodybag_text(event)
                        if text:
                            push("goodybag", text)
                        else:
                            push("debug", "Event Goody Bag diterima, tetapi teks arahan tidak ditemui.")
                    client.on(goody_event)(on_goodybag)
            except Exception as register_error:
                push("debug", f"Handler Goody Bag khusus tidak tersedia: {type(register_error).__name__}")

            # Tangkap envelope WebSocket mentah untuk mengesan method Goody Bag
            # walaupun TikTokLive belum mempunyai event/protobuf khusus.
            seen_raw_messages = set()
            @client.on(WebsocketResponseEvent)
            async def on_websocket_response(event):
                raw = getattr(event, "raw", None)
                messages = getattr(raw, "messages", None) or []
                for message in messages:
                    method = str(getattr(message, "method", "") or "")
                    payload = getattr(message, "payload", b"") or b""
                    msg_id = getattr(message, "msg_id", None)
                    identity = (method, msg_id, hash(payload) if payload else None)
                    if identity in seen_raw_messages:
                        continue
                    seen_raw_messages.add(identity)

                    if "goodybag" in method.lower().replace("_", ""):
                        text = _decode_goodybag_payload(payload, method)
                        if text:
                            push("goodybag", text)
                        else:
                            push("debug", f"{method} diterima melalui WebSocket, tetapi teks arahan belum dapat dinyahkod.")
                    elif method:
                        push("debug", f"Method WebSocket diterima: {method}")

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
def status():
    return {**state, "event_count": len(events)}
@app.get("/api/events")
def get_events(since: int = 0, _auth: None = Depends(require_password)):
    return [e for e in list(events) if e["id"] > since]
