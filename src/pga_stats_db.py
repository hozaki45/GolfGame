"""PGA Tour統計データ ローカルデータベースキャッシュ。

PGA Tour GraphQL APIから取得した統計データをSQLiteに永続化。
- 2004年〜現在まで22年分のヒストリカルデータ対応
- 一度取得したデータはローカルDBに保存し、再取得しない
- 現在シーズンは大会終了ごとに更新可能
- 大会結果（順位・賞金額）とコースプロファイル（回帰係数）も管理
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .stats_models import PlayerStats


DB_PATH = Path("data/pga_stats.db")


class PGAStatsDB:
    """PGA Tour統計データのローカルキャッシュDB。"""

    def __init__(self, db_path: Path | None = None):
        """初期化。DBファイルとテーブルを作成。

        Args:
            db_path: DBファイルパス（デフォルト: data/pga_stats.db）
        """
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """DB接続を取得。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _create_tables(self) -> None:
        """テーブルを作成（存在しない場合のみ）。"""
        conn = self._get_conn()
        try:
            conn.executescript("""
                -- 個別統計値: 年 × 統計ID × 選手
                CREATE TABLE IF NOT EXISTS pga_season_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    stat_id TEXT NOT NULL,
                    stat_name TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    rank INTEGER,
                    stat_value REAL,
                    measured_rounds INTEGER,
                    fetched_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_pga_season_stats_unique
                    ON pga_season_stats(year, stat_id, player_id);

                CREATE INDEX IF NOT EXISTS idx_pga_season_stats_year
                    ON pga_season_stats(year);

                CREATE INDEX IF NOT EXISTS idx_pga_season_stats_player
                    ON pga_season_stats(player_name);

                -- 取得ログ: どの年×統計IDが取得済みか
                CREATE TABLE IF NOT EXISTS pga_fetch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    stat_id TEXT NOT NULL,
                    num_players INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_pga_fetch_log_unique
                    ON pga_fetch_log(year, stat_id);

                -- 大会マスタ: スケジュールから取得（コース情報含む）
                CREATE TABLE IF NOT EXISTS pga_tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    tournament_name TEXT NOT NULL,
                    course_name TEXT,
                    city TEXT,
                    state TEXT,
                    state_code TEXT,
                    country TEXT,
                    country_code TEXT,
                    purse TEXT,
                    fetched_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_pga_tournaments_unique
                    ON pga_tournaments(tournament_id, year);

                -- 大会結果: 選手ごとの順位・賞金額
                CREATE TABLE IF NOT EXISTS pga_tournament_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    position INTEGER,
                    total_score TEXT,
                    prize_money REAL,
                    fedex_points REAL,
                    fetched_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_pga_results_unique
                    ON pga_tournament_results(tournament_id, year, player_id);

                CREATE INDEX IF NOT EXISTS idx_pga_results_tournament
                    ON pga_tournament_results(tournament_id);

                CREATE INDEX IF NOT EXISTS idx_pga_results_year
                    ON pga_tournament_results(year);

                -- コースプロファイル: 重回帰分析の結果（コース名ベース）
                CREATE TABLE IF NOT EXISTS pga_course_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_name TEXT NOT NULL,
                    tournament_name TEXT NOT NULL,
                    years_analyzed INTEGER NOT NULL,
                    years_list TEXT NOT NULL,
                    n_samples INTEGER,
                    r_squared REAL,
                    coef_sg_approach REAL,
                    coef_sg_off_tee REAL,
                    coef_sg_tee_to_green REAL,
                    coef_sg_putting REAL,
                    coef_sg_around_green REAL,
                    coef_gir_pct REAL,
                    coef_driving_distance REAL,
                    coef_driving_accuracy_pct REAL,
                    coef_scrambling_pct REAL,
                    coef_scoring_average REAL,
                    pval_sg_approach REAL,
                    pval_sg_off_tee REAL,
                    pval_sg_tee_to_green REAL,
                    pval_sg_putting REAL,
                    pval_sg_around_green REAL,
                    pval_gir_pct REAL,
                    pval_driving_distance REAL,
                    pval_driving_accuracy_pct REAL,
                    pval_scrambling_pct REAL,
                    pval_scoring_average REAL,
                    scaler_params TEXT NOT NULL,
                    computed_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_course_profiles_unique
                    ON pga_course_profiles(course_name);
            """)
            conn.commit()
        finally:
            conn.close()

    def save_stats_bulk(
        self,
        year: int,
        data: dict[str, list[dict]],
        stat_id_map: dict[str, tuple[str, float]],
    ) -> int:
        """統計データを一括保存。

        Args:
            year: シーズン年
            data: {stat_id: [{player_id, player_name, rank, stat_value, measured_rounds}, ...]}
            stat_id_map: {stat_id: (field_name, weight)} のマッピング

        Returns:
            保存した総レコード数
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()
        total_saved = 0

        try:
            for stat_id, rows in data.items():
                if not rows:
                    continue

                field_name = stat_id_map.get(stat_id, (stat_id, 0))[0]

                # UPSERT（既存データは更新）
                for row in rows:
                    conn.execute("""
                        INSERT INTO pga_season_stats
                            (year, stat_id, stat_name, player_id, player_name,
                             rank, stat_value, measured_rounds, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(year, stat_id, player_id) DO UPDATE SET
                            stat_name=excluded.stat_name,
                            player_name=excluded.player_name,
                            rank=excluded.rank,
                            stat_value=excluded.stat_value,
                            measured_rounds=excluded.measured_rounds,
                            fetched_at=excluded.fetched_at
                    """, (
                        year, stat_id, field_name, row["player_id"], row["player_name"],
                        row.get("rank"), row["stat_value"], row.get("measured_rounds"),
                        now,
                    ))
                    total_saved += 1

                # 取得ログを更新
                conn.execute("""
                    INSERT INTO pga_fetch_log (year, stat_id, num_players, fetched_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(year, stat_id) DO UPDATE SET
                        num_players=excluded.num_players,
                        fetched_at=excluded.fetched_at
                """, (year, stat_id, len(rows), now))

            conn.commit()
            return total_saved

        finally:
            conn.close()

    def get_missing_stats(
        self, year: int, stat_ids: list[str]
    ) -> list[str]:
        """指定年でまだ取得していない統計IDを返す。

        完了済みシーズン（現在年より前）のデータは一度取得したら再取得しない。
        現在シーズンのデータは24時間以上経過していれば再取得対象とする。

        Args:
            year: シーズン年
            stat_ids: チェックする統計IDリスト

        Returns:
            まだ取得していない統計IDリスト
        """
        conn = self._get_conn()
        try:
            current_year = datetime.now().year
            missing = []

            for stat_id in stat_ids:
                row = conn.execute(
                    "SELECT fetched_at FROM pga_fetch_log WHERE year = ? AND stat_id = ?",
                    (year, stat_id),
                ).fetchone()

                if not row:
                    # 未取得
                    missing.append(stat_id)
                elif year >= current_year:
                    # 現在シーズン: 24時間以上経過していれば再取得
                    fetched_at = datetime.fromisoformat(row["fetched_at"])
                    hours_ago = (datetime.now() - fetched_at).total_seconds() / 3600
                    if hours_ago >= 24:
                        missing.append(stat_id)

            return missing

        finally:
            conn.close()

    def get_player_stats_for_year(
        self,
        year: int,
        player_names: list[str] | None = None,
    ) -> list[PlayerStats]:
        """指定年の統計データからPlayerStatsを構築。

        Args:
            year: シーズン年
            player_names: フィルタする選手名リスト（Noneの場合は全選手）

        Returns:
            PlayerStatsのリスト（データなしの場合は空リスト）
        """
        from fuzzywuzzy import fuzz

        conn = self._get_conn()
        try:
            # まずフェッチログをチェック（データがあるか）
            log_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM pga_fetch_log WHERE year = ?",
                (year,),
            ).fetchone()["cnt"]

            if log_count == 0:
                return []  # この年のデータはまだ取得されていない

            # 全統計データを取得
            rows = conn.execute(
                "SELECT stat_id, stat_name, player_id, player_name, stat_value "
                "FROM pga_season_stats WHERE year = ?",
                (year,),
            ).fetchall()

            if not rows:
                return []

            # 選手ごとに統計値を集約
            # stat_name（field_name）をキーにする
            players: dict[str, dict] = {}  # player_name -> {field: value}
            for row in rows:
                pname = row["player_name"]
                if pname not in players:
                    players[pname] = {}
                players[pname][row["stat_name"]] = row["stat_value"]

            # player_namesが指定されている場合、ファジーマッチング
            if player_names:
                filtered_results = []
                for target_name in player_names:
                    target_lower = target_name.lower().strip()

                    # 完全一致
                    matched_data = None
                    for pname, stats_dict in players.items():
                        if pname.lower().strip() == target_lower:
                            matched_data = stats_dict
                            break

                    # ファジーマッチ
                    if matched_data is None:
                        best_score = 0
                        for pname, stats_dict in players.items():
                            score = max(
                                fuzz.ratio(target_lower, pname.lower()),
                                fuzz.token_sort_ratio(target_lower, pname.lower()),
                            )
                            if score > best_score and score >= 80:
                                best_score = score
                                matched_data = stats_dict

                    if matched_data:
                        ps = self._build_player_stats(target_name, matched_data)
                        filtered_results.append(ps)
                    else:
                        # データなし
                        filtered_results.append(PlayerStats(
                            name=target_name,
                            data_source="pgatour_graphql_cache",
                        ))

                return filtered_results
            else:
                # 全選手
                return [
                    self._build_player_stats(pname, stats_dict)
                    for pname, stats_dict in players.items()
                ]

        finally:
            conn.close()

    def _build_player_stats(self, name: str, stats_dict: dict) -> PlayerStats:
        """統計辞書からPlayerStatsを構築。

        Args:
            name: 選手名
            stats_dict: {field_name: stat_value}

        Returns:
            PlayerStatsオブジェクト
        """
        return PlayerStats(
            name=name,
            sg_approach=stats_dict.get("sg_approach"),
            sg_off_tee=stats_dict.get("sg_off_tee"),
            sg_tee_to_green=stats_dict.get("sg_tee_to_green"),
            sg_total=stats_dict.get("sg_total"),
            sg_putting=stats_dict.get("sg_putting"),
            sg_around_green=stats_dict.get("sg_around_green"),
            greens_in_regulation_pct=stats_dict.get("gir_pct"),
            driving_distance=stats_dict.get("driving_distance"),
            driving_accuracy_pct=stats_dict.get("driving_accuracy_pct"),
            scrambling_pct=stats_dict.get("scrambling_pct"),
            scoring_average=stats_dict.get("scoring_average"),
            data_source="pgatour_graphql_cache",
        )

    def invalidate_current_season(self) -> int:
        """現在シーズンのキャッシュを無効化（再取得を強制）。

        Returns:
            削除されたフェッチログエントリ数
        """
        current_year = datetime.now().year
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM pga_fetch_log WHERE year = ?",
                (current_year,),
            )
            conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                print(f"[INFO] Invalidated {deleted} cache entries for {current_year}")
            return deleted
        finally:
            conn.close()

    def get_cached_years(self) -> list[dict]:
        """キャッシュ済みの年とその統計情報を取得。

        Returns:
            [{"year": 2024, "stats_count": 6, "players_count": 184, "fetched_at": "..."}, ...]
        """
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT
                    fl.year,
                    COUNT(DISTINCT fl.stat_id) as stats_count,
                    COALESCE(
                        (SELECT COUNT(DISTINCT player_id)
                         FROM pga_season_stats ss
                         WHERE ss.year = fl.year), 0
                    ) as players_count,
                    MAX(fl.fetched_at) as last_fetched
                FROM pga_fetch_log fl
                GROUP BY fl.year
                ORDER BY fl.year DESC
            """).fetchall()

            return [
                {
                    "year": r["year"],
                    "stats_count": r["stats_count"],
                    "players_count": r["players_count"],
                    "last_fetched": r["last_fetched"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_total_records(self) -> int:
        """DBの総レコード数を取得。"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM pga_season_stats").fetchone()
            return row["cnt"]
        finally:
            conn.close()

    #-----Tournament Results Methods-----

    def save_tournaments(self, tournaments: list[dict]) -> int:
        """大会マスタを一括保存（コース情報含む）。

        Args:
            tournaments: [{tournament_id, year, tournament_name,
                          course_name, city, state, state_code,
                          country, country_code, purse}, ...]

        Returns:
            保存したレコード数
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()
        saved = 0
        try:
            for t in tournaments:
                conn.execute("""
                    INSERT INTO pga_tournaments
                        (tournament_id, year, tournament_name,
                         course_name, city, state, state_code,
                         country, country_code, purse, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tournament_id, year) DO UPDATE SET
                        tournament_name=excluded.tournament_name,
                        course_name=excluded.course_name,
                        city=excluded.city,
                        state=excluded.state,
                        state_code=excluded.state_code,
                        country=excluded.country,
                        country_code=excluded.country_code,
                        purse=excluded.purse,
                        fetched_at=excluded.fetched_at
                """, (
                    t["tournament_id"], t["year"], t["tournament_name"],
                    t.get("course_name"), t.get("city"), t.get("state"),
                    t.get("state_code"), t.get("country"), t.get("country_code"),
                    t.get("purse"), now,
                ))
                saved += 1
            conn.commit()
            return saved
        finally:
            conn.close()

    def save_tournament_results(self, results: list[dict]) -> int:
        """大会結果を一括保存。

        Args:
            results: [{tournament_id, year, player_id, player_name, position,
                       total_score, prize_money, fedex_points}, ...]

        Returns:
            保存したレコード数
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()
        saved = 0
        try:
            for r in results:
                conn.execute("""
                    INSERT INTO pga_tournament_results
                        (tournament_id, year, player_id, player_name,
                         position, total_score, prize_money, fedex_points, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tournament_id, year, player_id) DO UPDATE SET
                        player_name=excluded.player_name,
                        position=excluded.position,
                        total_score=excluded.total_score,
                        prize_money=excluded.prize_money,
                        fedex_points=excluded.fedex_points,
                        fetched_at=excluded.fetched_at
                """, (
                    r["tournament_id"], r["year"], r["player_id"], r["player_name"],
                    r.get("position"), r.get("total_score"),
                    r.get("prize_money"), r.get("fedex_points"), now,
                ))
                saved += 1
            conn.commit()
            return saved
        finally:
            conn.close()

    def has_tournament_results(self, tournament_id: str, year: int) -> bool:
        """大会結果が既にDBにあるかチェック。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM pga_tournament_results WHERE tournament_id = ? AND year = ?",
                (tournament_id, year),
            ).fetchone()
            return row["cnt"] > 0
        finally:
            conn.close()

    def get_tournament_results(
        self, tournament_id: str, year: int | None = None
    ) -> list[dict]:
        """大会結果を取得。

        Args:
            tournament_id: トーナメントID（例: "R2024034"）
            year: 年（Noneの場合は全年）

        Returns:
            [{player_id, player_name, position, prize_money, ...}, ...]
        """
        conn = self._get_conn()
        try:
            if year is not None:
                rows = conn.execute(
                    "SELECT * FROM pga_tournament_results WHERE tournament_id = ? AND year = ? "
                    "ORDER BY position",
                    (tournament_id, year),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pga_tournament_results WHERE tournament_id = ? "
                    "ORDER BY year DESC, position",
                    (tournament_id,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_results_for_regression(
        self, tournament_num: str, years: list[int]
    ) -> list[dict]:
        """回帰分析用にresults + season_statsをJOINして取得。

        tournament_numは大会番号（例: "034"）。年によってIDが変わる場合があるため
        LIKE検索でマッチング（例: "%034"）。

        Args:
            tournament_num: 大会番号（トーナメントIDの数字部分）
            years: 分析対象年リスト

        Returns:
            [{player_id, player_name, year, prize_money, sg_approach, sg_off_tee, ...}, ...]
        """
        conn = self._get_conn()
        try:
            placeholders = ",".join("?" * len(years))
            pattern = f"%{tournament_num}"

            rows = conn.execute(f"""
                SELECT
                    tr.player_id,
                    tr.player_name,
                    tr.year,
                    tr.position,
                    tr.prize_money,
                    MAX(CASE WHEN ss.stat_name = 'sg_approach' THEN ss.stat_value END) as sg_approach,
                    MAX(CASE WHEN ss.stat_name = 'sg_off_tee' THEN ss.stat_value END) as sg_off_tee,
                    MAX(CASE WHEN ss.stat_name = 'sg_tee_to_green' THEN ss.stat_value END) as sg_tee_to_green,
                    MAX(CASE WHEN ss.stat_name = 'sg_putting' THEN ss.stat_value END) as sg_putting,
                    MAX(CASE WHEN ss.stat_name = 'sg_around_green' THEN ss.stat_value END) as sg_around_green,
                    MAX(CASE WHEN ss.stat_name = 'gir_pct' THEN ss.stat_value END) as gir_pct,
                    MAX(CASE WHEN ss.stat_name = 'driving_distance' THEN ss.stat_value END) as driving_distance,
                    MAX(CASE WHEN ss.stat_name = 'driving_accuracy_pct' THEN ss.stat_value END) as driving_accuracy_pct,
                    MAX(CASE WHEN ss.stat_name = 'scrambling_pct' THEN ss.stat_value END) as scrambling_pct,
                    MAX(CASE WHEN ss.stat_name = 'scoring_average' THEN ss.stat_value END) as scoring_average
                FROM pga_tournament_results tr
                LEFT JOIN pga_season_stats ss
                    ON tr.player_id = ss.player_id AND tr.year = ss.year
                WHERE tr.tournament_id LIKE ? AND tr.year IN ({placeholders})
                    AND tr.prize_money IS NOT NULL AND tr.prize_money > 0
                GROUP BY tr.player_id, tr.year
                HAVING sg_approach IS NOT NULL OR sg_off_tee IS NOT NULL
                    OR gir_pct IS NOT NULL
            """, (pattern, *years)).fetchall()

            return [dict(r) for r in rows]
        finally:
            conn.close()

    #-----Course Analysis Methods-----

    def get_years_by_course(self, tournament_num: str) -> dict[str, list[int]]:
        """大会番号に対して {course_name: [years]} を返す。

        同じ大会でもコースが変わる場合があるため、コース名でグルーピング。
        ファジーマッチング（閾値90）で表記揺れを統合。

        Args:
            tournament_num: 大会番号（例: "014"）

        Returns:
            {course_name: [year1, year2, ...]}  年は降順ソート
        """
        from fuzzywuzzy import fuzz

        conn = self._get_conn()
        try:
            pattern = f"%{tournament_num}"
            rows = conn.execute(
                "SELECT year, course_name FROM pga_tournaments "
                "WHERE tournament_id LIKE ? AND course_name IS NOT NULL "
                "AND course_name != '' "
                "ORDER BY year DESC",
                (pattern,),
            ).fetchall()

            if not rows:
                return {}

            # コース名でグルーピング（ファジーマッチングで表記揺れ統合）
            course_years: dict[str, list[int]] = {}
            canonical_names: list[str] = []  # 正規化済みコース名リスト

            for row in rows:
                course = row["course_name"]
                year = row["year"]

                # 既存のコース名とファジーマッチ
                matched_name = None
                for canonical in canonical_names:
                    score = fuzz.ratio(course.lower(), canonical.lower())
                    if score >= 90:
                        matched_name = canonical
                        break

                if matched_name is None:
                    # 新しいコース名
                    canonical_names.append(course)
                    course_years[course] = [year]
                else:
                    course_years[matched_name].append(year)

            # 各コースの年リストをソート（降順）
            for course in course_years:
                course_years[course] = sorted(course_years[course], reverse=True)

            return course_years
        finally:
            conn.close()

    def get_tournament_num(self, tournament_id: str) -> str:
        """トーナメントIDから大会番号を抽出。

        例: "R2024014" → "014", "R2024546" → "546"

        Args:
            tournament_id: トーナメントID

        Returns:
            大会番号（数字3桁以上）
        """
        import re
        match = re.search(r"R\d{4}(\d{3,})", tournament_id)
        return match.group(1) if match else tournament_id[-3:]

    def get_course_for_tournament(
        self, tournament_id: str, year: int
    ) -> str | None:
        """特定の大会・年のコース名を取得。

        Args:
            tournament_id: トーナメントID
            year: 年

        Returns:
            コース名。なければNone
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT course_name FROM pga_tournaments "
                "WHERE tournament_id = ? AND year = ?",
                (tournament_id, year),
            ).fetchone()
            return row["course_name"] if row else None
        finally:
            conn.close()

    def find_tournament_num_by_name(self, tournament_name: str) -> str | None:
        """大会名から大会番号を検索（ファジーマッチング）。

        Args:
            tournament_name: 大会名（部分一致可）

        Returns:
            大会番号（例: "034"）。見つからない場合はNone
        """
        import re
        from fuzzywuzzy import fuzz

        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT tournament_id, tournament_name FROM pga_tournaments "
                "ORDER BY year DESC"
            ).fetchall()

            best_score = 0
            best_tid = None
            target = tournament_name.lower().strip()

            for row in rows:
                name = row["tournament_name"].lower().strip()
                score = max(
                    fuzz.ratio(target, name),
                    fuzz.partial_ratio(target, name),
                    fuzz.token_sort_ratio(target, name),
                )
                if score > best_score and score >= 70:
                    best_score = score
                    best_tid = row["tournament_id"]

            if best_tid:
                match = re.search(r"R\d{4}(\d{3,})", best_tid)
                return match.group(1) if match else None
            return None
        finally:
            conn.close()

    #-----Course Profile Methods-----

    def save_course_profile(self, profile: dict) -> None:
        """コースプロファイル（回帰分析結果）を保存。

        Args:
            profile: {course_name, tournament_name, years_analyzed, years_list,
                      n_samples, r_squared, coef_*, pval_*, scaler_params}
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()
        try:
            conn.execute("""
                INSERT INTO pga_course_profiles
                    (course_name, tournament_name, years_analyzed, years_list,
                     n_samples, r_squared,
                     coef_sg_approach, coef_sg_off_tee, coef_sg_tee_to_green,
                     coef_sg_putting, coef_sg_around_green, coef_gir_pct,
                     coef_driving_distance, coef_driving_accuracy_pct,
                     coef_scrambling_pct, coef_scoring_average,
                     pval_sg_approach, pval_sg_off_tee, pval_sg_tee_to_green,
                     pval_sg_putting, pval_sg_around_green, pval_gir_pct,
                     pval_driving_distance, pval_driving_accuracy_pct,
                     pval_scrambling_pct, pval_scoring_average,
                     scaler_params, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(course_name) DO UPDATE SET
                    tournament_name=excluded.tournament_name,
                    years_analyzed=excluded.years_analyzed,
                    years_list=excluded.years_list,
                    n_samples=excluded.n_samples,
                    r_squared=excluded.r_squared,
                    coef_sg_approach=excluded.coef_sg_approach,
                    coef_sg_off_tee=excluded.coef_sg_off_tee,
                    coef_sg_tee_to_green=excluded.coef_sg_tee_to_green,
                    coef_sg_putting=excluded.coef_sg_putting,
                    coef_sg_around_green=excluded.coef_sg_around_green,
                    coef_gir_pct=excluded.coef_gir_pct,
                    coef_driving_distance=excluded.coef_driving_distance,
                    coef_driving_accuracy_pct=excluded.coef_driving_accuracy_pct,
                    coef_scrambling_pct=excluded.coef_scrambling_pct,
                    coef_scoring_average=excluded.coef_scoring_average,
                    pval_sg_approach=excluded.pval_sg_approach,
                    pval_sg_off_tee=excluded.pval_sg_off_tee,
                    pval_sg_tee_to_green=excluded.pval_sg_tee_to_green,
                    pval_sg_putting=excluded.pval_sg_putting,
                    pval_sg_around_green=excluded.pval_sg_around_green,
                    pval_gir_pct=excluded.pval_gir_pct,
                    pval_driving_distance=excluded.pval_driving_distance,
                    pval_driving_accuracy_pct=excluded.pval_driving_accuracy_pct,
                    pval_scrambling_pct=excluded.pval_scrambling_pct,
                    pval_scoring_average=excluded.pval_scoring_average,
                    scaler_params=excluded.scaler_params,
                    computed_at=excluded.computed_at
            """, (
                profile["course_name"], profile["tournament_name"],
                profile["years_analyzed"], profile["years_list"],
                profile.get("n_samples"), profile.get("r_squared"),
                profile.get("coef_sg_approach"), profile.get("coef_sg_off_tee"),
                profile.get("coef_sg_tee_to_green"), profile.get("coef_sg_putting"),
                profile.get("coef_sg_around_green"), profile.get("coef_gir_pct"),
                profile.get("coef_driving_distance"), profile.get("coef_driving_accuracy_pct"),
                profile.get("coef_scrambling_pct"), profile.get("coef_scoring_average"),
                profile.get("pval_sg_approach"), profile.get("pval_sg_off_tee"),
                profile.get("pval_sg_tee_to_green"), profile.get("pval_sg_putting"),
                profile.get("pval_sg_around_green"), profile.get("pval_gir_pct"),
                profile.get("pval_driving_distance"), profile.get("pval_driving_accuracy_pct"),
                profile.get("pval_scrambling_pct"), profile.get("pval_scoring_average"),
                profile["scaler_params"], now,
            ))
            conn.commit()
        finally:
            conn.close()

    def get_course_profile(self, course_name: str) -> dict | None:
        """コースプロファイルを取得。

        Args:
            course_name: コース名

        Returns:
            プロファイル辞書。なければNone
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM pga_course_profiles WHERE course_name = ?",
                (course_name,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_course_profiles(self) -> list[dict]:
        """全コースプロファイルを取得。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM pga_course_profiles ORDER BY tournament_name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_tournaments_for_year(self, year: int) -> list[dict]:
        """指定年の大会マスタを取得。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM pga_tournaments WHERE year = ? ORDER BY tournament_id",
                (year,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_tournament_results_count(self) -> dict:
        """大会結果の統計情報を取得。"""
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT
                    COUNT(DISTINCT tournament_id || year) as tournament_count,
                    COUNT(*) as total_results,
                    MIN(year) as min_year,
                    MAX(year) as max_year
                FROM pga_tournament_results
            """).fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def print_status(self) -> None:
        """DBキャッシュの状態を表示。"""
        print("=" * 60)
        print("  PGA Tour Stats Local Database Status")
        print("=" * 60)
        print(f"\n  DB Path: {self.db_path}")

        total = self.get_total_records()
        print(f"  Total Records: {total:,}")

        cached_years = self.get_cached_years()
        if not cached_years:
            print("\n  No data cached yet.")
            print("  Run: uv run python -m src.stats_scraper --historical")
            return

        print(f"\n  Cached Years: {len(cached_years)}")
        print(f"  {'Year':<8} {'Stats':<8} {'Players':<10} {'Last Fetched'}")
        print(f"  {'-'*8} {'-'*8} {'-'*10} {'-'*20}")

        for info in cached_years:
            print(
                f"  {info['year']:<8} "
                f"{info['stats_count']:<8} "
                f"{info['players_count']:<10} "
                f"{info['last_fetched'][:19]}"
            )

        # 欠落年チェック
        cached_year_set = {info["year"] for info in cached_years}
        current_year = datetime.now().year
        all_years = set(range(2004, current_year + 1))
        missing = sorted(all_years - cached_year_set)
        if missing:
            print(f"\n  Missing Years: {', '.join(str(y) for y in missing)}")
            print("  Run: uv run python -m src.stats_scraper --historical")

        # 大会結果の統計
        results_info = self.get_tournament_results_count()
        if results_info.get("total_results", 0) > 0:
            print(f"\n  Tournament Results:")
            print(f"    Tournaments: {results_info['tournament_count']}")
            print(f"    Total Results: {results_info['total_results']:,}")
            print(f"    Years: {results_info['min_year']}-{results_info['max_year']}")

        # コースプロファイル
        profiles = self.get_all_course_profiles()
        if profiles:
            print(f"\n  Course Profiles: {len(profiles)}")
