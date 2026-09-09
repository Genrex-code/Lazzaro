"""
Pruebas unitarias para ChromaClient (compatibles con la implementación actual).
Cubre: colección vacía, inserción, búsqueda semántica, filtrado, metadatos,
persistencia tras cierre y eliminación.
"""

from __future__ import annotations

import gc
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Union

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from memory.chroma_client import ChromaClient


# ------------------------------------------------------------
# Helpers para normalizar respuestas de ChromaDB
# ------------------------------------------------------------
def _ids(resultado: Optional[Union[dict, Any]]) -> list[str]:
    if not resultado:
        return []
    # Usar valor por defecto vacío para evitar None iterable
    ids = resultado.get("ids", []) if hasattr(resultado, "get") else getattr(resultado, "ids", [])
    if ids and isinstance(ids[0], list):
        return [str(item) for item in ids[0]]
    return [str(item) for item in ids] if ids else []


def _documents(resultado: Optional[Union[dict, Any]]) -> list[str]:
    if not resultado:
        return []
    # Normalizar a lista vacía si falta o es None
    docs = resultado.get("documents", []) if hasattr(resultado, "get") else getattr(resultado, "documents", [])
    if docs and isinstance(docs[0], list):
        return [str(item) for item in docs[0]]
    return [str(item) for item in docs]


def _metadatas(resultado: Optional[Union[dict, Any]]) -> list[dict]:
    if not resultado:
        return []
    # Obtener metadatas de forma segura y normalizar a lista
    if hasattr(resultado, "get"):
        metas = resultado.get("metadatas", [])
    else:
        metas = getattr(resultado, "metadatas", []) or []

    # Evitar iterar sobre None o valores no iterables
    if metas is None:
        return []

    # Algunas respuestas anidan la lista dentro de otra lista
    if isinstance(metas, list) and metas and isinstance(metas[0], list):
        inner = metas[0] or []
        return [dict(item) for item in inner if item is not None]

    # Asegurarse de que cada item pueda convertirse a dict
    result: list[dict] = []
    if isinstance(metas, dict):
        return [dict(metas)]
    if isinstance(metas, list):
        for item in metas:
            if item is None:
                continue
            try:
                result.append(dict(item))
            except Exception:
                # Ignorar elementos que no sean mapeables
                continue
    return result


# ------------------------------------------------------------
# FIXTURE: contexto que limpia automáticamente
# ------------------------------------------------------------
class ChromaTestContext:
    """Crea un ChromaClient sobre un directorio temporal y lo cierra al salir."""
    def __init__(self, collection_name: str = "test_coleccion"):
        self.temp_dir = TemporaryDirectory(prefix="lazzaro-chromadb-")
        self.collection_name = collection_name
        self.client: Optional[ChromaClient] = None

    def __enter__(self) -> ChromaClient:
        self.client = ChromaClient(
            db_path=self.temp_dir.name,
            collection_name=self.collection_name,
        )
        return self.client

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        self.temp_dir.cleanup()
        gc.collect()


# ------------------------------------------------------------
# Pruebas
# ------------------------------------------------------------
def test_coleccion_vacia():
    """Una colección nueva está disponible y no tiene documentos."""
    with ChromaTestContext("vacia") as client:
        assert client.disponible, "La colección debe estar disponible"
        assert client.contar() == 0, "El conteo inicial debe ser 0"
        datos = client.listar(limite=5)
        assert datos is not None and len(_ids(datos)) == 0, "Listar en vacío no devuelve IDs"
        res = client.buscar("consulta", n_resultados=3)
        assert res is not None, "Una búsqueda sana vacía debe distinguirse de un fallo"
        assert res.get("ids") == [[]], "Buscar en vacío conserva el batch vacío de Chroma"


def test_insercion_y_conteo():
    """Agregar documentos incrementa el conteo y no duplica IDs existentes."""
    with ChromaTestContext("insercion") as client:
        ok = client.agregar(
            documentos=["uno", "dos", "tres"],
            metadatos=[{"tag": i} for i in range(3)],
            ids=["a", "b", "c"],
        )
        assert ok, "La inserción inicial debe ser exitosa"
        assert client.contar() == 3, "Debe haber 3 documentos"

        # Chroma 1.5.9 acepta el ID existente como no-op. ROOT-015 definira
        # un receipt que distinga created/already; aqui solo protegemos el efecto.
        client.agregar(
            documentos=["uno"],
            metadatos=[{"tag": "duplicate-fixture"}],
            ids=["a"],
        )
        assert client.contar() == 3, "El ID existente no debe crear una cuarta fila"


def test_busqueda_semantica():
    """La búsqueda semántica devuelve documentos similares con distancias coherentes."""
    with ChromaTestContext("busqueda") as client:
        client.agregar(
            documentos=["cactus azul y faro verde", "esfera roja persistente", "nota sobre cápsula"],
            metadatos=[{"tipo": "planta"}, {"tipo": "objeto"}, {"tipo": "nota"}],
            ids=["doc1", "doc2", "doc3"],
        )
        res = client.buscar(consulta="cactus", n_resultados=3)
        assert res is not None, "La búsqueda no debe devolver None"
        ids_encontrados = _ids(res)
        assert "doc1" in ids_encontrados, "El documento del cactus debe aparecer primero"

        # Verificar distancia (el más similar debe ser el propio doc1)
        distancias = res.get("distances")
        assert distancias and distancias[0], "Debe haber distancias"
        assert distancias[0][0] < distancias[0][-1], "El primer resultado debe ser el más similar"


def test_filtrado_y_listado():
    """where filtra correctamente en listar y buscar."""
    with ChromaTestContext("filtros") as client:
        client.agregar(
            documentos=["rojo", "verde", "azul"],
            metadatos=[
                {"color": "rojo", "intensidad": 1},
                {"color": "verde", "intensidad": 2},
                {"color": "azul", "intensidad": 1},
            ],
            ids=["r", "v", "a"],
        )
        # listar con filtro
        filtrados = client.listar(where={"intensidad": 1}, limite=5)
        assert filtrados is not None
        assert set(_ids(filtrados)) == {"r", "a"}, "Filtro intensidad=1 debe devolver dos docs"

        # buscar con filtro
        res = client.buscar(consulta="colores", where={"color": "verde"}, n_resultados=5)
        assert res is not None
        assert _ids(res) == ["v"], "Filtro en buscar debe restringir al documento verde"


def test_metadata_actualizada():
    """Actualizar metadatos y verificarlos después."""
    with ChromaTestContext("metadata") as client:
        client.agregar(
            documentos=["original"],
            metadatos=[{"estado": "inicial"}],
            ids=["meta1"],
        )
        actualizados = {"estado": "actualizado", "sincronizado": True}
        ok = client.actualizar_metadatos(ids=["meta1"], metadatos=[actualizados])
        assert ok, "La actualización debe confirmar True"

        datos = client.listar(where={"sincronizado": True}, limite=5)
        assert datos is not None
        assert "meta1" in _ids(datos), "El documento debe encontrarse con el nuevo metadato"
        metas = _metadatas(datos)
        assert metas[0].get("estado") == "actualizado", "El metadato debe reflejar el nuevo valor"


def test_persistencia_tras_reabrir():
    """Los datos sobreviven al cerrar y reabrir la misma colección."""
    temp_dir = TemporaryDirectory(prefix="persist-")
    coleccion = "persistente"

    # Escribir
    client1 = ChromaClient(db_path=temp_dir.name, collection_name=coleccion)
    try:
        client1.agregar(documentos=["permanente"], metadatos=[{"v": 1}], ids=["p1"])
        client1.agregar(documentos=["efímero"], metadatos=[{"v": 2}], ids=["p2"])
    finally:
        client1.close()

    # Reabrir
    client2 = ChromaClient(db_path=temp_dir.name, collection_name=coleccion)
    try:
        assert client2.contar() == 2, "Tras reabrir deben seguir existiendo 2 documentos"
        datos = client2.listar(limite=5)
        assert datos is not None and set(_ids(datos)) == {"p1", "p2"}, "Ambos IDs deben recuperarse"

        res = client2.buscar(consulta="permanente", n_resultados=2)
        assert res is not None and "p1" in _ids(res), "La búsqueda semántica funciona tras reabrir"
    finally:
        client2.close()
    temp_dir.cleanup()


def test_eliminacion():
    """Eliminar documentos reduce el conteo y ya no aparecen."""
    with ChromaTestContext("eliminar") as client:
        insertado = client.agregar(
            documentos=["a", "b", "c"],
            metadatos=[{"kind": "delete-fixture", "index": i} for i in range(3)],
            ids=["x", "y", "z"],
        )
        assert insertado, "La precondición de inserción válida debe cumplirse"
        assert client.contar() == 3
        assert client.eliminar(["x", "y"]), "Eliminar debe devolver True"
        assert client.contar() == 1, "Debe quedar un solo documento"
        datos = client.listar(limite=5)
        assert datos is not None and _ids(datos) == ["z"], "Solo debe aparecer el documento no eliminado"


# ------------------------------------------------------------
def main():
    pruebas = [
        ("Colección vacía", test_coleccion_vacia),
        ("Inserción y conteo", test_insercion_y_conteo),
        ("Búsqueda semántica", test_busqueda_semantica),
        ("Filtrado y listado", test_filtrado_y_listado),
        ("Metadatos actualizados", test_metadata_actualizada),
        ("Persistencia tras reabrir", test_persistencia_tras_reabrir),
        ("Eliminación", test_eliminacion),
    ]
    for nombre, func in pruebas:
        try:
            func()
            print(f"[✔] {nombre}")
        except AssertionError as e:
            print(f"[✘] {nombre}: {e}")
        except Exception as e:
            print(f"[✘] {nombre} (error): {e}")

    print("\n=== PRUEBAS FINALIZADAS ===")


if __name__ == "__main__":
    main()
