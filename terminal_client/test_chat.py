from __future__ import annotations

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from pathlib import Path
import threading
import time
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.schemas import ActionResponse
from ai_engine.identity_projection import IdentityContextV1
from ai_engine.identity_state import IdentityState
from ai_engine.persona_engine import PersonaEngine
from api.routes import router
from api.schemas import AccionSaliente, EventoEntrante
from core.action_ledger import TurnAbortedError, TurnProcessingError
from core.orchestrator import Orchestrator
from core.identity_snapshot import IdentitySnapshotProvider, build_static_self_v1
from core.state_engine import StateEngine
from core.tool_policy import ToolPolicy
from memory.memory_manager import ManualConversationSaveResult
from terminal_client.chat import (
    HELP_TEXT,
    TerminalChatClient,
    TerminalChatError,
    ejecutar_chat,
)


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


def _evento_terminal(
    mensaje: str = "hola",
    id_transaccion: str | None = None,
) -> EventoEntrante:
    return EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion=id_transaccion or f"terminal-{abs(hash(mensaje))}",
        detalles={"mensaje": mensaje, "solicita_decision": True},
    )


def test_cliente_envia_contrato_terminal_y_solo_devuelve_texto_publico() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert_true(request.url.path == "/api/v1/comando", "Debe usar /api/v1/comando.")
        assert_true(payload["origen"] == "terminal", "Debe declarar origen terminal.")
        assert_true(payload["detalles"]["modo_conversacion"] is True, "Debe marcar modo conversacion.")
        assert_true(
            payload["detalles"]["mensaje"] == "hola  Lazzaro\n¿como estas? :3",
            "Debe preservar formato interno y recortar solo los extremos.",
        )
        uuid.UUID(payload["id_transaccion"])
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "mensaje": "Accion generada correctamente.",
                "accion": {
                    "id_accion": "accion-chat",
                    "nombre": "hablar",
                    "parametros": {
                        "texto_a_comunicar": "\u001b]0;TITULO_INYECTADO\u0007\u001b[31mHola, Perro.\u001b[0m"
                    },
                    "razonamiento": "Respuesta social.",
                    "pensamiento_privado": "Esto no debe mostrarse.",
                    "requiere_confirmacion": False,
                    "parametros_descripcion": [],
                },
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://lazzaro.test",
    )
    client = TerminalChatClient(http_client=http_client)
    try:
        texto = client.enviar_mensaje("  hola  Lazzaro\n¿como estas? :3  ")
    finally:
        http_client.close()

    assert_true(texto == "Hola, Perro.", "Solo debe devolver texto_a_comunicar.")
    assert_true(len(requests) == 1, "Debe producir una sola solicitud.")


def test_cliente_genera_un_uuid_distinto_por_turno() -> None:
    ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        ids.append(payload["id_transaccion"])
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "accion": {
                    "nombre": "hablar",
                    "parametros": {"texto_a_comunicar": "Turno recibido."},
                },
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://lazzaro.test",
    )
    client = TerminalChatClient(http_client=http_client)
    try:
        client.enviar_mensaje("primer turno")
        client.enviar_mensaje("segundo turno")
    finally:
        http_client.close()

    assert_true(len(set(ids)) == 2, "Cada turno debe tener un UUID diferente.")
    for id_transaccion in ids:
        uuid.UUID(id_transaccion)


def test_cliente_bloquea_respuesta_no_conversacional() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "accion": {
                    "id_accion": "accion-motora",
                    "nombre": "navegar_a_objeto",
                    "parametros": {"nombre_objeto": "Cube"},
                },
            },
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://lazzaro.test",
    )
    client = TerminalChatClient(http_client=http_client)
    try:
        try:
            client.enviar_mensaje("ve al cubo")
        except TerminalChatError as exc:
            assert_true("bloqueo" in str(exc), "Debe explicar que bloqueo la accion.")
        else:
            raise AssertionError("Una accion motora nunca debe aceptarse en terminal.")
    finally:
        http_client.close()


def test_cliente_no_expone_detalles_internos_de_http_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"mensaje": "Error interno: MARCADOR_SECRETO_DEL_BACKEND"},
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://lazzaro.test",
    )
    client = TerminalChatClient(http_client=http_client)
    try:
        try:
            client.enviar_mensaje("hola")
        except TerminalChatError as exc:
            detalle = str(exc)
            assert_true("HTTP 500" in detalle, "Debe conservar el diagnostico HTTP.")
            assert_true(
                "MARCADOR_SECRETO" not in detalle,
                "No debe imprimir el detalle interno del backend.",
            )
        else:
            raise AssertionError("Un HTTP 500 debe producir un error recuperable.")
    finally:
        http_client.close()


def test_repl_se_recupera_de_fallos_de_conexion_y_timeout() -> None:
    numero_solicitud = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal numero_solicitud
        numero_solicitud += 1
        if numero_solicitud == 1:
            raise httpx.ConnectError("conexion rechazada", request=request)
        if numero_solicitud == 2:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "accion": {
                    "nombre": "hablar",
                    "parametros": {"texto_a_comunicar": "Ya pude responder."},
                },
            },
        )

    entradas = iter(["primer intento", "segundo intento", "tercer intento", "/salir"])
    salidas: list[str] = []
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://lazzaro.test",
    )
    client = TerminalChatClient(http_client=http_client)
    try:
        codigo = ejecutar_chat(
            client,
            input_fn=lambda prompt: next(entradas),
            output_fn=salidas.append,
        )
    finally:
        http_client.close()

    assert_true(codigo == 0, "Los errores de red no deben derribar el REPL.")
    assert_true(
        len([linea for linea in salidas if linea.startswith("[error]")]) == 2,
        "Debe informar conexion y timeout como errores recuperables.",
    )
    assert_true(
        "Lazzaro > Ya pude responder." in salidas,
        "Debe aceptar un turno posterior cuando el backend se recupera.",
    )


def test_cliente_rechaza_respuestas_malformadas() -> None:
    respuestas = iter(
        [
            httpx.Response(200, content=b"{json roto"),
            httpx.Response(200, json={"status": "ok", "accion": None}),
            httpx.Response(
                200,
                json={"status": "ok", "accion": {"nombre": "hablar", "parametros": []}},
            ),
            httpx.Response(
                200,
                json={
                    "status": "ok",
                    "accion": {
                        "nombre": "hablar",
                        "parametros": {"texto_a_comunicar": None},
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "status": "ok",
                    "accion": {
                        "nombre": "hablar",
                        "parametros": {"texto_a_comunicar": 123},
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "accion": {
                        "nombre": "hablar",
                        "parametros": {"texto_a_comunicar": "Falta status."},
                    },
                },
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(respuestas)

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://lazzaro.test",
    )
    client = TerminalChatClient(http_client=http_client)
    errores = 0
    try:
        for indice in range(6):
            try:
                client.enviar_mensaje(f"turno invalido {indice}")
            except TerminalChatError:
                errores += 1
    finally:
        http_client.close()

    assert_true(errores == 6, "Debe rechazar cada forma de respuesta fuera del contrato.")


def test_comandos_locales_no_se_envian_como_conversacion() -> None:
    metodos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        metodos.append(f"{request.method} {request.url.path}")
        if request.url.path == "/api/v1/estado":
            return httpx.Response(
                200,
                json={
                    "energia": 72,
                    "estres": 8,
                    "confianza": 61,
                    "motivacion": 80,
                    "foco": 75,
                    "tarea_actual": None,
                    "dopamina": 61,
                    "personalidad": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "accion": {
                    "id_accion": "accion-chat",
                    "nombre": "hablar",
                    "parametros": {"texto_a_comunicar": "Aqui estoy."},
                    "razonamiento": "MARCADOR_PRIVADO_RAZON",
                    "pensamiento_privado": "MARCADOR_PRIVADO_PENSAMIENTO",
                },
            },
        )

    entradas = iter(["   ", " /AYUDA ", "/desconocido", " /ESTADO ", "hola", "/salir"])
    salidas: list[str] = []
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://lazzaro.test",
    )
    client = TerminalChatClient(http_client=http_client)
    try:
        codigo = ejecutar_chat(
            client,
            input_fn=lambda prompt: next(entradas),
            output_fn=salidas.append,
        )
    finally:
        http_client.close()

    assert_true(codigo == 0, "El REPL debe cerrar correctamente.")
    assert_true(HELP_TEXT in salidas, "/ayuda debe resolverse localmente.")
    assert_true(
        metodos == ["GET /api/v1/estado", "POST /api/v1/comando"],
        "Solo /estado y el mensaje normal deben tocar el backend.",
    )
    assert_true(
        any(linea == "Lazzaro > Aqui estoy." for linea in salidas),
        "Debe imprimir la respuesta publica.",
    )
    salida_completa = "\n".join(salidas)
    assert_true(
        "MARCADOR_PRIVADO" not in salida_completa,
        "El REPL nunca debe mostrar razonamiento ni pensamiento privado.",
    )


def test_repl_cierra_limpio_si_se_interrumpe_una_solicitud() -> None:
    class InterruptClient:
        @staticmethod
        def enviar_mensaje(mensaje: str) -> str:
            raise KeyboardInterrupt

    entradas = iter(["hola"])
    salidas: list[str] = []
    codigo = ejecutar_chat(
        InterruptClient(),
        input_fn=lambda prompt: next(entradas),
        output_fn=salidas.append,
    )

    assert_true(codigo == 0, "Ctrl+C durante una solicitud debe cerrar sin traceback.")
    assert_true(salidas[-1] == "\nCerrando chat.", "Debe informar el cierre limpio.")

    class InterruptStatusClient:
        @staticmethod
        def obtener_estado() -> dict:
            raise KeyboardInterrupt

    salidas_estado: list[str] = []
    codigo_estado = ejecutar_chat(
        InterruptStatusClient(),
        input_fn=lambda prompt: "/estado",
        output_fn=salidas_estado.append,
    )
    assert_true(codigo_estado == 0, "Ctrl+C durante /estado debe cerrar limpiamente.")

    def eof_input(prompt: str) -> str:
        raise EOFError

    salidas_eof: list[str] = []
    codigo_eof = ejecutar_chat(
        InterruptClient(),
        input_fn=eof_input,
        output_fn=salidas_eof.append,
    )
    assert_true(codigo_eof == 0, "EOF debe cerrar sin traceback.")


def test_api_comando_acepta_terminal_y_fuerza_evento_conversacional() -> None:
    class FakeOrchestrator:
        def __init__(self):
            self.eventos: list[EventoEntrante] = []

        def procesar_evento(self, evento: EventoEntrante) -> AccionSaliente:
            self.eventos.append(evento)
            return AccionSaliente(
                id_accion="api-terminal",
                nombre="hablar",
                parametros={"texto_a_comunicar": "Contrato aceptado."},
            )

    fake = FakeOrchestrator()
    app = FastAPI()
    app.state.orchestrator = fake
    app.include_router(router)

    with TestClient(app) as api_client:
        response = api_client.post(
            "/api/v1/comando",
            json={
                "origen": "terminal",
                "tipo_evento": "actualizacion_estado",
                "id_transaccion": "api-terminal-1",
                "detalles": {"mensaje": "hola"},
            },
        )

    assert_true(response.status_code == 200, "La API debe aceptar el origen terminal.")
    assert_true(len(fake.eventos) == 1, "La API debe entregar un solo evento al orquestador.")
    assert_true(
        fake.eventos[0].tipo_evento == "comando_usuario",
        "/comando debe forzar el contrato conversacional.",
    )
    assert_true(fake.eventos[0].origen == "terminal", "Debe conservar el origen terminal.")


class _FakeClock:
    def __init__(self, instante: float = 1_000.0):
        self._instante = float(instante)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._instante

    def avanzar(self, segundos: float) -> None:
        with self._lock:
            self._instante += float(segundos)


class _TrackingStateEngine(StateEngine):
    def __init__(self, *, clock: _FakeClock):
        super().__init__(clock=clock)
        self.descansos_iniciados = 0
        self.descansos_finalizados = 0
        self.bloquear_finalizacion = False
        self.finalizacion_iniciada = threading.Event()
        self.liberar_finalizacion = threading.Event()

    def iniciar_descanso(self) -> bool:
        iniciado = super().iniciar_descanso()
        if iniciado:
            self.descansos_iniciados += 1
        return iniciado

    def finalizar_descanso(self) -> dict[str, float | bool]:
        if self.bloquear_finalizacion:
            self.finalizacion_iniciada.set()
            assert_true(
                self.liberar_finalizacion.wait(timeout=2),
                "La prueba debe liberar la finalizacion del descanso.",
            )
        resultado = super().finalizar_descanso()
        if resultado["aplicado"]:
            self.descansos_finalizados += 1
        return resultado


class _TrackingIdentity(IdentityState):
    def __init__(self, behavior):
        super().__init__(behavior)
        self.eventos = behavior.eventos

    def registrar_evento(self, **kwargs) -> None:
        super().registrar_evento(**kwargs)


class _FakeBehavior:
    def __init__(self, state_engine: StateEngine | None = None):
        self.state_engine = state_engine or StateEngine()
        self.eventos: list[dict] = []

    def capture_identity_components(self):
        state = self.state_engine.obtener_estado_biologico()
        return (
            {key: state[key] for key in ("energia", "estres", "foco")},
            {
                "resiliencia": 0.0,
                "impulsividad": 0.0,
                "paciencia": 0.0,
                "sociabilidad": 0.0,
                "metodico": 0.0,
            },
        )

    def registrar_impacto_identidad(self, **kwargs) -> None:
        self.eventos.append(dict(kwargs))
        intensidad = float(kwargs.get("intensidad", 1.0))
        route = str(kwargs.get("route", ""))
        success = kwargs.get("success")
        if kwargs.get("es_descanso"):
            self.state_engine.aplicar_cambios_biologicos(
                delta_energia=40.0,
                delta_estres=-30.0,
                foco_absoluto=100.0,
            )
            return
        delta_e = -5.0 * intensidad if route == "motor" else (-3.0 * intensidad if route == "oracle" else (-1.0 * intensidad if route == "persona" else 0.0))
        delta_s = 2.0 * intensidad if route == "oracle" else 0.0
        delta_f = -2.0 * intensidad if route == "motor" else (3.0 * intensidad if route == "persona" else 0.0)
        if success is False:
            delta_s += 15.0 * intensidad
            delta_f -= 10.0 * intensidad
        self.state_engine.aplicar_cambios_biologicos(
            delta_energia=delta_e,
            delta_estres=delta_s,
            delta_foco=delta_f,
        )

    def iniciar_descanso_biologico(self):
        return self.state_engine.iniciar_descanso()

    def finalizar_descanso_biologico(self):
        return self.state_engine.finalizar_descanso()

    @property
    def descansando(self):
        return self.state_engine.descansando

    def procesar_evento(self, tipo_evento: str, detalles: dict) -> dict:
        estado = {}
        self.state_engine.aplicar_evento(tipo_evento, contexto=detalles)
        estado = self.state_engine.obtener_estado()
        return {
            "estado": estado,
            "narrativa": "",
            "contexto_llm": {},
            "tarea": None,
            "policy_activa": "exploradora",
            "es_interrupcion_critica": False,
            "tipo_memoria": tipo_evento,
            "percepcion_resumida": "",
        }

    def obtener_resumen(self) -> dict:
        estado = self.state_engine.obtener_estado() if self.state_engine is not None else {}
        return {
            "estado": estado,
            "personalidad": {},
            "tarea": None,
        }


class _FakeMemory:
    def __init__(self):
        self.experiencias: list[dict] = []
        self.acciones: list[dict] = []
        self.turnos: list[dict] = []
        self.ram_publica: list[tuple[str, str]] = [
            ("Hablamos del cactus azul.", "Lo recuerdo en la RAM actual."),
            ("También mencionamos un faro.", "El faro sigue en el segundo turno."),
        ]
        self.consolidaciones = 0
        self.guardados_manuales: list[str] = []
        self.resultados_guardado_manual: deque[ManualConversationSaveResult] = deque()
        self.guardado_manual_delay = 0.0

    def obtener_contexto_memoria(self, **kwargs) -> str:
        return ""

    def obtener_contexto_conversacional(self, n: int = 6) -> str:
        return ""

    def obtener_contexto_relacional(self, **kwargs) -> str:
        return ""

    def obtener_ultimas_acciones(self) -> str:
        return ""

    def guardar_experiencia(self, **kwargs) -> None:
        self.experiencias.append(kwargs)

    def agregar_accion(self, **kwargs) -> None:
        self.acciones.append(kwargs)

    def registrar_turno_conversacional(self, **kwargs) -> None:
        self.turnos.append(kwargs)

    def consolidar_memorias_si_hace_falta(self) -> None:
        self.consolidaciones += 1

    def guardar_fotografia_conversacional(
        self,
        id_transaccion: str,
    ) -> ManualConversationSaveResult:
        if self.guardado_manual_delay:
            time.sleep(self.guardado_manual_delay)
        self.guardados_manuales.append(id_transaccion)
        if self.resultados_guardado_manual:
            return self.resultados_guardado_manual.popleft()
        if not self.ram_publica:
            return ManualConversationSaveResult(status="empty")
        return ManualConversationSaveResult(
            status="saved",
            memory_id="manual-memory-test",
            fingerprint="manual-fingerprint-test",
            turn_count=len(self.ram_publica),
        )


class _FakeRegistry:
    def __init__(self):
        self.descripciones_solicitadas: list[list[str]] = []

    @staticmethod
    def listar_nombres() -> list[str]:
        return ["hablar", "navegar_a_objeto", "cancelar_tarea"]

    def describir_herramientas(self, nombres: list[str]) -> dict[str, str]:
        self.descripciones_solicitadas.append(list(nombres))
        return {nombre: nombre for nombre in nombres}

    @staticmethod
    def get_tool(nombre: str) -> dict | None:
        if nombre in {"hablar", "navegar_a_objeto", "cancelar_tarea"}:
            return {"requires_confirmation": False}
        return None


class _FakeTriage:
    def __init__(self, route: str, intent: str | None = None):
        self.route = route
        self.intent = intent

    def route_event(self, evento: dict, estado_agente: dict) -> dict:
        return {
            "intent": self.intent or f"TEST_{self.route.upper()}",
            "route": self.route,
            "confidence": 0.99,
            "requires_memory": self.route in {"persona", "oracle"},
            "requires_oracle": self.route == "oracle",
            "reason": "Decision de prueba.",
        }


class _FakeIdentity:
    def __init__(self):
        self.eventos: list[dict] = []

    def registrar_evento(self, **kwargs) -> None:
        self.eventos.append(kwargs)

    @staticmethod
    def inyectar_en_prompt() -> str:
        return "Estado de prueba."


class _BombReasoning:
    @staticmethod
    def process(**kwargs):
        raise AssertionError("El fallback general no debe ejecutarse para terminal.")


def _orquestador_aislado(
    route: str,
    intent: str | None = None,
    state_engine: StateEngine | None = None,
) -> tuple[Orchestrator, dict[str, int]]:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.behavior = _FakeBehavior(state_engine=state_engine)
    orchestrator.memory = _FakeMemory()
    orchestrator.reasoning = _BombReasoning()
    orchestrator.tool_registry = _FakeRegistry()
    orchestrator.triage_router = _FakeTriage(route, intent=intent)
    orchestrator.tool_policy = ToolPolicy()
    orchestrator.identity_provider = IdentitySnapshotProvider(
        build_static_self_v1(), orchestrator.behavior
    )
    orchestrator.identity_state = _TrackingIdentity(orchestrator.behavior)
    orchestrator._transacciones_procesadas = OrderedDict()
    orchestrator._guardados_conversacionales_pendientes = OrderedDict()
    orchestrator._alias_candidates_pendientes = OrderedDict()
    orchestrator._notificaciones = deque(maxlen=50)
    orchestrator._transacciones_lock_guard = threading.Lock()
    orchestrator._transacciones_locks = {}
    orchestrator._descanso_terminal_lock = threading.RLock()
    orchestrator._descanso_terminal_pendiente = None

    llamadas = {"persona": 0, "oracle": 0, "motor": 0, "cancelacion": 0}

    def resolver_persona(**kwargs) -> ActionResponse:
        llamadas["persona"] += 1
        return ActionResponse(
            pensamiento="Respuesta privada de prueba.",
            accion="hablar",
            parametros={"texto_a_comunicar": "Solo estoy conversando."},
            razon="Ruta Persona.",
        )

    def resolver_oracle(**kwargs) -> ActionResponse:
        llamadas["oracle"] += 1
        return ActionResponse(
            pensamiento="Conocimiento privado de prueba.",
            accion="hablar",
            parametros={"texto_a_comunicar": "Respuesta tecnica conversada."},
            razon="Ruta Oracle verbalizada.",
        )

    def resolver_motor(**kwargs) -> ActionResponse:
        llamadas["motor"] += 1
        raise AssertionError("La ruta motora no debe ejecutarse para terminal.")

    def resolver_cancelacion(*args, **kwargs) -> ActionResponse:
        llamadas["cancelacion"] += 1
        raise AssertionError("Terminal no debe activar la cancelacion de tareas.")

    orchestrator._resolver_ruta_persona = resolver_persona
    orchestrator._resolver_ruta_oracle = resolver_oracle
    orchestrator._resolver_ruta_motor = resolver_motor
    orchestrator._resolver_cancelacion_tarea_usuario = resolver_cancelacion
    return orchestrator, llamadas


def test_flujo_terminal_reconduce_motor_y_no_cancela_tareas() -> None:
    orchestrator, llamadas = _orquestador_aislado("motor")

    accion = orchestrator.procesar_evento(_evento_terminal("detente"))

    assert_true(accion is not None and accion.nombre == "hablar", "Terminal debe terminar en hablar.")
    assert_true(llamadas["persona"] == 1, "La frase motora debe llegar a Persona.")
    assert_true(llamadas["motor"] == 0, "No debe entrar a MotorEngine.")
    assert_true(llamadas["cancelacion"] == 0, "No debe cancelar la tarea activa.")
    assert_true(
        orchestrator.tool_registry.descripciones_solicitadas == [["hablar"]],
        "El flujo completo solo debe ofrecer la herramienta hablar.",
    )
    assert_true(
        orchestrator.memory.turnos[0]["route"] == "persona",
        "La RAM debe registrar la ruta efectiva Persona.",
    )
    assert_true(
        orchestrator.memory.turnos[0]["metadata"]["origen"] == "terminal",
        "La RAM debe conservar el origen terminal.",
    )


def test_flujo_terminal_conserva_oracle_y_verbaliza() -> None:
    orchestrator, llamadas = _orquestador_aislado("oracle")

    accion = orchestrator.procesar_evento(_evento_terminal("explica este error de arquitectura"))

    assert_true(accion is not None and accion.nombre == "hablar", "Oracle debe terminar verbalizado.")
    assert_true(llamadas["oracle"] == 1, "El terminal debe conservar la ruta Oracle.")
    assert_true(llamadas["motor"] == 0, "Oracle no debe desviarse al motor.")


def test_orchestrator_reconduce_motor_y_blinda_la_salida() -> None:
    evento = _evento_terminal("usa el cubo")
    decision = {
        "intent": "MOTOR_SIMPLE",
        "route": "motor",
        "confidence": 0.9,
        "requires_memory": False,
        "requires_oracle": False,
        "reason": "Clasificacion motora.",
    }

    adaptada = Orchestrator._adaptar_triage_para_terminal(evento, decision)
    assert_true(adaptada["route"] == "persona", "Terminal debe reconducir motor a Persona.")
    assert_true(adaptada["requires_memory"] is True, "Persona debe conservar acceso a memoria.")
    assert_true(
        adaptada["terminal_original_route"] == "motor",
        "Debe conservar la ruta original para auditoria.",
    )

    respuesta_motora = ActionResponse(
        pensamiento="Intento fisico.",
        accion="navegar_a_objeto",
        parametros={"nombre_objeto": "Cube"},
        razon="Prueba.",
    )
    blindada = Orchestrator._blindar_salida_terminal(evento, respuesta_motora)
    assert_true(blindada.accion == "hablar", "La barrera final debe producir hablar.")
    assert_true(
        blindada.metadata["terminal_action_blocked"] == "navegar_a_objeto",
        "Debe auditar la accion bloqueada.",
    )

    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = _FakeRegistry()
    hablar_invalido = ActionResponse(
        pensamiento="Respuesta fallida.",
        accion="hablar",
        parametros={},
        valido=False,
        error="Contrato social invalido.",
    )
    sanitizada = orchestrator._sanitizar_action_response(hablar_invalido)
    reblindada = orchestrator._blindar_salida_terminal(evento, sanitizada)
    assert_true(
        reblindada.accion == "none" and reblindada.valido is False,
        "El firewall terminal no debe convertir INVALID en una accion valida.",
    )


def test_oracle_se_conserva_y_tool_policy_solo_expone_hablar() -> None:
    evento = _evento_terminal("explicame este error")
    oracle = {
        "intent": "ORACLE_QUERY",
        "route": "oracle",
        "confidence": 0.95,
        "requires_memory": True,
        "requires_oracle": True,
        "reason": "Consulta tecnica.",
    }
    adaptada = Orchestrator._adaptar_triage_para_terminal(evento, oracle)
    assert_true(adaptada["route"] == "oracle", "Terminal debe conservar acceso al Oraculo.")

    herramientas = ToolPolicy().seleccionar(
        ["hablar", "navegar_a_objeto", "escribir_teclado"],
        origen="terminal",
        route="motor",
    )
    assert_true(herramientas == ["hablar"], "Terminal solo debe exponer hablar en toda ruta.")


def test_state_engine_descanso_temporal_proyecta_sin_mutar_y_no_acumula() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    estado.energia = 40.0

    assert_true(estado.iniciar_descanso() is True, "El primer descanso debe iniciar.")
    assert_true(
        estado.iniciar_descanso() is False,
        "Una segunda solicitud no debe acumular otro descanso.",
    )
    reloj.avanzar(25.0)

    proyectado_a = estado.obtener_estado()
    proyectado_b = estado.obtener_estado()
    assert_true(
        abs(proyectado_a["energia"] - 42.5) < 1e-6,
        "El descanso debe proyectar exactamente 0.10 de energia por segundo.",
    )
    assert_true(
        proyectado_b["energia"] == proyectado_a["energia"],
        "Consultar el estado no debe acumular dos veces el mismo intervalo.",
    )
    assert_true(
        abs(estado.energia - 40.0) < 1e-6,
        "La proyeccion publica no debe mutar la reserva antes de despertar.",
    )

    resultado = estado.finalizar_descanso()
    assert_true(resultado["aplicado"] is True, "El siguiente mensaje debe liquidar el descanso.")
    assert_true(
        abs(float(resultado["energia_recuperada"]) - 2.5) < 1e-6,
        "La liquidacion debe informar solo la energia acumulada durante el intervalo.",
    )
    assert_true(abs(estado.energia - 42.5) < 1e-6, "La energia proyectada debe quedar aplicada.")
    assert_true(estado.descansando is False, "La recuperacion debe detenerse al despertar.")
    assert_true(
        estado.finalizar_descanso()["aplicado"] is False,
        "Finalizar otra vez no debe duplicar la recuperacion.",
    )

    reloj_tope = _FakeClock()
    estado_tope = StateEngine(clock=reloj_tope)
    estado_tope.energia = 99.5
    estado_tope.iniciar_descanso()
    reloj_tope.avanzar(20.0)
    assert_true(
        estado_tope.obtener_estado()["energia"] == 100.0,
        "La proyeccion del descanso debe respetar el tope biologico.",
    )
    estado_tope.finalizar_descanso()
    assert_true(estado_tope.energia == 100.0, "La liquidacion tampoco debe superar 100.")


def test_descanso_parcial_relaja_sin_decaer_foco_ni_aplicar_fatiga_activa() -> None:
    reloj = _FakeClock()
    estado = StateEngine(clock=reloj)
    estado.energia = 50.0
    estado.estres = 80.0
    estado.foco = 60.0
    estado.carga_cognitiva = 40.0
    segundos = 10.0

    estado.iniciar_descanso()
    reloj.avanzar(segundos)
    proyectado = estado.obtener_estado()
    energia_esperada = 50.0 + estado.RECUPERACION_DESCANSO_POR_SEGUNDO * segundos
    estres_esperado = 80.0 - estado.RECUPERACION_ESTRES_POR_SEGUNDO * segundos
    carga_esperada = 40.0 - estado.RECUPERACION_CARGA_POR_SEGUNDO * segundos

    assert_true(
        abs(proyectado["energia"] - energia_esperada) < 1e-6,
        "Durante descanso solo debe aplicarse la tasa positiva, incluso con estres alto.",
    )
    assert_true(
        abs(proyectado["estres"] - estres_esperado) < 1e-6,
        "El estres debe relajarse segun su constante temporal.",
    )
    assert_true(
        abs(proyectado["carga_cognitiva"] - carga_esperada) < 1e-6,
        "La carga debe relajarse segun su constante temporal.",
    )
    assert_true(
        proyectado["foco"] == 60.0,
        "El foco no debe decaer ni recibir la penalizacion activa por estres alto.",
    )
    assert_true(
        estado.energia == 50.0
        and estado.estres == 80.0
        and estado.foco == 60.0
        and estado.carga_cognitiva == 40.0,
        "La lectura parcial debe seguir siendo una proyeccion sin mutaciones.",
    )

    estado.finalizar_descanso()
    assert_true(
        abs(estado.energia - energia_esperada) < 1e-6
        and abs(estado.estres - estres_esperado) < 1e-6
        and estado.foco == 60.0
        and abs(estado.carga_cognitiva - carga_esperada) < 1e-6,
        "Al despertar deben liquidarse exactamente los valores proyectados.",
    )


def test_identity_state_y_state_engine_comparten_reserva_biologica() -> None:
    reloj = _FakeClock()
    estado = StateEngine(clock=reloj)
    identidad = IdentityState(_FakeBehavior(estado))
    estado.energia = 50.0

    identidad.registrar_evento(route="persona", success=True)
    assert_true(
        identidad.energia == estado.energia == 49.0,
        "Persona y StateEngine deben descontar sobre una sola reserva de energia.",
    )

    estado.iniciar_descanso()
    reloj.avanzar(20.0)
    estado.finalizar_descanso()
    assert_true(
        identidad.energia == estado.energia == 51.0,
        "La recuperacion canonica debe ser visible inmediatamente para Persona.",
    )


def test_descanso_terminal_proyecta_en_estado_y_despierta_antes_de_responder() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CORE_SYSTEM",
        state_engine=estado,
    )
    energias_vistas: list[float] = []
    descansando_al_responder: list[bool] = []

    def resolver_persona(**kwargs) -> ActionResponse:
        energias_vistas.append(orchestrator.identity_state.energia)
        descansando_al_responder.append(estado.descansando)
        return ActionResponse(
            pensamiento="Respuesta privada de prueba.",
            accion="hablar",
            parametros={"texto_a_comunicar": "Descansare hasta que vuelvas."},
            razon="Ruta Persona.",
        )

    orchestrator._resolver_ruta_persona = resolver_persona
    solicitud = _evento_terminal("descansa", "rest-projection")
    respuesta = orchestrator.procesar_evento(solicitud)

    assert_true(respuesta is not None and respuesta.nombre == "hablar", "Debe responder conversando.")
    assert_true(
        descansando_al_responder == [False],
        "El descanso debe armarse despues de generar la respuesta a la solicitud.",
    )
    assert_true(estado.descansando is True, "La solicitud CORE_SYSTEM debe dejar descanso activo.")
    assert_true(estado.descansos_iniciados == 1, "Debe iniciar un solo periodo.")
    energia_base = estado.energia

    reloj.avanzar(25.0)
    app = FastAPI()
    app.state.orchestrator = orchestrator
    app.include_router(router)
    with TestClient(app) as api_client:
        estado_a = api_client.get("/api/v1/estado")
        estado_b = api_client.get("/api/v1/estado")

    assert_true(estado_a.status_code == 200 and estado_b.status_code == 200, "/estado debe responder.")
    energia_proyectada = min(100.0, energia_base + 2.5)
    assert_true(
        abs(float(estado_a.json()["energia"]) - energia_proyectada) < 1e-6,
        "/estado debe proyectar la recuperacion acumulada.",
    )
    assert_true(
        estado_b.json()["energia"] == estado_a.json()["energia"],
        "Dos consultas de estado no deben aplicar dos veces el intervalo.",
    )
    assert_true(
        abs(estado.energia - energia_base) < 1e-6 and estado.descansando,
        "/estado no debe despertar ni mutar la energia almacenada.",
    )

    orchestrator.triage_router.intent = "SOCIAL"
    siguiente = orchestrator.procesar_evento(_evento_terminal("ya volvi", "wake-projection"))
    assert_true(siguiente is not None and siguiente.nombre == "hablar", "El despertar debe conversar.")
    assert_true(
        abs(energias_vistas[-1] - energia_proyectada) < 1e-6,
        "Persona debe recibir la energia recuperada antes de responder al siguiente mensaje.",
    )
    assert_true(
        descansando_al_responder[-1] is False,
        "El siguiente turno debe cerrar la recuperacion antes de entrar a Persona.",
    )
    assert_true(estado.descansos_finalizados == 1, "El periodo debe liquidarse una sola vez.")
    assert_true(estado.descansando is False, "El siguiente mensaje debe detener la recuperacion.")
    energia_tras_despertar = estado.energia
    reloj.avanzar(100.0)
    assert_true(
        orchestrator.obtener_estado_publico().energia == round(energia_tras_despertar, 2),
        "Sin un nuevo descanso, el estado no debe seguir proyectando recuperacion.",
    )
    assert_true(orchestrator.memory.consolidaciones == 0, "Terminal no debe consolidar al descansar.")


def test_segunda_peticion_core_system_despierta_y_rearma_sin_apilar_tasas() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CORE_SYSTEM",
        state_engine=estado,
    )
    descansando_al_responder: list[bool] = []

    def resolver_persona(**kwargs) -> ActionResponse:
        descansando_al_responder.append(estado.descansando)
        return ActionResponse(
            pensamiento="Respuesta privada de prueba.",
            accion="hablar",
            parametros={"texto_a_comunicar": "De acuerdo."},
            razon="Ruta Persona.",
        )

    orchestrator._resolver_ruta_persona = resolver_persona
    orchestrator.procesar_evento(_evento_terminal("descansa", "rest-sequential-a"))
    energia_primer_inicio = estado.energia
    reloj.avanzar(10.0)

    segunda = orchestrator.procesar_evento(
        _evento_terminal("descansa otro rato", "rest-sequential-b")
    )
    assert_true(segunda is not None and segunda.nombre == "hablar", "La segunda peticion debe hablar.")
    assert_true(
        descansando_al_responder == [False, False],
        "Cada solicitud debe responder fuera del periodo anterior y rearmarse despues.",
    )
    assert_true(
        estado.descansos_iniciados == 2 and estado.descansos_finalizados == 1,
        "La segunda peticion debe liquidar el primer periodo y abrir exactamente otro.",
    )
    assert_true(estado.descansando is True, "El segundo periodo debe quedar activo.")
    assert_true(
        abs(estado.energia - energia_primer_inicio) < 1e-6,
        "La primera recuperacion y el coste del segundo turno deben aplicarse una vez cada uno.",
    )

    energia_segundo_inicio = estado.energia
    reloj.avanzar(10.0)
    assert_true(
        abs(
            estado.obtener_estado()["energia"]
            - min(100.0, energia_segundo_inicio + estado.RECUPERACION_DESCANSO_POR_SEGUNDO * 10.0)
        )
        < 1e-6,
        "El segundo periodo debe recuperar a 0.10/s, no a una tasa apilada.",
    )

    orchestrator.triage_router.intent = "SOCIAL"
    orchestrator.procesar_evento(_evento_terminal("volvi", "rest-sequential-wake"))
    assert_true(
        estado.descansos_iniciados == 2 and estado.descansos_finalizados == 2,
        "El siguiente mensaje debe cerrar solo el segundo periodo.",
    )
    assert_true(estado.descansando is False, "No debe quedar descanso residual.")
    assert_true(orchestrator.memory.consolidaciones == 0, "Ninguno de los periodos debe consolidar.")


def test_ayuda_estado_y_salir_no_despiertan_descanso_terminal() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CORE_SYSTEM",
        state_engine=estado,
    )
    orchestrator.procesar_evento(_evento_terminal("descansa", "local-commands-rest"))
    reloj.avanzar(15.0)

    class RestingClient:
        @staticmethod
        def enviar_mensaje(mensaje: str) -> str:
            raise AssertionError("Los comandos locales no deben enviarse como conversacion.")

        @staticmethod
        def obtener_estado() -> dict:
            return orchestrator.obtener_estado_publico().model_dump(mode="python")

    entradas = iter(["/ayuda", "/estado", "/salir"])
    salidas: list[str] = []
    codigo = ejecutar_chat(
        RestingClient(),
        input_fn=lambda prompt: next(entradas),
        output_fn=salidas.append,
    )

    assert_true(codigo == 0, "El REPL debe cerrar normalmente.")
    assert_true(HELP_TEXT in salidas, "La ayuda debe resolverse dentro del cliente.")
    assert_true(estado.descansando is True, "Ayuda, estado y salir no deben despertar.")
    assert_true(estado.descansos_finalizados == 0, "Ningun comando local debe consumir el periodo.")
    assert_true(orchestrator.memory.consolidaciones == 0, "Los comandos locales no consolidan.")


def test_retries_cacheados_no_rearman_ni_despiertan_descanso_terminal() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    orchestrator, _ = _orquestador_aislado("persona", intent="SOCIAL", state_engine=estado)

    evento_anterior = _evento_terminal("turno anterior", "cached-before-rest")
    accion_anterior = orchestrator.procesar_evento(evento_anterior)
    orchestrator.triage_router.intent = "CORE_SYSTEM"
    solicitud = _evento_terminal("descansa", "cached-rest")
    accion_descanso = orchestrator.procesar_evento(solicitud)
    energia_base = estado.energia
    reloj.avanzar(10.0)

    retry_anterior = orchestrator.procesar_evento(evento_anterior)
    retry_descanso = orchestrator.procesar_evento(solicitud)
    assert_true(
        retry_anterior is not None
        and accion_anterior is not None
        and retry_anterior.id_accion == accion_anterior.id_accion,
        "Un retry anterior debe seguir saliendo del cache.",
    )
    assert_true(
        retry_descanso is not None
        and accion_descanso is not None
        and retry_descanso.id_accion == accion_descanso.id_accion,
        "Reintentar la solicitud no debe crear un segundo periodo.",
    )
    assert_true(estado.descansando is True, "Los retries cacheados no deben despertar.")
    assert_true(estado.descansos_iniciados == 1, "El retry no debe rearmar el reloj.")
    assert_true(
        abs(estado.obtener_estado()["energia"] - min(100.0, energia_base + 1.0)) < 1e-6,
        "La proyeccion debe conservar el inicio original del descanso.",
    )

    orchestrator.triage_router.intent = "SOCIAL"
    despertar = _evento_terminal("mensaje nuevo", "cached-wake")
    accion_despertar = orchestrator.procesar_evento(despertar)
    energia_final = estado.energia
    reloj.avanzar(30.0)
    retry_despertar = orchestrator.procesar_evento(despertar)
    assert_true(
        retry_despertar is not None
        and accion_despertar is not None
        and retry_despertar.id_accion == accion_despertar.id_accion,
        "El retry del mensaje despertador debe devolver la accion cacheada.",
    )
    assert_true(estado.descansos_finalizados == 1, "El retry no debe recuperar dos veces.")
    assert_true(estado.energia == energia_final, "El retry no debe volver a mutar la biologia.")
    assert_true(orchestrator.memory.consolidaciones == 0, "Los retries no deben consolidar.")


class _ContendedRLock:
    def __init__(self):
        self._lock = threading.RLock()
        self._attempt_guard = threading.Lock()
        self._attempts = 0
        self.second_attempt = threading.Event()

    def __enter__(self):
        with self._attempt_guard:
            self._attempts += 1
            if self._attempts >= 2:
                self.second_attempt.set()
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._lock.release()
        return False


def test_mensajes_concurrentes_consumen_descanso_terminal_una_sola_vez() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CORE_SYSTEM",
        state_engine=estado,
    )
    orchestrator.procesar_evento(_evento_terminal("descansa", "concurrent-rest"))
    reloj.avanzar(10.0)
    orchestrator.triage_router.intent = "SOCIAL"
    estado.bloquear_finalizacion = True
    descanso_lock = _ContendedRLock()
    orchestrator._descanso_terminal_lock = descanso_lock

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            orchestrator.procesar_evento,
            _evento_terminal("primer despertar", "concurrent-wake-a"),
        )
        assert_true(
            estado.finalizacion_iniciada.wait(timeout=2),
            "La primera solicitud debe entrar a la liquidacion.",
        )
        second = executor.submit(
            orchestrator.procesar_evento,
            _evento_terminal("segundo despertar", "concurrent-wake-b"),
        )
        assert_true(
            descanso_lock.second_attempt.wait(timeout=2),
            "El segundo ID debe solaparse sobre el lock de descanso.",
        )
        estado.liberar_finalizacion.set()
        acciones = [first.result(timeout=2), second.result(timeout=2)]

    assert_true(
        all(accion is not None and accion.nombre == "hablar" for accion in acciones),
        "Ambos mensajes nuevos deben conservar una respuesta conversacional.",
    )
    assert_true(estado.descansos_finalizados == 1, "Solo un mensaje debe liquidar el descanso.")
    assert_true(estado.descansando is False, "El latch debe quedar consumido.")
    assert_true(orchestrator.memory.consolidaciones == 0, "La carrera no debe consolidar memoria.")


def test_guardado_manual_despierta_y_same_id_no_repite_persistencia() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CORE_SYSTEM",
        state_engine=estado,
    )
    orchestrator.procesar_evento(_evento_terminal("descansa", "save-rest"))
    reloj.avanzar(20.0)
    orchestrator.triage_router.intent = "CONVERSATION_SAVE"
    orchestrator.memory.resultados_guardado_manual.extend(
        (
            ManualConversationSaveResult(status="error", error="fallo transitorio", turn_count=2),
            ManualConversationSaveResult(
                status="saved",
                memory_id="manual-after-rest",
                fingerprint="manual-after-rest-fingerprint",
                turn_count=2,
            ),
        )
    )
    guardar = _evento_terminal("guarda lo que hemos hablado", "save-wake")

    try:
        orchestrator.procesar_evento(guardar)
    except TurnProcessingError:
        pass
    else:
        raise AssertionError("El write manual requerido fallido no debe producir success.")
    energia_tras_primera = estado.energia
    reloj.avanzar(30.0)
    try:
        orchestrator.procesar_evento(guardar)
    except TurnAbortedError:
        pass
    else:
        raise AssertionError("El mismo ID debe devolver el clean abort durable.")

    assert_true(estado.descansos_finalizados == 1, "El guardado debe despertar exactamente una vez.")
    assert_true(estado.descansando is False, "El retry manual no debe rearmar el descanso.")
    assert_true(estado.energia == energia_tras_primera, "El retry manual no debe mutar otra vez la energia.")
    assert_true(
        orchestrator.memory.guardados_manuales == [guardar.id_transaccion],
        "El mismo ID no debe reintentar ni siquiera la persistencia manual.",
    )
    assert_true(orchestrator.memory.consolidaciones == 0, "Guardar al despertar no debe consolidar.")


def test_guardado_manual_con_palabras_de_sueno_no_arma_descanso() -> None:
    reloj = _FakeClock()
    estado = _TrackingStateEngine(clock=reloj)
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CONVERSATION_SAVE",
        state_engine=estado,
    )

    accion = orchestrator.procesar_evento(
        _evento_terminal("duerme y guarda nuestra charla", "save-sleep-control")
    )

    assert_true(accion is not None and accion.nombre == "hablar", "El control debe confirmar hablando.")
    assert_true(estado.descansos_iniciados == 0, "CONVERSATION_SAVE nunca debe armar descanso.")
    assert_true(estado.descansando is False, "El control no debe dejar recuperacion pendiente.")
    assert_true(orchestrator.memory.consolidaciones == 0, "El control nunca debe consolidar.")


def test_terminal_no_usa_descanso_inmediato_y_unity_lo_conserva() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.memory = _FakeMemory()
    orchestrator.behavior = _FakeBehavior()
    orchestrator.identity_state = _TrackingIdentity(orchestrator.behavior)
    respuesta = ActionResponse(
        pensamiento="",
        accion="hablar",
        parametros={"texto_a_comunicar": "Podemos hablar sobre dormir."},
    )

    orchestrator._registrar_estado_identidad(
        _evento_terminal("descansa y duerme un poco"),
        {"route": "persona", "intent": "TEST"},
        respuesta,
    )

    evento_unity = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="unity-descanso",
        detalles={"mensaje": "descansa y duerme un poco"},
    )
    orchestrator._registrar_estado_identidad(
        evento_unity,
        {"route": "persona", "intent": "TEST"},
        respuesta,
    )

    assert_true(
        orchestrator.memory.consolidaciones == 1,
        "Terminal no debe consolidar; Unity conserva su descanso inmediato.",
    )
    assert_true(
        orchestrator.identity_state.eventos[0]["es_descanso"] is False,
        "Terminal debe usar el periodo temporal, no el descanso biologico inmediato.",
    )
    assert_true(
        orchestrator.identity_state.eventos[1]["es_descanso"] is True,
        "El comportamiento previo de descanso en Unity debe mantenerse.",
    )

    control_guardado = ActionResponse(
        pensamiento="",
        accion="hablar",
        parametros={"texto_a_comunicar": "No pude guardar esta vez."},
        metadata={
            "manual_memory_save_control": True,
            "manual_memory_save_status": "error",
        },
    )
    orchestrator._registrar_estado_identidad(
        EventoEntrante(
            origen="unity",
            tipo_evento="comando_usuario",
            id_transaccion="unity-save-sleep-word",
            detalles={"mensaje": "duerme y guarda nuestra charla"},
        ),
        {"route": "persona", "intent": "CONVERSATION_SAVE"},
        control_guardado,
    )
    assert_true(
        orchestrator.memory.consolidaciones == 1,
        "El control de guardado nunca debe parecer descanso, tampoco desde Unity.",
    )
    assert_true(
        orchestrator.identity_state.eventos[-1]["es_descanso"] is False,
        "La metadata de control debe bloquear regeneración biológica.",
    )


def test_terminal_guarda_ram_como_conversacion_y_no_registra_el_control() -> None:
    orchestrator, llamadas = _orquestador_aislado(
        "persona",
        intent="CONVERSATION_SAVE",
    )
    evento = _evento_terminal("guarda lo que hemos hablado")

    accion = orchestrator.procesar_evento(evento)
    accion_retry = orchestrator.procesar_evento(evento)

    assert_true(accion is not None and accion.nombre == "hablar", "Debe confirmar hablando.")
    assert_true(
        accion.parametros["texto_a_comunicar"] == "Está bien, lo recordaré para después.",
        "Persona debe confirmar únicamente después del guardado.",
    )
    assert_true(accion_retry is not None, "El retry debe devolver la respuesta cacheada.")
    assert_true(
        accion_retry.id_accion == accion.id_accion,
        "La misma transacción debe devolver exactamente la acción cacheada.",
    )
    assert_true(
        orchestrator.memory.guardados_manuales == [evento.id_transaccion],
        "El retry exitoso no debe escribir dos veces.",
    )
    assert_true(
        orchestrator.memory.turnos == [],
        "La solicitud y confirmación de control no deben alterar la RAM.",
    )
    assert_true(
        len(orchestrator.memory.ram_publica) == 2,
        "La fotografía no debe vaciar la RAM pública simulada.",
    )
    assert_true(
        orchestrator.tool_registry.descripciones_solicitadas == [],
        "El guardado no debe exponerse como herramienta.",
    )
    assert_true(llamadas["persona"] == 0, "No debe pedir al LLM que decida si el guardado ocurrió.")
    assert_true(orchestrator.memory.consolidaciones == 0, "No debe activar consolidación.")
    assert_true(
        orchestrator.identity_state.eventos[0]["es_descanso"] is False,
        "El guardado manual no debe regenerar la biología.",
    )


def test_terminal_same_id_no_repite_guardado_fallido_ni_inventa_exito() -> None:
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CONVERSATION_SAVE",
    )
    orchestrator.memory.resultados_guardado_manual.extend(
        (
            ManualConversationSaveResult(
                status="error",
                error="fallo transitorio",
                turn_count=2,
            ),
            ManualConversationSaveResult(
                status="saved",
                memory_id="manual-after-retry",
                fingerprint="manual-after-retry-fingerprint",
                turn_count=2,
            ),
        )
    )
    evento = _evento_terminal("guarda lo que tengas en ram en tu mente")

    try:
        orchestrator.procesar_evento(evento)
    except TurnProcessingError:
        pass
    else:
        raise AssertionError("Un write requerido fallido no debe producir respuesta success.")
    try:
        orchestrator.procesar_evento(evento)
    except TurnAbortedError:
        pass
    else:
        raise AssertionError("El mismo ID debe devolver el clean abort durable.")
    assert_true(
        orchestrator.memory.guardados_manuales == [
            evento.id_transaccion,
        ],
        "La respuesta de error debe bloquear el replay de la misma transaccion.",
    )
    assert_true(
        evento.id_transaccion not in orchestrator._transacciones_procesadas,
        "Un fallo requerido nunca debe poblar el cache de success.",
    )
    assert_true(
        len(orchestrator.memory.experiencias) == 1,
        "El retry manual no debe volver a persistir el evento genérico.",
    )
    assert_true(
        len(orchestrator.memory.acciones) == 0,
        "Un guardado fallido no debe crear memoria operativa de decision.",
    )
    assert_true(
        len(orchestrator.identity_state.eventos) == 0,
        "Un guardado fallido no debe emitir señal de identidad.",
    )
    assert_true(orchestrator.memory.turnos == [], "Los intentos de control no deben entrar en RAM.")


def test_terminal_informa_ram_vacia_y_guardado_ya_existente() -> None:
    empty_orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CONVERSATION_SAVE",
    )
    empty_orchestrator.memory.ram_publica = []
    empty = empty_orchestrator.procesar_evento(
        _evento_terminal("guarda lo que hemos hablado")
    )
    assert_true(
        empty is not None
        and "no tengo una conversación reciente" in empty.parametros["texto_a_comunicar"],
        "RAM vacía debe producir una explicación, nunca éxito.",
    )

    existing_orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CONVERSATION_SAVE",
    )
    existing_orchestrator.memory.resultados_guardado_manual.append(
        ManualConversationSaveResult(
            status="already_saved",
            memory_id="existing-memory",
            fingerprint="existing-fingerprint",
            turn_count=2,
        )
    )
    existing = existing_orchestrator.procesar_evento(
        _evento_terminal("guarda lo que tengas en ram en tu mente")
    )
    assert_true(
        existing is not None
        and "ya estaba guardado" in existing.parametros["texto_a_comunicar"],
        "Una huella existente debe confirmarse sin afirmar una nueva escritura.",
    )


def test_retries_simultaneos_comparten_una_sola_transaccion() -> None:
    orchestrator, _ = _orquestador_aislado(
        "persona",
        intent="CONVERSATION_SAVE",
    )
    evento = _evento_terminal("guarda lo que hemos hablado")
    guardado_iniciado = threading.Event()
    liberar_guardado = threading.Event()
    guardar_original = orchestrator.memory.guardar_fotografia_conversacional

    def guardar_bloqueado(id_transaccion: str) -> ManualConversationSaveResult:
        guardado_iniciado.set()
        assert_true(
            liberar_guardado.wait(timeout=2),
            "La prueba debe liberar la primera escritura.",
        )
        return guardar_original(id_transaccion)

    class _TrackingRLock:
        def __init__(self):
            self._lock = threading.RLock()
            self._attempt_guard = threading.Lock()
            self._attempts = 0
            self.second_attempt = threading.Event()

        def __enter__(self):
            with self._attempt_guard:
                self._attempts += 1
                if self._attempts >= 2:
                    self.second_attempt.set()
            self._lock.acquire()
            return self

        def __exit__(self, exc_type, exc, traceback):
            self._lock.release()
            return False

    transaction_lock = _TrackingRLock()
    orchestrator.memory.guardar_fotografia_conversacional = guardar_bloqueado
    orchestrator._transacciones_locks[evento.id_transaccion] = {
        "lock": transaction_lock,
        "users": 0,
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(orchestrator.procesar_evento, evento)
        assert_true(
            guardado_iniciado.wait(timeout=2),
            "La primera solicitud debe estar dentro de la escritura manual.",
        )
        second = executor.submit(orchestrator.procesar_evento, evento)
        assert_true(
            transaction_lock.second_attempt.wait(timeout=2),
            "La segunda solicitud debe solaparse y esperar el lock del mismo ID.",
        )
        liberar_guardado.set()
        futures = [first, second]
        acciones = [future.result(timeout=2) for future in futures]

    assert_true(
        all(accion is not None and accion.nombre == "hablar" for accion in acciones),
        "Ambos callers deben recibir una respuesta conversacional.",
    )
    assert_true(
        acciones[0].id_accion == acciones[1].id_accion,
        "Los retries solapados deben compartir la acción cacheada.",
    )
    assert_true(
        orchestrator.memory.guardados_manuales == [evento.id_transaccion],
        "La misma transacción concurrente debe escribir una sola vez.",
    )
    assert_true(len(orchestrator.memory.experiencias) == 1, "No debe duplicar el evento.")
    assert_true(len(orchestrator.memory.acciones) == 1, "No debe duplicar memoria operativa.")
    assert_true(len(orchestrator.identity_state.eventos) == 1, "No debe duplicar identidad.")


def test_persona_recibe_la_restriccion_de_canal_terminal() -> None:
    evento = _evento_terminal("ve al cubo")
    restriccion = Orchestrator._restriccion_canal_persona(evento)
    behavior = _FakeBehavior()
    identity_context = IdentityContextV1.from_snapshot(
        IdentitySnapshotProvider(build_static_self_v1(), behavior).capture()
    )
    mensajes = PersonaEngine().preparar_interaccion(
        mensaje_usuario="ve al cubo",
        identity_context=identity_context,
        restriccion_canal=restriccion,
    )
    system_prompt = mensajes[0]["content"]

    assert_true("RESTRICCION DEL CANAL ACTUAL" in system_prompt, "Debe identificar el canal.")
    assert_true("sin cuerpo disponible" in system_prompt, "Persona debe conocer el limite fisico.")
    assert_true(
        "no afirmes que la ejecutaste" in system_prompt,
        "Persona no debe fingir que materializo una accion.",
    )


def test_unity_conserva_su_flujo_motor_y_cancelacion() -> None:
    evento = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="unity-regression",
        detalles={"mensaje": "detente", "task_intent": "cancel"},
    )
    decision = {
        "intent": "MOTOR_SIMPLE",
        "route": "motor",
        "requires_memory": False,
        "requires_oracle": False,
    }
    respuesta = ActionResponse(
        pensamiento="",
        accion="navegar_a_objeto",
        parametros={"nombre_objeto": "Cube"},
    )

    assert_true(
        Orchestrator._adaptar_triage_para_terminal(evento, decision)["route"] == "motor",
        "Unity debe conservar la ruta motora.",
    )
    assert_true(
        Orchestrator._blindar_salida_terminal(evento, respuesta).accion == "navegar_a_objeto",
        "El firewall terminal no debe modificar salidas de Unity.",
    )
    assert_true(
        Orchestrator._usuario_pide_cancelar_tarea(evento) is True,
        "Unity debe conservar la cancelacion cuando llega tipada.",
    )
    herramientas = ToolPolicy().seleccionar(
        ["hablar", "navegar_a_objeto", "escribir_teclado"],
        origen="unity",
        route="motor",
    )
    assert_true(
        herramientas == ["hablar", "navegar_a_objeto"],
        "Unity debe conservar voz y herramientas fisicas seguras.",
    )


def main() -> None:
    print("=== INICIANDO PRUEBA DEL CHAT TERMINAL ===\n")
    test_cliente_envia_contrato_terminal_y_solo_devuelve_texto_publico()
    test_cliente_genera_un_uuid_distinto_por_turno()
    test_cliente_bloquea_respuesta_no_conversacional()
    test_cliente_no_expone_detalles_internos_de_http_500()
    test_repl_se_recupera_de_fallos_de_conexion_y_timeout()
    test_cliente_rechaza_respuestas_malformadas()
    test_comandos_locales_no_se_envian_como_conversacion()
    test_repl_cierra_limpio_si_se_interrumpe_una_solicitud()
    test_api_comando_acepta_terminal_y_fuerza_evento_conversacional()
    test_flujo_terminal_reconduce_motor_y_no_cancela_tareas()
    test_flujo_terminal_conserva_oracle_y_verbaliza()
    test_orchestrator_reconduce_motor_y_blinda_la_salida()
    test_oracle_se_conserva_y_tool_policy_solo_expone_hablar()
    test_state_engine_descanso_temporal_proyecta_sin_mutar_y_no_acumula()
    test_descanso_parcial_relaja_sin_decaer_foco_ni_aplicar_fatiga_activa()
    test_identity_state_y_state_engine_comparten_reserva_biologica()
    test_descanso_terminal_proyecta_en_estado_y_despierta_antes_de_responder()
    test_segunda_peticion_core_system_despierta_y_rearma_sin_apilar_tasas()
    test_ayuda_estado_y_salir_no_despiertan_descanso_terminal()
    test_retries_cacheados_no_rearman_ni_despiertan_descanso_terminal()
    test_mensajes_concurrentes_consumen_descanso_terminal_una_sola_vez()
    test_guardado_manual_despierta_y_same_id_no_repite_persistencia()
    test_guardado_manual_con_palabras_de_sueno_no_arma_descanso()
    test_terminal_no_usa_descanso_inmediato_y_unity_lo_conserva()
    test_terminal_guarda_ram_como_conversacion_y_no_registra_el_control()
    test_terminal_same_id_no_repite_guardado_fallido_ni_inventa_exito()
    test_terminal_informa_ram_vacia_y_guardado_ya_existente()
    test_retries_simultaneos_comparten_una_sola_transaccion()
    test_persona_recibe_la_restriccion_de_canal_terminal()
    test_unity_conserva_su_flujo_motor_y_cancelacion()
    print("[OK] test_chat paso: terminal conversa sin ejecutar acciones fisicas.")


if __name__ == "__main__":
    main()
