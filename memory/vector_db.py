import os
import chromadb
from chromadb.config import Settings
import logging

logger = logging.getLogger(__name__)

class LazzaroVectorDB:
    def __init__(self, db_path="./lazzaro_data"):
        """Inicializa la base de datos local y crea la colección de recuerdos."""
        logger.info(f"Conectando a ChromaDB en {db_path}...")
        
        # Crea una base de datos local persistente en la carpeta especificada
        self.client = chromadb.PersistentClient(path=db_path)
        
        # Una 'collection' es el equivalente a una tabla en SQL
        self.collection = self.client.get_or_create_collection(
            name="experiencias_lazzaro",
            metadata={"hnsw:space": "cosine"} # Métrica para medir qué tan similares son dos recuerdos
        )
        logger.info("Base de datos vectorial lista y colección cargada.")

    def agregar_recuerdo(self, id_recuerdo: str, texto_experiencia: str, metadatos: dict):
        """Guarda un nuevo recuerdo en la base de datos."""
        self.collection.add(
            documents=[texto_experiencia],
            metadatas=[metadatos],
            ids=[id_recuerdo]
        )
        logger.debug(f"Recuerdo '{id_recuerdo}' guardado con éxito.")

    def buscar_similitud(self, contexto_actual: str, num_resultados: int = 3):
        """Busca los recuerdos que más se parezcan a la situación actual."""
        resultados = self.collection.query(
            query_texts=[contexto_actual],
            n_results=num_resultados
        )
        return resultados

# Instancia global para importar en otros archivos
db_vectorial = LazzaroVectorDB()