from fastapi import FastAPI
from webhook.router import router
from auth.router import router as auth_router


app = FastAPI(title="Aman HMO Pre-Auth Agent")
app.include_router(router)
app.include_router(auth_router)

@app.get("/health")
def health():
    return {"status": "ok"}
