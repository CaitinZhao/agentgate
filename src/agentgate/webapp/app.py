"""AgentGate web platform assembly (doc 33 §6).

One process hosts everything: the REST API (default :8030), the resident OTLP receiver
(:4318), the resident LLM recording proxy (:8300), and the global serial worker thread.
Fixed ports are the contract for cross-machine agents — they configure the trace endpoint
and proxy base_url once (doc 33 WD5-9). Data lives under data/ outside src.
"""
import os
import threading
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db
from .routes import admin_routes, auth_routes, benchmark_routes, content_routes, run_routes

_STATUS_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>AgentGate Platform</title></head>
<body style="font-family:sans-serif;max-width:40em;margin:4em auto">
<h1>AgentGate Platform</h1>
<p>API: <code>/api/v1</code> &middot; health: <a href="/api/v1/health">/api/v1/health</a></p>
<p>The web frontend (W2) is not built yet — run <code>npm run build</code> in
<code>web/</code> and restart, then this page serves the UI.</p>
</body></html>"""


def _start_proxy(port: int):
    """Run the resident recording proxy in a daemon thread (uvicorn skips signal handler
    installation outside the main thread). Returns the server; verify .started before relying
    on it — a taken port (stale proxy from a previous session) must not pass silently."""
    import time as _time
    import uvicorn
    from ..trace.llm_proxy import app as proxy_app
    config = uvicorn.Config(proxy_app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="agentgate-proxy").start()
    deadline = _time.time() + 10
    while not server.started and _time.time() < deadline and not server.should_exit:
        _time.sleep(0.2)
    if not server.started:
        print("[agentgate] WARNING: resident proxy failed to bind :%d — another proxy may "
              "already be listening; recording stays with that process until it is stopped"
              % port)
    return server


def _frontend_dist():
    """Locate the built frontend: AGENTGATE_FRONTEND_DIST env > repo layout (src/) > the
    bundle layout inside the platform image (/app/agentgate/web/dist)."""
    env = os.environ.get("AGENTGATE_FRONTEND_DIST", "")
    cands = [Path(env)] if env else []
    cands += [Path(__file__).resolve().parents[3] / "web" / "dist",
              Path("/app/agentgate/web/dist")]
    for c in cands:
        if (c / "index.html").exists():
            return c
    return None


def create_app(data_dir: str = None, worker_poll: float = 2.0,
               start_services: bool = True) -> FastAPI:
    """Build the FastAPI app. data_dir overrides the data root (tests); start_services=False
    keeps only the REST API (used by tests and nested tooling)."""
    if data_dir:
        os.environ["AGENTGATE_DATA_DIR"] = data_dir

    app = FastAPI(title="AgentGate Platform", version="0.2.0")
    origins = [o.strip() for o in os.environ.get(
        "AGENTGATE_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(auth_routes.router)
    app.include_router(benchmark_routes.router)
    app.include_router(content_routes.router)
    app.include_router(run_routes.router)
    app.include_router(admin_routes.router)
    from .routes import sandbox_routes
    app.include_router(sandbox_routes.router)

    state = {"receiver": None, "worker": None, "proxy_server": None}

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "data_dir": str(db.data_root())}

    @app.get("/api/v1/proxy/status")
    def proxy_status(user: dict = Depends(auth.require_min("member"))):
        from ..trace import llm_proxy
        return {"upstream": llm_proxy.STATE["upstream"] or "",
                "sink": llm_proxy.STATE["sink"],
                "port": db.get_int_setting("proxy_port", 8300)}

    @app.on_event("startup")
    def _startup():
        db.connect().close()             # create dirs + schema + default settings
        auth.bootstrap_owner()
        from . import banks as banks_mod
        banks_mod.backfill_requirements()   # fairness contract backfill (idempotent)
        if not start_services:
            return
        # deployment-level port overrides win over the settings table on every boot
        # (docker -e AGENTGATE_RECEIVER_PORT / AGENTGATE_PROXY_PORT; unset = settings value)
        for key, env in (("receiver_port", "AGENTGATE_RECEIVER_PORT"),
                         ("proxy_port", "AGENTGATE_PROXY_PORT")):
            if os.environ.get(env, "").strip():
                db.set_setting(key, os.environ[env].strip())
        # resident OTLP receiver: cross-machine agents export here
        from ..trace.receivers.otlp_http import OTLPHTTPReceiver
        rport = db.get_int_setting("receiver_port", 4318)
        try:
            recv = OTLPHTTPReceiver(rport)
            recv.start()
            state["receiver"] = recv
        except OSError as e:
            print("[agentgate] resident receiver failed on :%d (%s); runs will start "
                  "per-run receivers instead" % (rport, e))
        # resident llm recording proxy; upstream from settings (admin-editable at runtime),
        # seeded once from env so docker-compose/env-only deployments work out of the box
        from ..trace import llm_proxy
        if not db.get_setting("proxy_upstream", "") and os.environ.get("AGENTGATE_PROXY_UPSTREAM", ""):
            db.set_setting("proxy_upstream", os.environ["AGENTGATE_PROXY_UPSTREAM"].strip())
        if not db.get_setting("proxy_agent_url", "") and os.environ.get("AGENTGATE_PROXY_AGENT_URL", ""):
            db.set_setting("proxy_agent_url", os.environ["AGENTGATE_PROXY_AGENT_URL"].strip())
        llm_proxy.set_upstream(db.get_setting("proxy_upstream", ""))
        sink = str(db.data_root() / "llm_proxy_sink" / "current.jsonl")
        llm_proxy.set_sink(sink)
        state["proxy_server"] = _start_proxy(db.get_int_setting("proxy_port", 8300))
        # startup recovery: re-queue runs interrupted by a previous process (see db helper)
        n_requeued = db.requeue_interrupted_runs()
        if n_requeued:
            print("[agentgate] re-queued %d run(s) interrupted by restart" % n_requeued)
        # global serial worker
        from .worker import Worker
        worker = Worker(receiver=state["receiver"], poll_seconds=worker_poll,
                        proxy_sink=sink)
        worker.start()
        state["worker"] = worker
        print("[agentgate] platform ready | data: %s | receiver :%d | proxy :%d"
              % (db.data_root(), rport, db.get_int_setting("proxy_port", 8300)))

    @app.on_event("shutdown")
    def _shutdown():
        if state["worker"]:
            state["worker"].stop()
        if state["receiver"]:
            state["receiver"].stop()
        if state["proxy_server"]:
            state["proxy_server"].should_exit = True

    # frontend dist when built (W2); API routes above keep precedence over this mount
    dist = _frontend_dist()
    if dist:
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False)
        def index():
            return HTMLResponse(_STATUS_PAGE)

    return app
