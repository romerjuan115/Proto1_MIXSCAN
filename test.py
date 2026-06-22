# test_conexion.py  →  D:\Proto1\
import adi

print("Intentando conectar al PlutoSDR...")
try:
    sdr = adi.Pluto(uri="ip:192.168.2.1")
    print("Conectado:", sdr._ctrl.name)
    print("Sample rate:", sdr.sample_rate)
    print("Frecuencia LO:", sdr.rx_lo)
    print("Ganancia:", sdr.gain_control_mode_chan0)
except Exception as e:
    print("Error de conexión:", e)
    print("Prueba con uri='usb:' si falla la IP")