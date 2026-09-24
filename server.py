"""
Drishti prototype server.

- Reads road video (webcam or file), detects hazards with a pre-trained YOLO model.
- Streams the annotated "front view" to the rear-screen page (/).
- Publishes anonymised hazard events through an open API (/api/..., docs at /docs).
- Serves the authority hazard-map dashboard (/dashboard).

Run:  python server.py --source road.mp4
      python server.py --source 0            (webcam)
      python server.py --source road.mp4 --serial COM3   (Arduino proximity sensor)
"""
import argparse
import asyncio
import math
import random
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

BASE = Path(__file__).parent
VEHICLES = {"car", "motorcycle", "bus", "truck", "bicycle"}

# Simulated GPS route: Moodbidri -> Mangaluru (approximate points, demo only)
ROUTE = [(13.0707, 74.9956), (13.0480, 74.9540), (13.0010, 74.9150),
         (12.9700, 74.8900), (12.9141, 74.8560)]

LOCK = threading.Lock()
STATE = {
    "jpg": None, "hazard": None, "reason": "", "lag_ms": 0, "blank": False,
    "near": False, "distance_cm": None, "brake": False, "left": False, "right": False,
}
EVENTS = []
CONSENT = {"exact_location": True, "share_with": {"highway": True, "police": True, "public": False}}
START = time.time()
ARGS = None


# ---------------------------------------------------------------- detection
class Detector:
    def __init__(self, enabled: bool, imgsz: int):
        self.model, self.imgsz = None, imgsz
        if enabled:
            from ultralytics import YOLO  # downloads yolov8n.pt on first run
            self.model = YOLO("yolov8n.pt")

    def detect(self, frame):
        if self.model is None:
            return []
        r = self.model(frame, imgsz=self.imgsz, conf=0.4, verbose=False)[0]
        out = []
        for b in r.boxes:
            name = r.names[int(b.cls)]
            if name in VEHICLES or name == "person":
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                out.append((name, x1, y1, x2, y2))
        return out


def assess(dets, w, h):
    """Rule-based hazard decision. Tune these thresholds with your own footage.
    India drives on the left, so the oncoming lane is on the RIGHT of the frame."""
    best = None
    for name, x1, y1, x2, y2 in dets:
        cx = (x1 + x2) / 2 / w
        area = (x2 - x1) * (y2 - y1) / (w * h)
        bottom = y2 / h
        if name in VEHICLES and cx > 0.55 and area > 0.015:
            cand = (3, "oncoming", f"{name} in oncoming lane")
        elif name == "person" and 0.2 < cx < 0.8 and bottom > 0.55:
            cand = (2, "pedestrian", "person on the road ahead")
        elif name in VEHICLES and 0.3 < cx < 0.6 and area > 0.06:
            cand = (1, "vehicle_close", f"{name} close ahead")
        else:
            continue
        if best is None or cand[0] > best[0]:
            best = cand
    return best


def draw(frame, dets):
    for name, x1, y1, x2, y2 in dets:
        color = (0, 180, 255) if name in VEHICLES else (40, 40, 230)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, name, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)


# ---------------------------------------------------------------- GPS + events
def _dist(a, b):
    dy = (a[0] - b[0]) * 111_000
    dx = (a[1] - b[1]) * 111_000 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


SEG = [_dist(ROUTE[i], ROUTE[i + 1]) for i in range(len(ROUTE) - 1)]


def point_on_route(meters):
    meters %= sum(SEG)
    for i, seg in enumerate(SEG):
        if meters <= seg:
            t = meters / seg
            a, b = ROUTE[i], ROUTE[i + 1]
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        meters -= seg
    return ROUTE[-1]


def gps_now():
    return point_on_route((time.time() - START) * 11)  # ~40 km/h


_last_logged = {}


def log_event(hazard_type, reason, when=None, pos=None):
    now = time.time()
    if when is None and now - _last_logged.get(hazard_type, 0) < 4:
        return  # avoid logging the same hazard every frame
    _last_logged[hazard_type] = now
    lat, lon = pos or gps_now()
    with LOCK:
        EVENTS.append({
            "id": len(EVENTS) + 1, "type": hazard_type, "reason": reason,
            "lat": round(lat, 5), "lon": round(lon, 5),
            "time": (when or datetime.now()).isoformat(timespec="seconds"),
            "vehicle": ARGS.vehicle_id,
        })


# ---------------------------------------------------------------- loops
def video_loop(det):
    src = int(ARGS.source) if ARGS.source.isdigit() else ARGS.source
    is_file = not ARGS.source.isdigit()
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"Could not open video source '{ARGS.source}'. Check the file path or webcam number.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    while True:
        t0 = time.time()
        ok, frame = cap.read()
        if not ok:
            if is_file:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the demo video
            else:
                time.sleep(0.1)
            continue
        h, w = frame.shape[:2]
        if w > 960:
            frame = cv2.resize(frame, (960, int(h * 960 / w)))
            h, w = frame.shape[:2]
        dets = det.detect(frame)
        hz = assess(dets, w, h)
        draw(frame, dets)
        _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        lag = int((time.time() - t0) * 1000)
        with LOCK:
            STATE.update(jpg=jpg.tobytes(), lag_ms=lag, blank=lag > ARGS.lag_limit,
                         hazard=hz[1] if hz else None, reason=hz[2] if hz else "")
        if hz:
            log_event(hz[1], hz[2])
        if is_file:
            time.sleep(max(0, 1 / fps - (time.time() - t0)))


def serial_loop(port, near_cm):
    import serial
    ser = serial.Serial(port, 9600, timeout=1)
    while True:
        line = ser.readline().decode(errors="ignore").strip()
        try:
            cm = float(line)
        except ValueError:
            continue
        with LOCK:
            STATE["distance_cm"] = cm
            STATE["near"] = 0 < cm < near_cm


# ---------------------------------------------------------------- API
app = FastAPI(title="Drishti Road-Safety Event API",
              description="Open API for anonymised hazard and near-miss events from heavy vehicles.")


class Proximity(BaseModel):
    near: bool


class Signals(BaseModel):
    brake: bool = False
    left: bool = False
    right: bool = False


class Consent(BaseModel):
    exact_location: bool
    share_with: dict[str, bool]


@app.get("/", include_in_schema=False)
def rear_screen():
    return FileResponse(BASE / "static" / "rear.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(BASE / "static" / "dashboard.html")


@app.get("/stream", include_in_schema=False)
async def stream():
    async def frames():
        last = None
        while True:
            with LOCK:
                jpg = STATE["jpg"]
            if jpg is not None and jpg is not last:
                last = jpg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(0.03)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.websocket("/ws")
async def ws_state(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            with LOCK:
                snap = {k: v for k, v in STATE.items() if k != "jpg"}
            await ws.send_json(snap)
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass


@app.post("/api/proximity", summary="Set whether a vehicle is close behind (manual demo control)")
def set_proximity(p: Proximity):
    with LOCK:
        STATE["near"] = p.near
    return {"near": p.near}


@app.post("/api/signal", summary="Mirror the truck's brake and indicator signals")
def set_signals(s: Signals):
    with LOCK:
        STATE.update(s.model_dump())
    return s


@app.get("/api/events", summary="Hazard events visible to a data user")
def get_events(viewer: str = "highway", since: int = 0):
    if not CONSENT["share_with"].get(viewer, False):
        raise HTTPException(403, f"The fleet owner has not shared data with '{viewer}'.")
    with LOCK:
        items = [dict(e) for e in EVENTS if e["id"] > since]
    if not CONSENT["exact_location"]:
        for e in items:  # ~1 km precision
            e["lat"], e["lon"] = round(e["lat"], 2), round(e["lon"], 2)
    return items


@app.get("/api/consent", summary="Current data-sharing consent set by the fleet owner")
def get_consent():
    return CONSENT


@app.post("/api/consent", summary="Update data-sharing consent")
def set_consent(c: Consent):
    CONSENT.update(c.model_dump())
    return CONSENT


@app.get("/api/route", summary="Route used by the simulated GPS")
def get_route():
    return ROUTE


@app.post("/api/seed", summary="Add sample past events (demo black spots)")
def seed():
    spots = [(8_000, "oncoming"), (19_000, "vehicle_close"), (27_000, "pedestrian")]
    reasons = {"oncoming": "car in oncoming lane", "vehicle_close": "truck close ahead",
               "pedestrian": "person on the road ahead"}
    for _ in range(45):
        m, kind = random.choice(spots)
        lat, lon = point_on_route(m + random.gauss(0, 400))
        when = datetime.now() - timedelta(days=random.uniform(1, 30))
        log_event(kind, reasons[kind], when=when,
                  pos=(lat + random.gauss(0, 0.0015), lon + random.gauss(0, 0.0015)))
    return {"added": 45}


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Drishti prototype server")
    ap.add_argument("--source", default="0", help="video file path or webcam number (default 0)")
    ap.add_argument("--serial", help="Arduino serial port for the proximity sensor, e.g. COM3 or /dev/ttyUSB0")
    ap.add_argument("--near-cm", type=float, default=40, help="distance that counts as 'vehicle close behind'")
    ap.add_argument("--lag-limit", type=int, default=200, help="blank the video above this delay in ms")
    ap.add_argument("--imgsz", type=int, default=416, help="YOLO input size; smaller is faster")
    ap.add_argument("--no-ai", action="store_true", help="run without YOLO (video only)")
    ap.add_argument("--vehicle-id", default="KA19-DEMO", help="demo vehicle ID attached to events")
    ap.add_argument("--port", type=int, default=8000)
    ARGS = ap.parse_args()

    detector = Detector(not ARGS.no_ai, ARGS.imgsz)
    threading.Thread(target=video_loop, args=(detector,), daemon=True).start()
    if ARGS.serial:
        threading.Thread(target=serial_loop, args=(ARGS.serial, ARGS.near_cm), daemon=True).start()
    print(f"Rear screen: http://localhost:{ARGS.port}/   Dashboard: http://localhost:{ARGS.port}/dashboard"
          f"   API docs: http://localhost:{ARGS.port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=ARGS.port, log_level="warning")
