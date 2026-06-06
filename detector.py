"""
detector.py — Motor de detección espectral de la Capa 1 (MIXSCAN)

Construye una referencia de ruido de fondo promediando clips sin dron,
y para cada frame nuevo calcula el ΔSNR en la banda 0–3 MHz respecto
a esa referencia. Si ΔSNR ≥ UMBRAL_DELTA_SNR (2.0 dB) → DRON DETECTADO.

La similitud coseno se calcula en paralelo como métrica de diagnóstico
pero no dispara alertas por sí sola: con ruido real de baja intensidad
el coseno genera demasiados falsos negativos.

Si falla este módulo, captura.py no puede analizar ningún frame
y el sistema queda ciego aunque el SDR esté capturando correctamente.
"""

import os
import sys
import pathlib
import numpy as np
import scipy.io.wavfile as wav
import soundfile as sf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import csv
from datetime import datetime
from common.config import UMBRAL_DELTA_SNR

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────

# Carpeta de clips de ruido puro usados para construir la referencia de fondo.
# Deben ser capturas sin dron activo, idealmente del mismo entorno de despliegue.
# Cambiar la carpeta o reemplazar los clips invalida la referencia calibrada.
WAV_DIR    = r"D:\Proto1\capturas\ruido"
OUTPUT_DIR = r"D:\Proto1"
COMP_DIR   = os.path.join(OUTPUT_DIR, "comparaciones")  # PNGs de comparación espectral
CSV_PATH   = os.path.join(OUTPUT_DIR, "resultados.csv") # log de detecciones para el dashboard

# Los primeros 10 clips ordenados por nombre (= orden cronológico por timestamp).
# 10 es suficiente para promediar el piso de ruido con buena estabilidad estadística;
# usar más clips no mejora la referencia de forma apreciable y ralentiza el arranque.
REF_FILES = sorted(pathlib.Path(WAV_DIR).glob("clip_live_*.wav"))[:10]

if not REF_FILES:
    sys.exit("ERROR: No se encontraron clips clip_live_*.wav en: " + WAV_DIR)

# ─────────────────────────────────────────────
# UMBRALES — 2 clases
# ─────────────────────────────────────────────

# Lee de common/config.py (actualmente 2.0 dB). Ver allí la justificación experimental.
UMBRAL_SNR = UMBRAL_DELTA_SNR

# Coseno de similitud máximo para considerar una firma "distinta al ruido".
# Un coseno bajo significa que el espectro del clip se alejó de la referencia,
# lo que es consistente con una señal activa. Valor de 0.55 calibrado
# observando la distribución de cosenos en clips de ruido puro (S1–S3):
# todos quedaron por encima de 0.60; un dron real los empujó por debajo de 0.45.
# OJO: no usar como disparador único — ver decision() para la lógica actual.
UMBRAL_COS = 0.55

# El SiK 433 MHz ocupa ~1.7 MHz de ancho de banda (433.05–434.79 MHz en RF).
# En baseband (centrado en DC) eso aparece entre 0 y ~1.5 MHz.
# Usamos 3 MHz de margen para absorber desviaciones de configuración del módulo SiK
# y drift térmico del oscilador del PlutoSDR.
# Reducir la banda de integración de 28 MHz (total SDR) a 3 MHz da una ganancia
# teórica de ~+9.7 dB en ΔSNR: fue el cambio que hizo el sistema detectable a 6 m.
BANDA_MIN_HZ: int = 0          # Hz baseband (DC = 433.5 MHz en RF)
BANDA_MAX_HZ: int = 3_000_000  # 3 MHz — cubre ±1.5 MHz alrededor del centro

# ─────────────────────────────────────────────
# FUNCIONES CORE — NO MODIFICAR
# ─────────────────────────────────────────────

def leer_wav(ruta):
    """Lee un WAV y devuelve los primeros 1 048 576 samples como array mono float64.

    1 048 576 = 2^20 samples. Elegí potencia de dos para que la FFT (rfft) opere
    en su tamaño óptimo sin zero-padding. A 61.44 Msps equivale a ~17 ms de señal,
    suficiente para calcular la PSD con resolución de ~58 Hz por bin.

    Intenté con buffers más grandes (hasta 4×): el ΔSNR no mejoraba pero
    el tiempo de FFT subía linealmente. 2^20 es el punto de compromiso.

    Args:
        ruta (str | Path): Ruta al archivo WAV. Acepta formato float32 (soundfile)
            o int16 (scipy). Si tiene dos canales, promedia a mono.

    Returns:
        tuple[int, np.ndarray]: (sample_rate, array mono float64).
    """
    try:
        data, rate = sf.read(ruta, frames=1048576, dtype='float32')
    except Exception:
        rate, data = wav.read(ruta)
        if data.dtype != np.float32:
            data = data.astype(np.float32)
        if len(data) > 1048576:
            data = data[:1048576]

    if data.ndim == 2:
        mono = data.mean(axis=1).astype(np.float64)
    else:
        mono = data.astype(np.float64)
    return rate, mono


def calcular_psd(data, rate):
    """Calcula la densidad espectral de potencia (PSD) en dB sobre el array de entrada.

    Usa rfft (FFT real) porque las muestras son reales (mono float64).
    La normalización por N convierte la magnitud al cuadrado en densidad
    espectral, no en energía total, lo que hace la PSD comparable entre
    clips de distinta longitud.

    El +1e-12 antes del log10 evita log(0) en bins de potencia cero,
    que aparecen en silencio digital absoluto o en clips sintéticos.

    Args:
        data (np.ndarray): Array mono float64.
        rate (int): Frecuencia de muestreo en Hz.

    Returns:
        tuple[np.ndarray, np.ndarray]: (freqs_hz, psd_db) — mismo tamaño.
    """
    N      = len(data)
    fft    = np.fft.rfft(data)
    psd    = (np.abs(fft) ** 2) / N
    psd_db = 10 * np.log10(psd + 1e-12)
    freqs  = np.fft.rfftfreq(N, d=1.0 / rate)
    return freqs, psd_db


def potencia_media(psd_db):
    """Potencia media de toda la banda capturada, en dB.

    Usada como fallback en potencia_banda() y para las líneas de referencia
    en los PNGs de comparación. No se usa para tomar decisiones de detección
    porque diluye la señal narrowband del SiK en los 28 MHz del SDR.
    """
    return float(np.mean(psd_db))


def potencia_banda(freqs, psd_db, f_min=None, f_max=None):
    """Potencia media restringida al rango de frecuencias de interés.

    Mucho más sensible que potencia_media() porque no diluye la señal
    narrowband del SiK (~1.7 MHz) en los 56 MHz capturados por el SDR.

    Parameters
    ----------
    freqs   : array de frecuencias devuelto por calcular_psd (Hz, baseband)
    psd_db  : array PSD en dB del mismo tamaño que freqs
    f_min   : límite inferior en Hz (default: BANDA_MIN_HZ)
    f_max   : límite superior en Hz (default: BANDA_MAX_HZ)

    Returns
    -------
    float : potencia media en dB dentro de la banda
    """
    if f_min is None:
        f_min = BANDA_MIN_HZ
    if f_max is None:
        f_max = BANDA_MAX_HZ
    mask = (freqs >= f_min) & (freqs <= f_max)
    if not np.any(mask):
        return potencia_media(psd_db)   # fallback si la banda está fuera de rango
    return float(np.mean(psd_db[mask]))


def similitud_coseno(vec_a, vec_b):
    """Similitud coseno entre dos vectores PSD, con media centrada.

    El centrado (restar la media) elimina la componente DC de potencia absoluta
    y compara solo la forma del espectro. Sin centrado, dos PSDs con distinto
    nivel de ruido pero idéntica forma darían coseno < 1, lo que es un falso negativo.

    Los vectores se recortan al mínimo de sus longitudes para comparar
    clips de distinto tamaño sin zero-padding.

    Returns:
        float: Valor en [-1, 1]. Cerca de 1 → formas similares (ruido).
               Valores bajos → firma espectral distinta (candidato a señal).
    """
    min_len = min(len(vec_a), len(vec_b))
    a = vec_a[:min_len].astype(np.float64)
    b = vec_b[:min_len].astype(np.float64)
    a = a - np.mean(a)
    b = b - np.mean(b)
    num = np.dot(a, b)
    den = np.linalg.norm(a) * np.linalg.norm(b) + 1e-12
    return float(num / den)

def decision(delta_snr, coseno):
    """Clasifica un clip en DRON DETECTADO o SIN DRON a partir del ΔSNR.

    Lógica conservadora de una sola condición: solo el ΔSNR decide.
    El coseno entra como parámetro para que el CSV lo registre y facilitar
    futuros análisis, pero no dispara ninguna alerta por sí solo.

    Probé usar coseno como segundo disparador (AND con ΔSNR): generaba
    falsos negativos en clips reales de baja intensidad donde el coseno
    no bajaba lo suficiente aunque la potencia sí subía.

    Args:
        delta_snr (float): Diferencia de potencia en banda respecto a referencia (dB).
        coseno (float): Similitud coseno respecto a la PSD de referencia.

    Returns:
        tuple[str, int]: (etiqueta, confianza_pct).
            etiqueta es "DRON DETECTADO" o "SIN DRON".
            confianza_pct es 100 o 0 (sistema binario, sin gradiente).
    """
    if delta_snr >= UMBRAL_SNR:
        return "DRON DETECTADO", 100
    else:
        return "SIN DRON", 0

# ─────────────────────────────────────────────
# REFERENCIA DE RUIDO
# ─────────────────────────────────────────────

def construir_referencia():
    """Promedia las PSDs de los clips de referencia para obtener el piso de ruido.

    Lee hasta 10 clips de WAV_DIR, calcula la PSD de cada uno y promedia.
    El resultado es la firma espectral del "ruido limpio" del entorno;
    todo lo que se desvíe de esto hacia arriba en banda será candidato a dron.

    Recorta todos los vectores al mínimo de longitudes antes de promediar
    porque los clips pueden tener mínimas diferencias de tamaño por timing del SDR.

    Returns:
        tuple[np.ndarray, np.ndarray]: (freqs_ref, ref_psd_db).
            freqs_ref: eje de frecuencias en Hz.
            ref_psd_db: PSD promedio de referencia en dB.

    Notas:
        - Llama a sys.exit(1) si no hay ningún clip de referencia disponible.
          El sistema no puede operar sin referencia de ruido.
    """
    psds      = []
    min_len   = None
    freqs_ref = None

    for ruta in REF_FILES:
        ruta = str(ruta)
        if not os.path.exists(ruta):
            print(f"  [AVISO] Referencia no encontrada: {ruta} — se omite")
            continue
        rate, mono    = leer_wav(ruta)
        freqs, psd_db = calcular_psd(mono, rate)
        psds.append(psd_db)
        if min_len is None or len(psd_db) < min_len:
            min_len   = len(psd_db)
            freqs_ref = freqs

    if not psds:
        print("ERROR: No se encontró ningún archivo de referencia.")
        sys.exit(1)

    ref_promedio = np.mean([p[:min_len] for p in psds], axis=0)
    freqs_ref    = freqs_ref[:min_len]
    print(f"  Referencia construida con {len(psds)} archivo(s).")
    return freqs_ref, ref_promedio


# ─────────────────────────────────────────────
# VISUALIZACIÓN PNG
# ─────────────────────────────────────────────

def guardar_comparacion(nombre, freqs_ref, psd_ref_db,
                         freqs_sig, psd_sig_db,
                         delta_snr, coseno_val, resultado):
    """Genera y guarda un PNG de comparación espectral entre referencia y señal.

    El color de la curva de señal indica el resultado: naranja para DRON DETECTADO,
    azul para SIN DRON. Útil para depuración visual y para incluir en informes.

    El PNG va a COMP_DIR con el mismo nombre base que el WAV analizado.
    No se genera por defecto en el modo live (analisis_worker no lo llama);
    solo en el modo interactivo si el usuario lo solicita.

    Returns:
        str: Ruta completa al PNG guardado.
    """
    os.makedirs(COMP_DIR, exist_ok=True)
    color = "#e05c00" if resultado == "DRON DETECTADO" else "#2196F3"
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(freqs_ref / 1e3, psd_ref_db, color="#aaaaaa", linewidth=0.8,
            label="Referencia (ruido promedio)")
    ax.plot(freqs_sig / 1e3, psd_sig_db, color=color, linewidth=1.0,
            label=f"Señal: {nombre}")
    ax.axhline(potencia_media(psd_ref_db), color="#aaaaaa",
               linestyle="--", linewidth=0.6, alpha=0.6)
    ax.axhline(potencia_media(psd_sig_db), color=color,
               linestyle="--", linewidth=0.6, alpha=0.6)
    ax.set_xlabel("Frecuencia relativa (kHz)")
    ax.set_ylabel("PSD (dB)")
    ax.set_title(
        f"{nombre}  |  ΔSNR={delta_snr:+.2f} dB  |  "
        f"Coseno={coseno_val:.4f}  |  {resultado}"
    )
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    png_path = os.path.join(COMP_DIR, nombre.replace(".wav", ".png"))
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    return png_path


# ─────────────────────────────────────────────
# CSV
# ─────────────────────────────────────────────

def init_csv():
    """Crea o sobreescribe el CSV de resultados con solo la cabecera.

    Se llama una vez al arrancar el worker para empezar con un CSV limpio
    en cada sesión. Si el archivo ya existe lo trunca: el dashboard muestra
    solo los resultados de la sesión activa, no acumulados de sesiones previas.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            "archivo", "delta_snr_db", "similitud_coseno",
            "resultado", "confianza_pct", "timestamp"
        ])


def append_csv(fila):
    """Agrega una fila de resultados al CSV abierto por init_csv().

    Llamado por analisis_worker() y por analizar() tras cada clip procesado.
    No usa lock porque en el modo live solo el hilo worker escribe aquí.
    """
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(fila)


# ─────────────────────────────────────────────
# ANÁLISIS DE UN ARCHIVO
# ─────────────────────────────────────────────

def analizar(nombre, freqs_ref, ref_psd_db, guardar_png=False):
    """Analiza un archivo WAV individual y devuelve el resultado de detección.

    Función de modo batch/interactivo: lee el WAV de disco, calcula ΔSNR y coseno,
    imprime el resultado en consola y lo graba en el CSV.

    A diferencia del análisis en analisis_worker() (que opera sobre arrays en memoria),
    esta función lee desde disco. Es más lenta pero sirve para revisar clips grabados.

    Args:
        nombre (str): Nombre del archivo WAV o ruta absoluta.
        freqs_ref (np.ndarray): Eje de frecuencias de la referencia.
        ref_psd_db (np.ndarray): PSD de referencia en dB.
        guardar_png (bool): Si True genera el PNG de comparación en COMP_DIR.

    Returns:
        dict | None: Diccionario con claves archivo, delta_snr_db, coseno,
            resultado, confianza. None si el archivo no existe.
    """
    if os.path.isabs(nombre) and os.path.exists(nombre):
        ruta   = nombre
        nombre = os.path.basename(nombre)
    else:
        ruta = os.path.join(WAV_DIR, nombre)

    if not os.path.exists(ruta):
        print(f"  [ERROR] No encontrado: {ruta}")
        return None

    rate, mono      = leer_wav(ruta)
    freqs_s, psd_db = calcular_psd(mono, rate)

    delta_snr  = potencia_media(psd_db) - potencia_media(ref_psd_db)
    coseno_val = similitud_coseno(ref_psd_db, psd_db)
    resultado, confianza = decision(delta_snr, coseno_val)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    icono = "🚨" if resultado == "DRON DETECTADO" else "✅"

    print(f"\n{'─'*55}")
    print(f"  Archivo  : {nombre}")
    print(f"  Δ SNR    : {delta_snr:+.2f} dB  (umbral ≥ {UMBRAL_SNR})")
    print(f"  Coseno   : {coseno_val:.4f}      (umbral ≤ {UMBRAL_COS})")
    print(f"  {icono}  {resultado} — {confianza}%")
    print(f"{'─'*55}")

    if guardar_png:
        png = guardar_comparacion(
            nombre, freqs_ref, ref_psd_db,
            freqs_s, psd_db, delta_snr, coseno_val, resultado
        )
        print(f"  PNG guardado: {png}")

    append_csv([nombre, f"{delta_snr:.4f}", f"{coseno_val:.4f}",
                resultado, confianza, ts])

    return {"archivo": nombre, "delta_snr_db": delta_snr,
            "coseno": coseno_val, "resultado": resultado,
            "confianza": confianza}


# ─────────────────────────────────────────────
# MODO INTERACTIVO
# ─────────────────────────────────────────────

def modo_interactivo(freqs_ref, ref_psd_db):
    """Menú de consola para analizar clips WAV de forma interactiva.

    Permite analizar un archivo individual o toda la carpeta WAV_DIR.
    Diseñado para sesiones de depuración y validación de umbrales,
    no para el despliegue en campo (ese usa el bucle de captura.py).
    """
    print("\n" + "="*55)
    print("  PROTO1 v3.0 — Detector de drones (2 clases)")
    print("  Hardware : PlutoSDR | 433.5 MHz | 61.44 MSPS")
    print(f"  Umbrales : SNR ≥ {UMBRAL_SNR} dB | Coseno ≤ {UMBRAL_COS}")
    print("="*55)
    print("  Menú:")
    print("  - [nombre].wav  → analiza un archivo")
    print("  - todos         → analiza toda la carpeta WAV_DIR")
    print("  - salir")
    print("="*55)

    while True:
        entrada = input("\n> ").strip()

        if entrada.lower() == "salir":
            print(f"\n  Resultados en: {CSV_PATH}")
            break

        elif entrada.lower() == "todos":
            archivos = sorted([f for f in os.listdir(WAV_DIR) if f.endswith(".wav")])
            print(f"\n  Analizando {len(archivos)} archivos...")
            resultados  = []
            detectados  = []
            for archivo in archivos:
                r = analizar(archivo, freqs_ref, ref_psd_db, guardar_png=False)
                if r:
                    resultados.append(r)
                    if r["resultado"] == "DRON DETECTADO":
                        detectados.append(r)

            print(f"\n{'='*55}")
            print(f"  RESUMEN — {len(resultados)} archivos analizados")
            print(f"  ✅ Sin dron      : {len(resultados) - len(detectados)}")
            print(f"  🚨 Dron detectado: {len(detectados)}")
            print(f"  CSV guardado     : {CSV_PATH}")
            print(f"{'='*55}")

        else:
            if not entrada.endswith(".wav"):
                entrada += ".wav"
            png_resp = input("  ¿Generar PNG? (s/n): ").strip().lower()
            analizar(entrada, freqs_ref, ref_psd_db, guardar_png=(png_resp == "s"))


# ─────────────────────────────────────────────
# ENTRADA PRINCIPAL
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*55)
    print("  PROTO1 v3.0 — Detector RF de drones")
    print("  Clases: sin_dron / dron_detectado")
    print("="*55)

    archivo_arg = sys.argv[1] if len(sys.argv) > 1 else None

    print("\n[1/2] Construyendo referencia de ruido...")
    freqs_ref, ref_psd_db = construir_referencia()

    print("\n[2/2] Inicializando CSV...")
    init_csv()
    print(f"  CSV listo: {CSV_PATH}")

    if archivo_arg:
        analizar(archivo_arg, freqs_ref, ref_psd_db, guardar_png=False)
    else:
        modo_interactivo(freqs_ref, ref_psd_db)
