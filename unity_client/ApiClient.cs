using System;
using System.Collections;
using System.Collections.Generic;
using Newtonsoft.Json;
using UnityEngine;
using UnityEngine.Networking;

public class ApiClient : MonoBehaviour
{
    private static readonly Dictionary<string, string> MensajesPublicos =
        new Dictionary<string, string>(StringComparer.Ordinal)
        {
            { "RECONCILIATION_REQUIRED", "El turno requiere reconciliacion antes de continuar." },
            { "TURN_ABORTED", "El turno fallo de forma controlada y no se volvera a ejecutar." },
            { "TURN_PROCESSING_FAILED", "No se pudo procesar el turno." },
            { "TRANSACTION_INCOMPLETE", "La transaccion esta incompleta y requiere revision." },
            { "TRANSACTION_CONFLICT", "El identificador de transaccion ya pertenece a otro evento." },
            { "OPERATIONAL_STORAGE_UNAVAILABLE", "El almacenamiento operacional no esta disponible." },
            { "AGENT_UNAVAILABLE", "El agente no esta disponible." },
            { "STATE_READ_FAILED", "No se pudo leer el estado del agente." },
            { "CONSTELLATION_READ_FAILED", "No se pudo leer la constelacion cognitiva." },
            { "VALIDATION_ERROR", "La solicitud no cumple el contrato esperado." },
            { "INTERNAL_ERROR", "Ocurrio un error interno." }
        };

    public string EventoUrl = "http://localhost:8000/api/v1/evento";
    [Range(15, 180)]
    public int TimeoutSegundos = 90;

    public IEnumerator EnviarEvento(
        EventoEntrante evento,
        Action<RespuestaAPI> onSuccess,
        Action<ApiFailure> onFailure = null
    )
    {
        string json = JsonConvert.SerializeObject(evento);

        using (UnityWebRequest request = new UnityWebRequest(EventoUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json);
            request.uploadHandler = new UploadHandlerRaw(bodyRaw);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.timeout = Mathf.Clamp(TimeoutSegundos, 15, 180);

            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                try
                {
                    string responseBody = request.downloadHandler.text;
                    RespuestaAPI respuesta = null;
                    if (ResponseDuplicateWireBoundary.TryValidateResponse(responseBody))
                    {
                        if (ResponseEnvelopeWireBoundaryV1.TryValidateResponse(responseBody))
                        {
                            if (ActionEnvelopeWireBoundary.TryValidateResponse(responseBody))
                            {
                                if (PhysicalActionWireBoundaryV1.TryValidateResponse(responseBody))
                                {
                                    if (AuthorityWireBoundary.TryValidateResponse(responseBody))
                                    {
                                        respuesta = JsonConvert.DeserializeObject<RespuestaAPI>(responseBody);
                                        if (!ActionBindingVerifierV1.TryVerifyResponse(respuesta))
                                        {
                                            respuesta = null;
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if (respuesta == null)
                    {
                        onFailure?.Invoke(CrearFalloLocal(
                            "INVALID_RESPONSE",
                            "El backend devolvio una respuesta invalida.",
                            request.responseCode,
                            false
                        ));
                    }
                    else if (respuesta.status == "error")
                    {
                        // Un error 2xx legacy nunca convierte mensaje remoto en UX.
                        onFailure?.Invoke(CrearFalloLocal(
                            "BACKEND_ERROR",
                            "El backend reporto un error.",
                            request.responseCode,
                            false
                        ));
                    }
                    else if (respuesta.status == "ok" || respuesta.status == "pendiente")
                    {
                        onSuccess?.Invoke(respuesta);
                    }
                    else
                    {
                        onFailure?.Invoke(CrearFalloLocal(
                            "INVALID_RESPONSE",
                            "El backend devolvio una respuesta invalida.",
                            request.responseCode,
                            false
                        ));
                    }
                }
                catch (Exception)
                {
                    onFailure?.Invoke(CrearFalloLocal(
                        "INVALID_RESPONSE",
                        "El backend devolvio una respuesta invalida.",
                        request.responseCode,
                        false
                    ));
                }
            }
            else
            {
                string body = request.downloadHandler != null ? request.downloadHandler.text : "";
                ApiFailure failure;
                if (TryParsePublicError(body, request.responseCode, out failure))
                {
                    onFailure?.Invoke(failure);
                }
                else if (EsTimeout(request))
                {
                    onFailure?.Invoke(CrearFalloLocal(
                        "TRANSPORT_TIMEOUT",
                        "La solicitud al backend excedio el tiempo limite.",
                        request.responseCode,
                        true
                    ));
                }
                else if (request.responseCode == 0)
                {
                    onFailure?.Invoke(CrearFalloLocal(
                        "TRANSPORT_FAILURE",
                        "No fue posible contactar al backend.",
                        0,
                        true
                    ));
                }
                else
                {
                    // HTML, proxy bodies y JSON ajeno se descartan por completo.
                    onFailure?.Invoke(CrearFalloLocal(
                        "BACKEND_ERROR",
                        "El backend devolvio un error no reconocido.",
                        request.responseCode,
                        false
                    ));
                }
            }
        }
    }

    private static bool TryParsePublicError(
        string body,
        long httpStatus,
        out ApiFailure failure
    )
    {
        failure = null;
        if (string.IsNullOrWhiteSpace(body))
        {
            return false;
        }

        PublicErrorEnvelope envelope;
        try
        {
            envelope = JsonConvert.DeserializeObject<PublicErrorEnvelope>(body);
        }
        catch (Exception)
        {
            return false;
        }

        if (
            envelope == null
            || envelope.version != 1
            || envelope.error == null
            || (envelope.status != "error" && envelope.status != "pendiente")
        )
        {
            return false;
        }

        Guid parsedCorrelation;
        if (!Guid.TryParse(envelope.error.correlation_id, out parsedCorrelation))
        {
            return false;
        }

        string localMessage;
        if (!MensajesPublicos.TryGetValue(envelope.error.code ?? "", out localMessage))
        {
            failure = CrearFalloLocal(
                "BACKEND_ERROR",
                "El backend devolvio un error no reconocido.",
                httpStatus,
                false,
                parsedCorrelation.ToString()
            );
            return true;
        }

        if (!StatusCompatible(envelope.error.code, envelope.status))
        {
            return false;
        }

        // error.message se ignora deliberadamente; el code elige una plantilla local.
        failure = CrearFalloLocal(
            envelope.error.code,
            localMessage,
            httpStatus,
            false,
            parsedCorrelation.ToString()
        );
        return true;
    }

    private static bool StatusCompatible(string code, string status)
    {
        bool pendingCode = code == "RECONCILIATION_REQUIRED"
            || code == "TRANSACTION_INCOMPLETE";
        return pendingCode ? status == "pendiente" : status == "error";
    }

    private static bool EsTimeout(UnityWebRequest request)
    {
        if (request == null || request.responseCode != 0 || string.IsNullOrEmpty(request.error))
        {
            return false;
        }
        return request.error.IndexOf("timeout", StringComparison.OrdinalIgnoreCase) >= 0
            || request.error.IndexOf("timed out", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    private static ApiFailure CrearFalloLocal(
        string code,
        string message,
        long httpStatus,
        bool transportFailure,
        string correlationId = ""
    )
    {
        return new ApiFailure
        {
            Code = code,
            Message = message,
            CorrelationId = correlationId,
            HttpStatus = httpStatus,
            TransportFailure = transportFailure
        };
    }
}
