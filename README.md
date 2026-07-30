# Lazzaro

Intento de agente cognitivo porque estaba aburrido :3

## Chat en terminal

Con el backend activo y Unity cerrado:

```powershell
python main.py
```

En otra terminal:

```powershell
python -m terminal_client
```

El cliente es exclusivamente conversacional. Sus únicos comandos locales son
`/ayuda`, `/estado` y `/salir`; cualquier mensaje normal pasa por Persona u
Oracle, salvo el guardado manual, que usa una rama determinista y solo verbaliza
el resultado. Toda respuesta exitosa aceptada por el cliente es `hablar`; los
fallos de red o de contrato se muestran localmente como `[error]`.

Durante una conversación, cualquiera de estas frases base solicita a Lazzaro
persistir una fotografía pública de lo que todavía conserva en RAM:

```text
guarda lo que hemos hablado
guarda lo que tengas en ram en tu mente
```

No existe un comando local `/guardar`: la solicitud entra como habla natural de
Capa 2. El guardado conserva la RAM, excluye pensamiento privado y no activa
descanso ni el consolidador observacional.

Consulta [la guía del chat en terminal](docs/chat_terminal.md) para ver el
alcance, opciones de conexión, barreras de seguridad y limitaciones aprobadas.

## Registro de pruebas

- [Estado actual 2026-07-29](docs/estado_actual_proyecto_2026-07-29.md): estado del cliente terminal, validaciones aprobadas y pendientes de campo.
- [Pruebas Unity/Terminal-Python](docs/registro_pruebas_unity.md): bitacora de glitches, causas raiz y cambios de estabilidad.
- [Arquitectura completa](docs/arquitectura_completa_lazzaro.md): mapa de carpetas, archivos, contratos de entrada/salida y diagramas de Unity, terminal y Python.
- [Contratos cognitivos](docs/cognitive_contracts.md): invariantes de cada capa y de ambos clientes.
- [Chat en terminal](docs/chat_terminal.md): uso del cliente conversacional, guardado natural y límites vigentes.
- [Guardado manual de RAM](docs/plan_guardado_ram_conversacional.md): contrato, implementación y validaciones del segundo entregable.
- [Auditoria integral 2026-07-12](docs/auditoria_integral_2026-07-12.md): escaneo histórico de bugs, riesgos y piezas obsoletas.
- [Constelacion HTML](constelacion.html): visualizacion generada por `telescopio/visualizar_constelacion.py` desde memorias ChromaDB.
