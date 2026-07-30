"""
triage_router.py - Router cognitivo de capa 0 con Machine Learning. 
dios a en que momento esto se me fue de las manos
yo solo queria ya no hacer la tarea de ingles y documentacion de web apps aaaaaaaaaaaaaaaaaaa
Decide a que capa especializada enviar un evento usando Random forest. o como me gusta llamarlo
chingaderitas al azar bamos yipeeee :3
Responsabilidad:
- Aplicar reglas duras de seguridad.
- Usar Random Forest para clasificar intención de comandos de usuario.
- Usar fallback heurístico si el ML falla o duda.
- Derivar la ruta desde la intención, no desde otro modelo.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import joblib
except ImportError:  # El fallback heuristico debe seguir vivo sin dependencias ML.
    joblib = None

from .intent_types import INTENT_TO_ROUTE, IntentType, normalize_intent, route_for_intent
from .routing_rules import (
    match_guardrail_intent,
    match_intent_by_keywords,
    match_manual_conversation_save,
)

# hoy perdio el america asi que que hoy pierde la niña 
logger = logging.getLogger(__name__)

# ==========================================================
# CONTRATO ESTRICTO INTENCIÓN → RUTA
# ==========================================================
# Nota:
# PERCEPTION va a "motor" solo si MotorEngine sabe responder consultas de
# percepción con una acción "hablar", sin moverse físicamente.
INTENT_TO_ROUTE_MAP = {
    intent.value: route.value for intent, route in INTENT_TO_ROUTE.items()
}


# Intenciones motoras que NO deben entrar mientras Unity ya se mueve.
MOTOR_BLOCKED_WHILE_MOVING = {
    "MOTOR_SIMPLE",
    "MOTOR_REFERENCE",
}


class TriageRouter:
    def __init__(self):
        self.model_intent = None
        self.intent_path = (
            Path(__file__).resolve().parents[1]
            / "lazzaro_data"
            / "triage_model_intent.joblib"
        )

        if joblib is None:
            logger.warning(
                "joblib no esta disponible; Capa 0 usara fallback heuristico."
            )
            return

        try:
            if self.intent_path.exists():
                self.model_intent = joblib.load(self.intent_path)
                logger.info("Cerebro de Intención Random Forest cargado y listo.")
            else:
                logger.warning("No se encontró el modelo .joblib de intención.")
        except Exception as e:
            logger.error(f"Error cargando modelo ML de intención: {e}")

    def route_event(
        self,
        evento: Dict[str, Any],
        estado_agente: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        tipo_evento = evento.get("tipo_evento", "desconocido")
        detalles_raw = evento.get("detalles", {}) or {}
        detalles = detalles_raw if isinstance(detalles_raw, dict) else {}
        texto_usuario = (
            detalles.get("mensaje")
            or detalles.get("texto_usuario")
            or detalles.get("texto")
            or ""
        )
        texto_usuario = str(texto_usuario).strip()

        # ==========================================================
        # 15% — REGLAS DURAS / SISTEMA INMUNE
        # ==========================================================

        if tipo_evento == "actualizacion_estado":
            return self._build_response(
                intent="NOOP",
                route="noop",
                confidence=1.0,
                reason="Telemetría pasiva. Ignorada."
            )

        if tipo_evento == "resultado_accion":
            return self._build_response(
                intent="INTERNAL",
                route="internal",
                confidence=1.0,
                reason="Resultado de acción procesado internamente."
            )

        # Punto 2:
        # No lo etiquetamos como NOOP puro porque semánticamente sí es evento de sistema.
        # La acción sigue siendo noop, pero el intent queda limpio para dataset.
        if tipo_evento == "inicio_simulacion":
            return self._build_response(
                intent="CORE_SYSTEM",
                route="noop",
                confidence=1.0,
                reason="Inicio de simulación procesado como evento de sistema."
            )

        # Punto 3:
        # Este evento nunca debe esperar al ML ni al LLM.
        # Lo mandamos a motor para que Orchestrator/BehaviorCore resuelva escape seguro.
        if tipo_evento == "alerta_dolor_fisico":
            return self._build_response(
                intent="CORE_SYSTEM",
                route="motor",
                confidence=1.0,
                reason="Interrupción física crítica. Ruta motora segura sin ML."
            )

        # ==========================================================
        # 85% — COMANDOS DE USUARIO / ML + FALLBACK
        # ==========================================================

        if tipo_evento == "comando_usuario":
            if not texto_usuario:
                return self._build_response(
                    intent="NOOP",
                    route="noop",
                    confidence=1.0,
                    reason="Comando vacío. Ignorado."
                )

            if match_manual_conversation_save(texto_usuario):
                return self._build_response(
                    intent=IntentType.CONVERSATION_SAVE.value,
                    route=route_for_intent(IntentType.CONVERSATION_SAVE).value,
                    confidence=1.0,
                    reason="Solicitud explícita de guardar la RAM conversacional.",
                )

            guardrail_intent = normalize_intent(match_guardrail_intent(texto_usuario))
            if guardrail_intent is not IntentType.UNKNOWN:
                return self._build_response(
                    intent=guardrail_intent.value,
                    route=route_for_intent(guardrail_intent).value,
                    confidence=0.95,
                    reason=(
                        "Guardrail relacional: autoridad/regano sobre el sistema "
                        "se procesa en Capa Social antes que Oraculo."
                    )
                )

            intent_str = self._predict_intent_with_ml(texto_usuario)

            # Si ML falló o dudó, usar fallback heurístico viejo.
            if intent_str is None:
                intent_str = self._predict_intent_with_rules(texto_usuario)

            intent = normalize_intent(intent_str)
            if intent is IntentType.UNKNOWN and str(intent_str or "").strip().upper() != "UNKNOWN":
                logger.warning("Intent desconocido fuera de contrato: %s", intent_str)

            intent_str = intent.value
            route_str = route_for_intent(intent).value

            # Guardrail común.
            # Importante: se aplica tanto a ML como a fallback.
            if (
                intent_str in MOTOR_BLOCKED_WHILE_MOVING
                and self._agente_en_movimiento(estado_agente, detalles)
            ):
                return self._build_response(
                    intent="NOOP",
                    route="noop",
                    confidence=0.95,
                    reason="Agente en movimiento. Comando motor ignorado hasta terminar."
                )

            return self._build_response(
                intent=intent_str,
                route=route_str,
                confidence=getattr(self, "_last_confidence", 0.5),
                reason=getattr(self, "_last_reason", "Decisión de Capa 0.")
            )

        # Cualquier otro evento no soportado.
        return self._build_response(
            intent="UNKNOWN",
            route="noop",
            confidence=0.0,
            reason=f"Evento '{tipo_evento}' no soportado por Triage."
        )

    @staticmethod
    def _agente_en_movimiento(
        estado_agente: Optional[Dict[str, Any]],
        detalles: Dict[str, Any],
    ) -> bool:
        """Detecta si el cliente reporta movimiento activo."""
        claves_movimiento = (
            "is_moving",
            "esta_moviendo",
            "en_movimiento",
            "movimiento_activo",
        )
        valores_verdaderos = {
            "1",
            "true",
            "si",
            "sí",
            "yes",
            "moving",
            "moviendo",
            "en_movimiento",
        }

        for fuente in (estado_agente, detalles):
            if not isinstance(fuente, dict):
                continue
            for clave in claves_movimiento:
                valor = fuente.get(clave)
                if isinstance(valor, bool):
                    if valor:
                        return True
                    continue
                if str(valor or "").strip().lower() in valores_verdaderos:
                    return True

        estado_fisico = str(detalles.get("estado_fisico", "")).strip().lower()
        return estado_fisico in {"moving", "moviendo", "en movimiento"}

    # ==========================================================
    # PREDICCIÓN ML
    # ==========================================================

    def _predict_intent_with_ml(self, texto_usuario: str) -> Optional[str]:
        """
        Devuelve intent si el ML está disponible y tiene confianza suficiente.
        Devuelve None si debe caer a fallback heurístico.
        """
        self._last_confidence = 0.5
        self._last_reason = "ML no disponible. Usando fallback heurístico."

        if not self.model_intent:
            return None

        try:
            intent_str = self.model_intent.predict([texto_usuario])[0]
            proba = self.model_intent.predict_proba([texto_usuario])[0]
            confidence = round(float(max(proba)), 2)

            self._last_confidence = confidence

            # Punto 4:
            # Umbral de confianza. Puedes subirlo a 0.65/0.70 cuando tengas más dataset.
            if confidence < 0.60:
                self._last_reason = (
                    f"ML con baja confianza ({confidence}). "
                    "Usando fallback heurístico."
                )
                return None

            self._last_reason = f"Decisión ML Capa 0. Confianza: {confidence}."
            return str(intent_str)

        except Exception as e:
            logger.error(f"Error en predicción ML: {e}")
            self._last_confidence = 0.5
            self._last_reason = "ML falló. Usando fallback heurístico."
            return None

    # ==========================================================
    # FALLBACK HEURÍSTICO
    # ==========================================================

    def _predict_intent_with_rules(self, texto_usuario: str) -> str:
        """
        Red de seguridad si el modelo no existe, falla o duda.
        """
        try:
            intent_enum = match_intent_by_keywords(texto_usuario)
            intent = normalize_intent(intent_enum)
            intent_str = intent.value

            if intent is IntentType.UNKNOWN:
                self._last_confidence = min(self._last_confidence, 0.4)
            else:
                self._last_confidence = max(self._last_confidence, 0.55)

            self._last_reason = (
                self._last_reason
                + f" Fallback heurístico resolvió intent={intent_str}."
            )
            return intent_str

        except Exception as e:
            logger.error(f"Error en fallback heurístico: {e}")
            self._last_reason = "ML y fallback heurístico fallaron. Usando UNKNOWN."
            self._last_confidence = 0.5
            return "UNKNOWN"

    # ==========================================================
    # CONSTRUCTOR DE RESPUESTA
    # ==========================================================

    @staticmethod
    def _build_response(
        intent: str,
        route: str,
        confidence: float,
        reason: str
    ) -> Dict[str, Any]:
        """
        Construye la respuesta estándar de Capa 0.

        Punto 4:
        requires_memory ya no se activa para TODO motor.
        Solo se activa para rutas que realmente necesitan contexto, o para
        percepción si decides que MotorEngine debe consultar memoria/percepción.
        """
        requires_memory = (
            route in {"persona", "oracle"}
            or intent in {"PERCEPTION", "MEMORY_QUERY", "MEMORY_UPDATE"}
        )

        return {
            "intent": intent,
            "route": route,
            "confidence": round(float(confidence), 2),
            "requires_memory": requires_memory,
            "requires_oracle": (route == "oracle"),
            "reason": reason,
        }
