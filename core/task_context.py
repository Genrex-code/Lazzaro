"""
task_context.py - Capa 2 (parte) del sistema de comportamiento de Lazzaro.
Administra el contexto de la tarea actual: objetivo, intentos, frustración local
y un microhistorial para detectar patrones no deseados (loops, estancamiento, compulsiones).
"""

import time
from core.logger import get_logger

logger = get_logger(__name__)


class TaskContext:
    """
    Contexto asociado a un objetivo concreto.
    La frustración generada aquí impacta en el estrés global del agente a través de
    behavior_core, que leerá la frustración local y aplicará el estímulo necesario.
    """

    ACCIONES_MOVIMIENTO = {
        "navegar_a_objeto",
        "navegar_a_posicion",
        "alejarse_de_objeto",
        "girar_avatar",
    }

    def __init__(self, objetivo_id: str = "", max_historial: int = 20):
        self.objetivo_id = objetivo_id
        self.intentos_totales = 0
        self.intentos_fallidos = 0
        self.frustracion_local = 0.0          # 0 - 100
        self.umbral_abandono = 80.0           # Valor por defecto (sin influencia de paciencia)

        # Microhistorial: almacena las últimas N acciones
        self.max_historial = max_historial
        self.historial = []  # Lista de dicts: {"accion": str, "exito": bool, "timestamp": float}

    def reset(self, nuevo_objetivo: str = ""):
        """Reinicia el contexto para una nueva tarea."""
        self.objetivo_id = nuevo_objetivo
        self.intentos_totales = 0
        self.intentos_fallidos = 0
        self.frustracion_local = 0.0
        self.historial.clear()
        logger.debug(f"TaskContext reseteado. Nuevo objetivo: '{nuevo_objetivo}'")

    def registrar_intento(self, accion: str, exito: bool):
        """
        Registra una acción realizada para el objetivo actual.
        Actualiza contadores, frustración local y el microhistorial.
        """
        if type(exito) is not bool:
            raise TypeError("TaskContext.registrar_intento exige un bool estricto")

        ahora = time.time()
        self.intentos_totales += 1
        if not exito:
            self.intentos_fallidos += 1
            # La frustración sube más si ya estaba alta (efecto acumulativo)
            incremento_base = 5.0
            if self.frustracion_local > 50:
                incremento_base *= 1.5
            self.frustracion_local = min(100.0, self.frustracion_local + incremento_base)
        else:
            # Éxito: reduce la frustración (más si el éxito fue tras varios fallos)
            reduccion = 15.0
            if self.intentos_fallidos > 3:
                reduccion *= 1.3
            self.frustracion_local = max(0.0, self.frustracion_local - reduccion)

        # Agregar al historial y limitar tamaño
        self.historial.append({
            "accion": accion,
            "exito": exito,
            "timestamp": ahora,
        })
        if len(self.historial) > self.max_historial:
            self.historial.pop(0)

        logger.debug(f"Intento registrado: '{accion}' -> {'éxito' if exito else 'fallo'}. "
                     f"Frustración: {self.frustracion_local:.1f}")

    def actualizar_umbral_abandono(self, paciencia: float):
        """
        Recalcula el umbral de abandono según el rasgo de personalidad 'paciencia'.
        paciencia: valor entre -1.0 y 1.0.
        """
        # Umbral base: 80. A mayor paciencia, umbral más alto (más tolerante).
        # A menor paciencia, umbral más bajo (se rinde antes).
        self.umbral_abandono = 80.0 + paciencia * 20.0
        # Limitar entre 40 y 100
        self.umbral_abandono = max(40.0, min(100.0, self.umbral_abandono))
        logger.debug(f"Umbral de abandono actualizado a {self.umbral_abandono:.1f} (paciencia={paciencia:.2f})")

    # --- Métodos de detección de patrones ---

    def detectar_loop(self, ventana: int = 5) -> bool:
        """
        Detecta si las últimas 'ventana' acciones son idénticas (misma acción y mismo resultado).
        Indica que el agente está repitiendo la misma conducta sin éxito.
        """
        if len(self.historial) < ventana:
            return False
        ultimas = self.historial[-ventana:]
        # Comparar todas con la primera de la ventana
        referencia = ultimas[0]
        for registro in ultimas[1:]:
            if registro["accion"] != referencia["accion"] or registro["exito"] != referencia["exito"]:
                return False
        if referencia["exito"] and referencia["accion"] in self.ACCIONES_MOVIMIENTO:
            return False
        logger.warning(f"Loop detectado: {ventana} repeticiones de '{referencia['accion']}' "
                       f"con resultado {'éxito' if referencia['exito'] else 'fallo'}")
        return True

    def detectar_estancamiento(self, umbral_fallos_consecutivos: int = 4) -> bool:
        """
        Detecta si ha habido un número excesivo de fallos consecutivos recientes.
        """
        if len(self.historial) < umbral_fallos_consecutivos:
            return False
        # Revisar últimos N registros hacia atrás
        consecutivos = 0
        for registro in reversed(self.historial):
            if not registro["exito"]:
                consecutivos += 1
                if consecutivos >= umbral_fallos_consecutivos:
                    logger.warning(f"Estancamiento detectado: {consecutivos} fallos consecutivos.")
                    return True
            else:
                break
        return False

    def detectar_compulsion(self, ventana_tiempo: float = 30.0, repeticiones: int = 5) -> bool:
        """
        Detecta si se ha repetido la misma acción muchas veces en un corto período de tiempo,
        independientemente del resultado (compulsión).
        """
        if len(self.historial) < repeticiones:
            return False
        ahora = time.time()
        # Tomar las acciones que ocurrieron dentro de la ventana de tiempo
        acciones_recientes = [
            reg for reg in self.historial
            if ahora - reg["timestamp"] <= ventana_tiempo
        ]
        if len(acciones_recientes) < repeticiones:
            return False
        # Contar ocurrencias de cada acción
        conteo = {}
        for reg in acciones_recientes:
            accion = reg["accion"]
            if accion in self.ACCIONES_MOVIMIENTO:
                continue
            conteo[accion] = conteo.get(accion, 0) + 1
        for accion, count in conteo.items():
            if count >= repeticiones:
                logger.warning(f"Compulsión detectada: '{accion}' repetida {count} veces en {ventana_tiempo}s")
                return True
        return False

    def obtener_resumen(self) -> dict:
        """Devuelve un diccionario con los datos relevantes para el resto del sistema."""
        return {
            "objetivo_id": self.objetivo_id,
            "objetivo": self.objetivo_id,
            "intentos_totales": self.intentos_totales,
            "intentos_fallidos": self.intentos_fallidos,
            "frustracion_local": round(self.frustracion_local, 2),
            "umbral_abandono": round(self.umbral_abandono, 2),
            "loop_detectado": self.detectar_loop(),
            "estancamiento_detectado": self.detectar_estancamiento(),
            "estancamiento": self.detectar_estancamiento(),
            "compulsion_detectada": self.detectar_compulsion(),
            "compulsion": self.detectar_compulsion(),
        }
