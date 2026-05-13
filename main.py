from fastapi import FastAPI
from webhook.router import router

app = FastAPI(title="Aman HMO Pre-Auth Agent")
app.include_router(router)

@app.get("/health")
def health():
    return {"status": "ok"}
