"""
main.py — Punto de entrada del sistema MIXSCAN (Capa 1 + Capa 2)

Orquesta dos pipelines paralelos mediante hilos daemon:

  hilo_capa1  [daemon] → captura.main(hook_dron)
      └── analisis_worker confirma DRON DETECTADO
               └── hook_dron(delta_snr)
                      ├── guarda _ultimo_snr bajo lock
                      └── _evento_dron.set()   ← despierta al dispatcher

  hilo_dispatcher  [daemon] → espera _evento_dron
      └── lanza hilo_capa2  [daemon] → ejecutar_capa2_serial()
               Lee MAVLink del SiK ground por COM8 — sin tocar el PlutoSDR.

La Capa 1 nunca se detiene mientras el sistema está activo: el SDR captura
de forma continua independientemente de lo que haga la Capa 2.
Esa es la razón de toda la arquitectura de hilos — si la Capa 2 bloqueara
el hilo principal, se perderían frames de captura durante la verificación.

Mecanismo de señal: threading.Event + threading.Lock
  - Event: disparo instantáneo (set/wait/clear), sin polling activo.
  - Lock:  protege _ultimo_snr frente a race conditions entre hilos.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import captura                                               # Capa 1
from capa2_mavlink.capa2_serial import ejecutar_capa2_serial  # Capa 2
from common.config import (
    FRECUENCIA_HZ,
    SAMPLE_RATE,
    BUFFER_SIZE,
    GANANCIA_DB,
    UMBRAL_DELTA_SNR,
    SERIAL_PORT,
    BAUD_RATE_SERIAL,
)

# =============================================================================
# Mecanismo de señal entre Capa 1 y Capa 2
# =============================================================================

# Event que se activa cuando analisis_worker confirma DRON DETECTADO.
# .wait() bloquea el dispatcher sin consumir CPU (no hay polling).
# .clear() lo reinicia para que la próxima detección vuelva a disparar.
_evento_dron: threading.Event = threading.Event()

# Último ΔSNR medido en el frame que disparó la alerta de Capa 1.
# El lock es necesario porque hilo_capa1 escribe y hilo_dispatcher lee
# desde hilos distintos sin ninguna sincronización implícita.
_snr_lock:    threading.Lock  = threading.Lock()
_ultimo_snr:  Optional[float] = None


def _hook_dron(delta_snr: float) -> None:
    """Callback que conecta la detección de Capa 1 con el dispatcher de Capa 2.

    Se invoca desde hilo_capa1 en cada transición SIN→DETECTADO.
    Guarda el ΔSNR del frame confirmado y activa el Event para que el dispatcher
    lance ejecutar_capa2_serial() sin bloquear el bucle de captura SDR.

    Debe ser lo más corta posible: se ejecuta dentro del hilo de Capa 1
    y cualquier bloqueo aquí retrasa la captura del siguiente frame IQ.

    Args:
        delta_snr (float): ΔSNR en dB del frame que superó UMBRAL_DELTA_SNR.
            Se propaga a Capa 2 para enriquecer el registro MAVLink.
    """
    global _ultimo_snr
    with _snr_lock:
        _ultimo_snr = delta_snr
    _evento_dron.set()   # despierta al dispatcher


# =============================================================================
# Dispatcher de Capa 2
# =============================================================================

def _dispatcher_capa2() -> None:
    """Hilo daemon que espera señales de Capa 1 y lanza la verificación MAVLink.

    Corre en bucle infinito bloqueado en _evento_dron.wait(). Cuando Capa 1
    confirma un dron:
      1. Limpia el event para que la próxima detección vuelva a disparar.
      2. Lee _ultimo_snr bajo lock (escritura concurrente desde hilo_capa1).
      3. Comprueba que no haya una instancia de Capa 2 ya en curso.
      4. Lanza ejecutar_capa2_serial() en un hilo daemon independiente.

    El guard del paso 3 evita apilar hilos de Capa 2 si las detecciones
    llegan más rápido de lo que tarda la verificación serie (~10 s timeout).
    En ese caso se omite el disparo y se imprime un aviso.
    """
    hilo_capa2: Optional[threading.Thread] = None

    while True:
        _evento_dron.wait()      # bloquea hasta DRON DETECTADO
        _evento_dron.clear()     # reset para la próxima detección

        with _snr_lock:
            snr = _ultimo_snr

        # Guard: no lanzar Capa 2 si la anterior todavía está corriendo
        if hilo_capa2 is not None and hilo_capa2.is_alive():
            print(
                "[MAIN] ⚠ DRON detectado pero Capa 2 ya en curso — "
                "se omite este disparo para evitar contención del SDR."
            )
            continue

        print(
            f"[MAIN] 🚨 DRON DETECTADO — disparando Capa 2 serial "
            f"(SNR referencia: {f'{snr:+.2f} dB' if snr is not None else 'N/A'})"
        )

        hilo_capa2 = threading.Thread(
            target=_ejecutar_capa2_con_aviso,
            kwargs={"delta_snr": snr},
            daemon=True,
            name="hilo-capa2",
        )
        hilo_capa2.start()


def _ejecutar_capa2_con_aviso(delta_snr: Optional[float]) -> None:
    """Llama a ejecutar_capa2_serial() y reporta el resultado en consola.

    Los errores de puerto (módulo SiK desconectado, COM8 ocupado por otro proceso)
    los absorbe capa2_serial internamente y retorna False; aquí solo registramos
    el resultado para que el hilo de Capa 1 no se vea afectado por ninguna excepción
    inesperada que pudiera propagarse desde el stack serial.

    Args:
        delta_snr (float | None): ΔSNR propagado desde Capa 1. Se pasa al logger
            de capa2_serial para enriquecer la fila del CSV de detecciones.
    """
    try:
        confirmado = ejecutar_capa2_serial(delta_snr=delta_snr)
        if confirmado:
            print("[MAIN] ✔ Capa 2: HEARTBEAT MAVLink confirmado por puerto serie.")
        else:
            print("[MAIN] ✗ Capa 2: sin HEARTBEAT en la ventana de verificación.")
    except Exception as exc:
        print(f"[MAIN] ⚠ Error inesperado en Capa 2: {exc}")


# =============================================================================
# Banner de inicio
# =============================================================================

def _mostrar_banner() -> None:
    """Imprime en consola el banner de inicio con los parámetros activos del sistema.

    Útil para verificar de un vistazo que los valores importados de common/config.py
    son los esperados antes de que arranque la captura. Si algo está mal
    (ganancia, puerto COM, umbral) se ve aquí antes de que el SDR empiece a medir.
    """
    sep = "═" * 60
    print(f"\n{sep}")
    print("  PROTO1 — Sistema de Detección y Verificación OTA")
    print(f"  Versión 4.0  |  Capas: 1 (SDR raw) + 2 (OTA MAVLink)")
    print(sep)
    print()
    print("  ── CAPA 1: Detección SDR (parámetros validados) ──────")
    print(f"  Frecuencia      : {FRECUENCIA_HZ / 1e6:.1f} MHz")
    print(f"  Sample rate     : {SAMPLE_RATE / 1e6:.2f} Msps")
    print(f"  Buffer          : {BUFFER_SIZE:,} muestras  (~1 s)")
    print(f"  Ganancia        : {GANANCIA_DB} dB")
    print(f"  Umbral ΔSNR     : {UMBRAL_DELTA_SNR:+.1f} dB")
    print()
    print("  ── CAPA 2: Verificación MAVLink por puerto serie ─────")
    print(f"  Puerto COM      : {SERIAL_PORT}")
    print(f"  Baud rate SiK   : {BAUD_RATE_SERIAL:,} bd")
    print(f"  Método          : lectura directa USB → SiK ground module")
    print(f"  Sin contención  : PlutoSDR exclusivo de Capa 1")
    print(sep)
    print()


# =============================================================================
# Punto de entrada
# =============================================================================

def main() -> None:
    """Arranca el sistema completo: Capa 1 SDR + dispatcher + Capa 2 MAVLink.

    Lanza hilo_capa1 y hilo_dispatcher como daemons y entra en el keepalive
    del hilo principal. El keepalive verifica cada 2 s que hilo_capa1 siga vivo;
    si muere inesperadamente (SDR desconectado, error de buffer) rompe el bucle
    y el sistema termina limpiamente en el bloque finally.

    Notas:
        - Los hilos daemon mueren automáticamente cuando el proceso principal termina.
        - Ctrl+C en el hilo principal lanza KeyboardInterrupt y sale del keepalive.
        - No relanzamos hilo_capa1 si muere — requiere intervención manual
          porque el SDR puede estar en estado indeterminado tras un fallo.
    """
    _mostrar_banner()

    # ── Hilo Capa 1 ──────────────────────────────────────────────────────────
    # Si el PlutoSDR no está disponible, captura.main() lanza una excepción
    # que termina el hilo. El keepalive lo detecta en ~2 s y sale limpiamente.
    hilo_capa1 = threading.Thread(
        target=captura.main,
        kwargs={"hook_dron": _hook_dron},
        daemon=True,
        name="hilo-capa1",
    )

    # ── Hilo dispatcher de Capa 2 ─────────────────────────────────────────────
    hilo_dispatcher = threading.Thread(
        target=_dispatcher_capa2,
        daemon=True,
        name="hilo-dispatcher",
    )

    print("[MAIN] Iniciando Capa 1 (SDR capture)...")
    hilo_capa1.start()

    print("[MAIN] Iniciando dispatcher de Capa 2...")
    hilo_dispatcher.start()

    print("[MAIN] Sistema activo. Ctrl+C para detener.\n")

    # ── Keepalive del hilo principal ─────────────────────────────────────────
    # Los hilos daemon mueren si el proceso principal termina, por eso no podemos
    # salir del main thread. El sleep(2) cede CPU sin consumirla en un busy-loop,
    # y el check de is_alive() detecta caídas del SDR en menos de 2 segundos.
    try:
        while True:
            time.sleep(2)
            if not hilo_capa1.is_alive():
                print(
                    "\n[MAIN] ⚠ El hilo de Capa 1 terminó inesperadamente. "
                    "Verifica la conexión al PlutoSDR y reinicia el sistema."
                )
                break   # salir del keepalive; el finally cierra limpiamente

    except KeyboardInterrupt:
        print("\n[MAIN] Ctrl+C recibido — deteniendo sistema...")

    finally:
        print("[MAIN] Sistema detenido.")


if __name__ == "__main__":
    main()
