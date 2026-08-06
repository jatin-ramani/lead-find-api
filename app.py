from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.business import router as business_router
from api.scanner import router as scanner_router
from api.scan_jobs import router as scan_jobs_router
from api.dashboard import router as dashboard_router

app = FastAPI(
    title="Lead Finder API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {
        "message": "Lead Finder API is running 🚀"
    }

app.include_router(business_router)
app.include_router(scanner_router)
app.include_router(scan_jobs_router)
app.include_router(dashboard_router)