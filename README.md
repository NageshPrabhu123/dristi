# Drishti

See-through safety display for heavy vehicles, plus an open road-safety data exchange.
Team Savdhaan, Build for Billions 2026, track: Reinvent Digital Public Infrastructure For Billions.

A front camera shows the road ahead on a screen at the back of a truck or bus, so vehicles behind
can see oncoming traffic and hazards. Every hazard is also published as an anonymised event through
an open API, so highway authorities get a live map of dangerous stretches.

## What's in the prototype

| Part | File | What it does |
|---|---|---|
| Server | `server.py` | Reads road video, detects hazards with YOLO, streams video, runs the API |
| Rear screen | `static/rear.html` | What vehicles behind the truck see: live view, warnings in English, Kannada and Hindi, brake and indicator icons |
| Dashboard | `static/dashboard.html` | Hazard map and heatmap for authorities, consent controls for fleet owners |
| Sensor | `arduino/proximity/proximity.ino` | Ultrasonic sensor that turns the screen on when a vehicle is close behind |

## Run it

```
pip install -r requirements.txt
python server.py --source road.mp4
```

Then open:

- Rear screen: http://localhost:8000/
- Dashboard: http://localhost:8000/dashboard
- API docs: http://localhost:8000/docs

Use `--source 0` for a webcam. The first run downloads the YOLO model (about 6 MB).

### Rear screen keys (for the demo)

- `P`: vehicle close behind on/off (the screen is off until this is on)
- `B`: brake
- Left / right arrow: indicators

### With the Arduino sensor

Upload `arduino/proximity/proximity.ino`, close the Arduino Serial Monitor, then:

```
python server.py --source road.mp4 --serial COM3
```

(`/dev/ttyUSB0` or `/dev/ttyACM0` on Linux). Hold your hand within 40 cm of the sensor to turn the screen on; change this with `--near-cm`.

### Useful options

- `--lag-limit 200`: blank the video if processing takes longer than this many ms (product target: 150)
- `--imgsz 320`: faster detection on slow laptops
- `--no-ai`: run without YOLO, video only

## Tuning hazard detection

The rules are in `assess()` in `server.py`. India drives on the left, so a vehicle on the right side of the
frame is treated as oncoming. Adjust the position and size thresholds using your own recorded road footage.

## Acknowledgements

Third-party tools and data used in this project:

- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics), pre-trained object detection model (AGPL-3.0)
- [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/) and [OpenCV](https://opencv.org/)
- [Leaflet](https://leafletjs.com/) and [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) for the map
- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors
- Fonts: Barlow, Noto Sans Kannada and Noto Sans Devanagari (Google Fonts, SIL Open Font License)
