from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from memory.memory_decay import MemoryDecay
from memory.memory_manager import MemoryManager
from memory.memory_retriever import MemoryRetriever
from memory.memory_types import EpisodicMemory, MemoryMetadata, MemoryResult, SemanticMemory


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakeClientConEpisodios:
    def __init__(self, ahora: datetime) -> None:
        self.registros = {
            "ids": ["old-low", "old-high", "new-low", "critical", "future", "invalid-time"],
            "documents": [
                "Veo una capsula sin cambios relevantes.",
                "Observacion muy importante que no debe compactarse.",
                "Veo un cubo nuevo hace un instante.",
                "Alerta de dolor fisico por choque.",
                "Observacion con reloj futuro.",
                "Observacion con reloj corrupto.",
            ],
            "metadatas": [
                {
                    "tipo": "episodico",
                    "tipo_evento": "actualizacion_estado",
                    "impacto_emocional": 0.2,
                    "timestamp": (ahora - timedelta(days=20)).isoformat(),
                },
                {
                    "tipo": "episodico",
                    "tipo_evento": "actualizacion_estado",
                    "impacto_emocional": 0.95,
                    "timestamp": (ahora - timedelta(days=20)).isoformat(),
                },
                {
                    "tipo": "episodico",
                    "tipo_evento": "actualizacion_estado",
                    "impacto_emocional": 0.1,
                    "timestamp": ahora.isoformat(),
                },
                {
                    "tipo": "episodico",
                    "tipo_evento": "alerta_dolor_fisico",
                    "impacto_emocional": 0.2,
                    "timestamp": (ahora - timedelta(days=20)).isoformat(),
                },
                {
                    "tipo": "episodico",
                    "tipo_evento": "actualizacion_estado",
                    "impacto_emocional": 0.1,
                    "timestamp": (ahora + timedelta(days=1)).isoformat(),
                },
                {
                    "tipo": "episodico",
                    "tipo_evento": "actualizacion_estado",
                    "impacto_emocional": 0.1,
                    "timestamp": "not-iso",
                },
            ],
        }

    def listar(self, where=None):
        return self.registros


def main() -> None:
    print("=== INICIANDO PRUEBA DE MEMORY DECAY ===\n")

    ahora = datetime.now(timezone.utc)
    reciente = EpisodicMemory(
        id="recent",
        metadata=MemoryMetadata(
            tipo_evento="comando_usuario",
            valencia=0.0,
            impacto_emocional=0.5,
            timestamp=ahora,
        ),
        narrativa="El usuario pidio continuidad.",
    )
    antiguo = EpisodicMemory(
        id="old",
        metadata=MemoryMetadata(
            tipo_evento="actualizacion_estado",
            valencia=0.0,
            impacto_emocional=0.5,
            timestamp=ahora - timedelta(days=30),
        ),
        narrativa="Observacion antigua de baja prioridad.",
    )
    semantico_antiguo = SemanticMemory(
        id="semantic-old",
        metadata=MemoryMetadata(
            tipo_evento="memoria_relacional",
            valencia=0.25,
            impacto_emocional=0.65,
            timestamp=ahora - timedelta(days=365),
        ),
        regla="Memoria relacional: el usuario es Perro.",
        origen="conversacion_selectiva",
    )

    peso_antiguo = MemoryDecay.calcular_importancia_efectiva(
        antiguo,
        now_utc=ahora,
    )
    assert_true(
        peso_antiguo["importancia_total"] < antiguo.metadata.impacto_emocional,
        "Un recuerdo antiguo debe perder importancia efectiva.",
    )

    resultados = [
        MemoryResult(recuerdo=antiguo, distancia=0.1),
        MemoryResult(recuerdo=reciente, distancia=0.1),
        MemoryResult(recuerdo=semantico_antiguo, distancia=0.1),
    ]
    retriever = object.__new__(MemoryRetriever)
    MemoryRetriever._calcular_scores(retriever, resultados, now_utc=ahora)
    scores = {resultado.recuerdo.id: resultado.metadata_score for resultado in resultados}
    assert_true(
        scores["recent"] > scores["old"],
        "El reranking debe favorecer recuerdos episodicos recientes equivalentes.",
    )
    assert_true(
        scores["semantic-old"] > scores["old"],
        "Las reglas semanticas deben envejecer mas lento que episodios crudos.",
    )

    gestor = object.__new__(MemoryManager)
    gestor.client = FakeClientConEpisodios(ahora)
    gestor.decay = MemoryDecay()
    gestor.consolidacion_antiguedad_minutos = 5
    gestor.consolidacion_umbral = 30
    gestor.consolidacion_importancia_maxima = 0.25

    candidatos = MemoryManager._obtener_candidatos_consolidacion(gestor)
    ids = [candidato["id"] for candidato in candidatos]
    assert_true(ids == ["old-low"], "Solo debe compactar observaciones viejas de baja importancia efectiva.")
    assert_true(
        "decaimiento" in candidatos[0],
        "Los candidatos de consolidacion deben conservar metrica de decaimiento.",
    )

    print("[OK] test_memory_decay paso: decaimiento conectado a recuperacion y consolidacion.")


if __name__ == "__main__":
    main()
