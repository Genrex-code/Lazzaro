from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline


BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "dataset_triage_etiquetado.csv"
MODEL_DIR = BASE_DIR / "lazzaro_data"
INTENT_MODEL_PATH = MODEL_DIR / "triage_model_intent.joblib"
ROUTE_MODEL_PATH = MODEL_DIR / "triage_model_route.joblib"
REQUIRED_COLUMNS = {"texto_usuario", "intencion", "ruta"}


def _cargar_dataset(dataset_path: Path) -> pd.DataFrame:
    if not dataset_path.exists():
        raise FileNotFoundError(f"No existe el dataset de triage: {dataset_path}")

    df = pd.read_csv(dataset_path)
    missing = REQUIRED_COLUMNS.difference(df.columns)
    if missing:
        columnas = ", ".join(sorted(missing))
        raise ValueError(f"Dataset incompleto. Faltan columnas: {columnas}")

    df = df.dropna(subset=list(REQUIRED_COLUMNS)).copy()
    for column in REQUIRED_COLUMNS:
        df[column] = df[column].astype(str).str.strip()

    df = df[
        (df["texto_usuario"] != "")
        & (df["intencion"] != "")
        & (df["ruta"] != "")
    ]
    if df.empty:
        raise ValueError("El dataset de triage no contiene ejemplos entrenables.")

    return df


def _crear_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2))),
            (
                "rf",
                RandomForestClassifier(
                    n_estimators=150,
                    random_state=42,
                    class_weight="balanced",
                ),
            ),
        ]
    )


def entrenar_capa_0(
    dataset_path: str | Path = DATASET_PATH,
    model_dir: str | Path = MODEL_DIR,
) -> None:
    dataset_path = Path(dataset_path).resolve()
    model_dir = Path(model_dir).resolve()

    print("Iniciando entrenamiento de Capa 0 con Random Forest.")
    df = _cargar_dataset(dataset_path)
    print(f"Dataset cargado: {len(df)} ejemplos desde {dataset_path}.")

    pipeline_intent = _crear_pipeline()
    print("Entrenando clasificador de intencion...")
    pipeline_intent.fit(df["texto_usuario"], df["intencion"])

    pipeline_route = _crear_pipeline()
    print("Entrenando clasificador de ruta...")
    pipeline_route.fit(df["texto_usuario"], df["ruta"])

    model_dir.mkdir(parents=True, exist_ok=True)
    intent_model_path = model_dir / "triage_model_intent.joblib"
    route_model_path = model_dir / "triage_model_route.joblib"

    joblib.dump(pipeline_intent, intent_model_path)
    joblib.dump(pipeline_route, route_model_path)

    print("Entrenamiento completado.")
    print(f"Modelo de intencion: {intent_model_path}")
    print(f"Modelo de ruta: {route_model_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Entrena los modelos ML de Capa 0.")
    parser.add_argument(
        "--dataset",
        default=str(DATASET_PATH),
        help="CSV etiquetado con columnas texto_usuario,intencion,ruta.",
    )
    parser.add_argument(
        "--model-dir",
        default=str(MODEL_DIR),
        help="Directorio donde se guardaran los .joblib.",
    )
    args = parser.parse_args()

    entrenar_capa_0(args.dataset, args.model_dir)
