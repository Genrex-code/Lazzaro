"""
oracle_engine.py - Capa 3 (Cortex de Alto Nivel/Oraculo).
Procesa preguntas complejas, codigo y filosofia. Prioriza la precision y
la logica sobre la personalidad. Utiliza cache semantico para evitar
repetir trabajo pesado.
"""

from core.logger import get_logger
from typing import Dict,Any,List,Optional
from .oracle_memory import OracleMemory
import json
import re

logger = get_logger(__name__)

class OracleEngine:
    """
    Motor analitico para tareas complejas (Capa 3).
    Flujo:1) buscar en cache -> 2) si no existe, consultar LLM ->3) guardar en cache
    """

    def __init__ (self):
        # Identidad fria y tecnica: esta capa prioriza precision sobre personalidad.
        self.system_prompt = (
            "Eres el Oraculo, subsistema de procesamiento logico, tecnico y analitico de Lazzaro. "
            "Tu objetivo es resolver problemas complejos, escribir codigo, explicar conceptos cientificos "
            "y razonar de forma profunda. "
            "No uses emociones, no hagas roleplay, no menciones cansancio ni estres. "
            "Se preciso, directo, estructurado y exhaustivo. "
            "Responde SIEMPRE con el conocimiento mas exacto posible."
        )

    # paso 1 : Intercepcion rapida ( cache)
    def evaluar_cache(
            self,mensaje_usuario: str,memoria:OracleMemory
    ) -> Optional[Dict[str,Any]]:
        """
        Consulta la cache vectorial antes de gastar tokens.
        Si encuentra una respuesta previa similar, la devuelve lista para enviar a unity.
        """
        respuesta_cacheteada = memoria.buscar_respuesta_cacheada(mensaje_usuario)

        if respuesta_cacheteada:
            logger.info("Oracle Cache HIT. Devolviendo respuestas prealmacenada.")
            return {
                "accion" : "hablar",
                "parametros": {"texto_a_comunicar": respuesta_cacheteada},
                "pensamiento_privado": (
                    "Recupere esta informacion de mi cache semantico. "
                    "Respuesta instantanea sin costo de razonamiento."
                ),
                "razon": "Recuperacion desde cache (HIT).",
            }
        return None
    #paso 2: Preparar la consulta pesada ( prompt para el LLM)
    def preparar_consulta(self,mensaje_usuario:str) -> List[Dict[str,str]]:
        """
        Construye el prompt frio y estricto para el modelo local o externo.
        """
        prompt_reglas = (
            f"{self.system_prompt}\n\n"
            "=== FORMATO DE SALIDA ESTRICTO ===\n"
            "Responde UNICAMENTE con un objeto JSON valido. Nada de texto fuera del JSON.\n"
            "Estructura requerida:\n"
            "{\n"
            '  "accion": "hablar",\n'
            '  "parametros": {\n'
            '    "texto_a_comunicar": "Tu respuesta tecnica, codigo o explicacion detallada"\n'
            '  },\n'
            '  "pensamiento_privado": "Resumen breve de como llegaste a la conclusion",\n'
            '  "razon": "Justificacion de la solucion propuesta"\n'
            "}"
        )
        return [
            {"role": "system", "content": prompt_reglas},
            {"role": "user", "content": mensaje_usuario},
        ]
    #paso perdi la cuenta : parsear, validar y asimilar conocimiento 
    def keep_y_save(
            self,
            respuesta_llm:str,
            pregunta_original:str,
            memoria: OracleMemory,
    ) -> Dict[str,Any]:
        """
        Limpia el Json devuelto por el LLM y, si es exitoso,
        persiste el nuevo conocimiento en la cache.
        """
        if not respuesta_llm or not respuesta_llm.strip():
            logger.warning("El LLM devolvio una respuesta vacia.")
            return self._fallback("El procesamiento complejo fue interrumpido por falta de datos")
        
        try:
            texto_limpio = self._limpiar_json(respuesta_llm)
            data = json.loads(texto_limpio)
        except json.JSONDecodeError:
            if texto_limpio and not texto_limpio.lstrip().startswith("{"):
                logger.warning("El Oraculo devolvio texto plano; se encapsula como respuesta hablada.")
                return self._respuesta_desde_texto_plano(
                    texto_limpio,
                    pregunta_original,
                    memoria,
                )
            logger.error(
                "Error al decodificar el JSON del oraculo. Respuestas crudas %.100s",
                respuesta_llm,
            )
            return self._fallback(
                "Mi procesador logico encontro un error de sintaxis al generar la respuesta tecnica."
            )

        if not isinstance(data, dict):
            logger.error("El Oraculo devolvio JSON valido, pero no un objeto.")
            return self._fallback(
                "Mi procesador logico genero una estructura de respuesta invalida."
            )
        
        # Forzar que Capa 3 solo devuelva texto hablado.
        accion  = data.get ("accion","none")
        if accion != "hablar":
            logger.warning("Accion '%s' bloqueada en Capa 3. Forzando 'hablar'.",accion)
            data["accion"] = "hablar"

        # Asimilacion: guarda en cache para el futuro.
        parametros = data.get("parametros", {})
        if not isinstance(parametros, dict):
            logger.error("El Oraculo devolvio parametros con formato invalido.")
            return self._fallback(
                "Mi procesador logico no pudo preparar una respuesta comunicable."
            )

        texto_respuesta = str(parametros.get("texto_a_comunicar", "")).strip()
        if not texto_respuesta:
            logger.error("El Oraculo no devolvio texto_a_comunicar.")
            return self._fallback(
                "Mi procesador logico no produjo una respuesta util para comunicar."
            )

        data["parametros"] = {"texto_a_comunicar": texto_respuesta}
        memoria.guardar_en_cache(pregunta_original,texto_respuesta)
        data.setdefault("pensamiento_privado","Resolucion analitica completada.")
        data.setdefault("razon","Calculo en Capa 3 finalizado.")
        return data
    # Helpers
    @staticmethod
    def _limpiar_json(texto_crudo: str) -> str:
        """
        Elimina fences de Markdown y texto alrededor del JSON.
        Soporta ```json ... ```, ``` ... ``` y extrae el primer objeto JSON.
        """
        texto = texto_crudo.strip()

        # Eliminar fences de Markdown
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)

        # Si aun no empieza por "{", intentar extraer el primer objeto JSON.
        if not texto.startswith("{"):
            match = re.search(r"\{.*\}", texto, re.DOTALL)
            if match:
                texto = match.group(0)

        return texto.strip()

    @staticmethod
    def _respuesta_desde_texto_plano(
        texto: str,
        pregunta_original: str,
        memoria: OracleMemory,
    ) -> Dict[str, Any]:
        """Convierte texto plano util en una respuesta valida para Unity."""
        texto_respuesta = texto.strip()
        memoria.guardar_en_cache(pregunta_original, texto_respuesta)
        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": texto_respuesta},
            "pensamiento_privado": "El Oraculo produjo texto plano y fue normalizado.",
            "razon": "Respuesta analitica normalizada desde texto plano.",
        }

    @staticmethod
    def _fallback(mensaje: str) -> Dict[str, Any]:
        """Rescate cuando el LLM no produce una respuesta valida."""
        logger.info("Activando fallback en Capa 3.")
        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": mensaje},
            "pensamiento_privado": "Fallback por error en procesamiento analitico.",
            "razon": "Error interno.",
            "error": "Capa Oraculo activo una respuesta de rescate.",
        }
