import uvicorn

from .config import Settings

cfg = Settings()

if __name__ == "__main__":
    uvicorn.run("fin_runtime.server:app", host="127.0.0.1", port=cfg.app_port)
