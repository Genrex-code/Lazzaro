from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (ROOT / "unity_client" / name).read_text(encoding="utf-8")


def _method(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_unity_producer_has_stable_configured_identity_and_atomic_capture_pair() -> None:
    source = _source("PerceptionLifecycleContext.cs")
    begin_capture = _method(source, "public PerceptionCaptureContext BeginCapture()", "\n    }")

    assert '[SerializeField]' in source
    assert 'private string producerInstanceId = "";' in source
    assert 'producerEpochId = Guid.NewGuid().ToString("D")' in source
    assert "lock (captureLock)" in begin_capture
    assert "long generation = nextGeneration" in begin_capture
    assert 'producer_capture_id = Guid.NewGuid().ToString("D")' in begin_capture
    assert "DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()" in begin_capture
    assert "Scene.name" not in source


def test_unity_snapshot_carries_separate_producer_runtime_and_display_identity() -> None:
    source = _source("PerceptionSystem.cs")

    assert '"producer_instance_id"' in source
    assert '"producer_capture_id"' in source
    assert '"producer_object_id"' in source
    assert '"runtime_object_id"' in source
    assert '"display_name"' in source
    assert "GetInstanceID().ToString(CultureInfo.InvariantCulture)" in source


def test_exact_target_resolver_uses_only_exact_runtime_id_on_authority_path() -> None:
    source = _source("ExactTargetResolver.cs")
    resolve = _method(source, "public ExactTargetResolution Resolve", "private static ExactTargetResolution Failure")

    assert "runtimeId == reference.runtime_object_id" in resolve
    assert "HashSet<GameObject> exactObjects" in resolve
    assert "exactObjects.Add(candidate.gameObject)" in resolve
    assert "exactObjects.Count == 0" in resolve
    assert "exactObjects.Count != 1" in resolve
    assert "producerId != reference.producer_object_id" in resolve
    assert "producer_instance_id != lifecycle.ProducerInstanceId" in resolve
    assert "producer_epoch_id != lifecycle.ProducerEpochId" in resolve
    assert "logical_world_id != lifecycle.LogicalWorldId" in resolve
    assert "Contains(" not in resolve
    assert "IndexOf(" not in resolve
    assert "BuscarObjeto" not in resolve
    assert "Distance(" not in resolve
    assert "OrderBy" not in resolve


def test_exact_target_resolver_groups_multiple_colliders_by_game_object_identity() -> None:
    source = _source("ExactTargetResolver.cs")
    resolve = _method(source, "public ExactTargetResolution Resolve", "private static ExactTargetResolution Failure")

    assert "List<Collider> exact" not in resolve
    assert "HashSet<GameObject> exactObjects" in resolve
    assert "exactObjects.Add(candidate.gameObject) && target == null" in resolve
    assert "exactObjects.Count != 1" in resolve
    assert "InteractableMetadata metadata = target.GetComponent<InteractableMetadata>()" in resolve


def test_authority_bearing_agent_path_never_calls_legacy_name_lookup() -> None:
    source = _source("LazzaroAgent.cs")
    authority_method = _method(
        source,
        "private bool TryResolverObjetivoAutorizado",
        "private static void AgregarDetallesExtra",
    )

    assert "exactTargetResolver.Resolve(authority.target)" in authority_method
    assert "percepcion.BuscarObjeto" not in authority_method
    assert "TryBuscarObjetoCercano" not in authority_method
    assert "JToken.DeepEquals(boundParameters, contexto.Parametros)" in authority_method
    assert "authority.id_accion != contexto.IdAccion" in authority_method
    assert "authority.action_name != contexto.Nombre" in authority_method
    assert "authority.interaction_type != interactionType" in authority_method


def test_legacy_lookup_is_reachable_only_when_authority_reference_is_absent() -> None:
    source = _source("LazzaroAgent.cs")
    lookup = _method(
        source,
        "private bool TryBuscarObjetoCercano",
        "private bool TryResolverObjetivoAutorizado",
    )

    assert "if (contexto.PerceptionAuthority != null)" in lookup
    assert "TryResolverObjetivoAutorizado" in lookup
    assert "else" in lookup
    assert "percepcion.BuscarObjeto(nombreObjeto)" in lookup


def test_all_generation_binding_modes_fail_closed_when_unknown() -> None:
    source = _source("ExactTargetResolver.cs")

    assert 'case "REQUIRE_AUTHORIZED_GENERATION"' in source
    assert 'case "REJECT_IF_KNOWN_SUPERSEDED"' in source
    assert 'case "ALLOW_ADVANCE_WITH_EXACT_TARGET"' in source
    assert "default:\n                return false;" in source


def test_wire_reference_is_minimal_typed_and_does_not_embed_full_receipt() -> None:
    source = _source("DTOs.cs")

    assert "public PerceptionAuthorityReferenceV1 perception_authority;" in source
    assert "public string authority_decision_id;" in source
    assert "public TargetReferenceV1 target;" in source
    assert "public string action_binding_sha256;" in source
    assert "public string safety_disposition;" in source
    assert "PerceptionAuthorityReceiptV1" not in source


def test_unity_does_not_claim_to_recompute_or_validate_backend_sha_v1() -> None:
    source = _source("LazzaroAgent.cs")
    authority_method = _method(
        source,
        "private bool TryResolverObjetivoAutorizado",
        "private static void AgregarDetallesExtra",
    )

    assert "action_binding_sha256" in authority_method
    assert "ComputeHash" not in authority_method
    assert "SHA256" not in authority_method


def test_movement_controller_accepts_pre_resolved_collider_without_retargeting() -> None:
    source = _source("MovementController.cs")
    assert "Collider objetivo" in source
    assert "NavegarAObjetivoExacto" in source
    assert "AlejarseDeObjetivoExacto" in source
