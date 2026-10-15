import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="SCG Content Machine",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "system": "SCG Content Machine",
        "status": "ONLINE",
        "version": "1.0.0"
    }


@app.get("/health")
def health():
    return JSONResponse(
        {
            "status": "healthy",
            "service": "content-machine"
        }
    )
