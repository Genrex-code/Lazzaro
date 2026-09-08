"""Pruebas estructurales C# del boundary privado ROOT-009."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DTO_SOURCE = (ROOT / "unity_client" / "DTOs.cs").read_text(encoding="utf-8-sig")
AGENT_SOURCE = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
API_SOURCE = (ROOT / "unity_client" / "ApiClient.cs").read_text(encoding="utf-8-sig")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _class_body(source: str, name: str) -> str:
    match = re.search(rf"public\s+class\s+{re.escape(name)}\s*\{{", source)
    if match is None:
        raise AssertionError(f"No se encontro la clase C# {name}.")
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    raise AssertionError(f"La clase C# {name} no cierra sus llaves.")


def test_01_unity_action_dto_has_no_private_cognition_fields() -> None:
    body = _class_body(DTO_SOURCE, "AccionSaliente")
    assert_true("pensamiento_privado" not in body, "Unity DTO conserva private thought.")
    assert_true("razonamiento" not in body, "Unity DTO conserva internal reason.")


def test_02_unity_action_dto_keeps_all_execution_fields() -> None:
    body = _class_body(DTO_SOURCE, "AccionSaliente")
    for declaration in (
        "public string id_accion;",
        "public string nombre;",
        "public JObject parametros;",
        "public bool requiere_confirmacion;",
        "public string estado_ejecucion;",
        "public string id_transaccion_origen;",
    ):
        assert_true(declaration in body, f"Unity perdio un campo operacional: {declaration}")


def test_03_unity_consumer_never_reads_private_cognition() -> None:
    assert_true("accion.pensamiento_privado" not in AGENT_SOURCE, "Consumer lee thought privado.")
    assert_true("accion.razonamiento" not in AGENT_SOURCE, "Consumer lee reason interno.")


def test_04_unity_ui_has_no_remote_private_source() -> None:
    assert_true("textoPensamientoUI" in AGENT_SOURCE, "El componente visual general no debe eliminarse.")
    assert_true("textoPensamientoUI.text" not in AGENT_SOURCE, "La API privada aun alimenta la UI.")
    assert_true("[Pensando...]" not in AGENT_SOURCE, "Permanece un renderer de pensamiento remoto.")


def test_05_unity_logs_have_no_remote_private_source() -> None:
    assert_true("Pensamiento de Lazzaro" not in AGENT_SOURCE, "Unity sigue loggeando cognition remota.")
    assert_true("mostrarLogsCognitivos" in AGENT_SOURCE, "El flag general puede conservar otros logs locales.")


def test_06_public_speech_and_physical_dispatch_remain_intact() -> None:
    assert_true('parametros?["texto_a_comunicar"]' in AGENT_SOURCE, "Hablar perdio su texto publico.")
    assert_true('case "navegar_a_objeto":' in AGENT_SOURCE, "Se altero el dispatch fisico.")
    assert_true('case "hablar":' in AGENT_SOURCE, "Se altero el dispatch de voz.")
    assert_true("EncolarInicioEjecucion(contexto);" in AGENT_SOURCE, "Se altero el lifecycle.")


def test_07_pending_authorization_still_uses_lifecycle_not_cognition() -> None:
    assert_true('respuesta.status == "pendiente"' in AGENT_SOURCE, "Unity perdio pending.")
    assert_true('estadoEjecucion != "awaiting_authorization"' in AGENT_SOURCE, "Falta guard de authorization.")
    assert_true("accionesEsperandoAutorizacion[contexto.IdAccion]" in AGENT_SOURCE, "Falta cola pending.")


def test_08_root010_error_client_remains_local_and_typed() -> None:
    assert_true("Dictionary<string, string> MensajesPublicos" in API_SOURCE, "ROOT-010 allowlist cambio.")
    assert_true("Action<ApiFailure> onFailure" in API_SOURCE, "ROOT-010 typed failure cambio.")
    assert_true("respuesta.mensaje" not in API_SOURCE, "Texto remoto recupero autoridad de UX.")


def test_09_no_debug_switch_reintroduces_private_fields() -> None:
    combined = DTO_SOURCE + AGENT_SOURCE + API_SOURCE
    for forbidden in ("include_private", "X-Debug-Private", "/debug/thoughts", "/debug/reasoning"):
        assert_true(forbidden not in combined, f"Se creo un bypass privado: {forbidden}")


def test_10_csharp_sources_have_balanced_braces() -> None:
    for name, source in (("DTOs.cs", DTO_SOURCE), ("LazzaroAgent.cs", AGENT_SOURCE), ("ApiClient.cs", API_SOURCE)):
        assert_true(source.count("{") == source.count("}"), f"Llaves desbalanceadas en {name}.")


def main() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-009 Unity static: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
