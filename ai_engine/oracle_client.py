"""
oracle_client.py - Client de API Externa para la Capa 3.
Conecta a Lazzaro con modelos de lenguaje masivos en la nube
para resolver problemas matematicos, logicas y tecnicos complejos.
"""
import os
import httpx
from typing import List,Dict,Optional,Any
from core.logger import get_logger


logger = get_logger(__name__)

class OracleClient:
    """
    Cliente generico para APIS de completado conversacional.
    Pensado para consultas de alta precision (matematicas, codigo , logica).
    """

    def __init__(self,settings: Optional[Any] = None):
        """
        Inicializa el cliente desde variables de entorno o desde un objeto Settings.
        
        Args: 
            settings: Objeto con atributos ORACLE_API_URL, ORACLE_API_KEY, ORACLE_MODEL.
                Si es None, se leen directamente de las variables de entorno.
                """
        if settings is not None:
            self.api_url = getattr(
                settings,
                "ORACLE_API_URL",
                "https://api.groq.com/openai/v1/chat/completions",
            )
            self.api_key = getattr(settings, "ORACLE_API_KEY", "")
            self.model_name = getattr(settings,"ORACLE_MODEL","llama3-70b-8192")
            timeout = getattr(settings, "ORACLE_TIMEOUT", 15.0)
        else:
            self.api_url = os.getenv("ORACLE_API_URL", "https://api.groq.com/openai/v1/chat/completions")
            self.api_key = os.getenv("ORACLE_API_KEY", "")
            self.model_name = os.getenv("ORACLE_MODEL", "llama3-70b-8192")
            timeout = os.getenv("ORACLE_TIMEOUT", "15.0")

        self.timeout = float(timeout)
        self._supports_json_format = True  # cambiar a False si el proveedor no lo soporta

    @property
    def configurado (self) -> bool:
        """Indica si el cleitne tiene credenciales para funcionar."""
        return bool(self.api_key and self.api_key.strip())
    
    #Consulta principal
    def consultar_oraculo ( self,mensajes: List[Dict[str,str]]) -> Optional [str]:
        """
        Envia los mensajes al modelo en la nube y devuelve el texto de la respuesta.
        Si algo falla, retorna None para que el sistema utilice el fallback del LLM activo.
        ARGS:
            mensajes: lista de diccionarios con 'role' y 'content'.
            
            Returns: 
                Contenido textual de la respuesta, o None si hubo error.
            """
        
        if not self.configurado:
            logger.warning("OracleClient no esta configurado (API KEY ausente).")
            return None
        
        headers = {
            "Authorization" : f"Bearer {self.api_key}",
            "Content-Type" : "application/json",
        }

        payload: Dict [str, Any] = {
            "model" : self.model_name,
            "messages" : mensajes,
            "temperature": 0.1 # casi determinista para respuestas tecnicas
        }

        #Algunos proveedores (Groq, OpenAI) permiten forzar JSON 
        if self._supports_json_format:
            payload["response_format"] = {"type": "json_object"}

        try:
            logger.info ("Consultando al Oraculo (%s)...", self.model_name)
            with httpx.Client(timeout=self.timeout) as Client:
                respuesta = Client.post(self.api_url,headers = headers,json = payload)

                respuesta.raise_for_status()
                data = respuesta.json()

                contenido = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not contenido:
                    logger.warning("El oraculo devolvio una respuesta vacia.")
                    return None
                
                logger.info("Respuesta del Oraculo recibida (%d caracteres).", len(contenido))
                return contenido
        except httpx.TimeoutException:
            logger.error("Timeout al contactar al Oraculo (%1fs).", self.timeout)
            return None
        except httpx.HTTPStatusError as e: 
            logger.error("Error HTTP %d del oraculo: %s",e.response.status_code,e.response.text[:200])
            return None
        except Exception as e: 
            logger.error("Error inesperado en OracleClient: %s", e)
            return None
