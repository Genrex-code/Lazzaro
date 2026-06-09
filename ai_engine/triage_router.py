"""
triage_router.py - Router cognitivo de capa 0.
Decide a que capa especializada enviar un evento.
"""
from typing import Any, Dict
from .intent_types import IntentType, Route, INTENT_TO_ROUTE
from .routing_rules import match_intent_by_keywords

class TriageRouter:
    """
    Enrutador inicial basado en reglas heuristicas.
    En futuras fases podra usar un clasficiador ML.
    """

    def __init__(self):
        pass

    def route_event(self, evento: Dict[str, Any], estado_agente: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Procesa un evento entrante y devuelve la ruta recomendada.
        Args:
            evento: diccionario con 'tipo_evento', 'detalles', etc.
            estado_agente: snapshot opcional del estado interno (ej. {'is_moving': bool}).

        Returns:
            Diccionario con intent, route, confidence, requires_memory, requires_oracle y reason.
        """
        tipo_evento = evento.get ("tipo_evento", "desconocido")
        detalles = evento.get ("detalles", {})
        texto_usuario = detalles.get ("mensaje","") if detalles else "" # el que invento estas formas de usar ifs que coma cola neta

        #1. Eventos internos que NUNCA van al LLM

        if tipo_evento == "actualizacion_estado":
            return self._build_response(
                    IntentType.NOOP,
                    Route.NOOP,
                    1.0,
                    "Telemetria pasiva. Ignorada."
            )
        if tipo_evento == "resultado_accion":
            return self._build_response(
                IntentType.INTERNAL,
                Route.INTERNAL,
                1.0,
                "Resultado de accion procesado internamente."
            )
        
        #comandos de usuario (el triaje real)
        if tipo_evento == "comando_usuario":
            intent = match_intent_by_keywords(texto_usuario)
            # Prevenir comandos motores si el agente ya está moviéndose
            if intent == IntentType.MOTOR_SIMPLE and estado_agente and estado_agente.get("is_moving"):
                return self._build_response(
                    IntentType.NOOP,
                    Route.NOOP,
                    0.95,
                    "El agente está en movimiento. Comando motor ignorado hasta que termine."
                )

            # Si es motor y el agente ya está en movimiento, ignorar
            # (ya cubierto por el chequeo anterior, pero reforzamos)
            if intent == IntentType.MOTOR_SIMPLE:
                confidence = 0.9
                reason = "Comando físico detectado."
            elif intent == IntentType.ORACLE:
                confidence = 0.85
                reason = "Se requiere conocimiento profundo."
            elif intent == IntentType.SOCIAL:
                confidence = 0.85
                reason = "Interacción social o de identidad."
            else:
                # UNKNOWN  fallback amistoso a Capa 2
                intent = IntentType.UNKNOWN
                confidence = 0.5
                reason = "Intención desconocida. Enviando a la Capa 2 para respuesta genérica."

            route = INTENT_TO_ROUTE[intent]

            return self._build_response(intent, route, confidence, reason)

        # Cualquier otro tipo de evento no reconocido  ignorar
        return self._build_response(
            IntentType.UNKNOWN,
            Route.NOOP,
            0.0,
            f"Tipo de evento '{tipo_evento}' no soportado por el Triage."
        )
    # Helper interno
    @staticmethod
    def _build_response(
        intent: IntentType,
        route: Route,
        confidence: float,
        reason: str
    ) -> Dict[str, Any]:
        return {
            "intent": intent.value,
            "route": route.value,
            "confidence": round(confidence, 2),
            "requires_memory": (route == Route.PERSONA or route == Route.ORACLE),
            "requires_oracle": (route == Route.ORACLE),
            "reason": reason,
        }
