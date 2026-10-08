"""
motor_schemas.py - Esquemas Pydantic para acciones fisicas de la Capa 1.
"""

import math
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Optional, Self, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
)

from .motor_resolution import InteractionType, MotorActionKindV1


def _validar_numero_fisico_finito(value: Any) -> Any:
    """Rechaza bool y no-finitos antes de que Pydantic convierta a float."""
    if isinstance(value, bool):
        raise ValueError("Un booleano no es un numero fisico valido.")

    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("El parametro fisico debe ser numerico.") from exc

    if not math.isfinite(normalized):
        raise ValueError("El parametro fisico debe ser finito.")
    return value


PhysicalFloat = Annotated[float, BeforeValidator(_validar_numero_fisico_finito)]


class StrictParams(BaseModel):
    """Base estricta: el motor no acepta parametros fisicos inventados."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if update:
            raise MotorActionContractError(
                MotorActionFailureCodeV1.MUTATION_FORBIDDEN
            )
        return super().model_copy(deep=deep)


class NavigateToObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Nombre exacto o aproximado del objeto destino.")
    distancia: Optional[PhysicalFloat] = Field(
        None,
        ge=0.1,
        le=10.0,
        description="Distancia opcional en metros a mantener del objeto.",
    )


class NavigateToPositionParams(StrictParams):
    x: PhysicalFloat = Field(..., description="Coordenada X en el mundo 3D.")
    y: PhysicalFloat = Field(..., description="Coordenada Y en el mundo 3D.")
    z: PhysicalFloat = Field(..., description="Coordenada Z en el mundo 3D.")


class InteractObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto con el que se va a interactuar.")
    tipo_accion: InteractionType = Field(
        ...,
        description="Tipo de interaccion fisica permitida.",
    )


class GrabObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Nombre del objeto a agarrar.")


class ReleaseObjectParams(StrictParams):
    fuerza_lanzamiento: PhysicalFloat = Field(
        0.0,
        ge=0.0,
        le=20.0,
        description="Fuerza de lanzamiento (0 = soltar).",
    )


class MoveAwayParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto del cual alejarse.")
    distancia: PhysicalFloat = Field(2.0, ge=0.5, le=5.0, description="Metros a retroceder.")


class RotateAvatarParams(StrictParams):
    grados: PhysicalFloat = Field(
        ...,
        ge=-360.0,
        le=360.0,
        description="Grados relativos de giro en Y. Positivo=derecha, negativo=izquierda.",
    )


class ApplyForceParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto al que se aplicara fuerza.")
    vector_x: PhysicalFloat = Field(..., description="Componente X de la direccion.")
    vector_y: PhysicalFloat = Field(..., description="Componente Y de la direccion.")
    vector_z: PhysicalFloat = Field(..., description="Componente Z de la direccion.")
    fuerza: PhysicalFloat = Field(..., gt=0.0, le=100.0, description="Magnitud de la fuerza.")


class CancelTaskParams(StrictParams):
    motivo: str = Field("Solicitud explicita del usuario.", description="Motivo de la cancelacion.")
    cancelar_movimiento: StrictBool = Field(True, description="Detener movimiento activo en Unity.")
    cancelar_cola: StrictBool = Field(True, description="Limpiar cola de subtareas en Unity.")
    texto_confirmacion: str = Field(
        "De acuerdo, cancelo la tarea actual.",
        description="Texto breve de confirmacion opcional.",
    )


ACTION_SCHEMA_MAP = {
    "navegar_a_objeto": NavigateToObjectParams,
    "navegar_a_posicion": NavigateToPositionParams,
    "interactuar_objeto_3d": InteractObjectParams,
    "agarrar_objeto": GrabObjectParams,
    "soltar_objeto": ReleaseObjectParams,
    "alejarse_de_objeto": MoveAwayParams,
    "girar_avatar": RotateAvatarParams,
    "aplicar_fuerza": ApplyForceParams,
    "cancelar_tarea": CancelTaskParams,
}

PHYSICAL_NUMERIC_ACTIONS = frozenset(
    {
        "navegar_a_objeto",
        "navegar_a_posicion",
        "soltar_objeto",
        "alejarse_de_objeto",
        "girar_avatar",
        "aplicar_fuerza",
    }
)


def validar_parametros_fisicos(accion: str, parametros: dict[str, Any]) -> dict[str, Any]:
    """Valida y normaliza parametros usando el schema canonico de la accion Motor."""
    schema_cls = ACTION_SCHEMA_MAP[accion]
    return schema_cls.model_validate(parametros).model_dump(exclude_none=True)


class MotorActionFailureCodeV1(str, Enum):
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    PARAMETERS_NOT_OBJECT = "PARAMETERS_NOT_OBJECT"
    REQUIRED_FIELD_MISSING = "REQUIRED_FIELD_MISSING"
    EXTRA_FIELD_FORBIDDEN = "EXTRA_FIELD_FORBIDDEN"
    FIELD_TYPE_INVALID = "FIELD_TYPE_INVALID"
    FIELD_VALUE_INVALID = "FIELD_VALUE_INVALID"
    ACTION_PARAMETER_MISMATCH = "ACTION_PARAMETER_MISMATCH"
    CROSS_FIELD_INVARIANT_FAILED = "CROSS_FIELD_INVARIANT_FAILED"
    MUTATION_FORBIDDEN = "MUTATION_FORBIDDEN"


class MotorActionContractError(TypeError):
    """Fallo estructural finito; el texto de una excepcion no es autoridad."""

    def __init__(self, reason_code: MotorActionFailureCodeV1):
        self.reason_code = reason_code
        super().__init__(reason_code.value)


class NoActionParams(StrictParams):
    """Parametros exactamente vacios del sentinel no ejecutable."""


class _CanonicalMotorActionBase(BaseModel):
    contract_version: StrictInt = 1

    model_config = ConfigDict(extra="forbid", frozen=True)
    PARAMS_TYPE: ClassVar[type[StrictParams]]

    @field_validator("contract_version", mode="before")
    @classmethod
    def _exact_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("params", mode="before", check_fields=False)
    @classmethod
    def _exact_params_type(cls, value: Any) -> StrictParams:
        if type(value) is not cls.PARAMS_TYPE:
            raise ValueError("canonical action requires its exact parameter model")
        return value

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if update:
            raise MotorActionContractError(
                MotorActionFailureCodeV1.MUTATION_FORBIDDEN
            )
        return super().model_copy(deep=deep)


class NoActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.NONE] = MotorActionKindV1.NONE
    params: NoActionParams
    PARAMS_TYPE = NoActionParams


class NavigateToObjectActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.NAVIGATE_TO_OBJECT] = MotorActionKindV1.NAVIGATE_TO_OBJECT
    params: NavigateToObjectParams
    PARAMS_TYPE = NavigateToObjectParams


class NavigateToPositionActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.NAVIGATE_TO_POSITION] = MotorActionKindV1.NAVIGATE_TO_POSITION
    params: NavigateToPositionParams
    PARAMS_TYPE = NavigateToPositionParams


class InteractObjectActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.INTERACT_OBJECT_3D] = MotorActionKindV1.INTERACT_OBJECT_3D
    params: InteractObjectParams
    PARAMS_TYPE = InteractObjectParams


class GrabObjectActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.GRAB_OBJECT] = MotorActionKindV1.GRAB_OBJECT
    params: GrabObjectParams
    PARAMS_TYPE = GrabObjectParams


class ReleaseObjectActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.RELEASE_OBJECT] = MotorActionKindV1.RELEASE_OBJECT
    params: ReleaseObjectParams
    PARAMS_TYPE = ReleaseObjectParams


class MoveAwayActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.MOVE_AWAY_FROM_OBJECT] = MotorActionKindV1.MOVE_AWAY_FROM_OBJECT
    params: MoveAwayParams
    PARAMS_TYPE = MoveAwayParams


class RotateAvatarActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.ROTATE_AVATAR] = MotorActionKindV1.ROTATE_AVATAR
    params: RotateAvatarParams
    PARAMS_TYPE = RotateAvatarParams


class ApplyForceActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.APPLY_FORCE] = MotorActionKindV1.APPLY_FORCE
    params: ApplyForceParams
    PARAMS_TYPE = ApplyForceParams


class CancelTaskActionV1(_CanonicalMotorActionBase):
    action: Literal[MotorActionKindV1.CANCEL_TASK] = MotorActionKindV1.CANCEL_TASK
    params: CancelTaskParams
    PARAMS_TYPE = CancelTaskParams


MotorActionVariantV1 = Annotated[
    Union[
        NoActionV1,
        NavigateToObjectActionV1,
        NavigateToPositionActionV1,
        InteractObjectActionV1,
        GrabObjectActionV1,
        ReleaseObjectActionV1,
        MoveAwayActionV1,
        RotateAvatarActionV1,
        ApplyForceActionV1,
        CancelTaskActionV1,
    ],
    Field(discriminator="action"),
]


class MotorActionV1(RootModel[MotorActionVariantV1]):
    """Accion Motor canonica, cerrada e inmutable."""

    model_config = ConfigDict(frozen=True)

    @property
    def action(self) -> MotorActionKindV1:
        return self.root.action

    @property
    def params(self) -> StrictParams:
        return self.root.params

    def parameters_dict(self) -> dict[str, Any]:
        return self.params.model_dump(mode="json", exclude_none=True)

    def to_legacy_dict(
        self,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "accion": self.action.value,
            "parametros": self.parameters_dict(),
            "razon": str(reason or ""),
            "metadata": dict(metadata or {}),
        }

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if update:
            raise MotorActionContractError(
                MotorActionFailureCodeV1.MUTATION_FORBIDDEN
            )
        return super().model_copy(deep=deep)


class MotorActionContractFailureV1(BaseModel):
    contract_version: Literal[1] = 1
    reason_code: MotorActionFailureCodeV1

    model_config = ConfigDict(extra="forbid", frozen=True)


_ACTION_VARIANTS: dict[
    MotorActionKindV1,
    tuple[type[StrictParams], type[_CanonicalMotorActionBase]],
] = {
    MotorActionKindV1.NONE: (NoActionParams, NoActionV1),
    MotorActionKindV1.NAVIGATE_TO_OBJECT: (NavigateToObjectParams, NavigateToObjectActionV1),
    MotorActionKindV1.NAVIGATE_TO_POSITION: (NavigateToPositionParams, NavigateToPositionActionV1),
    MotorActionKindV1.INTERACT_OBJECT_3D: (InteractObjectParams, InteractObjectActionV1),
    MotorActionKindV1.GRAB_OBJECT: (GrabObjectParams, GrabObjectActionV1),
    MotorActionKindV1.RELEASE_OBJECT: (ReleaseObjectParams, ReleaseObjectActionV1),
    MotorActionKindV1.MOVE_AWAY_FROM_OBJECT: (MoveAwayParams, MoveAwayActionV1),
    MotorActionKindV1.ROTATE_AVATAR: (RotateAvatarParams, RotateAvatarActionV1),
    MotorActionKindV1.APPLY_FORCE: (ApplyForceParams, ApplyForceActionV1),
    MotorActionKindV1.CANCEL_TASK: (CancelTaskParams, CancelTaskActionV1),
}


class MotorActionAdapterV1:
    """Unica entrada desde strings/dicts legacy al contrato Motor canonico."""

    @classmethod
    def parse(
        cls,
        action: object,
        params: object,
    ) -> MotorActionV1 | MotorActionContractFailureV1:
        if isinstance(action, MotorActionKindV1):
            action_kind = action
        elif type(action) is str:
            try:
                action_kind = MotorActionKindV1(action.strip())
            except ValueError:
                return cls._failure(MotorActionFailureCodeV1.UNSUPPORTED_ACTION)
        else:
            return cls._failure(MotorActionFailureCodeV1.UNSUPPORTED_ACTION)

        params_type, variant_type = _ACTION_VARIANTS[action_kind]
        if isinstance(params, StrictParams):
            if type(params) is not params_type:
                return cls._failure(
                    MotorActionFailureCodeV1.ACTION_PARAMETER_MISMATCH
                )
            validated_params = params
        elif type(params) is dict:
            try:
                validated_params = params_type.model_validate(params)
            except ValidationError as exc:
                return cls._failure(cls._reason_for_validation_error(exc))
        else:
            return cls._failure(MotorActionFailureCodeV1.PARAMETERS_NOT_OBJECT)

        try:
            variant = variant_type(action=action_kind, params=validated_params)
            return MotorActionV1(root=variant)
        except ValidationError:
            return cls._failure(
                MotorActionFailureCodeV1.CROSS_FIELD_INVARIANT_FAILED
            )

    @classmethod
    def require(cls, action: object, params: object) -> MotorActionV1:
        result = cls.parse(action, params)
        if isinstance(result, MotorActionContractFailureV1):
            raise MotorActionContractError(result.reason_code)
        return result

    @staticmethod
    def _failure(
        reason_code: MotorActionFailureCodeV1,
    ) -> MotorActionContractFailureV1:
        return MotorActionContractFailureV1(reason_code=reason_code)

    @staticmethod
    def _reason_for_validation_error(
        error: ValidationError,
    ) -> MotorActionFailureCodeV1:
        kinds = {str(item.get("type") or "") for item in error.errors()}
        if "missing" in kinds:
            return MotorActionFailureCodeV1.REQUIRED_FIELD_MISSING
        if "extra_forbidden" in kinds:
            return MotorActionFailureCodeV1.EXTRA_FIELD_FORBIDDEN
        if any(
            kind.endswith("_type")
            or kind in {"enum", "literal_error", "model_type", "is_instance_of"}
            for kind in kinds
        ):
            return MotorActionFailureCodeV1.FIELD_TYPE_INVALID
        return MotorActionFailureCodeV1.FIELD_VALUE_INVALID


# Nombre legacy conservado sin mantener el antiguo modelo permisivo.
MotorAction = MotorActionV1
