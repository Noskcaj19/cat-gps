import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from paho.mqtt import client as mqtt_client

from config import Config

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

config = Config.load()

device_by_id = {d.id: d for d in config.devices}

position_queue: asyncio.Queue[dict] = asyncio.Queue()
ws_clients: set[WebSocket] = set()
last_positions: dict[str, dict] = {}

mqtt: mqtt_client.Client | None = None
mqtt_loop: asyncio.AbstractEventLoop | None = None
broadcaster_task: asyncio.Task | None = None


def on_mqtt_connect(client: mqtt_client.Client, userdata, flags, rc, properties):
    client.subscribe("espresense/companion/+/attributes")


def on_mqtt_message(client: mqtt_client.Client, userdata, msg):
    global mqtt_loop
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return

    x = payload.get("x")
    y = payload.get("y")
    if x is None or y is None:
        return

    parts = msg.topic.split("/")
    if len(parts) < 4:
        return
    device_id = parts[2]

    device = device_by_id.get(device_id)
    if not device:
        return

    data = {
        "device_id": device_id,
        "device_name": device.name,
        "x": x,
        "y": y,
    }

    if mqtt_loop is not None:
        mqtt_loop.call_soon_threadsafe(position_queue.put_nowait, data)


async def broadcast_positions():
    while True:
        data = await position_queue.get()
        last_positions[data["device_id"]] = data
        dead_clients = []

        for ws in list(ws_clients):
            try:
                await ws.send_json(data)
            except Exception:
                dead_clients.append(ws)

        for ws in dead_clients:
            ws_clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mqtt, mqtt_loop, broadcaster_task

    mqtt_loop = asyncio.get_running_loop()

    mqtt = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    mqtt.username_pw_set(config.mqtt.username, config.mqtt.password)
    mqtt.on_connect = on_mqtt_connect
    mqtt.on_message = on_mqtt_message

    mqtt.connect(config.mqtt.host, config.mqtt.port, keepalive=60)
    mqtt.loop_start()

    broadcaster_task = asyncio.create_task(broadcast_positions())

    yield

    if broadcaster_task:
        broadcaster_task.cancel()
        try:
            await broadcaster_task
        except asyncio.CancelledError:
            pass

    if mqtt:
        mqtt.loop_stop()
        mqtt.disconnect()


app = FastAPI(title="Cat GPS", lifespan=lifespan)


@app.websocket("/ws/positions")
async def ws_positions(ws: WebSocket):
    await ws.accept()
    for pos in last_positions.values():
        await ws.send_json(pos)
    ws_clients.add(ws)
    try:
        while True:
            try:
                await ws.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        ws_clients.discard(ws)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def map_page(request: Request):
    bounds = config.floors[0].bounds
    min_x, min_y = bounds[0].x, bounds[0].y
    max_x, max_y = bounds[1].x, bounds[1].y

    svg_width, svg_height = 800, 600
    base_scale_x = svg_width / (max_x - min_x)
    base_scale_y = svg_height / (max_y - min_y)
    base_scale = min(base_scale_x, base_scale_y)

    def to_svg(x: float, y: float) -> tuple[float, float]:
        sx = (x - min_x) * base_scale
        sy = svg_height - (y - min_y) * base_scale
        return (sx, sy)

    rooms = []
    for floor in config.floors:
        for room in floor.rooms:
            svg_points = [to_svg(p.x, p.y) for p in room.points]
            cx = sum(p[0] for p in svg_points) / len(svg_points)
            cy = sum(p[1] for p in svg_points) / len(svg_points)
            rooms.append({
                "name": room.name,
                "svg_points": svg_points,
                "label_x": cx,
                "label_y": cy,
            })

    nodes = []
    for node in config.nodes:
        sx, sy = to_svg(node.point.x, node.point.y)
        nodes.append({"x": sx, "y": sy})

    return templates.TemplateResponse(
        request,
        "map.html",
        {
            "rooms": rooms,
            "nodes": nodes,
            "min_x": min_x,
            "min_y": min_y,
            "max_x": max_x,
            "max_y": max_y,
        },
    )
