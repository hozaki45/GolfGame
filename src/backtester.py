"""ヒストリカルバックテスト & ML最適ウェイト学習。

Pick'emフィールドデータ + PGA大会結果を照合し、
アンサンブル予測の最適信号ウェイトをMLで学習する。

Usage:
    uv run python -m src.backtester --quick           # PK 400-409
    uv run python -m src.backtester --run              # 全PK
    uv run python -m src.backtester --optimize         # 最適化
    uv run python -m src.backtester --validate         # ウォークフォワード検証
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from fuzzywuzzy import fuzz

from src.database import get_connection
from src.pga_stats_db import PGAStatsDB


#-----Data Classes-----

@dataclass
class GroupObservation:
    """1グループの1選手の観測データ。"""
    pk: int
    group_id: int
    player_name: str
    ranking_signal: float | None = None
    stats_signal: float | None = None
    fit_signal: float | None = None
    crowd_signal: float | None = None
    affinity_signal: float | None = None
    pga_position: int | None = None
    is_group_winner: bool = False
    is_group_top2: bool = False


@dataclass
class TournamentBacktestResult:
    """1大会のバックテスト結果。"""
    pk: int
    tournament_name: str
    pga_year: int
    pga_tournament_id: str
    num_groups: int
    num_groups_with_results: int
    has_real_odds: bool = False
    observations: list[GroupObservation] = field(default_factory=list)


@dataclass
class BacktestResult:
    """全体のバックテスト結果。"""
    tournaments: list[TournamentBacktestResult] = field(default_factory=list)

    @property
    def total_groups(self) -> int:
        return sum(t.num_groups_with_results for t in self.tournaments)

    @property
    def total_observations(self) -> int:
        return sum(len(t.observations) for t in self.tournaments)

    def all_observations(self) -> list[GroupObservation]:
        obs = []
        for t in self.tournaments:
            obs.extend(t.observations)
        return obs


@dataclass
class OptimalWeights:
    """最適化されたウェイト。"""
    ranking: float
    stats: float
    course_fit: float
    crowd: float
    affinity: float
    accuracy_winner: float
    accuracy_top2: float
    method: str


#-----PK-to-Year Mapping-----

# 既知のSentry/シーズン開始PKからマッピング
PK_SEASON_BOUNDARIES: list[tuple[int, int]] = [
    (11, 2019),
    (91, 2020),
    (140, 2021),
    (199, 2022),
    (254, 2023),
    (314, 2024),
    (356, 2025),
]


def pk_to_year(pk: int) -> int:
    """PKからPGAシーズン年を推定。"""
    for start_pk, year in reversed(PK_SEASON_BOUNDARIES):
        if pk >= start_pk:
            return year
    return 2019  # fallback


#-----Helpers-----

def _parse_finish_position(finish: str) -> int | None:
    """順位文字列を数値に変換。'T9'→9, 'CUT'→80, 'WD'→None。"""
    if not finish:
        return None
    finish = finish.strip().upper()
    if finish in ("MC", "CUT", "MDF"):
        return 80
    if finish in ("WD", "DQ", "DNS"):
        return None
    m = re.match(r"T?(\d+)", finish)
    if m:
        return int(m.group(1))
    return None


def _min_max_scale(values: dict[str, float], invert: bool = False) -> dict[str, float]:
    """辞書の値を0-100にmin-maxスケーリング。

    Args:
        values: {name: raw_value}
        invert: Trueの場合、低い値が高スコア（WGR、順位向け）
    """
    if not values:
        return {}
    vals = list(values.values())
    min_v, max_v = min(vals), max(vals)
    if max_v == min_v:
        return {name: 50.0 for name in values}
    result = {}
    for name, v in values.items():
        if invert:
            result[name] = ((max_v - v) / (max_v - min_v)) * 100.0
        else:
            result[name] = ((v - min_v) / (max_v - min_v)) * 100.0
    return result


#-----Historical Backtester-----

class HistoricalBacktester:
    """ヒストリカルバックテスト実行エンジン。"""

    def __init__(self):
        self.stats_db = PGAStatsDB()
        # SG統計ウェイト（config.yaml準拠）
        self.stat_weights = {
            "sg_approach": 0.30,
            "sg_off_tee": 0.25,
            "sg_tee_to_green": 0.20,
            "gir_pct": 0.10,
            "scoring_average": 0.08,
            "scrambling_pct": 0.07,
        }
        # 大会リンクキャッシュ
        self._link_cache: dict[int, tuple[str, int] | None] = {}
        # PGA大会名キャッシュ（year→list）
        self._pga_tournaments_cache: dict[int, list[dict]] = {}

    #-----Tournament Linking-----

    def _get_pga_tournaments_for_year(self, year: int) -> list[dict]:
        """指定年のPGA大会リストを取得（キャッシュ付き）。"""
        if year in self._pga_tournaments_cache:
            return self._pga_tournaments_cache[year]

        conn = self.stats_db._get_conn()
        try:
            rows = conn.execute(
                "SELECT tournament_id, tournament_name FROM pga_tournaments WHERE year = ?",
                (year,),
            ).fetchall()
            result = [dict(r) for r in rows]
            self._pga_tournaments_cache[year] = result
            return result
        finally:
            conn.close()

    def link_tournament(self, pk: int, tournament_name: str) -> tuple[str, int] | None:
        """PickemトーナメントをPGA大会結果にリンク。

        Returns:
            (pga_tournament_id, year) or None
        """
        if pk in self._link_cache:
            return self._link_cache[pk]

        if not tournament_name:
            self._link_cache[pk] = None
            return None

        year = pk_to_year(pk)
        candidates = self._get_pga_tournaments_for_year(year)

        target = tournament_name.lower().strip()
        best_score, best_tid = 0, None

        for c in candidates:
            name = c["tournament_name"].lower().strip()
            score = max(
                fuzz.ratio(target, name),
                fuzz.partial_ratio(target, name),
                fuzz.token_sort_ratio(target, name),
            )
            if score > best_score and score >= 70:
                best_score = score
                best_tid = c["tournament_id"]

        if best_tid:
            # 結果データが存在するか確認
            conn = self.stats_db._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM pga_tournament_results "
                    "WHERE tournament_id = ? AND year = ? AND position IS NOT NULL AND position > 0",
                    (best_tid, year),
                ).fetchone()
                if row["cnt"] >= 10:
                    self._link_cache[pk] = (best_tid, year)
                    return (best_tid, year)
            finally:
                conn.close()

        self._link_cache[pk] = None
        return None

    #-----Ground Truth-----

    def get_pga_results(self, pga_tid: str, year: int) -> dict[str, int]:
        """PGA大会の選手→順位マップを取得。

        Returns:
            {player_name_lower: position}
        """
        results = self.stats_db.get_tournament_results(pga_tid, year)
        lookup: dict[str, int] = {}
        for r in results:
            pos = r.get("position")
            if pos and pos > 0:
                lookup[r["player_name"].lower().strip()] = pos
        return lookup

    def match_player_to_results(
        self, player_name: str, results_lookup: dict[str, int]
    ) -> int | None:
        """選手名をPGA結果にマッチさせて順位を返す。"""
        target = player_name.lower().strip()

        # 完全一致
        if target in results_lookup:
            return results_lookup[target]

        # ファジーマッチ
        best_score, best_pos = 0, None
        for rname, rpos in results_lookup.items():
            score = max(
                fuzz.ratio(target, rname),
                fuzz.token_sort_ratio(target, rname),
            )
            if score > best_score and score >= 80:
                best_score = score
                best_pos = rpos

        return best_pos

    def compute_group_results(
        self, field_players: list[sqlite3.Row], results_lookup: dict[str, int]
    ) -> dict[int, dict[str, int]]:
        """グループごとの選手順位マップを構築。

        Returns:
            {group_id: {player_name: position}}
        """
        group_results: dict[int, dict[str, int]] = {}
        for fp in field_players:
            gid = fp["group_id"]
            if gid not in group_results:
                group_results[gid] = {}
            pos = self.match_player_to_results(fp["player_name"], results_lookup)
            if pos is not None:
                group_results[gid][fp["player_name"]] = pos
            else:
                group_results[gid][fp["player_name"]] = 999
        return group_results

    #-----Signal 1: Odds / Ranking-----

    def compute_odds_signal(
        self, group_players: list[sqlite3.Row], tournament_name: str, conn: sqlite3.Connection
    ) -> tuple[dict[str, float], bool]:
        """実オッズがあれば使用、なければWGRフォールバック。

        大会開始日の日本時間正午以前のスナップショットのみを使用する。
        大会中・大会後のオッズは分析対象外。

        Returns:
            (signal_dict, is_real_odds)
        """
        player_names = [p["player_name"] for p in group_players]

        # odds_snapshots 内の大会名をファジーマッチで解決
        odds_tournament_name = tournament_name
        exact = conn.execute(
            "SELECT COUNT(*) as c FROM odds_snapshots WHERE tournament_name = ?",
            (tournament_name,),
        ).fetchone()["c"]
        if exact == 0:
            # ファジーマッチで候補を探す
            all_names = conn.execute(
                "SELECT DISTINCT tournament_name FROM odds_snapshots"
            ).fetchall()
            best_name, best_score = None, 0
            for r in all_names:
                s = fuzz.ratio(tournament_name.lower(), r["tournament_name"].lower())
                if s > best_score and s >= 70:
                    best_score = s
                    best_name = r["tournament_name"]
            if best_name:
                odds_tournament_name = best_name

        # 大会開始日を取得してカットオフ時刻を計算
        start_row = conn.execute(
            "SELECT tournament_start_date FROM odds_snapshots "
            "WHERE tournament_name = ? AND tournament_start_date IS NOT NULL "
            "LIMIT 1",
            (odds_tournament_name,),
        ).fetchone()

        if start_row and start_row["tournament_start_date"]:
            # 大会開始日の正午(JST)以前のスナップショットのみ対象
            cutoff = start_row["tournament_start_date"] + "T12:00:00"
            row = conn.execute(
                "SELECT snapshot_at FROM odds_snapshots "
                "WHERE tournament_name = ? AND snapshot_at < ? "
                "ORDER BY snapshot_at DESC LIMIT 1",
                (odds_tournament_name, cutoff),
            ).fetchone()
        else:
            # tournament_start_dateが未設定の場合は最新を使用（後方互換）
            row = conn.execute(
                "SELECT snapshot_at FROM odds_snapshots "
                "WHERE tournament_name = ? ORDER BY snapshot_at DESC LIMIT 1",
                (odds_tournament_name,),
            ).fetchone()

        if row:
            snapshot_at = row["snapshot_at"]
            # 各選手のimplied_probabilityを取得（ブックメーカー中央値）
            implied_probs: dict[str, float] = {}
            for name in player_names:
                # 選手名のファジーマッチ
                odds_rows = conn.execute(
                    "SELECT odds_value FROM odds_snapshots "
                    "WHERE tournament_name = ? AND snapshot_at = ? AND LOWER(player_name) = LOWER(?)",
                    (odds_tournament_name, snapshot_at, name),
                ).fetchall()

                if not odds_rows:
                    # ファジーマッチ
                    all_players = conn.execute(
                        "SELECT DISTINCT player_name FROM odds_snapshots "
                        "WHERE tournament_name = ? AND snapshot_at = ?",
                        (odds_tournament_name, snapshot_at),
                    ).fetchall()
                    best_match, best_score = None, 0
                    for r in all_players:
                        s = fuzz.ratio(name.lower(), r["player_name"].lower())
                        if s > best_score and s >= 80:
                            best_score = s
                            best_match = r["player_name"]
                    if best_match:
                        odds_rows = conn.execute(
                            "SELECT odds_value FROM odds_snapshots "
                            "WHERE tournament_name = ? AND snapshot_at = ? AND player_name = ?",
                            (odds_tournament_name, snapshot_at, best_match),
                        ).fetchall()

                if odds_rows:
                    # BetMGMの100000は除外（プレースホルダー値）
                    valid_odds = [r["odds_value"] for r in odds_rows if r["odds_value"] < 50000]
                    if valid_odds:
                        median_odds = sorted(valid_odds)[len(valid_odds) // 2]
                        # American odds → implied probability
                        if median_odds > 0:
                            implied_probs[name] = 100.0 / (median_odds + 100.0)
                        else:
                            implied_probs[name] = abs(median_odds) / (abs(median_odds) + 100.0)

            if len(implied_probs) >= 3:
                # 高い確率 = 高スコア
                scaled = _min_max_scale(implied_probs)
                result: dict[str, float] = {}
                for name in player_names:
                    result[name] = scaled.get(name, 50.0)
                return result, True

        # フォールバック: WGRプロキシ
        return self.compute_ranking_signal(group_players), False

    def compute_ranking_signal(
        self, group_players: list[sqlite3.Row]
    ) -> dict[str, float]:
        """WGRをグループ内0-100スコアに変換。低WGR=高スコア。"""
        wgrs: dict[str, float] = {}
        for p in group_players:
            wgr = p["current_wgr"]
            wgrs[p["player_name"]] = wgr if wgr and wgr > 0 else 500
        return _min_max_scale(wgrs, invert=True)

    #-----Signal 2: Stats (SG from Previous Year)-----

    def compute_stats_signal(
        self, group_players: list[sqlite3.Row], year: int
    ) -> dict[str, float | None]:
        """前年のSG統計からグループ内スコアを算出。"""
        player_names = [p["player_name"] for p in group_players]
        stats_list = self.stats_db.get_player_stats_for_year(year - 1, player_names)

        # 選手名→PlayerStatsマッピング
        stats_by_name: dict[str, object] = {}
        for ps in stats_list:
            stats_by_name[ps.name] = ps

        raw_scores: dict[str, float] = {}
        for name in player_names:
            ps = stats_by_name.get(name)
            if ps is None or not ps.has_sufficient_data(2):
                continue

            score = 0.0
            total_weight = 0.0
            stat_map = {
                "sg_approach": ps.sg_approach,
                "sg_off_tee": ps.sg_off_tee,
                "sg_tee_to_green": ps.sg_tee_to_green,
                "gir_pct": ps.greens_in_regulation_pct,
                "scoring_average": ps.scoring_average,
                "scrambling_pct": ps.scrambling_pct,
            }
            for stat_name, val in stat_map.items():
                if val is not None:
                    w = self.stat_weights.get(stat_name, 0)
                    # scoring_averageは低い方が良い → 反転
                    if stat_name == "scoring_average":
                        score -= val * w
                    else:
                        score += val * w
                    total_weight += w

            if total_weight > 0:
                raw_scores[name] = score / total_weight

        if not raw_scores:
            return {name: None for name in player_names}

        scaled = _min_max_scale(raw_scores)
        result: dict[str, float | None] = {}
        for name in player_names:
            result[name] = scaled.get(name)
        return result

    #-----Signal 3: Course Fit (Tournament History)-----

    def compute_fit_signal(
        self, group_players: list[sqlite3.Row], tournament_name: str
    ) -> dict[str, float | None]:
        """tournament_historyから当該大会の過去成績をスコア化。"""
        player_names = [p["player_name"] for p in group_players]
        raw_scores: dict[str, float] = {}

        for p in group_players:
            history_str = p["tournament_history"]
            if not history_str:
                continue

            try:
                history = json.loads(history_str)
            except (json.JSONDecodeError, TypeError):
                continue

            if not history:
                continue

            # 全大会の過去結果から順位を集計
            # tournament_historyはその大会CSV生成時点の過去成績
            positions = []
            for tourney_name, finish_str in history.items():
                pos = _parse_finish_position(finish_str)
                if pos is not None:
                    positions.append(pos)

            if positions:
                # 平均順位（低い方が良い）
                avg_pos = sum(positions) / len(positions)
                raw_scores[p["player_name"]] = avg_pos

        if not raw_scores:
            return {name: None for name in player_names}

        # 低順位=高スコア
        scaled = _min_max_scale(raw_scores, invert=True)
        result: dict[str, float | None] = {}
        for name in player_names:
            result[name] = scaled.get(name)
        return result

    #-----Signal 4: Crowd Wisdom-----

    def compute_crowd_signal(
        self, group_players: list[sqlite3.Row], pk: int, conn: sqlite3.Connection
    ) -> dict[str, float | None]:
        """pick'emピックデータからcrowdスコアを算出。"""
        player_names = [p["player_name"] for p in group_players]

        # この大会のpickem_tournament_idを取得
        row = conn.execute(
            "SELECT id FROM pickem_tournaments WHERE pk = ?", (pk,)
        ).fetchone()
        if not row:
            return {name: None for name in player_names}

        t_id = row["id"]

        # この大会のピック集計
        picks_rows = conn.execute(
            "SELECT picked_player, COUNT(*) as cnt FROM pickem_picks "
            "WHERE pickem_tournament_id = ? GROUP BY LOWER(picked_player)",
            (t_id,),
        ).fetchall()

        if not picks_rows:
            return {name: None for name in player_names}

        # 選手ごとのピック数をファジーマッチで集計
        pick_counts: dict[str, int] = {}
        for name in player_names:
            target = name.lower().strip()
            total = 0
            for pr in picks_rows:
                picked = pr["picked_player"].lower().strip()
                score = max(
                    fuzz.ratio(target, picked),
                    fuzz.partial_ratio(target, picked),
                )
                if score >= 80:
                    total += pr["cnt"]
            pick_counts[name] = total

        total_picks = sum(pick_counts.values())
        if total_picks == 0:
            return {name: None for name in player_names}

        scaled = _min_max_scale(pick_counts)
        result: dict[str, float | None] = {}
        for name in player_names:
            val = scaled.get(name)
            result[name] = val if pick_counts.get(name, 0) > 0 else None
        return result

    #-----Signal 5: Tournament Affinity-----

    def compute_affinity_signal(
        self, group_players: list[sqlite3.Row], tournament_name: str
    ) -> dict[str, float | None]:
        """当該大会への親和性スコアを算出。

        tournament_historyから当該大会のみの過去出場回数＋成績を抽出。
        出場回数が多い・成績が良い → 高スコア。
        """
        player_names = [p["player_name"] for p in group_players]
        raw_scores: dict[str, float] = {}

        for p in group_players:
            history_str = p["tournament_history"]
            if not history_str:
                continue

            try:
                history = json.loads(history_str)
            except (json.JSONDecodeError, TypeError):
                continue

            if not history:
                continue

            # 当該大会名にマッチするキーを検索
            target = tournament_name.lower().strip()
            matched_finishes: list[int] = []
            for key, finish_str in history.items():
                score = max(
                    fuzz.ratio(target, key.lower()),
                    fuzz.partial_ratio(target, key.lower()),
                )
                if score >= 70:
                    pos = _parse_finish_position(finish_str)
                    if pos is not None:
                        matched_finishes.append(pos)

            if not matched_finishes:
                continue

            # 出場回数ボーナス + 平均順位の組み合わせスコア
            # 出場回数: 多いほど高い（1回=1.0, 2回=1.2, 3回=1.4, 4回+=1.5）
            n_appearances = len(matched_finishes)
            appearance_bonus = min(1.0 + (n_appearances - 1) * 0.2, 1.5)

            # 平均順位: 低いほど良い
            avg_pos = sum(matched_finishes) / n_appearances

            # 複合スコア: 低い平均順位 × 高い出場ボーナス = 良いスコア
            # ※最終的にmin-maxスケーリングで0-100にするので方向だけ合わせる
            # avg_posが低いほど良い → 100/avg_posで反転
            raw_scores[p["player_name"]] = (100.0 / max(avg_pos, 1.0)) * appearance_bonus

        if not raw_scores:
            return {name: None for name in player_names}

        scaled = _min_max_scale(raw_scores)
        result: dict[str, float | None] = {}
        for name in player_names:
            result[name] = scaled.get(name)
        return result

    #-----Backtest Execution-----

    def run_single_tournament(
        self, pk: int, conn: sqlite3.Connection
    ) -> TournamentBacktestResult | None:
        """1大会のバックテストを実行。"""
        # 大会情報取得
        t_row = conn.execute(
            "SELECT id, name FROM pickem_tournaments WHERE pk = ?", (pk,)
        ).fetchone()
        if not t_row or not t_row["name"]:
            return None

        tournament_name = t_row["name"]
        t_id = t_row["id"]

        # PGA大会にリンク
        link = self.link_tournament(pk, tournament_name)
        if link is None:
            return None

        pga_tid, pga_year = link

        # フィールドデータ取得
        field_players = conn.execute(
            "SELECT * FROM pickem_field_players WHERE pickem_tournament_id = ? "
            "ORDER BY group_id, player_name",
            (t_id,),
        ).fetchall()

        if not field_players:
            return None

        # PGA結果取得（グラウンドトゥルース）
        results_lookup = self.get_pga_results(pga_tid, pga_year)
        if len(results_lookup) < 10:
            return None

        group_results = self.compute_group_results(field_players, results_lookup)

        # グループ別に処理
        groups: dict[int, list[sqlite3.Row]] = {}
        for fp in field_players:
            gid = fp["group_id"]
            if gid not in groups:
                groups[gid] = []
            groups[gid].append(fp)

        # 実オッズ有無を大会単位で判定（大会開始前のスナップショットのみ）
        # 大会名ファジーマッチ（compute_odds_signalと同じロジック）
        odds_tname = tournament_name
        exact_cnt = conn.execute(
            "SELECT COUNT(*) as c FROM odds_snapshots WHERE tournament_name = ?",
            (tournament_name,),
        ).fetchone()["c"]
        if exact_cnt == 0:
            all_onames = conn.execute(
                "SELECT DISTINCT tournament_name FROM odds_snapshots"
            ).fetchall()
            best_n, best_s = None, 0
            for r in all_onames:
                s = fuzz.ratio(tournament_name.lower(), r["tournament_name"].lower())
                if s > best_s and s >= 70:
                    best_s = s
                    best_n = r["tournament_name"]
            if best_n:
                odds_tname = best_n

        start_row = conn.execute(
            "SELECT tournament_start_date FROM odds_snapshots "
            "WHERE tournament_name = ? AND tournament_start_date IS NOT NULL LIMIT 1",
            (odds_tname,),
        ).fetchone()

        if start_row and start_row["tournament_start_date"]:
            cutoff = start_row["tournament_start_date"] + "T12:00:00"
            has_real_odds = conn.execute(
                "SELECT COUNT(*) as c FROM odds_snapshots "
                "WHERE tournament_name = ? AND snapshot_at < ?",
                (odds_tname, cutoff),
            ).fetchone()["c"] > 0
        else:
            has_real_odds = conn.execute(
                "SELECT COUNT(*) as c FROM odds_snapshots WHERE tournament_name = ?",
                (odds_tname,),
            ).fetchone()["c"] > 0

        result = TournamentBacktestResult(
            pk=pk,
            tournament_name=tournament_name,
            pga_year=pga_year,
            pga_tournament_id=pga_tid,
            num_groups=len(groups),
            num_groups_with_results=0,
            has_real_odds=has_real_odds,
        )

        for gid, gplayers in sorted(groups.items()):
            gresults = group_results.get(gid, {})

            # 有効な順位を持つ選手が3人未満のグループはスキップ
            valid_positions = [p for p in gresults.values() if p < 999]
            if len(valid_positions) < 3:
                continue

            result.num_groups_with_results += 1

            # グループ勝者・Top2を特定
            sorted_by_pos = sorted(gresults.items(), key=lambda x: x[1])
            winner_name = sorted_by_pos[0][0] if sorted_by_pos else None
            top2_names = {n for n, _ in sorted_by_pos[:2]}

            # 5信号を計算（実オッズ優先、なければWGRフォールバック）
            ranking_sig, _ = self.compute_odds_signal(
                gplayers, tournament_name, conn
            )
            stats_sig = self.compute_stats_signal(gplayers, pga_year)
            fit_sig = self.compute_fit_signal(gplayers, tournament_name)
            crowd_sig = self.compute_crowd_signal(gplayers, pk, conn)
            affinity_sig = self.compute_affinity_signal(gplayers, tournament_name)

            # 観測データ生成
            for p in gplayers:
                name = p["player_name"]
                pos = gresults.get(name, 999)
                obs = GroupObservation(
                    pk=pk,
                    group_id=gid,
                    player_name=name,
                    ranking_signal=ranking_sig.get(name),
                    stats_signal=stats_sig.get(name),
                    fit_signal=fit_sig.get(name),
                    crowd_signal=crowd_sig.get(name),
                    affinity_signal=affinity_sig.get(name),
                    pga_position=pos if pos < 999 else None,
                    is_group_winner=(name == winner_name),
                    is_group_top2=(name in top2_names),
                )
                result.observations.append(obs)

        return result if result.num_groups_with_results > 0 else None

    def run_backtest(
        self, pk_min: int = 11, pk_max: int = 410
    ) -> BacktestResult:
        """バッチバックテスト実行。"""
        conn = get_connection()
        result = BacktestResult()

        # 有効なPKリスト取得
        rows = conn.execute(
            "SELECT pk, name FROM pickem_tournaments "
            "WHERE pk >= ? AND pk <= ? AND name IS NOT NULL "
            "ORDER BY pk",
            (pk_min, pk_max),
        ).fetchall()

        total = len(rows)
        linked = 0
        skipped = 0

        print(f"[INFO] Backtest: PK {pk_min}-{pk_max} ({total} tournaments with names)")

        try:
            for i, row in enumerate(rows):
                pk = row["pk"]
                t_result = self.run_single_tournament(pk, conn)

                if t_result is not None:
                    result.tournaments.append(t_result)
                    linked += 1
                    if linked % 20 == 0:
                        print(
                            f"[INFO]   Progress: {i+1}/{total} processed, "
                            f"{linked} linked, {len(result.all_observations())} observations"
                        )
                else:
                    skipped += 1

            print(
                f"[INFO] Backtest complete: {linked}/{total} linked, "
                f"{skipped} skipped, {result.total_groups} groups, "
                f"{result.total_observations} observations"
            )

        finally:
            conn.close()

        return result

    #-----Training Dataset-----

    def build_training_dataset(self, result: BacktestResult) -> pd.DataFrame:
        """全観測をDataFrameに変換。"""
        records = []
        for obs in result.all_observations():
            records.append({
                "pk": obs.pk,
                "group_id": obs.group_id,
                "player_name": obs.player_name,
                "ranking_signal": obs.ranking_signal,
                "stats_signal": obs.stats_signal,
                "fit_signal": obs.fit_signal,
                "crowd_signal": obs.crowd_signal,
                "affinity_signal": obs.affinity_signal,
                "pga_position": obs.pga_position,
                "is_group_winner": int(obs.is_group_winner),
                "is_group_top2": int(obs.is_group_top2),
            })

        df = pd.DataFrame(records)
        n_winners = df["is_group_winner"].sum()
        n_tournaments = df["pk"].nunique()
        print(
            f"[INFO] Training dataset: {len(df)} observations, "
            f"{n_tournaments} tournaments, {n_winners} group winners"
        )
        return df

    #-----ML Optimization-----

    def _evaluate_weights(
        self,
        df: pd.DataFrame,
        w_rank: float,
        w_stats: float,
        w_fit: float,
        w_crowd: float,
        w_affinity: float = 0.0,
    ) -> tuple[float, float]:
        """指定ウェイトでの勝者的中率とTop2的中率を計算。

        Returns:
            (winner_accuracy, top2_accuracy)
        """
        # 欠損信号は50（ニュートラル）で埋める
        score = (
            w_rank * df["ranking_signal"].fillna(50)
            + w_stats * df["stats_signal"].fillna(50)
            + w_fit * df["fit_signal"].fillna(50)
            + w_crowd * df["crowd_signal"].fillna(50)
            + w_affinity * df["affinity_signal"].fillna(50)
        )
        df = df.copy()
        df["score"] = score

        correct_winner = 0
        correct_top2 = 0
        total_groups = 0

        for (pk, gid), gdf in df.groupby(["pk", "group_id"]):
            if gdf["is_group_winner"].sum() == 0:
                continue
            total_groups += 1

            # 予測: スコア最高の選手
            sorted_gdf = gdf.sort_values("score", ascending=False)
            predicted_winner = sorted_gdf.iloc[0]["player_name"]
            predicted_top2 = set(sorted_gdf.iloc[:2]["player_name"])

            actual_winner_row = gdf[gdf["is_group_winner"] == 1]
            if actual_winner_row.empty:
                continue

            actual_winner = actual_winner_row.iloc[0]["player_name"]

            if predicted_winner == actual_winner:
                correct_winner += 1
            if actual_winner in predicted_top2:
                correct_top2 += 1

        if total_groups == 0:
            return 0.0, 0.0

        return correct_winner / total_groups, correct_top2 / total_groups

    def grid_search(self, df: pd.DataFrame) -> OptimalWeights:
        """グリッドサーチで最適ウェイトを探索（5信号）。"""
        print("[INFO] Grid search: exploring 5-signal weight combinations...")

        best_accuracy = 0.0
        best_top2 = 0.0
        best_weights = (0.30, 0.25, 0.15, 0.15, 0.15)
        step = 0.05
        tested = 0

        for w_rank in np.arange(0.05, 0.50 + step, step):
            for w_stats in np.arange(0.05, 0.50 + step, step):
                for w_fit in np.arange(0.00, 0.40 + step, step):
                    for w_crowd in np.arange(0.00, 0.40 + step, step):
                        w_affinity = round(1.0 - w_rank - w_stats - w_fit - w_crowd, 2)
                        if w_affinity < 0 or w_affinity > 0.40:
                            continue
                        tested += 1
                        winner_acc, top2_acc = self._evaluate_weights(
                            df, w_rank, w_stats, w_fit, w_crowd, w_affinity
                        )
                        if winner_acc > best_accuracy:
                            best_accuracy = winner_acc
                            best_top2 = top2_acc
                            best_weights = (
                                round(w_rank, 2),
                                round(w_stats, 2),
                                round(w_fit, 2),
                                round(w_crowd, 2),
                                round(w_affinity, 2),
                            )

        print(f"[INFO] Grid search: tested {tested} combinations")
        print(
            f"[INFO] Best: ranking={best_weights[0]}, stats={best_weights[1]}, "
            f"fit={best_weights[2]}, crowd={best_weights[3]}, affinity={best_weights[4]} "
            f"→ winner={best_accuracy:.1%}, top2={best_top2:.1%}"
        )

        return OptimalWeights(
            ranking=best_weights[0],
            stats=best_weights[1],
            course_fit=best_weights[2],
            crowd=best_weights[3],
            affinity=best_weights[4],
            accuracy_winner=best_accuracy,
            accuracy_top2=best_top2,
            method="grid_search",
        )

    def gradient_boosting_analysis(self, df: pd.DataFrame) -> dict:
        """GBTで非線形関係を学習し、特徴量重要度を抽出。"""
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import cross_val_score

        features = ["ranking_signal", "stats_signal", "fit_signal", "crowd_signal", "affinity_signal"]

        # ranking + stats が必須
        df_valid = df.dropna(subset=["ranking_signal", "stats_signal"]).copy()
        if len(df_valid) < 100:
            print("[WARN] Insufficient data for GBT analysis")
            return {}

        X = df_valid[features].fillna(50)
        y = df_valid["is_group_winner"]

        gbt = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=3,
            min_samples_leaf=20,
            learning_rate=0.1,
            random_state=42,
        )

        cv_scores = cross_val_score(gbt, X, y, cv=5, scoring="accuracy")
        gbt.fit(X, y)

        importance = dict(zip(features, gbt.feature_importances_))

        print(f"[INFO] GBT CV accuracy: {cv_scores.mean():.3f} (+/- {cv_scores.std():.3f})")
        print("[INFO] GBT Feature importance:")
        for feat, imp in sorted(importance.items(), key=lambda x: -x[1]):
            bar = "#" * int(imp * 40)
            print(f"  {feat:20s}: {imp:.3f} {bar}")

        return {
            "feature_importance": importance,
            "cv_accuracy": float(cv_scores.mean()),
            "cv_std": float(cv_scores.std()),
        }

    def walk_forward_validation(
        self, result: BacktestResult, train_pct: float = 0.7
    ) -> dict:
        """ウォークフォワード検証（時系列分割）。"""
        all_pks = sorted(set(t.pk for t in result.tournaments))
        if len(all_pks) < 20:
            print("[WARN] Insufficient tournaments for walk-forward validation")
            return {}

        split_idx = int(len(all_pks) * train_pct)
        train_pks = set(all_pks[:split_idx])
        test_pks = set(all_pks[split_idx:])

        df = self.build_training_dataset(result)
        df_train = df[df["pk"].isin(train_pks)]
        df_test = df[df["pk"].isin(test_pks)]

        print(
            f"[INFO] Walk-forward: train={len(df_train)} obs ({len(train_pks)} tournaments), "
            f"test={len(df_test)} obs ({len(test_pks)} tournaments)"
        )

        # Trainでグリッドサーチ
        print("[INFO] Optimizing on train set...")
        optimal = self.grid_search(df_train)

        # Testで評価
        test_winner, test_top2 = self._evaluate_weights(
            df_test, optimal.ranking, optimal.stats, optimal.course_fit,
            optimal.crowd, optimal.affinity
        )

        # デフォルトウェイトとの比較
        default_winner, default_top2 = self._evaluate_weights(
            df_test, 0.35, 0.25, 0.15, 0.15, 0.10
        )

        # 均等ウェイト
        random_winner, random_top2 = self._evaluate_weights(
            df_test, 0.20, 0.20, 0.20, 0.20, 0.20
        )

        print(f"\n[INFO] === Walk-Forward Validation Results ===")
        print(f"  Train period: PK {min(train_pks)}-{max(train_pks)} ({len(train_pks)} tournaments)")
        print(f"  Test period:  PK {min(test_pks)}-{max(test_pks)} ({len(test_pks)} tournaments)")
        print(f"  Optimal weights: R={optimal.ranking} S={optimal.stats} "
              f"F={optimal.course_fit} C={optimal.crowd} A={optimal.affinity}")
        print(f"  Test Winner Acc (optimal):  {test_winner:.1%}")
        print(f"  Test Winner Acc (default):  {default_winner:.1%}")
        print(f"  Test Winner Acc (equal):    {random_winner:.1%}")
        print(f"  Test Top-2 Acc (optimal):   {test_top2:.1%}")
        print(f"  Test Top-2 Acc (default):   {default_top2:.1%}")

        # 過学習チェック
        train_winner, train_top2 = self._evaluate_weights(
            df_train, optimal.ranking, optimal.stats, optimal.course_fit,
            optimal.crowd, optimal.affinity
        )
        overfit = train_winner - test_winner
        print(f"  Overfit check: train={train_winner:.1%}, test={test_winner:.1%}, "
              f"gap={overfit:.1%}")

        return {
            "train_pks": sorted(train_pks),
            "test_pks": sorted(test_pks),
            "optimal_weights": optimal,
            "test_winner_accuracy": test_winner,
            "test_top2_accuracy": test_top2,
            "default_winner_accuracy": default_winner,
            "default_top2_accuracy": default_top2,
            "train_winner_accuracy": train_winner,
            "overfit_gap": overfit,
        }

    #-----Report-----

    def format_backtest_report(
        self, result: BacktestResult, df: pd.DataFrame, optimal: OptimalWeights | None = None
    ) -> str:
        """バックテストレポートを生成。"""
        lines = []
        lines.append("=" * 60)
        lines.append("  HISTORICAL BACKTEST REPORT")
        lines.append("=" * 60)

        all_pks = sorted(set(t.pk for t in result.tournaments))
        lines.append(f"  Period: PK {min(all_pks)}-{max(all_pks)} "
                     f"({len(result.tournaments)} tournaments)")
        lines.append(f"  Groups evaluated: {result.total_groups}")
        lines.append(f"  Observations: {result.total_observations}")

        # オッズデータ状況
        real_odds_count = sum(1 for t in result.tournaments if t.has_real_odds)
        wgr_count = len(result.tournaments) - real_odds_count
        lines.append(f"\n  Odds Data:")
        lines.append(f"    Real odds:  {real_odds_count} tournaments")
        lines.append(f"    WGR proxy:  {wgr_count} tournaments")

        # 信号カバレッジ
        total = len(df)
        r_cov = df["ranking_signal"].notna().sum()
        s_cov = df["stats_signal"].notna().sum()
        f_cov = df["fit_signal"].notna().sum()
        c_cov = df["crowd_signal"].notna().sum()
        a_cov = df["affinity_signal"].notna().sum()
        lines.append(f"\n  Signal Coverage:")
        lines.append(f"    Odds/Ranking:     {r_cov:>6} / {total} ({r_cov/total:.0%})")
        lines.append(f"    Stats (SG):       {s_cov:>6} / {total} ({s_cov/total:.0%})")
        lines.append(f"    Course Fit:       {f_cov:>6} / {total} ({f_cov/total:.0%})")
        lines.append(f"    Crowd:            {c_cov:>6} / {total} ({c_cov/total:.0%})")
        lines.append(f"    Affinity:         {a_cov:>6} / {total} ({a_cov/total:.0%})")

        # デフォルトウェイト精度
        default_w, default_t2 = self._evaluate_weights(df, 0.35, 0.25, 0.15, 0.15, 0.10)
        lines.append(f"\n  Default Weights (0.35/0.25/0.15/0.15/0.10):")
        lines.append(f"    Winner accuracy: {default_w:.1%}")
        lines.append(f"    Top-2 accuracy:  {default_t2:.1%}")

        # 各信号の単独精度
        lines.append(f"\n  Single Signal Accuracy:")
        for name, w in [("Ranking only", (1, 0, 0, 0, 0)),
                        ("Stats only", (0, 1, 0, 0, 0)),
                        ("Fit only", (0, 0, 1, 0, 0)),
                        ("Crowd only", (0, 0, 0, 1, 0)),
                        ("Affinity only", (0, 0, 0, 0, 1))]:
            acc, t2 = self._evaluate_weights(df, *w)
            lines.append(f"    {name:15s}: winner={acc:.1%}, top2={t2:.1%}")

        if optimal:
            lines.append(f"\n  OPTIMAL WEIGHTS ({optimal.method}):")
            lines.append(f"    Ranking:    {optimal.ranking}")
            lines.append(f"    Stats:      {optimal.stats}")
            lines.append(f"    Course Fit: {optimal.course_fit}")
            lines.append(f"    Crowd:      {optimal.crowd}")
            lines.append(f"    Affinity:   {optimal.affinity}")
            lines.append(f"    Winner accuracy: {optimal.accuracy_winner:.1%}")
            lines.append(f"    Top-2 accuracy:  {optimal.accuracy_top2:.1%}")

        random_w, random_t2 = self._evaluate_weights(df, 0.20, 0.20, 0.20, 0.20, 0.20)
        lines.append(f"\n  Random Baseline (1/7): winner={1/7:.1%}, top2={2/7:.1%}")
        lines.append(f"  Equal Weights:         winner={random_w:.1%}, top2={random_t2:.1%}")

        lines.append("=" * 60)
        return "\n".join(lines)

    #-----DB Save-----

    def save_backtest_results(
        self, result: BacktestResult, optimal: OptimalWeights | None = None
    ) -> str:
        """バックテスト結果をDBに保存。

        Returns:
            run_id
        """
        conn = get_connection()
        run_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()

        try:
            # 観測データ保存
            for obs in result.all_observations():
                conn.execute(
                    "INSERT INTO backtest_results "
                    "(run_id, pk, group_id, player_name, ranking_signal, stats_signal, "
                    "fit_signal, crowd_signal, affinity_signal, pga_position, "
                    "is_group_winner, is_group_top2, computed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id, obs.pk, obs.group_id, obs.player_name,
                        obs.ranking_signal, obs.stats_signal,
                        obs.fit_signal, obs.crowd_signal, obs.affinity_signal,
                        obs.pga_position, int(obs.is_group_winner),
                        int(obs.is_group_top2), now,
                    ),
                )

            # ラン情報保存
            all_pks = sorted(set(t.pk for t in result.tournaments))
            conn.execute(
                "INSERT INTO backtest_runs "
                "(run_id, pk_min, pk_max, total_tournaments, total_groups, "
                "total_observations, optimal_w_ranking, optimal_w_stats, "
                "optimal_w_fit, optimal_w_crowd, optimal_w_affinity, "
                "accuracy_winner, accuracy_top2, method, computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, min(all_pks), max(all_pks),
                    len(result.tournaments), result.total_groups,
                    result.total_observations,
                    optimal.ranking if optimal else None,
                    optimal.stats if optimal else None,
                    optimal.course_fit if optimal else None,
                    optimal.crowd if optimal else None,
                    optimal.affinity if optimal else None,
                    optimal.accuracy_winner if optimal else None,
                    optimal.accuracy_top2 if optimal else None,
                    optimal.method if optimal else "backtest_only",
                    now,
                ),
            )

            conn.commit()
            print(f"[INFO] Saved backtest run '{run_id}' to database")
            return run_id

        finally:
            conn.close()


    #-----Verify-----

    def load_latest_weights(self, conn: sqlite3.Connection) -> OptimalWeights | None:
        """DBから最新の最適ウェイトを取得。"""
        row = conn.execute(
            "SELECT * FROM backtest_runs "
            "WHERE optimal_w_ranking IS NOT NULL "
            "ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return OptimalWeights(
            ranking=row["optimal_w_ranking"],
            stats=row["optimal_w_stats"],
            course_fit=row["optimal_w_fit"],
            crowd=row["optimal_w_crowd"],
            affinity=row["optimal_w_affinity"] or 0.0,
            accuracy_winner=row["accuracy_winner"],
            accuracy_top2=row["accuracy_top2"],
            method=row["method"],
        )

    def verify_tournament(self, pk: int) -> str:
        """1大会のグループ別予測 vs 実結果の詳細検証レポート。"""
        conn = get_connection()
        try:
            # 最適ウェイト読み込み
            weights = self.load_latest_weights(conn)
            if not weights:
                w_r, w_s, w_f, w_c, w_a = 0.35, 0.25, 0.15, 0.15, 0.10
                weight_src = "default"
            else:
                w_r = weights.ranking
                w_s = weights.stats
                w_f = weights.course_fit
                w_c = weights.crowd
                w_a = weights.affinity
                weight_src = weights.method

            # バックテスト実行（1大会）
            t_result = self.run_single_tournament(pk, conn)
            if not t_result:
                return f"[ERROR] PK {pk}: データなし、リンク失敗、または結果不足"

            # オッズカットオフ情報取得（大会名ファジーマッチ）
            odds_tname_v = t_result.tournament_name
            exact_v = conn.execute(
                "SELECT COUNT(*) as c FROM odds_snapshots WHERE tournament_name = ?",
                (t_result.tournament_name,),
            ).fetchone()["c"]
            if exact_v == 0:
                all_on = conn.execute(
                    "SELECT DISTINCT tournament_name FROM odds_snapshots"
                ).fetchall()
                best_vn, best_vs = None, 0
                for r in all_on:
                    s = fuzz.ratio(t_result.tournament_name.lower(), r["tournament_name"].lower())
                    if s > best_vs and s >= 70:
                        best_vs = s
                        best_vn = r["tournament_name"]
                if best_vn:
                    odds_tname_v = best_vn

            start_row = conn.execute(
                "SELECT tournament_start_date FROM odds_snapshots "
                "WHERE tournament_name = ? AND tournament_start_date IS NOT NULL LIMIT 1",
                (odds_tname_v,),
            ).fetchone()
            cutoff_info = ""
            snapshot_info = ""
            if start_row and start_row["tournament_start_date"]:
                start_date = start_row["tournament_start_date"]
                cutoff = start_date + "T12:00:00"
                cutoff_info = f"cutoff: {start_date} 12:00 JST"
                snap = conn.execute(
                    "SELECT snapshot_at FROM odds_snapshots "
                    "WHERE tournament_name = ? AND snapshot_at < ? "
                    "ORDER BY snapshot_at DESC LIMIT 1",
                    (odds_tname_v, cutoff),
                ).fetchone()
                snapshot_info = snap["snapshot_at"] if snap else "NONE"

            # グループ別詳細レポート生成
            lines: list[str] = []
            lines.append("=" * 70)
            lines.append(f"  PREDICTION VERIFICATION: {t_result.tournament_name} (PK {pk})")
            lines.append("=" * 70)
            lines.append(f"  PGA Year: {t_result.pga_year}")

            if t_result.has_real_odds:
                lines.append(f"  Odds Data: Real odds ({cutoff_info})")
                lines.append(f"  Snapshot used: {snapshot_info}")
            else:
                lines.append(f"  Odds Data: WGR proxy (no valid pre-tournament odds)")

            lines.append(f"  Weights ({weight_src}): R={w_r} S={w_s} F={w_f} C={w_c} A={w_a}")
            lines.append("")

            # グループごとに集計
            groups: dict[int, list[GroupObservation]] = {}
            for obs in t_result.observations:
                if obs.group_id not in groups:
                    groups[obs.group_id] = []
                groups[obs.group_id].append(obs)

            total_groups = 0
            correct_winner = 0
            correct_top2 = 0

            for gid in sorted(groups.keys()):
                obs_list = groups[gid]

                # スコア計算
                scored: list[tuple[str, float, GroupObservation]] = []
                for obs in obs_list:
                    r = obs.ranking_signal if obs.ranking_signal is not None else 50.0
                    s = obs.stats_signal if obs.stats_signal is not None else 50.0
                    f = obs.fit_signal if obs.fit_signal is not None else 50.0
                    c = obs.crowd_signal if obs.crowd_signal is not None else 50.0
                    a = obs.affinity_signal if obs.affinity_signal is not None else 50.0
                    score = w_r * r + w_s * s + w_f * f + w_c * c + w_a * a
                    scored.append((obs.player_name, score, obs))

                scored.sort(key=lambda x: x[1], reverse=True)

                # 実際の勝者とTop2
                actual_sorted = sorted(
                    [o for o in obs_list if o.pga_position is not None],
                    key=lambda o: o.pga_position,
                )
                if len(actual_sorted) < 2:
                    continue

                total_groups += 1
                actual_winner = actual_sorted[0].player_name
                actual_top2 = {actual_sorted[0].player_name, actual_sorted[1].player_name}
                predicted_winner = scored[0][0]
                predicted_top2 = {scored[0][0], scored[1][0]}

                is_hit = predicted_winner == actual_winner
                is_top2 = predicted_winner in actual_top2
                if is_hit:
                    correct_winner += 1
                if is_top2:
                    correct_top2 += 1

                # グループ詳細出力
                lines.append(f"  Group {gid}:")
                lines.append(f"    {'#':<3} {'Player':<24} {'Score':>6}  {'Pred':>4}  "
                             f"{'Actual':>6}  Result")

                for rank, (name, score, obs) in enumerate(scored, 1):
                    # 実順位（グループ内）
                    actual_rank = ""
                    for ar, ao in enumerate(actual_sorted, 1):
                        if ao.player_name == name:
                            actual_rank = f"{ar}"
                            break

                    pred_mark = "<<<" if rank == 1 else ""
                    hit_mark = ""
                    if rank == 1:
                        hit_mark = "HIT" if is_hit else "MISS"

                    pos_str = f"T{obs.pga_position}" if obs.pga_position else "-"
                    lines.append(
                        f"    {rank:<3} {name:<24} {score:>6.1f}  {pred_mark:>4}  "
                        f"{actual_rank:>3}({pos_str:<4})  {hit_mark}"
                    )

                result_mark = "HIT" if is_hit else "MISS"
                lines.append(f"    -> Predicted: {predicted_winner}")
                lines.append(f"       Actual:    {actual_winner} | {result_mark}")
                lines.append("")

            # サマリー
            lines.append("-" * 70)
            if total_groups > 0:
                w_pct = correct_winner / total_groups
                t2_pct = correct_top2 / total_groups
                lines.append(f"  Winner:  {correct_winner}/{total_groups} correct ({w_pct:.1%})")
                lines.append(f"  Top-2:   {correct_top2}/{total_groups} correct ({t2_pct:.1%})")
            else:
                lines.append("  No valid groups found")
            lines.append(f"  Random baseline (1/7): 14.3%")
            lines.append("=" * 70)

            return "\n".join(lines)

        finally:
            conn.close()


#-----CLI-----

def main() -> None:
    """CLIエントリポイント。"""
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Historical Backtester")
    parser.add_argument("--quick", action="store_true", help="PK 400-409 クイックテスト")
    parser.add_argument("--run", action="store_true", help="全PK バックテスト")
    parser.add_argument("--range", nargs=2, type=int, metavar=("MIN", "MAX"),
                        help="PK範囲指定")
    parser.add_argument("--optimize", action="store_true", help="バックテスト + グリッドサーチ")
    parser.add_argument("--validate", action="store_true", help="ウォークフォワード検証")
    parser.add_argument("--report", action="store_true", help="最新結果レポート")
    parser.add_argument("--verify", type=int, metavar="PK",
                        help="指定PKのグループ別予測詳細検証")
    args = parser.parse_args()

    backtester = HistoricalBacktester()

    # PK範囲決定
    if args.quick:
        pk_min, pk_max = 400, 409
    elif args.range:
        pk_min, pk_max = args.range
    else:
        pk_min, pk_max = 11, 410

    if args.verify:
        report = backtester.verify_tournament(args.verify)
        print(f"\n{report}")
        return

    if args.report:
        # 最新のバックテスト結果を表示
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM backtest_runs ORDER BY computed_at DESC LIMIT 1"
            ).fetchone()
            if row:
                print(f"[INFO] Latest backtest run: {row['run_id']}")
                print(f"  PK range: {row['pk_min']}-{row['pk_max']}")
                print(f"  Tournaments: {row['total_tournaments']}")
                print(f"  Groups: {row['total_groups']}")
                print(f"  Observations: {row['total_observations']}")
                if row["optimal_w_ranking"]:
                    print(f"  Optimal: R={row['optimal_w_ranking']} S={row['optimal_w_stats']} "
                          f"F={row['optimal_w_fit']} C={row['optimal_w_crowd']}")
                    print(f"  Winner accuracy: {row['accuracy_winner']:.1%}")
                    print(f"  Top-2 accuracy: {row['accuracy_top2']:.1%}")
                print(f"  Method: {row['method']}")
                print(f"  Computed: {row['computed_at']}")
            else:
                print("[INFO] No backtest runs found")
        finally:
            conn.close()
        return

    # バックテスト実行
    if args.run or args.quick or args.range or args.optimize or args.validate:
        result = backtester.run_backtest(pk_min, pk_max)

        if result.total_observations == 0:
            print("[WARN] No observations generated. Check data linkage.")
            return

        df = backtester.build_training_dataset(result)

        # レポート表示
        optimal = None

        if args.optimize or args.validate:
            if args.validate:
                wf_result = backtester.walk_forward_validation(result)
                if wf_result:
                    optimal = wf_result.get("optimal_weights")
            else:
                optimal = backtester.grid_search(df)

            # GBT分析も実行
            backtester.gradient_boosting_analysis(df)

        report = backtester.format_backtest_report(result, df, optimal)
        print(f"\n{report}")

        # DB保存
        backtester.save_backtest_results(result, optimal)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
