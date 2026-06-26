import re
import csv 
from pathlib import Path

def extraer_comida_de_logs(log_path: str, output_csv: str):
    print(f"Analizando la mente de Lazzaro en: {log_path}...")

    # CORRECCIÓN AQUÍ: 
    # 1. Sin espacios alrededor del '|'
    # 2. Es "Nueva tarea iniciada", no "inicializada"
    # 3. Le agregamos re.IGNORECASE para que las mayúsculas no importen si cambian a futuro.
    regex_texto = re.compile(r"(?:Nueva tarea iniciada|Tarea reiniciada con nuevo objetivo):\s*'(.+?)'", re.IGNORECASE)

    # Expresión para atrapar la decisión que tomó el sistema (el label)
    # duermanme son las 4 am xdxdxd aaaaa
    regex_triage = re.compile(r"Triage:\s*intent=(\w+)\s+route=(\w+)")
    
    dataset = []
    texto_pendiente = None

    try:                                               
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as archivo_log:
            for linea in archivo_log:
                # ¿La chica escucha algo nuevo?
                match_texto = regex_texto.search(linea)
                if match_texto:
                    texto_pendiente = match_texto.group(1).strip()
                    continue

                # 2. ¿El triage sí sirvió como el tonoto lo diseño :3?
                match_triage = regex_triage.search(linea)
                if match_triage and texto_pendiente:
                    intent = match_triage.group(1)
                    route = match_triage.group(2)

                    dataset.append({
                        "texto_usuario": texto_pendiente,
                        "intencion": intent,
                        "ruta": route,
                    })
                    # limpiamos toda la pagina sin dejar cometer una sola mancha de rotura hasta que :
                    """
                            /\_/\ 
                            ( o.o )
                            > ^ <
                            /  _  \
                            /__|__\
                              " "
                              # el gato chueco es canon ahora
                    """
                    texto_pendiente = None

    except FileNotFoundError:
        print(f"Error: No se encontró el archivo {log_path}")
        return
    
    # guardar todo el csv
    if dataset:
        with open(output_csv, 'w', encoding='utf-8', newline='') as f:
            # Definimos las columnas
            columnas = ["texto_usuario", "intencion", "ruta"]
            writer = csv.DictWriter(f, fieldnames=columnas)
            
            writer.writeheader()
            writer.writerows(dataset)
        print(f"¡Éxito! Se extrajeron {len(dataset)} interacciones.")
        print(f"Dataset guardado en: {output_csv}")
    else:
        print("No se encontraron pares de texto/triage en el log. Revisa el Regex.")

if __name__ == "__main__":
    RUTA_LOGS = "lazzaro.log"
    RUTA_OUTPUT = "dataset_triage_crudo.csv"
    
    extraer_comida_de_logs(RUTA_LOGS, RUTA_OUTPUT)