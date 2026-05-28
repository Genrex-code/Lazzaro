"""
prompt_builder.py - Construye el prompt final para el LLM a partir de plantillas
y del AgentContext proporcionado por context_builder.
"""

from pathlib import Path
from typing import Optional

from ai_engine.schemas import AgentContext, PromptSections
from core.logger import get_logger

logger = get_logger(__name__)


class PromptBuilder:
    """
    Ensambla prompts dinamicos basados en plantillas de texto y el contexto actual.
    Las plantillas se cargan desde la carpeta prompts/ y se rellenan con los datos
    del agente.
    """

    def __init__(self, prompts_dir: Optional[str] = None):
        default_prompts_dir = Path(__file__).resolve().parent / "prompts"
        self.prompts_dir = Path(prompts_dir) if prompts_dir else default_prompts_dir
        self.templates = self._cargar_plantillas()

    def _cargar_plantillas(self) -> dict[str, str]:
        """Lee los archivos .txt de la carpeta prompts/ y los devuelve como dict."""
        plantillas = {}
        archivos = [
            "system_prompt.txt",
            "personality_prompt.txt",
            "estado_prompt.txt",
            "memoria_prompt.txt",
            "tool_prompt.txt",
            "format_prompt.txt",
        ]
        for archivo in archivos:
            ruta = self.prompts_dir / archivo
            try:
                with ruta.open("r", encoding="utf-8") as f:
                    plantillas[archivo] = f.read()
            except FileNotFoundError:
                logger.warning("Plantilla no encontrada: %s. Se usara una cadena vacia.", ruta)
                plantillas[archivo] = ""
        return plantillas

    def build(self, context: AgentContext) -> str:
        """
        Construye el prompt completo a partir del AgentContext.
        Retorna un string listo para enviar al LLM.
        """
        secciones = PromptSections()
        secciones.system = self.templates.get("system_prompt.txt", "")
        secciones.personalidad = self._construir_personalidad(context.personalidad)
        secciones.estado_emocional = self._construir_estado_emocional(context.estado_narrativo)
        secciones.entorno = self._construir_entorno(context.percepcion_texto)
        secciones.objetivo = self._construir_objetivo(context.tarea)
        secciones.memoria = self._construir_memoria(context.memoria_texto)
        secciones.herramientas = self._construir_herramientas(context.herramientas)
        secciones.restricciones = self._construir_restricciones(context)
        secciones.formato_respuestas = self.templates.get("format_prompt.txt", "")

        prompt = secciones.ensamblar()
        logger.debug("Prompt construido con %d caracteres.", len(prompt))
        return prompt

    def _construir_personalidad(self, rasgos: dict[str, float]) -> str:
        plantilla = self.templates.get("personality_prompt.txt", "")
        if not rasgos:
            return plantilla.replace("{rasgos}", "neutros")
        texto_rasgos = ", ".join(f"{k}={v:.2f}" for k, v in rasgos.items())
        return plantilla.replace("{rasgos}", texto_rasgos)

    def _construir_estado_emocional(self, narrativa: str) -> str:
        plantilla = self.templates.get("estado_prompt.txt", "")
        return plantilla.replace("{estado_narrativo}", narrativa or "estable")

    def _construir_entorno(self, percepcion_texto: str) -> str:
        if not percepcion_texto:
            return ""
        return "Percepcion actual del entorno:\n" + percepcion_texto

    def _construir_memoria(self, memoria: str) -> str:
        plantilla = self.templates.get("memoria_prompt.txt", "")
        return plantilla.replace("{memoria}", memoria or "sin recuerdos relevantes")

    def _construir_herramientas(self, herramientas: list[str]) -> str:
        plantilla = self.templates.get("tool_prompt.txt", "")
        if not herramientas:
            return plantilla.replace("{herramientas}", "ninguna (solo modo texto)")
        return plantilla.replace("{herramientas}", ", ".join(herramientas))

    def _construir_objetivo(self, tarea: Optional[dict]) -> str:
        if not tarea or not tarea.get("objetivo"):
            return "No hay un objetivo activo en este momento."

        obj = tarea["objetivo"]
        frustracion = tarea.get("frustracion", 0.0)
        umbral = tarea.get("umbral_abandono", 80.0)
        intentos = tarea.get("intentos", 0)
        fallos = tarea.get("fallos", 0)
        loop = tarea.get("loop_detectado", False)
        estancado = tarea.get("estancamiento", False)

        lineas = [f"Objetivo actual: {obj}."]
        lineas.append(
            f"Intentos: {intentos}, Fallos: {fallos}. Frustracion: {frustracion:.1f}/100 "
            f"(umbral de abandono: {umbral:.1f})."
        )
        if loop:
            lineas.append("Se ha detectado un bucle de acciones repetidas.")
        if estancado:
            lineas.append("Estas estancado con muchos fallos consecutivos.")
        return " ".join(lineas)

    def _construir_restricciones(self, context: AgentContext) -> str:
        restricciones = []
        politica = context.politica
        if politica == "cautelosa":
            restricciones.append("- Eres extremadamente cauteloso. Evita acciones arriesgadas.")
        elif politica == "exploradora":
            restricciones.append(
                "- Tienes tendencia exploratoria. Puedes probar herramientas no convencionales si el riesgo es bajo."
            )
        elif politica == "eficiente":
            restricciones.append("- Priorizas la eficiencia. Busca la solucion mas rapida y con menos pasos.")
        elif politica == "social":
            restricciones.append("- Prefieres pedir ayuda o interactuar en lugar de actuar solo.")
        elif politica == "autonoma":
            restricciones.append("- Tienes alta autonomia. Actua solo si la accion es coherente y segura.")

        banderas = context.banderas
        if banderas.get("debe_pedir_ayuda"):
            restricciones.append("- Tu nivel de confianza es bajo. Considera pedir ayuda.")
        if banderas.get("debe_descansar"):
            restricciones.append("- Estas cansado. Limita la accion a pasos ligeros o pausas.")
        if banderas.get("debe_ser_precavido"):
            restricciones.append("- Tu foco es bajo. Verifica dos veces cualquier accion.")
        if banderas.get("debe_abandonar"):
            restricciones.append("- Has superado el umbral de frustracion. Considera abandonar la estrategia actual.")
        if banderas.get("debe_cambiar_estrategia"):
            restricciones.append("- Tu estrategia actual no esta funcionando. Cambia de enfoque.")
        if banderas.get("esta_bloqueado"):
            restricciones.append("- Te encuentras bloqueado. Pide ayuda o replantea el problema desde cero.")

        if restricciones:
            return "Restricciones actuales:\n" + "\n".join(restricciones)
        return ""
