import re
import csv 
from pathlib import Path

import regex

def extraer_comida_de_logs(log_path: str, output_csv: str ):
    print (f"Analizando la mente de lazzaro en : {log_path}...")

    #expresion para atrapar lo que dice el usuario (inputs)
    # atrapalos a todos yei " nueva tarea inicializada ' texto' " o "tarea reiniciada con nuevo objetivo:'testo'"
    regex_texto = re.compile(r"(?:nueva tarea inicializada | tarea reiniciada con nuevo objetivo): \s*'(.+?)'")

    #expresion para atrapar la desicion que tomo el sistema (el label ) 
    #atrapa : "intent = social route = perosna"
    #duermanme son las 4 am xdxdxd aaaaa
    regex_triage = re.compile(r"Triage:\s*intent=(\w+)\s+route=(\w+)")
    #no me acuerdo si eran con las llaves no cuadradas
    # con cual era las listas aaaaa
    dataset = []
    texto_pendiente = None

    try:                                               #tal real esto de errores asjasjas 
        with open (log_path, 'r', encoding= 'utf-8',errors='ignore') as archivo_log:
            for linea in archivo_log:
                #la chica escuccha algo nuevo?
                match_texto = regex_texto.search(linea)
                if match_texto:
                    texto_pendiente = match_texto.group(1).strip()
                    continue

                #2. el traige si sirvio como el tonoto lo diseño :3?
                match_triage = regex_triage.search(linea)
                if match_triage and texto_pendiente:
                    intent = match_triage.group(1)
                    route = match_triage.group(2)

                    dataset.append({
                        "texto_usuario" : texto_pendiente,
                        "intencion" : intent,
                        "ruta" : route ,
                    })
                    #limpiamos toda la pagina sin dejar cometer una sola mancha de rotura hasta que :
                    """
                            /\_/\ 
                            ( o.o )
                            > ^ <
                            /  _  \
                            /__|__\
                              " "
                              #me uedo chueco el gato xdxdd
                    """
                    texto_pendiente = None

    except FileNotFoundError:
        print (f"Error: Nose encontro el archivo {log_path}")
        return
    
    #guardar todo el csv aaa odio hacer estos scripts de guardado son confusos aaaaaa
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
        print("No se encontraron pares de texto/triage en el log.")

if __name__ == "__main__":
    # Ajusta estas rutas si tus logs están en otra carpeta
    RUTA_LOGS = "lazzaro.log"
    RUTA_OUTPUT = "dataset_triage_crudo.csv"
    
    extraer_comida_de_logs(RUTA_LOGS, RUTA_OUTPUT)