"""
memory_summarizer.py - Convierte múltiples recuerdos en un resumen compacto y significativo
para inyectar en el prompt del LLM. En su primera versión es determinista: agrupa por tipo
de evento, destaca patrones y selecciona los recuerdos de mayor impacto.
"""

from typing import List
from collections import Counter

from memory.memory_types import MemoryResult, EpisodicMemory, SemanticMemory
from core.logger import get_logger

logger = get_logger(__name__)


class MemorySummarizer:
    """
    Genera un texto resumido a partir de una lista de MemoryResult,
    evitando saturar el prompt con decenas de recuerdos completos.
    """

    @staticmethod
    def resumir(recuerdos: List[MemoryResult], max_caracteres: int = 600) -> str:
        """
        Produce un párrafo que sintetiza los recuerdos recuperados.

        Args:
            recuerdos: lista ordenada de MemoryResult (mejores primero).
            max_caracteres: límite aproximado de longitud del resumen.

        Returns:
            Cadena de texto lista para añadir al contexto del LLM.
        """
        if not recuerdos:
            return "No hay recuerdos relevantes para esta situación."

        episodicos = [r for r in recuerdos if isinstance(r.recuerdo, EpisodicMemory)]
        semanticos = [r for r in recuerdos if isinstance(r.recuerdo, SemanticMemory)]

        fragmentos = []

        # 1. Reglas semánticas más relevantes (máximo 2)
        if semanticos:
            fragmentos.append("Reglas aprendidas:")
            for r in semanticos[:2]:
                regla = r.recuerdo.regla
                fragmentos.append(f"- {regla}")
            fragmentos.append("")

        # 2. Patrones detectados en recuerdos episódicos
        if episodicos:
            patrones = MemorySummarizer._extraer_patrones(episodicos)
            if patrones:
                fragmentos.append("Patrones recurrentes:")
                fragmentos.extend(f"- {p}" for p in patrones)
                fragmentos.append("")

        # 3. Recuerdos más impactantes (máximo 3, con detalles mínimos)
        if episodicos:
            # ordenar por impacto descendente
            impactantes = sorted(
                episodicos,
                key=lambda r: r.recuerdo.metadata.impacto_emocional if r.recuerdo.metadata else 0.0,
                reverse=True,
            )
            fragmentos.append("Experiencias más relevantes:")
            for r in impactantes[:3]:
                tipo = r.recuerdo.metadata.tipo_evento if r.recuerdo.metadata else "evento"
                narr = r.recuerdo.narrativa
                # Acortar narrativa si es muy larga
                if len(narr) > 120:
                    narr = narr[:120] + "..."
                fragmentos.append(f"- [{tipo}] {narr}")

        resultado = "\n".join(fragmentos).strip()
        if len(resultado) > max_caracteres:
            resultado = resultado[:max_caracteres - 3] + "..."

        return resultado

    @staticmethod
    def _extraer_patrones(episodicos: List[MemoryResult]) -> List[str]:
        """
        Detecta patrones simples: repeticiones del mismo tipo de evento,
        secuencias de fracasos, o combinaciones frecuentes.
        """
        patrones = []
        tipos = [r.recuerdo.metadata.tipo_evento for r in episodicos if r.recuerdo.metadata]
        conteo = Counter(tipos)

        # Si hay un tipo de evento muy repetido
        for tipo, cuenta in conteo.items():
            if cuenta >= 3:
                patrones.append(f"Alta frecuencia de '{tipo}' ({cuenta} veces).")

        # Buscar si la mayoría son fracasos/errores
        categorias_negativas = {"fracaso", "error_critico", "loop_detectado", "sobrecarga"}
        negativos = sum(conteo[t] for t in categorias_negativas if t in conteo)
        if negativos >= 3:
            patrones.append("Predominio de eventos negativos en los recuerdos recientes.")

        # Si hay muchos recuerdos con la misma narrativa base (similitud textual simple)
        narrativas = [r.recuerdo.narrativa.lower().strip() for r in episodicos]
        repeticiones = Counter(narrativas)
        for frase, cuenta in repeticiones.items():
            if cuenta >= 2:
                patrones.append(f"El pensamiento '{frase[:80]}' aparece repetidamente.")

        return patrones