import logging
import asyncio
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.processor import process_directory

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Episode Matcher")

# Setup templates and static files
templates = Jinja2Templates(directory="app/templates")
# We don't strictly need static files right now, but good to have if we add custom CSS/JS
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Store active websockets for broadcasting progress
active_connections: list[WebSocket] = []

async def broadcast_progress(file: str, message: str):
    """Sends a progress update to all connected WebSocket clients."""
    data = {"file": file, "message": message}
    for connection in active_connections:
        try:
            await connection.send_json(data)
        except Exception as e:
            logger.error(f"Error sending websocket message: {e}")
            active_connections.remove(connection)

def progress_callback(file: str, message: str):
    """Callback function passed to the synchronous processor to trigger async broadcasts."""
    # Since processor runs in a thread, we need to schedule the broadcast safely on the event loop
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_progress(file, message))
    except RuntimeError:
        # If no running loop (e.g., test environment), just ignore
        pass

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serves the main Web UI page."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/start")
async def start_processing(directory: str = Form(...), show_name: str = Form(...)):
    """API endpoint to start the batch processing job."""
    # Run the blocking batch process in a separate thread so it doesn't block the FastAPI event loop
    asyncio.create_task(asyncio.to_thread(process_directory, directory, show_name, progress_callback))
    return {"message": f"Processing started for '{show_name}' in '{directory}'."}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time progress updates."""
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            # Keep connection alive, though we mostly just send data TO the client
            await websocket.receive_text()
    except WebSocketDisconnect:
        active_connections.remove(websocket)