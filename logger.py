"""
capa2_mavlink/logger.py — Registro CSV de detecciones MAVLink confirmadas (Capa 2)

Escribe una fila en logs/detecciones.csv por cada HEARTBEAT MAVLink confirmado.
El servidor FastAPI (server.py) vigila ese archivo con watch_detecciones() y
emite los datos por WebSocket al dashboard en tiempo real.

Diseño:
  - _inicializar_log() es idempotente: crea directorio y archivo solo la primera
    vez, nunca sobreescribe datos de sesiones anteriores.
  - registrar_deteccion() serializa las escrituras con un Lock de módulo:
    mavlink_parser.py puede llamarlo desde múltiples hilos sin corromper el CSV.
  - obtener_detecciones() lee el CSV completo y devuelve una lista de dicts,
    útil para el dashboard sin acoplarlo al formato interno del archivo.

Compatibilidad de nombres con mavlink_parser.py:
  El parámetro Python se llama snr_db (terminología SDR estándar);
  la columna CSV se llama nivel_senal_db (spec del proyecto).
  El mapeo ocurre dentro de registrar_deteccion().
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# =============================================================================
# Paths
# =============================================================================

# Dos niveles arriba de este archivo:
#   capa2_mavlink/logger.py → .parent → capa2_mavlink/ → .parent → Proto1/
# Así el CSV siempre se crea en la raíz del proyecto sin hardcodear rutas.
_ROOT:     Path = Path(__file__).parent.parent
_LOG_DIR:  Path = _ROOT / "logs"
_CSV_PATH: Path = _LOG_DIR / "detecciones.csv"

# Debe coincidir exactamente con lo que espera watch_detecciones() en server.py:
# ese código parsea cols[1] como msg_id y cols[4] como armado.
# Cambiar el orden de columnas aquí rompe el dashboard sin error visible.
_CABECERA: list[str] = [
    "timestamp_utc", "msg_id", "nivel_senal_db", "fuente",
    "armado", "tipo_vehiculo", "autopilot",
]

# =============================================================================
# Estado interno del módulo
# =============================================================================

# Lock global de módulo: cualquier hilo que llame a registrar_deteccion()
# espera aquí hasta que el anterior termine de escribir su fila.
# Sin esto, dos hilos abriendo el archivo en modo append simultáneamente
# pueden entrelazar bytes y corromper una fila del CSV.
_lock: threading.Lock = threading.Lock()

# Evita llamar a mkdir y a exists() en cada detección.
# Una vez inicializado en la sesión, el directorio y el archivo ya existen.
_inicializado: bool = False


# =============================================================================
# Función privada de inicialización
# =============================================================================

def _inicializar_log() -> None:
    """Crea logs/ y detecciones.csv si no existen; no hace nada si ya existen.

    Se llama al inicio de cada registrar_deteccion() y obtener_detecciones().
    La bandera _inicializado evita el overhead de I/O en cada detección
    una vez que el directorio y archivo ya están creados.

    No sobreescribe el CSV si ya tiene datos: las detecciones de sesiones
    anteriores se conservan y las nuevas se agregan al final (modo append).
    """
    global _inicializado

    if _inicializado:
        return   # ya inicializado en esta sesión, nada que hacer

    # Crear directorio (no falla si ya existe)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Crear CSV con cabecera solo si aún no existe
    if not _CSV_PATH.exists():
        with open(_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_CABECERA)

    _inicializado = True


# =============================================================================
# Función pública principal
# =============================================================================

def registrar_deteccion(
    timestamp_utc: str,
    msg_id: int,
    snr_db: Optional[float],           # columna CSV: nivel_senal_db
    fuente: str,
    armed: Optional[bool] = None,      # columna CSV: armado
    vehicle_type: Optional[str] = None, # columna CSV: tipo_vehiculo
    autopilot_name: Optional[str] = None, # columna CSV: autopilot
) -> None:
    """Registra una detección MAVLink confirmada en el CSV y en consola.

    Cada llamada agrega exactamente una fila al archivo CSV.  La escritura
    está protegida por un Lock de módulo para ser segura en entornos
    multi-hilo (captura.py lanza un hilo de análisis por frame).

    Parameters
    ----------
    timestamp_utc : str
        Marca de tiempo ISO en UTC.  Ejemplo: "2026-05-21 14:32:01 UTC".
        Generado por ``datetime.now(timezone.utc).strftime(...)`` en el llamador.
    msg_id : int
        Identificador del mensaje MAVLink detectado (0 = HEARTBEAT).
    snr_db : float | None
        Nivel de señal en dB proveniente del pipeline IQ.
        Se almacena como ``nivel_senal_db`` en el CSV.
        None se convierte a la cadena "N/A".
    fuente : str
        Origen de la detección.  Valores esperados: "ota_iq_v1", "ota_iq_v2",
        "serial" u otro identificador de pipeline.

    Side effects
    ------------
    - Crea ``logs/`` y ``logs/detecciones.csv`` si no existen.
    - Agrega una fila al CSV.
    - Imprime una línea de confirmación en stdout.
    """
    _inicializar_log()

    nivel_str  = f"{snr_db:.2f}" if snr_db is not None else "N/A"
    armed_str  = str(armed)        if armed        is not None else "N/A"
    vtype_str  = vehicle_type      if vehicle_type  is not None else "N/A"
    ap_str     = autopilot_name    if autopilot_name is not None else "N/A"

    fila = [timestamp_utc, msg_id, nivel_str, fuente, armed_str, vtype_str, ap_str]

    # Solo un hilo escribe a la vez; el resto espera en el lock.
    # El bloque with libera el lock automáticamente aunque falle la escritura.
    with _lock:
        with open(_CSV_PATH, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(fila)

    # Imprimir fuera del lock: stdout tiene su propio mecanismo de sincronización
    # y no necesita el lock del CSV. Mantenerlo dentro alargaría el tiempo de
    # bloqueo para otros hilos que quieran escribir.
    print(
        f"[logger] ✔ Detección registrada — "
        f"msg_id={msg_id} | snr={nivel_str} dB | fuente={fuente} | {timestamp_utc}"
    )


# =============================================================================
# Consulta del historial
# =============================================================================

def obtener_detecciones() -> list[dict]:
    """Lee el CSV completo y devuelve las detecciones como lista de dicts.

    Cada dict tiene las claves de _CABECERA: timestamp_utc, msg_id,
    nivel_senal_db, fuente, armado, tipo_vehiculo, autopilot.

    No requiere el lock porque solo lee: el SO garantiza que una lectura
    secuencial no puede obtener una fila a medias escrita por append.

    Returns:
        list[dict]: Detecciones en orden de inserción. Lista vacía si el
            archivo no existe aún o contiene solo la cabecera.
    """
    _inicializar_log()

    detecciones: list[dict] = []

    try:
        with open(_CSV_PATH, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for fila in reader:
                detecciones.append(dict(fila))
    except FileNotFoundError:
        # No debería ocurrir tras _inicializar_log(), pero puede pasar si
        # alguien elimina el archivo manualmente entre la inicialización y la lectura.
        pass
    except csv.Error as exc:
        print(f"[logger] AVISO: CSV malformado en {_CSV_PATH}: {exc}")

    return detecciones
