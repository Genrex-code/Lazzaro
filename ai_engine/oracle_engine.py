"""Capa 3 fria con parsing estructurado separado de policy y cache."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from core.logger import get_logger

from .oracle_memory import OracleMemory
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
OracleSource = Literal["api", "fallback"]


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
    ) -> Optional[OracleResult]:
        """Consulta el cache existente sin afirmar calidad de parse legacy."""
        buscar_resultado = getattr(memoria, "buscar_resultado_cacheado", None)
        if callable(buscar_resultado):
            resultado = buscar_resultado(mensaje_usuario)
            if resultado:
                logger.info("Oracle Cache HIT. Devolviendo conocimiento interno cacheado.")
                return resultado
            return None

        respuesta_cacheada = memoria.buscar_respuesta_cacheada(mensaje_usuario)
        if respuesta_cacheada:
            logger.info("Oracle Cache HIT legacy. Calidad de parse no disponible.")
            return OracleResult(
                pregunta_original=mensaje_usuario,
                contenido_tecnico=respuesta_cacheada,
                resumen_para_persona=None,
                fuente="cache",
                cache_hit=True,
            )
        return None

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
        fuente: OracleSource = "api",
    ) -> OracleResult:
        """Aplica disponibilidad y cache sobre un parse result ya calculado."""
        fuente_normalizada = self._normalizar_fuente(fuente)
        if parse_result.status is StructuredParseStatus.VALID:
            assert parse_result.value is not None
            resultado = OracleResult(
                pregunta_original=pregunta_original,
                contenido_tecnico=parse_result.value.contenido_tecnico,
                resumen_para_persona=parse_result.value.resumen_para_persona,
                fuente=fuente_normalizada,
                cache_hit=False,
            )
            self._guardar_resultado(memoria, resultado)
            return resultado

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
                fuente=fuente_normalizada,
                cache_hit=False,
            )

        return self._fallback(
            pregunta_original,
            "Mi procesador logico no produjo conocimiento estructurado util para comunicar.",
        )

    def keep_y_save(
        self,
        respuesta_llm: object,
        pregunta_original: str,
        memoria: OracleMemory,
        *,
        fuente: OracleSource = "api",
    ) -> OracleResult:
        """Wrapper legacy: una sola parseada, policy y cache solo para VALID."""
        result = self.parse_result(respuesta_llm)
        return self.proyectar_resultado(
            result,
            pregunta_original,
            memoria,
            fuente=fuente,
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
    def _guardar_resultado(memoria: OracleMemory, resultado: OracleResult) -> None:
        try:
            memoria.guardar_en_cache(
                resultado.pregunta_original,
                resultado.contenido_tecnico,
                resumen_para_persona=resultado.resumen_para_persona,
                fuente=resultado.fuente,
            )
        except Exception as error:
            logger.error(
                "Oracle cache write failed component=oracle_cache error_type=%s",
                type(error).__name__,
            )

    @staticmethod
    def _fallback(pregunta_original: str, mensaje: str) -> OracleResult:
        logger.info("Activando fallback frio en Capa 3.")
        return OracleResult(
            pregunta_original=pregunta_original,
            contenido_tecnico=mensaje,
            resumen_para_persona=mensaje,
            fuente="error",
            cache_hit=False,
            error="Capa Oraculo activo un resultado de rescate.",
        )

    @staticmethod
    def _normalizar_fuente(fuente: str) -> OracleSource:
        return "fallback" if str(fuente).strip().lower() == "fallback" else "api"

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
