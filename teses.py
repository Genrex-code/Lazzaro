import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import Settings
from ai_engine.gemini_client import GeminiClient
from ai_engine.schemas import AgentContext
from ai_engine.prompt_builder import PromptBuilder
from ai_engine.response_parser import ResponseParser

def probar_cerebro():
    print("1. Iniciando cliente Gemini...")
    # El cliente leerá la key de tu archivo .env automáticamente gracias a Pydantic
    client = GeminiClient(Settings()) 
    
    print("2. Simulando el contexto de Lazzaro...")
    # Simulamos lo que escupiría el motor emocional y la memoria
    contexto = AgentContext(
        estado_narrativo="Lazzaro se siente con energía y listo para trabajar.",
        valores_estado={"estres": 10.0, "energia": 90.0},
        personalidad={"curiosidad": 0.8, "precisión": 0.9},
        herramientas=["escanear_red", "buscar_archivo", "ninguna"],
        memoria_texto="Ayer aprendí a no usar variables globales."
    )
    
    print("3. Construyendo el Prompt gigante...")
    builder = PromptBuilder()
    prompt_final = builder.build(contexto)
    
    print("4. Enviando al LLM (Esperando respuesta...)")
    # Imprimimos un pedacito del prompt para que veas qué se envió
    print(f"   (Enviando {len(prompt_final)} caracteres a Gemini...)")
    
    respuesta_cruda = client.generate(prompt_final)
    print("\n--- RESPUESTA CRUDA DEL LLM ---")
    print(respuesta_cruda)
    print("-------------------------------\n")
    
    print("5. Pasando por el Guardia de Seguridad (ResponseParser)...")
    accion_validada = ResponseParser.parse(respuesta_cruda)
    
    if accion_validada.valido:
        print("✅ ¡ÉXITO! El LLM devolvió un JSON perfecto.")
        print(f"Lazzaro pensó: {accion_validada.pensamiento}")
        print(f"Lazzaro decidió usar la herramienta: {accion_validada.accion}")
    else:
        print(f"❌ ERROR: El LLM se equivocó. Razón: {accion_validada.error}")

if __name__ == "__main__":
    probar_cerebro()