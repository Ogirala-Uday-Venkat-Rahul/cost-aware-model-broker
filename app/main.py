from fastapi import FastAPI

app = FastAPI(title="Model-Routing Service")


@app.get("/health")
def health():
    return {"status": "ok"}
