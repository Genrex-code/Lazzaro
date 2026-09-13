from __future__ import annotations

import json
import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.identity_projection import IdentityContextV1
from ai_engine.persona_engine import PersonaEngine
from ai_engine.provider_prompt_projection import project_chat_messages
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator
from core.behavior_core import BehaviorCore
from core.identity_snapshot import IdentitySnapshotProvider, build_static_self_v1


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


def main() -> None:
    print("=== INICIANDO PRUEBA DE LA CAPA SOCIAL (FASE 3) ===\n")

    # 1. Instanciar y alterar el estado biologico de Lazzaro.
    behavior = BehaviorCore()
    estado = behavior

    print("[!] Simulando que Lazzaro camino mucho y fallo una tarea...")
    estado.registrar_impacto_identidad(route="motor", success=True, intensidad=9)
    estado.registrar_impacto_identidad(route="motor", success=False, intensidad=2)

    estado_actual = estado.state_engine.obtener_estado_biologico(proyectar_descanso=False)
    print("\n=== ESTADO BIOLOGICO SIMULADO ===")
    print(json.dumps(estado_actual, indent=2, ensure_ascii=False))

    assert_true(estado_actual["energia"] < 50, "La prueba esperaba energia baja.")
    assert_true(estado_actual["estres"] > 15, "La prueba esperaba estres perceptible.")

    # 2. Instanciar el motor social.
    motor_social = PersonaEngine()
    identity_provider = IdentitySnapshotProvider(build_static_self_v1(), behavior)
    identity_context = IdentityContextV1.from_snapshot(identity_provider.capture())

    # 3. Falsificar una percepcion de Unity.
    percepcion_falsa = {
        "resumen_percepcion": "Frente a mi veo: Cube a 2.5m al norte y Sphere a 4m al este."
    }

    # 4. Preparar la interaccion.
    mensajes = motor_social.preparar_interaccion(
        mensaje_usuario="hola nina, puedes ver la esfera?",
        identity_context=identity_context,
        percepcion=percepcion_falsa,
    )

    proyectados = project_chat_messages(mensajes)
    assert_true(len(proyectados) == 2, "Se esperaban mensajes system y user.")
    assert_true(proyectados[0].role == "system", "El primer mensaje debe ser system.")
    assert_true("Sphere" in proyectados[1].content, "La percepcion no entro al contexto.")
    assert_true("UNICAMENTE con JSON" in proyectados[0].content, "Falta regla de JSON estricto.")
    assert_true('"accion":"hablar"' in proyectados[0].content, "El prompt debe forzar accion hablar.")

    print("\n=== PROMPT MAESTRO QUE SE ENVIARIA AL LLM ===")
    print(proyectados[0].content)
    print("=================================================\n")

    # 5. Prueba de fuego: escudo anti-alucinaciones.
    # Simulamos que el LLM escupe texto extra y Markdown alrededor del JSON.
    respuesta_sucia_llm = """
Claro, aqui tienes la respuesta en formato JSON como pediste:
```json
{
  "accion": "mover",
  "parametros": {
    "texto_a_comunicar": "Ugh, si, veo la esfera... pero estoy muy cansada para moverme ahora mismo."
  },
  "pensamiento_privado": "Mi energia esta baja y el usuario quiere que vea cosas. Usare sarcasmo.",
  "razon": "Adaptacion al estres actual."
}
```
"""

    respuesta_parseada = motor_social.parsear_respuesta(respuesta_sucia_llm)

    print("=== RESPUESTA PARSEADA Y BLINDADA ===")
    print(json.dumps(respuesta_parseada, indent=2, ensure_ascii=False))

    assert_true(respuesta_parseada["accion"] == "hablar", "La capa social debe bloquear acciones fisicas.")
    assert_true(
        respuesta_parseada["parametros"]["texto_a_comunicar"],
        "La respuesta debe conservar texto comunicable.",
    )

    # 6. Respuesta vacia: debe pedir un reintento barato antes del fallback final.
    respuesta_vacia = motor_social.parsear_respuesta("")
    assert_true(respuesta_vacia["accion"] == "hablar", "El fallback social debe hablar.")
    assert_true(
        respuesta_vacia.get("requiere_reintento") is True,
        "Una respuesta vacia debe marcar requiere_reintento.",
    )

    # 7. JSON truncado como los logs reales: conservar texto si ya venia completo.
    respuesta_truncada = """
{
  "accion": "hablar",
  "parametros": {
    "texto_a_comunicar": "Claro, puedo hablar de eso sin perder el hilo."
  },
  "pensamiento_privado": "El modelo se corto aqui
"""
    respuesta_rescatada = motor_social.parsear_respuesta(respuesta_truncada)
    assert_true(
        respuesta_rescatada["parametros"]["texto_a_comunicar"] == "Claro, puedo hablar de eso sin perder el hilo.",
        "El parser debe rescatar texto_a_comunicar desde JSON parcial.",
    )
    assert_true(
        respuesta_rescatada.get("requiere_reintento") is False,
        "Un JSON parcial recuperable no debe gastar reintento.",
    )

    # 8. Reintento social: el orquestador debe intentar una reparacion corta una sola vez.
    class FakeLLMAdapter:
        def __init__(self):
            self.calls = []

        def ejecutar_plan(self, mensajes, **kwargs):
            self.calls.append((mensajes, kwargs))
            if len(self.calls) == 1:
                return ""
            return json.dumps(
                {
                    "accion": "hablar",
                    "parametros": {"texto_a_comunicar": "Ya volvi, se me cruzo un cable."},
                    "pensamiento_privado": "Reintento social exitoso.",
                    "razon": "Reparacion de salida vacia.",
                }
            )

    orquestador = object.__new__(Orchestrator)
    orquestador.persona_engine = motor_social
    orquestador.identity_provider = identity_provider
    orquestador.llm_adapter = FakeLLMAdapter()

    evento_social = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="test-persona-retry",
        detalles={"mensaje": "hola, sigues ahi?"},
    )
    respuesta_orquestador = orquestador._resolver_ruta_persona(
        evento=evento_social,
        contexto_llm={},
        memoria_texto="",
        politica="exploradora",
        identity_context=identity_context,
    )
    assert_true(
        len(orquestador.llm_adapter.calls) == 2,
        "La Capa Social debe hacer exactamente un reintento.",
    )
    assert_true(
        respuesta_orquestador.parametros["texto_a_comunicar"] == "Ya volvi, se me cruzo un cable.",
        "El reintento social debe reemplazar el fallback inicial si produce JSON valido.",
    )

    print("\n[OK] test_persona paso: prompt dinamico + parser anti-alucinaciones funcionan.")


if __name__ == "__main__":
    main()
