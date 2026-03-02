"""コースフィット予測モデル。

重回帰分析（OLS）でコース特性を数値化し、
K-Meansクラスタリングで選手タイプを分類。
選手ごとのコースフィットスコア（0-100）を算出。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import statsmodels.api as sm
import yaml

from .pga_stats_db import PGAStatsDB


#-----Constants-----

STAT_FEATURES = [
    "sg_approach", "sg_off_tee", "sg_tee_to_green",
    "sg_putting", "sg_around_green", "gir_pct",
    "driving_distance", "driving_accuracy_pct",
    "scrambling_pct", "scoring_average",
]

# K-Meansクラスタリング用特徴量（8次元）
CLUSTER_FEATURES = [
    "sg_off_tee", "driving_distance",
    "sg_approach", "gir_pct",
    "sg_putting", "sg_around_green",
    "scrambling_pct", "driving_accuracy_pct",
]

# クラスタタイプ名の判定基準（中心ベクトルの特徴から自動命名）
PLAYER_TYPES = {
    "Power Hitter": ["sg_off_tee", "driving_distance"],
    "Approach Specialist": ["sg_approach", "gir_pct"],
    "Short Game Wizard": ["sg_putting", "sg_around_green", "scrambling_pct"],
    "All-Rounder": [],  # デフォルト
}

MIN_SAMPLES_FOR_REGRESSION = 30


#-----Data Classes-----

@dataclass
class CourseProfile:
    """コースの回帰分析結果。"""
    course_name: str
    tournament_name: str
    years_analyzed: int
    years_list: list[int]
    n_samples: int
    r_squared: float
    coefficients: dict[str, float]  # {feature_name: standardized_coefficient}
    p_values: dict[str, float]      # {feature_name: p_value}
    scaler_means: dict[str, float]  # StandardScalerのmean_
    scaler_scales: dict[str, float] # StandardScalerのscale_
    features_used: list[str]        # 実際に使用した特徴量リスト
    confidence: str = "Medium"      # "High", "Medium", "Low"

    def to_db_dict(self) -> dict:
        """DB保存用辞書に変換。"""
        d: dict = {
            "course_name": self.course_name,
            "tournament_name": self.tournament_name,
            "years_analyzed": self.years_analyzed,
            "years_list": json.dumps(self.years_list),
            "n_samples": self.n_samples,
            "r_squared": self.r_squared,
            "scaler_params": json.dumps({
                "means": self.scaler_means,
                "scales": self.scaler_scales,
                "features_used": self.features_used,
            }),
        }
        for feat in STAT_FEATURES:
            d[f"coef_{feat}"] = self.coefficients.get(feat)
            d[f"pval_{feat}"] = self.p_values.get(feat)
        return d

    @classmethod
    def from_db_row(cls, row: dict) -> CourseProfile:
        """DB行から復元。"""
        scaler_params = json.loads(row["scaler_params"])
        years_list = json.loads(row["years_list"])

        coefficients = {}
        p_values = {}
        for feat in STAT_FEATURES:
            val = row.get(f"coef_{feat}")
            if val is not None:
                coefficients[feat] = val
            pval = row.get(f"pval_{feat}")
            if pval is not None:
                p_values[feat] = pval

        n_samples = row.get("n_samples", 0) or 0
        r_squared = row.get("r_squared", 0.0) or 0.0

        # 信頼度判定
        if n_samples >= 100 and r_squared >= 0.15:
            confidence = "High"
        elif n_samples >= MIN_SAMPLES_FOR_REGRESSION:
            confidence = "Medium"
        else:
            confidence = "Low"

        return cls(
            course_name=row["course_name"],
            tournament_name=row["tournament_name"],
            years_analyzed=row["years_analyzed"],
            years_list=years_list,
            n_samples=n_samples,
            r_squared=r_squared,
            coefficients=coefficients,
            p_values=p_values,
            scaler_means=scaler_params.get("means", {}),
            scaler_scales=scaler_params.get("scales", {}),
            features_used=scaler_params.get("features_used", []),
            confidence=confidence,
        )


#-----TournamentRegressor-----

class TournamentRegressor:
    """コースの重回帰分析。正規化済みデータでOLS回帰を実行。"""

    def __init__(self, db: PGAStatsDB | None = None):
        self.db = db or PGAStatsDB()

    def analyze_course(
        self,
        tournament_num: str,
        mode: str = "recent",
        n_years: int = 3,
    ) -> CourseProfile | None:
        """コースの回帰分析を実行。

        Args:
            tournament_num: 大会番号（例: "014"）
            mode: "recent"（直近N年）or "all"（全期間）
            n_years: recentモードの場合の対象年数

        Returns:
            CourseProfile。分析不可の場合はNone
        """
        # Step 1: 同一コース開催年を特定
        course_years = self.db.get_years_by_course(tournament_num)
        if not course_years:
            print(f"[WARN] No course data found for tournament {tournament_num}")
            return None

        # 最も開催回数が多いコースを対象にする
        course_name = max(course_years, key=lambda c: len(course_years[c]))
        all_years = course_years[course_name]

        # 直近の大会名を取得
        tournament_name = self._get_tournament_name(tournament_num, all_years[0])

        print(f"[INFO] Course: {course_name}")
        print(f"[INFO] Tournament: {tournament_name}")
        print(f"[INFO] Available years at this course: {all_years}")

        # Step 2: モードに応じて対象年を選択
        if mode == "recent":
            target_years = all_years[:n_years]
        else:
            target_years = all_years

        if len(target_years) < 1:
            print(f"[WARN] Not enough years at {course_name}")
            return None

        print(f"[INFO] Analyzing years: {target_years} (mode={mode})")

        # Step 3: 結果 × シーズン統計をJOIN → DataFrame
        rows = self.db.get_results_for_regression(tournament_num, target_years)
        if len(rows) < MIN_SAMPLES_FOR_REGRESSION:
            print(f"[WARN] Only {len(rows)} samples (need {MIN_SAMPLES_FOR_REGRESSION}+)")
            if len(rows) < 10:
                return None

        df = pd.DataFrame(rows)
        print(f"[INFO] Raw data: {len(df)} player-year records")

        # Step 4: 目的変数 Y = log(prize_money + 1)
        df["log_prize"] = np.log1p(df["prize_money"])

        # Step 5: 説明変数の準備と正規化
        available_features = [f for f in STAT_FEATURES if f in df.columns]

        # 50%以上欠損の列を除外
        features_used = []
        for feat in available_features:
            missing_pct = df[feat].isna().mean()
            if missing_pct < 0.5:
                features_used.append(feat)
            else:
                print(f"[INFO] Excluding {feat} ({missing_pct:.0%} missing)")

        if len(features_used) < 2:
            print(f"[WARN] Not enough features ({len(features_used)}) for regression")
            return None

        # NaN行を除外
        df_clean = df[["log_prize"] + features_used].dropna()
        n_samples = len(df_clean)
        print(f"[INFO] Clean data: {n_samples} samples, {len(features_used)} features")

        if n_samples < MIN_SAMPLES_FOR_REGRESSION:
            print(f"[WARN] Only {n_samples} clean samples (need {MIN_SAMPLES_FOR_REGRESSION}+)")
            if n_samples < 10:
                return None

        # Step 6: StandardScalerで正規化
        X_raw = df_clean[features_used].values
        Y = df_clean["log_prize"].values

        scaler = StandardScaler()
        X_normalized = scaler.fit_transform(X_raw)

        scaler_means = dict(zip(features_used, scaler.mean_.tolist()))
        scaler_scales = dict(zip(features_used, scaler.scale_.tolist()))

        # Step 7: OLS回帰（定数項あり）
        X_with_const = sm.add_constant(X_normalized)
        model = sm.OLS(Y, X_with_const).fit()

        r_squared = model.rsquared
        print(f"[INFO] R2 = {r_squared:.4f}")

        # 係数とp値を抽出（index 0は定数項なのでスキップ）
        coefficients = {}
        p_values = {}
        for i, feat in enumerate(features_used):
            coefficients[feat] = float(model.params[i + 1])
            p_values[feat] = float(model.pvalues[i + 1])

        # 有意な変数を表示
        sig_features = [(f, coefficients[f], p_values[f])
                        for f in features_used if p_values[f] < 0.05]
        sig_features.sort(key=lambda x: abs(x[1]), reverse=True)

        if sig_features:
            print(f"[INFO] Significant features (p<0.05):")
            for feat, coef, pval in sig_features:
                print(f"  {feat:<25} coef={coef:+.4f}  p={pval:.4f}")
        else:
            print(f"[INFO] No features with p<0.05")

        # 信頼度判定
        if n_samples >= 100 and r_squared >= 0.15:
            confidence = "High"
        elif n_samples >= MIN_SAMPLES_FOR_REGRESSION:
            confidence = "Medium"
        else:
            confidence = "Low"

        profile = CourseProfile(
            course_name=course_name,
            tournament_name=tournament_name,
            years_analyzed=len(target_years),
            years_list=target_years,
            n_samples=n_samples,
            r_squared=r_squared,
            coefficients=coefficients,
            p_values=p_values,
            scaler_means=scaler_means,
            scaler_scales=scaler_scales,
            features_used=features_used,
            confidence=confidence,
        )

        # DBに保存
        self.db.save_course_profile(profile.to_db_dict())
        print(f"[OK] Saved course profile for {course_name} (confidence={confidence})")

        return profile

    def analyze_all_courses(
        self,
        start_year: int = 2018,
        end_year: int = 2025,
        mode: str = "recent",
        n_years: int = 3,
    ) -> list[CourseProfile]:
        """全コースのプロファイルを一括計算。"""
        # 全大会番号を取得
        tournament_nums = self._get_all_tournament_nums(start_year, end_year)
        print(f"[INFO] Found {len(tournament_nums)} unique tournament numbers")

        profiles = []
        for idx, tnum in enumerate(tournament_nums, 1):
            print(f"\n{'='*50}")
            print(f"  [{idx}/{len(tournament_nums)}] Tournament {tnum}")
            print(f"{'='*50}")

            profile = self.analyze_course(tnum, mode=mode, n_years=n_years)
            if profile:
                profiles.append(profile)

        print(f"\n[OK] Analyzed {len(profiles)}/{len(tournament_nums)} courses")
        return profiles

    def _get_tournament_name(self, tournament_num: str, year: int) -> str:
        """大会番号と年から大会名を取得。"""
        conn = self.db._get_conn()
        try:
            pattern = f"%{tournament_num}"
            row = conn.execute(
                "SELECT tournament_name FROM pga_tournaments "
                "WHERE tournament_id LIKE ? AND year = ?",
                (pattern, year),
            ).fetchone()
            return row["tournament_name"] if row else f"Tournament {tournament_num}"
        finally:
            conn.close()

    def _get_all_tournament_nums(
        self, start_year: int, end_year: int
    ) -> list[str]:
        """期間内の全大会番号を取得。"""
        import re
        conn = self.db._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT tournament_id FROM pga_tournaments "
                "WHERE year BETWEEN ? AND ?",
                (start_year, end_year),
            ).fetchall()

            nums = set()
            for row in rows:
                tid = row["tournament_id"]
                match = re.search(r"R\d{4}(\d{3,})", tid)
                if match:
                    nums.add(match.group(1))
            return sorted(nums)
        finally:
            conn.close()


#-----CourseFitScorer-----

class CourseFitScorer:
    """コースフィットスコア計算。回帰分析時と同じ正規化基準を使用。"""

    def __init__(self, db: PGAStatsDB | None = None):
        self.db = db or PGAStatsDB()

    def score_players(
        self,
        profile: CourseProfile,
        player_stats_list: list,
    ) -> list[dict]:
        """各選手のフィットスコア(0-100)を計算。

        Args:
            profile: コースプロファイル
            player_stats_list: PlayerStatsオブジェクトのリスト

        Returns:
            [{player_name, fit_score, fit_rank, raw_score, contributing_features}, ...]
        """
        if not profile.features_used or not profile.coefficients:
            print("[WARN] Empty course profile, cannot score")
            return []

        features_used = profile.features_used
        raw_scores = []

        for ps in player_stats_list:
            name = ps.name

            # 選手の統計値を取得
            player_vals = {}
            has_data = False
            for feat in features_used:
                val = self._get_stat_value(ps, feat)
                if val is not None:
                    player_vals[feat] = val
                    has_data = True

            if not has_data:
                raw_scores.append((name, None, {}))
                continue

            # 回帰分析時と同じ正規化パラメータで正規化
            normalized_sum = 0.0
            contributing = {}
            for feat in features_used:
                raw_val = player_vals.get(feat)
                if raw_val is None:
                    continue

                mean = profile.scaler_means.get(feat, 0.0)
                scale = profile.scaler_scales.get(feat, 1.0)

                if scale == 0:
                    continue

                normalized = (raw_val - mean) / scale
                coef = profile.coefficients.get(feat, 0.0)
                contribution = normalized * coef
                normalized_sum += contribution

                contributing[feat] = {
                    "raw": raw_val,
                    "normalized": round(normalized, 3),
                    "coef": round(coef, 4),
                    "contribution": round(contribution, 4),
                }

            raw_scores.append((name, normalized_sum, contributing))

        # min-maxスケーリングで0-100に変換
        valid_scores = [s for _, s, _ in raw_scores if s is not None]
        if not valid_scores:
            return []

        min_score = min(valid_scores)
        max_score = max(valid_scores)
        score_range = max_score - min_score

        results = []
        for name, raw, contrib in raw_scores:
            if raw is None:
                results.append({
                    "player_name": name,
                    "fit_score": None,
                    "raw_score": None,
                    "contributing_features": {},
                })
            else:
                if score_range > 0:
                    fit_score = (raw - min_score) / score_range * 100
                else:
                    fit_score = 50.0

                results.append({
                    "player_name": name,
                    "fit_score": round(fit_score, 1),
                    "raw_score": round(raw, 4),
                    "contributing_features": contrib,
                })

        # fit_scoreでソートしてランク付け
        scored = [r for r in results if r["fit_score"] is not None]
        scored.sort(key=lambda x: x["fit_score"], reverse=True)
        for rank, r in enumerate(scored, 1):
            r["fit_rank"] = rank

        # スコアなしのプレイヤーにもfit_rankを設定
        for r in results:
            if r["fit_score"] is None:
                r["fit_rank"] = None

        return results

    @staticmethod
    def _get_stat_value(player_stats, feature_name: str) -> float | None:
        """PlayerStatsから特徴量値を取得。"""
        mapping = {
            "sg_approach": "sg_approach",
            "sg_off_tee": "sg_off_tee",
            "sg_tee_to_green": "sg_tee_to_green",
            "sg_putting": "sg_putting",
            "sg_around_green": "sg_around_green",
            "gir_pct": "greens_in_regulation_pct",
            "driving_distance": "driving_distance",
            "driving_accuracy_pct": "driving_accuracy_pct",
            "scrambling_pct": "scrambling_pct",
            "scoring_average": "scoring_average",
        }
        attr = mapping.get(feature_name, feature_name)
        return getattr(player_stats, attr, None)


#-----PlayerTyper-----

class PlayerTyper:
    """K-Meansによる選手タイプ分類（4タイプ）。"""

    def __init__(self, n_clusters: int = 4):
        self.n_clusters = n_clusters
        self.cluster_names: dict[int, str] = {}

    def classify_players(
        self, player_stats_list: list
    ) -> dict[str, str]:
        """全選手をタイプ分類。

        使用可能な特徴量のみでクラスタリング。
        CLUSTER_FEATURESの全統計が揃わなくても、利用可能な統計で実行。

        Args:
            player_stats_list: PlayerStatsオブジェクトのリスト

        Returns:
            {player_name: "Power Hitter", ...}
        """
        # 使用可能な特徴量を特定（最初の選手で確認）
        available_features = []
        for feat in CLUSTER_FEATURES:
            has_any = False
            for ps in player_stats_list:
                val = CourseFitScorer._get_stat_value(ps, feat)
                if val is not None:
                    has_any = True
                    break
            if has_any:
                available_features.append(feat)

        if len(available_features) < 2:
            print(f"[WARN] Not enough features ({len(available_features)}) for clustering")
            return {}

        # 特徴量行列を構築（利用可能な統計のみ）
        names = []
        feature_rows = []

        for ps in player_stats_list:
            vals = []
            has_data = True
            for feat in available_features:
                val = CourseFitScorer._get_stat_value(ps, feat)
                if val is None:
                    has_data = False
                    break
                vals.append(val)

            if has_data:
                names.append(ps.name)
                feature_rows.append(vals)

        if len(feature_rows) < self.n_clusters:
            print(f"[WARN] Not enough players ({len(feature_rows)}) for clustering")
            return {}

        print(f"[INFO] Clustering {len(feature_rows)} players with {len(available_features)} features")

        X = np.array(feature_rows)

        # StandardScalerで正規化
        scaler = StandardScaler()
        X_normalized = scaler.fit_transform(X)

        # K-Means クラスタリング
        n_clusters = min(self.n_clusters, len(X_normalized))
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(X_normalized)

        # クラスタ中心の特徴からタイプ名を自動割り当て
        self._assign_cluster_names(kmeans.cluster_centers_, available_features)

        # 結果を辞書に
        result = {}
        for name, label in zip(names, labels):
            result[name] = self.cluster_names.get(label, "All-Rounder")

        return result

    def _assign_cluster_names(
        self, centers: np.ndarray, features: list[str]
    ) -> None:
        """クラスタ中心の特徴から自動命名。"""
        n_clusters = centers.shape[0]
        used_names: set[str] = set()

        # 各クラスタの特徴的な統計を特定
        cluster_scores: dict[int, dict[str, float]] = {}
        for i in range(n_clusters):
            cluster_scores[i] = {}
            for type_name, key_features in PLAYER_TYPES.items():
                if not key_features:
                    continue
                # 該当特徴量の平均値（正規化済み）
                score = np.mean([
                    centers[i, features.index(f)]
                    for f in key_features if f in features
                ])
                cluster_scores[i][type_name] = score

        # スコアが高い順にタイプ名を割り当て
        for i in range(n_clusters):
            best_name = "All-Rounder"
            best_score = -float("inf")

            for type_name, score in cluster_scores[i].items():
                if type_name not in used_names and score > best_score:
                    best_score = score
                    best_name = type_name

            if best_name != "All-Rounder":
                used_names.add(best_name)
            self.cluster_names[i] = best_name


#-----Pipeline Integration-----

def run_course_fit_analysis(
    groups: dict[int, list],
    tournament_id: str | None = None,
    tournament_num: str | None = None,
    mode: str = "recent",
    n_years: int = 3,
) -> dict:
    """パイプラインから呼び出されるメイン関数。

    Args:
        groups: {group_id: [GroupPlayer, ...]}
        tournament_id: トーナメントID（例: "R2025034"）
        tournament_num: 大会番号（例: "034"）。tournament_idがあれば自動抽出
        mode: 分析モード（"recent" or "all"）
        n_years: recentモードの対象年数

    Returns:
        {
            "profile": CourseProfile or None,
            "scores": [{player_name, fit_score, fit_rank, ...}],
            "player_types": {player_name: type_name},
        }
    """
    db = PGAStatsDB()

    # 大会番号の特定
    if tournament_num is None and tournament_id:
        tournament_num = db.get_tournament_num(tournament_id)

    if not tournament_num:
        print("[WARN] No tournament number specified for course fit analysis")
        return {"profile": None, "scores": [], "player_types": {}}

    print(f"\n[INFO] Course Fit Analysis for tournament {tournament_num}")
    print(f"[INFO] Mode: {mode}, n_years: {n_years}")

    # Step 1: コースプロファイル取得または計算
    regressor = TournamentRegressor(db)
    profile = regressor.analyze_course(tournament_num, mode=mode, n_years=n_years)

    if not profile:
        print("[WARN] Could not generate course profile")
        return {"profile": None, "scores": [], "player_types": {}}

    # Step 2: 全選手のPlayerStatsを収集
    all_player_stats = []
    player_stats_map = {}
    for group_id, players in groups.items():
        for gp in players:
            if gp.stats and gp.stats.has_sufficient_data():
                all_player_stats.append(gp.stats)
                player_stats_map[gp.name] = gp.stats

    if not all_player_stats:
        print("[WARN] No player stats available for scoring")
        return {"profile": profile, "scores": [], "player_types": {}}

    print(f"[INFO] Scoring {len(all_player_stats)} players with stats data")

    # Step 3: フィットスコア計算
    scorer = CourseFitScorer(db)
    scores = scorer.score_players(profile, all_player_stats)

    # Step 4: 選手タイプ分類
    typer = PlayerTyper()
    player_types = typer.classify_players(all_player_stats)

    # Step 5: GroupPlayerにフィットスコアとタイプを付与
    score_map = {s["player_name"]: s for s in scores}
    for group_id, players in groups.items():
        for gp in players:
            if gp.name in score_map:
                s = score_map[gp.name]
                gp.course_fit_score = s.get("fit_score")
                gp.course_fit_rank = s.get("fit_rank")
            if gp.name in player_types:
                gp.player_type = player_types[gp.name]

    print(f"[OK] Course fit analysis complete: {len(scores)} players scored")

    return {
        "profile": profile,
        "scores": scores,
        "player_types": player_types,
    }


#-----CLI-----

def main() -> None:
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(description="Course Fit Analysis")
    parser.add_argument(
        "--profile", type=str, metavar="TOURNAMENT_NUM",
        help="Analyze course profile for a tournament (e.g. 014, 034)",
    )
    parser.add_argument(
        "--mode", type=str, default="recent", choices=["recent", "all"],
        help="Analysis mode (default: recent)",
    )
    parser.add_argument(
        "--years", type=int, default=3,
        help="Number of years for recent mode (default: 3)",
    )
    parser.add_argument(
        "--venue-history", type=str, metavar="TOURNAMENT_NUM",
        help="Show venue history for a tournament",
    )
    parser.add_argument(
        "--all-courses", action="store_true",
        help="Analyze all courses in the database",
    )
    parser.add_argument(
        "--show-profiles", action="store_true",
        help="Show all saved course profiles",
    )

    args = parser.parse_args()
    db = PGAStatsDB()

    if args.venue_history:
        courses = db.get_years_by_course(args.venue_history)
        if not courses:
            print(f"No venue data for tournament {args.venue_history}")
            return

        # 大会名を取得
        for course_name, years in courses.items():
            print(f"\n  {course_name}:")
            print(f"    Years: {years}")
            print(f"    Count: {len(years)}")

    elif args.profile:
        regressor = TournamentRegressor(db)
        profile = regressor.analyze_course(
            args.profile, mode=args.mode, n_years=args.years
        )
        if profile:
            _print_profile(profile)

    elif args.all_courses:
        regressor = TournamentRegressor(db)
        profiles = regressor.analyze_all_courses(mode=args.mode, n_years=args.years)
        for p in profiles:
            _print_profile(p)
            print()

    elif args.show_profiles:
        profiles = db.get_all_course_profiles()
        if not profiles:
            print("No course profiles saved yet.")
            return
        for row in profiles:
            p = CourseProfile.from_db_row(row)
            _print_profile(p)
            print()

    else:
        parser.print_help()


def _print_profile(profile: CourseProfile) -> None:
    """コースプロファイルを表示。"""
    print(f"\n{'='*60}")
    print(f"  Course: {profile.course_name}")
    print(f"  Tournament: {profile.tournament_name}")
    print(f"  Years: {profile.years_list} ({profile.years_analyzed} years)")
    print(f"  Samples: {profile.n_samples}")
    print(f"  R2: {profile.r_squared:.4f}")
    print(f"  Confidence: {profile.confidence}")
    print(f"{'='*60}")

    print(f"\n  {'Feature':<25} {'Coefficient':>12} {'p-value':>10} {'Sig':>5}")
    print(f"  {'-'*25} {'-'*12} {'-'*10} {'-'*5}")

    # 係数の絶対値でソート
    sorted_feats = sorted(
        profile.features_used,
        key=lambda f: abs(profile.coefficients.get(f, 0)),
        reverse=True,
    )

    for feat in sorted_feats:
        coef = profile.coefficients.get(feat, 0)
        pval = profile.p_values.get(feat, 1)
        sig = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else ""
        print(f"  {feat:<25} {coef:>+12.4f} {pval:>10.4f} {sig:>5}")


if __name__ == "__main__":
    main()
