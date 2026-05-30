"""
behavior_core.py - Capa 3 del sistema de comportamiento de Lazzaro.
Fachada principal que integra StateEngine, PersonalityTraits, TaskContext y PsychologyEngine.
"""

from datetime import datetime, timezone
import re
from typing import Any

from core.logger import get_logger
from core.personality import PersonalityTraits
from core.psychology import PsychologyEngine
from core.state_engine import StateEngine
from core.task_context import TaskContext
from telescopio.constelacion_cognitiva import ConstelacionCognitiva

logger = get_logger(__name__)


class BehaviorCore:
    """
    Orquestador interno del agente.
    Mantiene el estado, la personalidad, el contexto de tarea actual y la politica activa.
    """

    EVENTOS_CON_RESULTADO = {"resultado_accion", "alerta_dolor_fisico"}
    EVENTOS_PERCEPTIVOS = {"inicio_simulacion", "actualizacion_estado", "percepcion_entorno"}
    ACCIONES_EXPLORATORIAS = {
        "navegar_a_objeto", #agregado
        "navegar_a_posicion",# agregado
        "moverse_entorno_3d",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "soltar_objeto",
        "aplicar_fuerza",
    }

    def __init__(self):
        self.state_engine = StateEngine()
        self.personality = PersonalityTraits()
        self.psychology = PsychologyEngine()

        self.task_context: TaskContext | None = None

        self.policy = "exploradora"
        self.policies_disponibles = ["cautelosa", "exploradora", "eficiente", "social", "autonoma"]
        self._offsets_politica: dict[str, float] = {}
        self._constelacion = ConstelacionCognitiva(nombre="lazzaro_cognitiva")
        self._ultima_percepcion: dict[str, Any] = {}
        self._ultima_percepcion_resumida = ""
        self._actualizar_modificadores_globales()

    def iniciar_tarea(self, objetivo_id: str):
        """Crea o resetea el TaskContext para un nuevo objetivo."""
        if self.task_context is None:
            self.task_context = TaskContext(objetivo_id)
            logger.info("Nueva tarea iniciada: '%s'", objetivo_id)
        else:
            self.task_context.reset(objetivo_id)
            logger.info("Tarea reiniciada con nuevo objetivo: '%s'", objetivo_id)

    def finalizar_tarea(self, exito: bool):
        """Finaliza la tarea actual y genera un evento de cierre."""
        if self.task_context is None:
            return

        if exito:
            self.procesar_evento("tarea_completada", {"intensidad": 1.0})
        else:
            self.procesar_evento("fracaso", {"intensidad": 1.0})
        self.task_context = None
        logger.info("Tarea finalizada y contexto liberado.")

    def procesar_evento(self, tipo_evento: str, detalles: dict | None = None) -> dict:
        """
        Recibe un evento externo y actualiza todo el sistema.
        Retorna un diccionario con el nuevo estado, banderas y contexto LLM.
        """
        if detalles is None:
            detalles = {}

        intensidad = self._leer_intensidad(detalles)
        accion = detalles.get("accion")
        exito = detalles.get("exito")
        evento_emocional = self._resolver_evento_emocional(tipo_evento, detalles)

        self._sincronizar_tarea_desde_detalles(tipo_evento, detalles)
        percepcion = self._extraer_percepcion(detalles)
        percepcion_resumida = self._resumir_percepcion(percepcion)
        if percepcion:
            self._ultima_percepcion = dict(percepcion)
            self._ultima_percepcion_resumida = percepcion_resumida

        logger.info("Procesando evento: '%s' intensidad=%s", tipo_evento, intensidad)

        self.state_engine.aplicar_evento(evento_emocional, intensidad, contexto=detalles)
        self.personality.update_from_event(evento_emocional, intensidad)

        if self.task_context is not None and accion is not None and exito is not None:
            self.task_context.registrar_intento(str(accion), bool(exito))
            self.task_context.actualizar_umbral_abandono(self.personality.paciencia)
            if self.task_context.detectar_loop():
                self.procesar_evento("loop_detectado", {"intensidad": 1.0})
            if self.task_context.detectar_estancamiento():
                self.procesar_evento("fracaso", {"intensidad": 0.5})
            if self.task_context.detectar_compulsion():
                self.procesar_evento("sobrecarga", {"intensidad": 0.3})

        self._actualizar_modificadores_globales()
        percepcion_contexto = percepcion or self._ultima_percepcion
        percepcion_resumida_contexto = percepcion_resumida or self._ultima_percepcion_resumida

        contexto = self.psychology.generar_contexto_llm(
            self.state_engine,
            self.personality,
            self.task_context,
            policy_offsets=self._offsets_politica,
            percepcion=percepcion_contexto or None,
            percepcion_resumida=percepcion_resumida_contexto,
        )
        self._actualizar_constelacion(contexto, percepcion_contexto, percepcion_resumida_contexto)
        constelacion_resumen = self._resumir_constelacion_texto()
        contexto["constelacion_resumen"] = constelacion_resumen

        respuesta = {
            "estado": self.state_engine.obtener_estado(),
            "personalidad": self.personality.obtener_rasgos(),
            "banderas": contexto["umbrales"],
            "narrativa": contexto["estado_narrativo"],
            "contexto_llm": contexto,
            "policy_activa": self.policy,
            "tarea": self.task_context.obtener_resumen() if self.task_context else None,
            "es_interrupcion_critica": tipo_evento == "alerta_dolor_fisico",
            "tipo_memoria": self._clasificar_tipo_memoria(tipo_evento, percepcion),
            "percepcion_resumida": percepcion_resumida_contexto,
            "constelacion_cognitiva": self._constelacion.a_dict(),
            "constelacion_resumen": constelacion_resumen,
        }
        return respuesta

    def cambiar_policy(self, nombre: str):
        """Cambia la politica activa si es valida."""
        if nombre in self.policies_disponibles:
            self.policy = nombre
            logger.info("Politica cambiada a '%s'", nombre)
            self._actualizar_modificadores_globales()
        else:
            logger.warning("Politica '%s' no reconocida. Usar: %s", nombre, self.policies_disponibles)

    def _policy_modifiers(self) -> dict:
        mods = {
            "confianza_offset": 0.0,
            "tolerancia_riesgo_offset": 0.0,
            "curiosidad_offset": 0.0,
            "agresividad_offset": 0.0,
            "motivacion_offset": 0.0,
            "foco_offset": 0.0,
            "carga_cognitiva_offset": 0.0,
            "tasa_estres_mult": 1.0,
            "tasa_energia_mult": 1.0,
            "tasa_confianza_mult": 1.0,
        }

        if self.policy == "cautelosa":
            mods["tolerancia_riesgo_offset"] = -25.0
            mods["confianza_offset"] = -10.0
            mods["tasa_estres_mult"] = 1.2
            mods["tasa_confianza_mult"] = 0.8
        elif self.policy == "exploradora":
            mods["curiosidad_offset"] = 20.0
            mods["tolerancia_riesgo_offset"] = 15.0
            mods["tasa_energia_mult"] = 1.1
        elif self.policy == "eficiente":
            mods["foco_offset"] = 10.0
            mods["carga_cognitiva_offset"] = -10.0
            mods["tasa_estres_mult"] = 0.9
            mods["motivacion_offset"] = 5.0
        elif self.policy == "social":
            mods["agresividad_offset"] = -10.0
            mods["confianza_offset"] = 5.0
            mods["tasa_estres_mult"] = 0.8
        elif self.policy == "autonoma":
            mods["confianza_offset"] = 12.0
            mods["tolerancia_riesgo_offset"] = 20.0
            mods["motivacion_offset"] = 8.0
            mods["foco_offset"] = 5.0
            mods["tasa_energia_mult"] = 1.1
            mods["tasa_confianza_mult"] = 1.1

        return mods

    def _actualizar_modificadores_globales(self):
        pers_mods = self.personality.get_modifiers()
        poli_mods = self._policy_modifiers()

        self._offsets_politica = {
            "confianza": poli_mods.get("confianza_offset", 0.0),
            "curiosidad": poli_mods.get("curiosidad_offset", 0.0),
            "tolerancia_riesgo": poli_mods.get("tolerancia_riesgo_offset", 0.0),
            "agresividad": poli_mods.get("agresividad_offset", 0.0),
            "motivacion": poli_mods.get("motivacion_offset", 0.0),
            "foco": poli_mods.get("foco_offset", 0.0),
            "carga_cognitiva": poli_mods.get("carga_cognitiva_offset", 0.0),
        }

        mods_engine = {
            "tasa_estres": pers_mods["tasa_estres"] * poli_mods.get("tasa_estres_mult", 1.0),
            "tasa_energia": pers_mods["tasa_energia"] * poli_mods.get("tasa_energia_mult", 1.0),
            "tasa_foco": pers_mods["tasa_foco"],
            "tasa_carga_cognitiva": pers_mods["tasa_carga_cognitiva"],
            "tasa_confianza": pers_mods["tasa_confianza"] * poli_mods.get("tasa_confianza_mult", 1.0),
            "tasa_curiosidad": pers_mods["tasa_curiosidad"],
            "tasa_motivacion": pers_mods["tasa_motivacion"],
            "tasa_agresividad": pers_mods["tasa_agresividad"],
            "tasa_tolerancia_riesgo": pers_mods["tasa_tolerancia_riesgo"],
            "amortiguador_estres": pers_mods["amortiguador_estres"],
        }
        self.state_engine.actualizar_modificadores(mods_engine)

    def obtener_contexto_llm(self) -> dict:
        """Wrapper para obtener el contexto listo para el LLM."""
        contexto = self.psychology.generar_contexto_llm(
            self.state_engine,
            self.personality,
            self.task_context,
            policy_offsets=self._offsets_politica,
            percepcion=self._ultima_percepcion or None,
            percepcion_resumida=self._ultima_percepcion_resumida,
        )
        self._actualizar_constelacion(
            contexto,
            contexto.get("percepcion", {}),
            contexto.get("percepcion_resumida", ""),
        )
        contexto["constelacion_resumen"] = self._resumir_constelacion_texto()
        return contexto

    def obtener_constelacion(self) -> dict[str, Any]:
        """Devuelve la constelacion cognitiva actual del agente."""
        if self._constelacion.esta_vacia():
            self.obtener_contexto_llm()
        return self._constelacion.a_dict()

    def obtener_constelacion_resumen(self) -> str:
        """Devuelve un resumen compacto de la constelacion actual."""
        if self._constelacion.esta_vacia():
            self.obtener_contexto_llm()
        return self._resumir_constelacion_texto()

    def obtener_resumen(self) -> dict:
        """Devuelve un resumen completo del estado del agente."""
        tarea = self.task_context.obtener_resumen() if self.task_context else None
        return {
            "estado": self.state_engine.obtener_estado(),
            "personalidad": self.personality.obtener_rasgos(),
            "tarea": tarea,
            "policy": self.policy,
            "narrativa": self.psychology.generar_resumen_narrativo(
                self.state_engine,
                self.personality,
                self.task_context,
                policy_offsets=self._offsets_politica,
            ),
            "constelacion_cognitiva": self.obtener_constelacion(),
            "constelacion_resumen": self.obtener_constelacion_resumen(),
        }

    @staticmethod
    def _leer_intensidad(detalles: dict[str, Any]) -> float:
        valor = detalles.get("intensidad", 1.0)
        try:
            return float(valor)
        except (TypeError, ValueError):
            return 1.0

    def _resolver_evento_emocional(self, tipo_evento: str, detalles: dict[str, Any]) -> str:
        if tipo_evento == "resultado_accion":
            accion = str(detalles.get("accion", ""))
            exito = detalles.get("exito")
            if exito is True:
                if accion in self.ACCIONES_EXPLORATORIAS:
                    return "exploracion_exitosa"
                return "recompensa"
            if exito is False:
                if accion in self.ACCIONES_EXPLORATORIAS:
                    return "exploracion_fallida"
                return "fracaso"
        if tipo_evento == "comando_usuario":
            return "comando_usuario"
        return tipo_evento

    def _sincronizar_tarea_desde_detalles(self, tipo_evento: str, detalles: dict[str, Any]) -> None:
        objetivo = self._extraer_objetivo_desde_detalles(tipo_evento, detalles)
        if objetivo:
            objetivo_actual = self.task_context.objetivo_id if self.task_context else None
            if objetivo_actual != objetivo:
                self.iniciar_tarea(objetivo)

        if self.task_context is not None and detalles.get("objetivo_completado") is True:
            self.finalizar_tarea(exito=True)

    def _extraer_objetivo_desde_detalles(self, tipo_evento: str, detalles: dict[str, Any]) -> str | None:
        mensaje = str(detalles.get("mensaje", "")).strip()
        if not mensaje:
            return None

        match = re.search(
            r"objetivo(?:\s+actual|\s+prioritario)?\s*:\s*(.+)",
            mensaje,
            re.IGNORECASE,
        )
        if match:
            objetivo = match.group(1).strip()
            objetivo = re.split(r"\bREGLAS?\b", objetivo, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            return objetivo.rstrip(".")

        if tipo_evento == "comando_usuario":
            return mensaje

        return None

    @staticmethod
    def _extraer_percepcion(detalles: dict[str, Any]) -> dict[str, Any]:
        claves_perceptivas = {
            "objetos_visibles",
            "resumen_percepcion",
            "objeto_mas_cercano",
            "cantidad_objetos_visibles",
            "posicion_lazzaro",
            "estado_fisico",
            "salud_avatar",
            "mensaje_critico",
        }
        return {clave: valor for clave, valor in detalles.items() if clave in claves_perceptivas}

    @staticmethod
    def _resumir_percepcion(percepcion: dict[str, Any]) -> str:
        if not percepcion:
            return ""

        lineas: list[str] = []
        posicion = percepcion.get("posicion_lazzaro")
        if isinstance(posicion, dict):
            x = posicion.get("x", 0)
            y = posicion.get("y", 0)
            z = posicion.get("z", 0)
            lineas.append(f"Posicion actual: x={x}, y={y}, z={z}.")

        cantidad = percepcion.get("cantidad_objetos_visibles")
        if isinstance(cantidad, (int, float)):
            lineas.append(f"Objetos visibles: {int(cantidad)}.")

        mas_cercano = percepcion.get("objeto_mas_cercano")
        if isinstance(mas_cercano, dict):
            nombre = mas_cercano.get("nombre", "desconocido")
            distancia = mas_cercano.get("distancia")
            if distancia is not None:
                lineas.append(f"Objeto mas cercano: {nombre} a {distancia} unidades.")
            else:
                lineas.append(f"Objeto mas cercano: {nombre}.")

        visibles = percepcion.get("objetos_visibles")
        if isinstance(visibles, str) and visibles.strip():
            lineas.append(f"Resumen visual: {visibles.strip()}")
        elif isinstance(visibles, list) and visibles:
            muestras = []
            for objeto in visibles[:3]:
                if not isinstance(objeto, dict):
                    continue
                nombre = objeto.get("nombre", "objeto")
                distancia = objeto.get("distancia")
                if distancia is None:
                    muestras.append(str(nombre))
                else:
                    muestras.append(f"{nombre} ({distancia})")
            if muestras:
                lineas.append("Muestras del entorno: " + ", ".join(muestras) + ".")

        resumen_percepcion = percepcion.get("resumen_percepcion")
        if isinstance(resumen_percepcion, str) and resumen_percepcion.strip():
            if resumen_percepcion.strip() not in " ".join(lineas):
                lineas.append(f"Resumen perceptivo: {resumen_percepcion.strip()}")

        estado_fisico = percepcion.get("estado_fisico")
        if estado_fisico:
            lineas.append(f"Estado fisico: {estado_fisico}.")

        mensaje_critico = percepcion.get("mensaje_critico")
        if mensaje_critico:
            lineas.append(f"Alerta: {mensaje_critico}.")

        return " ".join(lineas)

    def _clasificar_tipo_memoria(self, tipo_evento: str, percepcion: dict[str, Any]) -> str:
        if tipo_evento in self.EVENTOS_PERCEPTIVOS and percepcion:
            return "percepcion_entorno"
        if tipo_evento == "resultado_accion":
            return "resultado_accion"
        return tipo_evento

    def _actualizar_constelacion(
        self,
        contexto: dict[str, Any],
        percepcion: dict[str, Any],
        percepcion_resumida: str,
    ) -> None:
        tarea = self.task_context.obtener_resumen() if self.task_context else None
        estado = contexto.get("valores") or self.state_engine.obtener_estado()
        rasgos = self.personality.obtener_rasgos()
        banderas = contexto.get("umbrales", {})

        constelacion = ConstelacionCognitiva.desde_estado_bruto(
            estado=estado,
            rasgos=rasgos,
            banderas=banderas,
            contexto={
                "policy_activa": self.policy,
                "estado_narrativo": contexto.get("estado_narrativo", ""),
                "percepcion_resumida": percepcion_resumida,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            nombre="lazzaro_cognitiva",
        )

        constelacion.crear_nodo(
            nombre="meta:policy_activa",
            categoria="meta",
            valor=self.policy,
            descripcion="Politica conductual activa.",
        )

        if tarea:
            objetivo = tarea.get("objetivo") or tarea.get("objetivo_id")
            if objetivo:
                constelacion.crear_nodo(
                    nombre="tarea:objetivo_actual",
                    categoria="tarea",
                    valor=str(objetivo),
                    descripcion="Objetivo principal vigente.",
                )
            constelacion.crear_nodo(
                nombre="tarea:intentos",
                categoria="tarea",
                valor=int(tarea.get("intentos_totales", 0)),
                descripcion="Cantidad de intentos acumulados en la tarea actual.",
            )
            constelacion.crear_nodo(
                nombre="tarea:fallos",
                categoria="tarea",
                valor=int(tarea.get("intentos_fallidos", 0)),
                descripcion="Cantidad de intentos fallidos.",
            )
            constelacion.crear_nodo(
                nombre="tarea:frustracion_local",
                categoria="tarea",
                valor=float(tarea.get("frustracion_local", 0.0)),
                descripcion="Frustracion asociada al objetivo activo.",
            )
            self._conectar_si_existe(constelacion, "meta:policy_activa", "tarea:objetivo_actual", "modula")
            self._conectar_si_existe(constelacion, "estado:motivacion", "tarea:objetivo_actual", "sostiene")
            self._conectar_si_existe(constelacion, "estado:foco", "tarea:objetivo_actual", "orienta")
            self._conectar_si_existe(
                constelacion,
                "tarea:frustracion_local",
                "bandera:debe_cambiar_estrategia",
                "activa_si_aumenta",
            )

        if percepcion_resumida:
            constelacion.crear_nodo(
                nombre="percepcion:resumen",
                categoria="percepcion",
                valor=percepcion_resumida,
                descripcion="Resumen legible del entorno actual.",
            )
            self._conectar_si_existe(constelacion, "estado:curiosidad", "percepcion:resumen", "atiende")

        cantidad_objetos = percepcion.get("cantidad_objetos_visibles")
        if isinstance(cantidad_objetos, (int, float)):
            constelacion.crear_nodo(
                nombre="percepcion:cantidad_objetos_visibles",
                categoria="percepcion",
                valor=int(cantidad_objetos),
                descripcion="Cantidad de objetos detectados en el entorno.",
            )

        objeto_cercano = percepcion.get("objeto_mas_cercano")
        if isinstance(objeto_cercano, dict):
            nombre_objeto = str(objeto_cercano.get("nombre", "desconocido"))
            constelacion.crear_nodo(
                nombre="percepcion:objeto_mas_cercano",
                categoria="percepcion",
                valor=nombre_objeto,
                descripcion="Objeto detectado con menor distancia relativa.",
                metadata={"distancia": objeto_cercano.get("distancia")},
            )
            self._conectar_si_existe(
                constelacion,
                "percepcion:objeto_mas_cercano",
                "tarea:objetivo_actual",
                "puede_ser_relevante_para",
            )

        estado_fisico = percepcion.get("estado_fisico") or percepcion.get("salud_avatar")
        if estado_fisico is not None:
            constelacion.crear_nodo(
                nombre="percepcion:estado_fisico",
                categoria="percepcion",
                valor=str(estado_fisico),
                descripcion="Estado fisico reportado por el cliente.",
            )
            self._conectar_si_existe(constelacion, "estado:estres", "percepcion:estado_fisico", "reacciona_a")

        if banderas.get("dolor_fisico_critico"):
            self._conectar_si_existe(
                constelacion,
                "bandera:dolor_fisico_critico",
                "tarea:objetivo_actual",
                "interrumpe",
                peso=1.4,
            )
            self._conectar_si_existe(
                constelacion,
                "bandera:dolor_fisico_critico",
                "percepcion:estado_fisico",
                "prioriza_seguridad",
                peso=1.4,
            )

        self._constelacion = constelacion

    def _resumir_constelacion_texto(self) -> str:
        if self._constelacion.esta_vacia():
            return ""

        partes: list[str] = []
        politica = self._valor_nodo("meta:policy_activa")
        if politica:
            partes.append(f"Politica activa: {politica}.")

        objetivo = self._valor_nodo("tarea:objetivo_actual")
        if objetivo:
            partes.append(f"Objetivo vigente: {objetivo}.")

        objetos_visibles = self._valor_nodo("percepcion:cantidad_objetos_visibles")
        if objetos_visibles is not None:
            partes.append(f"Entorno detectado con {objetos_visibles} objetos visibles.")

        objeto_cercano = self._valor_nodo("percepcion:objeto_mas_cercano")
        if objeto_cercano:
            partes.append(f"Objeto mas cercano: {objeto_cercano}.")

        if self._valor_nodo("bandera:dolor_fisico_critico") is True:
            partes.append("Dolor fisico critico activo; la seguridad tiene prioridad inmediata.")

        if not partes:
            return self._constelacion.resumen()
        return " ".join(partes)

    def _valor_nodo(self, nombre: str) -> Any:
        nodo = self._constelacion.obtener_nodo(nombre)
        return nodo.valor if nodo is not None else None

    @staticmethod
    def _conectar_si_existe(
        constelacion: ConstelacionCognitiva,
        origen: str,
        destino: str,
        relacion: str,
        peso: float = 1.0,
    ) -> None:
        if constelacion.obtener_nodo(origen) is None or constelacion.obtener_nodo(destino) is None:
            return
        constelacion.conectar(origen, destino, relacion, peso=peso)


behavior_core = BehaviorCore()
