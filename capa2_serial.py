"""
capa2_mavlink/capa2_serial.py — Verificación MAVLink por puerto serie (Capa 2)

Lee el bytestream MAVLink directamente del módulo SiK ground conectado por USB
y busca frames HEARTBEAT (msg_id=0) para confirmar que el dron está activo
y transmitiendo a través del enlace de telemetría en 433 MHz.

Esta implementación reemplazó la Capa 2 OTA (demodulación GFSK con el PlutoSDR)
porque elimina la contención de hardware: el PlutoSDR queda exclusivo para Capa 1
y el módulo SiK ground demodula y entrega el MAVLink ya parseado por hardware.
Latencia < 1 s porque el SiK emite HEARTBEAT cada ~1 s por defecto.

Puerto y velocidad configurados en common/config.py (SERIAL_PORT, BAUD_RATE_SERIAL).
Si falla este módulo, Capa 2 no confirma ningún dron pero Capa 1 sigue detectando.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import serial

from common.config import SERIAL_PORT, BAUD_RATE_SERIAL
from capa2_mavlink.mavlink_parser import (
    buscar_frames_mavlink,
    HEARTBEAT_MSG_ID,
)
from capa2_mavlink.logger import registrar_deteccion

# 4 KB cubre entre 15 y 30 frames MAVLink completos (un frame v1 típico son 17 bytes).
# Con 57 600 baud el SiK produce ~7 200 bytes/s, así que 4 KB representa
# menos de 600 ms de stream — suficiente para que el parser no pierda frames
# sin que el buffer crezca ilimitado entre lecturas.
_BUFFER_MAX: int = 4096


# =============================================================================
# Función principal
# =============================================================================

def ejecutar_capa2_serial(
    delta_snr:   Optional[float] = None,
    timeout_seg: float           = 10.0,
    puerto:      Optional[str]   = None,
    baud:        Optional[int]   = None,
) -> bool:
    """Busca un HEARTBEAT MAVLink en el stream serie del módulo SiK ground.

    Abre el puerto COM, lee bytes en bloques de 256 y acumula en un buffer circular
    sobre el que llama al parser MAVLink en cada iteración. Sale en cuanto encuentra
    el primer HEARTBEAT válido (CRC correcto, msg_id=0) o agota el timeout.

    El SiK emite HEARTBEAT cada ~1 s, así que con timeout=10 s hay ~10 intentos
    antes de declarar fallo. Eso absorbe los gaps TDMA y los momentos de silencio
    del protocolo sin disparar falsos negativos.

    Args:
        delta_snr (float | None): ΔSNR del clip de Capa 1 que disparó esta
            verificación, en dB. Se propaga al logger para enriquecer el CSV.
            None si se llama manualmente sin contexto de Capa 1.
        timeout_seg (float): Segundos máximos de espera (default 10 s).
            Con el SiK a 57 600 baud y HEARTBEAT cada 1 s, 10 s da margen
            para hasta 3 gaps consecutivos sin señal antes de declarar fallo.
        puerto (str | None): Puerto COM. None → usa SERIAL_PORT de config.py.
        baud (int | None): Baudios. None → usa BAUD_RATE_SERIAL de config.py.

    Returns:
        bool: True si se confirmó al menos un HEARTBEAT MAVLink con CRC válido.
              False si se agotó el timeout o el puerto no pudo abrirse.
    """
    _puerto = puerto or SERIAL_PORT
    _baud   = baud   or BAUD_RATE_SERIAL

    snr_str = f"{delta_snr:+.2f} dB" if delta_snr is not None else "N/A"
    print(
        f"\n[CAPA 2 SERIAL] Verificando HEARTBEAT MAVLink — "
        f"puerto={_puerto} | {_baud} bd | SNR ref={snr_str} | timeout={timeout_seg} s"
    )

    # ── Abrir puerto serie ────────────────────────────────────────────────────
    # timeout=0.5 s en el Serial: si no hay bytes disponibles, ser.read() devuelve
    # vacío en 0.5 s en lugar de bloquear indefinidamente. Permite que el bucle
    # while revise el timeout global en cada iteración sin bloquearse en I/O.
    try:
        ser = serial.Serial(_puerto, _baud, timeout=0.5)
    except serial.SerialException as exc:
        print(
            f"[CAPA 2 SERIAL] ERROR: No se pudo abrir {_puerto}: {exc}\n"
            f"  Verificar: módulo SiK conectado por USB, puerto correcto en config.py."
        )
        return False

    buffer   = bytearray()
    t0       = time.monotonic()
    intentos = 0

    try:
        while time.monotonic() - t0 < timeout_seg:
            # 256 bytes por lectura: con 57 600 baud entran ~7 bytes/ms,
            # así que 256 bytes representan ~35 ms de stream — granularidad
            # suficiente para no perder frames entre iteraciones del bucle.
            chunk = ser.read(256)
            if not chunk:
                continue

            intentos += 1
            buffer.extend(chunk)

            # Descartar los bytes más antiguos: el parser ya los procesó en la
            # iteración anterior. Solo necesitamos retener lo suficiente para
            # que un frame que llegó partido entre dos lecturas se complete.
            if len(buffer) > _BUFFER_MAX:
                buffer = buffer[-_BUFFER_MAX:]

            # ── Buscar frames MAVLink en el buffer acumulado ──────────────────
            # fuente="serial" distingue en el CSV las detecciones por puerto serie
            # de las detecciones OTA (fuente="ota_iq_v1/v2") de la rama experimental.
            frames = buscar_frames_mavlink(
                buffer,
                snr_db=delta_snr,
                fuente="serial",
            )
            heartbeats = [f for f in frames if f["msg_id"] == HEARTBEAT_MSG_ID]

            if heartbeats:
                hb      = heartbeats[0]
                elapsed = time.monotonic() - t0
                print(
                    f"[CAPA 2 SERIAL] HEARTBEAT confirmado en {elapsed:.1f} s "
                    f"(lectura #{intentos}) — "
                    f"MAVLink v{hb['version']} | SYS={hb['sys_id']} | COMP={hb['comp_id']}"
                )
                return True

    except serial.SerialException as exc:
        print(f"[CAPA 2 SERIAL] ERROR durante lectura: {exc}")

    finally:
        # Cerrar siempre: si no liberamos el puerto, el SO mantiene el COM abierto
        # y la próxima llamada a ejecutar_capa2_serial() falla con PermissionError.
        try:
            ser.close()
        except Exception:
            pass

    elapsed = time.monotonic() - t0
    print(
        f"[CAPA 2 SERIAL] Sin HEARTBEAT tras {elapsed:.1f} s "
        f"({intentos} lecturas).\n"
        f"  Posibles causas:\n"
        f"    - Dron apagado o fuera de rango\n"
        f"    - QGroundControl u otra app acaparando {_puerto}\n"
        f"    - Módulo SiK desconectado o COM port incorrecto en config.py"
    )
    return False


# =============================================================================
# Diagnóstico standalone — ejecutar directamente para verificar el enlace
# =============================================================================

def diagnostico_serial(
    duracion_seg: float        = 5.0,
    puerto:       Optional[str] = None,
    baud:         Optional[int] = None,
) -> None:
    """Captura el stream serie durante duracion_seg segundos y muestra estadísticas.

    Diseñado para ejecutarse antes del despliegue y verificar que el módulo SiK
    está emitiendo datos. Muestra bytes recibidos, marcadores STX v1/v2 y frames
    MAVLink con CRC válido — permite diagnosticar problemas de baud rate o
    puerto incorrecto antes de iniciar la captura real.

    Ejecutar directamente con: python -m capa2_mavlink.capa2_serial

    Args:
        duracion_seg (float): Duración del diagnóstico en segundos (default 5 s).
        puerto (str | None): Puerto COM. None → usa SERIAL_PORT de config.py.
        baud (int | None): Baudios. None → usa BAUD_RATE_SERIAL de config.py.
    """
    _puerto = puerto or SERIAL_PORT
    _baud   = baud   or BAUD_RATE_SERIAL

    print(f"\n[DIAGNÓSTICO SERIAL] Puerto={_puerto} | {_baud} bd | {duracion_seg} s")

    try:
        ser = serial.Serial(_puerto, _baud, timeout=0.5)
    except serial.SerialException as exc:
        print(f"[DIAGNÓSTICO SERIAL] ERROR abriendo puerto: {exc}")
        return

    buffer      = bytearray()
    total_bytes = 0
    t0          = time.monotonic()

    try:
        while time.monotonic() - t0 < duracion_seg:
            chunk = ser.read(512)
            if chunk:
                buffer.extend(chunk)
                total_bytes += len(chunk)
    finally:
        ser.close()

    # ── Estadísticas ──────────────────────────────────────────────────────────
    # Si total_bytes == 0: el módulo no está emitiendo — verificar cable USB y COM.
    # Si hay bytes pero cero frames: el baud rate es incorrecto — revisar BAUD_RATE_SERIAL.
    # Si hay frames pero cero HEARTBEAT: el dron no está transmitiendo todavía.
    from capa2_mavlink.mavlink_parser import MAVLINK_V1_STX, MAVLINK_V2_STX
    n_stx_v1 = buffer.count(MAVLINK_V1_STX)
    n_stx_v2 = buffer.count(MAVLINK_V2_STX)
    frames   = buscar_frames_mavlink(buffer, snr_db=None, fuente="serial_diag")
    n_hb     = sum(1 for f in frames if f["msg_id"] == HEARTBEAT_MSG_ID)

    sep = "─" * 52
    print(f"\n  {sep}")
    print(f"  DIAGNÓSTICO SERIAL — resultados")
    print(f"  {sep}")
    print(f"  Bytes recibidos        : {total_bytes:>8,}")
    print(f"  Marcadores STX v1 (0xFE): {n_stx_v1:>6}")
    print(f"  Marcadores STX v2 (0xFD): {n_stx_v2:>6}")
    print(f"  Frames MAVLink (CRC ok): {len(frames):>6}")
    print(f"  HEARTBEAT (msg_id=0)   : {n_hb:>6}  "
          f"{'<-- DRON ACTIVO' if n_hb > 0 else '(ninguno)'}")
    print(f"  {sep}")

    if n_hb > 0:
        print("  RESULTADO: Enlace SiK activo, dron transmitiendo HEARTBEAT.")
    elif len(frames) > 0:
        print("  RESULTADO: Datos MAVLink recibidos pero sin HEARTBEAT.")
        print("             Normal si el dron acaba de arrancar.")
    elif total_bytes > 0:
        print("  RESULTADO: Bytes recibidos pero sin frames MAVLink válidos.")
        print(f"             Revisar BAUD_RATE_SERIAL ({_baud}) en config.py.")
    else:
        print("  RESULTADO: Sin datos. Verificar módulo SiK y puerto COM.")
    print()


if __name__ == "__main__":
    # Modo diagnóstico directo: python -m capa2_mavlink.capa2_serial
    diagnostico_serial(duracion_seg=5.0)
