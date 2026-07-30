"""
persona_engine.py - Capa 2 Orquestador Social.

Prepara el prompt social de Lazzaro, parsea la salida del LLM local y
garantiza que la unica accion emitida por esta capa sea "hablar".
"""

import json
import re
from typing import Any, Dict, List, Optional

from core.logger import get_logger

from .identity_state import IdentityState
from .persona_prompt_buider import PersonaPromptBuilder
from .schemas import OracleResult

logger = get_logger(__name__)


class PersonaEngine:
    """
    Cerebro conversacional de Lazzaro.
    No ejecuta herramientas fisicas; solo gestiona dialogo.
    """

    def __init__(self):
        self.prompt_builder = PersonaPromptBuilder()

    @staticmethod
    def construir_respuesta_guardado_conversacional(
        status: str,
        turn_count: int = 0,
    ) -> Dict[str, Any]:
        """
        Verbaliza de forma determinista un resultado ya resuelto por memoria.

        La respuesta no decide ni simula la persistencia: solamente comunica el
        estado confirmado, evitando que un fallo del LLM anuncie un éxito falso.
        """
        if status == "saved":
            texto = "Está bien, lo recordaré para después."
            pensamiento = f"Quedaron persistidos {max(0, turn_count)} intercambios públicos."
            razon = "Confirmar un guardado manual ya completado."
            emocion = "Fcl_ALL_Joy"
        elif status == "already_saved":
            texto = "Eso ya estaba guardado en mi mente; no hace falta duplicarlo."
            pensamiento = "La fotografía conversacional ya tenía una copia persistente."
            razon = "Confirmar idempotencia sin fingir una escritura nueva."
            emocion = "Fcl_ALL_Neutral"
        elif status == "empty":
            texto = "Todavía no tengo una conversación reciente en RAM que pueda guardar."
            pensamiento = "No había intercambios públicos previos en la RAM conversacional."
            razon = "Explicar que no hubo contenido para persistir."
            emocion = "Fcl_ALL_Neutral"
        else:
            texto = (
                "No pude guardar lo que hablamos esta vez. "
                "Prefiero decírtelo antes que fingir que lo recordé."
            )
            pensamiento = "La memoria persistente no confirmó el guardado solicitado."
            razon = "Informar un fallo real sin anunciar éxito."
            emocion = "Fcl_ALL_Sorrow"

        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": texto},
            "pensamiento_privado": pensamiento,
            "razon": razon,
            "emocion_facial": emocion,
            "requiere_reintento": False,
        }

    def preparar_interaccion(
        self,
        mensaje_usuario: str,
        estado_biologico: IdentityState,
        percepcion: Optional[Dict[str, Any]] = None,
        recuerdos: str = "",
        restriccion_canal: str = "",
    ) -> List[Dict[str, str]]:
        """
        Prepara la lista de mensajes [system, user] para enviar al LLM local.
        """
        percepcion = percepcion or {}
        percepcion_texto = percepcion.get("resumen_percepcion", "")

        estado_inyectado = estado_biologico.inyectar_en_prompt()

        system_prompt = self.prompt_builder.build_system_prompt(
            estado_inyectado=estado_inyectado,
            percepcion_texto=percepcion_texto,
            recuerdos=recuerdos,
        ) or ""

        if restriccion_canal:
            system_prompt += (
                "\n=== RESTRICCION DEL CANAL ACTUAL ===\n"
                f"{restriccion_canal.strip()}\n"
            )

        system_prompt += (
            "\n=== FORMATO DE SALIDA ESTRICTO ===\n"
            "Responde UNICAMENTE con un objeto JSON valido. Nada de texto fuera del JSON.\n"
            "Estructura requerida:\n"
            "{\n"
            '  "accion": "hablar",\n'
            '  "parametros": {\n'
            '    "texto_a_comunicar": "Tu respuesta como Lazzaro aqui"\n'
            "  },\n"
            '  "pensamiento_privado": "Breve reflexion interna antes de hablar",\n'
            '  "razon": "Motivo por el que dices esto"\n'
            "}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": mensaje_usuario},
        ]

    def preparar_verbalizacion_oraculo(
        self,
        mensaje_usuario: str,
        estado_biologico: IdentityState,
        datos_oraculo: OracleResult,
        percepcion: Optional[Dict[str, Any]] = None,
        recuerdos: str = "",
        restriccion_canal: str = "",
    ) -> List[Dict[str, str]]:
        """
        Prepara una respuesta publica usando conocimiento frio de Capa 3.
        El Oraculo aporta contenido; Persona decide voz, tono e identidad.
        """
        percepcion = percepcion or {}
        percepcion_texto = percepcion.get("resumen_percepcion", "")
        estado_inyectado = estado_biologico.inyectar_en_prompt()

        system_prompt = self.prompt_builder.build_system_prompt(
            estado_inyectado=estado_inyectado,
            percepcion_texto=percepcion_texto,
            recuerdos=recuerdos,
        ) or ""

        if restriccion_canal:
            system_prompt += (
                "\n=== RESTRICCION DEL CANAL ACTUAL ===\n"
                f"{restriccion_canal.strip()}\n"
            )

        contenido_tecnico = self._limitar_texto(datos_oraculo.contenido_tecnico, 3000)
        resumen_para_persona = self._limitar_texto(datos_oraculo.resumen_para_persona or "", 800)
        error_oraculo = self._limitar_texto(datos_oraculo.error or "", 500)

        system_prompt += (
            "\n=== CONOCIMIENTO INTERNO DEL ORACULO ===\n"
            "Este bloque NO es tu voz publica. Es material interno de Capa 3.\n"
            f"Pregunta original: {datos_oraculo.pregunta_original}\n"
            f"Fuente: {datos_oraculo.fuente}\n"
            f"Cache hit: {datos_oraculo.cache_hit}\n"
            f"Error interno: {error_oraculo or 'ninguno'}\n"
            f"Contenido tecnico:\n{contenido_tecnico}\n"
        )
        if resumen_para_persona:
            system_prompt += f"Resumen sugerido para Persona:\n{resumen_para_persona}\n"

        system_prompt += (
            "\n=== TU TAREA ===\n"
            "Responde al usuario con tu propia voz de Lazzaro.\n"
            "No digas que eres el Oraculo ni que consultaste una capa interna.\n"
            "No inventes datos fuera del contenido tecnico recibido.\n"
            "Si el Oraculo fallo, dilo de forma natural y breve.\n"
            "Mantente social, clara y coherente con tu identidad.\n"
            "\n=== FORMATO DE SALIDA ESTRICTO ===\n"
            "Responde UNICAMENTE con un objeto JSON valido. Nada de texto fuera del JSON.\n"
            "La unica accion permitida es hablar.\n"
            "Estructura requerida:\n"
            "{\n"
            '  "accion": "hablar",\n'
            '  "parametros": {\n'
            '    "texto_a_comunicar": "Tu respuesta final como Lazzaro"\n'
            "  },\n"
            '  "pensamiento_privado": "Breve reflexion interna antes de hablar",\n'
            '  "razon": "Como adaptaste el conocimiento tecnico a tu voz"\n'
            "}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": mensaje_usuario},
        ]

    def preparar_reintento_json(
        self,
        mensaje_usuario: str,
        respuesta_fallida: str,
        restriccion_canal: str = "",
    ) -> List[Dict[str, str]]:
        """Construye un prompt minimo para reparar una respuesta social fallida."""
        salida = self._limitar_texto(respuesta_fallida or "", 1200)
        restriccion = (
            f"Restriccion del canal: {restriccion_canal.strip()}\n"
            if restriccion_canal
            else ""
        )
        return [
            {
                "role": "system",
                "content": (
                    "Repara la respuesta de Lazzaro. Devuelve SOLO JSON valido, sin markdown.\n"
                    "La unica accion permitida es hablar.\n"
                    f"{restriccion}"
                    "Formato exacto: "
                    '{"accion":"hablar","parametros":{"texto_a_comunicar":"..."},"pensamiento_privado":"...","razon":"..."}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Mensaje original del usuario: {mensaje_usuario}\n"
                    f"Salida fallida del modelo: {salida}\n"
                    "Si no puedes recuperar la idea, responde una frase breve y natural."
                ),
            },
        ]

    def parsear_respuesta(self, respuesta_llm: str) -> Dict[str, Any]:
        """
        Extrae y valida el JSON de la respuesta cruda del LLM.
        Si el modelo devuelve JSON parcial, intenta rescatar texto comunicable.
        """
        if not respuesta_llm or not respuesta_llm.strip():
            logger.warning("Respuesta del LLM vacia.")
            return self._fallback(
                "Me quede en blanco por un momento.",
                error="respuesta_vacia",
                requiere_reintento=True,
            )

        try:
            data = json.loads(self._limpiar_json(respuesta_llm))
        except json.JSONDecodeError:
            data = self._rescatar_respuesta_parcial(respuesta_llm)
            if data is None:
                logger.error("El LLM no devolvio JSON valido. Respuesta cruda: %.200s", respuesta_llm)
                return self._fallback(
                    "Senti un cortocircuito en mi lenguaje, no pude procesar eso.",
                    error="json_irrecuperable",
                    requiere_reintento=True,
                )
            logger.warning("Se rescato texto comunicable desde JSON social parcial.")

        if not isinstance(data, dict):
            logger.error("El LLM devolvio JSON valido, pero no un objeto.")
            return self._fallback(
                "Mi respuesta salio con una forma rara. Dame un segundo.",
                error="json_no_objeto",
                requiere_reintento=True,
            )

        accion = data.get("accion", "none")
        if accion != "hablar":
            logger.warning("Accion '%s' bloqueada en Capa 2. Forzando 'hablar'.", accion)
            data["accion"] = "hablar"

        parametros = data.get("parametros", {})
        if not isinstance(parametros, dict):
            logger.warning("Los parametros sociales no son un objeto. Se usa fallback.")
            return self._fallback(
                "No logre ordenar bien lo que queria decir.",
                error="parametros_invalidos",
                requiere_reintento=True,
            )

        texto_a_comunicar = str(parametros.get("texto_a_comunicar", "")).strip()
        if not texto_a_comunicar:
            logger.warning("El JSON no contiene 'texto_a_comunicar'. Se usa fallback.")
            return self._fallback(
                "No se que decir, mi mente divago.",
                error="texto_vacio",
                requiere_reintento=True,
            )

        data["parametros"] = {"texto_a_comunicar": self._limitar_texto(texto_a_comunicar, 700)}
        data["pensamiento_privado"] = self._limitar_texto(str(data.get("pensamiento_privado", "")), 240)
        data["razon"] = self._limitar_texto(str(data.get("razon", "")), 200)
        data.setdefault("emocion_facial", "Fcl_ALL_Neutral")
        data.setdefault("requiere_reintento", False)
        return data

    @staticmethod
    def _limpiar_json(texto_crudo: str) -> str:
        """
        Elimina bloques de Markdown y texto alrededor del primer JSON.
        Si el objeto esta truncado, devuelve el fragmento para rescate parcial.
        """
        texto = re.sub(r"<think>.*?</think>", "", texto_crudo, flags=re.DOTALL | re.IGNORECASE).strip()
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)
        objeto = PersonaEngine._extraer_primer_objeto_json(texto)
        return (objeto or texto).strip()

    @staticmethod
    def _extraer_primer_objeto_json(texto: str) -> Optional[str]:
        inicio = texto.find("{")
        if inicio == -1:
            return None

        profundidad = 0
        en_cadena = False
        escape = False
        for indice in range(inicio, len(texto)):
            char = texto[indice]
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                en_cadena = not en_cadena
                continue
            if en_cadena:
                continue
            if char == "{":
                profundidad += 1
            elif char == "}":
                profundidad -= 1
                if profundidad == 0:
                    return texto[inicio : indice + 1]

        return texto[inicio:]

    @classmethod
    def _rescatar_respuesta_parcial(cls, texto_crudo: str) -> Optional[Dict[str, Any]]:
        texto = re.sub(r"<think>.*?</think>", "", texto_crudo, flags=re.DOTALL | re.IGNORECASE)
        texto_rescatado = cls._extraer_valor_string(texto, "texto_a_comunicar")
        if not texto_rescatado:
            return None

        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": texto_rescatado},
            "pensamiento_privado": "Respuesta social recuperada de una salida parcial.",
            "razon": "Se rescato texto comunicable de una salida incompleta.",
            "emocion_facial": "Fcl_ALL_Neutral",
            "recuperado_de_json_parcial": True,
            "requiere_reintento": False,
        }

    @staticmethod
    def _extraer_valor_string(texto: str, clave: str) -> Optional[str]:
        patron_cerrado = rf'"{re.escape(clave)}"\s*:\s*"((?:\\.|[^"\\])*)"'
        match = re.search(patron_cerrado, texto, flags=re.DOTALL)
        if match:
            try:
                return str(json.loads(f'"{match.group(1)}"')).strip()
            except json.JSONDecodeError:
                return match.group(1).strip()

        patron_truncado = rf'"{re.escape(clave)}"\s*:\s*"([^"\r\n}}]*)'
        match = re.search(patron_truncado, texto, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _limitar_texto(texto: str, limite: int) -> str:
        texto_limpio = " ".join(str(texto or "").split())
        if len(texto_limpio) <= limite:
            return texto_limpio
        return texto_limpio[: limite - 3].rstrip() + "..."

    def _fallback(
        self,
        mensaje_seguridad: str,
        *,
        error: str = "fallo_llm",
        requiere_reintento: bool = False,
    ) -> Dict[str, Any]:
        """Rescate cuando el LLM falla por completo."""
        logger.info("Activando fallback de seguridad en Capa 2.")
        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": mensaje_seguridad},
            "pensamiento_privado": "Fallback de seguridad por fallo del LLM.",
            "razon": "Error interno.",
            "emocion_facial": "Fcl_ALL_Neutral",
            "error": error,
            "requiere_reintento": requiere_reintento,
        }
