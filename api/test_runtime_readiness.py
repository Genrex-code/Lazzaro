"""Contrato HTTP allowlisted de ROOT-023."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import router
from core.runtime_capabilities import RuntimeCapabilityHolder


def _client(holder: RuntimeCapabilityHolder | None = None) -> TestClient:
    app = FastAPI()
    app.state.orchestrator = None
    if holder is not None:
        app.state.runtime_capabilities = holder
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_001_liveness_is_independent_and_minimal() -> None:
    response = _client().get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"version": 1, "status": "LIVE"}


def test_002_startup_incomplete_readiness_is_503() -> None:
    response = _client(RuntimeCapabilityHolder()).get("/api/v1/health/ready")
    body = response.json()
    assert response.status_code == 503
    assert body["overall"] == "UNAVAILABLE"
    assert body["verification"] == "PARTIAL"


def test_003_ready_partial_is_200_and_strictly_allowlisted() -> None:
    holder = RuntimeCapabilityHolder()
    sink = holder.begin_generation()
    sink.mark_orchestration_available()
    response = _client(holder).get("/api/v1/health/ready")
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"version", "overall", "verification", "capabilities"}
    assert body["overall"] == "READY"
    assert body["verification"] == "PARTIAL"
    assert set(body["capabilities"]) == {
        "orchestration",
        "llm_generation",
        "llm_plan",
        "memory_read",
        "memory_write",
    }
    assert all(
        set(capability) == {"status", "reason_code"}
        for capability in body["capabilities"].values()
    )
    serialized = response.text.lower()
    for forbidden in ("provider", "model", "url", "prompt", "traceback", "secret"):
        assert forbidden not in serialized


def test_004_degraded_is_200_and_shutdown_is_503() -> None:
    holder = RuntimeCapabilityHolder()
    sink = holder.begin_generation()
    sink.mark_orchestration_available()
    sink.observe_llm_health_boolean(False)
    assert _client(holder).get("/api/v1/health/ready").status_code == 200
    holder.close_generation(sink.generation_id)
    assert _client(holder).get("/api/v1/health/ready").status_code == 503


def test_005_estado_remains_a_separate_domain_route() -> None:
    paths = _client(RuntimeCapabilityHolder()).app.openapi()["paths"]
    assert "/api/v1/estado" in paths
    assert "/api/v1/health/ready" in paths
    assert paths["/api/v1/estado"]["get"]["operationId"] != paths[
        "/api/v1/health/ready"
    ]["get"]["operationId"]
