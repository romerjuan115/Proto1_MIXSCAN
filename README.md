MIXSCAN — Prototipo de software para la detección de RPAS

Prototipo de detección de (RPAS) en la frecuencia 433 MHz mediante análisis espectral.
Combina captura SDR (Capa 1) con verificación del protocolo MAVLink vía
enlace serie (Capa 2).

**Resultados de validación:** 60 % de detección a 3 m, 70 % a 6 m, 
---

## Hardware requerido

| Componente | Modelo | Función |
|---|---|---|
| SDR | ADALM-PLUTO (PlutoSDR) | Captura IQ en 433 MHz |
| Radio telemetría (aire) | SiK Radio 433 MHz | Montado en el dron |
| Radio telemetría (tierra) | SiK Radio 433 MHz | Conectado al PC por USB |
| PC | Windows 10/11, puerto USB 3.0 | Ejecución del sistema |

**Conexiones:**
- PlutoSDR → PC por USB (IP fija `192.168.2.1`)
- SiK Radio ground → PC por USB (aparece como `COMx` en Administrador de Dispositivos → Puertos)

---

## Instalación

### Requisitos previos
- Python 3.11
- Drivers PlutoSDR: libiio + plutosdr-m2k-drivers (instrucciones en la wiki oficial de Analog Devices)
- Drivers SiK Radio: Silicon Labs (Windows los instala automáticamente en la mayoría de casos)

### Pasos

```powershell
# 1. Crear entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Instalar dependencias
pip install -r requirements.txt
```

---

## Configuración

Todos los parámetros están centralizados en `common/config.py`. Los únicos valores que
normalmente requieren ajuste al cambiar de equipo son:

| Parámetro | Valor por defecto | Cuándo ajustarlo |
|---|---|---|
| `SERIAL_PORT` | `"COM8"` | Si el SiK aparece en un COM distinto en tu equipo |
| `GANANCIA_DB` | `30` | Usar 30 a distancias ≤ 3 m; usar 40 a 6 m o más |

> No modificar `UMBRAL_DELTA_SNR`, `VENTANA_VOTOS` ni `VOTOS_MINIMOS` sin repetir la
> validación de campo. Fueron ajustados empíricamente en las sesiones S2 y S3.

---

## Uso

### Paso 0 — Verificar conexión al PlutoSDR (opcional)

```powershell
python test.py
```

### Paso 1 — Capturar referencia de ruido (dron apagado)

```powershell
python captura_ruido.py
```

Captura 10 clips de 1 segundo en `capturas/ruido/`. **Este paso es obligatorio** antes
de la primera sesión de detección y debe repetirse si se cambia la ganancia o el entorno
de RF. Los clips no están en el repositorio por su tamaño (~470 MB c/u).

### Paso 2 — Iniciar detección (dron encendido)

```powershell
# Solo Capa 1 — detección SDR sin verificación MAVLink
python captura.py

# Capa 1 + Capa 2 — detección + verificación MAVLink por puerto serie
python main.py
```

### Paso 3 — Dashboard web (proceso separado, opcional)

```powershell
python server.py
# Abrir en el navegador: http://127.0.0.1:8080
```

---

## Arquitectura

```
main.py  (orquestador de hilos)
    │
    ├── [hilo Capa 1]  PlutoSDR ──> captura IQ ──> ΔSNR + votación ──> resultados.csv
    │
    └── [hilo Capa 2]  SiK serial ──> MAVLink HEARTBEAT ──> logs/detecciones.csv
                                                                    │
                        server.py  ←── vigila ambos CSV ──> correlación ──> WebSocket ──> gui/
```

**Lógica de Capa 1:**
1. El PlutoSDR captura 1 segundo de IQ a 433.5 MHz / 61.44 Msps
2. Se calcula la PSD (método de Welch) y la potencia integrada en la banda 0–3 MHz
3. Se compara contra la referencia de ruido (ΔSNR)
4. Si ΔSNR ≥ 2.0 dB en ≥ 2 de los últimos 5 clips → alerta Capa 1

**Verificación en Capa 2:**
- Al activarse la alerta de Capa 1, el sistema lee el puerto serie del SiK Radio ground
- Busca tramas MAVLink v1/v2 (HEARTBEAT, msg_id = 0)
- Si encuentra HEARTBEAT → confirma dron activo y lo registra en `logs/detecciones.csv`

---

## Resultados de validación

| Sesión | Distancia | Detección Capa 1 | 
|---|---|---|---|
| S2 | 3 m | 60 % |
| S3 | 6 m | 70 % | 

La tasa de detección inferior al 100 % se explica por el ciclo TDMA half-duplex del
enlace SiK (~50 %), que genera gaps de 2-3 clips consecutivos sin señal. La ventana de
votación (`VENTANA_VOTOS = 5`, `VOTOS_MINIMOS = 2`) mitiga este efecto.

---

## Estructura del repositorio

```
.
├── common/config.py          # Fuente única de parametros — leer primero
├── captura.py                # Productor IQ + worker de analisis (Capa 1)
├── captura_ruido.py          # Calibracion one-shot (referencia de ruido)
├── detector.py               # ΔSNR, PSD, votacion, modo batch/interactivo
├── main.py                   # Orquestador Capa 1 + Capa 2
├── server.py                 # Dashboard FastAPI + WebSocket (puerto 8080)
├── test.py                   # Utilidades de diagnostico
├── capa2_mavlink/
│   ├── capa2_serial.py       # Lectura MAVLink por puerto serie
│   ├── mavlink_parser.py     # Parser MAVLink v1/v2 
│   ├── logger.py             # Escritura CSV 
├── gui/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── capturas/ruido/           # Clips de referencia — generados con captura_ruido.py, NO en git
└── logs/detecciones.csv      # Registro de confirmaciones Capa 2
```
---

## Notas para reproducibilidad

- Los clips de referencia de ruido (`capturas/ruido/*.wav`) no estan en el repositorio
  por su tamano (~470 MB por clip). Generarlos con `python captura_ruido.py` antes de
  la primera sesion de deteccion, con el dron apagado. La carpeta `capturas/ruido/` se
  crea automaticamente al ejecutar ese script.
- `captura_ruido.py` tiene la ruta de salida fija a `D:\Proto1\capturas\ruido` (linea 24).
  Si clonas el repositorio en otra ubicacion, cambia esa linea a la ruta correspondiente
  antes de ejecutar el script.
- El numero de puerto COM del SiK Radio ground varia entre equipos. Ajustar `SERIAL_PORT`
  en `common/config.py` si el sistema lanza `PermissionError` al iniciar.
- `winsound` (alertas acusticas) es parte de la biblioteca estandar de Python en Windows;
  no requiere instalacion adicional.
- Si QGroundControl u otro software GCS esta abierto y usa el mismo puerto COM, Capa 2
  lanzara `PermissionError`. Cerrar el GCS antes de ejecutar `main.py`.
