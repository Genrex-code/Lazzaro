from __future__ import annotations

from copy import deepcopy
import gc
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

import memory.memory_manager as memory_manager_module
from memory.memory_manager import MemoryManager
from memory.chroma_client import ChromaClient
from memory.memory_schema import MemorySchemaCodec, SEMANTIC_KIND


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakePersistentClient:
    """Backend compartible entre managers con contrato suficiente para estas pruebas."""

    def __init__(self, records: list[dict[str, Any]] | None = None):
        self.records = records if records is not None else []
        self.fail_mode = ""
        self.list_fail_mode = ""
        self.list_fail_message = "fallo programado de lectura"
        self.update_fail_mode = ""
        self.db_path = "fake-manual-memory"
        self.deleted_ids: list[str] = []

    @staticmethod
    def combinar_where(base: dict | None, extra: dict | None) -> dict | None:
        if not base:
            return extra
        if not extra:
            return base
        return {"$and": [base, extra]}

    def agregar(
        self,
        documentos: list[str],
        metadatos: list[dict[str, Any]],
        ids: list[str],
    ) -> bool:
        mode = self.fail_mode
        self.fail_mode = ""
        if mode in {"false", "raise"}:
            if mode == "raise":
                raise RuntimeError("fallo programado antes de escribir")
            return False

        for documento, metadata, memory_id in zip(documentos, metadatos, ids):
            self.records.append(
                {
                    "id": memory_id,
                    "document": documento,
                    "metadata": dict(metadata),
                }
            )

        if mode == "write_then_false":
            return False
        if mode == "write_then_raise":
            raise RuntimeError("timeout después de confirmar la escritura")
        return True

    def listar(
        self,
        where: dict | None = None,
        limite: int | None = None,
    ) -> dict[str, list[Any]] | None:
        list_fail_mode = self.list_fail_mode
        self.list_fail_mode = ""
        if list_fail_mode == "none":
            return None
        if list_fail_mode == "raise":
            raise RuntimeError(self.list_fail_message)
        seleccionados = [
            record for record in self.records if self._matches(record["metadata"], where)
        ]
        if limite is not None:
            seleccionados = seleccionados[:limite]
        return {
            "ids": [record["id"] for record in seleccionados],
            "documents": [record["document"] for record in seleccionados],
            "metadatas": [dict(record["metadata"]) for record in seleccionados],
        }

    def actualizar_metadatos(
        self,
        ids: list[str],
        metadatos: list[dict[str, Any]],
    ) -> bool:
        mode = self.update_fail_mode
        self.update_fail_mode = ""
        if mode == "raise":
            raise RuntimeError("fallo programado actualizando metadata")
        if mode == "false":
            return False
        updates = dict(zip(ids, metadatos))
        for record in self.records:
            if record["id"] in updates:
                record["metadata"] = dict(updates[record["id"]])
        return all(any(record["id"] == memory_id for record in self.records) for memory_id in ids)

    def buscar(
        self,
        consulta: str,
        n_resultados: int = 5,
        where: dict | None = None,
    ) -> dict[str, list[list[Any]]]:
        seleccionados = [
            record for record in self.records if self._matches(record["metadata"], where)
        ][:n_resultados]
        return {
            "ids": [[record["id"] for record in seleccionados]],
            "documents": [[record["document"] for record in seleccionados]],
            "metadatas": [[dict(record["metadata"]) for record in seleccionados]],
            "distances": [[0.0 for _ in seleccionados]],
        }

    def eliminar(self, ids: list[str]) -> bool:
        self.deleted_ids.extend(ids)
        self.records[:] = [record for record in self.records if record["id"] not in ids]
        return True

    def contar(self) -> int:
        return len(self.records)

    def close(self) -> None:
        return None

    @classmethod
    def _matches(cls, metadata: dict[str, Any], where: dict | None) -> bool:
        if not where:
            return True
        if "$and" in where:
            return all(cls._matches(metadata, clause) for clause in where["$and"])
        return all(metadata.get(key) == value for key, value in where.items())


def _manual_records(client: FakePersistentClient) -> list[dict[str, Any]]:
    return [
        record
        for record in client.records
        if record["metadata"].get("tipo_evento") == "memoria_conversacional_manual"
    ]


def _manager_with_private_turn(
    client: FakePersistentClient,
    memory_write_ledger_path: str | None = None,
) -> MemoryManager:
    manager = MemoryManager(
        client,
        memory_write_ledger_path=memory_write_ledger_path,
    )
    manager.conversation_memory.registrar_turno(
        usuario="Vi un cactus azul junto al entorno de pruebas.",
        lazzaro="Lo observé contigo y hablamos de conservar ese recuerdo.",
        pensamiento="CANARIO_PENSAMIENTO_PRIVADO",
        route="CANARIO_ROUTE",
        intent="CANARIO_INTENT",
        metadata={"secreto": "CANARIO_METADATA_PRIVADA"},
    )
    manager.conversation_memory.registrar_turno(
        usuario="Después señalé un faro verde al final del sendero.",
        lazzaro="El faro verde quedó como segundo tema de la charla.",
        pensamiento="CANARIO_PENSAMIENTO_PRIVADO_2",
        route="CANARIO_ROUTE_2",
        intent="CANARIO_INTENT_2",
        metadata={"secreto": "CANARIO_METADATA_PRIVADA_2"},
    )
    return manager


def test_snapshot_is_public_immutable_and_ram_stays_intact() -> None:
    client = FakePersistentClient()
    manager = _manager_with_private_turn(client)
    before = deepcopy(manager.conversation_memory.obtener_ultimos(12))
    public_before = manager.conversation_memory.snapshot_publico()

    def bomb_clear() -> None:
        raise AssertionError("El guardado manual nunca debe limpiar la RAM.")

    manager.conversation_memory.limpiar = bomb_clear

    result = manager.guardar_fotografia_conversacional("save-public-1")
    after = deepcopy(manager.conversation_memory.obtener_ultimos(12))

    assert_true(result.status == "saved", "Debe confirmar una escritura real.")
    assert_true(before == after, "Guardar no debe vaciar ni mutar la RAM.")
    assert_true(result.turn_count == 2, "Debe informar el número exacto de intercambios.")
    assert_true(
        result.started_at == MemoryManager._timestamp_utc(public_before[0].timestamp),
        "El rango debe comenzar en el primer turno.",
    )
    assert_true(
        result.ended_at == MemoryManager._timestamp_utc(public_before[-1].timestamp),
        "El rango debe terminar en el último turno.",
    )

    records = _manual_records(client)
    assert_true(len(records) == 1, "Debe persistir una sola unidad semántica.")
    document = records[0]["document"]
    metadata_text = repr(records[0]["metadata"])
    assert_true("cactus azul" in document, "Debe conservar el texto público del usuario.")
    assert_true("Lo observé contigo" in document, "Debe conservar la respuesta pública.")
    assert_true(
        document.index("cactus azul") < document.index("faro verde"),
        "La transcripción debe conservar el orden cronológico.",
    )
    assert_true(
        records[0]["metadata"]["cantidad_turnos"] == 2,
        "La metadata debe conservar la cantidad exacta.",
    )
    assert_true(
        records[0]["metadata"]["inicio_utc"] == result.started_at
        and records[0]["metadata"]["fin_utc"] == result.ended_at,
        "La metadata debe conservar el rango temporal exacto.",
    )
    for canary in (
        "CANARIO_PENSAMIENTO_PRIVADO",
        "CANARIO_ROUTE",
        "CANARIO_INTENT",
        "CANARIO_METADATA_PRIVADA",
        "CANARIO_PENSAMIENTO_PRIVADO_2",
        "CANARIO_ROUTE_2",
        "CANARIO_INTENT_2",
        "CANARIO_METADATA_PRIVADA_2",
    ):
        assert_true(canary not in document, f"No debe persistir el campo privado {canary}.")
        assert_true(canary not in metadata_text, f"No debe filtrar {canary} a metadata.")

    manager.conversation_memory.registrar_turno(
        usuario="Un turno posterior",
        lazzaro="No debe alterar la fotografía ya persistida.",
    )
    assert_true(
        "Un turno posterior" not in records[0]["document"],
        "La fotografía persistida debe ser independiente de mutaciones posteriores.",
    )


def test_empty_ram_and_write_failures_never_claim_success() -> None:
    empty_client = FakePersistentClient()
    empty_manager = MemoryManager(empty_client)
    empty = empty_manager.guardar_fotografia_conversacional("save-empty")
    assert_true(empty.status == "empty", "RAM vacía debe producir estado empty.")
    assert_true(not _manual_records(empty_client), "RAM vacía no debe escribir nada.")

    read_client = FakePersistentClient()
    read_manager = _manager_with_private_turn(read_client)
    read_before = deepcopy(read_manager.conversation_memory.obtener_ultimos(12))
    read_client.list_fail_mode = "none"
    unreadable = read_manager.guardar_fotografia_conversacional("save-read-failure")
    assert_true(
        unreadable.status == "error",
        "Un fallo al comprobar idempotencia debe cerrar la escritura, no asumir ausencia.",
    )
    assert_true(not _manual_records(read_client), "Una lectura incierta no debe crear duplicados.")
    assert_true(
        read_before == deepcopy(read_manager.conversation_memory.obtener_ultimos(12)),
        "El fallo de lectura tampoco debe mutar la RAM.",
    )

    client = FakePersistentClient()
    manager = _manager_with_private_turn(client)
    before_failure = deepcopy(manager.conversation_memory.obtener_ultimos(12))
    client.fail_mode = "false"
    failed = manager.guardar_fotografia_conversacional("save-retry")
    assert_true(failed.status == "error", "Una escritura no confirmada debe ser error.")
    assert_true(not failed.persisted, "El error nunca debe presentarse como persistido.")

    recovered = manager.guardar_fotografia_conversacional("save-retry")
    assert_true(recovered.status == "saved", "El mismo ID debe poder recuperarse tras un fallo.")
    assert_true(len(_manual_records(client)) == 1, "El retry debe terminar en una sola memoria.")
    assert_true(
        before_failure == deepcopy(manager.conversation_memory.obtener_ultimos(12)),
        "Error y retry deben conservar todos los campos de RAM.",
    )

    stable_client = FakePersistentClient()
    stable_manager = _manager_with_private_turn(stable_client)
    stable_client.fail_mode = "false"
    first_failure = stable_manager.guardar_fotografia_conversacional(
        "save-stable-retry"
    )
    stable_manager.conversation_memory.registrar_turno(
        usuario="TURNO_POSTERIOR_AL_FALLO",
        lazzaro="No pertenece a la transacción fallida.",
    )
    stable_retry = stable_manager.guardar_fotografia_conversacional(
        "save-stable-retry"
    )
    stable_document = _manual_records(stable_client)[0]["document"]
    assert_true(first_failure.status == "error", "El primer intento debe fallar.")
    assert_true(
        stable_retry.status == "saved" and stable_retry.turn_count == 2,
        "El retry debe persistir exactamente el snapshot fijado en el primer intento.",
    )
    assert_true(
        "TURNO_POSTERIOR_AL_FALLO" not in stable_document,
        "Un turno nuevo no debe cambiar la fotografía de una transacción pendiente.",
    )

    for mode in ("write_then_false", "write_then_raise"):
        client_after_commit = FakePersistentClient()
        manager_after_commit = _manager_with_private_turn(client_after_commit)
        client_after_commit.fail_mode = mode
        committed = manager_after_commit.guardar_fotografia_conversacional(
            f"save-commit-incierto-{mode}"
        )
        assert_true(
            committed.status == "saved",
            "Una respuesta perdida con exact match conserva el outcome CREATED.",
        )
        assert_true(
            len(_manual_records(client_after_commit)) == 1,
            "Un commit incierto reconocido no debe duplicarse.",
        )


def test_transaction_and_snapshot_fingerprints_are_durable() -> None:
    temporary = TemporaryDirectory(prefix="lazzaro-manual-ledger-")
    ledger_path = str(Path(temporary.name) / "memory_write_ledger.sqlite3")
    shared_records: list[dict[str, Any]] = []
    manager = _manager_with_private_turn(
        FakePersistentClient(shared_records),
        ledger_path,
    )
    first = manager.guardar_fotografia_conversacional("save-idempotent")
    same_transaction = manager.guardar_fotografia_conversacional("save-idempotent")
    new_transaction_same_snapshot = manager.guardar_fotografia_conversacional(
        "save-idempotent-new-id"
    )

    assert_true(first.status == "saved", "La primera solicitud debe escribir.")
    assert_true(
        same_transaction.status == "already_saved",
        "La misma transacción debe resolverse desde metadata durable.",
    )
    assert_true(
        new_transaction_same_snapshot.status == "already_saved",
        "Una nueva transacción sin cambios no debe duplicar la fotografía.",
    )
    assert_true(len(shared_records) == 1, "Transacción y huella deben dejar una sola unidad.")
    persisted_metadata = shared_records[0]["metadata"]
    decoded_metadata = MemorySchemaCodec.decode_metadata(persisted_metadata, SEMANTIC_KIND)
    assert_true(
        persisted_metadata["transacciones_guardado_hashes"]
        == decoded_metadata.extra["transacciones_guardado_hashes"],
        "El receipt manual debe mantener payload y proyección sincronizados.",
    )

    manager.conversation_memory.registrar_turno(
        usuario="Ahora agrega el faro verde.",
        lazzaro="El nuevo turno cambia la fotografía pública.",
    )
    second = manager.guardar_fotografia_conversacional("save-second-snapshot")
    assert_true(second.status == "saved", "Un snapshot nuevo debe producir otro recuerdo.")
    assert_true(second.fingerprint != first.fingerprint, "La huella debe cambiar con la RAM.")
    assert_true(len(shared_records) == 2, "Dos fotografías distintas deben coexistir.")

    manager.close()
    restarted = MemoryManager(
        FakePersistentClient(shared_records),
        memory_write_ledger_path=ledger_path,
    )
    after_restart = restarted.guardar_fotografia_conversacional(
        "save-idempotent-new-id"
    )
    assert_true(
        after_restart.status == "already_saved",
        "El ID deduplicado por huella debe quedar asociado tras reconstruir el manager.",
    )
    recovered = restarted.semantic_store.buscar(
        query="cactus azul",
        n=5,
        where={"tipo_evento": "memoria_conversacional_manual"},
    )
    assert_true(bool(recovered), "La memoria semántica debe recuperarse después del reinicio.")
    assert_true(
        "cactus azul" in recovered[0].recuerdo.regla,
        "La recuperación debe devolver la transcripción pública persistida.",
    )
    restarted.close()
    temporary.cleanup()


def test_transaction_receipt_update_failure_is_retryable() -> None:
    for mode in ("false", "raise"):
        temporary = TemporaryDirectory(prefix="lazzaro-manual-receipt-")
        ledger_path = str(Path(temporary.name) / "memory_write_ledger.sqlite3")
        shared_records: list[dict[str, Any]] = []
        client = FakePersistentClient(shared_records)
        manager = _manager_with_private_turn(client, ledger_path)
        first = manager.guardar_fotografia_conversacional(f"receipt-a-{mode}")
        assert_true(first.status == "saved", "La fotografía inicial debe existir.")

        client.update_fail_mode = mode
        missing_receipt = manager.guardar_fotografia_conversacional(
            f"receipt-b-{mode}"
        )
        assert_true(
            missing_receipt.status == "already_saved",
            "El ledger asocia la segunda transacción sin actualizar metadata Chroma.",
        )
        assert_true(
            len(_manual_records(client)) == 1,
            "El fallo de metadata no debe duplicar la fotografía existente.",
        )

        recovered = manager.guardar_fotografia_conversacional(
            f"receipt-b-{mode}"
        )
        assert_true(
            recovered.status == "already_saved",
            "El mismo ID debe completar su recibo durable al reintentar.",
        )

        manager.close()
        restarted = MemoryManager(
            FakePersistentClient(shared_records),
            memory_write_ledger_path=ledger_path,
        )
        after_restart = restarted.guardar_fotografia_conversacional(
            f"receipt-b-{mode}"
        )
        assert_true(
            after_restart.status == "already_saved",
            "La key durable del ledger debe sobrevivir la reconstrucción del manager.",
        )
        restarted.close()
        temporary.cleanup()


def test_compact_digest_covers_the_last_turn_in_persona_context() -> None:
    client = FakePersistentClient()
    manager = MemoryManager(client)
    for index in range(1, 13):
        manager.conversation_memory.registrar_turno(
            usuario=f"MARCA_U_{index} " + ("x" * 80) + f" FIN_U_{index}",
            lazzaro=f"MARCA_L_{index} " + ("y" * 80) + f" FIN_L_{index}",
        )

    result = manager.guardar_fotografia_conversacional("save-digest-all-turns")
    context = manager.obtener_contexto_memoria(
        query="FIN_L_12",
        limite=5,
        max_caracteres=600,
    )

    assert_true(result.status == "saved", "Debe guardar los 12 turnos permitidos.")
    assert_true(result.turn_count == 12, "El límite explícito debe ser 12.")
    assert_true("MARCA_U_1" in context, "El contexto compacto debe representar el primer turno.")
    assert_true("FIN_L_12" in context, "El contexto compacto debe representar el último turno.")
    assert_true(len(context) <= 600, "Debe respetar el presupuesto real de Persona.")


def test_manual_save_never_touches_observational_consolidation() -> None:
    client = FakePersistentClient()
    manager = _manager_with_private_turn(client)
    calls = {"consolidation": 0}

    def bomb_consolidation() -> None:
        calls["consolidation"] += 1
        raise AssertionError("El guardado manual no debe invocar el consolidador.")

    manager.consolidar_memorias_si_hace_falta = bomb_consolidation
    observations_before = manager._observaciones_desde_revision
    result = manager.guardar_fotografia_conversacional("save-no-consolidation")

    assert_true(result.status == "saved", "El diálogo con palabras visuales debe guardarse.")
    assert_true(calls["consolidation"] == 0, "No debe llamar consolidación.")
    assert_true(
        manager._observaciones_desde_revision == observations_before,
        "No debe adelantar el contador del batch observacional.",
    )
    assert_true(not client.deleted_ids, "No debe eliminar episodios.")
    assert_true(
        all(
            record["metadata"].get("tipo_evento") != "consolidacion_observacional"
            for record in client.records
        ),
        "No debe crear un checkpoint de consolidación.",
    )


def test_structured_log_covers_all_statuses_without_private_content() -> None:
    saved_client = FakePersistentClient()
    saved_manager = _manager_with_private_turn(saved_client)
    empty_manager = MemoryManager(FakePersistentClient())
    error_client = FakePersistentClient()
    error_manager = _manager_with_private_turn(error_client)

    class _CaptureHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    handler = _CaptureHandler()
    memory_logger = logging.getLogger("memory.memory_manager")
    memory_logger.addHandler(handler)
    try:
        saved = saved_manager.guardar_fotografia_conversacional("log-saved")
        already_saved = saved_manager.guardar_fotografia_conversacional(
            "log-already-saved"
        )
        empty = empty_manager.guardar_fotografia_conversacional("log-empty")
        error_client.list_fail_mode = "raise"
        error_client.list_fail_message = (
            "CANARIO_ERROR_PRIVADO con texto de usuario y metadata sensible"
        )
        error = error_manager.guardar_fotografia_conversacional("log-error")
    finally:
        memory_logger.removeHandler(handler)

    assert_true(saved.status == "saved", "El escenario de log saved debe guardar.")
    assert_true(
        already_saved.status == "already_saved",
        "El escenario duplicado debe producir already_saved.",
    )
    assert_true(empty.status == "empty", "El escenario vacío debe producir empty.")
    assert_true(error.status == "error", "El fallo de lectura debe producir error.")

    event_lines = [
        message
        for message in handler.messages
        if message.startswith("event=conversation_memory_save ")
    ]
    assert_true(
        len(event_lines) == 4,
        "Cada uno de los cuatro intentos debe producir exactamente una línea estructurada.",
    )
    assert_true(
        all("\n" not in line and "\r" not in line for line in event_lines),
        "Cada evento estructurado debe permanecer en una sola línea.",
    )

    by_transaction = {
        token.split("=", 1)[1]: line
        for line in event_lines
        for token in line.split()
        if token.startswith("transaction_id=")
    }
    expected = {
        "log-saved": (
            "saved",
            2,
            saved.fingerprint[:12],
            "true",
        ),
        "log-already-saved": (
            "already_saved",
            2,
            already_saved.fingerprint[:12],
            "true",
        ),
        "log-empty": ("empty", 0, "", "false"),
        "log-error": ("error", error.turn_count, error.fingerprint[:12], "false"),
    }
    assert_true(
        set(by_transaction) == set(expected),
        "Los cuatro IDs deben quedar correlacionados sin líneas extra.",
    )

    for transaction_id, (
        status,
        turn_count,
        fingerprint,
        write_confirmed,
    ) in expected.items():
        line = by_transaction[transaction_id]
        parsed_fields = {
            token.split("=", 1)[0]: token.split("=", 1)[1]
            for token in line.split()
        }
        expected_keys = {
            "event",
            "transaction_id",
            "status",
            "turn_count",
            "fingerprint",
            "persistent_write_confirmed",
        }
        if status == "error":
            expected_keys.add("error_type")
        assert_true(
            set(parsed_fields) == expected_keys,
            f"El evento de {transaction_id} solo debe usar campos permitidos.",
        )
        assert_true(f"status={status}" in line, f"Estado incorrecto para {transaction_id}.")
        assert_true(
            f"turn_count={turn_count}" in line,
            f"Conteo incorrecto para {transaction_id}.",
        )
        assert_true(
            f"fingerprint={fingerprint}" in line,
            f"Prefijo de huella incorrecto para {transaction_id}.",
        )
        assert_true(
            f"persistent_write_confirmed={write_confirmed}" in line,
            f"Confirmación durable incorrecta para {transaction_id}.",
        )
        if status == "error":
            assert_true(
                "error_type=ManualConversationSaveError" in line,
                "El error debe incluir un tipo estable sin incluir su mensaje.",
            )
        else:
            assert_true(
                "error_type=" not in line,
                "Los estados no erróneos no deben inventar un error_type.",
            )

    joined_logs = "\n".join(handler.messages)
    for private_canary in (
        "cactus azul",
        "faro verde",
        "CANARIO_PENSAMIENTO_PRIVADO",
        "CANARIO_ROUTE",
        "CANARIO_INTENT",
        "CANARIO_METADATA_PRIVADA",
        str(error.error or ""),
    ):
        assert_true(
            not private_canary or private_canary not in joined_logs,
            f"Ningún log del intento debe filtrar contenido privado: {private_canary}.",
        )


def test_structured_logging_is_fail_open_and_preserves_exceptions() -> None:
    manager = _manager_with_private_turn(FakePersistentClient())
    original_info = memory_manager_module.logger.info

    def broken_logger(*args, **kwargs) -> None:
        raise RuntimeError("fallo del handler de logging")

    memory_manager_module.logger.info = broken_logger
    try:
        result = manager.guardar_fotografia_conversacional("log-handler-failure")
    finally:
        memory_manager_module.logger.info = original_info

    assert_true(
        result.status == "saved",
        "Un fallo del logger no debe cambiar un guardado confirmado.",
    )

    exploding_manager = MemoryManager(FakePersistentClient())

    def explode(id_transaccion: str) -> ManualConversationSaveResult:
        raise ValueError("CANARIO_EXCEPCION_PRIVADA")

    exploding_manager._guardar_fotografia_conversacional = explode

    class _CaptureHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    handler = _CaptureHandler()
    memory_logger = logging.getLogger("memory.memory_manager")
    memory_logger.addHandler(handler)
    try:
        raised: Exception | None = None
        try:
            exploding_manager.guardar_fotografia_conversacional(
                "log-unexpected-exception"
            )
        except Exception as exc:
            raised = exc
    finally:
        memory_logger.removeHandler(handler)

    assert_true(
        isinstance(raised, ValueError),
        "El wrapper debe preservar la excepción funcional original.",
    )
    event_lines = [
        message
        for message in handler.messages
        if message.startswith("event=conversation_memory_save ")
    ]
    assert_true(
        len(event_lines) == 1
        and "status=error" in event_lines[0]
        and "error_type=ValueError" in event_lines[0],
        "Una excepción inesperada debe producir un único evento error tipado.",
    )
    assert_true(
        "CANARIO_EXCEPCION_PRIVADA" not in "\n".join(handler.messages),
        "El mensaje privado de la excepción no debe registrarse.",
    )


def test_real_chroma_survives_restart_and_recovers_last_turn() -> None:
    with TemporaryDirectory(prefix="lazzaro-manual-save-test-") as temp_dir:
        ledger_path = str(Path(temp_dir) / "memory_write_ledger.sqlite3")
        first: MemoryManager | None = MemoryManager(
            ChromaClient(temp_dir),
            memory_write_ledger_path=ledger_path,
        )
        try:
            for index in range(1, 13):
                first.conversation_memory.registrar_turno(
                    usuario=f"TURNO_REAL_{index} " + ("dato " * 30) + f"CIERRE_U_{index}",
                    lazzaro=f"RESPUESTA_REAL_{index} " + ("eco " * 30) + f"CIERRE_L_{index}",
                    pensamiento=f"PRIVADO_REAL_{index}",
                    metadata={"secreto": f"METADATA_REAL_{index}"},
                )
            saved = first.guardar_fotografia_conversacional("real-save-a")
            deduplicated = first.guardar_fotografia_conversacional("real-save-b")
            assert_true(saved.status == "saved", "Chroma real debe confirmar la escritura.")
            assert_true(
                deduplicated.status == "already_saved",
                "El segundo ID debe deduplicar la misma fotografía.",
            )
            fingerprint = saved.fingerprint
        finally:
            if first is not None:
                first.close()
            first = None
            gc.collect()

        restarted: MemoryManager | None = MemoryManager(
            ChromaClient(temp_dir),
            memory_write_ledger_path=ledger_path,
        )
        try:
            retry_b = restarted.guardar_fotografia_conversacional("real-save-b")
            assert_true(
                retry_b.status == "already_saved",
                "El recibo del segundo ID debe sobrevivir un reinicio real.",
            )
            raw = restarted.client.listar(
                where={"huella_guardado_conversacional": fingerprint},
                limite=1,
            )
            assert_true(raw is not None and len(raw["ids"]) == 1, "Debe existir una sola unidad.")
            document = raw["documents"][0]
            assert_true("CIERRE_L_12" in document, "La transcripción completa debe sobrevivir.")
            assert_true("PRIVADO_REAL" not in document, "Chroma no debe recibir pensamiento.")
            assert_true("METADATA_REAL" not in repr(raw["metadatas"]), "No debe filtrar metadata.")

            context = restarted.obtener_contexto_memoria(
                query="CIERRE_L_12",
                limite=5,
                max_caracteres=600,
            )
            assert_true(
                "CIERRE_L_12" in context,
                "El flujo real de Persona debe recibir una señal del último turno.",
            )
            restarted.consolidar_memorias_si_hace_falta()
            still_there = restarted.client.listar(
                where={"huella_guardado_conversacional": fingerprint},
                limite=1,
            )
            assert_true(
                still_there is not None and len(still_there["ids"]) == 1,
                "El consolidador observacional no debe eliminar la memoria manual.",
            )
        finally:
            if restarted is not None:
                restarted.close()
            restarted = None
            gc.collect()


def main() -> None:
    print("=== INICIANDO PRUEBA DE GUARDADO MANUAL DE RAM ===\n")
    test_snapshot_is_public_immutable_and_ram_stays_intact()
    test_empty_ram_and_write_failures_never_claim_success()
    test_transaction_and_snapshot_fingerprints_are_durable()
    test_transaction_receipt_update_failure_is_retryable()
    test_compact_digest_covers_the_last_turn_in_persona_context()
    test_manual_save_never_touches_observational_consolidation()
    test_structured_log_covers_all_statuses_without_private_content()
    test_structured_logging_is_fail_open_and_preserves_exceptions()
    test_real_chroma_survives_restart_and_recovers_last_turn()
    print(
        "[OK] test_manual_memory_save paso: persistencia pública, "
        "idempotente y separada del consolidador."
    )


if __name__ == "__main__":
    main()
