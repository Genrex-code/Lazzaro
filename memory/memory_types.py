"""
memory_types.py - Tipos de datos estrictos para el sistema de memoria de LAZZARO.
Define dataclasses inmutables o congeladas que representan los distintos tipos
de recuerdos y documentos que circulan entre las capas de memoria.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime

# Metadata común a todo recuerdo

@dataclass(frozen=True)
class MemoryMetadata:
    """
    Metadatos obligatorios que acompañan a cada recuerdo.
    No se usan para embeddings, solo para filtrado y reranking.
    """
    tipo_evento: str               # "error_critico", "recompensa", etc.
    valencia: float                # -1.0 (muy negativo) a 1.0 (muy positivo)
    impacto_emocional: float       # 0.0 (irrelevante) a 1.0 (extremadamente intenso)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    # Metadatos adicionales opcionales (ej. estres, energia)
    extra: dict[str, Any] = field(default_factory=dict)

# Memoria episódica (recuerdos concretos)
@dataclass(frozen=True)
class EpisodicMemory:
    """
    Representa una experiencia concreta e inmutable.
    El documento semántico (para embedding) debe generarse a partir
    de 'narrativa' más un resumen del estado, pero esa transformación
    ocurre en memory_encoder.
    """
    id: str                        # UUID único
    metadata: MemoryMetadata
    narrativa: str                 # Descripción textual limpia (ej. "Falló herramienta X bajo multitarea")
    estado_emocional: Optional[dict[str, float]] = None  # snapshot de state_engine (opcional)
    # Podría agregarse 'resultado', 'herramienta', etc. en el futuro

# Memoria semántica (abstracciones, reglas, patrones)

@dataclass(frozen=True)
class SemanticMemory:
    """
    Abstracción o regla aprendida.
    Inicialmente se almacena en una colección ChromaDB aparte,
    tratándose como un documento de texto que resume un patrón.
    """
    id: str
    metadata: MemoryMetadata
    regla: str                     # ej. "La multitarea excesiva reduce precisión"
    origen: str = "consolidacion"  # "manual", "gemini", "determinista"

# Documento listo para embedding (desacoplado de la representación interna)
@dataclass(frozen=True)
class MemoryDocument:
    """
    Estructura que entrega memory_encoder al chroma_client.
    - 'contenido' es lo que se embeberá (texto semántico puro).
    - 'metadata' se guarda como metadata de ChromaDB.
    - 'id' es el identificador único del documento.
    """
    id: str
    contenido: str
    metadata: dict[str, Any]       # dict compatible con ChromaDB (valores simples)

# Elemento de la memoria de trabajo (volátil, en RAM)
@dataclass(frozen=True)
class WorkingMemoryItem:
    """
    Representa una acción o evento reciente, mantenido en una deque.
    No se persiste ni se embeberá.
    """
    accion: str
    resultado: str                 # "exito", "fallo", "pendiente"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    detalles: Optional[dict[str, Any]] = None


# Resultado de una búsqueda en la memoria (para el retriever)
# aca le quite el solo lectura para que se mantenga el erro fuera de memorua perro ( retriever como golden retriver jasjas  (por favor duerman a este tipo - claude))
@dataclass()
class MemoryResult:
    """
    Elemento devuelto por memory_retriever tras una consulta.
    Incluye el recuerdo original (episódico o semántico) y la distancia
    de similitud devuelta por ChromaDB.
    """
    recuerdo: EpisodicMemory | SemanticMemory
    distancia: float               # menor = más similar (usando cosine)
    metadata_score: float = 1.0    # puntuación adicional por reglas de negocio (recencia, impacto)