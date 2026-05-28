using System;
using System.Collections;
using Newtonsoft.Json;
using UnityEngine;
using UnityEngine.Networking;

public class ApiClient : MonoBehaviour
{
    public string EventoUrl = "http://localhost:8000/api/v1/evento";

    public IEnumerator EnviarEvento(
        EventoEntrante evento,
        Action<RespuestaAPI> onSuccess,
        Action<string> onFailure = null
    )
    {
        string json = JsonConvert.SerializeObject(evento);

        using (UnityWebRequest request = new UnityWebRequest(EventoUrl, "POST"))
        {
            byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json);
            request.uploadHandler = new UploadHandlerRaw(bodyRaw);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.timeout = 10;

            yield return request.SendWebRequest();

            if (request.result == UnityWebRequest.Result.Success)
            {
                try
                {
                    var respuesta = JsonConvert.DeserializeObject<RespuestaAPI>(request.downloadHandler.text);
                    if (respuesta == null)
                    {
                        onFailure?.Invoke("Respuesta vacia del backend.");
                    }
                    else if (respuesta.status == "error")
                    {
                        onFailure?.Invoke(respuesta.mensaje);
                    }
                    else
                    {
                        onSuccess?.Invoke(respuesta);
                    }
                }
                catch (Exception e)
                {
                    onFailure?.Invoke($"Error parseando JSON: {e.Message}");
                }
            }
            else
            {
                string body = request.downloadHandler != null ? request.downloadHandler.text : "";
                onFailure?.Invoke(
                    $"Fallo de conexion ({request.error}) codigo={request.responseCode}. {body}"
                );
            }
        }
    }
}
