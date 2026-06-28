"""
oracle_engine.py - Capa 3 (Cortex de Alto Nivel/Oraculo).

Procesa preguntas complejas, codigo y filosofia. Esta capa es fria:
produce conocimiento interno para que Persona lo verbalice despues.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from core.logger import get_logger

from .oracle_memory import OracleMemory
from .schemas import OracleResult

logger = get_logger(__name__)

OracleSource = Literal["api", "fallback"]


class OracleEngine:
    """
    Motor analitico para tareas complejas (Capa 3).
    No produce ActionResponse ni voz publica; solo OracleResult frio.
    """

    def __init__(self):
        self.system_prompt = (
            "Eres el Oraculo, subsistema de procesamiento logico, tecnico y analitico de Lazzaro. "
            "Tu objetivo es resolver problemas complejos, escribir codigo, explicar conceptos cientificos "
            "y razonar de forma profunda. "
            "No uses emociones, no hagas roleplay, no menciones cansancio ni estres. "
            "Se preciso, directo, estructurado y exhaustivo. "
            "Tu salida sera conocimiento interno para que la Capa 2 lo verbalice despues."
        )

    def evaluar_cache(
        self,
        mensaje_usuario: str,
        memoria: OracleMemory,
    ) -> Optional[OracleResult]:
        """
        Consulta la cache vectorial antes de gastar tokens.
        Si encuentra conocimiento previo, lo devuelve como OracleResult.
        """
        buscar_resultado = getattr(memoria, "buscar_resultado_cacheado", None)
        if callable(buscar_resultado):
            resultado = buscar_resultado(mensaje_usuario)
            if resultado:
                logger.info("Oracle Cache HIT. Devolviendo conocimiento interno cacheado.")
                return resultado
            return None

        # Compatibilidad con cache viejo que solo devolvia texto.
        respuesta_cacheada = memoria.buscar_respuesta_cacheada(mensaje_usuario)
        if respuesta_cacheada:
            logger.info("Oracle Cache HIT legacy. Normalizando a OracleResult.")
            return OracleResult(
                pregunta_original=mensaje_usuario,
                contenido_tecnico=respuesta_cacheada,
                resumen_para_persona=None,
                fuente="cache",
                cache_hit=True,
            )
        return None

    def preparar_consulta(self, mensaje_usuario: str) -> list[dict[str, str]]:
        """
        Construye el prompt frio y estricto para el modelo local o externo.
        """
        prompt_reglas = (
            f"{self.system_prompt}\n\n"
            "=== FORMATO DE SALIDA ESTRICTO ===\n"
            "Responde UNICAMENTE con un objeto JSON valido. Nada de texto fuera del JSON.\n"
            "No incluyas accion, hablar, texto_a_comunicar ni ningun payload para Unity.\n"
            "Estructura requerida:\n"
            "{\n"
            '  "contenido_tecnico": "Respuesta tecnica, codigo o explicacion detallada",\n'
            '  "resumen_para_persona": "Resumen breve para que Persona lo comunique con su voz"\n'
            "}"
        )
        return [
            {"role": "system", "content": prompt_reglas},
            {"role": "user", "content": mensaje_usuario},
        ]

    def keep_y_save(
        self,
        respuesta_llm: str,
        pregunta_original: str,
        memoria: OracleMemory,
        *,
        fuente: OracleSource = "api",
    ) -> OracleResult:
        """
        Limpia la salida del LLM, la normaliza como OracleResult y la cachea.
        """
        fuente_normalizada = self._normalizar_fuente(fuente)
        if not respuesta_llm or not respuesta_llm.strip():
            logger.warning("El LLM devolvio una respuesta vacia.")
            return self._fallback(
                pregunta_original,
                "El procesamiento complejo fue interrumpido por falta de datos.",
            )

        texto_limpio = self._limpiar_json(respuesta_llm)
        try:
            data = json.loads(texto_limpio)
        except json.JSONDecodeError:
            if texto_limpio and not texto_limpio.lstrip().startswith("{"):
                logger.warning("El Oraculo devolvio texto plano; se normaliza como conocimiento interno.")
                return self._resultado_desde_texto_plano(
                    texto_limpio,
                    pregunta_original,
                    memoria,
                    fuente_normalizada,
                )
            logger.error(
                "Error al decodificar el JSON del Oraculo. Respuesta cruda %.100s",
                respuesta_llm,
            )
            return self._fallback(
                pregunta_original,
                "Mi procesador logico encontro un error de sintaxis al generar conocimiento tecnico.",
            )

        if not isinstance(data, dict):
            logger.error("El Oraculo devolvio JSON valido, pero no un objeto.")
            return self._fallback(
                pregunta_original,
                "Mi procesador logico genero una estructura de conocimiento invalida.",
            )

        contenido_tecnico = self._extraer_contenido_tecnico(data)
        if not contenido_tecnico:
            logger.error("El Oraculo no devolvio contenido_tecnico util.")
            return self._fallback(
                pregunta_original,
                "Mi procesador logico no produjo conocimiento util para comunicar.",
            )

        resumen_para_persona = str(data.get("resumen_para_persona") or data.get("resumen") or "").strip() or None
        resultado = OracleResult(
            pregunta_original=pregunta_original,
            contenido_tecnico=contenido_tecnico,
            resumen_para_persona=resumen_para_persona,
            fuente=fuente_normalizada,
            cache_hit=False,
        )
        self._guardar_resultado(memoria, resultado)
        return resultado

    @staticmethod
    def _limpiar_json(texto_crudo: str) -> str:
        """
        Elimina fences de Markdown y texto alrededor del JSON.
        Si no encuentra JSON, conserva texto plano util.
        """
        texto = texto_crudo.strip()
        texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL | re.IGNORECASE).strip()
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)

        if not texto.startswith("{"):
            match = re.search(r"\{.*\}", texto, re.DOTALL)
            if match:
                texto = match.group(0)

        return texto.strip()

    @staticmethod
    def _extraer_contenido_tecnico(data: dict[str, Any]) -> str:
        for clave in ("contenido_tecnico", "respuesta", "contenido", "explicacion", "resultado"):
            valor = data.get(clave)
            if valor:
                return str(valor).strip()

        parametros = data.get("parametros", {})
        if isinstance(parametros, dict):
            for clave in ("texto_a_comunicar", "texto", "mensaje"):
                valor = parametros.get(clave)
                if valor:
                    logger.warning("Normalizando salida legacy del Oraculo con parametros.%s.", clave)
                    return str(valor).strip()
        return ""

    def _resultado_desde_texto_plano(
        self,
        texto: str,
        pregunta_original: str,
        memoria: OracleMemory,
        fuente: OracleSource,
    ) -> OracleResult:
        resultado = OracleResult(
            pregunta_original=pregunta_original,
            contenido_tecnico=texto.strip(),
            resumen_para_persona=None,
            fuente=fuente,
            cache_hit=False,
        )
        self._guardar_resultado(memoria, resultado)
        return resultado

    @staticmethod
    def _guardar_resultado(memoria: OracleMemory, resultado: OracleResult) -> None:
        try:
            memoria.guardar_en_cache(
                resultado.pregunta_original,
                resultado.contenido_tecnico,
                resumen_para_persona=resultado.resumen_para_persona,
                fuente=resultado.fuente,
            )
        except Exception as e:
            logger.error("OracleMemory no pudo guardar el resultado: %s", e, exc_info=True)

    @staticmethod
    def _fallback(pregunta_original: str, mensaje: str) -> OracleResult:
        """Rescate frio cuando el LLM no produce conocimiento valido."""
        logger.info("Activando fallback frio en Capa 3.")
        return OracleResult(
            pregunta_original=pregunta_original,
            contenido_tecnico=mensaje,
            resumen_para_persona=mensaje,
            fuente="error",
            cache_hit=False,
            error="Capa Oraculo activo un resultado de rescate.",
        )

    @staticmethod
    def _normalizar_fuente(fuente: str) -> OracleSource:
        return "fallback" if str(fuente).strip().lower() == "fallback" else "api"
