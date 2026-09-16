"""Capa 3 fria con parsing estructurado separado de policy y cache."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from core.logger import get_logger

from .knowledge_provenance import (
    CacheLookupStatusV1,
    CacheReceiptV1,
    CacheStoreStatusV1,
    ProducerReceiptV1,
    cache_receipt_for_lookup,
    provenance_for,
    with_cache_store_status,
)
from .oracle_memory import OracleCacheLookupResultV1, OracleMemory
from .prompt_plan import (
    ContextEnvelopeV1,
    PromptPlanV1,
    RequestInstructionV1,
    build_oracle_system_instructions,
)
from .schemas import OracleResult
from .structured_parse_types import (
    JsonObjectInspection,
    StructuredParseIssueCode,
    StructuredParseResult,
    StructuredParseStatus,
    StructuredTextSalvage,
    StructuredTextSalvageKind,
    inspect_json_object,
)


logger = get_logger(__name__)


class _OraclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contenido_tecnico: StrictStr
    resumen_para_persona: Optional[StrictStr] = None


@dataclass(frozen=True)
class OracleStructuredValue:
    contenido_tecnico: str
    resumen_para_persona: Optional[str]


class OracleEngine:
    """Produce conocimiento interno; nunca produce acciones ni voz publica."""

    def __init__(self):
        pass

    def evaluar_cache(
        self,
        mensaje_usuario: str,
        memoria: OracleMemory,
    ) -> OracleCacheLookupResultV1:
        """Consulta una vez y conserva el recibo aun cuando no haya contenido."""
        buscar_tipado = getattr(memoria, "buscar_resultado_cacheado_con_recibo", None)
        if callable(buscar_tipado):
            result = buscar_tipado(mensaje_usuario)
            if not isinstance(result, OracleCacheLookupResultV1):
                raise TypeError("OracleMemory devolvio un cache lookup fuera de contrato")
            if result.cached_result is not None:
                logger.info("Oracle Cache HIT. Devolviendo conocimiento interno cacheado.")
            return result

        # Compatibilidad acotada con dobles antiguos: el resultado ya debe
        # contener provenance; un miss se representa explícitamente.
        buscar_resultado = getattr(memoria, "buscar_resultado_cacheado", None)
        resultado = buscar_resultado(mensaje_usuario) if callable(buscar_resultado) else None
        if isinstance(resultado, OracleResult):
            return OracleCacheLookupResultV1(
                1,
                resultado,
                resultado.provenance.retrieval.cache,
            )
        return OracleCacheLookupResultV1(
            1,
            None,
            cache_receipt_for_lookup(
                mensaje_usuario,
                status=CacheLookupStatusV1.MISS,
                threshold=OracleMemory.DEFAULT_CACHE_DISTANCE,
            ),
        )

    def preparar_consulta(self, mensaje_usuario: str | None) -> PromptPlanV1:
        """Build a cold Oracle plan; the request remains optional and typed."""
        request = None
        if isinstance(mensaje_usuario, str):
            request = RequestInstructionV1.current_user(mensaje_usuario)
        return PromptPlanV1(
            system=build_oracle_system_instructions(),
            context=ContextEnvelopeV1(),
            request=request,
        )

    def parse_result(
        self,
        respuesta_llm: object,
    ) -> StructuredParseResult[OracleStructuredValue]:
        """Parsea una salida Oracle sin ejecutar cache, retry ni fallback."""
        inspection = inspect_json_object(respuesta_llm)
        if inspection.data is None:
            issue = inspection.issue_code or StructuredParseIssueCode.MALFORMED_JSON
            if (
                issue is StructuredParseIssueCode.PLAIN_TEXT_ONLY
                and inspection.normalized_text.strip()
            ):
                result = self._oracle_degraded_result(
                    inspection,
                    (StructuredParseIssueCode.PLAIN_TEXT_ONLY,),
                    self._bounded_candidate(inspection.normalized_text),
                )
                self._log_parse_result(result)
                return result

            result = StructuredParseResult[OracleStructuredValue](
                contract_version=1,
                status=StructuredParseStatus.ERROR,
                value=None,
                salvage=None,
                reason_codes=(issue,),
                normalizations=inspection.normalizations,
                raw_shape=inspection.raw_shape,
                raw_length=inspection.raw_length,
            )
            self._log_parse_result(result)
            return result

        data = inspection.data
        try:
            payload = _OraclePayload.model_validate(data)
        except ValidationError:
            candidate, legacy_alias = self._safe_oracle_candidate(data)
            schema_issue = (
                StructuredParseIssueCode.NONCANONICAL_WRAPPER
                if legacy_alias
                else StructuredParseIssueCode.SCHEMA_VALIDATION_FAILED
            )
            reasons = self._merge_reason_codes(inspection.issue_code, schema_issue)
            result = self._oracle_degraded_or_error(
                inspection,
                reasons,
                candidate,
            )
            self._log_parse_result(result)
            return result

        contenido = payload.contenido_tecnico.strip()
        if not contenido:
            result = StructuredParseResult[OracleStructuredValue](
                contract_version=1,
                status=StructuredParseStatus.ERROR,
                value=None,
                salvage=None,
                reason_codes=(StructuredParseIssueCode.SCHEMA_VALIDATION_FAILED,),
                normalizations=inspection.normalizations,
                raw_shape=inspection.raw_shape,
                raw_length=inspection.raw_length,
            )
            self._log_parse_result(result)
            return result

        resumen = (
            payload.resumen_para_persona.strip()
            if isinstance(payload.resumen_para_persona, str)
            and payload.resumen_para_persona.strip()
            else None
        )
        if inspection.issue_code is not None:
            result = self._oracle_degraded_result(
                inspection,
                (inspection.issue_code,),
                contenido,
            )
            self._log_parse_result(result)
            return result

        result = StructuredParseResult[OracleStructuredValue](
            contract_version=1,
            status=StructuredParseStatus.VALID,
            value=OracleStructuredValue(
                contenido_tecnico=contenido,
                resumen_para_persona=resumen,
            ),
            salvage=None,
            reason_codes=(),
            normalizations=inspection.normalizations,
            raw_shape=inspection.raw_shape,
            raw_length=inspection.raw_length,
        )
        self._log_parse_result(result)
        return result

    def proyectar_resultado(
        self,
        parse_result: StructuredParseResult[OracleStructuredValue],
        pregunta_original: str,
        memoria: OracleMemory,
        *,
        producer: ProducerReceiptV1,
        cache_receipt: CacheReceiptV1,
    ) -> OracleResult:
        """Aplica disponibilidad y cache sobre un parse result ya calculado."""
        if producer.final_parse_status is not parse_result.status:
            raise ValueError("producer parse status must match the projected result")
        if cache_receipt.lookup_status in {
            CacheLookupStatusV1.HIT,
            CacheLookupStatusV1.LEGACY_ENTRY,
        }:
            raise ValueError("cache hits must be returned, never projected again")
        if cache_receipt.store_status is not CacheStoreStatusV1.NOT_ATTEMPTED:
            raise ValueError("projection requires a fresh cache lookup receipt")
        if parse_result.status is StructuredParseStatus.VALID:
            assert parse_result.value is not None
            resultado = OracleResult(
                pregunta_original=pregunta_original,
                contenido_tecnico=parse_result.value.contenido_tecnico,
                resumen_para_persona=parse_result.value.resumen_para_persona,
                provenance=provenance_for(producer, cache_receipt),
            )
            stored_receipt = self._guardar_resultado(memoria, resultado)
            return resultado.model_copy(
                update={"provenance": provenance_for(producer, stored_receipt)}
            )

        if (
            parse_result.status is StructuredParseStatus.DEGRADED
            and parse_result.salvage is not None
            and parse_result.salvage.kind
            is StructuredTextSalvageKind.ORACLE_CANDIDATE_TEXT
        ):
            return OracleResult(
                pregunta_original=pregunta_original,
                contenido_tecnico=parse_result.salvage.text,
                resumen_para_persona=None,
                provenance=provenance_for(
                    producer,
                    with_cache_store_status(
                        cache_receipt,
                        CacheStoreStatusV1.SKIPPED_POLICY,
                    ),
                ),
            )

        return self._fallback(
            pregunta_original,
            "Mi procesador logico no produjo conocimiento estructurado util para comunicar.",
            producer=producer,
            cache_receipt=cache_receipt,
        )

    def keep_y_save(
        self,
        respuesta_llm: object,
        pregunta_original: str,
        memoria: OracleMemory,
        *,
        producer: ProducerReceiptV1,
        cache_receipt: CacheReceiptV1,
    ) -> OracleResult:
        """Wrapper legacy: una sola parseada, policy y cache solo para VALID."""
        result = self.parse_result(respuesta_llm)
        return self.proyectar_resultado(
            result,
            pregunta_original,
            memoria,
            producer=producer,
            cache_receipt=cache_receipt,
        )

    def _oracle_degraded_or_error(
        self,
        inspection: JsonObjectInspection,
        reasons: tuple[StructuredParseIssueCode, ...],
        candidate: Optional[str],
    ) -> StructuredParseResult[OracleStructuredValue]:
        if candidate:
            return self._oracle_degraded_result(inspection, reasons, candidate)
        return StructuredParseResult[OracleStructuredValue](
            contract_version=1,
            status=StructuredParseStatus.ERROR,
            value=None,
            salvage=None,
            reason_codes=reasons,
            normalizations=inspection.normalizations,
            raw_shape=inspection.raw_shape,
            raw_length=inspection.raw_length,
        )

    @staticmethod
    def _oracle_degraded_result(
        inspection: JsonObjectInspection,
        reasons: tuple[StructuredParseIssueCode, ...],
        candidate: str,
    ) -> StructuredParseResult[OracleStructuredValue]:
        return StructuredParseResult[OracleStructuredValue](
            contract_version=1,
            status=StructuredParseStatus.DEGRADED,
            value=None,
            salvage=StructuredTextSalvage(
                kind=StructuredTextSalvageKind.ORACLE_CANDIDATE_TEXT,
                text=OracleEngine._bounded_candidate(candidate),
            ),
            reason_codes=reasons,
            normalizations=inspection.normalizations,
            raw_shape=inspection.raw_shape,
            raw_length=inspection.raw_length,
        )

    @staticmethod
    def _safe_oracle_candidate(data: dict[str, Any]) -> tuple[Optional[str], bool]:
        canonical = data.get("contenido_tecnico")
        if isinstance(canonical, str) and canonical.strip():
            return canonical.strip(), False

        for key in ("respuesta", "contenido", "explicacion", "resultado"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), True

        parametros = data.get("parametros")
        if isinstance(parametros, dict):
            for key in ("texto_a_comunicar", "texto", "mensaje"):
                value = parametros.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip(), True
        return None, False

    @staticmethod
    def _bounded_candidate(text: str) -> str:
        return text.strip()[:12_000]

    @staticmethod
    def _merge_reason_codes(
        *codes: StructuredParseIssueCode | None,
    ) -> tuple[StructuredParseIssueCode, ...]:
        result: list[StructuredParseIssueCode] = []
        for code in codes:
            if code is not None and code not in result:
                result.append(code)
        return tuple(result)

    @staticmethod
    def _guardar_resultado(
        memoria: OracleMemory,
        resultado: OracleResult,
    ) -> CacheReceiptV1:
        try:
            guardar = getattr(memoria, "guardar_resultado_en_cache", None)
            if not callable(guardar):
                raise TypeError("OracleMemory no expone guardar_resultado_en_cache")
            receipt = guardar(resultado)
            if not isinstance(receipt, CacheReceiptV1):
                raise TypeError("OracleMemory devolvio un store receipt invalido")
            return receipt
        except Exception as error:
            logger.error(
                "Oracle cache write failed component=oracle_cache error_type=%s",
                type(error).__name__,
            )
            return with_cache_store_status(
                resultado.provenance.retrieval.cache,
                CacheStoreStatusV1.FAILED,
            )

    @staticmethod
    def _fallback(
        pregunta_original: str,
        mensaje: str,
        *,
        producer: ProducerReceiptV1,
        cache_receipt: CacheReceiptV1,
    ) -> OracleResult:
        logger.info("Activando fallback frio en Capa 3.")
        return OracleResult(
            pregunta_original=pregunta_original,
            contenido_tecnico=mensaje,
            resumen_para_persona=mensaje,
            provenance=provenance_for(
                producer,
                with_cache_store_status(
                    cache_receipt,
                    CacheStoreStatusV1.SKIPPED_POLICY,
                ),
            ),
            error="Capa Oraculo activo un resultado de rescate.",
        )

    @staticmethod
    def _log_parse_result(
        result: StructuredParseResult[OracleStructuredValue],
    ) -> None:
        if result.status is StructuredParseStatus.VALID:
            logger.debug(
                "Structured parse component=oracle status=%s raw_length=%d",
                result.status.value,
                result.raw_length,
            )
            return
        logger.warning(
            "Structured parse component=oracle status=%s issue_codes=%s raw_length=%d",
            result.status.value,
            ",".join(code.value for code in result.reason_codes),
            result.raw_length,
        )
