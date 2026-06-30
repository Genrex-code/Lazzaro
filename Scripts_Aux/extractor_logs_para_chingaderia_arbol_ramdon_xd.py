import argparse
import csv
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_CANDIDATES = (
    BASE_DIR / "logs antiguos" / "lazzaro_3.log", # acuerdate de cambiar aca el numero para que registres los correctos por el momento queda en 0 
    BASE_DIR / "lazzaro.log",
)
DEFAULT_OUTPUT = BASE_DIR / "dataset_triage_crudo.csv"

REGEX_TEXTO = re.compile(
    r"(?:Nueva tarea iniciada|Tarea reiniciada con nuevo objetivo):\s*'(.+?)'",
    re.IGNORECASE,
)
REGEX_TRIAGE = re.compile(r"Triage:\s*intent=(\w+)\s+route=(\w+)")


def _resolver_path(path_value: str | Path | None, default: Path | None = None) -> Path:
    if path_value:
        path = Path(path_value)
        return path if path.is_absolute() else BASE_DIR / path
    if default is not None:
        return default
    for candidate in DEFAULT_LOG_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_LOG_CANDIDATES[0]


def extraer_comida_de_logs(
    log_path: str | Path | None = None,
    output_csv: str | Path = DEFAULT_OUTPUT,
) -> int:
    log_path = _resolver_path(log_path)
    output_csv = _resolver_path(output_csv, DEFAULT_OUTPUT)

    print(f"Analizando logs de Lazzaro en: {log_path}")

    dataset = []
    texto_pendiente = None

    try:
        with log_path.open("r", encoding="utf-8", errors="ignore") as archivo_log:
            for linea in archivo_log:
                match_texto = REGEX_TEXTO.search(linea)
                if match_texto:
                    texto_pendiente = match_texto.group(1).strip()
                    continue

                match_triage = REGEX_TRIAGE.search(linea)
                if match_triage and texto_pendiente:
                    dataset.append(
                        {
                            "texto_usuario": texto_pendiente,
                            "intencion": match_triage.group(1),
                            "ruta": match_triage.group(2),
                        }
                    )
                    texto_pendiente = None
    except FileNotFoundError:
        print(f"Error: no se encontro el archivo {log_path}")
        return 0

    if not dataset:
        print("No se encontraron pares de texto/triage en el log.")
        return 0

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        columnas = ["texto_usuario", "intencion", "ruta"]
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(dataset)

    print(f"Se extrajeron {len(dataset)} interacciones.")
    print(f"Dataset guardado en: {output_csv}")
    return len(dataset)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convierte logs de Lazzaro en CSV para entrenar Capa 0."
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Ruta del log a leer. Default: logs/lazzaro.log y fallback lazzaro.log.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="CSV de salida.",
    )
    args = parser.parse_args()

    extraer_comida_de_logs(args.log, args.output)
