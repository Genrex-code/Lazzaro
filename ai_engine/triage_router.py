"""
triage_router.py - ROuter cognitivi de capa 0 con Machine LEarnign. dios a en que momento esto se me fue de las manos
yo solo queria ya no hacer la tarea de ingles y documentacion de web apps aaaaaaaaaaaaaaaaaaa
Decide a que capa especializada enviar un evento usando Random forest. o como me gusta llamarlo
chingaderitas al azar bamos yipeeee :3
"""

import os
import joblib
import logging
from typing import Any, Dict

from websockets import route
from .intent_types import IntentType, Route
from ai_engine import intent_types

logger = logging.getLogger(__name__)

class TriageRouter:
    def __init__(self):
        #cargar los cerebros al arrancar el server
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        intent_path = os.path.join(base_dir, "lazzaro_data", "triage_model_intent.joblib")
        route_path = os.path.join(base_dir, "lazzaro_data", "triage_model_route.joblib")

        self.model_intent = None
        self.model_route = None

        try:
            if os.path.exists(intent_path) and os.path.exists(route_path):
                self.model_intent = joblib.load(intent_path)
                self.model_route = joblib.load(route_path)
                logger.info("cerebros de capa 0 (ramdon forest cargados y listos :3)")
            else:
                logger.warning("no se encontraron los modelos joblib en lazzaro/caprpeta ya sabes cual no voy a especificar cual tonoto")
        except Exception as e:
            logger.error(f"error cargando modelols ML: {e}")
        

    def route_event(self, evento: Dict[str, Any], estado_agente: Dict[str, Any] = None) -> Dict[str, Any]:
        tipo_evento = evento.get("tipo_evento","desconocido")
        detalles = evento.get("detalles", {})
        # ¡Adiós al if de una línea ilegible! Forma clásica y limpia:
        texto_usuario = ""
        if detalles:
            texto_usuario = detalles.get("mensaje", "")

        # ==========================================
        # 🛡️ EVENTOS INTERNOS (15% Reglas Duras)
        # ==========================================
        if tipo_evento == "actualizacion_estado":
            return self._build_response(IntentType.NOOP, Route.NOOP, 1.0, "Telemetría pasiva. Ignorada.")
            
        if tipo_evento == "resultado_accion":
            return self._build_response(IntentType.INTERNAL, Route.INTERNAL, 1.0, "Resultado procesado internamente.")

        # ==========================================
        # 🌲 COMANDOS DE USUARIO (85% Machine Learning)
        # ==========================================
        if tipo_evento == "comando_usuario":
            # Si el mensaje viene vacío por error de Unity
            if not texto_usuario:
                return self._build_response(IntentType.UNKNOWN, Route.PERSONA, 0.5, "Comando de usuario vacío.")

            # Si los cerebros cargaron bien, hacemos magia
            if self.model_intent and self.model_route:
                try:
                    # 1. Predicción del texto
                    intent_str = self.model_intent.predict([texto_usuario])[0]
                    route_str = self.model_route.predict([texto_usuario])[0]
                    
                    # 2. Psicología real (Confianza matemática)
                    proba = self.model_intent.predict_proba([texto_usuario])[0]
                    confidence = round(float(max(proba)), 2)
                    
                    # 3. Convertir los strings del ML a tus Enums estrictos
                    try:
                        intent_enum = IntentType(intent_str)
                        route_enum = Route(route_str)
                    except ValueError:
                        # Si el ML escupe algo raro que no está en el Enum
                        intent_enum = IntentType.UNKNOWN
                        route_enum = Route.PERSONA

                    # 4. Barrera de Seguridad Motora (Guardrail físico)
                    if intent_enum == IntentType.MOTOR_SIMPLE and estado_agente and estado_agente.get("is_moving"):
                        return self._build_response(
                            IntentType.NOOP, 
                            Route.NOOP, 
                            0.95, 
                            "El agente está en movimiento. Comando motor ignorado."
                        )

                    return self._build_response(
                        intent=intent_enum, 
                        route=route_enum, 
                        confidence=confidence, 
                        reason=f"Decisión ML Capa 0 (Confianza real: {confidence})"
                    )
                except Exception as e:
                    logger.error(f"Error en predicción ML: {e}")
            
            # Fallback si los modelos fallaron o no existen
            return self._build_response(IntentType.UNKNOWN, Route.PERSONA, 0.5, "Fallback por fallo en modelos ML.")

        # Cualquier otro tipo de evento
        return self._build_response(IntentType.UNKNOWN, Route.NOOP, 0.0, f"Evento '{tipo_evento}' no soportado.")

    @staticmethod
    def _build_response(intent: IntentType, route: Route, confidence: float, reason: str) -> Dict[str, Any]:
        return {
            "intent": intent.value,
            "route": route.value,
            "confidence": confidence,
            "requires_memory": (route == Route.PERSONA or route == Route.ORACLE),
            "requires_oracle": (route == Route.ORACLE),
            "reason": reason,
        }