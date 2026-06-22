# SKYSHIELD — Guia de uso del dashboard

---

## Como iniciar el sistema completo

### Terminal 1 — Servidor del dashboard

```powershell
python server.py
```

Abre el dashboard en el navegador: **http://127.0.0.1:8080**

---

### Terminal 2 — Deteccion (elegir una opcion)

```powershell
# Solo Capa 1 (deteccion SDR sin verificacion MAVLink)
python captura.py

# Capa 1 + Capa 2 (deteccion + verificacion MAVLink por puerto serie)
python main.py
```

El dashboard se actualiza automaticamente via WebSocket al recibir nuevas detecciones.

---

## Que muestra el dashboard

- **Banner de alerta** — cambia entre DRON DETECTADO y ESPACIO AEREO LIMPIO
- **Radar** — punto rojo al detectar actividad en 433 MHz
- **Graficas** — ΔSNR y similitud coseno de los ultimos clips analizados
- **Tabla de logs** — historial de detecciones con timestamp, resultado y confianza
- **Correlacion Capa 1 + Capa 2** — alerta de alta confianza cuando ambas capas coinciden en una ventana de 10 s

---

## Modo simulacion (sin hardware)

Para probar el dashboard sin PlutoSDR ni SiK Radio:

```powershell
python simular_detecciones.py
```

Genera detecciones sinteticas en `resultados.csv` y `logs/detecciones.csv`.
