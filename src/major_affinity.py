"""メジャー大会アフィニティ分析モジュール。

メジャー大会 (Masters, PGA Championship, US Open, The Open, Players Championship)
での選手の過去成績に基づいて 0-100 のアフィニティスコアを算出する。

スコアは2層構造:
  1. 大会別スコア: 各メジャー大会ごとの個別適性 (per_tournament_scores)
  2. 総合スコア: 現在の大会を60%、他メジャー全体を40%で統合 (scores)

非メジャー週では全選手 None を返し、Ensemble の自動ウェイト再配分で除外される。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fuzzywuzzy import fuzz


# メジャー大会の tournament_num マッピング（pga_stats.db のIDパターン）
MAJOR_TOURNAMENT_NUMS: dict[str, str] = {
    "Masters Tournament": "014",
    "PGA Championship": "033",
    "THE PLAYERS Championship": "011",
    "The Open Championship": "100",
    "U.S. Open": "026",
}

# 現在の大会 vs 他メジャーの重みバランス
CURRENT_TOURNAMENT_WEIGHT = 0.60
OTHER_MAJORS_WEIGHT = 0.40


def is_major_tournament(tournament_name: str, config: dict | None = None) -> bool:
    """現在の大会がメジャーかどうかを判定。"""
    if not tournament_name:
        return False

    major_names = list(MAJOR_TOURNAMENT_NUMS.keys())
    if config and config.get("tournaments"):
        major_names = config["tournaments"]

    threshold = config.get("fuzzy_threshold", 80) if config else 80

    name_lower = tournament_name.lower().strip()
    for major in major_names:
        if fuzz.partial_ratio(name_lower, major.lower()) >= threshold:
            return True
    return False


def _match_current_major(tournament_name: str) -> str | None:
    """現在の大会名からメジャー大会のキー名を返す。"""
    name_lower = tournament_name.lower().strip()
    best_match = None
    best_score = 0
    for major_name in MAJOR_TOURNAMENT_NUMS:
        score = fuzz.partial_ratio(name_lower, major_name.lower())
        if score > best_score:
            best_score = score
            best_match = major_name
    return best_match if best_score >= 75 else None


@dataclass
class TournamentStats:
    """1選手の特定メジャー大会での成績。"""
    played: int = 0
    made_cuts: int = 0
    avg_position: float | None = None
    cut_rate: float = 0.0
    top_10_count: int = 0
    top_5_count: int = 0
    win_count: int = 0
    recent_avg_position: float | None = None
    recent_played: int = 0
    entries: list[dict] = field(default_factory=list)  # [{year, position, course}]


@dataclass
class PlayerMajorStats:
    """選手のメジャー通算成績 + 大会別成績。"""
    player_name: str
    # 全メジャー通算
    majors_played: int = 0
    made_cuts: int = 0
    avg_position: float | None = None
    cut_rate: float = 0.0
    top_10_count: int = 0
    top_5_count: int = 0
    win_count: int = 0
    recent_avg_position: float | None = None
    recent_majors_played: int = 0
    # 大会別の詳細成績
    by_tournament: dict[str, TournamentStats] = field(default_factory=dict)
    # 大会別スコア (0-100, min-max正規化前のraw値)
    tournament_raw_scores: dict[str, float] = field(default_factory=dict)


class MajorAffinityCalculator:
    """メジャー大会アフィニティスコアの算出。"""

    def __init__(self, config: dict | None = None, current_tournament: str | None = None):
        self.config = config or {}
        self.recent_years = self.config.get("recent_years", 3)
        self.current_major = current_tournament  # マッチしたメジャー名
        self._all_results: list[dict] | None = None

    def _load_all_major_results(self) -> list[dict]:
        """pga_stats.db から全メジャー結果を一括取得。"""
        if self._all_results is not None:
            return self._all_results

        from .pga_stats_db import PGAStatsDB
        db = PGAStatsDB()
        self._all_results = db.get_all_major_results(
            list(MAJOR_TOURNAMENT_NUMS.values())
        )
        return self._all_results

    def _classify_tournament(self, tournament_name: str) -> str | None:
        """DB上のtournament_nameを正規のメジャー名に分類。"""
        name_lower = tournament_name.lower().strip()
        best_match = None
        best_score = 0
        for major_name in MAJOR_TOURNAMENT_NUMS:
            score = fuzz.partial_ratio(name_lower, major_name.lower())
            if score > best_score:
                best_score = score
                best_match = major_name
        return best_match if best_score >= 75 else None

    def compute_player_stats(self, player_name: str) -> PlayerMajorStats:
        """1選手のメジャー通算成績 + 大会別成績を算出。"""
        all_results = self._load_all_major_results()

        # ファジーマッチで選手の結果を抽出
        player_results = []
        for r in all_results:
            if fuzz.ratio(player_name.lower(), r["player_name"].lower()) >= 85:
                player_results.append(r)

        stats = PlayerMajorStats(player_name=player_name)
        if not player_results:
            return stats

        stats.majors_played = len(player_results)
        all_positions = []
        recent_positions = []

        max_year = max(r["year"] for r in player_results)
        recent_cutoff = max_year - self.recent_years

        # 大会別に振り分け
        tournament_results: dict[str, list[dict]] = {}
        for r in player_results:
            classified = self._classify_tournament(r["tournament_name"])
            key = classified or r["tournament_name"]
            if key not in tournament_results:
                tournament_results[key] = []
            tournament_results[key].append(r)

        # 大会別 TournamentStats を構築
        for t_name, results in tournament_results.items():
            ts = TournamentStats()
            ts.played = len(results)
            t_positions = []
            t_recent_positions = []

            for r in results:
                pos = r["position"]
                ts.entries.append({
                    "year": r["year"],
                    "position": pos,
                    "course": r.get("course_name", ""),
                })

                if pos is not None:
                    ts.made_cuts += 1
                    t_positions.append(pos)
                    all_positions.append(pos)
                    if pos <= 10:
                        ts.top_10_count += 1
                        stats.top_10_count += 1
                    if pos <= 5:
                        ts.top_5_count += 1
                        stats.top_5_count += 1
                    if pos == 1:
                        ts.win_count += 1
                        stats.win_count += 1
                    if r["year"] > recent_cutoff:
                        t_recent_positions.append(pos)
                        recent_positions.append(pos)
                        ts.recent_played += 1
                        stats.recent_majors_played += 1
                else:
                    if r["year"] > recent_cutoff:
                        ts.recent_played += 1
                        stats.recent_majors_played += 1

                stats.made_cuts += (1 if pos is not None else 0)

            ts.cut_rate = ts.made_cuts / ts.played if ts.played > 0 else 0
            if t_positions:
                ts.avg_position = sum(t_positions) / len(t_positions)
            if t_recent_positions:
                ts.recent_avg_position = sum(t_recent_positions) / len(t_recent_positions)

            stats.by_tournament[t_name] = ts

        # 全メジャー通算
        stats.cut_rate = stats.made_cuts / stats.majors_played if stats.majors_played > 0 else 0
        if all_positions:
            stats.avg_position = sum(all_positions) / len(all_positions)
        if recent_positions:
            stats.recent_avg_position = sum(recent_positions) / len(recent_positions)

        return stats

    def _compute_tournament_raw_score(self, ts: TournamentStats) -> float:
        """1大会の成績からrawスコアを算出。"""
        if ts.played == 0:
            return 0.0

        score = 0.0

        # 平均順位 (0.25)
        if ts.avg_position is not None and ts.avg_position > 0:
            score += (100.0 / ts.avg_position) * 0.25

        # CUT通過率 (0.15)
        score += ts.cut_rate * 100.0 * 0.15

        # Top-10率 (0.20)
        top10_rate = ts.top_10_count / ts.played
        score += top10_rate * 100.0 * 0.20

        # 勝利ボーナス (0.15)
        score += min(ts.win_count * 20.0, 60.0) * 0.15

        # 経験値 (0.10): この大会に何回出たか
        exp = min(ts.played / 8.0, 1.0) * 50.0
        score += exp * 0.10

        # 直近フォーム (0.15): 最近のこの大会での成績
        if ts.recent_avg_position is not None and ts.recent_avg_position > 0:
            score += (100.0 / ts.recent_avg_position) * 0.15

        return score

    def compute_integrated_score(self, stats: PlayerMajorStats) -> float:
        """現在の大会を重視した統合スコアを算出。

        current_tournament が指定されている場合:
          総合 = current大会スコア * 0.60 + 他メジャー平均 * 0.40

        指定がない場合:
          全メジャー均等平均
        """
        if stats.majors_played == 0:
            return 0.0

        # 各大会のrawスコアを算出
        t_scores: dict[str, float] = {}
        for t_name, ts in stats.by_tournament.items():
            t_scores[t_name] = self._compute_tournament_raw_score(ts)
        stats.tournament_raw_scores = t_scores

        if not t_scores:
            return 0.0

        if self.current_major and self.current_major in t_scores:
            current_score = t_scores[self.current_major]
            other_scores = [s for t, s in t_scores.items() if t != self.current_major]
            other_avg = sum(other_scores) / len(other_scores) if other_scores else 0.0
            return current_score * CURRENT_TOURNAMENT_WEIGHT + other_avg * OTHER_MAJORS_WEIGHT
        else:
            # 現在の大会に出場歴なし → 他メジャー平均のみ（ペナルティ付き）
            all_avg = sum(t_scores.values()) / len(t_scores)
            # 該当大会未出場ペナルティ: 50%に削減
            if self.current_major and self.current_major not in t_scores:
                return all_avg * 0.50
            return all_avg

    def compute_group_scores(
        self, player_names: list[str]
    ) -> tuple[dict[str, float | None], dict[str, PlayerMajorStats], dict[str, dict[str, float | None]]]:
        """グループ内選手のスコアを算出し、min-max正規化。

        Returns:
            (scores, details, per_tournament_scores)
            - scores: {name: 0-100 or None} 統合スコア
            - details: {name: PlayerMajorStats}
            - per_tournament_scores: {name: {tournament_name: 0-100 or None}}
        """
        details: dict[str, PlayerMajorStats] = {}
        raw_scores: dict[str, float] = {}

        for name in player_names:
            pstats = self.compute_player_stats(name)
            details[name] = pstats
            raw = self.compute_integrated_score(pstats)
            if pstats.majors_played > 0:
                raw_scores[name] = raw

        # 統合スコア Min-max 正規化
        scores: dict[str, float | None] = {}
        if raw_scores:
            vals = list(raw_scores.values())
            min_v = min(vals)
            max_v = max(vals)
            rng = max_v - min_v if max_v > min_v else 1.0

            for name in player_names:
                if name in raw_scores:
                    scores[name] = ((raw_scores[name] - min_v) / rng) * 100.0
                else:
                    scores[name] = None
        else:
            scores = {name: None for name in player_names}

        # 大会別スコア Min-max 正規化 (各大会ごとに独立正規化)
        per_tournament_scores: dict[str, dict[str, float | None]] = {}
        all_tournament_names = set()
        for d in details.values():
            all_tournament_names.update(d.tournament_raw_scores.keys())

        for t_name in all_tournament_names:
            t_raws = {}
            for pname in player_names:
                d = details.get(pname)
                if d and t_name in d.tournament_raw_scores and d.tournament_raw_scores[t_name] > 0:
                    t_raws[pname] = d.tournament_raw_scores[t_name]

            if t_raws:
                t_vals = list(t_raws.values())
                t_min = min(t_vals)
                t_max = max(t_vals)
                t_rng = t_max - t_min if t_max > t_min else 1.0

                for pname in player_names:
                    if pname not in per_tournament_scores:
                        per_tournament_scores[pname] = {}
                    if pname in t_raws:
                        per_tournament_scores[pname][t_name] = ((t_raws[pname] - t_min) / t_rng) * 100.0
                    else:
                        per_tournament_scores[pname][t_name] = None

        # 全選手に全大会のキーを保証
        for pname in player_names:
            if pname not in per_tournament_scores:
                per_tournament_scores[pname] = {}
            for t_name in all_tournament_names:
                if t_name not in per_tournament_scores[pname]:
                    per_tournament_scores[pname][t_name] = None

        return scores, details, per_tournament_scores


def compute_major_affinity(
    groups: dict,
    tournament_name: str,
    config: dict | None = None,
) -> dict:
    """メジャーアフィニティ計算のトップレベル関数。

    Returns:
        {
            "is_major": bool,
            "current_major": str|None,
            "scores": {player_name: float|None},
            "per_tournament_scores": {player_name: {tournament: float|None}},
            "player_details": {player_name: PlayerMajorStats},
            "major_history": {player_name: {tournament: [{year, position}, ...]}},
            "field_summary": {...},
        }
    """
    if not is_major_tournament(tournament_name, config):
        all_names = [p.name for players in groups.values() for p in players]
        return {
            "is_major": False,
            "current_major": None,
            "scores": {name: None for name in all_names},
            "per_tournament_scores": {},
            "player_details": {},
            "major_history": {},
            "field_summary": {"total": 0, "experienced": 0, "winners": []},
        }

    current_major = _match_current_major(tournament_name)
    print(f"[INFO] Major tournament detected: {current_major}")
    print(f"[INFO] Scoring weights: current={CURRENT_TOURNAMENT_WEIGHT:.0%}, "
          f"other_majors={OTHER_MAJORS_WEIGHT:.0%}")

    calc = MajorAffinityCalculator(config, current_tournament=current_major)

    all_names = [p.name for players in groups.values() for p in players]
    scores, details, per_tournament_scores = calc.compute_group_scores(all_names)

    # フィールドサマリー
    experienced = sum(1 for d in details.values() if d.majors_played > 0)
    winners = [
        {"name": d.player_name, "wins": d.win_count}
        for d in details.values()
        if d.win_count > 0
    ]
    winners.sort(key=lambda x: x["wins"], reverse=True)

    # 該当大会の出場者/勝者
    current_winners = []
    current_experienced = 0
    if current_major:
        for d in details.values():
            ts = d.by_tournament.get(current_major)
            if ts and ts.played > 0:
                current_experienced += 1
                if ts.win_count > 0:
                    current_winners.append({
                        "name": d.player_name,
                        "wins": ts.win_count,
                        "avg_pos": ts.avg_position,
                    })
        current_winners.sort(key=lambda x: x["wins"], reverse=True)

    # major_history 構築
    major_history: dict[str, dict[str, list]] = {}
    for name, d in details.items():
        if d.by_tournament:
            major_history[name] = {}
            for t_name, ts in d.by_tournament.items():
                major_history[name][t_name] = ts.entries

    with_score = sum(1 for v in scores.values() if v is not None)
    print(f"[OK] Major affinity: {with_score}/{len(all_names)} players scored, "
          f"{len(winners)} major winners, "
          f"{current_experienced} with {current_major} history")

    return {
        "is_major": True,
        "current_major": current_major,
        "scores": scores,
        "per_tournament_scores": per_tournament_scores,
        "player_details": details,
        "major_history": major_history,
        "field_summary": {
            "total": len(all_names),
            "experienced": experienced,
            "winners": winners,
            "current_major": current_major,
            "current_experienced": current_experienced,
            "current_winners": current_winners,
        },
    }
