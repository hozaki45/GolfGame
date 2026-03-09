"""GolfGame Dashboard API サーバー。

FastAPI による REST API + SSE エンドポイント。
パイプラインの進捗をリアルタイムストリーミングし、
大会データの CRUD を提供する。

Usage:
    uv run uvicorn api.server:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse


#-----Setup-----

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from api.pipeline_runner import PipelineManager

app = FastAPI(
    title="GolfGame Dashboard API",
    description="パイプライン実行・大会データ管理 API",
    version="1.0.0",
)

# CORS設定（開発時: Viteの5173ポートを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# パイプラインマネージャー（パイプライン用・結果収集用の2つ）
pipeline_mgr = PipelineManager(project_root=PROJECT_ROOT)
collect_mgr = PipelineManager(project_root=PROJECT_ROOT)


@app.on_event("shutdown")
async def shutdown_event():
    """サーバー終了時に実行中の子プロセスをクリーンアップ。"""
    pipeline_mgr.terminate()
    collect_mgr.terminate()


#-----SSE: Pipeline-----

@app.get("/api/pipeline/start", tags=["Pipeline"])
async def start_pipeline():
    """パイプライン実行を開始し、進捗をSSEでストリーミング。"""
    if pipeline_mgr.is_running:
        async def error_stream():
            yield {"event": "error", "data": "Pipeline is already running"}
        return EventSourceResponse(error_stream())

    pipeline_mgr.start()

    async def event_generator():
        while True:
            event = await pipeline_mgr.get_next_event()
            if event is None:
                status = "success" if pipeline_mgr.last_exit_code == 0 else "error"
                yield {"event": "complete", "data": status}
                break
            yield {"event": "log", "data": event}

    return EventSourceResponse(event_generator())


@app.get("/api/pipeline/status", tags=["Pipeline"])
async def pipeline_status():
    """現在のパイプライン状態を返す。"""
    return {
        "status": pipeline_mgr.status,
        "current_step": pipeline_mgr.current_step,
        "last_exit_code": pipeline_mgr.last_exit_code,
    }


#-----SSE: Result Collection-----

@app.get("/api/results/collect", tags=["Results"])
async def collect_results_sse(
    espn_date: str = Query(default="", description="ESPN日付 (YYYYMMDD)"),
    tournament_id: int | None = Query(default=None, description="大会DB ID"),
):
    """結果収集を開始し、進捗をSSEでストリーミング。"""
    if collect_mgr.is_running:
        async def error_stream():
            yield {"event": "error", "data": "Result collection is already running"}
        return EventSourceResponse(error_stream())

    collect_mgr.start_collect(espn_date=espn_date, tournament_id=tournament_id)

    async def event_generator():
        while True:
            event = await collect_mgr.get_next_event()
            if event is None:
                status = "success" if collect_mgr.last_exit_code == 0 else "error"
                yield {"event": "complete", "data": status}
                break
            yield {"event": "log", "data": event}

    return EventSourceResponse(event_generator())


#-----Tournament Data-----

@app.get("/api/tournaments", tags=["Data"])
async def get_tournaments():
    """全大会リストを返す。"""
    from src.database import list_tournaments
    tournaments = list_tournaments()
    return [
        {
            "id": t.id,
            "name": t.name,
            "start_date": t.start_date,
            "status": t.status,
            "num_players": t.num_players,
            "num_bookmakers": t.num_bookmakers,
            "has_results": t.has_results,
        }
        for t in tournaments
    ]


@app.get("/api/accumulation", tags=["Data"])
async def get_accumulation():
    """結果蓄積状況を返す。"""
    from src.database import get_accumulation_status
    return get_accumulation_status()


@app.get("/api/accuracy", tags=["Data"])
async def get_accuracy():
    """ML精度レポートを返す。"""
    from src.database import get_ml_accuracy
    return get_ml_accuracy()


#-----Pick'em Data-----

@app.get("/api/pickem/status", tags=["Pick'em"])
async def get_pickem_status():
    """Pick'emデータの状況を返す。"""
    from src.database import get_connection
    conn = get_connection()
    try:
        t_count = conn.execute("SELECT COUNT(*) as c FROM pickem_tournaments").fetchone()["c"]
        p_count = conn.execute("SELECT COUNT(*) as c FROM pickem_picks").fetchone()["c"]
        s_count = conn.execute("SELECT COUNT(*) as c FROM pickem_scores").fetchone()["c"]
        u_count = conn.execute("SELECT COUNT(*) as c FROM pickem_users").fetchone()["c"]

        recent = conn.execute(
            "SELECT pk, name, num_users, num_groups FROM pickem_tournaments ORDER BY pk DESC LIMIT 5"
        ).fetchall()

        return {
            "tournaments": t_count,
            "users": u_count,
            "picks": p_count,
            "scores": s_count,
            "recent": [
                {"pk": r["pk"], "name": r["name"], "num_users": r["num_users"], "num_groups": r["num_groups"]}
                for r in recent
            ],
        }
    finally:
        conn.close()


#-----Training History-----

@app.get("/api/training/history", tags=["Training"])
async def get_training_history():
    """EGS訓練履歴を返す。"""
    import json
    history_path = PROJECT_ROOT / "data" / "models" / "egs_training_history.json"
    if not history_path.exists():
        return {"entries": [], "count": 0}
    with open(history_path, encoding="utf-8") as f:
        entries = json.load(f)
    return {"entries": entries, "count": len(entries)}


@app.get("/api/training/report", tags=["Training"])
async def get_training_report():
    """訓練履歴HTMLレポートを返す。"""
    report_path = PROJECT_ROOT / "data" / "output" / "training.html"
    if report_path.exists():
        return HTMLResponse(content=report_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>No training history</h1><p>Run EGS training first.</p>",
        status_code=404,
    )


#-----Report-----

@app.get("/api/report", tags=["Report"])
async def get_report():
    """既存HTMLレポートを返す。"""
    report_path = PROJECT_ROOT / "data" / "output" / "index.html"
    if report_path.exists():
        return HTMLResponse(content=report_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>No report available</h1><p>Run the pipeline first.</p>",
        status_code=404,
    )


#-----Config (sanitized)-----

@app.get("/api/config", tags=["Data"])
async def get_config():
    """設定サマリーを返す（機密情報除外）。"""
    import yaml

    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        return {"error": "Config not found"}

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return {
        "stats_enabled": config.get("stats_source", {}).get("enabled", False),
        "course_fit_enabled": config.get("course_fit", {}).get("enabled", False),
        "ml_enabled": config.get("ml_prediction", {}).get("enabled", False),
        "ml_weights": config.get("ml_prediction", {}).get("default_weights", {}),
    }


#-----Static Files (production)-----

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", tags=["Frontend"], include_in_schema=False)
    async def serve_frontend(full_path: str):
        """React SPA: 全パスを index.html にフォールバック。"""
        file_path = FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
