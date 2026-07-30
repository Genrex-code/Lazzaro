"""
Cliente de terminal de LAZZARO.

Este frontend solo conversa: nunca interpreta ni ejecuta acciones fisicas.
Todo el estado cognitivo permanece en el backend compartido.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import re
import uuid
from typing import Any

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 300.0
ANSI_ESCAPE_RE = re.compile(
    r"(?:\x1b\][^\x07]*(?:\x07|\x1b\\))"
    r"|(?:\x1b\[[0-?]*[ -/]*[@-~])"
    r"|(?:\x1b[@-_])"
)

HELP_TEXT = """Comandos locales:
  /ayuda   Muestra esta ayuda.
  /estado  Consulta el estado publico de Lazzaro.
  /salir   Cierra el chat.

Cualquier otro texto se envia a Lazzaro como conversacion."""


class TerminalChatError(RuntimeError):
    """Error legible producido por el transporte o el contrato conversacional."""


def sanitizar_texto_terminal(valor: object) -> str:
    """Elimina secuencias ANSI y controles capaces de alterar la terminal."""
    texto = ANSI_ESCAPE_RE.sub("", str(valor or ""))
    return "".join(
        caracter
        for caracter in texto
        if caracter in {"\n", "\t"} or (ord(caracter) >= 32 and caracter != "\x7f")
    )


class TerminalChatClient:
    """Cliente HTTP delgado para conversar con el backend existente."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        http_client: httpx.Client | None = None,
    ):
        url_limpia = str(base_url or "").strip().rstrip("/")
        if not url_limpia:
            raise ValueError("La URL del backend no puede estar vacia.")
        if timeout_seconds <= 0:
            raise ValueError("El timeout debe ser mayor que cero.")

        self.base_url = url_limpia
        self.timeout_seconds = float(timeout_seconds)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds),
        )

    def __enter__(self) -> "TerminalChatClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        """Cierra el transporte si fue creado por esta instancia."""
        if self._owns_http_client:
            self._http.close()

    def enviar_mensaje(
        self,
        mensaje: str,
        *,
        id_transaccion: str | None = None,
    ) -> str:
        """Envia un turno y devuelve solamente el texto publico de Lazzaro."""
        mensaje_limpio = str(mensaje or "").strip()
        if not mensaje_limpio:
            raise ValueError("El mensaje no puede estar vacio.")

        transaccion = str(id_transaccion or uuid.uuid4()).strip()
        if not transaccion:
            raise ValueError("El id de transaccion no puede estar vacio.")

        payload = {
            "origen": "terminal",
            "tipo_evento": "comando_usuario",
            "id_transaccion": transaccion,
            "objeto_involucrado": "ninguno",
            "detalles": {
                "mensaje": mensaje_limpio,
                "solicita_decision": True,
                "modo_conversacion": True,
            },
        }
        respuesta = self._request_json("POST", "/api/v1/comando", json=payload)
        return self._extraer_texto_hablar(respuesta)

    def obtener_estado(self) -> dict[str, Any]:
        """Consulta el snapshot publico sin crear un turno conversacional."""
        respuesta = self._request_json("GET", "/api/v1/estado")
        if not isinstance(respuesta, dict):
            raise TerminalChatError("El backend devolvio un estado invalido.")
        return respuesta

    def _request_json(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TerminalChatError(
                f"Lazzaro tardo mas de {self.timeout_seconds:g}s en responder."
            ) from exc
        except httpx.RequestError as exc:
            raise TerminalChatError(
                f"No pude conectar con el backend en {self.base_url}."
            ) from exc

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detalle = self._detalle_error_http(response)
            raise TerminalChatError(
                f"El backend respondio HTTP {response.status_code}: {detalle}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TerminalChatError("El backend no devolvio JSON valido.") from exc

        if not isinstance(data, dict):
            raise TerminalChatError("El backend devolvio una respuesta inesperada.")
        return data

    @staticmethod
    def _detalle_error_http(response: httpx.Response) -> str:
        if response.status_code >= 500:
            if response.status_code == 503:
                return "servicio no disponible"
            return "error interno"

        try:
            data = response.json()
        except ValueError:
            return "solicitud rechazada"
        if isinstance(data, dict):
            detalle = data.get("mensaje") or data.get("detail")
            if isinstance(detalle, str) and detalle.strip():
                return sanitizar_texto_terminal(detalle).strip()[:240]
        return "solicitud rechazada"

    @staticmethod
    def _extraer_texto_hablar(respuesta: dict[str, Any]) -> str:
        status = str(respuesta.get("status", "")).strip().lower()
        if status != "ok":
            mensaje = str(respuesta.get("mensaje") or "El backend reporto un error.")
            raise TerminalChatError(mensaje)

        accion = respuesta.get("accion")
        if not isinstance(accion, dict):
            raise TerminalChatError("Lazzaro no devolvio una respuesta hablada.")

        nombre = str(accion.get("nombre", "")).strip()
        if nombre != "hablar":
            raise TerminalChatError(
                f"El canal terminal bloqueo una accion no conversacional: {nombre or 'desconocida'}."
            )

        parametros = accion.get("parametros")
        if not isinstance(parametros, dict):
            raise TerminalChatError("La respuesta hablada no contiene parametros validos.")

        texto_crudo = parametros.get("texto_a_comunicar")
        if not isinstance(texto_crudo, str) or not texto_crudo.strip():
            raise TerminalChatError("Lazzaro devolvio una respuesta hablada vacia.")
        texto = sanitizar_texto_terminal(texto_crudo).strip()
        if not texto:
            raise TerminalChatError("Lazzaro devolvio una respuesta hablada vacia.")
        return texto


def formatear_estado(estado: dict[str, Any]) -> str:
    """Convierte el snapshot de API en un bloque compacto para la terminal."""
    etiquetas = (
        ("energia", "Energia"),
        ("estres", "Estres"),
        ("confianza", "Confianza"),
        ("motivacion", "Motivacion"),
        ("foco", "Foco"),
    )
    lineas = ["Estado de Lazzaro:"]
    for clave, etiqueta in etiquetas:
        if clave in estado:
            lineas.append(f"  {etiqueta}: {estado[clave]}")

    tarea = estado.get("tarea_actual")
    lineas.append(f"  Tarea actual: {tarea or 'ninguna'}")
    return "\n".join(lineas)


def ejecutar_chat(
    client: TerminalChatClient,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Ejecuta el REPL conversacional hasta que el usuario decida salir."""
    output_fn("Chat de Lazzaro listo. Escribe /ayuda para ver los comandos.")

    while True:
        try:
            entrada = input_fn("Tu > ")
        except (EOFError, KeyboardInterrupt):
            output_fn("\nCerrando chat.")
            return 0

        texto = str(entrada or "").strip()
        if not texto:
            continue

        comando = texto.lower()
        if comando == "/salir":
            output_fn("Cerrando chat.")
            return 0
        if comando == "/ayuda":
            output_fn(HELP_TEXT)
            continue
        if comando == "/estado":
            try:
                output_fn(sanitizar_texto_terminal(formatear_estado(client.obtener_estado())))
            except KeyboardInterrupt:
                output_fn("\nCerrando chat.")
                return 0
            except TerminalChatError as exc:
                output_fn(f"[error] {sanitizar_texto_terminal(exc)}")
            continue
        if comando.startswith("/"):
            output_fn("Comando desconocido. Usa /ayuda.")
            continue

        try:
            respuesta = client.enviar_mensaje(texto)
        except KeyboardInterrupt:
            output_fn("\nCerrando chat.")
            return 0
        except (TerminalChatError, ValueError) as exc:
            output_fn(f"[error] {sanitizar_texto_terminal(exc)}")
            continue

        output_fn(f"Lazzaro > {respuesta}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat conversacional de LAZZARO.")
    parser.add_argument(
        "--url",
        default=DEFAULT_BASE_URL,
        help=f"URL base del backend (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout por turno en segundos (default: {DEFAULT_TIMEOUT_SECONDS:g}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        with TerminalChatClient(args.url, args.timeout) as client:
            return ejecutar_chat(client)
    except (TerminalChatError, ValueError) as exc:
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
