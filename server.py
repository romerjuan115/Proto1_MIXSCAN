"""
server.py — Servidor FastAPI del dashboard MEXSCAN

Expone la interfaz web en http://127.0.0.1:8080 y coordina tres flujos:

  1. API REST: arrancar/detener detección, limpiar capturas, consultar stats.
  2. WebSocket (/ws): emite eventos en tiempo real al dashboard (new_data,
     mavlink_heartbeat, mavlink_lost, correlation_alert).
  3. Tareas background: watch_csv(), watch_detecciones()
     vigilan archivos en disco y notifican a los clientes conectados.

Si este servidor se detiene, el dashboard queda sin datos pero el proceso
de captura (main.py) sigue corriendo de forma independiente.
"""

import os
import json
import subprocess
import sys
import asyncio
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import uvicorn

# Puerto del servidor web. 8080 evita el conflicto con el puerto 80 reservado
# en Windows sin requerir privilegios de administrador.
PORT = 8080
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
_server_start = time.time()

# =============================================================================
# Estado compartido para correlación Capa 1 + Capa 2
# =============================================================================

# Tras 5 s sin HEARTBEAT el sistema considera que el enlace se perdió
# y emite mavlink_lost al dashboard. El SiK emite cada ~1 s, así que
# 5 s equivale a tolerar 4 gaps consecutivos antes de declarar pérdida.
HB_TIMEOUT          = 5.0

# Dos detecciones (Capa 1 y Capa 2) se consideran simultáneas si ocurrieron
# dentro de esta ventana. 10 s absorbe la latencia de arranque de Capa 2
# (hasta ~10 s de timeout en capa2_serial) más el tiempo de procesamiento.
CORRELATION_WINDOW  = 10.0

# Tiempo mínimo entre alertas de correlación consecutivas.
# Sin este cooldown, una detección sostenida generaría una alerta por cada
# nuevo HEARTBEAT recibido, saturando el dashboard con mensajes redundantes.
CORRELATION_COOLDOWN = 30.0

_last_capa1_time      = 0.0   # última detección DRON DETECTADO (Capa 1)
_last_capa2_time      = 0.0   # último HEARTBEAT recibido (Capa 2)
_last_capa2_armed     = False  # estado ARMADO del último HEARTBEAT
_last_corr_sent       = 0.0   # timestamp del último envío de correlation_alert


async def _check_correlation() -> None:
    """Emite correlation_alert al dashboard si Capa 1 y Capa 2 coinciden en tiempo.

    Compara el timestamp de la última detección de Capa 1 (DRON DETECTADO en
    resultados.csv) con el del último HEARTBEAT de Capa 2 (detecciones.csv).
    Si ambos ocurrieron dentro de CORRELATION_WINDOW segundos → alerta de alta
    confianza (99% si el dron estaba armado, 92% si solo estaba encendido).

    El cooldown de 30 s evita spam de alertas durante detecciones sostenidas.
    """
    global _last_corr_sent
    now = time.monotonic()

    if now - _last_corr_sent < CORRELATION_COOLDOWN:
        return

    d1 = now - _last_capa1_time
    d2 = now - _last_capa2_time

    if d1 > CORRELATION_WINDOW or d2 > CORRELATION_WINDOW:
        return   # una de las capas es demasiado antigua

    armed     = _last_capa2_armed
    confianza = 99 if armed else 92
    _last_corr_sent = now

    payload = json.dumps({
        "type":       "correlation_alert",
        "confianza":  confianza,
        "armed":      armed,
        "delta_c1":   round(d1, 1),
        "delta_c2":   round(d2, 1),
    })
    print(f"[CORRELACIÓN] Capa1={d1:.1f}s  Capa2={d2:.1f}s  "
          f"armed={armed}  confianza={confianza}%")
    for client in list(connected_clients):
        try:
            await client.send_text(payload)
        except Exception:
            pass


async def watch_detecciones():
    """Vigila logs/detecciones.csv y emite eventos MAVLink al dashboard por WebSocket.

    Detecta nuevas filas en el CSV de Capa 2 comparando el tamaño del archivo
    en cada iteración (polling cada 100 ms). Cuando hay filas nuevas:
      - Emite mavlink_heartbeat con el texto crudo de las líneas nuevas.
      - Parsea msg_id y armado para actualizar el estado de correlación.
      - Si pasan más de HB_TIMEOUT segundos sin HEARTBEAT, emite mavlink_lost.

    El detector de señal perdida usa time.monotonic() en lugar de timestamps
    del CSV para no depender de la sincronización de relojes entre procesos.
    """
    global _last_capa2_time, _last_capa2_armed

    csv_path     = os.path.join(DIRECTORY, 'logs', 'detecciones.csv')
    last_size    = 0
    last_hb_mono = None   # time.monotonic() del último HEARTBEAT visto
    lost_emitted = False  # evita emitir mavlink_lost repetidamente

    if os.path.exists(csv_path):
        last_size = os.path.getsize(csv_path)

    while True:
        try:
            # ── Detector de señal perdida ────────────────────────────────────
            if last_hb_mono is not None and not lost_emitted:
                if time.monotonic() - last_hb_mono > HB_TIMEOUT:
                    lost_emitted = True
                    elapsed = round(time.monotonic() - last_hb_mono, 1)
                    print(f"[CAPA 2] Señal perdida — sin HEARTBEAT en {elapsed} s")
                    payload = json.dumps({"type": "mavlink_lost", "elapsed": elapsed})
                    for client in list(connected_clients):
                        try:
                            await client.send_text(payload)
                        except Exception:
                            pass

            if os.path.exists(csv_path):
                current_size = os.path.getsize(csv_path)

                if current_size > last_size:
                    with open(csv_path, 'r', encoding='utf-8') as f:
                        f.seek(last_size)
                        new_lines = f.read()
                    last_size = current_size

                    # ── Parsear nuevas filas para HEARTBEAT ──────────────────
                    for line in new_lines.strip().split('\n'):
                        line = line.strip()
                        if not line or line.startswith('timestamp'):
                            continue
                        cols = [c.strip() for c in line.split(',')]
                        if len(cols) >= 2:
                            try:
                                if int(cols[1]) == 0:   # msg_id=0 → HEARTBEAT
                                    last_hb_mono     = time.monotonic()
                                    lost_emitted     = False
                                    _last_capa2_time = last_hb_mono
                                    if len(cols) >= 5:
                                        _last_capa2_armed = cols[4].lower() in ('true',)
                            except ValueError:
                                pass

                    if new_lines and connected_clients:
                        for client in list(connected_clients):
                            try:
                                await client.send_text(json.dumps({
                                    "type": "mavlink_heartbeat",
                                    "data": new_lines
                                }))
                            except Exception:
                                pass
                        await _check_correlation()

                elif current_size < last_size:
                    # CSV truncado (clear_captures) — reiniciar tracking
                    last_size    = current_size
                    last_hb_mono = None
                    lost_emitted = False

            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error watching detecciones.csv: {e}")
            await asyncio.sleep(1)


async def watch_csv():
    """Vigila resultados.csv de Capa 1 y emite new_data al dashboard por WebSocket.

    Mismo patrón que watch_detecciones() pero para el CSV de detecciones SDR.
    Además actualiza _last_capa1_time cuando encuentra una fila DRON DETECTADO,
    lo que dispara la comprobación de correlación con Capa 2.

    Si el archivo se trunca (tamaño actual < último tamaño conocido), significa
    que /api/clear-captures reinició el CSV — se resetea el puntero de lectura.
    """
    csv_path = os.path.join(DIRECTORY, 'resultados.csv')
    last_size = 0

    if os.path.exists(csv_path):
        last_size = os.path.getsize(csv_path)

    while True:
        try:
            if os.path.exists(csv_path):
                current_size = os.path.getsize(csv_path)

                if current_size > last_size:
                    with open(csv_path, 'r', encoding='utf-8') as f:
                        f.seek(last_size)
                        new_lines = f.read()

                    last_size = current_size

                    # ── Tracking Capa 1 para correlación ────────────────────
                    global _last_capa1_time
                    for line in new_lines.strip().split('\n'):
                        line = line.strip()
                        if not line or line.startswith('archivo'):
                            continue
                        cols = [c.strip() for c in line.split(',')]
                        if len(cols) >= 4 and cols[3] == 'DRON DETECTADO':
                            _last_capa1_time = time.monotonic()
                            await _check_correlation()
                            break

                    if new_lines and connected_clients:
                        for client in list(connected_clients):
                            try:
                                await client.send_text(json.dumps({
                                    "type": "new_data",
                                    "data": new_lines
                                }))
                            except Exception:
                                pass

                elif current_size < last_size:
                    last_size = current_size

            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error watching csv: {e}")
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestiona el ciclo de vida del servidor: arranca watchers y limpia procesos al cerrar.

    Las dos tareas background (watch_csv, watch_detecciones) se
    lanzan como asyncio tasks al arrancar. Al cerrar el servidor (Ctrl+C o SIGTERM),
    el bloque after yield mata los procesos de captura hijos si siguen corriendo,
    evitando procesos huérfanos que sigan consumiendo el SDR o el puerto serie.
    """
    # Startup
    task  = asyncio.create_task(watch_csv())
    task2 = asyncio.create_task(watch_detecciones())
    print(f"Servidor MEXSCAN activo con WebSockets")
    yield
    # Shutdown
    global captura_proc
    print("\nServidor detenido. Limpiando procesos...")
    if captura_proc is not None and captura_proc.poll() is None:
        captura_proc.kill()
    print("Servidor y proceso de captura cerrados.")

app = FastAPI(lifespan=lifespan)

# Global process variables for background tasks
captura_proc  = None   # Proto1: main.py  (Capa1 + Capa2)
connected_clients = []

@app.get("/api/stats")
async def get_stats():
    """Devuelve capturas totales, alertas de dron y uptime del servidor.

    Lee resultados.csv línea a línea para contar totales sin cargarlo completo
    en memoria — necesario porque en sesiones largas el CSV puede tener miles
    de filas. El uptime se calcula desde _server_start (time.time() al importar).
    """
    csv_path = os.path.join(DIRECTORY, 'resultados.csv')
    total = 0
    alertas = 0
    try:
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue
                line = line.strip()
                if not line:
                    continue
                total += 1
                if 'DRON DETECTADO' in line:
                    alertas += 1
    except FileNotFoundError:
        pass
    uptime_s = int(time.time() - _server_start)
    h, rem = divmod(uptime_s, 3600)
    m, s   = divmod(rem, 60)
    return JSONResponse(content={
        "capturas": total,
        "alertas":  alertas,
        "uptime":   f"{h:02d}:{m:02d}:{s:02d}"
    })

@app.get("/api/detection-status")
async def get_detection_status():
    """Devuelve el estado de los procesos de captura activos (running/stopped).

    Comprueba si el subproceso (captura_proc) tiene poll()==None,
    que en subprocess significa que el proceso sigue vivo. No verifica que esté
    funcionando correctamente, solo que no ha terminado.
    """
    status = {
        "captura": "running" if (captura_proc is not None and captura_proc.poll() is None) else "stopped",
        "watcher": "stopped",
    }
    return JSONResponse(content=status)

@app.post("/api/clear-captures")
async def clear_captures():
    """Elimina capturas en vivo y reinicia los CSVs a su estado inicial (solo cabecera).

    Borra los WAV de capturas/ (no toca la subcarpeta ruido/ de referencia),
    trunca resultados.csv y watcher.log, y reinicia logs/detecciones.csv.
    Los watchers de background detectan el truncado automáticamente.

    Los watchers de background detectan el truncado por el cambio de tamaño
    del archivo y reinician sus punteros de lectura automáticamente.
    """
    errors = []

    # 1. Clear Proto1 Capturas
    capturas_dir = os.path.join(DIRECTORY, 'capturas')
    if os.path.exists(capturas_dir):
        for item in os.listdir(capturas_dir):
            item_path = os.path.join(capturas_dir, item)
            try:
                # Solo eliminar archivos individuales de capturas en vivo, NO tocar subcarpetas de referencia
                if os.path.isfile(item_path):
                    os.remove(item_path)
            except Exception as e:
                errors.append(f"No se pudo eliminar {item}: {str(e)}")
        print("Proto1: Limpieza de capturas en vivo completada.")

    # Truncate resultados.csv to just headers
    csv_path = os.path.join(DIRECTORY, 'resultados.csv')
    if os.path.exists(csv_path):
        try:
            with open(csv_path, 'w', encoding='utf-8') as f:
                f.write("archivo,delta_snr_db,similitud_coseno,resultado,confianza_pct,timestamp\n")
            print("Proto1: resultados.csv reiniciado.")
        except Exception as e:
            errors.append(f"No se pudo reiniciar resultados.csv: {str(e)}")

    # Truncate watcher.log
    log_path = os.path.join(DIRECTORY, 'watcher.log')
    if os.path.exists(log_path):
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write("")
            print("Proto1: watcher.log reiniciado.")
        except Exception as e:
            errors.append(f"No se pudo reiniciar watcher.log: {str(e)}")

    # Truncate logs/detecciones.csv (Capa 2 MAVLink confirmations)
    detecciones_path = os.path.join(DIRECTORY, 'logs', 'detecciones.csv')
    if os.path.exists(detecciones_path):
        try:
            with open(detecciones_path, 'w', encoding='utf-8') as f:
                f.write("timestamp_utc,msg_id,nivel_senal_db,fuente,armado,tipo_vehiculo,autopilot\n")
            print("Proto1: logs/detecciones.csv reiniciado.")
        except Exception as e:
            errors.append(f"No se pudo reiniciar detecciones.csv: {str(e)}")

    if len(errors) == 0:
        response = {"status": "success", "message": "Todas las capturas y reportes fueron eliminados correctamente."}
    else:
        response = {
            "status": "success",
            "message": "Se limpiaron la mayoría de capturas y reportes, pero algunos archivos estaban bloqueados por estar en uso:\n" + "\n".join(errors)
        }
    return JSONResponse(content=response)

@app.post("/api/start-detection")
async def start_detection(request: Request):
    """Arranca el proceso de captura correspondiente al modo solicitado.

    Modos disponibles:
      - "proto1" (default): lanza main.py — Capa 1 SDR + Capa 2 MAVLink serie.
        Reinicia resultados.csv antes de arrancar para que el dashboard
        muestre solo los datos de la sesión activa.
    Usa el Python del virtualenv (.venv/Scripts/python.exe) si existe,
    con PYTHONIOENCODING=utf-8 para que los emojis lleguen al log del servidor.
    No lanza un segundo proceso si el anterior sigue corriendo (guard de poll).
    """
    global captura_proc
    try:
        venv_python = os.path.join(DIRECTORY, '.venv', 'Scripts', 'python.exe')
        python_exe  = venv_python if os.path.exists(venv_python) else sys.executable
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        csv_path = os.path.join(DIRECTORY, 'resultados.csv')
        if os.path.exists(csv_path):
            with open(csv_path, 'w', encoding='utf-8') as f:
                f.write("archivo,delta_snr_db,similitud_coseno,resultado,confianza_pct,timestamp\n")

        if captura_proc is None or captura_proc.poll() is not None:
            captura_proc = subprocess.Popen(
                [python_exe, os.path.join(DIRECTORY, 'main.py')],
                cwd=DIRECTORY,
                env=env
            )
            print("Servidor: main.py iniciado (Capa 1 SDR + Capa 2 MAVLink).")
        return JSONResponse(content={
            "status": "success",
            "message": "Detección activa: Capa 1 SDR + Capa 2 MAVLink serie (COM8)."
        })

    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@app.post("/api/stop-detection")
async def stop_detection(request: Request):
    """Detiene el proceso de captura indicado por el modo del request body.

    Intenta terminate() primero
    (SIGTERM, permite limpieza) y si el proceso no termina en 2 s hace kill()
    (SIGKILL, forzado). Pone el handle del proceso a None tras detenerlo para
    que get_detection_status() refleje el estado correcto.
    """
    global captura_proc
    stopped = []
    try:
        if captura_proc is not None and captura_proc.poll() is None:
            captura_proc.terminate()
            try:
                captura_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                captura_proc.kill()
            stopped.append("main.py")
        captura_proc = None

        return JSONResponse(content={
            "status": "success",
            "message": f"Detenido: {', '.join(stopped) if stopped else 'ningún proceso activo'}"
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# ==========================================
# WebSocket — canal de eventos en tiempo real
# ==========================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Mantiene la conexión WebSocket con el dashboard y gestiona desconexiones.

    Agrega el cliente a connected_clients al conectar y lo elimina al
    desconectar. Los watchers background usan connected_clients para saber
    a quién enviar eventos — si la lista está vacía, no generan tráfico.

    El bucle receive_text() solo sirve para mantener la conexión viva;
    el dashboard no envía mensajes al servidor por este canal.
    """
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            # Mantener conexión viva
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
        print("Cliente WebSocket desconectado")

# Serve /gui/ at root
@app.get("/")
async def serve_index():
    return RedirectResponse(url="/gui/")

# Mount remaining static files at root (html=True allows serving index.html in /gui/)
app.mount("/", StaticFiles(directory=DIRECTORY, html=True), name="static")

if __name__ == "__main__":
    os.chdir(DIRECTORY)
    uvicorn.run("server:app", host="127.0.0.1", port=PORT, log_level="info")
