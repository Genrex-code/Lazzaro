"""
context_buider.py - Construye el contexto estrcuturado que alimenta el prompt del LLM.
Recibe las salida de PsychologyEngine (contexto_llm), la memoria resumida,
las herramientas disponibles y la politica activa, y los empaquetadso en un AgentContext.
No reinterpreta emociones; solo organiza los datos.
"""

from typing import Optional,List

from core.logger import get_logger
from ai_engine.schemas import AgentContext

#logs
logger = get_logger(__name__)

class ContextBuider: 
    """
    Resposable de consolidad toda la informacio relevante de un unico objeto
    AgentContext, listo para que el prompt_buider lo transforme en texto.
    """

    @staticmethod
    def build(
        contexto_llm: dict,
        memoria_texto: str = "",
        herramientas: Optional[list[str]] = None,
        politica: str = "exploradora",
        tarea: Optional[dict] = None,
    ) -> AgentContext:
        """
        Construye un AgentContext inmutable a partir de las piezas proporcionadas.
        
        Args:
            contexto_llm: diccionario devuelto por PsychologyEngine.generar_contexto_llm.
                Contiene 'estado_narrativo', 'valores', 'umbrales', 'personalidad', etc.
            memoria_texto: texto resumido de la memoria (desde MemoryManager.obtener_contexto_memoria).
            herramientas: lista de nombres de herramientas disponibles.
            politica: nombre de la política activa (ej. "cautelosa").
            tarea: diccionario opcional con 'objetivo_id', 'frustracion_local', etc.

        Returns:
            AgentContext listo para el prompt_builder.
        """
        #Extraer campos del context_llm con valores por defecto "seguros"
        estado_narrativo = contexto_llm.get("estado_narrativo","")
        valores = contexto_llm.get("valores",{})
        personalidad = contexto_llm.get("personalidad", {})
        umbrales = contexto_llm.get("umbrales",{})
        tarea_data = tarea if tarea else contexto_llm.get("tarea")

        #construimos el contexto de tarea si existe+
        tarea_contexto = None
        if tarea_data: 
            tarea_contexto = {
                "objetivo": tarea_data.get("objetivo_id", ""),
                "intentos": tarea_data.get("intentos_totales", 0),
                "fallos": tarea_data.get("intentos_fallidos", 0),
                "frustracion": tarea_data.get("frustracion_local", 0.0),
                "umbral_abandono": tarea_data.get("umbral_abandono", 80.0),
                "loop_detectado": tarea_data.get("loop_detectado", False),
                "estancamiento": tarea_data.get("estancamiento_detectado", False),
            }

        agent_context = AgentContext(
            estado_narrativo=estado_narrativo,
            valores_estado=valores,
            personalidad=personalidad,
            tarea=tarea_contexto,
            memoria_texto=memoria_texto,
            herramientas=herramientas or [],
            politica=politica,
            banderas=umbrales,
        )
        logger.debug("AgentContext construido con política '%s' y %d herramientas.",
                     politica, len(herramientas or []))
        return agent_context
