import logging
from core.state_engine import estado_actual
from memory.memory_manager import memoria
from ai_engine.gemini_client import generar_respuesta_lazzaro

logger = logging.getLogger(__name__)

def procesar_evento_roblox(evento_data: dict) -> dict:
    """
    Flujo principal de la arquitectura:
    1. Actualiza emociones.
    2. Busca recuerdos.
    3. Consulta LLM.
    4. Guarda nueva memoria.
    5. Retorna acción a Roblox.
    """
    tipo_evento = evento_data.get("tipo_evento")
    detalles = evento_data.get("detalles", {})
    
    # 1. Impacto Emocional
    estado_actual.evaluar_evento(tipo_evento, detalles)
    stats = estado_actual.obtener_estado_actual()
    
    # 2. Consultar Memoria
    contexto_busqueda = f"{tipo_evento} con {evento_data.get('objeto_involucrado')}"
    recuerdos = memoria.buscar_recuerdos(contexto_busqueda)
    
    # 3. Llamar a la IA
    respuesta_ia_json = generar_respuesta_lazzaro(evento_data, stats, recuerdos)
    
    # 4. Guardar en memoria (se asume que es asíncrono o background task en producción)
    memoria.guardar_experiencia(
        evento=contexto_busqueda, 
        reaccion=respuesta_ia_json, 
        impacto_emocional=stats
    )
    
    # 5. Devolver al Frontend (Roblox)
    logger.info("Ciclo de procesamiento completado. Enviando orden a Roblox.")
    
    # Aquí deberías parsear el JSON de respuesta_ia_json, pero devolveremos un dict de ejemplo
    return {
        "status": "procesado",
        "accion_lazzaro": respuesta_ia_json
    }