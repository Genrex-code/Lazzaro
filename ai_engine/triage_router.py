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
import os
import joblib
from typing import Any, Dict , Optional
from .intent_types import IntentType, Route, INTENT_TO_ROUTE
from .routing_rules import match_intent_by_keywords # <-- Faltaba este import

# hoy perdio el america asi que que hoy pierde la niña 
logger = logging.getLogger(__name__)

# ==========================================================
# CONTRATO ESTRICTO INTENCIÓN → RUTA
# ==========================================================
# Nota:
# PERCEPTION va a "motor" solo si MotorEngine sabe responder consultas de
# percepción con una acción "hablar", sin moverse físicamente.
INTENT_TO_ROUTE_MAP = {
    "MOTOR_SIMPLE": "motor",
    "MOTOR_REFERENCE": "motor",
    "PERCEPTION": "motor",

    "SOCIAL": "persona",
    "MEMORY_QUERY": "persona",
    "MEMORY_UPDATE": "persona",
    "CORE_SYSTEM": "persona",
    "UNKNOWN": "persona",

    "ORACLE_QUERY": "oracle",

    # Eventos internos
    "NOOP": "noop",
    "INTERNAL": "internal",
}


# Intenciones motoras que NO deben entrar mientras Unity ya se mueve.
MOTOR_BLOCKED_WHILE_MOVING = {
    "MOTOR_SIMPLE",
    "MOTOR_REFERENCE",
}


class TriageRouter:
    def __init__(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        intent_path = os.path.join(
            base_dir,
            "lazzaro_data",
            "triage_model_intent.joblib"
        )

        self.model_intent = None

        try:
            if os.path.exists(intent_path):
                self.model_intent = joblib.load(intent_path)
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
        detalles = evento.get("detalles", {}) or {}
        texto_usuario = detalles.get("mensaje", "") or ""
        texto_usuario = texto_usuario.strip()

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

            intent_str = self._predict_intent_with_ml(texto_usuario)

            # Si ML falló o dudó, usar fallback heurístico viejo.
            if intent_str is None:
                intent_str = self._predict_intent_with_rules(texto_usuario)

            # Si el modelo o las reglas devuelven algo fuera del contrato, degradar seguro.
            if intent_str not in INTENT_TO_ROUTE_MAP:
                logger.warning(f"Intent desconocido fuera de contrato: {intent_str}")
                intent_str = "UNKNOWN"

            route_str = INTENT_TO_ROUTE_MAP.get(intent_str, "persona")

            # Guardrail común.
            # Importante: se aplica tanto a ML como a fallback.
            if (
                intent_str in MOTOR_BLOCKED_WHILE_MOVING
                and estado_agente
                and estado_agente.get("is_moving")
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
            intent_str = intent_enum.value

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