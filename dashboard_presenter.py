from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


UI_DIST_PATH = Path(__file__).resolve().parent / "dashboard_ui" / "dist"


def register_dashboard_ui(app: FastAPI) -> None:
    """Register the compiled Vue dashboard after the API routes."""
    index_path = UI_DIST_PATH / "index.html"
    assets_path = UI_DIST_PATH / "assets"

    if not index_path.is_file() or not assets_path.is_dir():
        raise RuntimeError(
            f"Dashboard UI build is missing: expected {index_path} and {assets_path}. "
            "Run `npm run build` from dashboard_ui before starting the dashboard."
        )

    app.mount("/assets", StaticFiles(directory=assets_path), name="dashboard-assets")

    async def serve_index() -> FileResponse:
        return FileResponse(
            index_path,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/", include_in_schema=False)
    async def dashboard_index() -> FileResponse:
        return await serve_index()

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard_fallback(path: str) -> FileResponse:
        if path in {"api", "photos", "dithered"} or path.startswith(("api/", "photos/", "dithered/")):
            raise HTTPException(status_code=404, detail="Not found")
        return await serve_index()
