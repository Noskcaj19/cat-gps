import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from paho.mqtt import client as mqtt_client

from config import Config

config = Config.load()

device_by_id = {d.id: d for d in config.devices}

position_queue: asyncio.Queue[dict] = asyncio.Queue()
ws_clients: set[WebSocket] = set()

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


@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1>Cat GPS</h1><p>Cat location tracker</p>"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/map", response_class=HTMLResponse)
async def map_page():
    rooms_js = []
    for floor in config.floors:
        for room in floor.rooms:
            points = [[p.x, p.y] for p in room.points]
            rooms_js.append({"name": room.name, "points": points})

    bounds = config.floors[0].bounds
    min_x, min_y = bounds[0].x, bounds[0].y
    max_x, max_y = bounds[1].x, bounds[1].y

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cat GPS - Map</title>
        <style>
            body {{ margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; background: #1a1a2e; }}
            canvas {{ border: 1px solid #333; }}
        </style>
    </head>
    <body>
        <canvas id="map" width="800" height="600"></canvas>
        <script>
            const rooms = {rooms_js};
            const bounds = {{ minX: {min_x}, minY: {min_y}, maxX: {max_x}, maxY: {max_y} }};
            const canvas = document.getElementById('map');
            const ctx = canvas.getContext('2d');

            const baseScaleX = canvas.width / (bounds.maxX - bounds.minX);
            const baseScaleY = canvas.height / (bounds.maxY - bounds.minY);
            const baseScale = Math.min(baseScaleX, baseScaleY);

            let zoom = 1;
            let panX = 0;
            let panY = 0;
            let isDragging = false;
            let lastX, lastY;

            function toCanvas(x, y) {{
                const sx = (x - bounds.minX) * baseScale * zoom + panX;
                const sy = (y - bounds.minY) * baseScale * zoom + panY;
                return [sx, sy];
            }}

            function draw() {{
                ctx.fillStyle = '#16213e';
                ctx.fillRect(0, 0, canvas.width, canvas.height);

                rooms.forEach((room, i) => {{
                    ctx.beginPath();
                    const [sx, sy] = toCanvas(room.points[0][0], room.points[0][1]);
                    ctx.moveTo(sx, sy);
                    room.points.slice(1).forEach(p => {{
                        const [px, py] = toCanvas(p[0], p[1]);
                        ctx.lineTo(px, py);
                    }});
                    ctx.closePath();
                    ctx.fillStyle = `hsl(${{i * 25}}, 50%, 30%)`;
                    ctx.fill();
                    ctx.strokeStyle = '#0f3460';
                    ctx.stroke();

                    const cx = room.points.reduce((s, p) => s + p[0], 0) / room.points.length;
                    const cy = room.points.reduce((s, p) => s + p[1], 0) / room.points.length;
                    const [tx, ty] = toCanvas(cx, cy);
                    ctx.fillStyle = '#e0e0e0';
                    ctx.font = '10px sans-serif';
                    ctx.textAlign = 'center';
                    ctx.fillText(room.name, tx, ty);
                }});
            }}

            canvas.addEventListener('wheel', (e) => {{
                e.preventDefault();
                const rect = canvas.getBoundingClientRect();
                const mx = e.clientX - rect.left;
                const my = e.clientY - rect.top;

                const oldZoom = zoom;
                zoom *= e.deltaY < 0 ? 1.1 : 0.9;
                zoom = Math.max(0.5, Math.min(10, zoom));

                panX = mx - (mx - panX) * (zoom / oldZoom);
                panY = my - (my - panY) * (zoom / oldZoom);
                drawAll();
            }});

            canvas.addEventListener('mousedown', (e) => {{
                isDragging = true;
                lastX = e.clientX;
                lastY = e.clientY;
                canvas.style.cursor = 'grabbing';
            }});

            canvas.addEventListener('mousemove', (e) => {{
                if (!isDragging) return;
                panX += e.clientX - lastX;
                panY += e.clientY - lastY;
                lastX = e.clientX;
                lastY = e.clientY;
                drawAll();
            }});

            canvas.addEventListener('mouseup', () => {{
                isDragging = false;
                canvas.style.cursor = 'grab';
            }});

            canvas.addEventListener('mouseleave', () => {{
                isDragging = false;
                canvas.style.cursor = 'grab';
            }});

            const cats = {{}};

            function drawCats() {{
                Object.values(cats).forEach(cat => {{
                    const [cx, cy] = toCanvas(cat.x, cat.y);
                    ctx.beginPath();
                    ctx.arc(cx, cy, 8 * zoom, 0, Math.PI * 2);
                    ctx.fillStyle = cat.color;
                    ctx.fill();
                    ctx.strokeStyle = '#fff';
                    ctx.lineWidth = 2;
                    ctx.stroke();

                    ctx.fillStyle = '#fff';
                    ctx.font = 'bold 12px sans-serif';
                    ctx.textAlign = 'center';
                    ctx.fillText(cat.name, cx, cy - 14 * zoom);
                }});
            }}

            function drawAll() {{
                draw();
                drawCats();
            }}

            const catColors = ['#ff6b6b', '#4ecdc4', '#ffe66d', '#95e1d3'];
            let colorIndex = 0;

            const ws = new WebSocket(`ws://${{location.host}}/ws/positions`);
            ws.onmessage = (e) => {{
                const data = JSON.parse(e.data);
                if (!cats[data.device_id]) {{
                    cats[data.device_id] = {{ color: catColors[colorIndex++ % catColors.length] }};
                }}
                cats[data.device_id].name = data.device_name;
                cats[data.device_id].x = data.x;
                cats[data.device_id].y = data.y;
                drawAll();
            }};

            canvas.style.cursor = 'grab';
            drawAll();
        </script>
    </body>
    </html>
    """
    return html
