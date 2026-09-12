"""persona_prompt_buider.py - Consutructor dinamico de identidad ( Capa 2).
Ensambla el prompt del sistema combinando la personalidad base,
el estado fisiologico, la persepcion espacial y la memoria.
"""
# si me doy cuenta que pongo BOLOGIA es mi protesta por que me pusieron 99 en biologia porque al profe no el gusto el logo que puse porque estaba pixeleado
from typing import Optional
from ai_engine.identity_projection import IdentityContextV1, render_persona_identity
from core.logger import get_logger

logger=get_logger(__name__)

class PersonaPromptBuilder:
    """"Genera el prompt maestro para el motor de personalidad de Lazzaro."""

    def build_system_prompt(
        self,
        identity_context: IdentityContextV1,
        percepcion_texto: str = "",
        recuerdos: Optional[str] = "",
    ) -> str|None:
        """
        Construye el prompt maestro que se enviará al LLM local (DeepSeek).
        Convergen aquí todas las variables dinámicas de la Capa 2.

        Args:
            identity_context: proyeccion tipada del snapshot canonico del turno.
            percepcion_texto: resumen perceptual enviado por Unity.
            recuerdos: texto opcional con recuerdos relevantes.

        Returns:
            Prompt completo listo para enviar al modelo.
            """
        prompt = render_persona_identity(identity_context) + "\n\n"
        prompt += (
            "=== ESTILO SOCIAL ===\n"
            "Habla en primera persona, con voz casual y directa. Puedes usar calidez, "
            "frialdad o sarcasmo moderado segun el estado, sin adoptar tono de servicio.\n\n"
        )

        #1.- Percepcion del entorno
        prompt += "=== ENTORNO ACTUAL REPORTADO POR EL RUNTIME ===\n"
        prompt += f"{percepcion_texto}\n\n" if percepcion_texto else "No percibes objetos a tu alrededor.\n\n"

        #2.- Memoria (evidencia contextual, nunca authority de identidad)
        if recuerdos:
            prompt += "=== TUS RECUERDOS RECIENTES O RELEVANTES === \n"
            prompt += f"{recuerdos}\n\n"

        #3.- reglas de salida
        prompt += (
            "=== REGLAS DE RESPUESTA ===\n"
            "1. Responde en primera persona, de forma conversacional.\n"
            "2. Adapta la longitud de tu respuesta a tu Energía y Estrés actuales de forma natural "
            "(no menciones tus porcentajes numéricos, solo actúa en consecuencia).\n"
            "3. Si se te pregunta por tu entorno, utiliza la información de "
            "'ENTORNO ACTUAL REPORTADO POR EL RUNTIME'.\n"
        )

        logger.debug("Prompt de persona construido (%d caracteres).",len(prompt))
        return prompt
