"""パイプライン実行マネージャー。

run_pipeline.py を子プロセスとして実行し、stdout をリアルタイムで
キャプチャして非同期キューに送る。SSEエンドポイントがこのキューを消費する。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
from pathlib import Path
from typing import Literal


#-----Constants-----

STEP_PATTERN = re.compile(r"\[STEP (\d+)\]")
STATUS_PATTERN = re.compile(r"\[(OK|ERROR|WARN|INFO)\]")
LEVEL_MAP = {"OK": "success", "ERROR": "error", "WARN": "warning", "INFO": "info"}


#-----Pipeline Manager-----

class PipelineManager:
    """パイプライン実行を管理し、ログイベントをSSEに中継する。

    subprocess.Popenで `uv run python run_pipeline.py` を実行し、
    stdoutを行単位でパースしてasyncio.Queueに送信する。
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self._status: Literal["idle", "running", "completed", "error"] = "idle"
        self._current_step: int = 0
        self._last_exit_code: int | None = None
        self._event_queue: asyncio.Queue[str | None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._process: subprocess.Popen | None = None

    @property
    def status(self) -> str:
        return self._status

    @property
    def current_step(self) -> int:
        return self._current_step

    @property
    def last_exit_code(self) -> int | None:
        return self._last_exit_code

    @property
    def is_running(self) -> bool:
        return self._status == "running"

    def terminate(self) -> None:
        """実行中の子プロセスを強制終了する。"""
        proc = self._process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._process = None
        if self._status == "running":
            self._status = "idle"

    def start(self) -> None:
        """パイプラインをバックグラウンドスレッドで開始。"""
        self._status = "running"
        self._current_step = 0
        self._last_exit_code = None
        self._event_queue = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        thread = threading.Thread(target=self._run_subprocess, daemon=True)
        thread.start()

    def start_collect(self, espn_date: str = "", tournament_id: int | None = None) -> None:
        """結果収集をバックグラウンドスレッドで開始。"""
        self._status = "running"
        self._current_step = 0
        self._last_exit_code = None
        self._event_queue = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        thread = threading.Thread(
            target=self._run_collect_subprocess,
            args=(espn_date, tournament_id),
            daemon=True,
        )
        thread.start()

    def _run_subprocess(self) -> None:
        """子プロセスでパイプラインを実行し、stdout を行単位でキューに送る。"""
        try:
            proc = subprocess.Popen(
                ["uv", "run", "python", "run_pipeline.py"],
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._process = proc

            for line in proc.stdout:
                line = line.rstrip("\n\r")
                if not line:
                    continue
                self._parse_and_enqueue(line)

            proc.wait()
            self._last_exit_code = proc.returncode
            self._status = "completed" if proc.returncode == 0 else "error"

        except Exception as e:
            self._enqueue_event(f"[FATAL] Pipeline crashed: {e}", 0, "error")
            self._status = "error"
            self._last_exit_code = -1

        finally:
            self._process = None
            # None = ストリーム終了のセンチネル
            asyncio.run_coroutine_threadsafe(
                self._event_queue.put(None),
                self._loop,
            )

    def _run_collect_subprocess(self, espn_date: str, tournament_id: int | None) -> None:
        """結果収集を子プロセスで実行。"""
        try:
            cmd = ["uv", "run", "python", "-m", "src.result_collector"]
            if espn_date:
                cmd.extend(["--date", espn_date])
            if tournament_id is not None:
                cmd.extend(["--tournament-id", str(tournament_id)])

            proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._process = proc

            for line in proc.stdout:
                line = line.rstrip("\n\r")
                if not line:
                    continue
                self._parse_and_enqueue(line)

            proc.wait()
            self._last_exit_code = proc.returncode
            self._status = "completed" if proc.returncode == 0 else "error"

        except Exception as e:
            self._enqueue_event(f"[FATAL] Result collection crashed: {e}", 0, "error")
            self._status = "error"
            self._last_exit_code = -1

        finally:
            self._process = None
            asyncio.run_coroutine_threadsafe(
                self._event_queue.put(None),
                self._loop,
            )

    def _parse_and_enqueue(self, line: str) -> None:
        """stdout行をパースして構造化イベントとしてキューに送信。"""
        step = self._current_step
        level = "info"

        step_match = STEP_PATTERN.search(line)
        if step_match:
            step = int(step_match.group(1))
            self._current_step = step

        status_match = STATUS_PATTERN.search(line)
        if status_match:
            level = LEVEL_MAP.get(status_match.group(1), "info")

        self._enqueue_event(line, step, level)

    def _enqueue_event(self, line: str, step: int, level: str) -> None:
        """イベントをJSON化してキューに送信。"""
        event_data = json.dumps({
            "line": line,
            "step": step,
            "level": level,
        }, ensure_ascii=False)

        asyncio.run_coroutine_threadsafe(
            self._event_queue.put(event_data),
            self._loop,
        )

    async def get_next_event(self) -> str | None:
        """次のイベントを非同期で取得。None はストリーム終了。"""
        if self._event_queue is None:
            return None
        return await self._event_queue.get()
