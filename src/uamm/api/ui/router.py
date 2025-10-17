from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

# Reuse existing agent streaming implementation
from uamm.api.routes import AnswerRequest, answer_stream as _answer_stream


router = APIRouter(prefix="/ui", tags=["UI"])


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
def playground(request: Request) -> HTMLResponse:
    """Render the agent playground UI."""
    context = {
        "request": request,
        "defaults": {
            "domain": "default",
        },
    }
    return templates.TemplateResponse(request, "playground.html", context)


@router.get("/home", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Home hub with workspace selector and quick actions."""
    context = {"request": request, "fullpage": True}
    return templates.TemplateResponse(request, "home.html", context)


@router.get("/rag", response_class=HTMLResponse)
def rag_home(request: Request) -> HTMLResponse:
    """Render the RAG tools page (upload, ingest, search)."""
    context = {"request": request}
    return templates.TemplateResponse(request, "rag.html", context)


@router.get("/obs", response_class=HTMLResponse)
def obs_home(request: Request) -> HTMLResponse:
    """Render the observability page (recent steps and metrics)."""
    context = {"request": request}
    return templates.TemplateResponse(request, "obs.html", context)


@router.get("/workspaces", response_class=HTMLResponse)
def workspaces_home(request: Request) -> HTMLResponse:
    """Render the workspaces admin-lite page."""
    context = {"request": request}
    return templates.TemplateResponse(request, "workspaces.html", context)


@router.get("/cp", response_class=HTMLResponse)
def cp_home(request: Request) -> HTMLResponse:
    """Render the CP threshold viewer page."""
    context = {"request": request}
    return templates.TemplateResponse(request, "cp.html", context)


@router.get("/evals", response_class=HTMLResponse)
def evals_home(request: Request) -> HTMLResponse:
    """Render the evals and tuning page."""
    context = {"request": request}
    return templates.TemplateResponse(request, "evals.html", context)


@router.get("/docs", response_class=HTMLResponse)
def docs_home(request: Request) -> HTMLResponse:
    """Render the in-app UI documentation page."""
    context = {"request": request}
    return templates.TemplateResponse(request, "docs.html", context)


@router.get("/agent/stream")
def ui_answer_stream(
    request: Request,
    question: str,
    domain: str = "default",
    use_memory: bool = True,
    memory_budget: int = 8,
    max_refinements: int = 2,
    borderline_delta: float = 0.05,
    stream_lite: bool = False,
    workspace: Optional[str] = None,
) -> Response:
    """GET wrapper for SSE streaming so browsers can use EventSource.

    Delegates to the existing POST `/agent/answer/stream` logic by constructing
    an `AnswerRequest` from query parameters.
    """
    req = AnswerRequest(
        question=question,
        use_memory=use_memory,
        memory_budget=memory_budget,
        stream=True,
        stream_lite=bool(stream_lite),
        max_refinements=max_refinements,
        borderline_delta=borderline_delta,
        domain=domain,
    )
    # Bind workspace context if provided via query (EventSource cannot set headers)
    if workspace:
        try:
            request.state.workspace = workspace
        except Exception:
            pass
    # Call the existing streaming implementation directly.
    return _answer_stream(req, request)
