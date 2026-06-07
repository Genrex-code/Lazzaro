from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import re
import sys
from typing import Any


if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from memory.chroma_client import ChromaClient

try:
    from telescopio.constelacion_cognitiva import ConstelacionCognitiva
except ModuleNotFoundError:
    from constelacion_cognitiva import ConstelacionCognitiva


DEFAULT_DB_PATH = "./lazzaro_data"
DEFAULT_COLLECTION = "experiencias"


def exportar_a_html(
    constelacion: ConstelacionCognitiva,
    archivo: str = "constelacion.html",
    title: str = "Constelacion Cognitiva",
) -> str:
    """
    Genera un archivo HTML interactivo a partir de una ConstelacionCognitiva.
    """
    datos = constelacion.a_dict()
    contexto = datos.get("contexto", {})
    nodos = _preparar_nodos(datos.get("nodos", []))
    enlaces = _preparar_enlaces(datos.get("enlaces", []))
    html_contenido = _construir_html(nodos, enlaces, title, contexto)

    ruta_salida = Path(archivo).resolve()
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_text(html_contenido, encoding="utf-8")

    print(f"Visualizacion generada: {ruta_salida}")
    return str(ruta_salida)


def exportar_chromadb_a_html(
    archivo: str = "constelacion.html",
    db_path: str = DEFAULT_DB_PATH,
    collection_name: str = DEFAULT_COLLECTION,
    limite: int = 80,
    vecinos: int = 2,
    similitud_minima: float = 0.82,
    title: str = "Constelacion de ChromaDB",
) -> str:
    """
    Construye una constelacion desde ChromaDB y la exporta a HTML.
    """
    constelacion = construir_constelacion_desde_chromadb(
        db_path=db_path,
        collection_name=collection_name,
        limite=limite,
        vecinos=vecinos,
        similitud_minima=similitud_minima,
    )
    return exportar_a_html(constelacion, archivo=archivo, title=title)


def construir_constelacion_desde_chromadb(
    db_path: str = DEFAULT_DB_PATH,
    collection_name: str = DEFAULT_COLLECTION,
    limite: int = 80,
    vecinos: int = 2,
    similitud_minima: float = 0.82,
) -> ConstelacionCognitiva:
    """
    Lee memorias desde ChromaDB y arma una constelacion visualizable.

    Las conexiones se construyen en dos capas:
    - Memoria -> hubs de metadatos (tipo_evento, accion, objeto, origen)
    - Memoria <-> memoria por similitud semantica en ChromaDB
    """
    client = ChromaClient(db_path=db_path, collection_name=collection_name)
    try:
        registros = client.listar(limite=limite)
        constelacion = ConstelacionCognitiva(nombre=f"chromadb:{collection_name}")
        constelacion.actualizar_contexto(
            db_path=str(Path(db_path).resolve()),
            collection_name=collection_name,
            generado_en=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            limite=limite,
            vecinos=vecinos,
            similitud_minima=similitud_minima,
        )

        if not registros:
            constelacion.actualizar_contexto(total_memorias=0)
            return constelacion

        ids = registros.get("ids") or []
        documentos = registros.get("documents") or []
        metadatas = registros.get("metadatas") or []
        constelacion.actualizar_contexto(total_memorias=len(ids))

        if not ids:
            return constelacion

        memorias: list[dict[str, Any]] = []
        for index, memoria_id in enumerate(ids):
            texto = str(documentos[index]) if index < len(documentos) else ""
            metadata = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
            memoria = _construir_memoria_cruda(str(memoria_id), texto, metadata)
            memorias.append(memoria)
            _agregar_nodo_memoria(constelacion, memoria)
            _agregar_hubs_metadata(constelacion, memoria)

        _conectar_memorias_similares(
            constelacion=constelacion,
            client=client,
            memorias=memorias,
            vecinos=vecinos,
            similitud_minima=similitud_minima,
        )

        constelacion.actualizar_contexto(
            nodos_generados=len(constelacion.nodos),
            enlaces_generados=len(constelacion.enlaces),
        )
        return constelacion
    finally:
        client.close()


def _construir_memoria_cruda(memoria_id: str, texto: str, metadata: dict[str, Any]) -> dict[str, Any]:
    tipo = _texto_seguro(metadata.get("tipo"), "desconocido")
    tipo_evento = _texto_seguro(metadata.get("tipo_evento"), "sin_tipo_evento")
    impacto = _float_seguro(metadata.get("impacto_emocional"), 0.5)
    valencia = _float_seguro(metadata.get("valencia"), 0.0)
    accion = _texto_seguro(metadata.get("accion"), "")
    objeto = _texto_seguro(metadata.get("objeto"), "")
    origen = _texto_seguro(metadata.get("origen"), "")
    timestamp = _texto_seguro(metadata.get("timestamp"), "")
    preview = _resumir_texto(texto, 220)

    return {
        "id": memoria_id,
        "node_id": f"memoria:{memoria_id}",
        "tipo": tipo,
        "tipo_evento": tipo_evento,
        "impacto": impacto,
        "valencia": valencia,
        "accion": accion,
        "objeto": objeto,
        "origen": origen,
        "timestamp": timestamp,
        "texto": texto,
        "preview": preview,
    }


def _agregar_nodo_memoria(constelacion: ConstelacionCognitiva, memoria: dict[str, Any]) -> None:
    categoria = f"memoria_{memoria['tipo']}"
    label = _label_memoria(memoria)
    descripcion = _descripcion_memoria(memoria)
    constelacion.crear_nodo(
        nombre=memoria["node_id"],
        categoria=categoria,
        valor=max(0.2, min(1.0, memoria["impacto"])),
        descripcion=descripcion,
        metadata={
            "label": label,
            "doc_id": memoria["id"],
            "tipo_evento": memoria["tipo_evento"],
            "accion": memoria["accion"],
            "objeto": memoria["objeto"],
            "origen": memoria["origen"],
            "timestamp": memoria["timestamp"],
            "preview": memoria["preview"],
        },
    )


def _agregar_hubs_metadata(constelacion: ConstelacionCognitiva, memoria: dict[str, Any]) -> None:
    relaciones = [
        ("tipo_evento", memoria["tipo_evento"], "tipo_evento", 0.45),
        ("accion", memoria["accion"], "accion", 0.35),
        ("objeto", memoria["objeto"], "objeto", 0.35),
        ("origen", memoria["origen"], "origen", 0.35),
    ]

    for categoria, valor, relacion, peso in relaciones:
        if not valor:
            continue
        hub_id = _asegurar_hub(constelacion, categoria, valor)
        constelacion.conectar(
            memoria["node_id"],
            hub_id,
            relacion,
            peso=peso,
            metadata={"valor": valor},
        )


def _asegurar_hub(constelacion: ConstelacionCognitiva, categoria: str, valor: str) -> str:
    hub_id = f"{categoria}:{_slug(valor)}"
    existente = constelacion.obtener_nodo(hub_id)
    if existente is None:
        constelacion.crear_nodo(
            nombre=hub_id,
            categoria=categoria,
            valor=1.0,
            descripcion=f"{categoria} = {valor}",
            metadata={"label": valor, "count": 1},
        )
        return hub_id

    conteo = existente.metadata.get("count", 1)
    if isinstance(conteo, (int, float)) and not isinstance(conteo, bool):
        nuevo_conteo = int(conteo) + 1
    else:
        nuevo_conteo = 2
    existente.metadata["count"] = nuevo_conteo
    existente.valor = float(nuevo_conteo)
    return hub_id


def _conectar_memorias_similares(
    constelacion: ConstelacionCognitiva,
    client: ChromaClient,
    memorias: list[dict[str, Any]],
    vecinos: int,
    similitud_minima: float,
) -> None:
    if vecinos <= 0:
        return

    enlaces_creados: dict[tuple[str, str], float] = {}
    memoria_por_id = {memoria["id"]: memoria for memoria in memorias}
    n_resultados = max(2, vecinos + 1)

    for memoria in memorias:
        resultados = client.buscar(memoria["texto"], n_resultados=n_resultados)
        if not resultados:
            continue

        ids_batches = resultados.get("ids") or []
        dist_batches = resultados.get("distances") or []
        vecinos_ids = ids_batches[0] if ids_batches else []
        distancias = dist_batches[0] if dist_batches else []

        agregados = 0
        for indice, vecino_id in enumerate(vecinos_ids):
            vecino_id = str(vecino_id)
            if vecino_id == memoria["id"]:
                continue

            vecino = memoria_por_id.get(vecino_id)
            if vecino is None:
                continue

            distancia = distancias[indice] if indice < len(distancias) else 1.0
            similitud = max(0.0, min(1.0, 1.0 - float(distancia)))
            if similitud < similitud_minima:
                continue

            origen, destino = sorted((memoria["node_id"], vecino["node_id"]))
            llave = (origen, destino)
            if llave in enlaces_creados:
                continue

            enlaces_creados[llave] = similitud
            if constelacion.obtener_nodo(origen) and constelacion.obtener_nodo(destino):
                constelacion.conectar(
                    origen,
                    destino,
                    "similaridad",
                    peso=similitud,
                    metadata={
                        "distancia": round(float(distancia), 4),
                        "similitud": round(similitud, 4),
                    },
                )
                agregados += 1

            if agregados >= vecinos:
                break


def _preparar_nodos(nodos_crudos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodos_vis = []
    for nodo in nodos_crudos:
        nombre = _texto_seguro(nodo.get("nombre"), "")
        categoria = _texto_seguro(nodo.get("categoria"), "general")
        if not nombre:
            continue

        metadata = nodo.get("metadata", {}) if isinstance(nodo.get("metadata"), dict) else {}
        valor_original = nodo.get("valor")
        valor_numerico = _valor_visual(valor_original)
        label = _texto_seguro(metadata.get("label"), _label_legible(nombre))
        descripcion = _texto_seguro(nodo.get("descripcion"), "")
        tooltip = _tooltip_nodo(
            label=label,
            categoria=categoria,
            valor_original=valor_original,
            descripcion=descripcion,
            metadata=metadata,
        )

        nodos_vis.append(
            {
                "id": nombre,
                "label": label,
                "group": categoria,
                "value": round(valor_numerico, 3),
                "size": _size_desde_valor(valor_numerico),
                "title": tooltip,
            }
        )
    return nodos_vis


def _preparar_enlaces(enlaces_crudos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aristas_vis = []
    for enlace in enlaces_crudos:
        origen = _texto_seguro(enlace.get("origen"), "")
        destino = _texto_seguro(enlace.get("destino"), "")
        relacion = _texto_seguro(enlace.get("relacion"), "")
        if not origen or not destino:
            continue

        metadata = enlace.get("metadata", {}) if isinstance(enlace.get("metadata"), dict) else {}
        peso = _float_seguro(enlace.get("peso"), 0.5)
        estilo = _estilo_enlace(relacion, peso)
        title = _tooltip_enlace(relacion, peso, metadata)

        aristas_vis.append(
            {
                "from": origen,
                "to": destino,
                "label": estilo["label"],
                "width": estilo["width"],
                "arrows": estilo["arrows"],
                "dashes": estilo["dashes"],
                "color": estilo["color"],
                "smooth": estilo["smooth"],
                "title": title,
            }
        )
    return aristas_vis


def _construir_html(
    nodos: list[dict[str, Any]],
    enlaces: list[dict[str, Any]],
    titulo: str,
    contexto: dict[str, Any],
) -> str:
    nodos_json = json.dumps(nodos, ensure_ascii=False)
    enlaces_json = json.dumps(enlaces, ensure_ascii=False)
    contexto_resumen = _resumen_contexto_html(contexto, len(nodos), len(enlaces))
    colores_json = json.dumps(
        {
            "memoria_episodico": {"color": "#74c0fc", "highlight": "#a5d8ff"},
            "memoria_semantico": {"color": "#ffd166", "highlight": "#ffe29a"},
            "tipo_evento": {"color": "#ff7b72", "highlight": "#ffa198"},
            "accion": {"color": "#80ed99", "highlight": "#b7f7c6"},
            "objeto": {"color": "#c77dff", "highlight": "#ddb0ff"},
            "origen": {"color": "#48cae4", "highlight": "#90e0ef"},
        },
        ensure_ascii=False,
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(titulo)}</title>
    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        :root {{
            --bg-1: #06111f;
            --bg-2: #10243b;
            --panel: rgba(7, 16, 28, 0.82);
            --panel-border: rgba(166, 209, 255, 0.16);
            --text: #eef6ff;
            --muted: #9eb3c7;
            --accent: #9ad1ff;
        }}
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            min-height: 100vh;
            color: var(--text);
            font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
            background:
                radial-gradient(circle at top left, rgba(83, 162, 255, 0.20), transparent 28%),
                radial-gradient(circle at bottom right, rgba(154, 209, 255, 0.10), transparent 26%),
                linear-gradient(135deg, var(--bg-1), var(--bg-2));
            overflow: hidden;
        }}
        #red {{
            width: 100vw;
            height: 100vh;
        }}
        .panel {{
            position: absolute;
            z-index: 10;
            width: min(360px, calc(100vw - 24px));
            background: var(--panel);
            border: 1px solid var(--panel-border);
            border-radius: 18px;
            box-shadow: 0 18px 45px rgba(0, 0, 0, 0.30);
            backdrop-filter: blur(10px);
        }}
        .panel h1,
        .panel h2 {{
            margin: 0 0 8px 0;
            font-weight: 600;
            letter-spacing: 0.02em;
        }}
        .panel p {{
            margin: 0;
            color: var(--muted);
            line-height: 1.45;
            font-size: 13px;
        }}
        .header {{
            top: 12px;
            left: 12px;
            padding: 16px 18px;
        }}
        .stats {{
            bottom: 12px;
            left: 12px;
            padding: 14px 16px;
        }}
        .legend {{
            top: 12px;
            right: 12px;
            padding: 14px 16px;
        }}
        .chips {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 12px;
        }}
        .chip {{
            padding: 6px 10px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.08);
            font-size: 12px;
            color: var(--text);
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 8px;
            color: var(--muted);
            font-size: 13px;
        }}
        .legend-swatch {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex: 0 0 12px;
        }}
        @media (max-width: 720px) {{
            .panel {{
                width: calc(100vw - 20px);
            }}
            .header {{
                top: 10px;
                left: 10px;
            }}
            .legend {{
                top: auto;
                right: 10px;
                bottom: 150px;
            }}
            .stats {{
                left: 10px;
                bottom: 10px;
            }}
        }}
    </style>
</head>
<body>
    <div id="red"></div>

    <section class="panel header">
        <h1>{html.escape(titulo)}</h1>
        <p>Mapa visual de memorias almacenadas en ChromaDB y sus conexiones mas cercanas.</p>
        <div class="chips">
            {contexto_resumen}
        </div>
    </section>

    <section class="panel legend">
        <h2>Leyenda</h2>
        <div id="leyenda"></div>
    </section>

    <section class="panel stats">
        <h2>Lectura</h2>
        <p>Arrastra para explorar, usa la rueda para zoom y toca un nodo para inspeccionar su contexto.</p>
    </section>

    <script>
        const rawNodes = {nodos_json};
        const rawEdges = {enlaces_json};
        const nodes = new vis.DataSet(rawNodes);
        const edges = new vis.DataSet(rawEdges);
        const container = document.getElementById("red");
        const groupColors = {colores_json};

        function getColorForGroup(group) {{
            if (groupColors[group]) return groupColors[group];
            const hue = (group.length * 53) % 360;
            return {{
                color: `hsl(${{hue}}, 70%, 62%)`,
                highlight: `hsl(${{hue}}, 78%, 75%)`
            }};
        }}

        const allGroups = [...new Set(rawNodes.map((node) => node.group))];
        const groups = {{}};
        allGroups.forEach((group) => {{
            const tone = getColorForGroup(group);
            groups[group] = {{
                color: {{
                    background: tone.color,
                    border: tone.highlight,
                    highlight: {{
                        background: tone.highlight,
                        border: tone.highlight
                    }},
                    hover: {{
                        background: tone.highlight,
                        border: tone.highlight
                    }}
                }}
            }};
        }});

        const options = {{
            nodes: {{
                shape: "dot",
                scaling: {{
                    min: 10,
                    max: 36,
                    label: {{ enabled: true, min: 11, max: 24 }}
                }},
                borderWidth: 1.5,
                borderWidthSelected: 3,
                shadow: {{
                    enabled: true,
                    color: "rgba(0,0,0,0.35)",
                    size: 10
                }},
                font: {{
                    color: "#edf6ff",
                    size: 14,
                    face: "Segoe UI"
                }}
            }},
            edges: {{
                selectionWidth: 2,
                hoverWidth: 1.2,
                smooth: {{
                    enabled: true,
                    type: "dynamic"
                }},
                font: {{
                    color: "#dceeff",
                    size: 10,
                    align: "middle",
                    background: "rgba(7,16,28,0.8)",
                    strokeWidth: 0
                }}
            }},
            groups,
            layout: {{
                improvedLayout: true
            }},
            physics: {{
                solver: "forceAtlas2Based",
                forceAtlas2Based: {{
                    gravitationalConstant: -42,
                    springLength: 145,
                    springConstant: 0.07,
                    damping: 0.45,
                    avoidOverlap: 0.55
                }},
                stabilization: {{
                    iterations: 180,
                    fit: true
                }}
            }},
            interaction: {{
                hover: true,
                tooltipDelay: 120,
                navigationButtons: true,
                keyboard: true,
                multiselect: true
            }}
        }};

        const network = new vis.Network(container, {{ nodes, edges }}, options);

        const legendDiv = document.getElementById("leyenda");
        legendDiv.innerHTML = allGroups.map((group) => {{
            const tone = getColorForGroup(group);
            return `
                <div class="legend-item">
                    <span class="legend-swatch" style="background:${{tone.color}};"></span>
                    <span>${{group}}</span>
                </div>
            `;
        }}).join("");

        network.once("stabilizationIterationsDone", () => {{
            network.setOptions({{ physics: false }});
        }});
    </script>
</body>
</html>"""


def _tooltip_nodo(
    label: str,
    categoria: str,
    valor_original: Any,
    descripcion: str,
    metadata: dict[str, Any],
) -> str:
    partes = [
        f"<b>{html.escape(label)}</b>",
        f"Categoria: {html.escape(categoria)}",
        f"Valor: {html.escape(_valor_legible(valor_original))}",
    ]
    if descripcion:
        partes.append(html.escape(descripcion))
    preview = _texto_seguro(metadata.get("preview"), "")
    if preview:
        partes.append(f"Preview: {html.escape(preview)}")
    return "<br>".join(partes)


def _tooltip_enlace(relacion: str, peso: float, metadata: dict[str, Any]) -> str:
    partes = [
        f"<b>{html.escape(relacion or 'conexion')}</b>",
        f"Peso: {peso:.2f}",
    ]
    if "similitud" in metadata:
        partes.append(f"Similitud: {float(metadata['similitud']):.4f}")
    if "distancia" in metadata:
        partes.append(f"Distancia: {float(metadata['distancia']):.4f}")
    valor = _texto_seguro(metadata.get("valor"), "")
    if valor:
        partes.append(f"Valor: {html.escape(valor)}")
    return "<br>".join(partes)


def _estilo_enlace(relacion: str, peso: float) -> dict[str, Any]:
    width = max(1.0, min(6.0, 1.0 + peso * 4.0))
    if relacion == "similaridad":
        return {
            "label": "",
            "width": round(width, 2),
            "arrows": "",
            "dashes": False,
            "color": {"color": "rgba(154, 209, 255, 0.28)", "highlight": "#d6ecff"},
            "smooth": {"type": "continuous", "roundness": 0.25},
        }

    return {
        "label": relacion,
        "width": round(max(1.0, min(4.0, 0.8 + peso * 3.0)), 2),
        "arrows": "to",
        "dashes": [6, 6],
        "color": {"color": "rgba(255, 255, 255, 0.22)", "highlight": "#ffffff"},
        "smooth": {"type": "cubicBezier", "roundness": 0.12},
    }


def _size_desde_valor(valor: float) -> float:
    return max(12.0, min(38.0, 12.0 + valor * 24.0))


def _valor_visual(valor: Any) -> float:
    if isinstance(valor, bool):
        return 1.0 if valor else 0.2
    if isinstance(valor, (int, float)):
        if valor > 1.0:
            return max(0.2, min(1.0, float(valor) / 10.0))
        return max(0.0, min(1.0, float(valor)))
    if valor is None:
        return 0.45
    return 0.55


def _valor_legible(valor: Any) -> str:
    if isinstance(valor, float):
        return f"{valor:.2f}"
    return str(valor)


def _label_memoria(memoria: dict[str, Any]) -> str:
    partes = [memoria["tipo_evento"]]
    if memoria["objeto"]:
        partes.append(memoria["objeto"])
    elif memoria["accion"]:
        partes.append(memoria["accion"])
    return "\n".join(partes[:2])


def _descripcion_memoria(memoria: dict[str, Any]) -> str:
    segmentos = [
        f"id={memoria['id']}",
        f"tipo={memoria['tipo']}",
        f"tipo_evento={memoria['tipo_evento']}",
    ]
    if memoria["accion"]:
        segmentos.append(f"accion={memoria['accion']}")
    if memoria["objeto"]:
        segmentos.append(f"objeto={memoria['objeto']}")
    if memoria["origen"]:
        segmentos.append(f"origen={memoria['origen']}")
    if memoria["timestamp"]:
        segmentos.append(f"timestamp={memoria['timestamp']}")
    segmentos.append(memoria["preview"])
    return " | ".join(parte for parte in segmentos if parte)


def _label_legible(nombre: str) -> str:
    if ":" in nombre:
        return nombre.split(":", 1)[1]
    return nombre


def _slug(texto: str) -> str:
    limpio = re.sub(r"[^a-zA-Z0-9]+", "_", texto.strip().lower()).strip("_")
    return limpio or "sin_valor"


def _texto_seguro(valor: Any, default: str) -> str:
    if isinstance(valor, str):
        limpio = valor.strip()
        return limpio or default
    if valor is None:
        return default
    return str(valor)


def _float_seguro(valor: Any, default: float) -> float:
    if isinstance(valor, bool):
        return default
    if isinstance(valor, (int, float)):
        return float(valor)
    try:
        return float(str(valor))
    except (TypeError, ValueError):
        return default


def _resumir_texto(texto: str, max_chars: int) -> str:
    plano = " ".join(texto.split())
    if len(plano) <= max_chars:
        return plano
    return plano[: max_chars - 3].rstrip() + "..."


def _resumen_contexto_html(contexto: dict[str, Any], total_nodos: int, total_enlaces: int) -> str:
    chips = [
        ("Coleccion", _texto_seguro(contexto.get("collection_name"), DEFAULT_COLLECTION)),
        ("Memorias", str(contexto.get("total_memorias", "0"))),
        ("Nodos", str(total_nodos)),
        ("Enlaces", str(total_enlaces)),
        ("Vecinos", str(contexto.get("vecinos", "0"))),
    ]
    return "".join(
        f'<span class="chip">{html.escape(titulo)}: {html.escape(valor)}</span>'
        for titulo, valor in chips
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exporta una constelacion HTML a partir de memorias almacenadas en ChromaDB."
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Ruta de la base de datos Chroma.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Nombre de la coleccion.")
    parser.add_argument("--salida", default="constelacion.html", help="Archivo HTML de salida.")
    parser.add_argument("--limite", type=int, default=80, help="Numero maximo de memorias a visualizar.")
    parser.add_argument("--vecinos", type=int, default=2, help="Conexiones semanticas maximas por memoria.")
    parser.add_argument(
        "--similitud-minima",
        type=float,
        default=0.82,
        help="Umbral minimo de similitud para dibujar una conexion entre memorias.",
    )
    parser.add_argument("--titulo", default="Constelacion de ChromaDB", help="Titulo del HTML.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    exportar_chromadb_a_html(
        archivo=args.salida,
        db_path=args.db_path,
        collection_name=args.collection,
        limite=args.limite,
        vecinos=args.vecinos,
        similitud_minima=args.similitud_minima,
        title=args.titulo,
    )
