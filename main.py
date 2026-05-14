from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from webhook.router import router
from auth.router import router as auth_router


app = FastAPI(title="Aman HMO Pre-Auth Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://api.saasprolabs.io"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(auth_router)

@app.get("/health")
def health():
    return {"status": "ok"}
