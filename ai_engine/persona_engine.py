"""
persona_engine.py - Capa 2 Orquestador Social.

Prepara el prompt social de Lazzaro, parsea la salida del LLM local y
garantiza que la unica accion emitida por esta capa sea "hablar".
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from core.logger import get_logger

from .identity_projection import IdentityContextV1
from .persona_prompt_buider import PersonaPromptBuilder
from .prompt_plan import PromptPlanV1
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


class _PersonaParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto_a_comunicar: StrictStr


class _PersonaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accion: StrictStr
    parametros: _PersonaParameters
    pensamiento_privado: StrictStr
    razon: StrictStr


@dataclass(frozen=True)
class PersonaStructuredValue:
    texto_a_comunicar: str
    pensamiento_privado: str
    razon: str


class PersonaEngine:
    """
    Cerebro conversacional de Lazzaro.
    No ejecuta herramientas fisicas; solo gestiona dialogo.
    """

    def __init__(self):
        self.prompt_builder = PersonaPromptBuilder()

    def preparar_interaccion(
        self,
        mensaje_usuario: str | None,
        identity_context: IdentityContextV1,
        percepcion: Optional[Dict[str, Any]] = None,
        recuerdos: str = "",
        restriccion_canal: str = "",
    ) -> PromptPlanV1:
        """Build the social PromptPlan for the current-user turn."""
        return self.prompt_builder.build_plan(
            mensaje_usuario=mensaje_usuario,
            identity_context=identity_context,
            percepcion=percepcion,
            recuerdos=recuerdos,
            terminal_channel=bool(restriccion_canal),
        )

    def preparar_verbalizacion_oraculo(
        self,
        mensaje_usuario: str | None,
        identity_context: IdentityContextV1,
        datos_oraculo: OracleResult,
        percepcion: Optional[Dict[str, Any]] = None,
        recuerdos: str = "",
        restriccion_canal: str = "",
    ) -> PromptPlanV1:
        """
        Prepara una respuesta publica usando conocimiento frio de Capa 3.
        El Oraculo aporta contenido; Persona decide voz, tono e identidad.
        """
        return self.prompt_builder.build_plan(
            mensaje_usuario=mensaje_usuario,
            identity_context=identity_context,
            percepcion=percepcion,
            recuerdos=recuerdos,
            terminal_channel=bool(restriccion_canal),
            oracle_result=datos_oraculo,
        )

    def preparar_reintento_json(
        self,
        mensajes_originales: PromptPlanV1,
        respuesta_fallida: str,
    ) -> PromptPlanV1:
        """Add bounded assistant evidence and a code-owned USER repair request."""
        if not isinstance(mensajes_originales, PromptPlanV1):
            raise TypeError("mensajes_originales debe ser PromptPlanV1")
        return mensajes_originales.with_repair(respuesta_fallida or "")

    def parsear_resultado(
        self,
        respuesta_llm: object,
    ) -> StructuredParseResult[PersonaStructuredValue]:
        """Describe el raw social sin decidir retry, fallback ni accion operativa."""
        inspection = inspect_json_object(respuesta_llm)
        if inspection.data is None:
            issue = inspection.issue_code or StructuredParseIssueCode.MALFORMED_JSON
            if issue not in {
                StructuredParseIssueCode.DUPLICATE_KEYS,
                StructuredParseIssueCode.MULTIPLE_OBJECTS,
                StructuredParseIssueCode.EMPTY_OUTPUT,
                StructuredParseIssueCode.WRONG_ROOT_TYPE,
            }:
                texto = self._extraer_valor_string(
                    inspection.normalized_text,
                    "texto_a_comunicar",
                )
                if texto:
                    result = self._persona_degraded_result(
                        inspection,
                        (StructuredParseIssueCode.PARTIAL_RECOVERY,),
                        texto,
                    )
                    self._log_parse_result(result)
                    return result

            result = StructuredParseResult[PersonaStructuredValue](
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
        safe_text = self._safe_persona_text(data)
        action = data.get("accion")
        if not isinstance(action, str) or action != "hablar":
            reasons = self._merge_reason_codes(
                inspection.issue_code,
                StructuredParseIssueCode.UNKNOWN_ACTION,
            )
            if not isinstance(data.get("parametros"), dict):
                reasons = self._merge_reason_codes(
                    *reasons,
                    StructuredParseIssueCode.INVALID_PARAMETERS,
                )
            result = self._persona_degraded_or_error(
                inspection,
                reasons,
                safe_text,
            )
            self._log_parse_result(result)
            return result

        try:
            payload = _PersonaPayload.model_validate(data)
        except ValidationError:
            reasons = self._merge_reason_codes(
                inspection.issue_code,
                StructuredParseIssueCode.SCHEMA_VALIDATION_FAILED,
            )
            if not isinstance(data.get("parametros"), dict) or (
                isinstance(data.get("parametros"), dict)
                and not isinstance(data["parametros"].get("texto_a_comunicar"), str)
            ):
                reasons = self._merge_reason_codes(
                    *reasons,
                    StructuredParseIssueCode.INVALID_PARAMETERS,
                )
            result = self._persona_degraded_or_error(
                inspection,
                reasons,
                safe_text,
            )
            self._log_parse_result(result)
            return result

        texto = payload.parametros.texto_a_comunicar.strip()
        if not texto:
            result = StructuredParseResult[PersonaStructuredValue](
                contract_version=1,
                status=StructuredParseStatus.ERROR,
                value=None,
                salvage=None,
                reason_codes=(StructuredParseIssueCode.INVALID_PARAMETERS,),
                normalizations=inspection.normalizations,
                raw_shape=inspection.raw_shape,
                raw_length=inspection.raw_length,
            )
            self._log_parse_result(result)
            return result

        if inspection.issue_code is not None:
            result = self._persona_degraded_result(
                inspection,
                (inspection.issue_code,),
                texto,
            )
            self._log_parse_result(result)
            return result

        result = StructuredParseResult[PersonaStructuredValue](
            contract_version=1,
            status=StructuredParseStatus.VALID,
            value=PersonaStructuredValue(
                texto_a_comunicar=self._limitar_texto(texto, 700),
                pensamiento_privado=self._limitar_texto(payload.pensamiento_privado, 240),
                razon=self._limitar_texto(payload.razon, 200),
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
        result: StructuredParseResult[PersonaStructuredValue],
    ) -> Dict[str, Any]:
        """Aplica policy social a un resultado autoritativo ya calculado."""
        metadata = self._parse_metadata(result)
        if result.status is StructuredParseStatus.VALID:
            assert result.value is not None
            return {
                "accion": "hablar",
                "parametros": {"texto_a_comunicar": result.value.texto_a_comunicar},
                "pensamiento_privado": result.value.pensamiento_privado,
                "razon": result.value.razon,
                "requiere_reintento": False,
                **metadata,
            }

        if (
            result.status is StructuredParseStatus.DEGRADED
            and result.salvage is not None
            and result.salvage.kind is StructuredTextSalvageKind.PERSONA_SAFE_TEXT
        ):
            return {
                "accion": "hablar",
                "parametros": {
                    "texto_a_comunicar": self._limitar_texto(result.salvage.text, 700)
                },
                "pensamiento_privado": "Salida social degradada; se uso texto seguro.",
                "razon": "Policy social de disponibilidad segura.",
                "recuperado_de_json_parcial": (
                    StructuredParseIssueCode.PARTIAL_RECOVERY in result.reason_codes
                ),
                "requiere_reintento": False,
                **metadata,
            }

        issue = result.reason_codes[0].value.lower()
        return self._fallback(
            "Senti un cortocircuito en mi lenguaje, no pude procesar eso.",
            error=f"structured_parse:{issue}",
            requiere_reintento=True,
            parse_metadata=metadata,
        )

    def parsear_respuesta(self, respuesta_llm: object) -> Dict[str, Any]:
        """Wrapper legacy: una sola parseada seguida por policy explicita."""
        result = self.parsear_resultado(respuesta_llm)
        return self.proyectar_resultado(result)

    def _persona_degraded_or_error(
        self,
        inspection: JsonObjectInspection,
        reasons: tuple[StructuredParseIssueCode, ...],
        safe_text: Optional[str],
    ) -> StructuredParseResult[PersonaStructuredValue]:
        if safe_text:
            return self._persona_degraded_result(inspection, reasons, safe_text)
        return StructuredParseResult[PersonaStructuredValue](
            contract_version=1,
            status=StructuredParseStatus.ERROR,
            value=None,
            salvage=None,
            reason_codes=reasons,
            normalizations=inspection.normalizations,
            raw_shape=inspection.raw_shape,
            raw_length=inspection.raw_length,
        )

    def _persona_degraded_result(
        self,
        inspection: JsonObjectInspection,
        reasons: tuple[StructuredParseIssueCode, ...],
        safe_text: str,
    ) -> StructuredParseResult[PersonaStructuredValue]:
        return StructuredParseResult[PersonaStructuredValue](
            contract_version=1,
            status=StructuredParseStatus.DEGRADED,
            value=None,
            salvage=StructuredTextSalvage(
                kind=StructuredTextSalvageKind.PERSONA_SAFE_TEXT,
                text=self._limitar_texto(safe_text, 700),
            ),
            reason_codes=reasons,
            normalizations=inspection.normalizations,
            raw_shape=inspection.raw_shape,
            raw_length=inspection.raw_length,
        )

    @staticmethod
    def _safe_persona_text(data: dict[str, Any]) -> Optional[str]:
        parametros = data.get("parametros")
        if not isinstance(parametros, dict):
            return None
        texto = parametros.get("texto_a_comunicar")
        if not isinstance(texto, str) or not texto.strip():
            return None
        return texto.strip()

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
    def _parse_metadata(
        result: StructuredParseResult[PersonaStructuredValue],
    ) -> dict[str, Any]:
        return {
            "primary_parse_valid": result.status is StructuredParseStatus.VALID,
            "structured_parse_status": result.status.value,
            "structured_parse_reason_codes": tuple(code.value for code in result.reason_codes),
            "structured_parse_normalizations": tuple(
                item.value for item in result.normalizations
            ),
        }

    @staticmethod
    def _log_parse_result(result: StructuredParseResult[PersonaStructuredValue]) -> None:
        if result.status is StructuredParseStatus.VALID:
            logger.debug(
                "Structured parse component=persona status=%s raw_length=%d",
                result.status.value,
                result.raw_length,
            )
            return
        logger.warning(
            "Structured parse component=persona status=%s issue_codes=%s raw_length=%d",
            result.status.value,
            ",".join(code.value for code in result.reason_codes),
            result.raw_length,
        )

    @staticmethod
    def _extraer_valor_string(texto: str, clave: str) -> Optional[str]:
        patron_cerrado = rf'"{re.escape(clave)}"\s*:\s*"((?:\\.|[^"\\])*)"'
        match = re.search(patron_cerrado, texto, flags=re.DOTALL)
        if match:
            try:
                return str(json.loads(f'"{match.group(1)}"')).strip()
            except json.JSONDecodeError:
                return match.group(1).strip()

        patron_truncado = rf'"{re.escape(clave)}"\s*:\s*"([^"\r\n}}]*)'
        match = re.search(patron_truncado, texto, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _limitar_texto(texto: str, limite: int) -> str:
        texto_limpio = " ".join(str(texto or "").split())
        if len(texto_limpio) <= limite:
            return texto_limpio
        return texto_limpio[: limite - 3].rstrip() + "..."

    def _fallback(
        self,
        mensaje_seguridad: str,
        *,
        error: str = "fallo_llm",
        requiere_reintento: bool = False,
        parse_metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Rescate cuando el LLM falla por completo."""
        logger.info("Activando fallback de seguridad en Capa 2.")
        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": mensaje_seguridad},
            "pensamiento_privado": "Fallback de seguridad por fallo del LLM.",
            "razon": "Error interno.",
            "error": error,
            "requiere_reintento": requiere_reintento,
            **(parse_metadata or {}),
        }
