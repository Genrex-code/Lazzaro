"""
core/orchestrator.py - Orquestador central de LAZZARO.
Coordina el flujo completo: evento -> estado interno -> memoria -> IA -> accion.
"""

from collections import OrderedDict, deque
from copy import deepcopy
from datetime import datetime, timezone
import logging
import threading
import time
from typing import List, Optional
import uuid

from ai_engine.ai_client import LLMCallAudit
from ai_engine.affordance_resolver import SafetyDisposition, SafetyReasonCode
from ai_engine.identity_projection import IdentityContextV1
from ai_engine.decision_outcome import (
    DecisionAttempt,
    DecisionDisposition,
    DecisionLayer,
    DecisionOutcome,
    DecisionQuality,
    DecisionReasonCode,
    MotorOutcomeStatus,
)
from ai_engine.learned_alias_candidates import build_learned_alias_candidate
from ai_engine.llm_generation_adapter import LLMGenerationAdapter
from ai_engine.llm_call_control import LLMCallBudgetV1, LLMCallResultV1
from ai_engine.knowledge_provenance import (
    CacheLookupStatusV1,
    KnowledgeProducerRouteV1,
    cache_receipt_for_lookup,
    cache_receipt_not_checked,
    local_deterministic_producer,
    provenance_for,
    provider_receipt_from_llm,
)
from ai_engine.motor_engine import MotorEngine
from ai_engine.motor_resolution import MotorActionKindV1
from ai_engine.motor_schemas import (
    MotorActionAdapterV1,
    MotorActionContractFailureV1,
    MotorActionV1,
)
from ai_engine.perception_contracts import (
    PerceptionIdentityReceiptV1,
    PerceptionParseReceiptV1,
    evaluate_perception_identity,
    parse_perception_payload,
)
from ai_engine.oracle_client import OracleClient
from ai_engine.oracle_engine import OracleEngine
from ai_engine.oracle_memory import OracleMemory
from ai_engine.persona_engine import PersonaEngine
from ai_engine.persona_factual_bridge import (
    PersonaFactualBoundaryV1,
    PersonaFactualBridgeV1,
    PersonaFactualRenderResultV1,
    PersonaFactualValidationStatusV1,
)
from ai_engine.schemas import ActionResponse, OracleResult
from ai_engine.structured_parse_types import StructuredParseStatus
from ai_engine.subjective_effort import SubjectiveEffortCalculator
from ai_engine.triage_contracts import TriageOutcomeV1
from ai_engine.triage_router import TriageRouter
from api.schemas import AccionSaliente, EstadoAgente, EventoEntrante, NotificacionSaliente
from core.action_ledger import (
    ActionLedger,
    ActionLedgerError,
    ActionRecord,
    ActionRecordStatus,
    ActionState,
    ActionTransitionEvent,
    OutcomeConsumptionStatus,
    OutcomeReplayStatus,
    OutcomeRecordResult,
    OutcomeRecordStatus,
    TransitionClassification,
    TransactionClaimStatus,
    TransactionCompletionStatus,
    TransactionConflictError,
    TransactionIncompleteError,
    TurnAbortedError,
    TurnEffectStatus,
    TurnProcessingError,
    TurnReconciliationRequiredError,
    TurnUoWStatus,
)
from core.audit_trace import AuditTrace, safe_preview
from core.audit_writer import AuditTraceJSONLWriter
from core.logger import get_logger
from core.identity_snapshot import IdentityComponentsV1, IdentitySnapshotProvider
from core.perception_authority import (
    AuthorityStatusV1,
    AuthorizedPhysicalActionV1,
    PerceptionAuthorityEvaluator,
    PerceptionAuthorityPolicyV1,
    PerceptionAuthorityReferenceV1,
    PhysicalActionCandidateV1,
    TargetReferenceV1,
    TrustedParameterPathV1,
    canonical_json,
    derive_parameter_provenance,
    physical_action_class,
)
from core.perception_freshness import (
    FreshnessPolicyV1,
    PerceptionFreshnessEvaluator,
    PerceptionFreshnessReceiptV1,
    PerceptionIngestionMetadataV1,
    SystemUtcClock,
)
from core.perception_lifecycle import (
    LifecycleStatusV1,
    PerceptionLifecycleReceiptV1,
    PerceptionLifecycleTracker,
)
from core.safe_error_logging import log_safe_boundary_failure
from core.material_attestation import (
    canonical_material_attestation,
    validate_material_attestation,
)
from core.operational_acknowledgement import manual_conversation_save_acknowledgement
from core.tool_policy import ToolPolicy
from core.transport_delivery import (
    DeliveryReconciliationResultV1,
    DeliveryResolutionV1,
)
from core.triage_boundary import (
    TriageBoundaryFailureV1,
    TriageEvaluationResultV1,
    triage_evaluation_failure,
    validate_triage_legacy_result,
)
from memory.memory_manager import ManualConversationSaveResult
from memory.memory_write_types import MemoryWriteReceipt, MemoryWriteStatus
from tools.executor import ActionExecutor

logger = get_logger(__name__)


class Orchestrator:
    """
    Punto central que une BehaviorCore, MemoryManager, ReasoningEngine y ToolRegistry.
    Expone metodos para procesar eventos y obtener el estado del agente.
    """

    MAX_TRANSACCIONES_CACHE = 2000
    _AUDIT_TIMING = threading.local()
    _PARAMETER_PROVENANCE = threading.local()

    def __init__(
        self,
        behavior_core,
        memory_manager,
        reasoning_engine,
        tool_registry,
        identity_provider: IdentitySnapshotProvider,
        audit_writer: AuditTraceJSONLWriter | None = None,
        action_ledger_path: str | None = None,
        perception_lifecycle_tracker: PerceptionLifecycleTracker | None = None,
        perception_freshness_policy: FreshnessPolicyV1 | None = None,
        perception_authority_policy: PerceptionAuthorityPolicyV1 | None = None,
        perception_clock: object | None = None,
    ):
        self.behavior = behavior_core
        self.memory = memory_manager
        self.reasoning = reasoning_engine
        self.tool_registry = tool_registry
        self.triage_router = TriageRouter()
        self.tool_policy = ToolPolicy()
        self.motor_engine = MotorEngine()
        self._conectar_aliases_aprendidos()
        if not isinstance(identity_provider, IdentitySnapshotProvider):
            raise TypeError("Orchestrator exige IdentitySnapshotProvider")
        self.identity_provider = identity_provider
        self.subjective_effort = SubjectiveEffortCalculator()
        self.persona_engine = PersonaEngine()
        self.persona_factual_bridge = PersonaFactualBridgeV1()
        self.perception_lifecycle_tracker = (
            perception_lifecycle_tracker or PerceptionLifecycleTracker()
        )
        self.perception_freshness_policy = perception_freshness_policy
        self.perception_authority_policy = perception_authority_policy
        self.perception_clock = perception_clock or SystemUtcClock()
        self.perception_freshness_evaluator = PerceptionFreshnessEvaluator()
        self.perception_authority_evaluator = PerceptionAuthorityEvaluator()
        self.llm_adapter = LLMGenerationAdapter(
            getattr(reasoning_engine, "ai_client", None),
            getattr(reasoning_engine, "policy_manager", None),
        )
        self.oracle_engine = OracleEngine()
        self._lifecycle_close_lock = threading.Lock()
        self._lifecycle_close_attempted = False
        oracle_db_path = getattr(getattr(memory_manager, "client", None), "db_path", "./lazzaro_data")
        self.oracle_memory = OracleMemory(db_path=str(oracle_db_path or "./lazzaro_data"))
        try:
            oracle_settings = getattr(getattr(reasoning_engine, "ai_client", None), "settings", None)
            self.oracle_client = OracleClient(settings=oracle_settings)
            self._transacciones_procesadas: OrderedDict[str, AccionSaliente] = OrderedDict()
            self._guardados_conversacionales_pendientes: OrderedDict[str, str] = OrderedDict()
            self._alias_candidates_pendientes: OrderedDict[str, dict] = OrderedDict()
            self._notificaciones: deque[NotificacionSaliente] = deque(maxlen=50)
            self._transacciones_cache_lock = threading.Lock()
            self._guardados_conversacionales_lock = threading.Lock()
            self._alias_candidates_lock = threading.Lock()
            self._notificaciones_lock = threading.Lock()
            self._transacciones_lock_guard = threading.Lock()
            self._transacciones_locks: dict[str, dict[str, object]] = {}
            self._descanso_terminal_lock = threading.RLock()
            self._descanso_terminal_pendiente: dict[str, str] | None = None
            self.audit_writer = (
                audit_writer if audit_writer is not None else AuditTraceJSONLWriter()
            )
            self.action_ledger = ActionLedger(action_ledger_path or ":memory:")
            logger.info("Orchestrator inicializado.")
        except BaseException:
            try:
                self.close()
            except BaseException:
                # close() registra el fallo secundario; conservar el error de construcción.
                pass
            raise

    def _conectar_aliases_aprendidos(self) -> None:
        proveedor = getattr(self.memory, "obtener_aliases_aprendidos", None)
        if callable(proveedor):
            self.motor_engine.fuzzy_resolver.learned_alias_provider = proveedor
        proveedor_afectivo = getattr(self.memory, "obtener_memorias_afectivas_espaciales", None)
        if callable(proveedor_afectivo):
            self.motor_engine.fuzzy_resolver.affective_memory_provider = proveedor_afectivo

    def procesar_evento(self, evento: EventoEntrante) -> Optional[AccionSaliente]:
        """Serializa únicamente retries solapados de la misma transacción."""
        transaction_id = str(evento.id_transaccion)
        trace_id = str(uuid.uuid4())
        self._begin_audit_timing()
        started_ns = time.perf_counter_ns()
        self._iniciar_captura_llm_audit()
        self._registrar_limite_audit(evento, trace_id, "event_start")

        try:
            guard = getattr(self, "_transacciones_lock_guard", None)
            locks = getattr(self, "_transacciones_locks", None)
            if guard is None or not isinstance(locks, dict):
                guard = threading.Lock()
                locks = {}
                self._transacciones_lock_guard = guard
                self._transacciones_locks = locks

            with guard:
                state = locks.get(transaction_id)
                if state is None:
                    state = {"lock": threading.RLock(), "users": 0}
                    locks[transaction_id] = state
                state["users"] = int(state.get("users", 0)) + 1
                transaction_lock = state["lock"]

            try:
                with transaction_lock:  # type: ignore[attr-defined]
                    return self._procesar_evento_serializado(evento, trace_id=trace_id)
            finally:
                with guard:
                    state["users"] = int(state.get("users", 1)) - 1
                    if state["users"] <= 0 and locks.get(transaction_id) is state:
                        locks.pop(transaction_id, None)
        except Exception as exc:
            failed_uow_status: TurnUoWStatus | None = None
            try:
                failed_uow_status = self._finalizar_uow_por_fallo(transaction_id, exc)
            except Exception as uow_error:
                log_safe_boundary_failure(
                    logger,
                    component="orchestrator.uow_failure_close",
                    code="TURN_PROCESSING_FAILED",
                    error=uow_error,
                )
            llm_events = self._consumir_llm_audit()
            self._registrar_errores_llm_audit(evento, trace_id, llm_events)
            if not bool(getattr(exc, "audit_recorded", False)):
                self._registrar_error_audit(
                    evento,
                    trace_id,
                    error_type=str(
                        getattr(exc, "failure_class", None) or type(exc).__name__
                    ),
                    component=str(
                        getattr(exc, "component", None) or "orchestrator"
                    ),
                    llm=self._resumir_llm_audit(llm_events),
                )
            if failed_uow_status in {
                TurnUoWStatus.PARTIAL,
                TurnUoWStatus.RECONCILIATION_REQUIRED,
            }:
                raise TurnReconciliationRequiredError(
                    "El turno requiere reconciliacion antes de cualquier replay"
                ) from exc
            if isinstance(exc, ActionLedgerError):
                raise
            if failed_uow_status is not None:
                raise TurnProcessingError("orchestrator", type(exc).__name__) from exc
            raise
        finally:
            self._consumir_llm_audit()
            try:
                self._registrar_limite_audit(
                    evento,
                    trace_id,
                    "event_end",
                    durations_ms={"total": self._elapsed_total_ms(started_ns)},
                )
            finally:
                self._end_audit_timing()

    def reconciliar_transaccion(
        self,
        *,
        transaction_id: str,
        origin: str,
        event_type: str,
        request_identity_version: int | None,
        request_fingerprint_sha256: str | None,
    ) -> tuple[DeliveryReconciliationResultV1, AccionSaliente | None]:
        """Consulta delivery durable sin procesar de nuevo ni mutar autoridades."""

        result = self._obtener_action_ledger().reconcile_delivery(
            transaction_id,
            origin=origin,
            event_type=event_type,
            request_identity_version=request_identity_version,
            request_fingerprint_sha256=request_fingerprint_sha256,
        )
        action = None
        if result.resolution is DeliveryResolutionV1.COMPLETED_WITH_ACTION:
            if result.action_id is not None:
                record = self._obtener_action_ledger().get_action(result.action_id)
                action = self._accion_desde_record(record) if record is not None else None
            else:
                action = self._restaurar_accion_desde_ledger(result.response_action)
            if action is not None:
                action = self._actualizar_snapshot_accion(action)
        return result, action

    @classmethod
    def _audit_timing_stack(cls) -> list[int]:
        stack = getattr(cls._AUDIT_TIMING, "stack", None)
        if not isinstance(stack, list):
            stack = []
            cls._AUDIT_TIMING.stack = stack
        return stack

    @classmethod
    def _begin_audit_timing(cls) -> None:
        cls._audit_timing_stack().append(0)

    @classmethod
    def _add_audit_overhead(cls, elapsed_ns: int) -> None:
        try:
            stack = cls._audit_timing_stack()
            if stack:
                stack[-1] += max(0, int(elapsed_ns))
        except Exception:
            pass

    @classmethod
    def _current_audit_overhead_ns(cls) -> int:
        try:
            stack = cls._audit_timing_stack()
            return max(0, int(stack[-1])) if stack else 0
        except Exception:
            return 0

    @classmethod
    def _end_audit_timing(cls) -> None:
        try:
            stack = cls._audit_timing_stack()
            if stack:
                stack.pop()
        except Exception:
            pass

    def _elapsed_total_ms(self, started_ns: int) -> float:
        finished_ns = time.perf_counter_ns()
        functional_ns = (
            finished_ns
            - started_ns
            - self._current_audit_overhead_ns()
        )
        return round(max(0, functional_ns) / 1_000_000, 3)

    def _write_audit_trace(self, trace: AuditTrace) -> bool:
        """Aísla el writer y descuenta su coste de la duración funcional total."""
        writer = getattr(self, "audit_writer", None)
        write = getattr(writer, "write", None)
        if not callable(write):
            return False

        started_ns = time.perf_counter_ns()
        try:
            return bool(write(trace))
        except Exception as exc:
            logger.warning(
                "No se pudo emitir AuditTrace %s; la ejecucion continuara. error_type=%s",
                trace.stage,
                type(exc).__name__,
            )
            return False
        finally:
            self._add_audit_overhead(time.perf_counter_ns() - started_ns)

    def _emit_audit_trace(self, *, stage: str, **kwargs) -> bool:
        """Construye y escribe un trace sin exponer el runtime a fallos del audit."""
        started_ns = time.perf_counter_ns()
        try:
            trace = AuditTrace(stage=stage, **kwargs)
        except Exception as exc:
            logger.warning(
                "No se pudo construir AuditTrace %s; la ejecucion continuara. error_type=%s",
                stage,
                type(exc).__name__,
            )
            return False
        finally:
            self._add_audit_overhead(time.perf_counter_ns() - started_ns)
        return self._write_audit_trace(trace)

    def _capturar_estado_tarea_audit(self) -> dict:
        """Copia el snapshot acotado del Core; observar nunca bloquea el turno."""
        started_ns = time.perf_counter_ns()
        try:
            snapshotter = getattr(
                getattr(self, "behavior", None), "obtener_snapshot_audit", None
            )
            if not callable(snapshotter):
                # Compatibilidad con componentes/fakes anteriores al gate.
                return {}
            snapshot = snapshotter()
            if not isinstance(snapshot, dict) or any(
                not isinstance(snapshot.get(key), dict) for key in ("state", "task")
            ):
                raise TypeError("Snapshot de estado/tarea invalido")
            # La serializacion/sanitizacion sigue perteneciendo a AuditTrace.
            # Copiar ahora evita que una mutacion posterior reescriba el pasado.
            captured = deepcopy({key: snapshot[key] for key in ("state", "task")})
            if "objetivo_id" in captured["task"]:
                # El objetivo estructurado también permanece acotado: no volcar
                # manuales, prompts ni secretos mediante el snapshot de task.
                captured["task"]["objetivo_id"] = safe_preview(
                    captured["task"]["objetivo_id"]
                )
            return captured
        except Exception as exc:
            try:
                logger.warning(
                    "No se pudo capturar estado/tarea de AuditTrace; "
                    "la ejecucion continuara. error_type=%s",
                    type(exc).__name__,
                )
            except Exception:
                pass
            # null es desconocido, no un estado vacio ni ausencia de tarea.
            return {"state": None, "task": None}
        finally:
            self._add_audit_overhead(time.perf_counter_ns() - started_ns)

    def _registrar_limite_audit(
        self,
        evento: EventoEntrante,
        trace_id: str,
        stage: str,
        durations_ms: dict | None = None,
    ) -> bool:
        """Emite los límites correlacionados de inicio/cierre del turno."""
        input_data = {}
        snapshot = self._capturar_estado_tarea_audit()
        if stage == "event_start":
            input_data = {
                "origin": evento.origen,
                "event_type": evento.tipo_evento,
                "message_preview": self._extraer_mensaje_usuario(evento),
            }

        return self._emit_audit_trace(
            stage=stage,
            trace_id=trace_id,
            id_transaccion=str(evento.id_transaccion),
            input=input_data,
            pre=snapshot if stage == "event_start" else {},
            post=snapshot if stage == "event_end" else {},
            durations_ms=durations_ms or {},
        )

    def _registrar_triage_audit(
        self,
        evento: EventoEntrante,
        trace_id: str | None,
        triage_snapshot: dict,
        durations_ms: dict | None = None,
    ) -> bool:
        """Registra la decision original y la ruta efectiva sin reevaluar Triage."""
        if not trace_id:
            return False

        return self._emit_audit_trace(
            stage="triage",
            trace_id=trace_id,
            id_transaccion=str(evento.id_transaccion),
            triage=triage_snapshot,
            durations_ms=durations_ms or {},
        )

    def _registrar_memory_audit(
        self,
        evento: EventoEntrante,
        trace_id: str | None,
        memory_snapshot: dict,
        durations_ms: dict | None = None,
    ) -> bool:
        """Registra solo la metadata v1 de las operaciones de memoria ya ejecutadas."""
        if not trace_id:
            return False

        try:
            memory_v1 = {
                "retrieved_ids": list(memory_snapshot.get("retrieved_ids") or []),
                "scores": list(memory_snapshot.get("scores") or []),
                "created_ids": list(memory_snapshot.get("created_ids") or []),
                "working_memory_count": int(
                    memory_snapshot.get("working_memory_count") or 0
                ),
            }
            return self._emit_audit_trace(
                stage="memory",
                trace_id=trace_id,
                id_transaccion=str(evento.id_transaccion),
                memory=memory_v1,
                durations_ms=durations_ms or {},
            )
        except Exception as exc:
            logger.warning(
                "No se pudo emitir AuditTrace memory; la ejecucion continuara. error_type=%s",
                type(exc).__name__,
            )
            return False

    def _registrar_decision_audit(
        self,
        evento: EventoEntrante,
        trace_id: str | None,
        *,
        layer: str,
        action: str,
        valid: bool,
        llm: dict | None = None,
        durations_ms: dict | None = None,
    ) -> bool:
        """Registra la capa responsable y la acción final posterior a guardarraíles."""
        if not trace_id:
            return False

        return self._emit_audit_trace(
            stage="decision",
            trace_id=trace_id,
            id_transaccion=str(evento.id_transaccion),
            decision={
                "layer": layer,
                "action": action,
                "valid": bool(valid),
            },
            llm=llm or {},
            durations_ms=durations_ms or {},
        )

    @staticmethod
    def _mapear_resultado_ejecucion_audit(resultado_memoria: str) -> str | None:
        """Traduce estados internos existentes sin afirmar ejecución no confirmada."""
        return {
            "sin_accion": "skipped",
            "pendiente_confirmacion": "pending",
            "ok": "ok",
            "error": "failed",
            "despachada": "pending",
        }.get(str(resultado_memoria or ""))

    def _registrar_execution_audit(
        self,
        evento: EventoEntrante,
        trace_id: str | None,
        resultado_memoria: str,
        *,
        error_type: str | None = None,
        durations_ms: dict | None = None,
    ) -> bool:
        """Observa el único retorno de _gestionar_ejecucion sin volver a ejecutar."""
        if not trace_id:
            return False

        status = self._mapear_resultado_ejecucion_audit(resultado_memoria)
        if status is None:
            return False

        return self._emit_audit_trace(
            stage="execution",
            trace_id=trace_id,
            id_transaccion=str(evento.id_transaccion),
            execution={
                "status": status,
                "error_type": error_type,
            },
            durations_ms=durations_ms or {},
        )

    @staticmethod
    def _elapsed_ms(started_ns: int, finished_ns: int | None = None) -> float:
        if finished_ns is None:
            finished_ns = time.perf_counter_ns()
        return round(max(0, finished_ns - started_ns) / 1_000_000, 3)

    def _llm_audit_clients(self) -> list[object]:
        reasoning = getattr(self, "reasoning", None)
        candidates = [
            getattr(reasoning, "ai_client", None),
            getattr(self, "oracle_client", None),
        ]
        clients: list[object] = []
        seen: set[int] = set()
        for client in candidates:
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            clients.append(client)
        return clients

    def _iniciar_captura_llm_audit(self) -> None:
        started_ns = time.perf_counter_ns()
        try:
            for client in self._llm_audit_clients():
                begin = getattr(client, "begin_audit_capture", None)
                if not callable(begin):
                    continue
                try:
                    begin()
                except Exception as exc:
                    logger.warning(
                        "No se pudo iniciar la captura LLM de AuditTrace; el turno continuara. error_type=%s",
                        type(exc).__name__,
                    )
        except Exception as exc:
            logger.warning(
                "No se pudo preparar la captura LLM de AuditTrace; el turno continuara. error_type=%s",
                type(exc).__name__,
            )
        finally:
            self._add_audit_overhead(time.perf_counter_ns() - started_ns)

    def _consumir_llm_audit(self) -> list[LLMCallAudit]:
        started_ns = time.perf_counter_ns()
        events: list[LLMCallAudit] = []
        try:
            for client in self._llm_audit_clients():
                consume = getattr(client, "consume_audit_events", None)
                if not callable(consume):
                    continue
                try:
                    captured = consume()
                except Exception as exc:
                    logger.warning(
                        "No se pudo consumir la captura LLM de AuditTrace; el turno continuara. error_type=%s",
                        type(exc).__name__,
                    )
                    continue
                if isinstance(captured, list):
                    events.extend(
                        event for event in captured if isinstance(event, LLMCallAudit)
                    )
            return sorted(events, key=lambda event: event.started_ns)
        except Exception as exc:
            logger.warning(
                "No se pudo preparar el consumo LLM de AuditTrace; el turno continuara. error_type=%s",
                type(exc).__name__,
            )
            return events
        finally:
            self._add_audit_overhead(time.perf_counter_ns() - started_ns)

    @staticmethod
    def _resumir_llm_audit(
        events: list[LLMCallAudit],
        *,
        fallback_used: bool = False,
    ) -> dict:
        successful = [event for event in events if event.status == "ok"]
        selected = successful[-1] if successful else None
        return {
            "provider": selected.provider if selected else None,
            "model_id": selected.model_id if selected else None,
            "fallback_used": bool(
                fallback_used
                or any(event.fallback_used for event in events)
                or (events and not successful)
            ),
        }

    def _registrar_error_audit(
        self,
        evento: EventoEntrante,
        trace_id: str | None,
        *,
        error_type: str,
        component: str,
        llm: dict | None = None,
        durations_ms: dict | None = None,
    ) -> bool:
        """Registra únicamente tipo/componente estables; nunca mensajes libres."""
        if not trace_id:
            return False

        return self._emit_audit_trace(
            stage="error",
            trace_id=trace_id,
            id_transaccion=str(evento.id_transaccion),
            llm=llm or {},
            durations_ms=durations_ms or {},
            error={
                "type": str(error_type or "Error"),
                "component": str(component or "orchestrator"),
            },
        )

    def _registrar_errores_llm_audit(
        self,
        evento: EventoEntrante,
        trace_id: str | None,
        events: list[LLMCallAudit],
    ) -> int:
        emitted = 0
        for event in events:
            if event.status == "ok":
                continue
            if self._registrar_error_audit(
                evento,
                trace_id,
                error_type=event.error_type or "LLMError",
                component="llm",
                llm={
                    "provider": event.provider,
                    "model_id": event.model_id,
                    "fallback_used": event.fallback_used,
                },
                durations_ms={"llm": event.duration_ms},
            ):
                emitted += 1
        return emitted

    def _procesar_evento_serializado(
        self,
        evento: EventoEntrante,
        trace_id: str | None = None,
    ) -> Optional[AccionSaliente]:
        """
        Procesa un evento externo y devuelve la accion que el agente decide ejecutar.
        Si el evento ya fue procesado, devuelve la accion almacenada.
        """
        ledger = self._obtener_action_ledger()
        claim_turn = getattr(ledger, "claim_turn_transaction", None)
        atomic_turn_claim = callable(claim_turn)
        identity_kwargs = (
            {
                "request_identity_version": evento.request_identity_version,
                "request_fingerprint_sha256": evento.request_fingerprint_sha256,
            }
            if evento.request_identity_version is not None
            else {}
        )
        claim = (
            claim_turn(
                str(evento.id_transaccion),
                origin=str(evento.origen),
                event_type=str(evento.tipo_evento),
                **identity_kwargs,
            )
            if atomic_turn_claim
            else ledger.claim_transaction(
                str(evento.id_transaccion),
                origin=str(evento.origen),
                event_type=str(evento.tipo_evento),
                **identity_kwargs,
            )
        )
        if claim.status == TransactionClaimStatus.CONFLICT:
            raise TransactionConflictError(
                f"id_transaccion reutilizado con otro contrato: {evento.id_transaccion}"
            )
        if claim.status == TransactionClaimStatus.UNVERIFIABLE:
            raise TurnReconciliationRequiredError(
                f"Identidad de request no verificable: {evento.id_transaccion}"
            )
        if claim.status == TransactionClaimStatus.INCOMPLETE:
            get_uow = getattr(ledger, "get_turn_uow", None)
            uow = get_uow(str(evento.id_transaccion)) if callable(get_uow) else None
            if uow is not None:
                if uow.status == TurnUoWStatus.ABORTED_CLEAN:
                    raise TurnAbortedError(uow.failure_class or "TurnAborted")
                if uow.status in {
                    TurnUoWStatus.PARTIAL,
                    TurnUoWStatus.RECONCILIATION_REQUIRED,
                }:
                    raise TurnReconciliationRequiredError(
                        f"Turno requiere reconciliacion: {evento.id_transaccion}"
                    )
                if uow.status == TurnUoWStatus.OPEN:
                    ledger.mark_abandoned_turn_uow(str(evento.id_transaccion))
                    raise TurnReconciliationRequiredError(
                        f"UoW OPEN abandonado; replay bloqueado: {evento.id_transaccion}"
                    )
            raise TransactionIncompleteError(
                f"Transaccion durable incompleta; replay bloqueado: {evento.id_transaccion}"
            )
        if claim.status == TransactionClaimStatus.COMPLETED:
            if evento.tipo_evento in {
                "confirmacion_accion",
                "inicio_ejecucion_accion",
            }:
                self._validar_replay_lifecycle(evento)
                return None
            if evento.tipo_evento == "resultado_accion":
                self._validar_replay_outcome(evento)
                return None
            cacheada = self._obtener_transaccion_cacheada(evento.id_transaccion)
            if cacheada is not None:
                cacheada = self._actualizar_snapshot_accion(cacheada)
                logger.info(
                    "Evento %s ya completado en ledger; devolviendo accion cacheada.",
                    evento.id_transaccion,
                )
                return cacheada
            restaurada = self._restaurar_accion_desde_ledger(claim.transaction.response_action)
            if restaurada is not None:
                self._cachear_transaccion(evento.id_transaccion, restaurada)
            logger.info(
                "Evento %s ya completado en ledger; devolviendo respuesta durable.",
                evento.id_transaccion,
            )
            return restaurada

        begin_uow = getattr(ledger, "begin_turn_uow", None)
        if not atomic_turn_claim and callable(begin_uow):
            begin_uow(str(evento.id_transaccion))

        cacheada = self._obtener_transaccion_cacheada(evento.id_transaccion)
        if cacheada is not None:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "decision:action_identity",
                effect_kind="decision",
                phase="decision",
                required=True,
            )
            try:
                self._persistir_identidad_accion(
                    evento,
                    cacheada,
                    task_instance_id=None,
                    decision_valid=None,
                )
            except Exception:
                self._cerrar_efecto_uow(
                    evento.id_transaccion,
                    "decision:action_identity",
                    TurnEffectStatus.FAILED,
                )
                raise
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:action_identity",
                TurnEffectStatus.APPLIED,
            )
            self._completar_transaccion_ledger(evento.id_transaccion, cacheada)
            logger.info(
                "Evento %s migrado desde cache RAM al ledger.", evento.id_transaccion
            )
            return cacheada

        if evento.tipo_evento == "confirmacion_accion":
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "execution:lifecycle_confirmation",
                effect_kind="execution",
                phase="execution",
                required=True,
            )
            respuesta_confirmacion = self._procesar_confirmacion_accion(evento)
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "execution:lifecycle_confirmation",
                TurnEffectStatus.APPLIED,
            )
            self._completar_transaccion_ledger(evento.id_transaccion, None)
            return respuesta_confirmacion
        if evento.tipo_evento == "inicio_ejecucion_accion":
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "execution:lifecycle_start",
                effect_kind="execution",
                phase="execution",
                required=True,
            )
            self._procesar_inicio_ejecucion_accion(evento)
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "execution:lifecycle_start",
                TurnEffectStatus.APPLIED,
            )
            self._completar_transaccion_ledger(evento.id_transaccion, None)
            return None

        outcome_action: ActionRecord | None = None
        outcome_action_id: str | None = None
        if evento.tipo_evento == "resultado_accion":
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "outcome:record",
                effect_kind="outcome",
                phase="outcome",
                required=True,
            )
            outcome_receipt = self._registrar_outcome_entrante(evento)
            if outcome_receipt.status != OutcomeRecordStatus.ACCEPTED:
                self._cerrar_efecto_uow(
                    evento.id_transaccion,
                    "outcome:record",
                    TurnEffectStatus.SKIPPED,
                )
                self._completar_transaccion_ledger(evento.id_transaccion, None)
                logger.warning(
                    "Outcome ignorado sin mutaciones. transaction_id=%s status=%s",
                    evento.id_transaccion,
                    outcome_receipt.status.value,
                )
                return None
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "outcome:record",
                TurnEffectStatus.APPLIED,
            )
            outcome_action = outcome_receipt.action
            if outcome_action is None:
                raise ActionLedgerError("Outcome aceptado sin identidad de accion")
            outcome_action_id = outcome_action.action_id
            evento = self._correlacionar_evento_outcome(evento, outcome_action)

        retry_guardado = self._reintentar_guardado_conversacional_pendiente(evento)
        if retry_guardado is not None:
            return retry_guardado

        self._iniciar_efecto_uow(
            evento.id_transaccion,
            "event:terminal_rest_wake",
            effect_kind="event",
            phase="event",
            required=False,
        )
        rest_wake = self._consumir_descanso_terminal_si_corresponde(evento)
        self._cerrar_efecto_uow(
            evento.id_transaccion,
            "event:terminal_rest_wake",
            TurnEffectStatus.APPLIED
            if rest_wake is True
            else TurnEffectStatus.FAILED
            if rest_wake is False
            else TurnEffectStatus.SKIPPED,
        )

        evento = self._adjuntar_ingreso_perceptivo(evento)

        behavior_started_ns = time.perf_counter_ns()
        behavior_effect_key = (
            "outcome:behavior_task"
            if outcome_action is not None
            else "event:behavior"
        )
        self._iniciar_efecto_uow(
            evento.id_transaccion,
            behavior_effect_key,
            effect_kind="outcome" if outcome_action is not None else "event",
            phase="outcome" if outcome_action is not None else "event",
            required=True,
        )
        try:
            respuesta_core = self.behavior.procesar_evento(
                tipo_evento=evento.tipo_evento,
                detalles=evento.detalles if evento.detalles else {},
            )
            turn_identity_components = getattr(
                respuesta_core,
                "identity_components",
                None,
            )
            if (
                turn_identity_components is not None
                and not isinstance(turn_identity_components, IdentityComponentsV1)
            ):
                raise TypeError(
                    "BehaviorCore devolvio identity_components fuera de contrato"
                )
        except Exception as e:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                behavior_effect_key,
                TurnEffectStatus.ROLLED_BACK,
            )
            log_safe_boundary_failure(
                logger,
                component="orchestrator.behavior_core",
                code="TURN_PROCESSING_FAILED",
                error=e,
            )
            self._registrar_error_audit(
                evento,
                trace_id,
                error_type=type(e).__name__,
                component="behavior_core",
                durations_ms={"behavior_core": self._elapsed_ms(behavior_started_ns)},
            )
            failure = TurnProcessingError("behavior_core", type(e).__name__)
            failure.audit_recorded = True
            raise failure from e
        self._cerrar_efecto_uow(
            evento.id_transaccion,
            behavior_effect_key,
            TurnEffectStatus.APPLIED,
        )

        estado_actual = respuesta_core.get("estado", {})
        narrativa = respuesta_core.get("narrativa", "")
        contexto_llm = respuesta_core.get("contexto_llm", {})
        tarea = respuesta_core.get("tarea")
        task_transition = respuesta_core.get("task_transition")
        task_action_instance_id = str(
            respuesta_core.get("task_action_instance_id") or ""
        ).strip() or None
        politica = respuesta_core.get("policy_activa", "exploradora")
        es_interrupcion_critica = bool(respuesta_core.get("es_interrupcion_critica"))
        tipo_memoria = str(respuesta_core.get("tipo_memoria", evento.tipo_evento))
        percepcion_resumida = str(respuesta_core.get("percepcion_resumida", ""))

        triage_started_ns = time.perf_counter_ns()
        triage_result, triage_original = self._evaluar_triage(evento, estado_actual)
        if isinstance(triage_result, TriageBoundaryFailureV1):
            triage_duration_ms = self._elapsed_ms(triage_started_ns)
            self._registrar_triage_audit(
                evento,
                trace_id,
                {
                    "intent": None,
                    "route": None,
                    "effective_route": None,
                    "confidence": None,
                    "reason": None,
                },
                durations_ms={"triage": triage_duration_ms},
            )
            raise TurnProcessingError("triage", triage_result.code.value)

        if triage_result.outcome is TriageOutcomeV1.FAILED:
            triage_duration_ms = self._elapsed_ms(triage_started_ns)
            self._registrar_triage_audit(
                evento,
                trace_id,
                {
                    "intent": None,
                    "route": None,
                    "effective_route": None,
                    "confidence": triage_original.get("confidence"),
                    "reason": triage_result.human_reason,
                },
                durations_ms={"triage": triage_duration_ms},
            )
            raise TurnProcessingError("triage", triage_result.reason_code.value)

        triage_snapshot = {
            "intent": triage_original.get("intent"),
            "route": triage_original.get("route"),
            "confidence": triage_original.get("confidence"),
            "reason": triage_original.get("reason"),
        }
        triage_decision = self._adaptar_triage_para_terminal(evento, triage_original)
        triage_snapshot["effective_route"] = triage_decision.get("route")
        triage_duration_ms = self._elapsed_ms(triage_started_ns)
        self._registrar_triage_audit(
            evento,
            trace_id,
            triage_snapshot,
            durations_ms={"triage": triage_duration_ms},
        )
        memory_started_ns = time.perf_counter_ns()
        memory_snapshot = {
            "retrieved_ids": [],
            "scores": [],
            "created_ids": [],
            "working_memory_count": 0,
        }
        solicita_guardado_conversacional = self._triage_es_guardado_conversacional(
            triage_decision
        )
        solicita_decision = self._evento_solicita_decision(evento, es_interrupcion_critica)

        memoria_texto_completa = ""
        if not solicita_guardado_conversacional and self._evento_requiere_contexto_memoria(
            triage_decision,
            solicita_decision,
            es_interrupcion_critica,
        ):
            memoria_texto_completa = self._obtener_contexto_memoria_completa(
                evento,
                narrativa,
                percepcion_resumida,
                tarea,
                memory_observation=memory_snapshot,
            )

        self._iniciar_efecto_uow(
            evento.id_transaccion,
            "memory:event_episode",
            effect_kind="event",
            phase="memory",
            required=False,
        )
        memory_write_result = self._guardar_evento_en_memoria(
            evento=evento,
            tipo_memoria=tipo_memoria,
            narrativa=narrativa,
            percepcion_resumida=percepcion_resumida,
            estado_actual=estado_actual,
            es_interrupcion_critica=es_interrupcion_critica,
        )
        if isinstance(memory_write_result, MemoryWriteReceipt):
            persisted = (
                memory_write_result.durable
                and memory_write_result.status
                in {MemoryWriteStatus.CREATED, MemoryWriteStatus.ALREADY_EXISTS}
            )
            newly_created = (
                persisted
                and memory_write_result.status is MemoryWriteStatus.CREATED
                and memory_write_result.changed_count > 0
                and bool(memory_write_result.target_id)
            )
            if newly_created:
                memory_snapshot["created_ids"] = [str(memory_write_result.target_id)]
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "memory:event_episode",
                TurnEffectStatus.APPLIED if persisted else (
                    TurnEffectStatus.AMBIGUOUS
                    if memory_write_result.status is MemoryWriteStatus.AMBIGUOUS
                    else TurnEffectStatus.FAILED
                ),
            )
        elif memory_write_result:
            memory_snapshot["created_ids"] = [str(memory_write_result)]
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "memory:event_episode",
                TurnEffectStatus.APPLIED,
            )
        else:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "memory:event_episode",
                TurnEffectStatus.FAILED,
            )
        memory_snapshot["working_memory_count"] = (
            self._obtener_cantidad_memoria_trabajo()
        )
        memory_duration_ms = self._elapsed_ms(memory_started_ns)
        self._registrar_memory_audit(
            evento,
            trace_id,
            memory_snapshot,
            durations_ms={"memory": memory_duration_ms},
        )

        decision_started_ns = time.perf_counter_ns()
        herramientas: list[str] = []
        herramientas_detalle: dict[str, str] = {}
        if (
            solicita_decision
            and not solicita_guardado_conversacional
            and not self._triage_debe_cortar_flujo(triage_decision)
        ):
            herramientas = self._herramientas_para_evento(evento, triage_decision)
            herramientas_detalle = (
                self.tool_registry.describir_herramientas(herramientas) if self.tool_registry else {}
            )

        decision_layer = "none"
        trusted_parameter_path = TrustedParameterPathV1.UNKNOWN
        decision_observation: dict[str, object] = {}
        identity_context: IdentityContextV1 | None = None
        self._iniciar_efecto_uow(
            evento.id_transaccion,
            "decision:layer",
            effect_kind="bookkeeping",
            phase="decision",
            required=True,
        )
        try:
            if (
                solicita_decision
                and not solicita_guardado_conversacional
                and not self._triage_debe_cortar_flujo(triage_decision)
                and not self._es_resultado_hablar(evento)
            ):
                identity_context = self._identity_context_for_turn(
                    turn_identity_components
                )
            if es_interrupcion_critica:
                action_response = self._resolver_interrupcion_critica(evento)
                trusted_parameter_path = TrustedParameterPathV1.CRITICAL_BODY_EVENT
            elif self._usuario_pide_cancelar_tarea(evento):
                action_response = self._resolver_cancelacion_tarea_usuario(
                    evento,
                    task_transition=task_transition,
                )
                trusted_parameter_path = TrustedParameterPathV1.ORCHESTRATOR_CONTROL
            elif solicita_guardado_conversacional:
                self._iniciar_efecto_uow(
                    evento.id_transaccion,
                    "decision:manual_memory_save",
                    effect_kind="decision",
                    phase="decision",
                    required=True,
                )
                action_response = self._resolver_guardado_conversacional(evento)
                trusted_parameter_path = TrustedParameterPathV1.ORCHESTRATOR_CONTROL
                self._cerrar_efecto_uow(
                    evento.id_transaccion,
                    "decision:manual_memory_save",
                    TurnEffectStatus.FAILED
                    if self._es_error_guardado_conversacional(action_response)
                    else TurnEffectStatus.APPLIED,
                )
            elif self._triage_debe_cortar_flujo(triage_decision):
                razon_triage = str(
                    triage_decision.get("reason")
                    or "Evento filtrado por Triage Router."
                )
                action_response = self._accion_pasiva(razon_triage)
                trusted_parameter_path = TrustedParameterPathV1.ORCHESTRATOR_CONTROL
            elif self._es_resultado_hablar(evento):
                action_response = self._accion_pasiva()
                trusted_parameter_path = TrustedParameterPathV1.ORCHESTRATOR_CONTROL
            elif not solicita_decision:
                action_response = self._accion_pasiva()
                trusted_parameter_path = TrustedParameterPathV1.ORCHESTRATOR_CONTROL
            elif self._triage_es_motor(triage_decision):
                decision_layer = "motor"
                self._record_trusted_parameter_path(TrustedParameterPathV1.UNKNOWN)
                action_response = self._resolver_ruta_motor(
                    evento=evento,
                    triage_decision=triage_decision,
                    contexto_llm=contexto_llm,
                    memoria_texto=memoria_texto_completa,
                    herramientas=herramientas,
                    herramientas_detalle=herramientas_detalle,
                    politica=politica,
                    tarea=tarea,
                    estado_actual=estado_actual,
                    audit_observation=decision_observation,
                    identity_context=identity_context,
                )
                trusted_parameter_path = self._consume_trusted_parameter_path()
            elif self._triage_es_persona(triage_decision):
                decision_layer = "persona"
                action_response = self._resolver_ruta_persona(
                    evento=evento,
                    contexto_llm=contexto_llm,
                    memoria_texto=memoria_texto_completa,
                    politica=politica,
                    audit_observation=decision_observation,
                    identity_context=identity_context,
                )
            elif self._triage_es_oracle(triage_decision):
                decision_layer = "oracle"
                action_response = self._resolver_ruta_oracle(
                    evento=evento,
                    contexto_llm=contexto_llm,
                    memoria_texto=memoria_texto_completa,
                    politica=politica,
                    audit_observation=decision_observation,
                    identity_context=identity_context,
                )
            else:
                decision_layer = "reasoning"
                action_response = self.reasoning.process(
                    contexto_llm=contexto_llm,
                    memoria_texto=memoria_texto_completa,
                    herramientas=herramientas,
                    herramientas_detalle=herramientas_detalle,
                    politica=politica,
                    tarea=tarea,
                    audit_observation=decision_observation,
                    identity_context=identity_context,
                    current_request=self._current_user_request(evento),
                )
        except Exception as e:
            decision_observation["fallback_used"] = True
            decision_observation.setdefault("decision_error_type", type(e).__name__)
            decision_observation.setdefault("decision_error_component", decision_layer or "decision")
            decision_observation.setdefault("decision_error_observed_ns", time.perf_counter_ns())
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:layer",
                TurnEffectStatus.FAILED,
            )
            log_safe_boundary_failure(
                logger,
                component="orchestrator.decision",
                code="TURN_PROCESSING_FAILED",
                error=e,
            )
            raise TurnProcessingError(decision_layer or "decision", type(e).__name__) from e

        action_response = self._asegurar_decision_outcome(
            action_response,
            decision_layer=decision_layer,
        )
        action_response = self._sanitizar_action_response(action_response)
        if self._es_error_guardado_conversacional(action_response):
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:layer",
                TurnEffectStatus.FAILED,
            )
            raise TurnProcessingError("memory", "ManualConversationSaveFailed")
        if not action_response.valido:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:layer",
                TurnEffectStatus.FAILED,
            )
            raise TurnProcessingError(
                str(decision_observation.get("decision_error_component") or "decision"),
                str(decision_observation.get("decision_error_type") or "InvalidDecision"),
            )
        action_response = self._blindar_salida_terminal(evento, action_response)
        action_response = self._adjuntar_esfuerzo_subjetivo(
            action_response,
            identity_context=identity_context,
        )
        action_response, canonical_motor_action = (
            self._canonicalizar_action_response_motor(action_response)
        )
        if not action_response.valido:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:layer",
                TurnEffectStatus.FAILED,
            )
            raise TurnProcessingError("motor_contract", "InvalidMotorStructure")
        self._cerrar_efecto_uow(
            evento.id_transaccion,
            "decision:layer",
            TurnEffectStatus.APPLIED,
        )
        action_id = str(uuid.uuid4())
        action_response, authority_reference = self._aplicar_autoridad_perceptiva(
            evento,
            action_response,
            action_id=action_id,
            trusted_parameter_path=trusted_parameter_path,
            canonical_motor_action=canonical_motor_action,
        )
        action_response, outgoing_motor_action = (
            self._canonicalizar_action_response_motor(action_response)
        )
        if not action_response.valido:
            raise TurnProcessingError("motor_contract", "InvalidGovernedMotorStructure")
        accion_saliente = self._a_accion_saliente(
            action_response,
            politica,
            action_id=action_id,
            perception_authority=authority_reference,
            canonical_motor_action=outgoing_motor_action,
        )
        if outcome_action is None:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "decision:action_identity",
                effect_kind="decision",
                phase="decision",
                required=True,
            )
            try:
                self._persistir_identidad_accion(
                    evento,
                    accion_saliente,
                    task_instance_id=task_action_instance_id,
                    decision_valid=action_response.valido,
                )
            except Exception:
                self._cerrar_efecto_uow(
                    evento.id_transaccion,
                    "decision:action_identity",
                    TurnEffectStatus.FAILED,
                )
                raise
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:action_identity",
                TurnEffectStatus.APPLIED,
            )
        decision_duration_ms = self._elapsed_ms(decision_started_ns)
        llm_events = self._consumir_llm_audit()
        failed_llm_events = [event for event in llm_events if event.status != "ok"]
        successful_llm_events = [event for event in llm_events if event.status == "ok"]
        self._registrar_errores_llm_audit(evento, trace_id, llm_events)
        llm_summary = self._resumir_llm_audit(
            llm_events,
            fallback_used=bool(
                decision_observation.get("oracle_source") in {"fallback", "error"}
                or decision_observation.get("fallback_used")
            ),
        )
        decision_error_type = str(
            decision_observation.get("decision_error_type") or ""
        ).strip()
        decision_error_component = str(
            decision_observation.get("decision_error_component") or "decision"
        ).strip()
        try:
            decision_error_observed_ns = int(
                decision_observation.get("decision_error_observed_ns") or 0
            )
        except (TypeError, ValueError):
            decision_error_observed_ns = 0
        decision_error_events = (
            [
                event
                for event in llm_events
                if event.started_ns <= decision_error_observed_ns
            ]
            if decision_error_observed_ns
            else llm_events
        )
        decision_error_llm = self._resumir_llm_audit(
            decision_error_events,
            fallback_used=bool(decision_observation.get("fallback_used")),
        )
        observed_failure_types = {
            str(event.error_type)
            for event in failed_llm_events
            if event.error_type
        }
        decision_error_is_derived_from_total_failure = bool(
            failed_llm_events
            and not successful_llm_events
            and decision_error_type in {"EmptyResponse", "InvalidLLMResponse"}
        )
        if (
            decision_error_type
            and not decision_error_is_derived_from_total_failure
            and (
                successful_llm_events
                or not llm_events
                or decision_error_type not in observed_failure_types
            )
        ):
            self._registrar_error_audit(
                evento,
                trace_id,
                error_type=decision_error_type,
                component=decision_error_component,
                llm=(
                    decision_error_llm
                    if decision_error_component == "llm"
                    else {}
                ),
            )
        elif action_response.error and not failed_llm_events:
            self._registrar_error_audit(
                evento,
                trace_id,
                error_type="DecisionError",
                component="decision",
            )
        decision_durations = {"decision": decision_duration_ms}
        if llm_events:
            decision_durations["llm"] = round(
                sum(event.duration_ms for event in llm_events),
                3,
            )
        self._registrar_decision_audit(
            evento,
            trace_id,
            layer=decision_layer,
            action=accion_saliente.nombre,
            valid=action_response.valido,
            llm=llm_summary,
            durations_ms=decision_durations,
        )
        self._iniciar_efecto_uow(
            evento.id_transaccion,
            "decision:alias_candidate",
            effect_kind="decision",
            phase="decision",
            required=False,
        )
        try:
            alias_candidate_created = self._registrar_alias_candidato_pendiente(
                action_response, accion_saliente
            )
        except Exception:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:alias_candidate",
                TurnEffectStatus.AMBIGUOUS,
            )
        else:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:alias_candidate",
                TurnEffectStatus.APPLIED
                if alias_candidate_created
                else TurnEffectStatus.SKIPPED,
            )

        execution_started_ns = time.perf_counter_ns()
        execution_effect_key = (
            "execution:local_tool"
            if self._debe_ejecutar_localmente(evento)
            else "execution:dispatch"
        )
        execution_effect_started = accion_saliente.nombre != "none"
        if execution_effect_started:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                execution_effect_key,
                effect_kind="execution",
                phase="execution",
                required=True,
            )
        resultado_memoria, detalles_resultado = self._gestionar_ejecucion(
            evento,
            accion_saliente,
            action_response=action_response,
        )
        if execution_effect_started:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                execution_effect_key,
                TurnEffectStatus.APPLIED,
            )
        accion_saliente = self._actualizar_snapshot_accion(accion_saliente)
        sandbox_outcome_action_id: str | None = None
        if (
            outcome_action is None
            and self._debe_ejecutar_localmente(evento)
            and accion_saliente.nombre != "none"
            and resultado_memoria in {"ok", "error"}
        ):
            sandbox_outcome_action_id = accion_saliente.id_accion
            sandbox_receipt = ledger.record_outcome(
                action_id=accion_saliente.id_accion,
                outcome_transaction_id=f"sandbox:{accion_saliente.id_accion}",
                success=resultado_memoria == "ok",
                action_name=accion_saliente.nombre,
                payload=self._payload_outcome_minimo(detalles_resultado or {}),
            )
            if sandbox_receipt.status != OutcomeRecordStatus.ACCEPTED:
                raise ActionLedgerError(
                    f"Outcome sandbox no aceptado: {sandbox_receipt.status.value}"
                )
            if sandbox_receipt.action is None:
                raise ActionLedgerError("Outcome sandbox aceptado sin action")
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "outcome:sandbox_behavior_task",
                effect_kind="outcome",
                phase="outcome",
                required=True,
            )
            try:
                self._aplicar_outcome_local_a_behavior(
                    sandbox_receipt.action,
                    resultado_memoria == "ok",
                    detalles_resultado or {},
                )
            except Exception:
                self._cerrar_efecto_uow(
                    evento.id_transaccion,
                    "outcome:sandbox_behavior_task",
                    TurnEffectStatus.ROLLED_BACK,
                )
                raise
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "outcome:sandbox_behavior_task",
                TurnEffectStatus.APPLIED,
            )
            accion_saliente = self._actualizar_snapshot_accion(accion_saliente)
        execution_duration_ms = self._elapsed_ms(execution_started_ns)
        execution_error_type = (
            "ExecutionFailed" if resultado_memoria == "error" else None
        )
        self._registrar_execution_audit(
            evento,
            trace_id,
            resultado_memoria,
            error_type=execution_error_type,
            durations_ms={"execution": execution_duration_ms},
        )
        if execution_error_type:
            self._registrar_error_audit(
                evento,
                trace_id,
                error_type=execution_error_type,
                component="execution",
                durations_ms={"execution": execution_duration_ms},
            )
        if resultado_memoria == "ok" and outcome_action is None:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "outcome:alias",
                effect_kind="outcome",
                phase="outcome",
                required=False,
            )
            alias_applied = self._aprobar_alias_pendiente_por_id(accion_saliente.id_accion)
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "outcome:alias",
                TurnEffectStatus.APPLIED
                if alias_applied is True
                else TurnEffectStatus.FAILED
                if alias_applied is False
                else TurnEffectStatus.SKIPPED,
            )
        working_action = outcome_action.action_name if outcome_action is not None else accion_saliente.nombre
        working_result = (
            "ok" if evento.detalles.get("exito") is True else "error"
        ) if outcome_action is not None else resultado_memoria
        working_details = dict(detalles_resultado or {})
        working_details["id_accion"] = (
            outcome_action.action_id if outcome_action is not None else accion_saliente.id_accion
        )
        working_effect_key = (
            "outcome:working_memory"
            if outcome_action is not None
            else "decision:working_memory"
        )
        should_write_working = not (
            working_action == "none" and working_result == "sin_accion"
        )
        if should_write_working:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                working_effect_key,
                effect_kind="outcome" if outcome_action is not None else "decision",
                phase="outcome" if outcome_action is not None else "decision",
                required=True,
            )
            try:
                self.memory.agregar_accion(
                    accion=working_action,
                    resultado=working_result,
                    detalles=working_details,
                )
            except Exception:
                self._cerrar_efecto_uow(
                    evento.id_transaccion,
                    working_effect_key,
                    TurnEffectStatus.AMBIGUOUS,
                )
                raise
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                working_effect_key,
                TurnEffectStatus.APPLIED,
            )
        self._iniciar_efecto_uow(
            evento.id_transaccion,
            "decision:conversation",
            effect_kind="decision",
            phase="decision",
            required=True,
        )
        try:
            conversation_written = self._registrar_turno_conversacional_si_aplica(
                evento=evento,
                triage_decision=triage_decision,
                action_response=action_response,
                accion_saliente=accion_saliente,
            )
        except Exception:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:conversation",
                TurnEffectStatus.AMBIGUOUS,
            )
            raise
        self._cerrar_efecto_uow(
            evento.id_transaccion,
            "decision:conversation",
            TurnEffectStatus.APPLIED if conversation_written else TurnEffectStatus.SKIPPED,
        )

        should_cache = self._debe_cachear_transaccion(action_response)
        if not should_cache and self._es_error_guardado_conversacional(action_response):
            self._marcar_guardado_conversacional_pendiente(
                evento.id_transaccion,
                politica,
            )
        self._iniciar_efecto_uow(
            evento.id_transaccion,
            "decision:notification",
            effect_kind="decision",
            phase="decision",
            required=False,
        )
        try:
            notification_written = self._agregar_notificacion_si_corresponde(action_response)
        except Exception:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:notification",
                TurnEffectStatus.AMBIGUOUS,
            )
        else:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "decision:notification",
                TurnEffectStatus.APPLIED
                if notification_written
                else TurnEffectStatus.SKIPPED,
            )
        execution_success: bool | None = None
        if outcome_action is not None:
            execution_success = evento.detalles.get("exito")
        elif sandbox_outcome_action_id is not None:
            execution_success = resultado_memoria == "ok"
        identity_effect_key = (
            "outcome:identity" if execution_success is not None else "decision:identity"
        )
        self._iniciar_efecto_uow(
            evento.id_transaccion,
            identity_effect_key,
            effect_kind="outcome" if execution_success is not None else "decision",
            phase="outcome" if execution_success is not None else "decision",
            required=True,
        )
        identity_applied = self._registrar_estado_identidad(
            evento,
            triage_decision,
            action_response,
            execution_success=execution_success,
        )
        if not identity_applied:
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                identity_effect_key,
                TurnEffectStatus.FAILED,
            )
            raise TurnProcessingError("identity", "IdentityEffectFailed")
        self._cerrar_efecto_uow(
            evento.id_transaccion,
            identity_effect_key,
            TurnEffectStatus.APPLIED,
        )
        self._iniciar_efecto_uow(
            evento.id_transaccion,
            "decision:terminal_rest_arm",
            effect_kind="decision",
            phase="decision",
            required=False,
        )
        rest_armed = self._armar_descanso_terminal_si_corresponde(evento, triage_decision)
        self._cerrar_efecto_uow(
            evento.id_transaccion,
            "decision:terminal_rest_arm",
            TurnEffectStatus.APPLIED
            if rest_armed is True
            else TurnEffectStatus.FAILED
            if rest_armed is False
            else TurnEffectStatus.SKIPPED,
        )

        if outcome_action is not None:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "outcome:alias",
                effect_kind="outcome",
                phase="outcome",
                required=False,
            )
            alias_applied = self._registrar_alias_aprendido_si_corresponde(evento)
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "outcome:alias",
                TurnEffectStatus.APPLIED
                if alias_applied is True
                else TurnEffectStatus.FAILED
                if alias_applied is False
                else TurnEffectStatus.SKIPPED,
            )

        if outcome_action_id is not None:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "outcome:mark_consumed",
                effect_kind="bookkeeping",
                phase="outcome",
                required=True,
            )
            consumed = ledger.mark_outcome_consumed(outcome_action_id)
            if consumed != OutcomeConsumptionStatus.CONSUMED:
                raise ActionLedgerError(
                    f"No se pudo consumir outcome {outcome_action_id}: {consumed.value}"
                )
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "outcome:mark_consumed",
                TurnEffectStatus.APPLIED,
            )
        if sandbox_outcome_action_id is not None:
            self._iniciar_efecto_uow(
                evento.id_transaccion,
                "outcome:sandbox_mark_consumed",
                effect_kind="bookkeeping",
                phase="outcome",
                required=True,
            )
            consumed = ledger.mark_outcome_consumed(sandbox_outcome_action_id)
            if consumed != OutcomeConsumptionStatus.CONSUMED:
                raise ActionLedgerError(
                    f"No se pudo consumir outcome sandbox {sandbox_outcome_action_id}: {consumed.value}"
                )
            self._cerrar_efecto_uow(
                evento.id_transaccion,
                "outcome:sandbox_mark_consumed",
                TurnEffectStatus.APPLIED,
            )

        respuesta_durable = None if outcome_action is not None else accion_saliente
        self._completar_transaccion_ledger(evento.id_transaccion, respuesta_durable)
        if should_cache and respuesta_durable is not None:
            self._olvidar_guardado_conversacional_pendiente(evento.id_transaccion)
            self._cachear_transaccion(evento.id_transaccion, respuesta_durable)

        logger.info("Evento %s procesado. Accion: %s", evento.id_transaccion, accion_saliente.nombre)
        return respuesta_durable

    def _obtener_contexto_memoria_completa(
        self,
        evento: EventoEntrante,
        narrativa: str,
        percepcion_resumida: str,
        tarea: dict | None,
        memory_observation: dict | None = None,
    ) -> str:
        """Lee memoria solo para eventos que realmente llegaran a una capa cognitiva."""
        try:
            query_memoria = self._construir_query_memoria(evento, narrativa, percepcion_resumida, tarea)
            obtener_con_metadata = getattr(
                self.memory,
                "obtener_contexto_memoria_con_metadata",
                None,
            )
            if callable(obtener_con_metadata):
                resultado_contexto = obtener_con_metadata(
                    query=query_memoria,
                    limite=5,
                    max_caracteres=600,
                )
                memoria_texto = str(getattr(resultado_contexto, "texto", "") or "")
                retrieved_ids = list(
                    getattr(resultado_contexto, "retrieved_ids", ()) or ()
                )
                scores = list(getattr(resultado_contexto, "scores", ()) or ())
                if memory_observation is not None:
                    if len(retrieved_ids) == len(scores):
                        memory_observation["retrieved_ids"] = retrieved_ids
                        memory_observation["scores"] = scores
                    else:
                        logger.warning(
                            "Metadata de memoria desalineada; se omitira de AuditTrace."
                        )
            else:
                memoria_texto = self.memory.obtener_contexto_memoria(
                    query=query_memoria,
                    limite=5,
                    max_caracteres=600,
                )
            memoria_conversacional = self._obtener_contexto_conversacional()
            memoria_relacional = self._obtener_contexto_relacional(query_memoria)
            memoria_corto_plazo = self.memory.obtener_ultimas_acciones()
            bloques_memoria = []
            if memoria_conversacional:
                bloques_memoria.append(memoria_conversacional)
            if memoria_relacional:
                bloques_memoria.append(memoria_relacional)
            bloques_memoria.append(f"MEMORIA DE TRABAJO OPERATIVA:\n{memoria_corto_plazo}")
            bloques_memoria.append(f"RECUERDOS A LARGO PLAZO:\n{memoria_texto}")
            return "\n\n".join(bloques_memoria)
        except Exception as e:
            logger.error("Error leyendo contexto de memoria: %s", e, exc_info=True)
            return ""

    def _obtener_cantidad_memoria_trabajo(self) -> int:
        """Lee solo el conteo público de la cola real; fakes legacy producen cero."""
        obtener = getattr(self.memory, "obtener_cantidad_memoria_trabajo", None)
        if not callable(obtener):
            return 0
        try:
            return max(0, int(obtener()))
        except Exception as exc:
            logger.warning(
                "No se pudo observar el conteo de memoria de trabajo. error_type=%s",
                type(exc).__name__,
            )
            return 0

    def _obtener_contexto_conversacional(self) -> str:
        """Lee continuidad conversacional solo cuando ya se autorizo leer memoria."""
        obtener = getattr(self.memory, "obtener_contexto_conversacional", None)
        if not callable(obtener):
            return ""
        try:
            return str(obtener(n=6) or "").strip()
        except Exception as e:
            logger.error("Error leyendo memoria conversacional: %s", e, exc_info=True)
            return ""

    def _obtener_contexto_relacional(self, query_memoria: str) -> str:
        """Lee hechos relacionales persistentes solo dentro del bloque autorizado de memoria."""
        obtener = getattr(self.memory, "obtener_contexto_relacional", None)
        if not callable(obtener):
            return ""
        try:
            return str(obtener(query=query_memoria, limite=4, max_caracteres=500) or "").strip()
        except Exception as e:
            logger.error("Error leyendo memoria relacional: %s", e, exc_info=True)
            return ""

    def _guardar_evento_en_memoria(
        self,
        evento: EventoEntrante,
        tipo_memoria: str,
        narrativa: str,
        percepcion_resumida: str,
        estado_actual: dict,
        es_interrupcion_critica: bool,
    ) -> str | None | MemoryWriteReceipt:
        """Escribe todos los eventos para no romper consolidacion/sueno."""
        try:
            kwargs = {
                "tipo_evento": tipo_memoria,
                "narrativa": self._construir_narrativa_memoria(
                    narrativa,
                    percepcion_resumida,
                    evento,
                ),
                "estado_emocional": estado_actual,
                "valencia": 0.0,
                "impacto_emocional": self._calcular_impacto_memoria(
                    evento,
                    es_interrupcion_critica,
                ),
                "extra": {
                    "origen": evento.origen,
                    "objeto": evento.objeto_involucrado,
                    "estado_fisico": evento.detalles.get("estado_fisico"),
                    "id_accion": evento.detalles.get("id_accion"),
                    "accion": evento.detalles.get("accion"),
                    "exito": evento.detalles.get("exito"),
                },
            }
            guardar_con_recibo = getattr(
                self.memory,
                "guardar_experiencia_con_recibo",
                None,
            )
            if callable(guardar_con_recibo):
                return guardar_con_recibo(
                    **kwargs,
                    idempotency_key={
                        "transaction_id": evento.id_transaccion,
                        "event_kind": tipo_memoria,
                    },
                )
            return self.memory.guardar_experiencia(
                **kwargs,
            )
        except Exception as e:
            logger.error("Error guardando evento en MemoryManager: %s", e, exc_info=True)
            return None

    def _registrar_turno_conversacional_si_aplica(
        self,
        evento: EventoEntrante,
        triage_decision: dict,
        action_response: ActionResponse,
        accion_saliente: AccionSaliente,
    ) -> bool:
        """Guarda en RAM el par usuario/respuesta cuando la salida final fue hablar."""
        if evento.tipo_evento != "comando_usuario":
            return False
        if accion_saliente.nombre != "hablar":
            return False
        if bool((action_response.metadata or {}).get("manual_memory_save_control")):
            return False

        registrar = getattr(self.memory, "registrar_turno_conversacional", None)
        if not callable(registrar):
            return False

        mensaje_usuario = self._extraer_mensaje_usuario(evento)
        respuesta_lazzaro = self._extraer_texto_hablar(action_response.parametros)
        if not mensaje_usuario and not respuesta_lazzaro:
            return False

        try:
            registrar(
                mensaje_usuario=mensaje_usuario,
                respuesta_lazzaro=respuesta_lazzaro,
                pensamiento=action_response.pensamiento,
                route=str(triage_decision.get("route", "")),
                intent=str(triage_decision.get("intent", "")),
                metadata={
                    "origen": evento.origen,
                    "id_transaccion": evento.id_transaccion,
                    "accion": accion_saliente.nombre,
                },
            )
            return True
        except Exception as e:
            logger.error("Error registrando turno conversacional: %s", e, exc_info=True)
            raise

    @staticmethod
    def _extraer_mensaje_usuario(evento: EventoEntrante) -> str:
        for clave in ("mensaje", "texto_usuario", "texto"):
            valor = evento.detalles.get(clave)
            if valor:
                return str(valor).strip()
        return ""

    @staticmethod
    def _usuario_pide_cancelar_tarea(evento: EventoEntrante) -> bool:
        if evento.tipo_evento != "comando_usuario":
            return False
        return str(evento.detalles.get("task_intent") or "").strip().lower() == "cancel"

    @staticmethod
    def _extraer_texto_hablar(parametros: dict) -> str:
        if not isinstance(parametros, dict):
            return ""
        for clave in ("texto_a_comunicar", "texto", "mensaje"):
            valor = parametros.get(clave)
            if valor:
                return str(valor).strip()
        return ""

    def _registrar_alias_candidato_pendiente(
        self,
        action_response: ActionResponse,
        accion_saliente: AccionSaliente,
    ) -> bool:
        metadata = action_response.metadata if isinstance(action_response.metadata, dict) else {}
        resolution = metadata.get("motor_resolution") if isinstance(metadata.get("motor_resolution"), dict) else None
        alias_learning = metadata.get("alias_learning") if isinstance(metadata.get("alias_learning"), dict) else {}
        if not isinstance(resolution, dict) or resolution.get("status") != "ready":
            return False

        user_query = str(alias_learning.get("user_query") or "").strip()
        if not user_query:
            return False

        target_name = str(
            action_response.parametros.get("nombre_objeto")
            if isinstance(action_response.parametros, dict)
            else ""
        ).strip()
        pending = {
            "id_accion": accion_saliente.id_accion,
            "action": action_response.accion,
            "target_name": target_name,
            "user_query": user_query,
            "motor_resolution": resolution,
        }
        with self._obtener_lock_recurso("_alias_candidates_lock"):
            pending_store = getattr(self, "_alias_candidates_pendientes", None)
            if not isinstance(pending_store, OrderedDict):
                self._alias_candidates_pendientes = OrderedDict()
                pending_store = self._alias_candidates_pendientes
            pending_store[accion_saliente.id_accion] = pending
            while len(pending_store) > self.MAX_TRANSACCIONES_CACHE:
                pending_store.popitem(last=False)
        return True

    def _registrar_alias_aprendido_si_corresponde(self, evento: EventoEntrante) -> bool | None:
        if evento.tipo_evento != "resultado_accion":
            return None
        if evento.detalles.get("exito") is not True:
            return None

        pending = self._buscar_alias_pendiente_para_resultado(evento.detalles)
        if pending is None:
            return None
        return self._persistir_alias_pendiente(pending)

    def _aprobar_alias_pendiente_por_id(self, id_accion: str) -> bool | None:
        with self._obtener_lock_recurso("_alias_candidates_lock"):
            pending_store = getattr(self, "_alias_candidates_pendientes", None)
            pending = (
                pending_store.pop(id_accion, None)
                if isinstance(pending_store, OrderedDict)
                else None
            )
        if pending is not None:
            return self._persistir_alias_pendiente(pending)
        return None

    def _buscar_alias_pendiente_para_resultado(self, detalles: dict) -> dict | None:
        with self._obtener_lock_recurso("_alias_candidates_lock"):
            pending_store = getattr(self, "_alias_candidates_pendientes", None)
            if not isinstance(pending_store, OrderedDict) or not pending_store:
                return None

            id_accion = str(detalles.get("id_accion") or "").strip()
            if not id_accion:
                return None
            return pending_store.pop(id_accion, None)

    def _persistir_alias_pendiente(self, pending: dict) -> bool:
        candidate = build_learned_alias_candidate(
            user_query=str(pending.get("user_query") or ""),
            resolution=pending.get("motor_resolution") if isinstance(pending.get("motor_resolution"), dict) else {},
            action_success=True,
            action=str(pending.get("action") or ""),
        )
        if candidate is None:
            return False

        guardar_alias = getattr(self.memory, "guardar_alias_aprendido", None)
        if not callable(guardar_alias):
            return False
        try:
            try:
                return bool(
                    guardar_alias(
                        candidate,
                        idempotency_key={
                            "action_id": str(pending.get("id_accion") or ""),
                            "effect": "learned_alias",
                        },
                    )
                )
            except TypeError:
                # Compatibilidad con adapters de tests/legacy de una sola posicion.
                return bool(guardar_alias(candidate))
        except Exception as e:
            logger.error("Error guardando alias aprendido: %s", e, exc_info=True)
            return False

    @staticmethod
    def _accion_pasiva(razon: str | None = None) -> ActionResponse:
        return ActionResponse(
            pensamiento="",
            accion="none",
            parametros={},
            razon=razon or "Evento tratado como telemetria pasiva.",
            valido=True,
            outcome=DecisionOutcome(
                disposition=DecisionDisposition.NO_ACTION,
                quality=DecisionQuality.VALIDATED,
                attempt=DecisionAttempt.FIRST,
                layer=DecisionLayer.CORE,
                reason_codes=(DecisionReasonCode.PASSIVE_NO_ACTION,),
                retry_count=0,
            ),
        )

    def _evaluar_triage(
        self,
        evento: EventoEntrante,
        estado_actual: dict,
    ) -> tuple[TriageEvaluationResultV1, dict | None]:
        """Evalua Capa 0 una vez y valida su proyeccion antes de autorizar."""
        try:
            decision = self.triage_router.route_event(
                evento.model_dump(mode="python"),
                estado_agente=estado_actual if isinstance(estado_actual, dict) else {},
            )
        except Exception as exc:
            failure = triage_evaluation_failure(exc)
            log_safe_boundary_failure(
                logger,
                component="orchestrator.triage",
                code=failure.code.value,
                error=exc,
            )
            return failure, None

        result = validate_triage_legacy_result(decision)
        if isinstance(result, TriageBoundaryFailureV1):
            log_safe_boundary_failure(
                logger,
                component="orchestrator.triage",
                code=result.code.value,
            )
            return result, None

        assert isinstance(decision, dict)

        logger.info(
            "Triage: intent=%s route=%s confidence=%s",
            decision.get("intent"),
            decision.get("route"),
            decision.get("confidence"),
        )
        return result, decision

    @staticmethod
    def _es_canal_terminal(evento: EventoEntrante) -> bool:
        """Identifica el cliente conversacional que nunca materializa acciones fisicas."""
        return str(evento.origen or "").strip().lower() == "terminal"

    @classmethod
    def _restriccion_canal_persona(cls, evento: EventoEntrante) -> str:
        if not cls._es_canal_terminal(evento):
            return ""
        return (
            "Este turno proviene de un chat de terminal sin cuerpo disponible. "
            "Solo conversa. Si el usuario menciona o pide una accion fisica, habla sobre ella "
            "o explica que requiere Unity; no afirmes que la ejecutaste, que la estas ejecutando "
            "ni que la ejecutaras desde este canal."
        )

    @classmethod
    def _adaptar_triage_para_terminal(
        cls,
        evento: EventoEntrante,
        decision: dict,
    ) -> dict:
        """
        Conserva Persona y Oraculo, pero reconduce cualquier otra ruta a conversacion.

        El clasificador sigue aportando su intent original para auditoria y memoria.
        Solo cambia el destino operativo, de modo que una clasificacion motora
        accidental nunca llegue al cuerpo desde el chat.
        """
        if not cls._es_canal_terminal(evento):
            return decision

        decision_terminal = dict(decision or {})
        route_original = str(decision_terminal.get("route", "")).strip().lower()
        if route_original in {"persona", "oracle"}:
            return decision_terminal

        razon_original = str(decision_terminal.get("reason", "")).strip()
        razon_terminal = "Canal terminal conversacional: la salida se verbaliza en Persona."
        decision_terminal.update(
            {
                "route": "persona",
                "requires_memory": True,
                "requires_oracle": False,
                "reason": " ".join(
                    parte for parte in (razon_original, razon_terminal) if parte
                ),
                "terminal_original_route": route_original or "sin_ruta",
            }
        )
        return decision_terminal

    @classmethod
    def _blindar_salida_terminal(
        cls,
        evento: EventoEntrante,
        action_response: ActionResponse,
    ) -> ActionResponse:
        """Aplica una ultima barrera para que terminal solo pueda devolver ``hablar``."""
        if not action_response.valido:
            return action_response
        if not cls._es_canal_terminal(evento) or action_response.accion == "hablar":
            return action_response

        metadata = dict(action_response.metadata or {})
        metadata.setdefault("terminal_action_blocked", action_response.accion)
        if action_response.error:
            texto = "No pude construir bien mi respuesta. Podemos intentarlo otra vez."
        else:
            texto = "Desde este chat solo puedo hablar; no voy a ejecutar acciones fisicas."

        return ActionResponse(
            pensamiento=action_response.pensamiento,
            accion="hablar",
            parametros={"texto_a_comunicar": texto},
            razon="Canal terminal limitado a conversacion.",
            valido=True,
            error=action_response.error,
            metadata=metadata,
            outcome=action_response.outcome.evolve(
                disposition=DecisionDisposition.BLOCKED,
                reason_codes=(
                    DecisionReasonCode.CHANNEL_BLOCKED,
                    *tuple(
                        code
                        for code in action_response.outcome.reason_codes
                        if code is not DecisionReasonCode.CHANNEL_BLOCKED
                    ),
                ),
                motor_status=None,
            ),
        )

    @staticmethod
    def _triage_debe_cortar_flujo(decision: dict) -> bool:
        route = str(decision.get("route", "")).strip().lower()
        return route in {"noop", "internal"}

    @staticmethod
    def _triage_es_motor(decision: dict) -> bool:
        return str(decision.get("route", "")).strip().lower() == "motor"

    @staticmethod
    def _triage_es_persona(decision: dict) -> bool:
        return str(decision.get("route", "")).strip().lower() == "persona"

    @staticmethod
    def _triage_es_oracle(decision: dict) -> bool:
        return str(decision.get("route", "")).strip().lower() == "oracle"

    @staticmethod
    def _triage_es_guardado_conversacional(decision: dict) -> bool:
        return str(decision.get("intent", "")).strip().upper() == "CONVERSATION_SAVE"

    def _evento_requiere_contexto_memoria(
        self,
        triage_decision: dict,
        solicita_decision: bool,
        es_interrupcion_critica: bool,
    ) -> bool:
        if es_interrupcion_critica:
            return False
        if not solicita_decision:
            return False
        return not self._triage_debe_cortar_flujo(triage_decision)

    def _resolver_ruta_persona(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        politica: str,
        audit_observation: dict[str, object] | None = None,
        identity_context: IdentityContextV1 | None = None,
    ) -> ActionResponse:
        """Usa Capa 2 para conversacion social, sin herramientas fisicas."""
        mensaje_usuario = self._current_user_request(evento)
        percepcion = self._extraer_percepcion_motor(evento, contexto_llm)

        try:
            bridge = self._persona_factual_boundary()
            factual_turn = bridge.prepare(
                message=mensaje_usuario,
                identity=self._exigir_identity_context(identity_context),
                memory_summary=memoria_texto,
                event_details=evento.detalles,
                prior_perception=percepcion,
                terminal_channel=self._es_canal_terminal(evento),
            )
            mensajes = factual_turn.prompt_plan
            llm_budget = self._nuevo_budget_ruta_llm()
            raw_response = self.llm_adapter.ejecutar_plan(
                mensajes,
                politica=politica,
                temperature=0.45,
                max_output_tokens=768,
                token_strategy="cap",
                error_context="Capa Social",
                budget=llm_budget,
            )
            factual_result = bridge.finish(raw_response, factual_turn)
            factual_result_final = factual_result
            hubo_reintento = False
            self._observe_factual_fallback(factual_result, audit_observation)
            if factual_result.retryable_syntax:
                logger.info("Capa Social solicita reintento breve por salida LLM irrecuperable.")
                mensajes_reintento = self.persona_engine.preparar_reintento_json(
                    mensajes_originales=mensajes,
                    respuesta_fallida=raw_response or "",
                )
                resultado_reintento = self.llm_adapter.ejecutar_plan_result(
                    mensajes_reintento,
                    politica=politica,
                    temperature=0.2,
                    max_output_tokens=256,
                    token_strategy="cap",
                    error_context="Reintento Capa Social",
                    budget=llm_budget,
                )
                hubo_reintento = bool(resultado_reintento.receipts)
                if hubo_reintento:
                    factual_result_final = bridge.finish(
                        resultado_reintento.final_text or "", factual_turn
                    )
            self._observe_factual_fallback(factual_result_final, audit_observation)
            datos = self._factual_persona_data(factual_result_final)
            return self._action_response_desde_persona(
                datos,
                outcome=self._factual_persona_outcome(
                    primary_status=factual_result.parse_status,
                    final_result=factual_result_final,
                    retried=hubo_reintento,
                    layer=DecisionLayer.PERSONA,
                ),
            )
        except Exception as e:
            logger.error("Error en Persona factual. error_type=%s", type(e).__name__)
            if isinstance(audit_observation, dict):
                audit_observation["fallback_used"] = True
                audit_observation.setdefault("decision_error_type", type(e).__name__)
                audit_observation.setdefault("decision_error_component", "persona")
                audit_observation.setdefault(
                    "decision_error_observed_ns",
                    time.perf_counter_ns(),
                )
            raise

    def _nuevo_budget_ruta_llm(self) -> LLMCallBudgetV1:
        """Crea el único deadline de una ruta justo antes de su primer I/O LLM."""
        facade = getattr(self.llm_adapter, "ai_client", None)
        factory = getattr(facade, "new_budget", None)
        if callable(factory):
            return factory()
        settings = getattr(facade, "settings", None)
        total = getattr(settings, "LLM_TOTAL_BUDGET_SECONDS", 45.0)
        return LLMCallBudgetV1.from_seconds(total)

    def _exigir_identity_context(
        self,
        identity_context: IdentityContextV1 | None,
    ) -> IdentityContextV1:
        if isinstance(identity_context, IdentityContextV1):
            return identity_context
        provider = getattr(self, "identity_provider", None)
        if not isinstance(provider, IdentitySnapshotProvider):
            raise TypeError("La decision cognitiva exige IdentityContextV1")
        return IdentityContextV1.from_snapshot(provider.capture())

    def _identity_context_for_turn(
        self,
        components: IdentityComponentsV1 | None,
    ) -> IdentityContextV1:
        """Construye identidad de turno sin releer owners cuando hay carrier."""
        if isinstance(components, IdentityComponentsV1):
            snapshot = self.identity_provider.from_components(components)
        elif components is None:
            # Compatibilidad para Behavior fakes/legacy sin carrier interno.
            snapshot = self.identity_provider.capture()
        else:
            raise TypeError("components debe ser IdentityComponentsV1 o None")
        return IdentityContextV1.from_snapshot(snapshot)

    def _persona_factual_boundary(self) -> PersonaFactualBoundaryV1:
        """The one replaceable ROOT-017 seam; never bypass it for a Persona turn."""
        bridge = getattr(self, "persona_factual_bridge", None)
        if bridge is None:
            # Also supports isolated Orchestrator fixtures made with object.__new__.
            bridge = PersonaFactualBridgeV1()
            self.persona_factual_bridge = bridge
        if not isinstance(bridge, PersonaFactualBoundaryV1):
            raise TypeError("Persona factual boundary must be typed")
        return bridge

    @staticmethod
    def _factual_persona_data(result: PersonaFactualRenderResultV1) -> dict:
        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": result.text},
            "pensamiento_privado": "",
            "razon": "ROOT-017 factual bridge",
            "structured_parse_status": result.parse_status.value,
            "primary_parse_valid": result.parse_status is StructuredParseStatus.VALID,
            "factual_validation_status": result.validation.status.value,
        }

    @staticmethod
    def _observe_factual_fallback(
        result: PersonaFactualRenderResultV1,
        audit_observation: dict[str, object] | None,
    ) -> None:
        if not isinstance(audit_observation, dict):
            return
        if result.validation.status is PersonaFactualValidationStatusV1.ACCEPTED:
            return
        audit_observation["fallback_used"] = True
        audit_observation.setdefault(
            "decision_error_type",
            "InvalidLLMResponse"
            if result.parse_status is not StructuredParseStatus.VALID
            else "InvalidFactualPlan",
        )
        audit_observation.setdefault(
            "decision_error_component",
            "llm" if result.parse_status is not StructuredParseStatus.VALID else "persona",
        )
        audit_observation.setdefault("decision_error_observed_ns", time.perf_counter_ns())

    def _factual_persona_outcome(
        self,
        *,
        primary_status: StructuredParseStatus,
        final_result: PersonaFactualRenderResultV1,
        retried: bool,
        layer: DecisionLayer,
    ) -> DecisionOutcome:
        outcome = self._outcome_persona(
            primary_status=primary_status,
            final_status=final_result.parse_status,
            retried=retried,
            layer=layer,
        )
        if final_result.validation.status is PersonaFactualValidationStatusV1.REJECTED:
            return outcome.evolve(
                quality=DecisionQuality.DETERMINISTIC_FALLBACK,
                reason_codes=(DecisionReasonCode.DETERMINISTIC_FALLBACK,),
            )
        if final_result.validation.status is PersonaFactualValidationStatusV1.PARTIAL:
            return outcome.evolve(
                # ROOT-008 reserves SALVAGED for a DEGRADED parse. This is a
                # VALID plan with factual acts dropped by deterministic policy.
                quality=DecisionQuality.DETERMINISTIC_FALLBACK,
                reason_codes=(DecisionReasonCode.SAFE_SALVAGE,),
            )
        return outcome

    @staticmethod
    def _observar_fallback_persona(
        datos: dict,
        audit_observation: dict[str, object] | None,
    ) -> None:
        if not isinstance(audit_observation, dict) or not isinstance(datos, dict):
            return
        if not (
            datos.get("requiere_reintento")
            or datos.get("error")
            or datos.get("recuperado_de_json_parcial")
            or datos.get("primary_parse_valid") is False
        ):
            return
        audit_observation["fallback_used"] = True
        audit_observation.setdefault("decision_error_type", "InvalidLLMResponse")
        audit_observation.setdefault("decision_error_component", "llm")
        audit_observation.setdefault(
            "decision_error_observed_ns",
            time.perf_counter_ns(),
        )

    def _resolver_guardado_conversacional(
        self,
        evento: EventoEntrante,
    ) -> ActionResponse:
        """Persiste RAM pública y emite un acuse operativo Memory/Core."""
        guardar = getattr(self.memory, "guardar_fotografia_conversacional", None)
        if not callable(guardar):
            resultado = ManualConversationSaveResult(
                status="error",
                error="MemoryManager no expone el guardado conversacional manual.",
            )
        else:
            try:
                resultado = guardar(id_transaccion=evento.id_transaccion)
            except Exception as exc:
                logger.error(
                    "Error resolviendo guardado manual de conversación: %s",
                    exc,
                    exc_info=True,
                )
                resultado = ManualConversationSaveResult(
                    status="error",
                    error=str(exc),
                )

        return manual_conversation_save_acknowledgement(resultado)

    def _reintentar_guardado_conversacional_pendiente(
        self,
        evento: EventoEntrante,
    ) -> Optional[AccionSaliente]:
        """
        Reintenta solo la escritura manual, sin repetir BehaviorCore ni memorias.

        El primer intento ya registró el evento y su fallo. Un retry con el
        mismo ID únicamente debe completar la persistencia pendiente.
        """
        with self._obtener_lock_recurso("_guardados_conversacionales_lock"):
            pendientes = getattr(self, "_guardados_conversacionales_pendientes", None)
            politica = (
                pendientes.get(evento.id_transaccion)
                if isinstance(pendientes, OrderedDict)
                else None
            )
        if politica is None:
            return None

        action_response = self._resolver_guardado_conversacional(evento)
        action_response = self._blindar_salida_terminal(evento, action_response)
        action_response = self._sanitizar_action_response(action_response)
        action_response = self._blindar_salida_terminal(evento, action_response)
        action_response = self._adjuntar_esfuerzo_subjetivo(action_response)
        accion_saliente = self._a_accion_saliente(action_response, politica)

        if self._debe_cachear_transaccion(action_response):
            self._olvidar_guardado_conversacional_pendiente(evento.id_transaccion)
            self._cachear_transaccion(evento.id_transaccion, accion_saliente)

        logger.info(
            "Retry de guardado manual %s resuelto con estado=%s.",
            evento.id_transaccion,
            (action_response.metadata or {}).get("manual_memory_save_status"),
        )
        return accion_saliente

    @staticmethod
    def _action_response_desde_persona(
        datos: dict,
        outcome: DecisionOutcome | None = None,
    ) -> ActionResponse:
        if not isinstance(datos, dict):
            datos = {}

        parametros = datos.get("parametros", {})
        if not isinstance(parametros, dict):
            parametros = {}

        texto = str(parametros.get("texto_a_comunicar", "")).strip()
        if not texto:
            texto = "No se que decir, mi mente se fue por una rendija."
            parametros = {"texto_a_comunicar": texto}

        metadata = {
            key: datos[key]
            for key in (
                "primary_parse_valid",
                "structured_parse_status",
                "structured_parse_reason_codes",
                "structured_parse_normalizations",
                "factual_validation_status",
            )
            if key in datos
        }

        return ActionResponse(
            pensamiento=str(datos.get("pensamiento_privado", "")),
            accion="hablar",
            parametros=parametros,
            razon=str(datos.get("razon", "Respuesta social de Capa 2.")),
            valido=True,
            error=(str(datos.get("error")) if datos.get("error") else None),
            metadata=metadata,
            outcome=outcome or DecisionOutcome.legacy_for_action("hablar"),
        )

    @staticmethod
    def _outcome_persona(
        *,
        primary_status: StructuredParseStatus,
        final_status: StructuredParseStatus,
        retried: bool,
        layer: DecisionLayer,
    ) -> DecisionOutcome:
        if final_status is StructuredParseStatus.VALID:
            quality = DecisionQuality.VALIDATED
            reason = (
                DecisionReasonCode.RETRY_REPAIRED
                if retried
                else DecisionReasonCode.PRIMARY_VALID
            )
        elif final_status is StructuredParseStatus.DEGRADED:
            quality = DecisionQuality.SALVAGED
            reason = DecisionReasonCode.SAFE_SALVAGE
        else:
            quality = DecisionQuality.DETERMINISTIC_FALLBACK
            reason = DecisionReasonCode.DETERMINISTIC_FALLBACK

        return DecisionOutcome(
            disposition=DecisionDisposition.ACTION,
            quality=quality,
            attempt=DecisionAttempt.REPAIR if retried else DecisionAttempt.FIRST,
            layer=layer,
            reason_codes=(reason,),
            retry_count=1 if retried else 0,
            primary_parse_status=primary_status,
            final_parse_status=final_status,
        )

    def _resolver_ruta_oracle(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        politica: str,
        audit_observation: dict[str, object] | None = None,
        identity_context: IdentityContextV1 | None = None,
    ) -> ActionResponse:
        """Usa Capa 3 para conocimiento frio y delega la voz publica a Persona."""
        mensaje_usuario = self._current_user_request(evento)
        mensaje_oracle = (mensaje_usuario or "").strip()
        if not mensaje_oracle:
            local_producer = local_deterministic_producer(
                accepted_at_utc=datetime.now(timezone.utc)
            )
            oracle_result = OracleResult(
                pregunta_original="",
                contenido_tecnico="La consulta al Oraculo no contiene mensaje de usuario.",
                resumen_para_persona="No habia una consulta clara para procesar.",
                provenance=provenance_for(
                    local_producer,
                    cache_receipt_not_checked(),
                ),
                error="La consulta al Oraculo no contiene mensaje de usuario.",
            )
            if isinstance(audit_observation, dict):
                audit_observation["oracle_source"] = oracle_result.fuente
                audit_observation["fallback_used"] = True
                audit_observation.setdefault(
                    "decision_error_type",
                    "InvalidOracleRequest",
                )
                audit_observation.setdefault("decision_error_component", "oracle")
                audit_observation.setdefault(
                    "decision_error_observed_ns",
                    time.perf_counter_ns(),
                )
            return self._verbalizar_resultado_oracle(
                evento=evento,
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto,
                politica=politica,
                oracle_result=oracle_result,
                audit_observation=audit_observation,
                identity_context=identity_context,
            )

        errores: list[str] = []
        llm_budget: LLMCallBudgetV1 | None = None
        oracle_parse_result = None
        cache_receipt = None
        try:
            cache_lookup = self.oracle_engine.evaluar_cache(
                mensaje_oracle,
                self.oracle_memory,
            )
            oracle_result = cache_lookup.cached_result
            cache_receipt = cache_lookup.cache_receipt
        except Exception as e:
            logger.error("Error consultando OracleMemory: %s", e, exc_info=True)
            errores.append("OracleMemory no pudo consultar el cache semantico.")
            oracle_result = None
            cache_receipt = cache_receipt_for_lookup(
                mensaje_oracle,
                status=CacheLookupStatusV1.ERROR,
                threshold=OracleMemory.DEFAULT_CACHE_DISTANCE,
            )

        if oracle_result is None:
            try:
                if cache_receipt is None:
                    raise RuntimeError("Oracle cache lookup missing typed receipt")
                mensajes = self.oracle_engine.preparar_consulta(mensaje_usuario)
                llm_budget = self._nuevo_budget_ruta_llm()
                active_facade = getattr(self.llm_adapter, "ai_client", None)
                if active_facade is None:
                    active_facade = getattr(getattr(self, "reasoning", None), "ai_client", None)
                active_providers = tuple(getattr(active_facade, "providers", ()))
                providers = (
                    (self.oracle_client, *active_providers)
                    if self.oracle_client.configurado
                    else active_providers
                )
                route_by_provider = self._construir_mapa_rutas_oracle(
                    oracle_provider=(
                        self.oracle_client if self.oracle_client.configurado else None
                    ),
                    active_providers=active_providers,
                )
                execute_result = getattr(self.llm_adapter, "ejecutar_plan_result", None)
                if callable(execute_result) and providers:
                    technical_result = execute_result(
                        mensajes,
                        politica=politica,
                        temperature=0.1,
                        max_output_tokens=2048,
                        token_strategy="floor",
                        error_context="Oraculo externo y fallback activo",
                        budget=llm_budget,
                        providers=providers,
                    )
                elif callable(getattr(active_facade, "execute_plan_result", None)) and providers:
                    technical_result = active_facade.execute_plan_result(
                        mensajes,
                        budget=llm_budget,
                        generation={
                            "temperature": 0.1,
                            "max_output_tokens": 2048,
                            "response_mime_type": "application/json",
                        },
                        providers=providers,
                    )
                elif self.oracle_client.configurado:
                    # Compatibilidad de dobles aislados: incluso aquí el Oracle
                    # atraviesa el mismo executor, nunca una llamada productiva cruda.
                    from ai_engine.fallback_ai_client import FallbackAIClient

                    controlled_oracle = FallbackAIClient((self.oracle_client,))
                    technical_result = controlled_oracle.execute_plan_result(
                        mensajes,
                        budget=llm_budget,
                        generation={"temperature": 0.1},
                    )
                else:
                    raise RuntimeError(
                        "Oracle route requires a typed ROOT-021 provider result"
                    )
                if not isinstance(technical_result, LLMCallResultV1):
                    raise TypeError("Oracle provider route returned an untyped result")
                respuesta_cruda = technical_result.final_text or ""

                oracle_parse_result = self.oracle_engine.parse_result(
                    respuesta_cruda or ""
                )
                if (
                    technical_result.succeeded
                    and oracle_parse_result.status is not StructuredParseStatus.ERROR
                ):
                    producer = provider_receipt_from_llm(
                        technical_result,
                        route_by_provider=route_by_provider,
                        final_parse_status=oracle_parse_result.status,
                        accepted_at_utc=datetime.now(timezone.utc),
                    )
                else:
                    producer = local_deterministic_producer(
                        technical_result=technical_result,
                        final_parse_status=oracle_parse_result.status,
                        accepted_at_utc=datetime.now(timezone.utc),
                    )
                oracle_result = self.oracle_engine.proyectar_resultado(
                    oracle_parse_result,
                    mensaje_oracle,
                    self.oracle_memory,
                    producer=producer,
                    cache_receipt=cache_receipt,
                )
            except Exception as e:
                logger.error("Error en OracleEngine: %s", e, exc_info=True)
                if isinstance(audit_observation, dict):
                    audit_observation["fallback_used"] = True
                    audit_observation.setdefault("decision_error_type", type(e).__name__)
                    audit_observation.setdefault("decision_error_component", "oracle")
                    audit_observation.setdefault(
                        "decision_error_observed_ns",
                        time.perf_counter_ns(),
                    )
                raise

        if oracle_result.error:
            errores.append(oracle_result.error)
        if errores:
            oracle_result = oracle_result.model_copy(update={"error": " ".join(errores)})

        if isinstance(audit_observation, dict):
            audit_observation["oracle_source"] = oracle_result.fuente
            if (
                oracle_result.fuente == "error"
                or (
                    oracle_parse_result is not None
                    and oracle_parse_result.status is not StructuredParseStatus.VALID
                )
            ):
                audit_observation["fallback_used"] = True
                audit_observation.setdefault(
                    "decision_error_type",
                    "InvalidLLMResponse",
                )
                audit_observation.setdefault("decision_error_component", "llm")
                audit_observation.setdefault(
                    "decision_error_observed_ns",
                    time.perf_counter_ns(),
                )

        return self._verbalizar_resultado_oracle(
            evento=evento,
            contexto_llm=contexto_llm,
            memoria_texto=memoria_texto,
            politica=politica,
            oracle_result=oracle_result,
            audit_observation=audit_observation,
            identity_context=identity_context,
            llm_budget=llm_budget,
        )

    @staticmethod
    def _construir_mapa_rutas_oracle(
        *,
        oracle_provider: object | None,
        active_providers: tuple[object, ...],
    ) -> dict[str, KnowledgeProducerRouteV1]:
        """Mapea slots reales antes de dispatch; IDs vacíos/duplicados fallan."""
        slots: list[tuple[object, KnowledgeProducerRouteV1]] = []
        if oracle_provider is not None:
            slots.append(
                (oracle_provider, KnowledgeProducerRouteV1.ORACLE_EXTERNAL)
            )
        slots.extend(
            (provider, KnowledgeProducerRouteV1.ACTIVE_PROVIDER_CHAIN)
            for provider in active_providers
        )

        route_by_provider: dict[str, KnowledgeProducerRouteV1] = {}
        for provider, route in slots:
            provider_id = str(getattr(provider, "provider_name", "")).strip()
            if not provider_id:
                raise ValueError("Oracle provider slot has an empty provider identity")
            if provider_id in route_by_provider:
                raise ValueError("Oracle provider slots have duplicate provider identities")
            route_by_provider[provider_id] = route
        return route_by_provider

    def _verbalizar_resultado_oracle(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        politica: str,
        oracle_result: OracleResult,
        audit_observation: dict[str, object] | None = None,
        identity_context: IdentityContextV1 | None = None,
        llm_budget: LLMCallBudgetV1 | None = None,
    ) -> ActionResponse:
        """Pasa conocimiento frio del Oraculo por Persona antes de responder a Unity."""
        mensaje_usuario = self._current_user_request(evento)
        percepcion = self._extraer_percepcion_motor(evento, contexto_llm)

        try:
            bridge = self._persona_factual_boundary()
            factual_turn = bridge.prepare(
                message=mensaje_usuario,
                identity=self._exigir_identity_context(identity_context),
                memory_summary=memoria_texto,
                event_details=evento.detalles,
                prior_perception=percepcion,
                terminal_channel=self._es_canal_terminal(evento),
                oracle_result=oracle_result,
            )
            mensajes = factual_turn.prompt_plan
            if llm_budget is None:
                # Cache hit: no hubo etapa técnica, así que el budget nace aquí.
                llm_budget = self._nuevo_budget_ruta_llm()
            raw_response = self.llm_adapter.ejecutar_plan(
                mensajes,
                politica=politica,
                temperature=0.35,
                max_output_tokens=768,
                token_strategy="cap",
                error_context="Verbalizacion Capa Social del Oraculo",
                budget=llm_budget,
            )
            factual_result = bridge.finish(raw_response, factual_turn)
            factual_result_final = factual_result
            hubo_reintento = False
            self._observe_factual_fallback(factual_result, audit_observation)
            if factual_result.retryable_syntax:
                logger.info("Verbalizacion del Oraculo solicita reintento social breve.")
                mensajes_reintento = self.persona_engine.preparar_reintento_json(
                    mensajes_originales=mensajes,
                    respuesta_fallida=raw_response or "",
                )
                resultado_reintento = self.llm_adapter.ejecutar_plan_result(
                    mensajes_reintento,
                    politica=politica,
                    temperature=0.2,
                    max_output_tokens=256,
                    token_strategy="cap",
                    error_context="Reintento verbalizacion Capa Social del Oraculo",
                    budget=llm_budget,
                )
                hubo_reintento = bool(resultado_reintento.receipts)
                if hubo_reintento:
                    factual_result_final = bridge.finish(
                        resultado_reintento.final_text or "", factual_turn
                    )

            self._observe_factual_fallback(factual_result_final, audit_observation)
            datos = self._factual_persona_data(factual_result_final)

            outcome = self._factual_persona_outcome(
                primary_status=factual_result.parse_status,
                final_result=factual_result_final,
                retried=hubo_reintento,
                layer=DecisionLayer.ORACLE_PERSONA,
            )
            if oracle_result.fuente == "error" or oracle_result.error:
                outcome = outcome.evolve(
                    quality=DecisionQuality.DETERMINISTIC_FALLBACK,
                    reason_codes=(
                        DecisionReasonCode.ORACLE_RESULT_UNAVAILABLE,
                        *tuple(
                            code
                            for code in outcome.reason_codes
                            if code is not DecisionReasonCode.ORACLE_RESULT_UNAVAILABLE
                        ),
                    ),
                )
            action_response = self._action_response_desde_persona(
                datos,
                outcome=outcome,
            )
            if oracle_result.error:
                action_response = action_response.model_copy(update={"error": oracle_result.error})
            return action_response
        except Exception as e:
            logger.error("Error verbalizando resultado del Oraculo. error_type=%s", type(e).__name__)
            if isinstance(audit_observation, dict):
                audit_observation["fallback_used"] = True
                audit_observation.setdefault("decision_error_type", type(e).__name__)
                audit_observation.setdefault("decision_error_component", "persona")
                audit_observation.setdefault(
                    "decision_error_observed_ns",
                    time.perf_counter_ns(),
                )
            raise

    def _resolver_ruta_motor(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        herramientas: list[str],
        herramientas_detalle: dict[str, str],
        politica: str,
        tarea: dict | None,
        triage_decision: dict | None = None,
        estado_actual: dict | None = None,
        audit_observation: dict[str, object] | None = None,
        identity_context: IdentityContextV1 | None = None,
    ) -> ActionResponse:
        response, _trusted_path = self._resolver_ruta_motor_con_provenance(
            evento=evento,
            contexto_llm=contexto_llm,
            memoria_texto=memoria_texto,
            herramientas=herramientas,
            herramientas_detalle=herramientas_detalle,
            politica=politica,
            tarea=tarea,
            triage_decision=triage_decision,
            estado_actual=estado_actual,
            audit_observation=audit_observation,
            identity_context=identity_context,
        )
        self._record_trusted_parameter_path(_trusted_path)
        return response

    @classmethod
    def _record_trusted_parameter_path(
        cls,
        path: TrustedParameterPathV1,
    ) -> None:
        if not isinstance(path, TrustedParameterPathV1):
            raise TypeError("trusted parameter path must be typed")
        cls._PARAMETER_PROVENANCE.path = path

    @classmethod
    def _consume_trusted_parameter_path(cls) -> TrustedParameterPathV1:
        path = getattr(
            cls._PARAMETER_PROVENANCE,
            "path",
            TrustedParameterPathV1.UNKNOWN,
        )
        try:
            del cls._PARAMETER_PROVENANCE.path
        except AttributeError:
            pass
        return (
            path
            if isinstance(path, TrustedParameterPathV1)
            else TrustedParameterPathV1.UNKNOWN
        )

    def _resolver_ruta_motor_con_provenance(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        herramientas: list[str],
        herramientas_detalle: dict[str, str],
        politica: str,
        tarea: dict | None,
        triage_decision: dict | None = None,
        estado_actual: dict | None = None,
        audit_observation: dict[str, object] | None = None,
        identity_context: IdentityContextV1 | None = None,
    ) -> tuple[ActionResponse, TrustedParameterPathV1]:
        """Usa Capa 1 para resolver/validar acciones fisicas."""
        percepcion = self._extraer_percepcion_motor(evento, contexto_llm)
        root029_context = self._observar_percepcion_root029(percepcion, evento=evento)
        mensaje = str(evento.detalles.get("mensaje", ""))
        triage_info = triage_decision if isinstance(triage_decision, dict) else {}
        intent = str(triage_info.get("intent") or "MOTOR_SIMPLE")
        contexto_motor = self._contexto_motor_estado(
            mensaje,
            estado_actual,
            identity_context.state if identity_context is not None else None,
        )

        diagnostico_motor = self.motor_engine.diagnosticar_comando_usuario(
            mensaje,
            percepcion=percepcion,
            herramientas_disponibles=herramientas,
            intent=intent,
            contexto_motor=contexto_motor,
        )
        if diagnostico_motor["estado"] == "lista":
            accion_motora = self.motor_engine.procesar_diagnostico_motor(
                diagnostico_motor
            )
            response = self._adjuntar_contexto_root029(
                self._action_response_desde_motor(
                    accion_motora,
                    pensamiento="",
                ),
                root029_context,
            )
            return response, self._trusted_path_for_motor_action(
                str(diagnostico_motor.accion or response.accion),
                perception_resolution=diagnostico_motor.resolucion_motor is not None,
                reasoning_generated=False,
            )
        if diagnostico_motor["estado"] in {"falta_objetivo", "ambigua"}:
            return self._adjuntar_contexto_root029(
                self._respuesta_aclaracion_motor(diagnostico_motor),
                root029_context,
            ), TrustedParameterPathV1.ORCHESTRATOR_CONTROL
        if diagnostico_motor["estado"] in {"requires_clarification", "not_found", "not_allowed", "unsafe"}:
            return self._adjuntar_contexto_root029(
                self._respuesta_resolucion_motor(diagnostico_motor),
                root029_context,
            ), TrustedParameterPathV1.ORCHESTRATOR_CONTROL

        try:
            action_response = self.reasoning.process(
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto,
                herramientas=herramientas,
                herramientas_detalle=herramientas_detalle,
                politica=politica,
                tarea=tarea,
                audit_observation=audit_observation,
                identity_context=identity_context,
                current_request=self._current_user_request(evento),
            )
        except Exception as e:
            logger.error("Error en ReasoningEngine para ruta motor: %s", e, exc_info=True)
            if isinstance(audit_observation, dict):
                audit_observation["fallback_used"] = True
                audit_observation.setdefault("decision_error_type", type(e).__name__)
                audit_observation.setdefault("decision_error_component", "reasoning")
                audit_observation.setdefault(
                    "decision_error_observed_ns",
                    time.perf_counter_ns(),
                )
            return self._adjuntar_contexto_root029(ActionResponse(
                pensamiento="Error al razonar accion motora.",
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error=str(e),
                outcome=DecisionOutcome(
                    disposition=DecisionDisposition.ERROR,
                    quality=DecisionQuality.FAILED,
                    attempt=DecisionAttempt.FIRST,
                    layer=DecisionLayer.MOTOR,
                    reason_codes=(DecisionReasonCode.MOTOR_RESOLUTION_FAILED,),
                    retry_count=0,
                ),
            ), root029_context), TrustedParameterPathV1.ORCHESTRATOR_CONTROL

        if action_response.accion in self.motor_engine.ACCIONES_MOTORAS:
            accion_validada = self.motor_engine.procesar_comando_motor(
                action_response.accion,
                action_response.parametros,
                percepcion=percepcion,
                intent=intent,
                contexto_motor=contexto_motor,
            )
            response = self._adjuntar_contexto_root029(
                self._action_response_desde_motor(
                    accion_validada,
                    pensamiento=action_response.pensamiento,
                ),
                root029_context,
            )
            validated_metadata = (
                accion_validada.get("metadata")
                if isinstance(accion_validada, dict)
                and isinstance(accion_validada.get("metadata"), dict)
                else {}
            )
            return response, self._trusted_path_for_motor_action(
                str(response.accion or "none"),
                perception_resolution=isinstance(
                    validated_metadata.get("motor_resolution"),
                    dict,
                ),
                reasoning_generated=True,
            )

        return action_response, TrustedParameterPathV1.UNKNOWN

    def _trusted_path_for_motor_action(
        self,
        action_name: str,
        *,
        perception_resolution: bool,
        reasoning_generated: bool,
    ) -> TrustedParameterPathV1:
        if action_name in MotorEngine.ACCIONES_CON_OBJETO and perception_resolution:
            return TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION
        if reasoning_generated:
            return TrustedParameterPathV1.UNKNOWN
        if action_name == "navegar_a_posicion":
            return TrustedParameterPathV1.MOTOR_EXPLICIT_COORDINATE_PARSER
        if action_name == "girar_avatar":
            return TrustedParameterPathV1.MOTOR_BODY_RELATIVE_PARSER
        if action_name == "soltar_objeto":
            return TrustedParameterPathV1.MOTOR_HELD_OBJECT_PATH
        if action_name in {"cancelar_tarea", "none", "hablar"}:
            return TrustedParameterPathV1.ORCHESTRATOR_CONTROL
        return TrustedParameterPathV1.UNKNOWN

    @staticmethod
    def _extraer_percepcion_motor(evento: EventoEntrante, contexto_llm: dict) -> dict:
        percepcion: dict = {}
        percepcion_contexto = contexto_llm.get("percepcion") if isinstance(contexto_llm, dict) else None
        if isinstance(percepcion_contexto, dict):
            percepcion.update(percepcion_contexto)

        claves_percepcion = {
            "contract_version",
            "source_kind",
            "producer_instance_id",
            "stream_id",
            "producer_epoch_id",
            "logical_world_id",
            "producer_capture_id",
            "generation",
            "captured_at_utc_ms",
            "objects",
            "objetos_visibles",
            "objetos_normalizados",
            "perception_ingestion",
            "resumen_percepcion",
            "objeto_mas_cercano",
            "cantidad_objetos_visibles",
            "posicion_lazzaro",
            "snapshot_id",
            "source",
            "unknown_affordances",
            "estado_fisico",
            "salud_avatar",
            "mensaje_critico",
        }
        for clave in claves_percepcion:
            if clave in evento.detalles:
                percepcion[clave] = evento.detalles[clave]
        return percepcion

    @staticmethod
    def _adjuntar_ingreso_perceptivo(evento: EventoEntrante) -> EventoEntrante:
        """Estampa una sola vez evidencia receiver-owned en el primer boundary."""

        detalles = dict(evento.detalles or {})
        modern_signal = any(
            key in detalles
            for key in (
                "producer_capture_id",
                "producer_epoch_id",
                "captured_at_utc_ms",
                "objects",
                "objetos_normalizados",
            )
        )
        if not modern_signal:
            return evento
        detalles["perception_ingestion"] = {
            "contract_version": 1,
            "local_ingest_id": evento.local_ingest_id,
            "received_at_utc_ms": evento.received_at_utc_ms,
        }
        return evento.model_copy(update={"detalles": detalles})

    def _observar_percepcion_root029(
        self,
        percepcion: dict,
        *,
        evento: EventoEntrante,
    ) -> dict[str, dict]:
        ingestion_raw = (
            percepcion.get("perception_ingestion")
            if isinstance(percepcion, dict)
            and isinstance(percepcion.get("perception_ingestion"), dict)
            else {}
        )
        local_ingest_id = str(
            ingestion_raw.get("local_ingest_id") or evento.local_ingest_id
        )
        received_at = ingestion_raw.get("received_at_utc_ms")
        if type(received_at) is not int:
            received_at = evento.received_at_utc_ms
        ingestion = PerceptionIngestionMetadataV1(
            local_ingest_id=local_ingest_id,
            received_at_utc_ms=received_at,
        )
        parse_receipt = parse_perception_payload(
            percepcion,
            local_ingest_id=local_ingest_id,
        )
        tracker = getattr(self, "perception_lifecycle_tracker", None)
        if not isinstance(tracker, PerceptionLifecycleTracker):
            tracker = PerceptionLifecycleTracker()
            self.perception_lifecycle_tracker = tracker
        lifecycle = tracker.observe_and_classify(parse_receipt)
        clock = getattr(self, "perception_clock", None) or SystemUtcClock()
        self.perception_clock = clock
        now = clock.now_utc_ms()
        evaluator = getattr(self, "perception_freshness_evaluator", None)
        if not isinstance(evaluator, PerceptionFreshnessEvaluator):
            evaluator = PerceptionFreshnessEvaluator()
            self.perception_freshness_evaluator = evaluator
        freshness = evaluator.evaluate(
            parse_receipt,
            ingestion,
            lifecycle,
            policy=getattr(self, "perception_freshness_policy", None),
            evaluated_at_utc_ms=now,
        )
        return {
            "parse": parse_receipt.model_dump(mode="json"),
            "lifecycle": lifecycle.model_dump(mode="json"),
            "freshness": freshness.model_dump(mode="json"),
            "ingestion": ingestion.model_dump(mode="json"),
        }

    @staticmethod
    def _adjuntar_contexto_root029(
        response: ActionResponse,
        context: dict[str, dict],
    ) -> ActionResponse:
        parse_receipt = context.get("parse") if isinstance(context, dict) else None
        if (
            not isinstance(parse_receipt, dict)
            or parse_receipt.get("status") != "CANONICAL"
        ):
            return response
        metadata = dict(response.metadata or {})
        metadata["root029_perception"] = deepcopy(context)
        return response.model_copy(update={"metadata": metadata})

    def _aplicar_autoridad_perceptiva(
        self,
        evento: EventoEntrante,
        action_response: ActionResponse,
        *,
        action_id: str,
        trusted_parameter_path: TrustedParameterPathV1 = TrustedParameterPathV1.UNKNOWN,
        canonical_motor_action: MotorActionV1 | None = None,
    ) -> tuple[ActionResponse, PerceptionAuthorityReferenceV1 | None]:
        """Compone A-E una vez; nunca vuelve a resolver el objetivo."""

        try:
            motor_kind = MotorActionKindV1(action_response.accion)
        except (TypeError, ValueError):
            motor_kind = None
        if motor_kind is not None:
            if canonical_motor_action is None:
                action_response, canonical_motor_action = (
                    self._canonicalizar_action_response_motor(action_response)
                )
            if canonical_motor_action is None:
                return action_response, None
            action_response = action_response.model_copy(
                update={
                    "accion": canonical_motor_action.action.value,
                    "parametros": canonical_motor_action.parameters_dict(),
                }
            )

        action_name = str(action_response.accion or "none")
        metadata = dict(action_response.metadata or {})
        resolution = (
            metadata.get("motor_resolution")
            if isinstance(metadata.get("motor_resolution"), dict)
            else None
        )
        provenance = derive_parameter_provenance(
            action_name,
            trusted_path=trusted_parameter_path,
        )
        metadata["parameter_provenance"] = provenance.model_dump(mode="json")

        if action_name not in MotorEngine.ACCIONES_MOTORAS:
            return action_response.model_copy(update={"metadata": metadata}), None

        context = (
            metadata.get("root029_perception")
            if isinstance(metadata.get("root029_perception"), dict)
            else {}
        )
        parse_receipt = self._root029_model(
            PerceptionParseReceiptV1,
            context.get("parse"),
        )
        lifecycle = self._root029_model(
            PerceptionLifecycleReceiptV1,
            context.get("lifecycle"),
        )
        freshness = self._root029_model(
            PerceptionFreshnessReceiptV1,
            context.get("freshness"),
        )

        runtime_id = (
            str(resolution.get("target_runtime_object_id") or "").strip()
            if resolution
            else ""
        )
        producer_object_id = (
            str(resolution.get("target_producer_object_id") or "").strip()
            if resolution
            else ""
        )
        lifecycle_resolved = bool(
            lifecycle is not None
            and lifecycle.status is LifecycleStatusV1.RESOLVED
        )
        identity = (
            evaluate_perception_identity(
                parse_receipt,
                runtime_object_id=runtime_id or None,
                producer_object_id=producer_object_id or None,
                lifecycle_resolved=lifecycle_resolved,
            )
            if parse_receipt is not None
            else None
        )
        target = self._construir_target_reference(
            parse_receipt,
            runtime_id=runtime_id,
            producer_object_id=producer_object_id,
        )
        affordance = self._affordance_seleccionada(resolution)
        safety = (
            affordance.get("safety_assessment")
            if isinstance(affordance.get("safety_assessment"), dict)
            else {}
        )
        try:
            safety_disposition = (
                SafetyDisposition(str(safety.get("disposition")))
                if safety.get("disposition")
                else None
            )
            safety_reason = (
                SafetyReasonCode(str(safety.get("reason_code")))
                if safety.get("reason_code")
                else None
            )
        except ValueError:
            safety_disposition = None
            safety_reason = None
        interaction_type = (
            str(resolution.get("interaction_type") or "").strip() or None
            if resolution
            else None
        )
        # ROOT-042 ya valido y normalizo la estructura antes de componer
        # autoridad semantica ROOT-029. Este evaluator nunca descubre pares
        # action/params malformados.
        assert canonical_motor_action is not None
        bound_parameters = canonical_motor_action.parameters_dict()
        schema_valid = True
        candidate = PhysicalActionCandidateV1(
            id_accion=action_id,
            action_name=action_name,
            canonical_parameters_json=canonical_json(
                bound_parameters
            ),
            parameter_provenance=provenance,
            action_class=physical_action_class(action_name, interaction_type),
            target_reference=target,
            interaction_type=interaction_type,
            grounding_status=(
                str(resolution.get("status") or "").strip() or None
                if resolution
                else None
            ),
            affordance_allowed=(
                affordance.get("allowed")
                if type(affordance.get("allowed")) is bool
                else None
            ),
            safety_disposition=safety_disposition,
            safety_reason_code=safety_reason,
            schema_valid=schema_valid,
        )
        authority_evaluator = getattr(self, "perception_authority_evaluator", None)
        if not isinstance(authority_evaluator, PerceptionAuthorityEvaluator):
            authority_evaluator = PerceptionAuthorityEvaluator()
            self.perception_authority_evaluator = authority_evaluator
        receipt, authorized = authority_evaluator.evaluate(
            candidate,
            parse_receipt=parse_receipt,
            identity_receipt=identity,
            lifecycle_receipt=lifecycle,
            freshness_receipt=freshness,
            policy=getattr(self, "perception_authority_policy", None),
        )
        metadata["perception_authority_receipt"] = receipt.model_dump(mode="json")
        if receipt.status is AuthorityStatusV1.NOT_APPLICABLE:
            return action_response.model_copy(update={"metadata": metadata}), None
        if authorized is not None and receipt.status is AuthorityStatusV1.AUTHORIZED:
            if not authorized.binding_is_intact():
                return self._bloquear_por_autoridad(
                    action_response,
                    metadata,
                    "BINDING_MISMATCH",
                ), None
            return (
                action_response.model_copy(
                    update={
                        "parametros": authorized.parameters_copy(),
                        "metadata": metadata,
                    }
                ),
                authorized.authority_reference,
            )
        return (
            self._bloquear_por_autoridad(
                action_response,
                metadata,
                receipt.reason_code.value,
            ),
            None,
        )

    @staticmethod
    def _root029_model(model_cls, value):
        if not isinstance(value, dict):
            return None
        try:
            return model_cls.model_validate(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _construir_target_reference(
        parse_receipt: PerceptionParseReceiptV1 | None,
        *,
        runtime_id: str,
        producer_object_id: str,
    ) -> TargetReferenceV1 | None:
        data = parse_receipt.data if parse_receipt is not None else None
        if data is None or not runtime_id:
            return None
        try:
            return TargetReferenceV1(
                producer_instance_id=data.producer_instance_id,
                stream_id=data.stream_id,
                producer_epoch_id=data.producer_epoch_id,
                logical_world_id=data.logical_world_id,
                producer_capture_id=data.producer_capture_id,
                generation=data.generation,
                producer_object_id=producer_object_id or None,
                runtime_object_id=runtime_id,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _affordance_seleccionada(resolution: dict | None) -> dict:
        if not isinstance(resolution, dict):
            return {}
        target_id = resolution.get("target_id")
        candidates = resolution.get("candidates")
        if not isinstance(candidates, list):
            return {}
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("target_id") != target_id:
                continue
            decision = candidate.get("affordance_decision")
            return decision if isinstance(decision, dict) else {}
        return {}

    @staticmethod
    def _bloquear_por_autoridad(
        response: ActionResponse,
        metadata: dict,
        reason_code: str,
    ) -> ActionResponse:
        metadata["perception_authority_block"] = reason_code
        outcome_updates = {
            "disposition": DecisionDisposition.BLOCKED,
            "reason_codes": (DecisionReasonCode.UNSAFE_BLOCK,),
        }
        if response.outcome.layer is DecisionLayer.MOTOR:
            outcome_updates["motor_status"] = MotorOutcomeStatus.UNSAFE
        return response.model_copy(
            update={
                "accion": "none",
                "parametros": {},
                "razon": "Autoridad perceptiva ROOT-029 no satisfecha.",
                "valido": True,
                "error": None,
                "metadata": metadata,
                "outcome": response.outcome.evolve(**outcome_updates),
            }
        )

    def _contexto_motor_estado(
        self,
        mensaje: str,
        estado_actual: dict | None = None,
        identity_state: object | None = None,
    ) -> dict:
        contexto: dict[str, float | str | dict] = {"user_query": mensaje}
        estado: dict = dict(estado_actual) if isinstance(estado_actual, dict) else {}
        if identity_state is not None:
            for key in ("energia", "estres", "foco"):
                value = getattr(identity_state, key, None)
                if value is not None:
                    estado[key] = value

        energia = self._leer_ratio_estado(estado, "energia")
        if energia is not None:
            contexto["fatiga"] = round(max(0.0, min(1.0, 1.0 - energia)), 4)

        estres = self._leer_ratio_estado(estado, "estres", "stress")
        if estres is not None:
            contexto["estres"] = round(estres, 4)

        curiosidad = self._leer_ratio_estado(estado, "curiosidad", "curiosity")
        if curiosidad is not None:
            contexto["curiosidad"] = round(curiosidad, 4)

        contexto["estado"] = {
            key: value
            for key, value in contexto.items()
            if key in {"fatiga", "estres", "curiosidad"}
        }
        return contexto

    @staticmethod
    def _leer_ratio_estado(estado: dict, *claves: str) -> float | None:
        for clave in claves:
            if clave not in estado:
                continue
            try:
                valor = float(estado[clave])
            except (TypeError, ValueError):
                continue
            if valor > 1.0:
                valor = valor / 100.0
            return max(0.0, min(1.0, valor))
        return None

    @staticmethod
    def _action_response_desde_motor(
        motor_action: dict,
        pensamiento: str,
    ) -> ActionResponse:
        if not isinstance(motor_action, dict):
            motor_action = {
                "accion": "none",
                "parametros": {},
                "razon": "Capa 1 devolvio una respuesta invalida.",
            }

        accion = str(motor_action.get("accion", "none"))
        metadata = motor_action.get("metadata", {}) if isinstance(motor_action.get("metadata"), dict) else {}
        resolution = metadata.get("motor_resolution") if isinstance(metadata.get("motor_resolution"), dict) else {}
        status = str(resolution.get("status") or "").strip()
        known_block = status in {"requires_clarification", "not_found", "not_allowed", "unsafe"}
        valido = accion != "none" or known_block
        if accion != "none":
            disposition = DecisionDisposition.ACTION
            quality = DecisionQuality.VALIDATED
            reason = DecisionReasonCode.MOTOR_READY
            motor_status = MotorOutcomeStatus.READY
        elif known_block:
            disposition = (
                DecisionDisposition.CLARIFICATION
                if status == "requires_clarification"
                else DecisionDisposition.BLOCKED
            )
            quality = DecisionQuality.VALIDATED
            reason = {
                "requires_clarification": DecisionReasonCode.MOTOR_CLARIFICATION,
                "not_found": DecisionReasonCode.TARGET_NOT_FOUND,
                "not_allowed": DecisionReasonCode.ACTION_NOT_ALLOWED,
                "unsafe": DecisionReasonCode.UNSAFE_BLOCK,
            }[status]
            motor_status = MotorOutcomeStatus(status)
        else:
            disposition = DecisionDisposition.INVALID
            quality = DecisionQuality.REJECTED
            reason = DecisionReasonCode.INVALID_PARAMETERS
            motor_status = None
        return ActionResponse(
            pensamiento=pensamiento,
            accion=accion,
            parametros=motor_action.get("parametros", {}) if isinstance(motor_action, dict) else {},
            razon=str(motor_action.get("razon", "")),
            valido=valido,
            error=None if valido else str(motor_action.get("razon", "Accion bloqueada por Capa 1.")),
            metadata=metadata,
            outcome=DecisionOutcome(
                disposition=disposition,
                quality=quality,
                attempt=DecisionAttempt.FIRST,
                layer=DecisionLayer.MOTOR,
                reason_codes=(reason,),
                retry_count=0,
                motor_status=motor_status,
            ),
        )

    @staticmethod
    def _respuesta_aclaracion_motor(diagnostico_motor: dict) -> ActionResponse:
        accion = str(diagnostico_motor.get("accion", "")).strip()
        mensajes = {
            "navegar_a_objeto": "Dime a que objeto quieres que vaya.",
            "alejarse_de_objeto": "Dime de que objeto quieres que me aleje.",
            "interactuar_objeto_3d": "Dime con que objeto quieres que interactue.",
            "agarrar_objeto": "Dime que objeto quieres que agarre.",
            "aplicar_fuerza": "Dime a que objeto quieres que le aplique fuerza.",
        }
        texto = mensajes.get(accion, "Dime que objeto quieres que use para esa accion.")
        return ActionResponse(
            pensamiento="Comando motor con objetivo incompleto; pido aclaracion antes de actuar.",
            accion="hablar",
            parametros={"texto_a_comunicar": texto},
            razon="Capa 1 bloqueo un comando motor ambiguo para evitar alucinaciones fisicas.",
            valido=True,
            outcome=DecisionOutcome(
                disposition=DecisionDisposition.CLARIFICATION,
                quality=DecisionQuality.VALIDATED,
                attempt=DecisionAttempt.FIRST,
                layer=DecisionLayer.MOTOR,
                reason_codes=(
                    DecisionReasonCode.AMBIGUOUS_TARGET
                    if str(diagnostico_motor.get("estado")) == "ambigua"
                    else DecisionReasonCode.MISSING_TARGET,
                ),
                retry_count=0,
                motor_status=MotorOutcomeStatus.REQUIRES_CLARIFICATION,
            ),
        )

    @staticmethod
    def _respuesta_resolucion_motor(diagnostico_motor: dict) -> ActionResponse:
        estado = str(diagnostico_motor.get("estado", "")).strip()
        accion = str(diagnostico_motor.get("accion", "")).strip()
        metadata = diagnostico_motor.get("metadata", {}) if isinstance(diagnostico_motor.get("metadata"), dict) else {}
        resolucion = metadata.get("motor_resolution", {}) if isinstance(metadata.get("motor_resolution"), dict) else {}
        candidatos = resolucion.get("candidates", []) if isinstance(resolucion, dict) else []
        correccion = resolucion.get("candidate_correction") if isinstance(resolucion, dict) else None

        if estado == "requires_clarification":
            if isinstance(correccion, dict):
                candidato = Orchestrator._candidato_de_correccion_motor(candidatos, correccion)
                descripcion = Orchestrator._descripcion_candidato_motor(candidato) if candidato else ""
                if not descripcion:
                    descripcion = str(
                        correccion.get("suggested_display_name")
                        or correccion.get("legacy_nombre_objeto")
                        or correccion.get("suggested_id")
                        or ""
                    ).strip()
                solicitado = str(correccion.get("requested") or "").strip()
                if solicitado and descripcion:
                    texto = f"No veo \"{solicitado}\" como objeto exacto. Quisiste decir {descripcion}?"
                elif descripcion:
                    texto = f"Quisiste decir {descripcion}?"
                else:
                    texto = "No veo ese objeto exacto. Quieres confirmar el candidato sugerido?"
                return ActionResponse(
                    pensamiento="Capa 1 detecto una posible correccion de objetivo y pide confirmacion antes de actuar.",
                    accion="hablar",
                    parametros={"texto_a_comunicar": texto},
                    razon="Capa 1 propuso una correccion candidata sin ejecutar accion fisica.",
                    valido=True,
                    metadata=metadata,
                    outcome=Orchestrator._outcome_resolucion_motor(estado, metadata),
                )

            descripciones = [
                Orchestrator._descripcion_candidato_motor(candidato)
                for candidato in candidatos
                if isinstance(candidato, dict)
            ]
            descripciones = [descripcion for descripcion in descripciones if descripcion][:3]
            if len(descripciones) >= 2:
                texto = f"Veo {', '.join(descripciones)}. Cual quieres?"
            else:
                texto = "No estoy segura de a que objeto te refieres. Cual quieres?"
            razon = "Capa 1 detecto candidatos ambiguos y pidio aclaracion."
            return ActionResponse(
                pensamiento="Comando motor ambiguo; Capa 1 pide aclaracion antes de actuar.",
                accion="hablar",
                parametros={"texto_a_comunicar": texto},
                razon=razon,
                valido=True,
                metadata=metadata,
                outcome=Orchestrator._outcome_resolucion_motor(estado, metadata),
            )

        if estado == "not_allowed":
            texto = "Ese objeto no permite esa accion."
            razon = "Capa 1 bloqueo la accion porque el objeto no tiene affordance compatible."
            return ActionResponse(
                pensamiento="La accion no aplica al objeto seleccionado.",
                accion="hablar",
                parametros={"texto_a_comunicar": texto},
                razon=razon,
                valido=True,
                metadata=metadata,
                outcome=Orchestrator._outcome_resolucion_motor(estado, metadata),
            )

        if estado == "not_found":
            texto = "No veo ese objeto en mi percepcion actual."
            razon = "Capa 1 no encontro un candidato perceptivo suficiente."
            return ActionResponse(
                pensamiento="No hay objeto perceptivo suficiente para ejecutar.",
                accion="hablar",
                parametros={"texto_a_comunicar": texto},
                razon=razon,
                valido=True,
                metadata=metadata,
                outcome=Orchestrator._outcome_resolucion_motor(estado, metadata),
            )

        return ActionResponse(
            pensamiento="Capa 1 bloqueo una accion potencialmente insegura.",
            accion="none",
            parametros={},
            razon=f"Resolucion motora '{estado or accion}' bloqueada por seguridad.",
            valido=True,
            error=None,
            metadata=metadata,
            outcome=Orchestrator._outcome_resolucion_motor(estado or "unsafe", metadata),
        )

    @staticmethod
    def _outcome_resolucion_motor(
        estado: str,
        metadata: dict | None = None,
    ) -> DecisionOutcome:
        audit = (
            metadata.get("motor_audit", {})
            if isinstance(metadata, dict)
            and isinstance(metadata.get("motor_audit"), dict)
            else {}
        )
        if audit.get("status_reason") == "resolver_exception":
            return DecisionOutcome(
                disposition=DecisionDisposition.ERROR,
                quality=DecisionQuality.DETERMINISTIC_FALLBACK,
                attempt=DecisionAttempt.FIRST,
                layer=DecisionLayer.MOTOR,
                reason_codes=(DecisionReasonCode.MOTOR_RESOLUTION_FAILED,),
                retry_count=0,
            )
        status = {
            "requires_clarification": MotorOutcomeStatus.REQUIRES_CLARIFICATION,
            "not_found": MotorOutcomeStatus.NOT_FOUND,
            "not_allowed": MotorOutcomeStatus.NOT_ALLOWED,
            "unsafe": MotorOutcomeStatus.UNSAFE,
        }.get(estado, MotorOutcomeStatus.UNSAFE)
        disposition = (
            DecisionDisposition.CLARIFICATION
            if status is MotorOutcomeStatus.REQUIRES_CLARIFICATION
            else DecisionDisposition.BLOCKED
        )
        reason = {
            MotorOutcomeStatus.REQUIRES_CLARIFICATION: DecisionReasonCode.MOTOR_CLARIFICATION,
            MotorOutcomeStatus.NOT_FOUND: DecisionReasonCode.TARGET_NOT_FOUND,
            MotorOutcomeStatus.NOT_ALLOWED: DecisionReasonCode.ACTION_NOT_ALLOWED,
            MotorOutcomeStatus.UNSAFE: DecisionReasonCode.UNSAFE_BLOCK,
        }[status]
        return DecisionOutcome(
            disposition=disposition,
            quality=DecisionQuality.VALIDATED,
            attempt=DecisionAttempt.FIRST,
            layer=DecisionLayer.MOTOR,
            reason_codes=(reason,),
            retry_count=0,
            motor_status=status,
        )

    @staticmethod
    def _descripcion_candidato_motor(candidato: dict) -> str:
        nombre = str(
            candidato.get("display_name")
            or candidato.get("legacy_nombre_objeto")
            or candidato.get("target_id")
            or ""
        ).strip()
        if not nombre:
            return ""

        distancia = candidato.get("distance")
        if distancia is not None:
            try:
                return f"{nombre} a {float(distancia):0.1f}m"
            except (TypeError, ValueError):
                return nombre

        posicion = candidato.get("position")
        if isinstance(posicion, dict):
            x = posicion.get("x")
            z = posicion.get("z")
            try:
                return f"{nombre} en x={float(x):0.1f}, z={float(z):0.1f}"
            except (TypeError, ValueError):
                return nombre

        return nombre

    @staticmethod
    def _candidato_de_correccion_motor(candidatos: list, correccion: dict) -> dict | None:
        suggested_id = str(correccion.get("suggested_id") or "").strip()
        suggested_name = str(correccion.get("suggested_display_name") or "").strip()
        for candidato in candidatos:
            if not isinstance(candidato, dict):
                continue
            ids = {
                str(candidato.get("target_id") or "").strip(),
                str(candidato.get("display_name") or "").strip(),
                str(candidato.get("legacy_nombre_objeto") or "").strip(),
            }
            if suggested_id in ids or suggested_name in ids:
                return candidato
        return None

    def _obtener_lock_descanso_terminal(self) -> threading.RLock:
        """Crea perezosamente el lock para conservar compatibilidad con fakes aislados."""
        lock = getattr(self, "_descanso_terminal_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._descanso_terminal_lock = lock
        return lock

    def _obtener_behavior_owner(self):
        """Devuelve el owner canonico que serializa mutaciones biologicas."""
        return getattr(self, "behavior", None)

    @classmethod
    def _es_comando_terminal(cls, evento: EventoEntrante) -> bool:
        return cls._es_canal_terminal(evento) and evento.tipo_evento == "comando_usuario"

    def _consumir_descanso_terminal_si_corresponde(
        self,
        evento: EventoEntrante,
    ) -> bool | None:
        """Despierta una sola vez antes de procesar el siguiente comando nuevo del terminal."""
        if not self._es_comando_terminal(evento):
            return None

        lock = self._obtener_lock_descanso_terminal()
        with lock:
            pendiente = getattr(self, "_descanso_terminal_pendiente", None)
            if not isinstance(pendiente, dict):
                return None

            transaction_id = str(evento.id_transaccion)
            if transaction_id == str(pendiente.get("transaction_id", "")):
                return None

            state_owner = self._obtener_behavior_owner()
            finalizar = getattr(state_owner, "finalizar_descanso_biologico", None)
            if not callable(finalizar):
                logger.error(
                    "event=terminal_rest_wake transaction_id=%s status=error error_type=missing_state_engine",
                    transaction_id,
                )
                return False

            try:
                resultado = finalizar()
            except Exception as exc:
                logger.error(
                    "event=terminal_rest_wake transaction_id=%s status=error error_type=%s",
                    transaction_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                return False

            self._descanso_terminal_pendiente = None
            logger.info(
                "event=terminal_rest_wake transaction_id=%s status=%s seconds=%.2f energy_recovered=%.2f",
                transaction_id,
                "applied" if bool(resultado.get("aplicado")) else "not_active",
                float(resultado.get("segundos", 0.0)),
                float(resultado.get("energia_recuperada", 0.0)),
            )
            return True

    def _armar_descanso_terminal_si_corresponde(
        self,
        evento: EventoEntrante,
        triage_decision: dict,
    ) -> bool | None:
        """Arma recuperacion diferida cuando el Random Forest detecta ``CORE_SYSTEM``."""
        if not self._es_comando_terminal(evento):
            return None
        if str(triage_decision.get("intent", "")).strip().upper() != "CORE_SYSTEM":
            return None

        transaction_id = str(evento.id_transaccion)
        lock = self._obtener_lock_descanso_terminal()
        with lock:
            if isinstance(getattr(self, "_descanso_terminal_pendiente", None), dict):
                logger.info(
                    "event=terminal_rest_arm transaction_id=%s status=already_armed",
                    transaction_id,
                )
                return None

            state_owner = self._obtener_behavior_owner()
            iniciar = getattr(state_owner, "iniciar_descanso_biologico", None)
            if not callable(iniciar):
                logger.error(
                    "event=terminal_rest_arm transaction_id=%s status=error error_type=missing_state_engine",
                    transaction_id,
                )
                return False

            try:
                iniciado = bool(iniciar())
            except Exception as exc:
                logger.error(
                    "event=terminal_rest_arm transaction_id=%s status=error error_type=%s",
                    transaction_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                return False

            if not iniciado and not bool(getattr(state_owner, "descansando", False)):
                logger.warning(
                    "event=terminal_rest_arm transaction_id=%s status=not_started",
                    transaction_id,
                )
                return False

            self._descanso_terminal_pendiente = {"transaction_id": transaction_id}
            logger.info(
                "event=terminal_rest_arm transaction_id=%s status=armed",
                transaction_id,
            )
            return True

    def _registrar_estado_identidad(
        self,
        evento: EventoEntrante,
        triage_decision: dict,
        action_response: ActionResponse,
        *,
        execution_success: bool | None = None,
    ) -> bool:
        """Actualiza energia, estres y enfoque despues de procesar el evento."""
        try:
            route = str(triage_decision.get("route", "")).strip().lower()

            if evento.tipo_evento == "alerta_dolor_fisico":
                route = "motor"

            es_descanso = self._evento_es_descanso(evento, action_response)
            if es_descanso:
                try:
                    self.memory.consolidar_memorias_si_hace_falta()
                except Exception as e:
                    logger.error("Error consolidando memorias durante descanso: %s", e, exc_info=True)

            self.behavior.registrar_impacto_identidad(
                route=route,
                success=execution_success,
                es_descanso=es_descanso,
                intensidad=self._intensidad_identidad(evento, route),
            )
            return True
        except Exception as e:
            logger.error("Error actualizando IdentityState: %s", e, exc_info=True)
            return False

    @classmethod
    def _evento_es_descanso(
        cls,
        evento: EventoEntrante,
        action_response: ActionResponse,
    ) -> bool:
        if bool((action_response.metadata or {}).get("manual_memory_save_control")):
            return False
        if cls._es_canal_terminal(evento):
            return False

        texto = " ".join(
            str(parte or "")
            for parte in (
                evento.detalles.get("mensaje"),
                evento.detalles.get("mensaje_resultado"),
                action_response.parametros.get("texto_a_comunicar")
                if isinstance(action_response.parametros, dict)
                else "",
            )
        ).lower()
        return any(token in texto for token in ("descansa", "dormir", "duerme", "reposar"))

    @staticmethod
    def _intensidad_identidad(evento: EventoEntrante, route: str) -> float:
        if evento.tipo_evento in {"actualizacion_estado", "resultado_accion"}:
            return 0.2
        if evento.tipo_evento == "alerta_dolor_fisico":
            return 1.4
        if route == "oracle":
            return 1.2
        return 1.0

    def obtener_estado_publico(self) -> EstadoAgente:
        """Devuelve un snapshot del estado interno para el frontend."""
        resumen = self.behavior.obtener_resumen()
        estado = resumen.get("estado", {})
        personalidad = resumen.get("personalidad", {})
        tarea = resumen.get("tarea")

        objetivo = None
        if tarea:
            objetivo = tarea.get("objetivo") or tarea.get("objetivo_id")

        return EstadoAgente(
            dopamina=estado.get("confianza", 50),
            estres=estado.get("estres", 0),
            energia=estado.get("energia", 100),
            confianza=estado.get("confianza", 0),
            motivacion=estado.get("motivacion", 0),
            foco=estado.get("foco", 70),
            personalidad=personalidad,
            tarea_actual=objetivo,
        )

    def obtener_notificaciones(self) -> List[NotificacionSaliente]:
        """Devuelve y limpia las notificaciones pendientes."""
        with self._obtener_lock_recurso("_notificaciones_lock"):
            notificaciones = list(self._notificaciones)
            self._notificaciones.clear()
            return notificaciones

    def _obtener_action_ledger(self) -> ActionLedger:
        """Crea un ledger efimero solo para fakes legacy sin ``__init__``."""
        ledger = getattr(self, "action_ledger", None)
        if ledger is None:
            ledger = ActionLedger(":memory:")
            self.action_ledger = ledger
        return ledger

    def _iniciar_efecto_uow(
        self,
        transaction_id: str,
        effect_key: str,
        *,
        effect_kind: str,
        phase: str,
        required: bool,
    ) -> None:
        start_effect = getattr(self._obtener_action_ledger(), "start_turn_effect", None)
        if not callable(start_effect):
            return
        start_effect(
            str(transaction_id),
            effect_key,
            effect_kind=effect_kind,
            phase=phase,
            required=required,
        )

    def _cerrar_efecto_uow(
        self,
        transaction_id: str,
        effect_key: str,
        status: TurnEffectStatus,
    ) -> None:
        finish_effect = getattr(self._obtener_action_ledger(), "finish_turn_effect", None)
        if callable(finish_effect):
            finish_effect(str(transaction_id), effect_key, status)

    def _finalizar_uow_por_fallo(
        self,
        transaction_id: str,
        exc: Exception,
    ) -> TurnUoWStatus | None:
        """Clasifica el intento sin reejecutar ni compensar efectos externos."""
        ledger = self._obtener_action_ledger()
        get_uow = getattr(ledger, "get_turn_uow", None)
        get_effects = getattr(ledger, "get_turn_effects", None)
        fail_uow = getattr(ledger, "fail_turn_uow", None)
        if not all(callable(method) for method in (get_uow, get_effects, fail_uow)):
            return None
        uow = get_uow(str(transaction_id))
        if uow is None or uow.status != TurnUoWStatus.OPEN:
            return None
        effects = get_effects(str(transaction_id))
        if any(effect.status == TurnEffectStatus.STARTED for effect in effects):
            status = TurnUoWStatus.RECONCILIATION_REQUIRED
        elif any(
            effect.status == TurnEffectStatus.APPLIED
            and effect.effect_kind in {"decision", "execution", "outcome"}
            for effect in effects
        ):
            status = TurnUoWStatus.PARTIAL
        else:
            status = TurnUoWStatus.ABORTED_CLEAN
        failure_class = getattr(exc, "failure_class", None) or type(exc).__name__
        failed = fail_uow(
            str(transaction_id),
            status=status,
            failure_class=str(failure_class),
        )
        return failed.status

    def _registrar_outcome_entrante(self, evento: EventoEntrante):
        detalles = evento.detalles if isinstance(evento.detalles, dict) else {}
        ledger = self._obtener_action_ledger()
        action_id = str(detalles.get("id_accion") or "")
        action = ledger.get_action(action_id)
        outcome_payload_details = detalles
        if action is not None:
            attestation = validate_material_attestation(
                action_name=action.action_name,
                action_id=action.action_id,
                success=detalles.get("exito"),
                payload=detalles,
            )
            if not attestation.valid:
                logger.warning(
                    "Outcome material rechazado antes del ledger. "
                    "action_id=%s validation=%s",
                    action.action_id,
                    attestation.status.value,
                )
                return OutcomeRecordResult(
                    OutcomeRecordStatus.INVALID_ATTESTATION,
                    action,
                    None,
                )
            outcome_payload_details = dict(detalles)
            if attestation.receipt is None:
                outcome_payload_details.pop("material_attestation", None)
            else:
                outcome_payload_details["material_attestation"] = (
                    attestation.receipt.to_payload()
                )
        return ledger.record_outcome(
            action_id=action_id,
            outcome_transaction_id=str(evento.id_transaccion),
            success=detalles.get("exito"),
            action_name=str(detalles.get("accion") or "") or None,
            originating_transaction_id=(
                str(detalles.get("id_transaccion_origen") or "").strip() or None
            ),
            payload=self._payload_outcome_minimo(outcome_payload_details),
        )

    def _validar_replay_lifecycle(self, evento: EventoEntrante) -> None:
        """Valida contenido de un receipt repetido sin repetir su side effect."""
        detalles = evento.detalles if isinstance(evento.detalles, dict) else {}
        event = (
            ActionTransitionEvent.AUTHORIZE
            if evento.tipo_evento == "confirmacion_accion" and detalles.get("approved") is True
            else ActionTransitionEvent.REJECT
            if evento.tipo_evento == "confirmacion_accion"
            else ActionTransitionEvent.START_EXECUTION
        )
        result = self._obtener_action_ledger().transition_action(
            str(detalles.get("id_accion") or ""),
            event,
            receipt_id=f"lifecycle:{evento.id_transaccion}",
        )
        if result.classification == TransitionClassification.CONFLICT:
            raise TransactionConflictError(
                f"Receipt lifecycle reutilizado con otro contenido: {evento.id_transaccion}"
            )

    def _validar_replay_outcome(self, evento: EventoEntrante) -> None:
        """Valida el receipt durable de outcome sin repetir consumidores ni writes."""

        detalles = evento.detalles if isinstance(evento.detalles, dict) else {}
        ledger = self._obtener_action_ledger()
        action_id = str(detalles.get("id_accion") or "")
        action = ledger.get_action(action_id)
        material_attestation_valid = True
        outcome_payload_details = detalles
        if action is not None:
            attestation = validate_material_attestation(
                action_name=action.action_name,
                action_id=action.action_id,
                success=detalles.get("exito"),
                payload=detalles,
            )
            if not attestation.valid:
                material_attestation_valid = False
            else:
                outcome_payload_details = dict(detalles)
                if attestation.receipt is None:
                    outcome_payload_details.pop("material_attestation", None)
                else:
                    outcome_payload_details["material_attestation"] = (
                        attestation.receipt.to_payload()
                    )

        validation = ledger.validate_outcome_replay(
            action_id=action_id,
            outcome_transaction_id=str(evento.id_transaccion),
            success=detalles.get("exito"),
            action_name=str(detalles.get("accion") or "") or None,
            originating_transaction_id=(
                str(detalles.get("id_transaccion_origen") or "").strip() or None
            ),
            payload=self._payload_outcome_minimo(outcome_payload_details),
        )
        if validation.status is OutcomeReplayStatus.MISSING_RECEIPT:
            raise TurnReconciliationRequiredError(
                f"Outcome completado sin receipt durable: {evento.id_transaccion}"
            )
        if (
            validation.status is OutcomeReplayStatus.CONFLICT
            or not material_attestation_valid
        ):
            raise TransactionConflictError(
                f"Receipt de outcome reutilizado con otro contenido: {evento.id_transaccion}"
            )

    def _procesar_inicio_ejecucion_accion(self, evento: EventoEntrante):
        detalles = evento.detalles if isinstance(evento.detalles, dict) else {}
        result = self._obtener_action_ledger().transition_action(
            str(detalles.get("id_accion") or ""),
            ActionTransitionEvent.START_EXECUTION,
            receipt_id=f"lifecycle:{evento.id_transaccion}",
        )
        if result.classification == TransitionClassification.CONFLICT:
            raise TransactionConflictError("Receipt de inicio reutilizado con otro contenido")
        if result.classification not in {
            TransitionClassification.APPLIED,
            TransitionClassification.ALREADY_APPLIED,
            TransitionClassification.UNKNOWN_ACTION,
            TransitionClassification.INVALID_TRANSITION,
        }:
            raise ActionLedgerError(
                f"Receipt de inicio no clasificable: {result.classification.value}"
            )
        return result

    def _procesar_confirmacion_accion(
        self,
        evento: EventoEntrante,
    ) -> AccionSaliente | None:
        detalles = evento.detalles if isinstance(evento.detalles, dict) else {}
        approved = detalles.get("approved")
        transition_event = (
            ActionTransitionEvent.AUTHORIZE
            if approved is True
            else ActionTransitionEvent.REJECT
        )
        ledger = self._obtener_action_ledger()
        result = ledger.transition_action(
            str(detalles.get("id_accion") or ""),
            transition_event,
            receipt_id=f"lifecycle:{evento.id_transaccion}",
        )
        if result.classification == TransitionClassification.CONFLICT:
            raise TransactionConflictError("Receipt de confirmacion reutilizado con otro contenido")
        if result.classification != TransitionClassification.APPLIED:
            return None

        record = ledger.get_action(result.action_id)
        if record is None:
            return None
        if approved is not True:
            return self._accion_desde_record(record)

        if record.intended_client != "sandbox":
            dispatch = ledger.transition_action(
                record.action_id,
                ActionTransitionEvent.DISPATCH,
                receipt_id=f"confirmation-dispatch:{evento.id_transaccion}",
            )
            self._exigir_transicion(dispatch, "dispatch_after_confirmation")
            updated = ledger.get_action(record.action_id)
            return self._accion_desde_record(updated or record)

        start = ledger.transition_action(
            record.action_id,
            ActionTransitionEvent.START_EXECUTION,
            receipt_id=f"confirmation-start:{evento.id_transaccion}",
        )
        self._exigir_transicion(start, "local_start_after_confirmation")
        permit = ledger.issue_execution_permit(record.action_id)
        if permit is None:
            raise ActionLedgerError("Confirmacion local no produjo execution permit")
        action = self._accion_desde_record(ledger.get_action(record.action_id) or record)
        tool_result = ActionExecutor.ejecutar(action, permit=permit)
        details = {
            "mensaje_resultado": tool_result.mensaje,
            "error": tool_result.error,
            "datos_extra": tool_result.datos_extra,
        }
        outcome = ledger.record_outcome(
            action_id=record.action_id,
            outcome_transaction_id=f"confirmation-outcome:{evento.id_transaccion}",
            success=tool_result.exito is True,
            action_name=record.action_name,
            originating_transaction_id=record.transaction_id,
            payload=self._payload_outcome_minimo(details),
        )
        if outcome.status != OutcomeRecordStatus.ACCEPTED or outcome.action is None:
            raise ActionLedgerError(
                f"Outcome local confirmado rechazado: {outcome.status.value}"
            )
        self._aplicar_outcome_local_a_behavior(
            outcome.action,
            tool_result.exito is True,
            details,
        )
        self.behavior.registrar_impacto_identidad(
            route="motor",
            success=tool_result.exito is True,
            intensidad=0.2,
        )
        self.memory.agregar_accion(
            accion=record.action_name,
            resultado="ok" if tool_result.exito is True else "error",
            detalles={"id_accion": record.action_id, **details},
        )
        if tool_result.exito is True:
            self._aprobar_alias_pendiente_por_id(record.action_id)
        consumed = ledger.mark_outcome_consumed(record.action_id)
        if consumed != OutcomeConsumptionStatus.CONSUMED:
            raise ActionLedgerError("Outcome local confirmado no pudo consumirse")
        updated = ledger.get_action(record.action_id)
        return self._accion_desde_record(updated or record)

    @staticmethod
    def _payload_outcome_minimo(detalles: dict) -> dict:
        permitidos = {
            "mensaje_resultado",
            "objeto",
            "nombre_objeto",
            "target",
            "object_id",
            "movimiento_finalizado",
            "distancia_objeto",
            "fuerza_lanzamiento",
            "fuerza",
            "vector_x",
            "vector_y",
            "vector_z",
            "grados",
            "x",
            "y",
            "z",
        }
        payload = {
            key: value
            for key, value in detalles.items()
            if key in permitidos
        }
        material_attestation = canonical_material_attestation(
            detalles.get("material_attestation")
        )
        if material_attestation is not None:
            payload["material_attestation"] = material_attestation
        return payload

    @staticmethod
    def _correlacionar_evento_outcome(
        evento: EventoEntrante,
        action: ActionRecord,
    ) -> EventoEntrante:
        correlacionado = evento.model_copy(deep=True)
        detalles = dict(correlacionado.detalles or {})
        detalles["accion"] = action.action_name
        detalles["id_accion"] = action.action_id
        detalles["id_transaccion_origen"] = action.transaction_id
        detalles["_task_correlation_enforced"] = True
        detalles["_task_instance_id_correlacion"] = action.task_instance_id
        correlacionado.detalles = detalles
        return correlacionado

    def _aplicar_outcome_local_a_behavior(
        self,
        action: ActionRecord,
        success: bool,
        details: dict,
    ) -> None:
        """Proyecta una vez el ToolResult terminal sin reconstruir el comando."""
        outcome_details = dict(self._payload_outcome_minimo(details))
        outcome_details.update(
            {
                "accion": action.action_name,
                "id_accion": action.action_id,
                "id_transaccion_origen": action.transaction_id,
                "exito": success,
                "_task_correlation_enforced": True,
                "_task_instance_id_correlacion": action.task_instance_id,
            }
        )
        self.behavior.procesar_evento("resultado_accion", outcome_details)

    def _persistir_identidad_accion(
        self,
        evento: EventoEntrante,
        accion: AccionSaliente,
        *,
        task_instance_id: str | None,
        decision_valid: bool | None = None,
    ) -> None:
        task_instance_id = str(task_instance_id or "").strip() or None
        action_name = accion.nombre
        parameters = dict(accion.parametros or {})
        try:
            MotorActionKindV1(action_name)
        except (TypeError, ValueError):
            pass
        else:
            canonical = MotorActionAdapterV1.require(action_name, parameters)
            action_name = canonical.action.value
            parameters = canonical.parameters_dict()
        receipt = self._obtener_action_ledger().record_action(
            action_id=accion.id_accion,
            transaction_id=str(evento.id_transaccion),
            action_name=action_name,
            parameters=parameters,
            requires_confirmation=accion.requiere_confirmacion,
            task_instance_id=task_instance_id,
            intended_client=str(evento.origen),
            outcome_expected=self._accion_requiere_outcome(evento, accion),
            decision_valid=decision_valid,
            authority_decision_id=(
                accion.perception_authority.authority_decision_id
                if accion.perception_authority is not None
                else None
            ),
            authority_reference=(
                accion.perception_authority.model_dump(mode="json")
                if accion.perception_authority is not None
                else None
            ),
            action_binding_sha256=(
                accion.perception_authority.action_binding_sha256
                if accion.perception_authority is not None
                else None
            ),
        )
        if receipt.status == ActionRecordStatus.CONFLICT:
            raise TransactionConflictError(
                f"Identidad de accion en conflicto: {accion.id_accion}"
            )

    @staticmethod
    def _accion_requiere_outcome(
        evento: EventoEntrante,
        accion: AccionSaliente,
    ) -> bool:
        if accion.nombre == "none":
            return False
        return evento.origen != "terminal"

    @staticmethod
    def _serializar_accion_durable(
        accion: AccionSaliente | None,
    ) -> dict | None:
        if accion is None:
            return None
        return {
            "id_accion": accion.id_accion,
            "nombre": accion.nombre,
            "parametros": dict(accion.parametros or {}),
            "requiere_confirmacion": accion.requiere_confirmacion,
            "estado_ejecucion": accion.estado_ejecucion,
            "id_transaccion_origen": accion.id_transaccion_origen,
            "decision_outcome": accion.decision_outcome.model_dump(mode="json"),
            "perception_authority": (
                accion.perception_authority.model_dump(mode="json")
                if accion.perception_authority is not None
                else None
            ),
        }

    def _restaurar_accion_desde_ledger(
        self,
        payload: dict | None,
    ) -> AccionSaliente | None:
        if not isinstance(payload, dict):
            return None
        action_name = str(payload.get("nombre") or "none")
        parameters = (
            dict(payload.get("parametros") or {})
            if isinstance(payload.get("parametros"), dict)
            else payload.get("parametros")
        )
        try:
            MotorActionKindV1(action_name)
        except (TypeError, ValueError):
            if not isinstance(parameters, dict):
                parameters = {}
        else:
            canonical = MotorActionAdapterV1.require(action_name, parameters)
            action_name = canonical.action.value
            parameters = canonical.parameters_dict()
        accion = AccionSaliente(
            id_accion=str(payload.get("id_accion") or ""),
            nombre=action_name,
            parametros=parameters,
            requiere_confirmacion=payload.get("requiere_confirmacion") is True,
            estado_ejecucion=str(
                payload.get("estado_ejecucion") or ActionState.LEGACY_UNKNOWN.value
            ),
            id_transaccion_origen=(
                str(payload.get("id_transaccion_origen") or "").strip() or None
            ),
            perception_authority=(
                PerceptionAuthorityReferenceV1.model_validate(
                    payload["perception_authority"]
                )
                if isinstance(payload.get("perception_authority"), dict)
                else None
            ),
        )
        accion.attach_decision_outcome(
            DecisionOutcome.model_validate(payload["decision_outcome"])
            if isinstance(payload.get("decision_outcome"), dict)
            else DecisionOutcome.legacy_for_action(payload.get("nombre"))
        )
        return self._actualizar_snapshot_accion(accion)

    def _actualizar_snapshot_accion(
        self,
        accion: AccionSaliente,
    ) -> AccionSaliente:
        record = self._obtener_action_ledger().get_action(accion.id_accion)
        if record is None:
            return accion
        if (
            accion.estado_ejecucion == record.action_state.value
            and accion.id_transaccion_origen == record.transaction_id
        ):
            return accion
        return accion.model_copy(
            update={
                "estado_ejecucion": record.action_state.value,
                "id_transaccion_origen": record.transaction_id,
            }
        )

    @staticmethod
    def _accion_desde_record(record: ActionRecord) -> AccionSaliente:
        action_name = record.action_name
        parameters = dict(record.parameters)
        try:
            MotorActionKindV1(action_name)
        except (TypeError, ValueError):
            pass
        else:
            canonical = MotorActionAdapterV1.require(action_name, parameters)
            action_name = canonical.action.value
            parameters = canonical.parameters_dict()
        return AccionSaliente(
            id_accion=record.action_id,
            nombre=action_name,
            parametros=parameters,
            requiere_confirmacion=record.requires_confirmation,
            estado_ejecucion=record.action_state.value,
            id_transaccion_origen=record.transaction_id,
            perception_authority=(
                PerceptionAuthorityReferenceV1.model_validate(
                    record.authority_reference
                )
                if isinstance(record.authority_reference, dict)
                else None
            ),
        ).attach_decision_outcome(DecisionOutcome.legacy_for_action(action_name))

    def _completar_transaccion_ledger(
        self,
        transaction_id: str,
        accion: AccionSaliente | None,
    ) -> None:
        ledger = self._obtener_action_ledger()
        response = self._serializar_accion_durable(accion)
        get_uow = getattr(ledger, "get_turn_uow", None)
        commit_uow = getattr(ledger, "commit_turn_uow", None)
        has_uow = callable(get_uow) and get_uow(str(transaction_id)) is not None
        status = (
            commit_uow(str(transaction_id), response)
            if has_uow and callable(commit_uow)
            else ledger.complete_transaction(str(transaction_id), response)
        )
        if status not in {
            TransactionCompletionStatus.COMPLETED,
            TransactionCompletionStatus.ALREADY_COMPLETED,
        }:
            raise ActionLedgerError(
                f"Completion durable rechazada para {transaction_id}: {status.value}"
            )

    def close(self) -> None:
        """Cierra recursos auxiliares persistentes del orquestador."""
        with self._lifecycle_close_lock:
            if self._lifecycle_close_attempted:
                return
            self._lifecycle_close_attempted = True
        first_error: BaseException | None = None
        for stage, resource in (
            ("action_ledger", getattr(self, "action_ledger", None)),
            ("oracle_memory", getattr(self, "oracle_memory", None)),
        ):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
                    try:
                        log_safe_boundary_failure(
                            logger,
                            component=f"orchestrator.close.{stage}",
                            code="RESOURCE_CLOSE_FAILED",
                            error=error,
                            level=logging.WARNING,
                        )
                    except Exception:
                        pass
        if first_error is not None:
            raise first_error

    def obtener_constelacion_publica(self) -> dict:
        """Expone la constelacion cognitiva actual para depuracion o visualizacion."""
        return {
            "constelacion": self.behavior.obtener_constelacion(),
            "resumen": self.behavior.obtener_constelacion_resumen(),
        }

    def _resolver_cancelacion_tarea_usuario(
        self,
        evento: EventoEntrante,
        *,
        task_transition: dict | None = None,
    ) -> ActionResponse:
        """Materializa una cancelación ya autorizada por señal estructurada."""
        transition = dict(task_transition) if isinstance(task_transition, dict) else {}
        if not transition:
            cancelar_tarea = getattr(self.behavior, "cancelar_tarea", None)
            if not callable(cancelar_tarea):
                raise RuntimeError("BehaviorCore no expone cancelación estructurada")
            receipt = cancelar_tarea("Solicitud explicita del usuario.")
            transition = (
                receipt.to_dict()
                if callable(getattr(receipt, "to_dict", None))
                else {}
            )

        if transition.get("status") != "cancelled":
            return ActionResponse(
                pensamiento="",
                accion="none",
                parametros={},
                razon="La cancelacion no tenia una tarea activa compatible.",
                valido=True,
                metadata={
                    "task_cancelled_by_user": False,
                    "task_transition": transition,
                },
            )

        if self._es_canal_terminal(evento):
            return ActionResponse(
                pensamiento="",
                accion="hablar",
                parametros={
                    "texto_a_comunicar": "De acuerdo, cancelé la tarea activa."
                },
                razon="Cancelacion estructurada aplicada a la tarea original.",
                valido=True,
                metadata={
                    "task_cancelled_by_user": True,
                    "task_transition": transition,
                },
            )

        cancel_action = MotorActionAdapterV1.require(
            MotorActionKindV1.CANCEL_TASK,
            {
                "motivo": "Solicitud explicita del usuario.",
                "cancelar_movimiento": True,
                "cancelar_cola": True,
                "texto_confirmacion": "De acuerdo, cancelo la tarea actual.",
            },
        )
        return ActionResponse(
            pensamiento="El usuario pidio cancelar la tarea actual. Debo detener cuerpo y cola.",
            accion=cancel_action.action.value,
            parametros=cancel_action.parameters_dict(),
            razon="Cancelacion estructurada aplicada antes de detener actividad externa.",
            valido=True,
            metadata={
                "task_cancelled_by_user": True,
                "task_transition": transition,
            },
        )

    def _resolver_interrupcion_critica(self, evento: EventoEntrante) -> ActionResponse:
        """Genera una accion inmediata y segura ante dolor fisico o colision fuerte."""
        direccion_escape = str(evento.detalles.get("direccion_escape", "atras"))
        if direccion_escape not in {"adelante", "atras", "izquierda", "derecha"}:
            direccion_escape = "atras"

        distancia = evento.detalles.get("distancia_escape", 1.5)
        pensamiento = (
            "Se detecto dolor fisico o colision. Debo interrumpir la tarea actual, "
            "detener el avance y corregir la trayectoria de inmediato."
        )
        razon = (
            "Interrupcion critica por seguridad fisica. Se prioriza retroceder o desviarse "
            "antes de continuar con cualquier objetivo."
        )

        posicion_actual = self._leer_posicion_lazzaro(evento.detalles)
        if self.tool_registry and self.tool_registry.get_tool("navegar_a_posicion") and posicion_actual:
            x, y, z = posicion_actual
            dx, dz = self._vector_escape(direccion_escape)
            distancia_segura = self._leer_distancia_escape(distancia)
            raw_parameters = {
                "x": round(x + dx * distancia_segura, 2),
                "y": round(y, 2),
                "z": round(z + dz * distancia_segura, 2),
            }
            parsed_escape = MotorActionAdapterV1.parse(
                MotorActionKindV1.NAVIGATE_TO_POSITION,
                raw_parameters,
            )
            if isinstance(parsed_escape, MotorActionContractFailureV1):
                action_name = MotorActionKindV1.NAVIGATE_TO_POSITION.value
                metadata = {
                    "motor_contract_failure": parsed_escape.model_dump(mode="json")
                }
            else:
                action_name = parsed_escape.action.value
                raw_parameters = parsed_escape.parameters_dict()
                metadata = {}
            return ActionResponse(
                pensamiento=pensamiento,
                accion=action_name,
                parametros=raw_parameters,
                razon=razon,
                valido=True,
                metadata=metadata,
            )

        objeto_escape = self._leer_objeto_escape(evento)
        if self.tool_registry and self.tool_registry.get_tool("alejarse_de_objeto") and objeto_escape:
            escape_action = MotorActionAdapterV1.require(
                MotorActionKindV1.MOVE_AWAY_FROM_OBJECT,
                {
                    "nombre_objeto": objeto_escape,
                    "distancia": self._leer_distancia_escape(distancia),
                },
            )
            return ActionResponse(
                pensamiento=pensamiento,
                accion=escape_action.action.value,
                parametros=escape_action.parameters_dict(),
                razon=razon,
                valido=True,
            )

        return ActionResponse(
            pensamiento=pensamiento,
            accion="none",
            parametros={},
            razon=razon,
            valido=False,
            error="No hay herramienta de movimiento disponible para reaccionar al dolor fisico.",
        )

    def _sanitizar_action_response(self, action_response: ActionResponse) -> ActionResponse:
        """
        Convierte respuestas invalidas o herramientas inexistentes en un no-op seguro.
        """
        if not action_response.valido:
            logger.warning(
                "Respuesta del modelo invalida; se degradara a no-op. Error: %s",
                action_response.error,
            )
            return ActionResponse(
                pensamiento=action_response.pensamiento,
                accion="none",
                parametros={},
                razon=action_response.razon,
                valido=False,
                error=action_response.error or "Respuesta invalida del modelo.",
                metadata=action_response.metadata,
                outcome=action_response.outcome,
            )

        try:
            MotorActionKindV1(action_response.accion)
        except (TypeError, ValueError):
            pass
        else:
            action_response, canonical = self._canonicalizar_action_response_motor(
                action_response
            )
            if canonical is None:
                return action_response

        if (
            action_response.accion != "none"
            and self.tool_registry
            and self.tool_registry.get_tool(action_response.accion) is None
        ):
            error = f"Herramienta no registrada: {action_response.accion}"
            logger.warning("%s. Se degradara a no-op.", error)
            return ActionResponse(
                pensamiento=action_response.pensamiento,
                accion="none",
                parametros={},
                razon=action_response.razon,
                valido=False,
                error=error,
                metadata=action_response.metadata,
                outcome=self._outcome_rechazado(
                    action_response.outcome,
                    DecisionReasonCode.UNKNOWN_ACTION,
                ),
            )

        return action_response

    @staticmethod
    def _canonicalizar_action_response_motor(
        action_response: ActionResponse,
    ) -> tuple[ActionResponse, MotorActionV1 | None]:
        """Convierte input Motor no confiable o lo rechaza con causalidad finita."""
        try:
            MotorActionKindV1(action_response.accion)
        except (TypeError, ValueError):
            return action_response, None

        parsed = MotorActionAdapterV1.parse(
            action_response.accion,
            action_response.parametros,
        )
        if isinstance(parsed, MotorActionContractFailureV1):
            metadata = dict(action_response.metadata or {})
            metadata["motor_contract_failure"] = parsed.model_dump(mode="json")
            return (
                action_response.model_copy(
                    update={
                        "accion": MotorActionKindV1.NONE.value,
                        "parametros": {},
                        "valido": False,
                        "error": "Contrato estructural Motor invalido.",
                        "metadata": metadata,
                        "outcome": Orchestrator._outcome_rechazado(
                            action_response.outcome,
                            DecisionReasonCode.INVALID_PARAMETERS,
                        ),
                    }
                ),
                None,
            )

        return (
            action_response.model_copy(
                update={
                    "accion": parsed.action.value,
                    "parametros": parsed.parameters_dict(),
                }
            ),
            parsed,
        )

    @staticmethod
    def _asegurar_decision_outcome(
        action_response: ActionResponse,
        *,
        decision_layer: str,
    ) -> ActionResponse:
        """Clasifica constructores legacy en el boundary sin fingir provenance LLM."""
        if action_response.outcome.quality is not DecisionQuality.LEGACY_UNKNOWN:
            return action_response

        layer = {
            "motor": DecisionLayer.MOTOR,
            "persona": DecisionLayer.PERSONA,
            "oracle": DecisionLayer.ORACLE_PERSONA,
            "reasoning": DecisionLayer.REASONING,
        }.get(str(decision_layer or "").strip().lower(), DecisionLayer.CORE)
        if not action_response.valido:
            disposition = DecisionDisposition.INVALID
            quality = DecisionQuality.REJECTED
            reason = DecisionReasonCode.PIPELINE_ERROR
        elif action_response.accion == "none":
            disposition = DecisionDisposition.NO_ACTION
            quality = DecisionQuality.VALIDATED
            reason = DecisionReasonCode.PASSIVE_NO_ACTION
        else:
            disposition = DecisionDisposition.ACTION
            quality = DecisionQuality.VALIDATED
            reason = DecisionReasonCode.PRIMARY_VALID

        return action_response.model_copy(
            update={
                "outcome": DecisionOutcome(
                    disposition=disposition,
                    quality=quality,
                    attempt=DecisionAttempt.FIRST,
                    layer=layer,
                    reason_codes=(reason,),
                    retry_count=0,
                )
            }
        )

    @staticmethod
    def _outcome_rechazado(
        previous: DecisionOutcome,
        reason: DecisionReasonCode,
    ) -> DecisionOutcome:
        if previous.attempt is DecisionAttempt.LEGACY:
            return DecisionOutcome(
                disposition=DecisionDisposition.INVALID,
                quality=DecisionQuality.REJECTED,
                attempt=DecisionAttempt.FIRST,
                layer=previous.layer,
                reason_codes=(reason, DecisionReasonCode.LEGACY_UNTYPED),
                retry_count=0,
            )
        return previous.evolve(
            disposition=DecisionDisposition.INVALID,
            quality=DecisionQuality.REJECTED,
            reason_codes=(reason,),
            motor_status=None,
        )

    def _adjuntar_esfuerzo_subjetivo(
        self,
        action_response: ActionResponse,
        *,
        identity_context: IdentityContextV1 | None = None,
    ) -> ActionResponse:
        """Agrega contexto subjetivo sin afectar la validacion fisica."""
        if action_response.accion == "none":
            return action_response

        try:
            calculator = getattr(self, "subjective_effort", None) or SubjectiveEffortCalculator()
            effort = calculator.calculate(
                action_response.accion,
                action_response.parametros,
                metadata=action_response.metadata,
                identity_state=(
                    identity_context.state if identity_context is not None else None
                ),
            )
            if effort is None:
                return action_response

            metadata = dict(action_response.metadata or {})
            metadata["subjective_effort"] = effort.to_dict()
            return action_response.model_copy(update={"metadata": metadata})
        except Exception as e:
            logger.error("Error calculando esfuerzo subjetivo: %s", e, exc_info=True)
            return action_response

    def _a_accion_saliente(
        self,
        action_response: ActionResponse,
        politica: str,
        *,
        action_id: str | None = None,
        perception_authority: PerceptionAuthorityReferenceV1 | None = None,
        canonical_motor_action: MotorActionV1 | None = None,
    ) -> AccionSaliente:
        """Convierte la ActionResponse interna en el modelo de API."""
        action_name = action_response.accion
        parameters = dict(action_response.parametros or {})
        try:
            MotorActionKindV1(action_name)
        except (TypeError, ValueError):
            pass
        else:
            if canonical_motor_action is None:
                canonical_motor_action = MotorActionAdapterV1.require(
                    action_name,
                    parameters,
                )
            action_name = canonical_motor_action.action.value
            parameters = canonical_motor_action.parameters_dict()

        requiere_confirmacion = False
        tool_info = self.tool_registry.get_tool(action_name) if self.tool_registry else None
        if tool_info and tool_info.get("requires_confirmation") and politica != "autonoma":
            requiere_confirmacion = True

        return AccionSaliente(
            id_accion=action_id or str(uuid.uuid4()),
            nombre=action_name,
            parametros=parameters,
            requiere_confirmacion=requiere_confirmacion,
            perception_authority=perception_authority,
        ).attach_decision_outcome(action_response.outcome)

    @staticmethod
    def _leer_posicion_lazzaro(detalles: dict) -> tuple[float, float, float] | None:
        posicion = detalles.get("posicion_lazzaro")
        if not isinstance(posicion, dict):
            return None
        try:
            return (
                float(posicion.get("x", 0.0)),
                float(posicion.get("y", 0.0)),
                float(posicion.get("z", 0.0)),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _leer_distancia_escape(valor: object) -> float:
        try:
            distancia = float(valor)
        except (TypeError, ValueError):
            distancia = 1.5
        return max(0.5, min(5.0, distancia))

    @staticmethod
    def _leer_objeto_escape(evento: EventoEntrante) -> str | None:
        candidatos = (
            evento.objeto_involucrado,
            evento.detalles.get("objeto"),
            evento.detalles.get("nombre_objeto"),
            evento.detalles.get("objeto_colisionado"),
        )
        for candidato in candidatos:
            nombre = str(candidato or "").strip()
            if nombre and nombre.lower() not in {"ninguno", "none", "null", "desconocido"}:
                return nombre
        return None

    @staticmethod
    def _vector_escape(direccion_escape: str) -> tuple[float, float]:
        vectores = {
            "adelante": (0.0, 1.0),
            "atras": (0.0, -1.0),
            "izquierda": (-1.0, 0.0),
            "derecha": (1.0, 0.0),
        }
        return vectores.get(direccion_escape, (0.0, -1.0))

    def _agregar_notificacion_si_corresponde(self, action_response: ActionResponse) -> bool:
        """No convierte cognition ni errores internos en mensajes publicos."""
        del action_response
        return False

    def _herramientas_para_evento(self, evento: EventoEntrante, triage_decision: dict) -> list[str]:
        herramientas = self.tool_registry.listar_nombres() if self.tool_registry else []
        return self.tool_policy.seleccionar(
            herramientas,
            origen=evento.origen,
            route=str(triage_decision.get("route", "")),
        )

    @staticmethod
    def _evento_solicita_decision(evento: EventoEntrante, es_interrupcion_critica: bool) -> bool:
        if es_interrupcion_critica:
            return True
        if evento.detalles.get("solicita_decision") is True:
            return True
        return evento.tipo_evento == "comando_usuario"

    @staticmethod
    def _current_user_request(evento: EventoEntrante) -> str | None:
        """Return only the exact request authored in the current user event."""
        if evento.tipo_evento != "comando_usuario":
            return None
        if "mensaje" not in evento.detalles:
            return None
        mensaje = evento.detalles.get("mensaje")
        return mensaje if isinstance(mensaje, str) else None

    @staticmethod
    def _es_resultado_hablar(evento: EventoEntrante) -> bool:
        return evento.tipo_evento == "resultado_accion" and evento.detalles.get("accion") == "hablar"

    def _gestionar_ejecucion(
        self,
        evento: EventoEntrante,
        accion_saliente: AccionSaliente,
        action_response: ActionResponse | None = None,
    ) -> tuple[str, dict | None]:
        ledger = self._obtener_action_ledger()
        action_record = ledger.get_action(accion_saliente.id_accion)
        if accion_saliente.nombre == "none":
            if action_record is not None and action_record.action_state == ActionState.CREATED:
                self._exigir_transicion(
                    ledger.transition_action(
                        accion_saliente.id_accion,
                        ActionTransitionEvent.SKIP,
                        receipt_id=f"skip:{accion_saliente.id_accion}",
                    ),
                    "skip",
                )
            return "sin_accion", None

        if accion_saliente.requiere_confirmacion:
            if action_record is not None and action_record.action_state == ActionState.CREATED:
                self._exigir_transicion(
                    ledger.transition_action(
                        accion_saliente.id_accion,
                        ActionTransitionEvent.REQUIRE_AUTHORIZATION,
                        receipt_id=f"require-authorization:{accion_saliente.id_accion}",
                    ),
                    "require_authorization",
                )
            return (
                "pendiente_confirmacion",
                self._adjuntar_esfuerzo_a_detalles(
                    {
                        "mensaje": "La accion requiere confirmacion antes de ejecutarse.",
                        "error": None,
                        "datos_extra": None,
                    },
                    action_response,
                ),
            )

        if action_record is not None and action_record.action_state == ActionState.CREATED:
            self._exigir_transicion(
                ledger.transition_action(
                    accion_saliente.id_accion,
                    ActionTransitionEvent.AUTO_AUTHORIZE,
                    receipt_id=f"auto-authorize:{accion_saliente.id_accion}",
                ),
                "auto_authorize",
            )

        if self._debe_ejecutar_localmente(evento):
            permit = None
            current = ledger.get_action_state(accion_saliente.id_accion)
            if current == ActionState.AUTHORIZED:
                self._exigir_transicion(
                    ledger.transition_action(
                        accion_saliente.id_accion,
                        ActionTransitionEvent.START_EXECUTION,
                        receipt_id=f"local-start:{accion_saliente.id_accion}",
                    ),
                    "start_execution",
                )
                permit = ledger.issue_execution_permit(accion_saliente.id_accion)
                if permit is None:
                    raise ActionLedgerError("Ledger no emitio permit para accion local")
            resultado_ejecucion = ActionExecutor.ejecutar(
                accion_saliente,
                permit=permit,
            )
            return (
                "ok" if resultado_ejecucion.exito is True else "error",
                self._adjuntar_esfuerzo_a_detalles(
                    {
                        "mensaje": resultado_ejecucion.mensaje,
                        "error": resultado_ejecucion.error,
                        "datos_extra": resultado_ejecucion.datos_extra,
                    },
                    action_response,
                ),
            )

        current = ledger.get_action_state(accion_saliente.id_accion)
        if current == ActionState.AUTHORIZED:
            self._exigir_transicion(
                ledger.transition_action(
                    accion_saliente.id_accion,
                    ActionTransitionEvent.DISPATCH,
                    receipt_id=f"dispatch:{accion_saliente.id_accion}",
                ),
                "dispatch",
            )

        return (
            "despachada",
            self._adjuntar_esfuerzo_a_detalles(
                {
                    "mensaje": f"Accion enviada al cliente '{evento.origen}' para su ejecucion.",
                    "error": None,
                    "datos_extra": None,
                },
                action_response,
            ),
        )

    @staticmethod
    def _exigir_transicion(receipt, nombre: str) -> None:
        if receipt.classification not in {
            TransitionClassification.APPLIED,
            TransitionClassification.ALREADY_APPLIED,
        }:
            raise ActionLedgerError(
                f"Transicion {nombre} rechazada: {receipt.classification.value}"
            )

    @staticmethod
    def _adjuntar_esfuerzo_a_detalles(
        detalles: dict | None,
        action_response: ActionResponse | None,
    ) -> dict | None:
        if action_response is None:
            return detalles
        effort = action_response.metadata.get("subjective_effort") if isinstance(action_response.metadata, dict) else None
        if not isinstance(effort, dict):
            return detalles
        detalles_enriquecidos = dict(detalles or {})
        detalles_enriquecidos["subjective_effort"] = effort
        return detalles_enriquecidos

    @staticmethod
    def _debe_ejecutar_localmente(evento: EventoEntrante) -> bool:
        return evento.origen == "sandbox"

    def _obtener_transaccion_cacheada(self, id_transaccion: str) -> Optional[AccionSaliente]:
        with self._obtener_lock_recurso("_transacciones_cache_lock"):
            accion = self._transacciones_procesadas.get(id_transaccion)
            if accion is not None:
                self._transacciones_procesadas.move_to_end(id_transaccion)
            return accion

    @staticmethod
    def _debe_cachear_transaccion(action_response: ActionResponse) -> bool:
        """Permite reintentar un guardado manual cuya escritura falló."""
        metadata = action_response.metadata if isinstance(action_response.metadata, dict) else {}
        return not (
            metadata.get("manual_memory_save_control") is True
            and metadata.get("manual_memory_save_status") == "error"
        )

    @staticmethod
    def _es_error_guardado_conversacional(action_response: ActionResponse) -> bool:
        metadata = action_response.metadata if isinstance(action_response.metadata, dict) else {}
        return (
            metadata.get("manual_memory_save_control") is True
            and metadata.get("manual_memory_save_status") == "error"
        )

    def _marcar_guardado_conversacional_pendiente(
        self,
        id_transaccion: str,
        politica: str,
    ) -> None:
        with self._obtener_lock_recurso("_guardados_conversacionales_lock"):
            pendientes = getattr(self, "_guardados_conversacionales_pendientes", None)
            if not isinstance(pendientes, OrderedDict):
                pendientes = OrderedDict()
                self._guardados_conversacionales_pendientes = pendientes
            pendientes[id_transaccion] = politica
            pendientes.move_to_end(id_transaccion)
            while len(pendientes) > self.MAX_TRANSACCIONES_CACHE:
                pendientes.popitem(last=False)

    def _olvidar_guardado_conversacional_pendiente(self, id_transaccion: str) -> None:
        with self._obtener_lock_recurso("_guardados_conversacionales_lock"):
            pendientes = getattr(self, "_guardados_conversacionales_pendientes", None)
            if isinstance(pendientes, OrderedDict):
                pendientes.pop(id_transaccion, None)

    def _cachear_transaccion(self, id_transaccion: str, accion: AccionSaliente) -> None:
        with self._obtener_lock_recurso("_transacciones_cache_lock"):
            self._transacciones_procesadas[id_transaccion] = accion
            self._transacciones_procesadas.move_to_end(id_transaccion)
            while len(self._transacciones_procesadas) > self.MAX_TRANSACCIONES_CACHE:
                self._transacciones_procesadas.popitem(last=False)

    def _obtener_lock_recurso(self, nombre: str):
        """Obtiene un lock local; el fallback conserva fakes legacy creados sin __init__."""
        lock = getattr(self, nombre, None)
        if lock is None:
            lock = threading.Lock()
            setattr(self, nombre, lock)
        return lock

    @staticmethod
    def _construir_query_memoria(
        evento: EventoEntrante,
        narrativa: str,
        percepcion_resumida: str,
        tarea: dict | None,
    ) -> str:
        partes = [narrativa, percepcion_resumida, evento.tipo_evento]
        if tarea:
            objetivo = tarea.get("objetivo") or tarea.get("objetivo_id")
            if objetivo:
                partes.append(str(objetivo))
        return " ".join(parte for parte in partes if parte).strip()

    @staticmethod
    def _construir_narrativa_memoria(
        narrativa: str,
        percepcion_resumida: str,
        evento: EventoEntrante,
    ) -> str:
        partes = [narrativa]
        if percepcion_resumida:
            partes.append(percepcion_resumida)
        if evento.tipo_evento == "resultado_accion":
            accion = evento.detalles.get("accion")
            exito = evento.detalles.get("exito")
            mensaje = evento.detalles.get("mensaje_resultado")
            partes.append(f"Resultado de accion: accion={accion}, exito={exito}, mensaje={mensaje}.")
        return " ".join(parte for parte in partes if parte).strip() or evento.tipo_evento

    @staticmethod
    def _calcular_impacto_memoria(evento: EventoEntrante, es_interrupcion_critica: bool) -> float:
        if es_interrupcion_critica:
            return 0.9
        if evento.tipo_evento == "resultado_accion" and evento.detalles.get("exito") is False:
            return 0.7
        if evento.tipo_evento == "resultado_accion" and evento.detalles.get("exito") is True:
            return 0.45
        if evento.tipo_evento in {"inicio_simulacion", "actualizacion_estado"}:
            return 0.2
        return 0.5
