import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from ai_engine.client_factory import build_ai_client, describe_ai_backend
from ai_engine.prompt_builder import PromptBuilder
from ai_engine.response_parser import ResponseParser
from ai_engine.schemas import AgentContext
from config.settings import Settings


def probar_cerebro():
    settings = Settings()
    print(f"1. Iniciando backend LLM: {describe_ai_backend(settings)}...")
    client = build_ai_client(settings=settings)

    print("2. Simulando el contexto de Lazzaro...")
    contexto = AgentContext(
        estado_narrativo="Lazzaro se siente con energia y listo para trabajar.",
        valores_estado={"estres": 10.0, "energia": 90.0},
        personalidad={"curiosidad": 0.8, "precision": 0.9},
        herramientas=["escanear_red", "buscar_archivo", "ninguna"],
        memoria_texto="Ayer aprendi a no usar variables globales.",
    )

    print("3. Construyendo el PromptPlan tipado...")
    builder = PromptBuilder()
    prompt_final = builder.build(contexto)

    print("4. Enviando al backend LLM...")
    print(
        "   (Enviando contexto tipado de "
        f"{len(prompt_final.user_envelope_json())} caracteres al backend activo...)"
    )

    respuesta_cruda = client.generate_plan(
        prompt_final,
        response_mime_type="application/json",
    )
    print("\n--- RESPUESTA CRUDA DEL LLM ---")
    print(respuesta_cruda)
    print("-------------------------------\n")

    print("5. Pasando por el ResponseParser...")
    accion_validada = ResponseParser.parse(respuesta_cruda)

    if accion_validada.valido:
        print("[OK] El LLM devolvio un JSON valido.")
        print(f"Lazzaro penso: {accion_validada.pensamiento}")
        print(f"Lazzaro decidio usar la herramienta: {accion_validada.accion}")
    else:
        print(f"[ERROR] El LLM se equivoco. Razon: {accion_validada.error}")

    client.close()


if __name__ == "__main__":
    probar_cerebro()
