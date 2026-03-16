"""EGS v2 モデル訓練スクリプト — Long/Short Memory Features。

v1 との違い:
  - Long Memory: 過去3年のキャリア統計（CUT通過率, 平均順位, 成長傾向）
  - Short Memory: 直近3大会の調子（順位推移, モメンタム, CUT率）
  - コース相性: 同一大会での過去成績

v1 モデルと並行運用し、比較ページで精度を検証する。

Usage:
    uv run python -m src.egs_v2_trainer              # 訓練実行
    uv run python -m src.egs_v2_trainer --info        # モデル情報表示
    uv run python -m src.egs_v2_trainer --compare     # v1 vs v2 比較
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.model_selection import GroupKFold, cross_val_predict, cross_val_score
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss

from .pga_stats_db import PGAStatsDB


# ----- Constants -----

MODEL_DIR = Path("data/models")
V2_CUT_MODEL_PATH = MODEL_DIR / "egs_v2_cut_classifier.joblib"
V2_POS_MODEL_PATH = MODEL_DIR / "egs_v2_position_regressor.joblib"
V2_METADATA_PATH = MODEL_DIR / "egs_v2_metadata.json"
V2_HISTORY_PATH = MODEL_DIR / "egs_v2_training_history.json"

# v1 paths (for comparison)
V1_METADATA_PATH = MODEL_DIR / "egs_model_metadata.json"

# Base features (same as v1)
BASE_FEATURES = [
    "sg_approach", "sg_off_tee", "sg_tee_to_green",
    "gir_pct", "scrambling_pct", "scoring_average",
    "scoring_average_rank", "field_size", "field_strength",
    "player_relative_strength",
]

# Long Memory features
LONG_MEMORY_FEATURES = [
    "career_cut_rate",           # 過去3年のCUT通過率
    "career_avg_position_pct",   # 過去3年の平均順位パーセンタイル
    "career_tournaments_played", # 過去3年の出場数
    "year_over_year_trend",      # スコアリング平均の前年比変化
    "course_history_avg_pos",    # 同一大会での過去平均順位
    "course_history_cut_rate",   # 同一大会での過去CUT通過率
]

# Short Memory features
SHORT_MEMORY_FEATURES = [
    "recent_3t_avg_pos_pct",     # 直近3大会の平均順位パーセンタイル
    "recent_3t_cut_rate",        # 直近3大会のCUT通過率
    "recent_3t_best_pos_pct",    # 直近3大会の最高順位パーセンタイル
    "momentum",                  # 直近3大会の順位改善傾向（回帰の傾き）
    "recent_vs_season",          # 直近3大会avg - シーズンavg の乖離
]

ALL_V2_FEATURES = BASE_FEATURES + LONG_MEMORY_FEATURES + SHORT_MEMORY_FEATURES

MIN_TRAINING_SAMPLES = 500


# ----- Data Classes -----

@dataclass
class V2TrainingResult:
    """v2 モデル訓練結果。"""
    n_samples_cut: int
    n_samples_position: int
    features_used: list[str]
    n_base_features: int
    n_long_memory_features: int
    n_short_memory_features: int
    # CutClassifier metrics
    cut_roc_auc_cv: float
    cut_brier_cv: float
    cut_accuracy_cv: float
    # PositionRegressor metrics
    pos_mae_cv: float
    pos_r2_cv: float
    pos_mae_raw_cv: float
    # Feature importance
    cut_feature_importance: dict[str, float] = field(default_factory=dict)
    pos_feature_importance: dict[str, float] = field(default_factory=dict)
    trained_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ----- Trainer -----

class EGSv2Trainer:
    """EGS v2: Long/Short Memory Features + HistGradientBoosting。"""

    def __init__(self):
        self.cut_classifier = HistGradientBoostingClassifier(
            max_iter=300,
            max_depth=6,
            min_samples_leaf=15,
            learning_rate=0.05,
            random_state=42,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=15,
            l2_regularization=0.1,
        )
        self.position_regressor = HistGradientBoostingRegressor(
            max_iter=300,
            max_depth=6,
            min_samples_leaf=15,
            learning_rate=0.05,
            loss="squared_error",
            random_state=42,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=15,
            l2_regularization=0.1,
        )
        self.features_used: list[str] = []

    @staticmethod
    def _compute_importance(
        model, X: np.ndarray, y: np.ndarray,
        feature_names: list[str], scoring: str,
    ) -> dict[str, float]:
        """permutation_importance で特徴量重要度を算出。"""
        n = min(5000, len(X))
        rng = np.random.RandomState(42)
        idx = rng.choice(len(X), n, replace=False)
        result = permutation_importance(
            model, X[idx], y[idx],
            n_repeats=5, random_state=42, scoring=scoring, n_jobs=-1,
        )
        importances = np.maximum(result.importances_mean, 0.0)
        total = importances.sum()
        if total > 0:
            importances = importances / total
        return dict(zip(feature_names, importances.tolist()))

    def build_training_data(self, db: PGAStatsDB | None = None) -> pd.DataFrame:
        """Long/Short Memory 特徴量付きの訓練データを構築。"""
        if db is None:
            db = PGAStatsDB()

        conn = db._get_conn()
        try:
            # Step 1: ベースデータ取得（v1と同じ）
            rows = conn.execute("""
                SELECT
                    tr.player_id,
                    tr.player_name,
                    tr.year,
                    tr.tournament_id,
                    tr.position,
                    CASE WHEN tr.position IS NOT NULL THEN 1 ELSE 0 END as made_cut,
                    field_info.field_size,
                    field_info.made_cut_count,
                    MAX(CASE WHEN ss.stat_name = 'sg_approach' THEN ss.stat_value END) as sg_approach,
                    MAX(CASE WHEN ss.stat_name = 'sg_off_tee' THEN ss.stat_value END) as sg_off_tee,
                    MAX(CASE WHEN ss.stat_name = 'sg_tee_to_green' THEN ss.stat_value END) as sg_tee_to_green,
                    MAX(CASE WHEN ss.stat_name = 'gir_pct' THEN ss.stat_value END) as gir_pct,
                    MAX(CASE WHEN ss.stat_name = 'scrambling_pct' THEN ss.stat_value END) as scrambling_pct,
                    MAX(CASE WHEN ss.stat_name = 'scoring_average' THEN ss.stat_value END) as scoring_average
                FROM pga_tournament_results tr
                LEFT JOIN pga_season_stats ss
                    ON tr.player_id = ss.player_id AND tr.year = ss.year
                LEFT JOIN (
                    SELECT tournament_id, year,
                           COUNT(*) as field_size,
                           COUNT(CASE WHEN position IS NOT NULL THEN 1 END) as made_cut_count
                    FROM pga_tournament_results
                    GROUP BY tournament_id, year
                ) field_info ON tr.tournament_id = field_info.tournament_id
                              AND tr.year = field_info.year
                WHERE tr.year >= 2018
                GROUP BY tr.player_id, tr.tournament_id, tr.year
                HAVING sg_approach IS NOT NULL OR sg_off_tee IS NOT NULL
                    OR sg_tee_to_green IS NOT NULL OR gir_pct IS NOT NULL
            """).fetchall()

            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame([dict(r) for r in rows])

            # Step 2: 全大会結果を取得（メモリ特徴量構築用）
            all_results = conn.execute("""
                SELECT tr.player_id, tr.year, tr.tournament_id, tr.position,
                       CASE WHEN tr.position IS NOT NULL THEN 1 ELSE 0 END as made_cut,
                       field_info.field_size,
                       field_info.made_cut_count
                FROM pga_tournament_results tr
                LEFT JOIN (
                    SELECT tournament_id, year,
                           COUNT(*) as field_size,
                           COUNT(CASE WHEN position IS NOT NULL THEN 1 END) as made_cut_count
                    FROM pga_tournament_results
                    GROUP BY tournament_id, year
                ) field_info ON tr.tournament_id = field_info.tournament_id
                              AND tr.year = field_info.year
                WHERE tr.year >= 2015
                ORDER BY tr.player_id, tr.year, tr.tournament_id
            """).fetchall()

            all_df = pd.DataFrame([dict(r) for r in all_results])

            # 前年のスコアリング平均（year_over_year_trend 用）
            prev_scoring = conn.execute("""
                SELECT player_id, year, stat_value as scoring_average
                FROM pga_season_stats
                WHERE stat_name = 'scoring_average' AND year >= 2017
            """).fetchall()
            prev_scoring_df = pd.DataFrame([dict(r) for r in prev_scoring])

        finally:
            conn.close()

        # Step 3: Base features engineering（v1 と同じ）
        df = self._engineer_base_features(df)

        # Step 4: Long Memory features
        df = self._engineer_long_memory(df, all_df, prev_scoring_df)

        # Step 5: Short Memory features
        df = self._engineer_short_memory(df, all_df)

        n_base = sum(1 for f in BASE_FEATURES if f in df.columns)
        n_long = sum(1 for f in LONG_MEMORY_FEATURES if f in df.columns)
        n_short = sum(1 for f in SHORT_MEMORY_FEATURES if f in df.columns)
        print(f"[INFO] Training data: {len(df)} samples, "
              f"{df['year'].nunique()} years, "
              f"{df['tournament_id'].nunique()} tournaments")
        print(f"[INFO] Features: {n_base} base + {n_long} long memory + {n_short} short memory")
        print(f"[INFO] Made cut: {df['made_cut'].sum()}, "
              f"Missed cut: {(~df['made_cut'].astype(bool)).sum()}")

        return df

    def _engineer_base_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """v1 と同じ base features。"""
        df["scoring_average_rank"] = (
            df.groupby("year")["scoring_average"]
            .rank(method="min", na_option="bottom")
        )
        df["field_strength"] = (
            df.groupby(["tournament_id", "year"])["scoring_average"]
            .transform("mean")
        )
        df["player_relative_strength"] = df["field_strength"] - df["scoring_average"]

        made_cut_mask = df["made_cut"] == 1
        df.loc[made_cut_mask, "position_pct"] = (
            df.loc[made_cut_mask, "position"] / df.loc[made_cut_mask, "made_cut_count"]
        )
        return df

    def _engineer_long_memory(
        self, df: pd.DataFrame, all_df: pd.DataFrame, prev_scoring_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Long Memory 特徴量を構築。"""
        # position_pct を all_df にも計算
        mask = all_df["made_cut"] == 1
        all_df = all_df.copy()
        all_df.loc[mask, "position_pct"] = (
            all_df.loc[mask, "position"] / all_df.loc[mask, "made_cut_count"]
        )

        # 選手×年ごとの集計を事前計算（過去3年分のルックアップ用）
        player_year_stats = (
            all_df.groupby(["player_id", "year"])
            .agg(
                total_tournaments=("tournament_id", "count"),
                total_cuts=("made_cut", "sum"),
                avg_position_pct=("position_pct", "mean"),
            )
            .reset_index()
        )

        # 大会番号（tournament_id の数値部分）を抽出してコース相性用
        all_df["tournament_num"] = all_df["tournament_id"].str.extract(r"R\d{4}(\d+)")[0]
        df["tournament_num"] = df["tournament_id"].str.extract(r"R\d{4}(\d+)")[0]

        # 大会ごとの過去成績
        course_history = (
            all_df.groupby(["player_id", "tournament_num"])
            .apply(
                lambda g: pd.Series({
                    "all_years": list(zip(g["year"], g["made_cut"], g["position_pct"])),
                }),
                include_groups=False,
            )
            .reset_index()
        )
        course_lookup = {}
        for _, row in course_history.iterrows():
            course_lookup[(row["player_id"], row["tournament_num"])] = row["all_years"]

        # prev_scoring lookup
        prev_sa = {}
        for _, row in prev_scoring_df.iterrows():
            prev_sa[(row["player_id"], row["year"])] = row["scoring_average"]

        # 各行に Long Memory 特徴量を付与
        career_cut_rates = []
        career_avg_pos = []
        career_tournaments = []
        yoy_trends = []
        course_avg_pos = []
        course_cut_rates = []

        for _, row in df.iterrows():
            pid = row["player_id"]
            year = row["year"]
            tid = row.get("tournament_num", "")

            # --- キャリア統計（過去3年） ---
            past = player_year_stats[
                (player_year_stats["player_id"] == pid)
                & (player_year_stats["year"] >= year - 3)
                & (player_year_stats["year"] < year)
            ]

            if len(past) > 0:
                total_t = past["total_tournaments"].sum()
                total_c = past["total_cuts"].sum()
                career_cut_rates.append(total_c / total_t if total_t > 0 else np.nan)
                career_avg_pos.append(past["avg_position_pct"].mean())
                career_tournaments.append(total_t)
            else:
                career_cut_rates.append(np.nan)
                career_avg_pos.append(np.nan)
                career_tournaments.append(0)

            # --- Year-over-year trend ---
            sa_this = prev_sa.get((pid, year))
            sa_prev = prev_sa.get((pid, year - 1))
            if sa_this is not None and sa_prev is not None:
                yoy_trends.append(sa_this - sa_prev)
            else:
                yoy_trends.append(np.nan)

            # --- コース相性 ---
            history = course_lookup.get((pid, tid), [])
            past_at_course = [(y, mc, pp) for y, mc, pp in history if y < year]
            if past_at_course:
                course_cut_rates.append(
                    sum(mc for _, mc, _ in past_at_course) / len(past_at_course)
                )
                pos_values = [pp for _, mc, pp in past_at_course if mc == 1 and pp is not None]
                course_avg_pos.append(np.mean(pos_values) if pos_values else np.nan)
            else:
                course_cut_rates.append(np.nan)
                course_avg_pos.append(np.nan)

        df["career_cut_rate"] = career_cut_rates
        df["career_avg_position_pct"] = career_avg_pos
        df["career_tournaments_played"] = career_tournaments
        df["year_over_year_trend"] = yoy_trends
        df["course_history_avg_pos"] = course_avg_pos
        df["course_history_cut_rate"] = course_cut_rates

        return df

    def _engineer_short_memory(
        self, df: pd.DataFrame, all_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Short Memory 特徴量を構築。"""
        # 各選手の大会履歴をソートして辞書化
        all_df = all_df.copy()
        mask = all_df["made_cut"] == 1
        all_df.loc[mask, "position_pct"] = (
            all_df.loc[mask, "position"] / all_df.loc[mask, "made_cut_count"]
        )

        player_sequences = {}
        for pid, grp in all_df.groupby("player_id"):
            sorted_grp = grp.sort_values(["year", "tournament_id"])
            player_sequences[pid] = list(zip(
                sorted_grp["year"],
                sorted_grp["tournament_id"],
                sorted_grp["made_cut"],
                sorted_grp["position_pct"],
            ))

        recent_avg_pos = []
        recent_cut_rates = []
        recent_best_pos = []
        momentums = []
        recent_vs_seasons = []

        for _, row in df.iterrows():
            pid = row["player_id"]
            year = row["year"]
            tid = row["tournament_id"]
            sa = row.get("scoring_average")

            seq = player_sequences.get(pid, [])

            # 現在の大会より前の直近3大会を取得
            prior = [(y, t, mc, pp) for y, t, mc, pp in seq
                     if (y < year) or (y == year and t < tid)]
            last_3 = prior[-3:] if len(prior) >= 3 else prior

            if len(last_3) >= 1:
                cuts = [mc for _, _, mc, _ in last_3]
                recent_cut_rates.append(sum(cuts) / len(cuts))

                pos_vals = [pp for _, _, mc, pp in last_3 if mc == 1 and pp is not None]
                if pos_vals:
                    recent_avg_pos.append(np.mean(pos_vals))
                    recent_best_pos.append(min(pos_vals))

                    # Momentum: 線形回帰の傾き（負=改善、正=悪化）
                    if len(pos_vals) >= 2:
                        x = np.arange(len(pos_vals))
                        slope = np.polyfit(x, pos_vals, 1)[0]
                        momentums.append(slope)
                    else:
                        momentums.append(0.0)

                    # Recent vs Season average
                    if sa is not None and not np.isnan(sa):
                        # scoring_average は低い方が良い → position_pct も低い方が良い
                        # 直近のposition_pct と シーズン平均のプロキシを比較
                        recent_vs_seasons.append(np.mean(pos_vals) - 0.5)
                    else:
                        recent_vs_seasons.append(np.nan)
                else:
                    recent_avg_pos.append(np.nan)
                    recent_best_pos.append(np.nan)
                    momentums.append(np.nan)
                    recent_vs_seasons.append(np.nan)
            else:
                recent_avg_pos.append(np.nan)
                recent_cut_rates.append(np.nan)
                recent_best_pos.append(np.nan)
                momentums.append(np.nan)
                recent_vs_seasons.append(np.nan)

        df["recent_3t_avg_pos_pct"] = recent_avg_pos
        df["recent_3t_cut_rate"] = recent_cut_rates
        df["recent_3t_best_pos_pct"] = recent_best_pos
        df["momentum"] = momentums
        df["recent_vs_season"] = recent_vs_seasons

        return df

    def train(self, db: PGAStatsDB | None = None) -> V2TrainingResult | None:
        """v2 モデル訓練。"""
        df = self.build_training_data(db)
        if df.empty or len(df) < MIN_TRAINING_SAMPLES:
            print(f"[WARN] Insufficient data: {len(df)} samples")
            return None

        # 利用可能な特徴量
        available = []
        for feat in ALL_V2_FEATURES:
            if feat in df.columns and df[feat].notna().sum() > len(df) * 0.03:
                available.append(feat)

        if len(available) < 6:
            print(f"[WARN] Not enough features: {len(available)}")
            return None

        self.features_used = available
        n_base = sum(1 for f in available if f in BASE_FEATURES)
        n_long = sum(1 for f in available if f in LONG_MEMORY_FEATURES)
        n_short = sum(1 for f in available if f in SHORT_MEMORY_FEATURES)
        print(f"[INFO] Using {len(available)} features: "
              f"{n_base} base + {n_long} long + {n_short} short")

        groups = df["year"].values

        # ----- CutClassifier -----
        print("\n--- CutClassifier v2 Training ---")
        X_cut = df[available].values
        y_cut = df["made_cut"].values

        gkf = GroupKFold(n_splits=min(5, df["year"].nunique()))

        cut_auc_scores = cross_val_score(
            self.cut_classifier, X_cut, y_cut,
            cv=gkf, groups=groups, scoring="roc_auc",
        )
        cut_auc = float(cut_auc_scores.mean())
        print(f"[INFO] ROC-AUC (CV): {cut_auc:.4f} (+/- {cut_auc_scores.std():.4f})")

        cut_acc_scores = cross_val_score(
            self.cut_classifier, X_cut, y_cut,
            cv=gkf, groups=groups, scoring="accuracy",
        )
        cut_acc = float(cut_acc_scores.mean())
        print(f"[INFO] Accuracy (CV): {cut_acc:.4f}")

        cut_proba_cv = cross_val_predict(
            self.cut_classifier, X_cut, y_cut,
            cv=gkf, groups=groups, method="predict_proba",
        )
        cut_brier = float(brier_score_loss(y_cut, cut_proba_cv[:, 1]))
        print(f"[INFO] Brier Score (CV): {cut_brier:.4f}")

        self.cut_classifier.fit(X_cut, y_cut)
        cut_importance = self._compute_importance(
            self.cut_classifier, X_cut, y_cut, available, "accuracy",
        )
        print("[INFO] CutClassifier v2 feature importance:")
        for feat, imp in sorted(cut_importance.items(), key=lambda x: -x[1]):
            tag = ""
            if feat in LONG_MEMORY_FEATURES:
                tag = " [LONG]"
            elif feat in SHORT_MEMORY_FEATURES:
                tag = " [SHORT]"
            bar = "#" * int(imp * 50)
            print(f"  {feat:<30} {imp:.3f} {bar}{tag}")

        # ----- PositionRegressor -----
        print("\n--- PositionRegressor v2 Training ---")
        made_cut_df = df[df["made_cut"] == 1].copy()
        X_pos = made_cut_df[available].values
        y_pos = made_cut_df["position_pct"].values
        groups_pos = made_cut_df["year"].values

        gkf_pos = GroupKFold(n_splits=min(5, made_cut_df["year"].nunique()))

        pos_mae_scores = cross_val_score(
            self.position_regressor, X_pos, y_pos,
            cv=gkf_pos, groups=groups_pos, scoring="neg_mean_absolute_error",
        )
        pos_mae = float(-pos_mae_scores.mean())
        print(f"[INFO] MAE position_pct (CV): {pos_mae:.4f} (+/- {pos_mae_scores.std():.4f})")

        pos_r2_scores = cross_val_score(
            self.position_regressor, X_pos, y_pos,
            cv=gkf_pos, groups=groups_pos, scoring="r2",
        )
        pos_r2 = float(pos_r2_scores.mean())
        print(f"[INFO] R2 (CV): {pos_r2:.4f}")

        mean_cut_count = made_cut_df["made_cut_count"].mean()
        pos_mae_raw = pos_mae * mean_cut_count
        print(f"[INFO] MAE raw position (CV, approx): {pos_mae_raw:.1f} places")

        self.position_regressor.fit(X_pos, y_pos)
        pos_importance = self._compute_importance(
            self.position_regressor, X_pos, y_pos, available, "neg_mean_absolute_error",
        )
        print("[INFO] PositionRegressor v2 feature importance:")
        for feat, imp in sorted(pos_importance.items(), key=lambda x: -x[1]):
            tag = ""
            if feat in LONG_MEMORY_FEATURES:
                tag = " [LONG]"
            elif feat in SHORT_MEMORY_FEATURES:
                tag = " [SHORT]"
            bar = "#" * int(imp * 50)
            print(f"  {feat:<30} {imp:.3f} {bar}{tag}")

        result = V2TrainingResult(
            n_samples_cut=len(df),
            n_samples_position=len(made_cut_df),
            features_used=available,
            n_base_features=n_base,
            n_long_memory_features=n_long,
            n_short_memory_features=n_short,
            cut_roc_auc_cv=cut_auc,
            cut_brier_cv=cut_brier,
            cut_accuracy_cv=cut_acc,
            pos_mae_cv=pos_mae,
            pos_r2_cv=pos_r2,
            pos_mae_raw_cv=pos_mae_raw,
            cut_feature_importance=cut_importance,
            pos_feature_importance=pos_importance,
        )
        self._save_models(result)
        return result

    def _save_models(self, result: V2TrainingResult) -> None:
        """v2 モデルとメタデータを保存。"""
        import joblib

        MODEL_DIR.mkdir(parents=True, exist_ok=True)

        cut_data = {"model": self.cut_classifier, "features_used": self.features_used}
        joblib.dump(cut_data, V2_CUT_MODEL_PATH)
        print(f"[OK] CutClassifier v2 saved: {V2_CUT_MODEL_PATH}")

        pos_data = {"model": self.position_regressor, "features_used": self.features_used}
        joblib.dump(pos_data, V2_POS_MODEL_PATH)
        print(f"[OK] PositionRegressor v2 saved: {V2_POS_MODEL_PATH}")

        metadata = {
            "model_type": "EGS v2 Long/Short Memory",
            "cut_model": "HistGradientBoostingClassifier",
            "position_model": "HistGradientBoostingRegressor",
            "n_samples_cut": result.n_samples_cut,
            "n_samples_position": result.n_samples_position,
            "features_used": result.features_used,
            "n_base_features": result.n_base_features,
            "n_long_memory_features": result.n_long_memory_features,
            "n_short_memory_features": result.n_short_memory_features,
            "cut_roc_auc_cv": round(result.cut_roc_auc_cv, 4),
            "cut_brier_cv": round(result.cut_brier_cv, 4),
            "cut_accuracy_cv": round(result.cut_accuracy_cv, 4),
            "pos_mae_cv": round(result.pos_mae_cv, 4),
            "pos_r2_cv": round(result.pos_r2_cv, 4),
            "pos_mae_raw_cv": round(result.pos_mae_raw_cv, 1),
            "cut_feature_importance": {
                k: round(v, 4) for k, v in result.cut_feature_importance.items()
            },
            "pos_feature_importance": {
                k: round(v, 4) for k, v in result.pos_feature_importance.items()
            },
            "trained_at": result.trained_at,
        }
        with open(V2_METADATA_PATH, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        print(f"[OK] v2 Metadata saved: {V2_METADATA_PATH}")

        # History
        history: list[dict] = []
        if V2_HISTORY_PATH.exists():
            try:
                with open(V2_HISTORY_PATH, encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, ValueError):
                history = []
        history.append(metadata)
        with open(V2_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        print(f"[OK] v2 History updated: {V2_HISTORY_PATH} ({len(history)} entries)")


# ----- Comparison -----

def compare_v1_v2() -> dict | None:
    """v1 と v2 のメトリクスを比較。"""
    if not V1_METADATA_PATH.exists():
        print("[WARN] v1 metadata not found")
        return None
    if not V2_METADATA_PATH.exists():
        print("[WARN] v2 metadata not found. Run training first.")
        return None

    with open(V1_METADATA_PATH, encoding="utf-8") as f:
        v1 = json.load(f)
    with open(V2_METADATA_PATH, encoding="utf-8") as f:
        v2 = json.load(f)

    print("=" * 70)
    print("  EGS Model Comparison: v1 (Baseline) vs v2 (Long/Short Memory)")
    print("=" * 70)

    metrics = [
        ("Samples (CUT)", "n_samples_cut", False),
        ("Samples (Position)", "n_samples_position", False),
        ("Features", "features_used", False),
        ("CUT ROC-AUC", "cut_roc_auc_cv", True),
        ("CUT Brier Score", "cut_brier_cv", False),
        ("CUT Accuracy", "cut_accuracy_cv", True),
        ("Position MAE", "pos_mae_cv", False),
        ("Position R2", "pos_r2_cv", True),
        ("Position MAE (raw)", "pos_mae_raw_cv", False),
    ]

    comparison = {}
    for label, key, higher_is_better in metrics:
        v1_val = v1.get(key)
        v2_val = v2.get(key)

        if isinstance(v1_val, list):
            v1_str = str(len(v1_val))
            v2_str = str(len(v2_val))
            diff_str = f"+{len(v2_val) - len(v1_val)}"
        elif isinstance(v1_val, (int, float)):
            v1_str = f"{v1_val}"
            v2_str = f"{v2_val}"
            diff = v2_val - v1_val
            if key in ("cut_brier_cv", "pos_mae_cv", "pos_mae_raw_cv"):
                # Lower is better
                winner = "v2" if diff < 0 else ("v1" if diff > 0 else "tie")
            else:
                winner = "v2" if diff > 0 else ("v1" if diff < 0 else "tie")
            arrow = ">>>" if winner == "v2" else ("<<<" if winner == "v1" else " = ")
            diff_str = f"{diff:+.4f} {arrow}"
            comparison[key] = {"v1": v1_val, "v2": v2_val, "diff": diff, "winner": winner}
        else:
            v1_str = str(v1_val)
            v2_str = str(v2_val)
            diff_str = ""

        print(f"  {label:<22} v1={v1_str:<12} v2={v2_str:<12} {diff_str}")

    # Memory features breakdown
    if "n_long_memory_features" in v2:
        print(f"\n  v2 Feature Breakdown:")
        print(f"    Base:         {v2.get('n_base_features', 0)}")
        print(f"    Long Memory:  {v2.get('n_long_memory_features', 0)}")
        print(f"    Short Memory: {v2.get('n_short_memory_features', 0)}")

    print("=" * 70)
    return comparison


# ----- CLI -----

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="EGS v2 Model Trainer")
    parser.add_argument("--info", action="store_true", help="Show v2 model info")
    parser.add_argument("--compare", action="store_true", help="Compare v1 vs v2")
    args = parser.parse_args()

    if args.compare:
        compare_v1_v2()
        return 0

    if args.info:
        if V2_METADATA_PATH.exists():
            with open(V2_METADATA_PATH, encoding="utf-8") as f:
                meta = json.load(f)
            print(json.dumps(meta, indent=2, ensure_ascii=False))
        else:
            print("[INFO] No v2 model found. Run training first.")
        return 0

    print("=" * 70)
    print("  EGS v2 Model Training (Long/Short Memory)")
    print("=" * 70)

    trainer = EGSv2Trainer()
    result = trainer.train()

    if result is None:
        print("[ERROR] Training failed.")
        return 1

    print()
    print("=" * 70)
    print("  v2 Training Complete!")
    print("=" * 70)
    print(f"  Features: {result.n_base_features} base + "
          f"{result.n_long_memory_features} long + "
          f"{result.n_short_memory_features} short = {len(result.features_used)} total")
    print(f"  CutClassifier:     ROC-AUC={result.cut_roc_auc_cv:.4f}, "
          f"Brier={result.cut_brier_cv:.4f}")
    print(f"  PositionRegressor: MAE={result.pos_mae_cv:.4f} "
          f"(~{result.pos_mae_raw_cv:.1f} places), R2={result.pos_r2_cv:.4f}")

    # Auto-compare with v1
    print()
    compare_v1_v2()

    return 0


if __name__ == "__main__":
    sys.exit(main())
