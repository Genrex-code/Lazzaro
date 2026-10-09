"""ROOT-038 B1: paridad estatica de distancia para navegar_a_objeto."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_engine.motor_schemas import MotorActionAdapterV1


ROOT = Path(__file__).resolve().parents[1]
AGENT = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
MOVEMENT = (ROOT / "unity_client" / "MovementController.cs").read_text(
    encoding="utf-8-sig"
)


def _method(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_absent_distance_keeps_the_historical_navmesh_threshold() -> None:
    public_path = _method(
        MOVEMENT,
        "public void NavegarAObjeto(",
        "public void NavegarAObjetivoExacto(",
    )
    apply = _method(
        MOVEMENT,
        "private void AplicarDistanciaParadaDeAccion(",
        "private void RestaurarDistanciaParadaDeAccion()",
    )

    assert "float? distanciaParada = null" in public_path
    assert "if (!distanciaParada.HasValue)" in apply
    assert "return;" in apply.split("if (!distanciaParada.HasValue)", 1)[1]


@pytest.mark.parametrize("distance", (0.1, 2.75, 10.0))
def test_canonical_distance_is_forwarded_to_the_exact_unclamped_consumer(
    distance: float,
) -> None:
    canonical = MotorActionAdapterV1.require(
        "navegar_a_objeto",
        {"nombre_objeto": "Caja", "distancia": distance},
    )
    apply = _method(
        MOVEMENT,
        "private void AplicarDistanciaParadaDeAccion(",
        "private void RestaurarDistanciaParadaDeAccion()",
    )

    assert canonical.parameters_dict()["distancia"] == distance
    assert "navAgent.stoppingDistance = distanciaParada.Value;" in apply
    assert "Mathf.Clamp" not in apply


def test_unity_parser_accepts_only_json_numbers_in_the_frozen_domain() -> None:
    parser = _method(
        AGENT,
        "private static bool TryLeerDistanciaNavegacionObjeto(",
        "private void EjecutarInteraccion(",
    )

    assert 'TryGetValue("distancia", out JToken token)' in parser
    assert "token.Type != JTokenType.Integer" in parser
    assert "token.Type != JTokenType.Float" in parser
    assert "double.IsNaN(valor)" in parser
    assert "double.IsInfinity(valor)" in parser
    assert "valor < 0.1d" in parser
    assert "valor > 10.0d" in parser
    assert "float.TryParse" not in parser
    assert "Mathf.Clamp" not in parser


def test_invalid_present_distance_returns_before_any_navigation_call() -> None:
    handler = _method(
        AGENT,
        "private void EjecutarNavegarAObjeto(",
        "private void EjecutarNavegarAPosicion(",
    )
    validation = handler.index("TryLeerDistanciaNavegacionObjeto")
    exact_dispatch = handler.index("movimiento.NavegarAObjetivoExacto")
    legacy_dispatch = handler.index("movimiento.NavegarAObjeto")

    assert validation < exact_dispatch
    assert validation < legacy_dispatch
    invalid_branch = handler.split(
        "if (!TryLeerDistanciaNavegacionObjeto", 1
    )[1].split("ContextoAccion movimientoContexto", 1)[0]
    assert "ReportarResultadoAccion" in invalid_branch
    assert "false" in invalid_branch
    assert "return;" in invalid_branch


def test_distance_flows_through_both_legacy_and_root029_exact_target_paths() -> None:
    handler = _method(
        AGENT,
        "private void EjecutarNavegarAObjeto(",
        "private void EjecutarNavegarAPosicion(",
    )

    assert "TryResolverObjetivoAutorizado(contexto" in handler
    assert "movimiento.NavegarAObjetivoExacto(\n                contexto.IdAccion,\n                exacto,\n                distanciaParada" in handler
    assert "movimiento.NavegarAObjeto(\n                contexto.IdAccion,\n                objeto,\n                distanciaParada" in handler


def test_movement_controller_defends_against_invalid_direct_callers() -> None:
    legacy = _method(
        MOVEMENT,
        "public void NavegarAObjeto(",
        "public void NavegarAObjetivoExacto(",
    )
    exact = _method(
        MOVEMENT,
        "public void NavegarAObjetivoExacto(",
        "public void NavegarAPosicion(",
    )
    validator = _method(
        MOVEMENT,
        "private static bool EsDistanciaParadaValida(",
        "private void AplicarDistanciaParadaDeAccion(",
    )

    for method in (legacy, exact):
        assert method.index("EsDistanciaParadaValida") < method.index("PuedeNavegar")
        assert "RechazarMovimiento" in method
        assert "return;" in method
    assert "float.IsNaN(valor)" in validator
    assert "float.IsInfinity(valor)" in validator
    assert "valor >= 0.1f" in validator
    assert "valor <= 10.0f" in validator


def test_custom_distance_is_action_local_and_restored_on_every_stop_path() -> None:
    stop = _method(
        MOVEMENT,
        "private void DetenerMovimientoActual()",
        "private static bool EsDistanciaParadaValida(",
    )
    restore = _method(
        MOVEMENT,
        "private void RestaurarDistanciaParadaDeAccion()",
        "private void DetenerRutaNavMesh()",
    )

    assert "PreviousStoppingDistance" in MOVEMENT
    assert "RestaurarDistanciaParadaDeAccion();" in stop
    assert "navAgent.stoppingDistance = stoppingDistanceContext.PreviousStoppingDistance;" in restore
    assert "stoppingDistanceContext = null;" in restore


def test_cancellation_disable_and_supersession_restore_action_local_state() -> None:
    cancel = _method(MOVEMENT, "public void FrenarMovimiento()", "private bool PuedeNavegar")
    supersede = _method(
        MOVEMENT,
        "private void IniciarRutinaMovimiento(IEnumerator rutina)",
        "private IEnumerator NavegarHastaDestino(",
    )

    assert "StopCoroutine(rutinaMovimiento);" in cancel
    assert "DetenerMovimientoActual();" in cancel
    assert "void OnDisable()" in MOVEMENT
    assert "FrenarMovimiento();" in _method(
        MOVEMENT,
        "void OnDisable()",
        "public void NavegarAObjeto(",
    )
    assert supersede.index("StopCoroutine(rutinaMovimiento);") < supersede.index(
        "DetenerMovimientoActual();"
    ) < supersede.index("StartCoroutine(rutina)")


def test_v1_semantics_are_threshold_to_projected_destination_not_surface_claim() -> None:
    navigation = _method(
        MOVEMENT,
        "private IEnumerator NavegarHastaDestino(",
        "private IEnumerator GirarHastaAngulo(",
    )

    assert "objetivo.ClosestPoint(transform.position)" in MOVEMENT
    assert "NavMesh.SamplePosition(destino" in MOVEMENT
    assert "DistanciaHorizontal(transform.position, destino) > navAgent.stoppingDistance" in navigation


def test_root029_exact_target_resolution_does_not_reintroduce_name_fallback() -> None:
    resolver = _method(
        AGENT,
        "private bool TryResolverObjetivoAutorizado(",
        "private static void AgregarDetallesExtra(",
    )

    assert "exactTargetResolver.Resolve(authority.target)" in resolver
    assert "percepcion.BuscarObjeto" not in resolver
    assert "TryBuscarObjetoCercano" not in resolver
