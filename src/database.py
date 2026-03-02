"""Local SQLite Database - Accumulate tournament data over time.

Stores bookmaker odds, group assignments, and actual results per tournament.
Enables historical analysis of bookmaker prediction accuracy.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.espn_scraper import ESPNScraper, TournamentInfo
from src.group_analyzer import GroupAnalysisResult, GroupPlayer


DB_PATH = Path("data/golfgame.db")


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating the DB and tables if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            espn_event_id TEXT,
            picks_pk TEXT,
            start_date TEXT,
            end_date TEXT,
            field_size INTEGER,
            status TEXT DEFAULT 'scheduled',
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_tournaments_name_date
            ON tournaments(name, start_date);

        CREATE TABLE IF NOT EXISTS group_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            wgr TEXT,
            fedex_rank TEXT,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
        );

        CREATE INDEX IF NOT EXISTS idx_group_players_tournament
            ON group_players(tournament_id);

        CREATE TABLE IF NOT EXISTS odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            bookmaker TEXT NOT NULL,
            odds_value INTEGER NOT NULL,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
        );

        CREATE INDEX IF NOT EXISTS idx_odds_tournament
            ON odds(tournament_id);

        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            espn_position INTEGER,
            score TEXT,
            group_id INTEGER,
            group_rank INTEGER,
            rounds_played INTEGER,
            espn_status TEXT DEFAULT '',
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
        );

        CREATE INDEX IF NOT EXISTS idx_results_tournament
            ON results(tournament_id);

        CREATE TABLE IF NOT EXISTS player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,

            -- Strokes Gained
            sg_approach REAL,
            sg_off_tee REAL,
            sg_tee_to_green REAL,
            sg_total REAL,
            sg_putting REAL,
            sg_around_green REAL,

            -- Traditional
            gir_pct REAL,
            driving_distance REAL,
            driving_accuracy_pct REAL,
            scrambling_pct REAL,
            scoring_average REAL,

            -- Form & Prediction
            recent_form_rank INTEGER,
            prediction_score REAL,
            confidence TEXT,

            -- Metadata
            data_source TEXT,
            fetched_at TEXT NOT NULL,

            FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
        );

        CREATE INDEX IF NOT EXISTS idx_player_stats_tournament
            ON player_stats(tournament_id);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_player_stats_unique
            ON player_stats(tournament_id, player_name);

        CREATE TABLE IF NOT EXISTS ml_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            ml_score REAL,
            odds_component REAL,
            stats_component REAL,
            fit_component REAL,
            crowd_component REAL DEFAULT 0,
            ml_rank_in_group INTEGER,
            model_version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
        );

        CREATE INDEX IF NOT EXISTS idx_ml_predictions_tournament
            ON ml_predictions(tournament_id);

        -- ========== Pick'em 履歴データ ==========

        CREATE TABLE IF NOT EXISTS pickem_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            first_seen_pk INTEGER,
            last_seen_pk INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pickem_tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pk INTEGER NOT NULL UNIQUE,
            name TEXT,
            season TEXT,
            num_groups INTEGER,
            num_users INTEGER,
            winner_username TEXT,
            prize_amount INTEGER DEFAULT 0,
            scraped_at TEXT,
            golfgame_tournament_id INTEGER,
            FOREIGN KEY (golfgame_tournament_id) REFERENCES tournaments(id)
        );

        CREATE TABLE IF NOT EXISTS pickem_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pickem_tournament_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            total_score INTEGER NOT NULL,
            bonus INTEGER DEFAULT 0,
            FOREIGN KEY (pickem_tournament_id) REFERENCES pickem_tournaments(id),
            UNIQUE(pickem_tournament_id, username)
        );

        CREATE TABLE IF NOT EXISTS pickem_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pickem_tournament_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            group_num INTEGER NOT NULL,
            picked_player TEXT NOT NULL,
            picked_player_normalized TEXT,
            FOREIGN KEY (pickem_tournament_id) REFERENCES pickem_tournaments(id),
            UNIQUE(pickem_tournament_id, username, group_num)
        );

        CREATE INDEX IF NOT EXISTS idx_pickem_picks_tournament
            ON pickem_picks(pickem_tournament_id);

        CREATE INDEX IF NOT EXISTS idx_pickem_scores_tournament
            ON pickem_scores(pickem_tournament_id);

        -- グループ構成（CSV由来: 全選手 × グループ割り当て + 統計）
        CREATE TABLE IF NOT EXISTS pickem_field_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pickem_tournament_id INTEGER NOT NULL,
            espn_id TEXT,
            player_name TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            current_wgr INTEGER,
            sow_wgr INTEGER,
            soy_wgr INTEGER,
            prior_year_finish TEXT,
            handicap INTEGER,
            fedex_rank INTEGER,
            fedex_points INTEGER,
            season_played INTEGER,
            season_won INTEGER,
            season_top10 INTEGER,
            season_top29 INTEGER,
            season_top49 INTEGER,
            season_over50 INTEGER,
            season_cut INTEGER,
            tournament_history TEXT,
            FOREIGN KEY (pickem_tournament_id) REFERENCES pickem_tournaments(id),
            UNIQUE(pickem_tournament_id, espn_id)
        );

        CREATE INDEX IF NOT EXISTS idx_pickem_field_tournament
            ON pickem_field_players(pickem_tournament_id);

        CREATE INDEX IF NOT EXISTS idx_pickem_field_group
            ON pickem_field_players(pickem_tournament_id, group_id);

        -- ========== バックテスト結果 ==========

        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            pk INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            ranking_signal REAL,
            stats_signal REAL,
            fit_signal REAL,
            crowd_signal REAL,
            affinity_signal REAL,
            pga_position INTEGER,
            is_group_winner INTEGER,
            is_group_top2 INTEGER,
            computed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_backtest_results_run
            ON backtest_results(run_id);

        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            pk_min INTEGER,
            pk_max INTEGER,
            total_tournaments INTEGER,
            total_groups INTEGER,
            total_observations INTEGER,
            optimal_w_ranking REAL,
            optimal_w_stats REAL,
            optimal_w_fit REAL,
            optimal_w_crowd REAL,
            optimal_w_affinity REAL,
            accuracy_winner REAL,
            accuracy_top2 REAL,
            method TEXT,
            computed_at TEXT NOT NULL
        );

        -- ========== オッズスナップショット蓄積 ==========

        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_name TEXT NOT NULL,
            picks_pk TEXT,
            player_name TEXT NOT NULL,
            group_id INTEGER,
            bookmaker TEXT NOT NULL,
            odds_value INTEGER NOT NULL,
            implied_probability REAL,
            snapshot_at TEXT NOT NULL,
            tournament_start_date TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_odds_snapshots_tournament
            ON odds_snapshots(tournament_name, snapshot_at);

        CREATE INDEX IF NOT EXISTS idx_odds_snapshots_player
            ON odds_snapshots(tournament_name, player_name);
    """)
    conn.commit()

    # マイグレーション: 既存DBに新カラムを追加
    _migrate_columns(conn)


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """既存DBに新カラムを追加する（なければ）。"""
    migrations = [
        ("results", "rounds_played", "INTEGER"),
        ("results", "espn_status", "TEXT DEFAULT ''"),
        ("tournaments", "field_size", "INTEGER"),
        ("ml_predictions", "egs", "REAL"),
        ("ml_predictions", "egs_rank_in_group", "INTEGER"),
        ("ml_predictions", "p_cut", "REAL"),
        ("ml_predictions", "handicap", "INTEGER"),
    ]
    for table, col, col_type in migrations:
        try:
            conn.execute(f"SELECT {col} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()


# ── Save Operations ──


def save_odds_snapshot(
    tournament_name: str,
    picks_pk: str,
    players: list[dict],
    snapshot_at: str | None = None,
    tournament_start_date: str | None = None,
) -> int:
    """オッズスナップショットを蓄積保存（追記のみ、DELETEなし）。

    Args:
        tournament_name: 大会名
        picks_pk: Pick'emサイトのPK
        players: [{name, group_id, odds_by_book, implied_probability}]
        snapshot_at: ISO timestamp（Noneなら現在時刻）
        tournament_start_date: 大会開始日 (YYYY-MM-DD)。
            大会開始日の日本時間正午以前のスナップショットのみ分析対象とする。

    Returns:
        保存したレコード数
    """
    if snapshot_at is None:
        snapshot_at = datetime.now().isoformat()

    conn = get_connection()
    count = 0
    try:
        for p in players:
            odds_by_book = p.get("odds_by_book", {})
            implied_prob = p.get("implied_probability")
            for book, odds_val in odds_by_book.items():
                # 重複チェック
                existing = conn.execute(
                    "SELECT id FROM odds_snapshots "
                    "WHERE tournament_name=? AND player_name=? AND bookmaker=? AND snapshot_at=?",
                    (tournament_name, p["name"], book, snapshot_at),
                ).fetchone()
                if existing:
                    continue
                conn.execute(
                    "INSERT INTO odds_snapshots "
                    "(tournament_name, picks_pk, player_name, group_id, bookmaker, "
                    "odds_value, implied_probability, snapshot_at, tournament_start_date) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (tournament_name, picks_pk, p["name"], p.get("group_id"),
                     book, odds_val, implied_prob, snapshot_at, tournament_start_date),
                )
                count += 1
        conn.commit()
        if count > 0:
            print(f"[DB] Archived odds snapshot: {count} entries ({tournament_name})")
        return count
    finally:
        conn.close()


def ingest_raw_odds_json(json_dir: str = "data/raw") -> int:
    """data/raw/odds_*.json をodds_snapshotsテーブルに一括投入。

    Returns:
        投入したレコード数
    """
    import glob
    import json
    import re

    pattern = str(Path(json_dir) / "odds_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"[INFO] No odds JSON files found in {json_dir}")
        return 0

    total = 0
    seen_timestamps: set[str] = set()

    for filepath in files:
        # ファイル名からタイムスタンプ抽出
        fname = Path(filepath).stem
        m = re.match(r"odds_(\d{8})_(\d{6})", fname)
        if not m:
            continue
        date_str = m.group(1)
        time_str = m.group(2)
        snapshot_at = (
            f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}T"
            f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
        )

        # 同一タイムスタンプの重複スキップ（秒単位の重複ファイル対策）
        if snapshot_at in seen_timestamps:
            continue
        seen_timestamps.add(snapshot_at)

        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        tournament_name = data.get("tournament_name", "")
        if not tournament_name:
            continue

        players = []
        for p in data.get("players", []):
            players.append({
                "name": p.get("name", ""),
                "odds_by_book": p.get("odds_by_book", {}),
                "implied_probability": p.get("implied_probability"),
            })

        if players:
            count = save_odds_snapshot(tournament_name, "", players, snapshot_at)
            total += count

    print(f"[INFO] Ingested {total} odds entries from {len(seen_timestamps)} unique snapshots")
    return total


def save_tournament_odds(
    analysis: GroupAnalysisResult,
    espn_event_id: str = "",
    picks_pk: str = "",
    start_date: str = "",
    end_date: str = "",
) -> int:
    """Save tournament group assignments and bookmaker odds to the database.

    Call this BEFORE the tournament starts (when odds are available).

    Args:
        analysis: Group analysis result with bookmaker odds.
        espn_event_id: ESPN event ID.
        picks_pk: Picks site tournament PK.
        start_date: Tournament start date.
        end_date: Tournament end date.

    Returns:
        Tournament database ID.
    """
    # スナップショット蓄積（追記のみ、DELETEなし）
    snapshot_players = []
    for gid, players in analysis.groups.items():
        for p in players:
            snapshot_players.append({
                "name": p.name,
                "group_id": gid,
                "odds_by_book": p.odds_by_book,
                "implied_probability": p.implied_prob,
            })
    if snapshot_players:
        save_odds_snapshot(
            analysis.tournament_name, picks_pk, snapshot_players,
            tournament_start_date=start_date or None,
        )

    conn = get_connection()
    try:
        # Upsert tournament
        existing = conn.execute(
            "SELECT id FROM tournaments WHERE name = ? AND start_date = ?",
            (analysis.tournament_name, start_date),
        ).fetchone()

        if existing:
            tid = existing["id"]
            conn.execute(
                "UPDATE tournaments SET espn_event_id=?, picks_pk=?, end_date=?, status='odds_saved' WHERE id=?",
                (espn_event_id, picks_pk, end_date, tid),
            )
            # Clear old data for re-save
            conn.execute("DELETE FROM group_players WHERE tournament_id=?", (tid,))
            conn.execute("DELETE FROM odds WHERE tournament_id=?", (tid,))
            print(f"[DB] Updated existing tournament (id={tid})")
        else:
            cur = conn.execute(
                "INSERT INTO tournaments (name, espn_event_id, picks_pk, start_date, end_date, status, created_at) VALUES (?,?,?,?,?,?,?)",
                (analysis.tournament_name, espn_event_id, picks_pk, start_date, end_date, "odds_saved", datetime.now().isoformat()),
            )
            tid = cur.lastrowid
            print(f"[DB] Created new tournament '{analysis.tournament_name}' (id={tid})")

        # Save group players
        for gid, players in analysis.groups.items():
            for p in players:
                conn.execute(
                    "INSERT INTO group_players (tournament_id, group_id, player_name, wgr, fedex_rank) VALUES (?,?,?,?,?)",
                    (tid, gid, p.name, p.wgr, p.fedex_rank),
                )

                # Save odds per bookmaker
                for book, odds_val in p.odds_by_book.items():
                    conn.execute(
                        "INSERT INTO odds (tournament_id, group_id, player_name, bookmaker, odds_value) VALUES (?,?,?,?,?)",
                        (tid, gid, p.name, book, odds_val),
                    )

        conn.commit()
        total_odds = sum(len(p.odds_by_book) for players in analysis.groups.values() for p in players)
        total_players = sum(len(players) for players in analysis.groups.values())
        print(f"[DB] Saved {total_players} players, {total_odds} odds entries for {len(analysis.bookmakers)} bookmakers")
        return tid

    finally:
        conn.close()


def save_ml_predictions(
    tournament_id: int,
    groups: dict,
    ml_result: dict,
) -> None:
    """ML予測結果をデータベースに保存。

    大会前の予測スコアを保存し、大会後に実績と比較可能にする。

    Args:
        tournament_id: トーナメントDB ID
        groups: {group_id: [GroupPlayer, ...]}
        ml_result: run_ml_prediction()の戻り値
    """
    predictions = ml_result.get("predictions", {})
    model_version = ml_result.get("model_version", "unknown")

    if not predictions:
        print("[DB] No ML predictions to save")
        return

    conn = get_connection()
    try:
        # 既存予測を削除（再保存対応）
        conn.execute("DELETE FROM ml_predictions WHERE tournament_id=?", (tournament_id,))

        saved = 0
        for gid, players in groups.items():
            for p in players:
                pred = predictions.get(p.name)
                if not pred:
                    continue
                conn.execute(
                    """INSERT INTO ml_predictions
                       (tournament_id, group_id, player_name,
                        ml_score, odds_component, stats_component, fit_component,
                        crowd_component, ml_rank_in_group, model_version,
                        egs, egs_rank_in_group, p_cut, handicap)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tournament_id, gid, p.name,
                     pred.ml_score, pred.odds_component,
                     pred.stats_component, pred.fit_component,
                     pred.crowd_component,
                     pred.ml_rank_in_group, model_version,
                     getattr(p, "egs", None),
                     getattr(p, "egs_rank_in_group", None),
                     getattr(p, "p_cut", None),
                     getattr(p, "handicap", None)),
                )
                saved += 1

        conn.commit()
        print(f"[DB] Saved {saved} ML predictions (model={model_version})")

    finally:
        conn.close()


def save_tournament_stats(
    tournament_id: int,
    stats: list,  # List of PlayerStats objects
) -> None:
    """Save player statistics to database.

    Args:
        tournament_id: Tournament database ID
        stats: List of PlayerStats objects from stats_models
    """
    conn = get_connection()
    try:
        # Clear existing stats for this tournament
        conn.execute("DELETE FROM player_stats WHERE tournament_id=?", (tournament_id,))

        # Insert new stats
        for player_stat in stats:
            conn.execute(
                """
                INSERT INTO player_stats (
                    tournament_id, player_name,
                    sg_approach, sg_off_tee, sg_tee_to_green, sg_total, sg_putting, sg_around_green,
                    gir_pct, driving_distance, driving_accuracy_pct, scrambling_pct, scoring_average,
                    recent_form_rank, prediction_score, confidence,
                    data_source, fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tournament_id,
                    player_stat.name,
                    player_stat.sg_approach,
                    player_stat.sg_off_tee,
                    player_stat.sg_tee_to_green,
                    player_stat.sg_total,
                    player_stat.sg_putting,
                    player_stat.sg_around_green,
                    player_stat.greens_in_regulation_pct,
                    player_stat.driving_distance,
                    player_stat.driving_accuracy_pct,
                    player_stat.scrambling_pct,
                    player_stat.scoring_average,
                    player_stat.recent_form_rank,
                    player_stat.prediction_score,
                    player_stat.confidence,
                    player_stat.data_source,
                    player_stat.fetched_at,
                ),
            )

        conn.commit()
        with_data = sum(1 for s in stats if hasattr(s, 'has_sufficient_data') and s.has_sufficient_data())
        print(f"[DB] Saved {len(stats)} player stats ({with_data} with sufficient data)")

    finally:
        conn.close()


def get_player_stats(tournament_id: int) -> list:
    """Retrieve cached player statistics from database.

    Args:
        tournament_id: Tournament database ID

    Returns:
        List of PlayerStats objects (empty list if not cached)
    """
    from .stats_models import PlayerStats

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM player_stats WHERE tournament_id = ?",
            (tournament_id,)
        ).fetchall()

        if not rows:
            return []

        results = []
        for row in rows:
            stats = PlayerStats(
                name=row["player_name"],
                tournament_id=tournament_id,
                sg_approach=row["sg_approach"],
                sg_off_tee=row["sg_off_tee"],
                sg_tee_to_green=row["sg_tee_to_green"],
                sg_total=row["sg_total"],
                sg_putting=row["sg_putting"],
                sg_around_green=row["sg_around_green"],
                greens_in_regulation_pct=row["gir_pct"],
                driving_distance=row["driving_distance"],
                driving_accuracy_pct=row["driving_accuracy_pct"],
                scrambling_pct=row["scrambling_pct"],
                scoring_average=row["scoring_average"],
                recent_form_rank=row["recent_form_rank"],
                prediction_score=row["prediction_score"],
                confidence=row["confidence"],
                data_source=row["data_source"],
                fetched_at=row["fetched_at"],
            )
            results.append(stats)

        print(f"[DB] Retrieved {len(results)} cached player stats")
        return results

    finally:
        conn.close()


def save_tournament_results(
    tournament_id: int | None = None,
    tournament_name: str = "",
    espn_date: str = "",
) -> bool:
    """Fetch and save actual tournament results from ESPN.

    Call this AFTER the tournament ends.

    Args:
        tournament_id: Database tournament ID. If None, looks up by name.
        tournament_name: Tournament name for lookup.
        espn_date: Date string (YYYYMMDD) for ESPN API lookup.

    Returns:
        True if results were saved successfully.
    """
    conn = get_connection()
    try:
        # Find tournament in DB
        if tournament_id:
            row = conn.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,)).fetchone()
        elif tournament_name:
            row = conn.execute(
                "SELECT * FROM tournaments WHERE name LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{tournament_name}%",),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM tournaments ORDER BY id DESC LIMIT 1").fetchone()

        if not row:
            print("[DB] No matching tournament found in database")
            return False

        tid = row["id"]
        print(f"[DB] Saving results for '{row['name']}' (id={tid})")

        # Fetch ESPN results
        espn = ESPNScraper()
        if espn_date:
            url = f"{espn.base_url}/scoreboard?dates={espn_date}"
        else:
            url = f"{espn.base_url}/scoreboard"

        import requests
        print(f"[INFO] Fetching ESPN results...")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        tournament = espn.parse_tournament(resp.json())

        if not tournament:
            print("[ERROR] No tournament data from ESPN")
            return False

        # Check if tournament has actual scores
        has_scores = any(p.score != "E" for p in tournament.players)
        if not has_scores:
            print("[WARN] Tournament has not finished yet - scores are still 'E'")
            return False

        print(f"[INFO] Got results for {len(tournament.players)} players")

        # Get group assignments from DB
        group_players = conn.execute(
            "SELECT player_name, group_id FROM group_players WHERE tournament_id=?",
            (tid,),
        ).fetchall()

        player_groups: dict[str, int] = {}
        for gp in group_players:
            player_groups[gp["player_name"].lower().strip()] = gp["group_id"]

        # Clear old results
        conn.execute("DELETE FROM results WHERE tournament_id=?", (tid,))

        # Match ESPN results to groups
        from fuzzywuzzy import fuzz

        saved_count = 0
        for ep in tournament.players:
            # Find group for this player
            ep_lower = ep.name.lower().strip()
            group_id = player_groups.get(ep_lower)

            if not group_id:
                # Fuzzy match
                for db_name, gid in player_groups.items():
                    score = max(fuzz.ratio(ep_lower, db_name), fuzz.token_sort_ratio(ep_lower, db_name))
                    if score >= 80:
                        group_id = gid
                        break

            rounds_played = len(ep.round_scores) if ep.round_scores else None
            espn_status = getattr(ep, "status", "") or ""

            conn.execute(
                "INSERT INTO results (tournament_id, player_name, espn_position, score, group_id, rounds_played, espn_status) VALUES (?,?,?,?,?,?,?)",
                (tid, ep.name, ep.position, ep.score, group_id, rounds_played, espn_status),
            )
            saved_count += 1

        # Calculate group_rank within each group
        groups_in_db = conn.execute(
            "SELECT DISTINCT group_id FROM results WHERE tournament_id=? AND group_id IS NOT NULL",
            (tid,),
        ).fetchall()

        for g in groups_in_db:
            gid = g["group_id"]
            players_in_group = conn.execute(
                "SELECT id, player_name, espn_position FROM results WHERE tournament_id=? AND group_id=? ORDER BY espn_position",
                (tid, gid),
            ).fetchall()

            for rank, p in enumerate(players_in_group, 1):
                conn.execute("UPDATE results SET group_rank=? WHERE id=?", (rank, p["id"]))

        # Update tournament status and field size
        conn.execute(
            "UPDATE tournaments SET status='results_saved', field_size=? WHERE id=?",
            (len(tournament.players), tid),
        )
        conn.commit()

        print(f"[DB] Saved {saved_count} results, ranked {len(list(groups_in_db))} groups")
        return True

    finally:
        conn.close()


# ── Query Operations ──


def get_pending_result_tournaments() -> list[dict]:
    """終了済みだが結果未収集の大会一覧を返す。

    status='odds_saved' かつ end_date < 今日 の大会をend_date昇順で返す。

    Returns:
        [{"id": int, "name": str, "end_date": str, "espn_event_id": str}, ...]
    """
    from datetime import date

    conn = get_connection()
    try:
        today = date.today().isoformat()
        rows = conn.execute(
            """SELECT id, name, end_date, espn_event_id
               FROM tournaments
               WHERE status = 'odds_saved'
                 AND end_date IS NOT NULL
                 AND end_date != ''
                 AND end_date < ?
               ORDER BY end_date ASC""",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@dataclass
class TournamentSummary:
    """Summary of a tournament in the database."""
    id: int
    name: str
    start_date: str
    status: str
    num_players: int
    num_bookmakers: int
    has_results: bool


def list_tournaments() -> list[TournamentSummary]:
    """List all tournaments in the database."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT t.id, t.name, t.start_date, t.status,
                   COUNT(DISTINCT gp.player_name) as num_players,
                   COUNT(DISTINCT o.bookmaker) as num_bookmakers,
                   COUNT(DISTINCT r.id) > 0 as has_results
            FROM tournaments t
            LEFT JOIN group_players gp ON gp.tournament_id = t.id
            LEFT JOIN odds o ON o.tournament_id = t.id
            LEFT JOIN results r ON r.tournament_id = t.id
            GROUP BY t.id
            ORDER BY t.start_date DESC
        """).fetchall()

        return [
            TournamentSummary(
                id=r["id"], name=r["name"], start_date=r["start_date"] or "",
                status=r["status"], num_players=r["num_players"],
                num_bookmakers=r["num_bookmakers"], has_results=bool(r["has_results"]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def get_historical_scores(tournament_ids: list[int] | None = None) -> dict[str, dict]:
    """Calculate bookmaker accuracy scores across multiple tournaments.

    Args:
        tournament_ids: Specific tournament IDs to include. None = all with results.

    Returns:
        Dict mapping bookmaker name to scoring data:
        {
            "DraftKings": {
                "total_match_points": 45,
                "total_top_pick_score": 22,
                "tournaments_scored": 3,
                "per_tournament": { tid: { "match_points": 15, "top_pick_score": 8 } }
            }
        }
    """
    conn = get_connection()
    try:
        # Get tournaments with results
        if tournament_ids:
            placeholders = ",".join("?" * len(tournament_ids))
            tournaments = conn.execute(
                f"SELECT id, name FROM tournaments WHERE id IN ({placeholders}) AND status='results_saved'",
                tournament_ids,
            ).fetchall()
        else:
            tournaments = conn.execute(
                "SELECT id, name FROM tournaments WHERE status='results_saved'"
            ).fetchall()

        if not tournaments:
            print("[DB] No tournaments with results found")
            return {}

        from fuzzywuzzy import fuzz

        bookmaker_scores: dict[str, dict] = {}

        for t in tournaments:
            tid = t["id"]
            t_name = t["name"]

            # Get all bookmakers for this tournament
            bookmakers = conn.execute(
                "SELECT DISTINCT bookmaker FROM odds WHERE tournament_id=?", (tid,)
            ).fetchall()

            # Get all groups
            groups = conn.execute(
                "SELECT DISTINCT group_id FROM group_players WHERE tournament_id=?", (tid,)
            ).fetchall()

            for bm_row in bookmakers:
                book = bm_row["bookmaker"]
                if book not in bookmaker_scores:
                    bookmaker_scores[book] = {
                        "total_match_points": 0,
                        "total_top_pick_score": 0,
                        "tournaments_scored": 0,
                        "per_tournament": {},
                    }

                match_points = 0
                top_pick_score = 0
                groups_scored = 0

                for g in groups:
                    gid = g["group_id"]

                    # Bookmaker's predicted ranking (by odds, ascending)
                    predicted = conn.execute(
                        "SELECT player_name, odds_value FROM odds WHERE tournament_id=? AND group_id=? AND bookmaker=? ORDER BY odds_value ASC",
                        (tid, gid, book),
                    ).fetchall()

                    # Actual ranking within group
                    actual = conn.execute(
                        "SELECT player_name, group_rank FROM results WHERE tournament_id=? AND group_id=? ORDER BY group_rank ASC",
                        (tid, gid),
                    ).fetchall()

                    if not predicted or not actual:
                        continue

                    groups_scored += 1
                    predicted_names = [r["player_name"] for r in predicted]
                    actual_names = [r["player_name"] for r in actual]

                    # Position match scoring
                    max_check = min(len(predicted_names), len(actual_names))
                    for pos in range(max_check):
                        p_lower = predicted_names[pos].lower().strip()
                        a_lower = actual_names[pos].lower().strip()
                        if p_lower == a_lower or max(fuzz.ratio(p_lower, a_lower), fuzz.token_sort_ratio(p_lower, a_lower)) >= 80:
                            match_points += (pos + 1)

                    # Top pick scoring
                    top_pick = predicted_names[0].lower().strip()
                    actual_rank = len(actual_names)  # Default worst
                    for i, a_name in enumerate(actual_names):
                        a_lower = a_name.lower().strip()
                        if top_pick == a_lower or max(fuzz.ratio(top_pick, a_lower), fuzz.token_sort_ratio(top_pick, a_lower)) >= 80:
                            actual_rank = i + 1
                            break
                    top_pick_score += actual_rank

                if groups_scored > 0:
                    bookmaker_scores[book]["total_match_points"] += match_points
                    bookmaker_scores[book]["total_top_pick_score"] += top_pick_score
                    bookmaker_scores[book]["tournaments_scored"] += 1
                    bookmaker_scores[book]["per_tournament"][tid] = {
                        "name": t_name,
                        "match_points": match_points,
                        "top_pick_score": top_pick_score,
                        "groups_scored": groups_scored,
                    }

        return bookmaker_scores

    finally:
        conn.close()


def get_ml_accuracy(tournament_id: int | None = None) -> dict:
    """ML予測とESPN実績を比較して精度を算出。

    Args:
        tournament_id: 特定の大会ID（Noneなら全大会）

    Returns:
        精度レポートデータ
    """
    conn = get_connection()
    try:
        if tournament_id:
            where = "WHERE ml.tournament_id = ?"
            params = (tournament_id,)
        else:
            where = ""
            params = ()

        rows = conn.execute(f"""
            SELECT
                ml.tournament_id,
                t.name as tournament_name,
                ml.group_id,
                ml.player_name,
                ml.ml_score,
                ml.ml_rank_in_group,
                ml.odds_component,
                ml.stats_component,
                ml.fit_component,
                ml.model_version,
                r.espn_position,
                r.group_rank
            FROM ml_predictions ml
            JOIN tournaments t ON t.id = ml.tournament_id
            LEFT JOIN results r
                ON r.tournament_id = ml.tournament_id
                AND r.group_id = ml.group_id
                AND LOWER(TRIM(r.player_name)) = LOWER(TRIM(ml.player_name))
            {where}
            ORDER BY ml.tournament_id, ml.group_id, ml.ml_rank_in_group
        """, params).fetchall()

        if not rows:
            return {"tournaments": [], "summary": None}

        # 大会ごとに集計
        from collections import defaultdict
        from fuzzywuzzy import fuzz

        tournaments_data: dict[int, dict] = {}
        for row in rows:
            tid = row["tournament_id"]
            if tid not in tournaments_data:
                tournaments_data[tid] = {
                    "name": row["tournament_name"],
                    "groups": defaultdict(list),
                    "model_version": row["model_version"],
                }
            tournaments_data[tid]["groups"][row["group_id"]].append(dict(row))

        # fuzzy matchも試みて結果を紐付け
        for tid, tdata in tournaments_data.items():
            all_results = conn.execute(
                "SELECT player_name, group_id, group_rank, espn_position FROM results WHERE tournament_id=?",
                (tid,)
            ).fetchall()

            result_lookup = {}
            for r in all_results:
                if r["group_id"]:
                    key = (r["group_id"], r["player_name"].lower().strip())
                    result_lookup[key] = {"group_rank": r["group_rank"], "espn_position": r["espn_position"]}

            for gid, players in tdata["groups"].items():
                for p in players:
                    if p["group_rank"] is None:
                        # fuzzy match
                        p_lower = p["player_name"].lower().strip()
                        for (rg, rn), rdata in result_lookup.items():
                            if rg == gid:
                                score = max(fuzz.ratio(p_lower, rn), fuzz.token_sort_ratio(p_lower, rn))
                                if score >= 80:
                                    p["group_rank"] = rdata["group_rank"]
                                    p["espn_position"] = rdata["espn_position"]
                                    break

        # 精度計算
        results_list = []
        total_groups = 0
        ml_correct_1st = 0
        ml_top2_correct = 0
        odds_correct_1st = 0

        for tid, tdata in tournaments_data.items():
            t_correct_1st = 0
            t_groups = 0
            t_odds_correct_1st = 0

            for gid, players in tdata["groups"].items():
                has_results = any(p["group_rank"] is not None for p in players)
                if not has_results:
                    continue

                t_groups += 1
                total_groups += 1

                # ML 1位の実績
                ml_first = [p for p in players if p["ml_rank_in_group"] == 1]
                if ml_first and ml_first[0]["group_rank"] == 1:
                    ml_correct_1st += 1
                    t_correct_1st += 1
                if ml_first and ml_first[0]["group_rank"] is not None and ml_first[0]["group_rank"] <= 2:
                    ml_top2_correct += 1

                # オッズ1位（odds_component最高）の実績
                odds_sorted = sorted(players, key=lambda x: x["odds_component"], reverse=True)
                if odds_sorted and odds_sorted[0]["group_rank"] == 1:
                    odds_correct_1st += 1
                    t_odds_correct_1st += 1

            results_list.append({
                "tournament_id": tid,
                "name": tdata["name"],
                "groups": t_groups,
                "ml_correct_1st": t_correct_1st,
                "odds_correct_1st": t_odds_correct_1st,
                "model_version": tdata["model_version"],
            })

        summary = {
            "total_groups": total_groups,
            "ml_correct_1st": ml_correct_1st,
            "ml_top2_correct": ml_top2_correct,
            "odds_correct_1st": odds_correct_1st,
            "ml_accuracy_1st": ml_correct_1st / total_groups if total_groups else 0,
            "odds_accuracy_1st": odds_correct_1st / total_groups if total_groups else 0,
            "phase2_ready": total_groups >= 50,
            "phase2_progress": f"{total_groups}/50",
        }

        return {
            "tournaments": results_list,
            "summary": summary,
        }

    finally:
        conn.close()


def get_accumulation_status() -> dict:
    """結果蓄積状況のサマリーを返す。

    Returns:
        蓄積状況データ
    """
    conn = get_connection()
    try:
        # 大会数
        total = conn.execute("SELECT COUNT(*) as c FROM tournaments").fetchone()["c"]
        with_results = conn.execute(
            "SELECT COUNT(*) as c FROM tournaments WHERE status='results_saved'"
        ).fetchone()["c"]
        with_ml = conn.execute(
            "SELECT COUNT(DISTINCT tournament_id) as c FROM ml_predictions"
        ).fetchone()["c"]

        # グループ結果数
        total_group_results = conn.execute("""
            SELECT COUNT(DISTINCT r.tournament_id || '-' || r.group_id) as c
            FROM results r
            WHERE r.group_id IS NOT NULL AND r.group_rank IS NOT NULL
        """).fetchone()["c"]

        # ML予測付き結果数
        ml_with_results = conn.execute("""
            SELECT COUNT(DISTINCT ml.tournament_id || '-' || ml.group_id) as c
            FROM ml_predictions ml
            JOIN results r ON r.tournament_id = ml.tournament_id
                AND r.group_id = ml.group_id
            WHERE r.group_rank IS NOT NULL
        """).fetchone()["c"]

        return {
            "total_tournaments": total,
            "with_results": with_results,
            "with_ml_predictions": with_ml,
            "total_group_results": total_group_results,
            "ml_with_results": ml_with_results,
            "phase2_ready": ml_with_results >= 50,
            "phase2_progress": f"{ml_with_results}/50",
        }

    finally:
        conn.close()


def get_season_cut_data(picks_pk: str) -> dict[str, dict]:
    """pickem_field_players からシーズンCUT実績を取得。

    Args:
        picks_pk: ピック大会PK

    Returns:
        {player_name: {season_played, season_cut, current_wgr}}
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT pf.player_name, pf.season_played, pf.season_cut,
                      pf.current_wgr
               FROM pickem_field_players pf
               JOIN pickem_tournaments pt ON pt.id = pf.pickem_tournament_id
               WHERE pt.pk = ?""",
            (picks_pk,),
        ).fetchall()

        result = {}
        for row in rows:
            result[row[0]] = {
                "season_played": row[1],
                "season_cut": row[2],
                "current_wgr": row[3],
            }
        return result
    except Exception:
        return {}
    finally:
        conn.close()


def get_review_data(tournament_id: int) -> dict | None:
    """振り返り分析用の全データを一括取得する。

    ml_predictions, results, group_players を tournament_id で取得し、
    group_id でグルーピングして返す。

    Args:
        tournament_id: 対象大会のDB ID

    Returns:
        {"tournament": {...}, "groups": {gid: {"predictions": [...], "results": [...], "players": [...]}}}
        or None if data insufficient
    """
    from collections import defaultdict

    conn = get_connection()
    try:
        # 大会情報
        t_row = conn.execute(
            "SELECT id, name, start_date, end_date, field_size, status FROM tournaments WHERE id=?",
            (tournament_id,),
        ).fetchone()
        if not t_row:
            return None

        tournament = dict(t_row)

        # ML予測
        pred_rows = conn.execute(
            """SELECT group_id, player_name, ml_score, odds_component,
                      stats_component, fit_component, crowd_component,
                      ml_rank_in_group, model_version, egs
               FROM ml_predictions
               WHERE tournament_id=?
               ORDER BY group_id, ml_rank_in_group""",
            (tournament_id,),
        ).fetchall()

        # 実績結果
        result_rows = conn.execute(
            """SELECT player_name, espn_position, score, group_id, group_rank,
                      rounds_played, espn_status
               FROM results
               WHERE tournament_id=? AND group_id IS NOT NULL
               ORDER BY group_id, group_rank""",
            (tournament_id,),
        ).fetchall()

        # グループプレイヤー
        gp_rows = conn.execute(
            """SELECT player_name, group_id, wgr, fedex_rank
               FROM group_players
               WHERE tournament_id=?""",
            (tournament_id,),
        ).fetchall()

        if not pred_rows or not result_rows:
            return None

        groups: dict[int, dict] = defaultdict(lambda: {
            "predictions": [], "results": [], "players": [],
        })

        for r in pred_rows:
            d = dict(r)
            groups[d["group_id"]]["predictions"].append(d)

        for r in result_rows:
            d = dict(r)
            groups[d["group_id"]]["results"].append(d)

        for r in gp_rows:
            d = dict(r)
            groups[d["group_id"]]["players"].append(d)

        # model_version を大会レベルで取得
        if pred_rows:
            tournament["model_version"] = pred_rows[0]["model_version"] or "unknown"
        else:
            tournament["model_version"] = "unknown"

        return {
            "tournament": tournament,
            "groups": dict(groups),
        }

    finally:
        conn.close()


def format_historical_report(scores: dict[str, dict]) -> str:
    """Format historical scores into a readable report."""
    if not scores:
        return "No historical data available."

    from tabulate import tabulate

    lines = []
    lines.append("=" * 80)
    lines.append("  HISTORICAL BOOKMAKER ACCURACY (All Tournaments)")
    lines.append("=" * 80)
    lines.append("")

    # Overall ranking by match points
    ranking = sorted(scores.items(), key=lambda x: x[1]["total_match_points"], reverse=True)

    table_data = []
    for rank, (book, data) in enumerate(ranking, 1):
        n = data["tournaments_scored"]
        avg_match = data["total_match_points"] / n if n else 0
        avg_top = data["total_top_pick_score"] / n if n else 0
        table_data.append([
            rank, book, data["total_match_points"], f"{avg_match:.1f}",
            data["total_top_pick_score"], f"{avg_top:.1f}", n,
        ])

    headers = ["Rank", "Bookmaker", "Total Match", "Avg Match", "Total Top", "Avg Top", "Tournaments"]
    lines.append(tabulate(table_data, headers=headers, tablefmt="simple"))
    lines.append("")
    lines.append("  Match: Higher = better (correct position predictions)")
    lines.append("  Top: Lower = better (top picks finished closer to 1st)")
    lines.append("")

    # Per-tournament breakdown
    all_tids = set()
    for data in scores.values():
        all_tids.update(data["per_tournament"].keys())

    for tid in sorted(all_tids):
        # Get tournament name from first bookmaker that has it
        t_name = ""
        for data in scores.values():
            if tid in data["per_tournament"]:
                t_name = data["per_tournament"][tid]["name"]
                break

        lines.append(f"  --- {t_name} ---")
        t_data = []
        for book, data in ranking:
            if tid in data["per_tournament"]:
                td = data["per_tournament"][tid]
                t_data.append([book, td["match_points"], td["top_pick_score"], td["groups_scored"]])
        headers = ["Bookmaker", "Match Pts", "Top Pick", "Groups"]
        lines.append(tabulate(t_data, headers=headers, tablefmt="simple"))
        lines.append("")

    return "\n".join(lines)


# ── CLI ──


def get_dashboard_historical() -> dict:
    """ダッシュボード用の時系列データを取得。

    Returns:
        {
            "backtest_trend": [{"run_id", "computed_at", "tournaments", "accuracy_winner", "accuracy_top2", "method"}],
            "ml_accuracy": [{"tournament_name", "groups", "ml_correct_1st", "odds_correct_1st"}],
        }
    """
    conn = get_connection()
    try:
        # バックテスト精度トレンド
        backtest_rows = conn.execute("""
            SELECT run_id, computed_at, total_tournaments,
                   accuracy_winner, accuracy_top2, method
            FROM backtest_runs
            ORDER BY computed_at ASC
        """).fetchall()
        backtest_trend = [
            {
                "run_id": r["run_id"],
                "computed_at": r["computed_at"][:10] if r["computed_at"] else "",
                "tournaments": r["total_tournaments"] or 0,
                "accuracy_winner": round(r["accuracy_winner"] * 100, 1) if r["accuracy_winner"] else 0,
                "accuracy_top2": round(r["accuracy_top2"] * 100, 1) if r["accuracy_top2"] else 0,
                "method": r["method"] or "",
            }
            for r in backtest_rows
        ]

        # ML予測精度（大会別）
        ml_rows = conn.execute("""
            SELECT
                t.name as tournament_name,
                COUNT(DISTINCT ml.group_id) as total_groups,
                SUM(CASE WHEN ml.ml_rank_in_group = 1 AND r.group_rank = 1 THEN 1 ELSE 0 END) as ml_correct,
                SUM(CASE WHEN ml.ml_rank_in_group = 1 THEN 1 ELSE 0 END) as ml_predicted
            FROM ml_predictions ml
            JOIN tournaments t ON t.id = ml.tournament_id
            LEFT JOIN results r
                ON r.tournament_id = ml.tournament_id
                AND r.group_id = ml.group_id
                AND LOWER(TRIM(r.player_name)) = LOWER(TRIM(ml.player_name))
            WHERE r.group_rank IS NOT NULL
            GROUP BY ml.tournament_id
            ORDER BY t.name
        """).fetchall()
        ml_accuracy = [
            {
                "tournament_name": r["tournament_name"],
                "groups": r["total_groups"],
                "ml_correct": r["ml_correct"],
                "accuracy": round(r["ml_correct"] / r["ml_predicted"] * 100, 1) if r["ml_predicted"] else 0,
            }
            for r in ml_rows
        ]

        return {
            "backtest_trend": backtest_trend,
            "ml_accuracy": ml_accuracy,
        }
    except Exception as e:
        print(f"[WARN] get_dashboard_historical failed: {e}")
        return {"backtest_trend": [], "ml_accuracy": []}
    finally:
        conn.close()


def main():
    """CLI entry point for database operations."""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    args = sys.argv[1:]

    if not args or args[0] == "list":
        tournaments = list_tournaments()
        if not tournaments:
            print("No tournaments in database yet.")
            print("Run 'uv run python run_pipeline.py' to save the first tournament.")
            return

        from tabulate import tabulate
        table = [[t.id, t.name, t.start_date, t.status, t.num_players, t.num_bookmakers,
                   "Yes" if t.has_results else "No"] for t in tournaments]
        headers = ["ID", "Tournament", "Date", "Status", "Players", "Books", "Results"]
        print(tabulate(table, headers=headers, tablefmt="simple"))

    elif args[0] == "results":
        # Save results for a tournament
        espn_date = args[1] if len(args) > 1 else ""
        tid = int(args[2]) if len(args) > 2 else None
        success = save_tournament_results(tournament_id=tid, espn_date=espn_date)
        if success:
            print("[OK] Results saved successfully")
        else:
            print("[FAIL] Could not save results")

    elif args[0] == "collect":
        # 大会終了後のデータ収集（推奨コマンド）
        from src.result_collector import collect_results
        espn_date = args[1] if len(args) > 1 else ""
        tid = int(args[2]) if len(args) > 2 else None
        collect_results(tournament_id=tid, espn_date=espn_date)

    elif args[0] == "scores":
        # Show historical scores
        scores = get_historical_scores()
        report = format_historical_report(scores)
        print(report)

    elif args[0] == "status":
        # 蓄積状況表示
        from src.result_collector import format_status_report
        status = get_accumulation_status()
        print(format_status_report(status))

    elif args[0] == "accuracy":
        # ML精度レポート
        from src.result_collector import format_accuracy_report
        accuracy = get_ml_accuracy()
        report = format_accuracy_report(accuracy)
        print(report)

    else:
        print("Usage:")
        print("  uv run python -m src.database list         # List tournaments")
        print("  uv run python -m src.database collect [YYYYMMDD] [tid]  # Collect results (recommended)")
        print("  uv run python -m src.database results [YYYYMMDD] [tid]  # Save results only")
        print("  uv run python -m src.database scores        # Bookmaker historical accuracy")
        print("  uv run python -m src.database status        # Data accumulation status")
        print("  uv run python -m src.database accuracy      # ML prediction accuracy")


if __name__ == "__main__":
    main()
