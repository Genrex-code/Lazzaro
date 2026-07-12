from api.schemas import EventoEntrante
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from tools.registry import ToolRegistry
import tools.simulated.movimiento_sim  # noqa: F401


def _evento_cancelacion(mensaje: str) -> EventoEntrante:
    return EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=f"test-cancel-{abs(hash(mensaje))}",
        detalles={"mensaje": mensaje},
    )


def test_detecta_cancelacion_explicita_de_tarea():
    evento = _evento_cancelacion("cancela tus tareas pendientes porfavor")

    assert Orchestrator._usuario_pide_cancelar_tarea(evento) is True


def test_no_confunde_para_que_con_cancelacion():
    evento = _evento_cancelacion("habla para que podamos seguir probando")

    assert Orchestrator._usuario_pide_cancelar_tarea(evento) is False


def test_behavior_core_cancela_tarea_activa():
    behavior = BehaviorCore()
    behavior.iniciar_tarea("caminar hacia la capsula")

    resumen = behavior.cancelar_tarea("test")

    assert resumen is not None
    assert behavior.task_context is None


def test_resolver_cancelacion_devuelve_accion_para_unity():
    orchestrator = object.__new__(Orchestrator)
    orchestrator.behavior = BehaviorCore()
    orchestrator.behavior.iniciar_tarea("caminar hacia la capsula")

    respuesta = orchestrator._resolver_cancelacion_tarea_usuario(
        _evento_cancelacion("detente y cancela todo")
    )

    assert respuesta.accion == "cancelar_tarea"
    assert respuesta.parametros["cancelar_movimiento"] is True
    assert respuesta.parametros["cancelar_cola"] is True
    assert orchestrator.behavior.task_context is None


def test_herramienta_cancelar_tarea_registrada():
    herramienta = ToolRegistry.get_tool("cancelar_tarea")

    assert herramienta is not None
    assert herramienta["requires_confirmation"] is False
