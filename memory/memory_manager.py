import logging
# import chromadb

logger = logging.getLogger(__name__)

class MemoryManager:
    def __init__(self):
        logger.info("Inicializando ChromaDB Local...")
        # self.client = chromadb.PersistentClient(path="./chroma_data")
        # self.collection = self.client.get_or_create_collection(name="lazzaro_memories")

    def guardar_experiencia(self, evento: str, reaccion: str, impacto_emocional: dict):
        """Guarda un log del evento convertido en vector."""
        logger.info("Guardando nueva experiencia en memoria vectorial...")
        # Lógica de ChromaDB aquí (agregar documento)
        pass

    def buscar_recuerdos(self, contexto_actual: str, limite: int = 3) -> str:
        """Busca en ChromaDB y devuelve un string resumido para inyectar en el prompt."""
        logger.info(f"Buscando recuerdos similares a: {contexto_actual}")
        # Lógica de búsqueda vectorial aquí
        
        # Retorno simulado por ahora
        return "No hay recuerdos relevantes previos sobre esto."

# Instancia global
memoria = MemoryManager()