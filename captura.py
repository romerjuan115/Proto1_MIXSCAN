# captura.py  →  D:\Proto1\
#
# NOTA: Los parámetros de radio (frecuencia, sample rate, buffer, ganancia)
# se importan desde common/config.py. NO los redefinas aquí.
# Consulta common/config.py para cambiarlos o entender su justificación.

import adi
import numpy as np
import soundfile as sf
import time
import sys
import os
import threading
import queue
import winsound
from pathlib import Path
from datetime import datetime

# Forzar UTF-8 en stdout/stderr para soportar emojis en consola Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from common.config import (
    FRECUENCIA_HZ,       # 433.5 MHz — frecuencia central SDR
    SAMPLE_RATE,         # 61.44 Msps — tasa de muestreo validada
    BUFFER_SIZE,         # 61 440 000 muestras ≈ 1 s
    GANANCIA_DB,         # 30 dB — ganancia LNA/IF
    NORMALIZAR_CAPTURA,  # CRÍTICO: debe ser False
    VENTANA_VOTOS,       # 5 clips — ventana de votación
    VOTOS_MINIMOS,       # 2 votos para confirmar dron
)
assert not NORMALIZAR_CAPTURA, "NORMALIZAR_CAPTURA debe ser False (ver common/config.py)"

# ──────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE RADIO
# ──────────────────────────────────────────────────────────────
URI   = "ip:192.168.2.1"
RF_BW = 56_000_000  # 56 MHz de ancho de banda RF — local, no está en common/config.py
CARPETA_SALIDA = Path(r"D:\Proto1\capturas")
CARPETA_SALIDA.mkdir(exist_ok=True)
LOG_PATH       = r"D:\Proto1\watcher.log"

# ──────────────────────────────────────────────────────────────
# ALERTAS SONORAS
# ──────────────────────────────────────────────────────────────
ALERTA_DRON  = "[!] ALERTA: DRON DETECTADO"
ALERTA_LIBRE = "[OK] SIN DRON - ambiente limpio"
SONIDO_DRON      = [(1200, 300), (1200, 300), (1200, 300)]  # 3 pitidos agudos — dron aparece
SONIDO_LIBRE     = [(400,  150)]                             # 1 pitido suave   — monitoreo normal
SONIDO_DRON_FIN  = [(900, 200), (600, 200), (400, 300)]     # 3 pitidos descendentes — dron desaparece

def log(msg, nivel="INFO"):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{nivel}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def reproducir(secuencia):
    def _play():
        for freq, dur in secuencia:
            try:
                winsound.Beep(freq, dur)
            except RuntimeError:
                try:
                    winsound.MessageBeep(winsound.MB_OK)
                except Exception:
                    pass
            if len(secuencia) > 1:
                time.sleep(0.08)
    threading.Thread(target=_play, daemon=True).start()

def test_sonidos():
    print("\n  Probando sonidos...")
    print("  Sin dron ->", end=" ", flush=True)
    reproducir(SONIDO_LIBRE)
    time.sleep(0.6)
    print("OK")
    print("  Dron     ->", end=" ", flush=True)
    reproducir(SONIDO_DRON)
    time.sleep(1.2)
    print("OK\n")

# ──────────────────────────────────────────────────────────────
# IMPORTAR DETECTOR
# ──────────────────────────────────────────────────────────────
try:
    import detector as det
except ImportError:
    print("ERROR: No se encontró detector.py")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────
# WORKER DE ANÁLISIS EN MEMORIA (CONSUMIDOR)
# ──────────────────────────────────────────────────────────────
def analisis_worker(q, hook_dron=None):
    log("Construyendo referencia de ruido de fondo...")
    try:
        freqs_ref, ref_psd_db = det.construir_referencia()
        det.ref_psd_db = ref_psd_db
        det.freqs_ref  = freqs_ref
        log("Referencia de ruido construida correctamente.")
    except Exception as e:
        log(f"Error construyendo referencia: {e}", "ERROR")
        os._exit(1)

    det.init_csv()
    log(f"CSV inicializado: {det.CSV_PATH}")
    test_sonidos()

    log("Worker activo — esperando frames en memoria...")
    log("  1 pitido suave = SIN DRON")
    log("  3 pitidos agudos = DRON DETECTADO 🚨")

    # ── Ventana de votación TDMA ─────────────────────────────────
    # El SiK alterna slots TX/RX (~50% duty cycle), por eso clips
    # consecutivos pueden oscilar entre DETECTADO y SIN DRON.
    # Votación: si >= VOTOS_MINIMOS de los últimos VENTANA_CLIPS
    # detectan dron → confirmamos DRON DETECTADO.
    # VENTANA_VOTOS y VOTOS_MINIMOS vienen de common/config.py
    historial: list[str] = []   # últimos resultados individuales
    dron_confirmado_anterior = False  # evita re-disparar el hook

    while True:
        item = q.get()
        if item is None:
            break

        nombre_str, stereo = item
        nombre_obj = CARPETA_SALIDA / nombre_str

        try:
            # Extraer solo el fragmento necesario para el análisis
            muestras_analisis = stereo[:1048576]
            mono = muestras_analisis.mean(axis=1).astype(np.float64)

            # Análisis matemático — banda estrecha para maximizar ΔSNR del SiK
            freqs_s, psd_db = det.calcular_psd(mono, SAMPLE_RATE)
            delta_snr = (det.potencia_banda(freqs_s, psd_db) -
                         det.potencia_banda(det.freqs_ref, det.ref_psd_db))
            coseno_val = det.similitud_coseno(det.ref_psd_db, psd_db)
            resultado, conf = det.decision(delta_snr, coseno_val)
            ts_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # ── Ventana de votación ───────────────────────────────
            historial.append(resultado)
            if len(historial) > VENTANA_VOTOS:
                historial.pop(0)
            votos_dron = historial.count("DRON DETECTADO")
            dron_confirmado = (votos_dron >= VOTOS_MINIMOS)

            # Guardar resultados en el CSV del dashboard
            det.append_csv([
                nombre_str,
                f"{delta_snr:.4f}",
                f"{coseno_val:.4f}",
                resultado,
                conf,
                ts_actual
            ])

            # ── Mostrar alerta y reproducir sonido ───────────────
            if resultado == "DRON DETECTADO":
                log(f"{nombre_str} → {resultado} ({conf}%) | ΔSNR={delta_snr:+.2f} dB | Cos={coseno_val:.4f} "
                    f"[votos: {votos_dron}/{len(historial)}]")
            else:
                log(f"{nombre_str} → {resultado} ({conf}%) | ΔSNR={delta_snr:+.2f} dB "
                    f"[votos: {votos_dron}/{len(historial)}]")

            if dron_confirmado:
                reproducir(SONIDO_DRON)
                # Transición SIN → CON dron: primera confirmación
                if not dron_confirmado_anterior:
                    log(f"🚨 DRON CONFIRMADO ({votos_dron}/{len(historial)} clips) — disparando alerta")
                    if hook_dron is not None:
                        hook_dron(delta_snr)
                    # Guardar evidencia del clip que confirmó
                    log(f"Guardando evidencia en disco: {nombre_str}")
                    threading.Thread(
                        target=sf.write,
                        args=(nombre_obj, stereo, SAMPLE_RATE),
                        kwargs={"subtype": "FLOAT"},
                        daemon=True
                    ).start()
            else:
                # Transición CON → SIN dron: el dron acaba de desaparecer
                if dron_confirmado_anterior:
                    log(f"✅ DRON PERDIDO — zona despejada ({votos_dron}/{len(historial)} clips con señal)", "INFO")
                    reproducir(SONIDO_DRON_FIN)
                else:
                    reproducir(SONIDO_LIBRE)

            dron_confirmado_anterior = dron_confirmado

        except Exception as e:
            log(f"Error analizando buffer de memoria: {e}", "ERROR")

        q.task_done()

# ──────────────────────────────────────────────────────────────
# BUCLE PRINCIPAL (PRODUCTOR)
# ──────────────────────────────────────────────────────────────
def main(hook_dron=None):
    print("=======================================================")
    print("  PROTO1 v3.0 — LIVE DETECTOR (IN-MEMORY)")
    print("=======================================================")
    print("Conectando al PlutoSDR...")
    sdr = adi.Pluto(uri=URI)
    sdr.rx_lo                   = FRECUENCIA_HZ
    sdr.sample_rate             = SAMPLE_RATE
    sdr.rx_rf_bandwidth         = RF_BW
    sdr.rx_buffer_size          = BUFFER_SIZE
    sdr.gain_control_mode_chan0 = "manual"
    sdr.rx_hardwaregain_chan0   = GANANCIA_DB

    print(f"[OK] Listo — {FRECUENCIA_HZ/1e6} MHz @ {SAMPLE_RATE/1e6} MSPS")
    
    # Iniciar worker
    q = queue.Queue(maxsize=3) # Evitar saturar RAM si el procesador no da abasto
    worker_thread = threading.Thread(target=analisis_worker, args=(q,), kwargs={"hook_dron": hook_dron}, daemon=True)
    worker_thread.start()

    print("   Iniciando captura en memoria... (Ctrl+C para detener)\n")
    clip_num = 0
    try:
        while True:
            # Captura desde SDR (bloqueante por 1s aprox)
            raw    = sdr.rx()
            iq     = raw.astype(np.complex64)
            stereo = np.column_stack([iq.real.astype(np.float32),
                                      iq.imag.astype(np.float32)])

            ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
            nombre = f"clip_live_{ts}_{clip_num:04d}.wav"
            
            # Enviar a analizar en memoria
            try:
                q.put_nowait((nombre, stereo))
            except queue.Full:
                log("Cola llena, saltando frame (CPU no da abasto)", "WARN")

            clip_num += 1
            time.sleep(0.1) # Breve pausa para no bloquear hardware continuo si no es necesario

    except KeyboardInterrupt:
        print(f"\nCaptura detenida. Total clips procesados: {clip_num}")
    finally:
        del sdr
        log("Cerrando sistema.")

if __name__ == "__main__":
    main()