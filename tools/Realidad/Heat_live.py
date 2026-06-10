#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Live heatmap from ESP32 serial data.

Expected serial lines:
  DATA,x,y,value
  DATA,x,value
"""

import argparse
import csv
import threading
import time
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
import serial
import serial.tools.list_ports


DEFAULT_BAUDRATE = 115200
DEFAULT_MAX_POINTS = 5000
DEFAULT_REFRESH_INTERVAL = 0.1
TIMEOUT_SERIAL = 0.2
READ_BURST_LIMIT = 100


def detectar_puerto():
    """Detecta automaticamente un puerto serial probable para ESP32."""
    puertos = serial.tools.list_ports.comports()
    for p in puertos:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if any(
            x in desc or x in hwid
            for x in (
                "cp210",
                "ch340",
                "ch341",
                "ftdi",
                "esp32",
                "usb serial",
                "uart",
                "silicon labs",
            )
        ):
            return p.device
    if puertos:
        return puertos[0].device
    return None


def parse_line(line):
    """
    Parsea una linea del ESP32.

    Retorna (x, y, valor) o None si no es una linea DATA valida.
    """
    line = line.strip()
    if not line:
        return None

    parts = [p.strip() for p in line.split(",")]
    if not parts or parts[0].upper() != "DATA":
        return None

    nums = []
    for p in parts[1:]:
        try:
            nums.append(float(p))
        except ValueError:
            return None

    if len(nums) == 2:
        x, val = nums
        return (x, 0.0, val)
    if len(nums) == 3:
        x, y, val = nums
        return (x, y, val)
    return None


def _set_limits(ax, xs, ys):
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad_x = max(1.0, (max_x - min_x) * 0.1)
    pad_y = max(1.0, (max_y - min_y) * 0.1)
    ax.set_xlim(min_x - pad_x, max_x + pad_x)
    ax.set_ylim(min_y - pad_y, max_y + pad_y)


def _update_plot(ax, scatter, points):
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    vals = np.array([p[2] for p in points], dtype=float)

    if scatter is None:
        scatter = ax.scatter(
            xs,
            ys,
            c=vals,
            cmap="hot",
            s=70,
            edgecolors="black",
            linewidth=0.5,
        )
        plt.colorbar(scatter, ax=ax, label="Valor medido")
    else:
        scatter.set_offsets(np.column_stack((xs, ys)))
        scatter.set_array(vals)

    min_val = float(vals.min())
    max_val = float(vals.max())
    if min_val == max_val:
        min_val -= 1.0
        max_val += 1.0
    scatter.set_clim(min_val, max_val)
    _set_limits(ax, xs, ys)
    return scatter


def _send_serial_command(ser, command):
    ser.write((command.strip() + "\n").encode("utf-8"))
    ser.flush()


def _start_command_thread(ser, stop_event):
    def worker():
        print("Comandos disponibles en esta terminal: X 2, Y 3, M, A, S, H, Q")
        print("Usa Q para detener el visor.")
        while not stop_event.is_set():
            try:
                command = input()
            except (EOFError, OSError):
                break

            command = command.strip()
            if not command:
                continue

            if command.upper() == "Q":
                stop_event.set()
                break

            try:
                _send_serial_command(ser, command)
            except serial.SerialException as e:
                print(f"Error al enviar comando serial: {e}")
                stop_event.set()
                break

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def live_heatmap(
    port=None,
    baudrate=DEFAULT_BAUDRATE,
    max_points=DEFAULT_MAX_POINTS,
    output_csv=None,
    refresh_interval=DEFAULT_REFRESH_INTERVAL,
    enable_input=True,
):
    """Conecta al puerto serie, lee DATA y actualiza el mapa en vivo."""
    if port is None:
        port = detectar_puerto()
        if port is None:
            print("No se pudo detectar el puerto del ESP32.")
            print("Puertos disponibles:")
            for p in serial.tools.list_ports.comports():
                print(f"  {p.device} -> {p.description}")
            print("Especifica el puerto manualmente con --port")
            return

    print(f"Usando puerto: {port} a {baudrate} baudios")

    try:
        ser = serial.Serial(port, baudrate, timeout=TIMEOUT_SERIAL)
        time.sleep(1.5)
        ser.reset_input_buffer()
        print("Conectado. Esperando datos DATA...")
    except serial.SerialException as e:
        print(f"Error al abrir el puerto {port}: {e}")
        return

    points = deque(maxlen=max_points)
    csv_file = None
    csv_writer = None

    if output_csv:
        csv_file = open(output_csv, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["x", "y", "value"])
        print(f"Guardando datos tambien en {output_csv}")

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 8))
    scatter = None
    last_draw = 0.0

    ax.set_xlabel("Coordenada X")
    ax.set_ylabel("Coordenada Y")
    ax.set_title("Mapa de calor en tiempo real desde ESP32")
    ax.grid(True, linestyle="--", alpha=0.5)

    print("Presiona Ctrl+C en la terminal o cierra la ventana para detener.")
    stop_event = threading.Event()
    if enable_input:
        _start_command_thread(ser, stop_event)

    try:
        while plt.fignum_exists(fig.number) and not stop_event.is_set():
            got_data = False

            for _ in range(READ_BURST_LIMIT):
                raw = ser.readline()
                if not raw:
                    break

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                parsed = parse_line(line)
                if parsed is None:
                    print(f"(ignorado) {line}")
                    continue

                x, y, val = parsed
                points.append((x, y, val))
                got_data = True

                if csv_writer is not None:
                    csv_writer.writerow([x, y, val])
                    csv_file.flush()

                print(f"Punto: ({x:g}, {y:g}) = {val:g} | Total: {len(points)}")

            now = time.monotonic()
            if points and (got_data or now - last_draw >= refresh_interval):
                scatter = _update_plot(ax, scatter, points)
                fig.canvas.draw_idle()
                last_draw = now

            plt.pause(refresh_interval)

    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
    finally:
        ser.close()
        if csv_file:
            csv_file.close()
        plt.ioff()
        if plt.fignum_exists(fig.number):
            plt.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mapa de calor en vivo desde ESP32 (formato DATA,x,y,valor)"
    )
    parser.add_argument(
        "--port",
        "-p",
        type=str,
        default=None,
        help="Puerto serie (ej: COM3, /dev/ttyUSB0). Si no se da, se autodetecta.",
    )
    parser.add_argument(
        "--baudrate",
        "-b",
        type=int,
        default=DEFAULT_BAUDRATE,
        help=f"Baudrate (por defecto {DEFAULT_BAUDRATE})",
    )
    parser.add_argument(
        "--maxpoints",
        "-m",
        type=int,
        default=DEFAULT_MAX_POINTS,
        help=f"Maximo numero de puntos a mostrar (por defecto {DEFAULT_MAX_POINTS})",
    )
    parser.add_argument(
        "--csv",
        "-c",
        type=str,
        default=None,
        help="Guardar todos los puntos en un archivo CSV (opcional)",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=DEFAULT_REFRESH_INTERVAL,
        help=f"Segundos entre refrescos del grafico (por defecto {DEFAULT_REFRESH_INTERVAL})",
    )
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="No leer comandos desde la terminal; solo escuchar el ESP32.",
    )
    args = parser.parse_args()

    live_heatmap(
        port=args.port,
        baudrate=args.baudrate,
        max_points=args.maxpoints,
        output_csv=args.csv,
        refresh_interval=args.refresh,
        enable_input=not args.no_input,
    )
