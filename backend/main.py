from backend.database.routers import router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app: FastAPI = FastAPI()
origins: list[str] = ["http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)