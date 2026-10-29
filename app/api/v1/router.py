from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin_papers,
    admin_repos,
    admin_users,
    auth,
    health,
    papers,
    repos,
    search,
    sessions,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(admin_users.router)
api_router.include_router(admin_papers.router)
api_router.include_router(admin_repos.router)
api_router.include_router(search.router)
api_router.include_router(sessions.router)
api_router.include_router(repos.router)
api_router.include_router(papers.router)
