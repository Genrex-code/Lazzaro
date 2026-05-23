import logging
from fastapi import FastAPI
from api.routes import router

# --- CONFIGURACIÓN DE LOGS FUERTES ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("lazzaro.log"), # Guarda todo en un archivo
        logging.StreamHandler()             # Lo muestra en la terminal
    ]
)
logger = logging.getLogger(__name__)

# --- INICIALIZACIÓN DE LA APP ---
app = FastAPI(title="Lazzaro Backend", version="1.0")

# Incluimos las rutas desde api/routes.py
app.include_router(router)

@app.on_event("startup")
async def startup_event():
    logger.info("🤖 Lazzaro Core encendiendo... Sistemas nominales.")

if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor en puerto 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)