"""Pruebas del contrato de prompts y acciones sin expresiones faciales."""

import json

from ai_engine.persona_engine import PersonaEngine
from ai_engine.prompt_builder import PromptBuilder
from ai_engine.response_parser import ResponseParser
from ai_engine.schemas import AgentContext, LLMResponse
from api.schemas import AccionSaliente


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_prompt_general_usa_contrato_de_cuatro_campos() -> None:
    prompt = PromptBuilder().build(
        AgentContext(
            estado_narrativo="estable",
            personalidad={"paciencia": 0.2},
            herramientas=["hablar"],
            herramientas_detalle={"hablar": "Comunica texto al usuario."},
            proveedor="ollama",
        )
    )
    prompt_normalizado = prompt.system.text.lower()
    assert_true("usa exactamente estas cuatro claves" in prompt_normalizado, "Falta el contrato v2.")
    assert_true("emocion_facial" not in prompt_normalizado, "El prompt no debe pedir expresiones faciales.")
    assert_true("fcl_all_" not in prompt_normalizado, "El prompt no debe contener presets VRM.")
    assert_true('"accion":"none"' in prompt.system.text, "La accion pasiva canonica debe ser 'none'.")


def test_parser_y_schema_rechazan_el_contrato_facial_anterior() -> None:
    salida_v2 = json.dumps(
        {
            "pensamiento": "No hace falta actuar.",
            "accion": "none",
            "parametros": {},
            "razon": "No hay objetivo activo.",
        }
    )
    respuesta = ResponseParser.parse(salida_v2, {"none"})
    assert_true(respuesta.valido, "La salida v2 debe ser valida.")
    assert_true("emocion_facial" not in respuesta.model_dump(), "ActionResponse no debe exponer el campo facial.")
    respuesta_por_defecto = LLMResponse(
        pensamiento="Pausa.",
        parametros={},
        razon="Sin objetivo.",
    )
    assert_true(
        respuesta_por_defecto.accion == "none",
        "El valor pasivo por defecto debe ser 'none'.",
    )

    try:
        LLMResponse(
            pensamiento="Contrato anterior.",
            accion="none",
            parametros={},
            razon="Incluye un campo retirado.",
            emocion_facial="Fcl_ALL_Neutral",
        )
    except Exception:
        pass
    else:
        raise AssertionError("LLMResponse debe rechazar el campo facial retirado.")


def test_persona_y_api_no_propagan_campos_faciales_legacy() -> None:
    salida_legacy = json.dumps(
        {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": "Sigo aqui."},
            "pensamiento_privado": "Respuesta breve.",
            "razon": "Mantener continuidad.",
            "emocion_facial": "Fcl_ALL_Joy",
        }
    )
    datos = PersonaEngine().parsear_respuesta(salida_legacy)
    assert_true("emocion_facial" not in datos, "Persona debe descartar el campo facial legacy.")

    accion = AccionSaliente(id_accion="sin-rostro", nombre="hablar")
    assert_true("emocion_facial" not in accion.model_dump(), "La API no debe publicar el campo facial.")


def main() -> None:
    test_prompt_general_usa_contrato_de_cuatro_campos()
    test_parser_y_schema_rechazan_el_contrato_facial_anterior()
    test_persona_y_api_no_propagan_campos_faciales_legacy()
    print("[OK] test_prompt_contract paso: prompts y acciones sin expresiones faciales.")


if __name__ == "__main__":
    main()
