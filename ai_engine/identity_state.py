"""Adaptador legacy de lectura para el estado poseido por BehaviorCore.

ROOT-016 retiro la reserva local y toda mutacion directa de StateEngine. Codigo
nuevo debe consumir IdentitySnapshotV1/IdentityContextV1.
"""

from __future__ import annotations

from typing import Any


class IdentityState:
    """Compatibilidad explicita; nunca es owner ni funciona sin BehaviorCore."""

    def __init__(self, behavior_core: Any) -> None:
        if behavior_core is None or not callable(
            getattr(behavior_core, "capture_identity_components", None)
        ):
            raise TypeError("IdentityState exige un BehaviorCore owner")
        self._behavior_core = behavior_core

    def _values(self) -> tuple[float, float, float]:
        state, _ = self._behavior_core.capture_identity_components()
        return float(state["energia"]), float(state["estres"]), float(state["foco"])

    @property
    def energia(self) -> float:
        return self._values()[0]

    @property
    def estres(self) -> float:
        return self._values()[1]

    @property
    def enfoque(self) -> float:
        return self._values()[2]

    def registrar_evento(
        self,
        intent: str = "",
        route: str = "",
        success: bool | None = None,
        es_descanso: bool = False,
        intensidad: float = 1.0,
    ) -> None:
        del intent
        self._behavior_core.registrar_impacto_identidad(
            route=route,
            success=success,
            es_descanso=es_descanso,
            intensidad=intensidad,
        )

    def obtener_estado_completo(self) -> dict[str, Any]:
        energia, estres, foco = self._values()
        return {
            "energia": round(energia, 1),
            "estres": round(estres, 1),
            "enfoque": round(foco, 1),
            "nivel_energia": self._describir_energia(energia),
            "nivel_estres": self._describir_estres(estres),
            "nivel_enfoque": self._describir_enfoque(foco),
        }

    @staticmethod
    def _describir_energia(valor: float) -> str:
        if valor < 15:
            return "exhausta, apenas puede mantenerse activa"
        if valor < 30:
            return "muy cansada, necesita descansar pronto"
        if valor < 50:
            return "algo cansada, preferiria tareas ligeras"
        if valor < 75:
            return "aceptable, puede continuar sin problemas"
        if valor < 90:
            return "buena, se siente con vitalidad"
        return "optima, llena de energia"

    @staticmethod
    def _describir_estres(valor: float) -> str:
        if valor > 85:
            return "abrumada, al borde del colapso"
        if valor > 60:
            return "muy tensa, notablemente irritada"
        if valor > 35:
            return "moderadamente estresada"
        if valor > 15:
            return "ligeramente tensa"
        return "relajada y tranquila"

    @staticmethod
    def _describir_enfoque(valor: float) -> str:
        if valor < 20:
            return "completamente dispersa, le cuesta hilar ideas"
        if valor < 40:
            return "algo distraida, puede perder detalles"
        if valor < 60:
            return "atencion normal"
        if valor < 80:
            return "bastante concentrada"
        return "hiperconcentrada, puede manejar tareas complejas"
