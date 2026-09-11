"""Valida respuestas estructuradas generales sin mezclar parsing y policy."""

from __future__ import annotations

from typing import Optional

from pydantic import ValidationError

from ai_engine.schemas import ActionResponse, LLMResponse
from ai_engine.structured_parse_types import (
    StructuredParseIssueCode,
    StructuredParseResult,
    StructuredParseStatus,
    inspect_json_object,
)
from core.logger import get_logger


logger = get_logger(__name__)


class ResponseParser:
    """Convierte una salida LLM en un resultado de parsing finito y versionado."""

    @staticmethod
    def parse_result(
        texto_crudo: object,
        acciones_permitidas: Optional[set[str]] = None,
    ) -> StructuredParseResult[ActionResponse]:
        inspection = inspect_json_object(texto_crudo)
        if inspection.data is None:
            issue = inspection.issue_code or StructuredParseIssueCode.MALFORMED_JSON
            result = StructuredParseResult[ActionResponse](
                contract_version=1,
                status=StructuredParseStatus.ERROR,
                value=None,
                salvage=None,
                reason_codes=(issue,),
                normalizations=inspection.normalizations,
                raw_shape=inspection.raw_shape,
                raw_length=inspection.raw_length,
            )
            ResponseParser._log_result(result)
            return result

        try:
            respuesta = LLMResponse.model_validate(inspection.data)
        except ValidationError:
            reasons = ResponseParser._merge_reasons(
                inspection.issue_code,
                StructuredParseIssueCode.SCHEMA_VALIDATION_FAILED,
            )
            result = StructuredParseResult[ActionResponse](
                contract_version=1,
                status=StructuredParseStatus.DEGRADED,
                value=None,
                salvage=None,
                reason_codes=reasons,
                normalizations=inspection.normalizations,
                raw_shape=inspection.raw_shape,
                raw_length=inspection.raw_length,
            )
            ResponseParser._log_result(result)
            return result

        accion_normalizada = ResponseParser._normalizar_accion(respuesta.accion)
        if (
            acciones_permitidas is not None
            and accion_normalizada != "none"
            and accion_normalizada not in acciones_permitidas
        ):
            reasons = ResponseParser._merge_reasons(
                inspection.issue_code,
                StructuredParseIssueCode.UNKNOWN_ACTION,
            )
            result = StructuredParseResult[ActionResponse](
                contract_version=1,
                status=StructuredParseStatus.DEGRADED,
                value=None,
                salvage=None,
                reason_codes=reasons,
                normalizations=inspection.normalizations,
                raw_shape=inspection.raw_shape,
                raw_length=inspection.raw_length,
            )
            ResponseParser._log_result(result)
            return result

        if inspection.issue_code is not None:
            result = StructuredParseResult[ActionResponse](
                contract_version=1,
                status=StructuredParseStatus.DEGRADED,
                value=None,
                salvage=None,
                reason_codes=(inspection.issue_code,),
                normalizations=inspection.normalizations,
                raw_shape=inspection.raw_shape,
                raw_length=inspection.raw_length,
            )
            ResponseParser._log_result(result)
            return result

        result = StructuredParseResult[ActionResponse](
            contract_version=1,
            status=StructuredParseStatus.VALID,
            value=ActionResponse(
                pensamiento=respuesta.pensamiento,
                accion=accion_normalizada,
                parametros=respuesta.parametros,
                razon=respuesta.razon,
                valido=True,
            ),
            salvage=None,
            reason_codes=(),
            normalizations=inspection.normalizations,
            raw_shape=inspection.raw_shape,
            raw_length=inspection.raw_length,
        )
        ResponseParser._log_result(result)
        return result

    @staticmethod
    def project_result(result: StructuredParseResult[ActionResponse]) -> ActionResponse:
        """Aplica la policy legacy de no-op a un resultado ya calculado."""
        if result.status is StructuredParseStatus.VALID:
            assert result.value is not None
            return result.value

        reason = result.reason_codes[0].value
        return ActionResponse(
            pensamiento="",
            accion="none",
            parametros={},
            razon="",
            valido=False,
            error=f"structured_parse:{reason.lower()}",
            metadata={
                "structured_parse_status": result.status.value,
                "structured_parse_reason_codes": tuple(code.value for code in result.reason_codes),
                "structured_parse_normalizations": tuple(
                    item.value for item in result.normalizations
                ),
            },
        )

    @staticmethod
    def parse(texto_crudo: object, acciones_permitidas: Optional[set[str]] = None) -> ActionResponse:
        """Wrapper compatible derivado de una unica ejecucion de ``parse_result``."""
        result = ResponseParser.parse_result(texto_crudo, acciones_permitidas)
        return ResponseParser.project_result(result)

    @staticmethod
    def _normalizar_accion(accion: object) -> str:
        if accion is None or not isinstance(accion, str):
            return "none"
        accion_limpia = accion.strip()
        if not accion_limpia or accion_limpia.lower() in {"none", "null"}:
            return "none"
        return accion_limpia

    @staticmethod
    def _merge_reasons(
        first: StructuredParseIssueCode | None,
        second: StructuredParseIssueCode,
    ) -> tuple[StructuredParseIssueCode, ...]:
        if first is None or first is second:
            return (second,)
        return (first, second)

    @staticmethod
    def _log_result(result: StructuredParseResult[ActionResponse]) -> None:
        if result.status is StructuredParseStatus.VALID:
            logger.debug(
                "Structured parse component=response status=%s raw_length=%d",
                result.status.value,
                result.raw_length,
            )
            return
        logger.warning(
            "Structured parse component=response status=%s issue_codes=%s raw_length=%d",
            result.status.value,
            ",".join(code.value for code in result.reason_codes),
            result.raw_length,
        )
