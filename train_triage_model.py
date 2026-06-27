import pandas as pd
from sklearn import pipeline
from sklearn.feature_extraction.text import TfidfTransformer, TfidfVectorizer
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
import joblib
import os

def entrenar_capa_0():
    print("Iniciando entrenamiento del bosque 'chingaderitas al azar ' :3 ")
    
    #1. cargar la "comida" (el dataset poes)
    df = pd.read_csv("dataset_triage_etiquetado.csv") # luego cambio esto con path para una carpeta y mas orden porque hay un reverendo caos en el trabajo :l

    X = df['texto_usuario']
    y_intent = df ['intencion']
    y_route = df ['ruta']

    print(f"Dataset cargado: {len(df)} ejemplos.")

    #2. Pipilinais para intencion ( OMG chingaderita faker referencia :O)
    #TF-IDF convierte el texto a numeros. ( ngram_range=(1,2) significa que 
    #analiza palabras sueltas como "camina" y pares como "camina hacia").
    pipeline_intent = Pipeline([
        ('tfidf', TfidfVectorizer(ngram_range=(1, 2))),
        ('rf', RandomForestClassifier(n_estimators=100, random_state=42))# aca se modifica la cantidad nodos , si se les decian nodos? 
    ])

    print ("entrenando clasificador de intencion")
    pipeline_intent.fit(X,y_intent)

    #3Pipeline para rute o ruta no se como lo guarde mas arriba
    pipeline_route = Pipeline([
        ('tfidf', TfidfVectorizer(ngram_range=(1, 2))),
        ('rf', RandomForestClassifier(n_estimators=100, random_state=42))
    ])

    print("entrenando clasificador de Ruta...................")
    pipeline_route.fit(X,y_route)

    #4. guardar los cerebros compilados en tu carpeta de datos 
    os.makedirs("lazzaro_data", exist_ok=True)
    
    path_intent = "lazzaro_data/triage_model_intent.joblib"
    path_route = "lazzaro_data/triage_model_route.joblib"
    
    joblib.dump(pipeline_intent, path_intent)
    joblib.dump(pipeline_route, path_route)
    
    print("\n todo salio bien yipieee :3 ")
    print(f"Cerebros guardados en: {path_intent} y {path_route}")

if __name__ == "__main__":
    entrenar_capa_0()