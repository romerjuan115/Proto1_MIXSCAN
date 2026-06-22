"""
common/config.py — Parámetros centralizados del sistema MIXSCAN

Fuente única de verdad para todos los valores de configuración del sistema.
Cualquier módulo los importa desde aquí; ninguno los redefine localmente.
Si este archivo falla o los valores se corrompen, el pipeline completo
(Capa 1 SDR + Capa 2 MAVLink) pierde sus umbrales de calibración.

Importar con:
    from common.config import FRECUENCIA_HZ, SAMPLE_RATE, ...

REGLA: No modificar los valores de Capa 1 sin revalidar el pipeline completo
       de captura (captura.py → detector.py → votación deslizante).
"""


# =============================================================================
# CAPA 1 – Captura SDR (parámetros validados en sesiones S2 y S3)
# =============================================================================

# Canal por defecto de los módulos SiK 433 MHz en su configuración de fábrica.
# El PlutoSDR centra su ventana de 56 MHz aquí; el SiK cae dentro de esa ventana.
FRECUENCIA_HZ: int = 433_500_000          # 433.5 MHz

# Tasa de muestreo máxima estable del PlutoSDR ADALM en este equipo.
# Bajarlo produce aliasing en la banda de interés.
# Subirlo produce drops de buffer: el USB no da abasto con el throughput IQ.
SAMPLE_RATE: int = 61_440_000             # 61.44 Msps

# Buffer igual a SAMPLE_RATE → captura exactamente 1 segundo de IQ por llamada.
# El análisis opera sobre los primeros 1 048 576 muestras (~17 ms), no el segundo completo.
# El resto actúa como padding contra variaciones de timing del SDR.
BUFFER_SIZE: int = 61_440_000             # muestras IQ (≈ 1 s de captura)

# Ganancia LNA/IF del PlutoSDR durante captura raw.
# 30 dB es estable a distancias menores de 3 metros sin saturar el ADC.
# A 6 metros se validó con 40 dB (ver GANANCIA_DEMOD_DB).
# OJO: subir esto sin recalibrar la referencia de ruido desplaza el piso
#      y puede generar falsos positivos aunque no haya señal activa.
GANANCIA_DB: int = 30                     # dB — validado a ≤ 3 m

# CRÍTICO: mantener en False.
# La normalización altera la envolvente de amplitud que usa el detector de
# pulsos FSK; activarla destruye la métrica delta-SNR y produce falsos positivos.
# Lo intentamos brevemente en S1 — todos los clips de ruido pasaron el umbral.
NORMALIZAR_CAPTURA: bool = False          # NO cambiar

# Umbral principal de detección por diferencia de potencia en banda.
# Bajado de 3.0 a 2.0 dB tras las sesiones S2 y S3 a 6 metros:
# con 3.0 dB se perdían clips reales con ΔSNR de 2.5–3.0 dB.
# El margen sobre el piso de ruido medido (±0.5 dB) queda en 4×:
# suficiente para mantener la tasa de falsos positivos en cero en condiciones de lab.
UMBRAL_DELTA_SNR: float = 2.0            # dB — era 3.0

# Umbral secundario para el modo combinado (ΔSNR + coseno simultáneos).
# Se usa en la rama experimental del detector; Capa 1 en producción usa solo
# UMBRAL_DELTA_SNR. Más bajo porque el coseno ya filtra firmas ajenas al ruido.
UMBRAL_DELTA_SNR_COMBINADO: float = 1.5  # dB — modo experimental, no activo en producción

# Similitud coseno mínima para el modo combinado.
# Un coseno ≤ 0.35 indica que la firma espectral del clip diverge del ruido
# de referencia lo suficiente para considerarla candidata a señal activa.
# No usar como disparador único: en ruido real con baja intensidad de señal
# el coseno fluctúa bajo 0.35 sin que haya dron (demasiados falsos negativos).
UMBRAL_COSENO_COMBINADO: float = 0.35    # similitud coseno — modo experimental

# Ventana deslizante de clips para la votación mayoritaria.
# Ampliada de 3 a 5 tras observar gaps TDMA de 2–3 clips consecutivos sin señal
# en pruebas reales (clips 0010–0011–0012 del log de sesión S3, gap de ~3 s).
# Con ventana de 3 esos gaps interrumpían detecciones reales.
VENTANA_VOTOS: int = 5   # era 3

# Mayoría simple dentro de la ventana de 5.
# No subir a 3: con TDMA al 50% la probabilidad de acertar 3 clips seguidos
# cae demasiado y se pierden detecciones legítimas con señal intermitente.
VOTOS_MINIMOS: int = 2


# =============================================================================
# CAPA 2 – Demodulación OTA (Over-The-Air, rama experimental)
# =============================================================================

# Igual que FRECUENCIA_HZ; separado para permitir ajustes independientes
# si en el futuro la Capa 2 OTA opera en un canal distinto al de captura.
FRECUENCIA_SIK_HZ: int = 433_500_000     # 433.5 MHz

# 1 Msps cubre la desviación FSK de ±20 kHz con margen (Nyquist a ±500 kHz).
# Reduce la carga de CPU frente a los 61.44 Msps de captura raw:
# 61× menos datos por segundo, sin perder información de la portadora SiK.
SAMPLE_RATE_DEMOD: int = 1_000_000       # 1 Msps

# Ganancia mayor que en Capa 1 porque el demodulador opera en banda estrecha
# y no corre riesgo de saturar con el ruido de banda ancha.
# Validado a 6 metros en sesión S3 sin saturación visible en el espectrograma.
GANANCIA_DEMOD_DB: int = 40              # dB — validado a 6 m

# Estándar del firmware SiK Radio, equivale al parámetro ATS1 del módulo.
# Cambiar esto sin reconfigurar el módulo con AT (ATS1=57600 + AT&W + ATZ)
# produce que el demodulador opere fuera de sincronismo de símbolo.
BAUD_RATE_SIK: int = 57_600              # baudios

# Desviación de frecuencia pico declarada por el fabricante SiK.
# El demodulador NBFM usa este valor para calcular el índice de modulación.
# No modificar sin medir la desviación real con un analizador de espectro.
DESVIACION_FSK_HZ: int = 20_000          # ±20 kHz


# =============================================================================
# INTERFAZ SERIAL (Capa 2 en producción — reemplaza la recepción OTA)
# =============================================================================

# Puerto asignado en Windows 11 al módulo SiK ground (chip Silicon Labs CP210x).
# COM8 es el número que asignó este equipo; puede cambiar entre sesiones o equipos.
# Si el sistema lanza PermissionError al iniciar Capa 2, verificar en
# Administrador de Dispositivos → Puertos (COM y LPT) antes de cambiar el valor.
SERIAL_PORT: str = "COM8"

# Estándar del firmware SiK Radio (parámetro ATS1 del módulo).
# El módulo expone la UART a esta velocidad independientemente del baud RF.
# Cambiar este valor sin reconfigurar el hardware con AT (ATS1=57600, AT&W, ATZ)
# produce tramas corruptas en capa2_serial.py.
BAUD_RATE_SERIAL: int = 57_600           # baudios
