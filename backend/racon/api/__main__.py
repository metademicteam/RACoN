"""Entrypoint for python -m backend.racon.api."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.racon.api.app:app", host="127.0.0.1", port=8765, reload=False)
