# captura_ruido.py  →  D:\Proto1\
#
# Script ONE-SHOT para capturar la referencia de ruido limpio.
#
# INSTRUCCIONES:
#   1. Apaga el dron (o desconecta su radio SiK)
#   2. Ejecuta:  python captura_ruido.py
#   3. Espera a que capture 10 clips (~25 segundos)
#   4. Luego enciende el dron y corre: python captura.py  (o main.py)
#
# Los clips se guardan en: D:\Proto1\capturas\ruido\
# =============================================================================

import adi
import numpy as np
import soundfile as sf
import time
from pathlib import Path
from datetime import datetime
from common.config import FRECUENCIA_HZ, SAMPLE_RATE, BUFFER_SIZE, GANANCIA_DB

URI    = "ip:192.168.2.1"
RF_BW  = 56_000_000
SALIDA = Path(r"D:\Proto1\capturas\ruido")
N_CLIPS = 10   # cuántos clips capturar como referencia

SALIDA.mkdir(parents=True, exist_ok=True)

print("=" * 60)
print("  CAPTURA DE REFERENCIA DE RUIDO LIMPIO")
print("=" * 60)
print(f"  Frecuencia  : {FRECUENCIA_HZ / 1e6:.1f} MHz")
print(f"  Sample rate : {SAMPLE_RATE / 1e6:.2f} Msps")
print(f"  Ganancia    : {GANANCIA_DB} dB")
print(f"  Clips       : {N_CLIPS}")
print(f"  Destino     : {SALIDA}")
print("=" * 60)
print()

# ── Verificación de seguridad ────────────────────────────────────
existentes = list(SALIDA.glob("clip_live_*.wav"))
if existentes:
    print(f"  ⚠  Ya existen {len(existentes)} clips en la carpeta ruido.")
    resp = input("  ¿Sobrescribir y capturar nuevos? (s/N): ").strip().lower()
    if resp != "s":
        print("  Operación cancelada.")
        raise SystemExit(0)
    for f in existentes:
        f.unlink()
    print(f"  {len(existentes)} clips anteriores eliminados.\n")

# ── Confirmación del operador ────────────────────────────────────
print("  ⚡ IMPORTANTE: asegúrate de que el DRON esté APAGADO")
print("     (o su radio SiK desconectada) antes de continuar.")
input("\n  Presiona ENTER cuando el ambiente esté limpio...")
print()

# ── Conectar al SDR ──────────────────────────────────────────────
print("  Conectando al PlutoSDR...")
sdr = adi.Pluto(uri=URI)
sdr.rx_lo                   = FRECUENCIA_HZ
sdr.sample_rate             = SAMPLE_RATE
sdr.rx_rf_bandwidth         = RF_BW
sdr.rx_buffer_size          = BUFFER_SIZE
sdr.gain_control_mode_chan0 = "manual"
sdr.rx_hardwaregain_chan0   = GANANCIA_DB
print(f"  [OK] PlutoSDR listo\n")

# ── Captura ──────────────────────────────────────────────────────
for i in range(N_CLIPS):
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre = f"clip_live_{ts}_{i:04d}.wav"
    ruta   = SALIDA / nombre

    raw    = sdr.rx()
    iq     = raw.astype(np.complex64)
    stereo = np.column_stack([iq.real.astype(np.float32),
                               iq.imag.astype(np.float32)])

    sf.write(str(ruta), stereo, SAMPLE_RATE, subtype="FLOAT")
    print(f"  [{i+1:2d}/{N_CLIPS}] Guardado: {nombre}")
    time.sleep(0.1)

del sdr

print()
print("=" * 60)
print(f"  ✅ Referencia lista — {N_CLIPS} clips en:")
print(f"     {SALIDA}")
print()
print("  Ahora puedes encender el dron y ejecutar:")
print("    python captura.py    ← solo Capa 1 (detección)")
print("    python main.py       ← Capa 1 + Capa 2 (detección + OTA)")
print("=" * 60)
