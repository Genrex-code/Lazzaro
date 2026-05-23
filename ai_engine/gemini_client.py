import os
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

# genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def generar_respuesta_lazzaro(evento_roblox: dict, estado: dict, recuerdos: str) -> str:
    """
    Construye el System Prompt dinámico y llama a Gemini.
    """
    prompt_sistema = f"""
    Eres Lazzaro. Vives en un entorno conectado a Roblox.
    Tus stats actuales:
    - Dopamina: {estado['dopamina']}/100
    - Estrés: {estado['estres']}/100
    - Energía: {estado['energia']}/100
    
    Recuerdos relevantes de situaciones similares:
    {recuerdos}
    
    EVENTO ACTUAL: {evento_roblox['tipo_evento']} con el objeto {evento_roblox['objeto_involucrado']}.
    Detalles: {evento_roblox.get('detalles', 'Ninguno')}
    
    ¿Qué acción deseas tomar en el entorno y qué piensas al respecto?
    Responde en formato JSON: {{"pensamiento": "...", "accion": "...", "objetivo": "..."}}
    """
    
    logger.info("Enviando prompt dinámico al LLM...")
    
    # model = genai.GenerativeModel('gemini-1.5-flash')
    # response = model.generate_content(prompt_sistema)
    # return response.text
    
    # Simulación de respuesta por ahora
    return '{"pensamiento": "Me da curiosidad este evento.", "accion": "acercarse", "objetivo": "cubo_rojo"}'