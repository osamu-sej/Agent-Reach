"""Browser UI and bounded background jobs for the research engine."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
from threading import Lock
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import SOURCES, diagnostics, markdown_report, research


STATIC_DIR = Path(__file__).with_name("static")
REPORT_DIR = Path(os.environ.get("REACH_REPORT_DIR", "reports/web"))
MAX_ACTIVE = 2
MAX_HISTORY = 50


class ResearchRequest(BaseModel):
    theme: str = Field(min_length=2, max_length=200)
    sources: list[str] = Field(min_length=1, max_length=len(SOURCES))
    depth: str = "balanced"
    limit: int = Field(default=5, ge=1, le=10)
    max_pages: int = Field(default=20, ge=0, le=40)
    use_scrapling: bool = False


class JobStore:
    def __init__(self):
        self.lock = Lock()
        self.jobs = {}
        self.executor = ThreadPoolExecutor(max_workers=MAX_ACTIVE, thread_name_prefix="reach-web")

    def submit(self, request):
        with self.lock:
            active = sum(job["status"] in ("queued", "running") for job in self.jobs.values())
            if active >= MAX_ACTIVE:
                raise HTTPException(status_code=429, detail="同時実行できる調査は2件までです。")
            finished = [key for key, value in self.jobs.items() if value["status"] in ("complete", "failed")]
            for key in finished[:-MAX_HISTORY]:
                self.jobs.pop(key, None)
            job_id = uuid4().hex
            job = {"id": job_id, "status": "queued", "theme": request.theme.strip(),
                   "created_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
                   "error": None, "result": None}
            self.jobs[job_id] = job
        self.executor.submit(self._run, job_id, request)
        return self.public(job_id)

    def _run(self, job_id, request):
        with self.lock:
            self.jobs[job_id]["status"] = "running"
        try:
            data = research(request.theme.strip(), request.sources, request.limit,
                            request.max_pages, request.use_scrapling, depth=request.depth,
                            use_opencli=False, use_direct=False)
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            json_path = REPORT_DIR / (job_id + ".json")
            md_path = REPORT_DIR / (job_id + ".md")
            json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            md_path.write_text(markdown_report(data), encoding="utf-8")
            with self.lock:
                self.jobs[job_id].update(status="complete", result=data,
                                         finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            with self.lock:
                self.jobs[job_id].update(status="failed", error=str(exc)[:300],
                                         finished_at=datetime.now(timezone.utc).isoformat())

    def public(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise HTTPException(status_code=404, detail="調査が見つかりません。")
            return {key: job[key] for key in ("id", "status", "theme", "created_at", "finished_at", "error")}

    def result(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise HTTPException(status_code=404, detail="調査が見つかりません。")
            if job["status"] != "complete":
                raise HTTPException(status_code=409, detail="調査はまだ完了していません。")
            return job["result"]


store = JobStore()
app = FastAPI(title="Agent-Reach", docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


def require_token(authorization: str = Header(default="")):
    expected = os.environ.get("APP_ACCESS_TOKEN", "")
    if expected and not secrets.compare_digest(authorization, "Bearer " + expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="アクセスキーを入力してください。")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/api/capabilities", dependencies=[Depends(require_token)])
def capabilities():
    return {"sources": list(SOURCES), "diagnostics": diagnostics(), "max_active": MAX_ACTIVE}


@app.post("/api/research", status_code=202, dependencies=[Depends(require_token)])
def create_research(request: ResearchRequest):
    if request.depth not in ("quick", "balanced", "deep"):
        raise HTTPException(status_code=422, detail="調査の深さが不正です。")
    if any(source not in SOURCES for source in request.sources):
        raise HTTPException(status_code=422, detail="対象媒体が不正です。")
    if request.use_scrapling and not diagnostics()["scrapling_installed"]:
        raise HTTPException(status_code=422, detail="Scraplingがインストールされていません。")
    return store.submit(request)


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str):
    return store.public(job_id)


@app.get("/api/jobs/{job_id}/result", dependencies=[Depends(require_token)])
def get_result(job_id: str):
    return store.result(job_id)


@app.get("/api/jobs/{job_id}/report", dependencies=[Depends(require_token)])
def get_report(job_id: str):
    store.result(job_id)
    path = REPORT_DIR / (job_id + ".md")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="レポートファイルが見つかりません。")
    return FileResponse(path, media_type="text/markdown; charset=utf-8",
                        filename="agent-reach-" + job_id[:8] + ".md")


def main():
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("APP_ACCESS_TOKEN"):
        raise SystemExit("公開する場合はAPP_ACCESS_TOKENを設定してください。")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("reach_research.web:app", host=host, port=port)
