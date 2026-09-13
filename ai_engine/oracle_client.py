"""
oracle_client.py - Client de API Externa para la Capa 3.
Conecta a Lazzaro con modelos de lenguaje masivos en la nube
para resolver problemas matematicos, logicas y tecnicos complejos.
"""
import os
import httpx
import time
from typing import Dict,Optional,Any
from ai_engine.ai_client import LLMAuditBuffer, LLMCallAudit
from ai_engine.prompt_plan import PromptPlanV1
from ai_engine.provider_prompt_projection import project_openai_messages
from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure


logger = get_logger(__name__)

class OracleClient(LLMAuditBuffer):
    """
    Cliente generico para APIS de completado conversacional.
    Pensado para consultas de alta precision (matematicas, codigo , logica).
    """

    provider_name = "oracle_external"

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
                getattr(settings, "GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions"),
            )
            self.api_key = getattr(settings, "ORACLE_API_KEY", None) or getattr(settings, "GROQ_API_KEY", "")
            self.model_name = getattr(settings, "ORACLE_MODEL", None) or getattr(
                settings,
                "GROQ_MODEL",
                "llama-3.1-8b-instant",
            )
            timeout = getattr(settings, "ORACLE_TIMEOUT", 15.0)
        else:
            self.api_url = os.getenv("ORACLE_API_URL", "https://api.groq.com/openai/v1/chat/completions")
            self.api_key = os.getenv("ORACLE_API_KEY") or os.getenv("GROQ_API_KEY", "")
            self.model_name = os.getenv("ORACLE_MODEL") or os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
            timeout = os.getenv("ORACLE_TIMEOUT", "15.0")

        self.timeout = float(timeout)
        self._supports_json_format = True  # cambiar a False si el proveedor no lo soporta

    @property
    def configurado (self) -> bool:
        """Indica si el cleitne tiene credenciales para funcionar."""
        return bool(self.api_key and self.api_key.strip())
    
    # Consulta principal: solo acepta el contrato tipado de confianza.
    def consultar_plan(self, plan: PromptPlanV1) -> Optional[str]:
        """
        Proyecta PromptPlanV1 al formato nativo y devuelve el texto de respuesta.
        Si algo falla, retorna None para que el sistema utilice el fallback del LLM activo.
        """
        if not isinstance(plan, PromptPlanV1):
            raise TypeError("consultar_plan requiere PromptPlanV1")
        mensajes = project_openai_messages(plan)

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

        started_ns = time.perf_counter_ns()
        try:
            logger.info ("Consultando al Oraculo (%s)...", self.model_name)
            with httpx.Client(timeout=self.timeout) as Client:
                respuesta = Client.post(self.api_url,headers = headers,json = payload)

                respuesta.raise_for_status()
                data = respuesta.json()

                contenido = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not contenido:
                    finished_ns = time.perf_counter_ns()
                    self._record_llm_audit(
                        LLMCallAudit(
                            provider=self.provider_name,
                            model_id=self.model_name,
                            fallback_used=False,
                            status="empty",
                            error_type="EmptyResponse",
                            duration_ms=self.elapsed_ms(started_ns, finished_ns),
                            started_ns=started_ns,
                        )
                    )
                    logger.warning("El oraculo devolvio una respuesta vacia.")
                    return None
                
                finished_ns = time.perf_counter_ns()
                self._record_llm_audit(
                    LLMCallAudit(
                        provider=self.provider_name,
                        model_id=self.model_name,
                        fallback_used=False,
                        status="ok",
                        error_type=None,
                        duration_ms=self.elapsed_ms(started_ns, finished_ns),
                        started_ns=started_ns,
                    )
                )
                logger.info("Respuesta del Oraculo recibida (%d caracteres).", len(contenido))
                return contenido
        except httpx.TimeoutException:
            finished_ns = time.perf_counter_ns()
            self._record_llm_audit(
                LLMCallAudit(
                    provider=self.provider_name,
                    model_id=self.model_name,
                    fallback_used=False,
                    status="timeout",
                    error_type="TimeoutError",
                    duration_ms=self.elapsed_ms(started_ns, finished_ns),
                    started_ns=started_ns,
                )
            )
            logger.error("Timeout al contactar al Oraculo (%1fs).", self.timeout)
            return None
        except httpx.HTTPStatusError as e: 
            finished_ns = time.perf_counter_ns()
            self._record_llm_audit(
                LLMCallAudit(
                    provider=self.provider_name,
                    model_id=self.model_name,
                    fallback_used=False,
                    status="error",
                    error_type=type(e).__name__,
                    duration_ms=self.elapsed_ms(started_ns, finished_ns),
                    started_ns=started_ns,
                )
            )
            log_safe_boundary_failure(
                logger,
                component="oracle.generate",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
                http_status=e.response.status_code,
            )
            return None
        except Exception as e: 
            finished_ns = time.perf_counter_ns()
            error_type = self.stable_error_type(e)
            self._record_llm_audit(
                LLMCallAudit(
                    provider=self.provider_name,
                    model_id=self.model_name,
                    fallback_used=False,
                    status="timeout" if error_type == "TimeoutError" else "error",
                    error_type=error_type,
                    duration_ms=self.elapsed_ms(started_ns, finished_ns),
                    started_ns=started_ns,
                )
            )
            log_safe_boundary_failure(
                logger,
                component="oracle.generate",
                code="UPSTREAM_PROVIDER_FAILED",
                error=e,
            )
            return None
