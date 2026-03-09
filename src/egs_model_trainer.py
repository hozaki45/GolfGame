"""EGSモデル訓練スクリプト。

CutClassifier (二値分類) + PositionRegressor (回帰) の2段階モデルを訓練し、
game_optimizer.py のヒューリスティック推定をML推定に置き換える。

Usage:
    uv run python -m src.egs_model_trainer           # 訓練実行
    uv run python -m src.egs_model_trainer --info     # モデル情報表示
    uv run python -m src.egs_model_trainer --evaluate  # 評価のみ
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
from sklearn.metrics import brier_score_loss, roc_auc_score

from .pga_stats_db import PGAStatsDB


#-----Constants-----

MODEL_DIR = Path("data/models")
CUT_MODEL_PATH = MODEL_DIR / "egs_cut_classifier.joblib"
POS_MODEL_PATH = MODEL_DIR / "egs_position_regressor.joblib"
EGS_METADATA_PATH = MODEL_DIR / "egs_model_metadata.json"
EGS_HISTORY_PATH = MODEL_DIR / "egs_training_history.json"

EGS_FEATURES = [
    # Core SG stats (6)
    "sg_approach", "sg_off_tee", "sg_tee_to_green",
    "gir_pct", "scrambling_pct", "scoring_average",
    # Derived context (4)
    "scoring_average_rank",
    "field_size",
    "field_strength",
    "player_relative_strength",
]

MIN_TRAINING_SAMPLES = 500


#-----Data Classes-----

@dataclass
class EGSTrainingResult:
    """2段階モデル訓練結果。"""
    n_samples_cut: int
    n_samples_position: int
    features_used: list[str]
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


#-----Trainer-----

class EGSModelTrainer:
    """CutClassifier + PositionRegressor の訓練パイプライン。"""

    def __init__(self):
        self.cut_classifier = HistGradientBoostingClassifier(
            max_iter=200,
            max_depth=5,
            min_samples_leaf=20,
            learning_rate=0.05,
            random_state=42,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=10,
        )
        self.position_regressor = HistGradientBoostingRegressor(
            max_iter=200,
            max_depth=5,
            min_samples_leaf=20,
            learning_rate=0.05,
            loss="squared_error",
            random_state=42,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=10,
        )
        self.features_used: list[str] = []

    @staticmethod
    def _compute_importance(
        model, X: np.ndarray, y: np.ndarray,
        feature_names: list[str], scoring: str,
    ) -> dict[str, float]:
        """permutation_importance で特徴量重要度を算出 (サブサンプル使用)。"""
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
        """pga_tournament_results + pga_season_stats をJOINして訓練データ構築。

        Returns:
            DataFrame with columns: player_id, player_name, year, tournament_id,
                position, made_cut, field_size, made_cut_count, + 6 stats + 4 derived
        """
        if db is None:
            db = PGAStatsDB()

        conn = db._get_conn()
        try:
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

            # Feature engineering
            df = self._engineer_features(df)

            print(f"[INFO] Training data: {len(df)} samples, "
                  f"{df['year'].nunique()} years, "
                  f"{df['tournament_id'].nunique()} tournaments")
            print(f"[INFO] Made cut: {df['made_cut'].sum()}, "
                  f"Missed cut: {(~df['made_cut'].astype(bool)).sum()}")

            return df

        finally:
            conn.close()

    def _engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Derived features: rank, field_strength, relative_strength, position_pct."""
        # scoring_average_rank: WGR proxy (低いscoring_average = 高ランク)
        df["scoring_average_rank"] = (
            df.groupby("year")["scoring_average"]
            .rank(method="min", na_option="bottom")
        )

        # field_strength: 大会内のscoring_average平均
        df["field_strength"] = (
            df.groupby(["tournament_id", "year"])["scoring_average"]
            .transform("mean")
        )

        # player_relative_strength: 選手がフィールド平均よりどれだけ良いか
        df["player_relative_strength"] = df["field_strength"] - df["scoring_average"]

        # position_pct: カット通過者の相対順位 (0.0-1.0)
        made_cut_mask = df["made_cut"] == 1
        df.loc[made_cut_mask, "position_pct"] = (
            df.loc[made_cut_mask, "position"] / df.loc[made_cut_mask, "made_cut_count"]
        )

        return df

    def train(self, db: PGAStatsDB | None = None) -> EGSTrainingResult | None:
        """メインの訓練パイプライン。

        Returns:
            EGSTrainingResult or None (データ不足時)
        """
        df = self.build_training_data(db)
        if df.empty or len(df) < MIN_TRAINING_SAMPLES:
            print(f"[WARN] Insufficient data: {len(df)} samples (need {MIN_TRAINING_SAMPLES}+)")
            return None

        # 利用可能な特徴量検出
        available = []
        for feat in EGS_FEATURES:
            if feat in df.columns and df[feat].notna().sum() > len(df) * 0.05:
                available.append(feat)

        if len(available) < 4:
            print(f"[WARN] Not enough features: {len(available)} (need 4+)")
            return None

        self.features_used = available
        print(f"[INFO] Using {len(available)} features: {available}")

        # Year groups for CV (時系列リーク防止)
        groups = df["year"].values

        #----- CutClassifier -----
        print("\n--- CutClassifier Training ---")
        X_cut = df[available].values
        y_cut = df["made_cut"].values

        # GroupKFold CV
        gkf = GroupKFold(n_splits=min(5, df["year"].nunique()))

        # ROC-AUC
        cut_auc_scores = cross_val_score(
            self.cut_classifier, X_cut, y_cut,
            cv=gkf, groups=groups, scoring="roc_auc",
        )
        cut_auc = float(cut_auc_scores.mean())
        print(f"[INFO] ROC-AUC (CV): {cut_auc:.4f} (+/- {cut_auc_scores.std():.4f})")

        # Accuracy
        cut_acc_scores = cross_val_score(
            self.cut_classifier, X_cut, y_cut,
            cv=gkf, groups=groups, scoring="accuracy",
        )
        cut_acc = float(cut_acc_scores.mean())
        print(f"[INFO] Accuracy (CV): {cut_acc:.4f}")

        # Brier score (cross_val_predict で算出)
        cut_proba_cv = cross_val_predict(
            self.cut_classifier, X_cut, y_cut,
            cv=gkf, groups=groups, method="predict_proba",
        )
        cut_brier = float(brier_score_loss(y_cut, cut_proba_cv[:, 1]))
        print(f"[INFO] Brier Score (CV): {cut_brier:.4f}")

        # Full train
        self.cut_classifier.fit(X_cut, y_cut)
        cut_importance = self._compute_importance(
            self.cut_classifier, X_cut, y_cut, available, "accuracy",
        )
        print("[INFO] CutClassifier feature importance:")
        for feat, imp in sorted(cut_importance.items(), key=lambda x: -x[1]):
            bar = "#" * int(imp * 50)
            print(f"  {feat:<28} {imp:.3f} {bar}")

        #----- PositionRegressor -----
        print("\n--- PositionRegressor Training ---")
        made_cut_df = df[df["made_cut"] == 1].copy()
        X_pos = made_cut_df[available].values
        y_pos = made_cut_df["position_pct"].values
        groups_pos = made_cut_df["year"].values

        gkf_pos = GroupKFold(n_splits=min(5, made_cut_df["year"].nunique()))

        # MAE on position_pct
        pos_mae_scores = cross_val_score(
            self.position_regressor, X_pos, y_pos,
            cv=gkf_pos, groups=groups_pos, scoring="neg_mean_absolute_error",
        )
        pos_mae = float(-pos_mae_scores.mean())
        print(f"[INFO] MAE position_pct (CV): {pos_mae:.4f} (+/- {pos_mae_scores.std():.4f})")

        # R2
        pos_r2_scores = cross_val_score(
            self.position_regressor, X_pos, y_pos,
            cv=gkf_pos, groups=groups_pos, scoring="r2",
        )
        pos_r2 = float(pos_r2_scores.mean())
        print(f"[INFO] R2 (CV): {pos_r2:.4f}")

        # Raw position MAE (position_pct * mean_made_cut_count)
        mean_cut_count = made_cut_df["made_cut_count"].mean()
        pos_mae_raw = pos_mae * mean_cut_count
        print(f"[INFO] MAE raw position (CV, approx): {pos_mae_raw:.1f} places")

        # Full train
        self.position_regressor.fit(X_pos, y_pos)
        pos_importance = self._compute_importance(
            self.position_regressor, X_pos, y_pos, available, "neg_mean_absolute_error",
        )
        print("[INFO] PositionRegressor feature importance:")
        for feat, imp in sorted(pos_importance.items(), key=lambda x: -x[1]):
            bar = "#" * int(imp * 50)
            print(f"  {feat:<28} {imp:.3f} {bar}")

        # Save
        result = EGSTrainingResult(
            n_samples_cut=len(df),
            n_samples_position=len(made_cut_df),
            features_used=available,
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

    def _save_models(self, result: EGSTrainingResult) -> None:
        """モデルとメタデータをjoblib/JSONで保存。"""
        import joblib

        MODEL_DIR.mkdir(parents=True, exist_ok=True)

        # CutClassifier
        cut_data = {
            "model": self.cut_classifier,
            "features_used": self.features_used,
        }
        joblib.dump(cut_data, CUT_MODEL_PATH)
        print(f"[OK] CutClassifier saved: {CUT_MODEL_PATH}")

        # PositionRegressor
        pos_data = {
            "model": self.position_regressor,
            "features_used": self.features_used,
        }
        joblib.dump(pos_data, POS_MODEL_PATH)
        print(f"[OK] PositionRegressor saved: {POS_MODEL_PATH}")

        # Metadata (JSON)
        metadata = {
            "model_type": "EGS 2-Stage (CutClassifier + PositionRegressor)",
            "cut_model": "HistGradientBoostingClassifier",
            "position_model": "HistGradientBoostingRegressor",
            "n_samples_cut": result.n_samples_cut,
            "n_samples_position": result.n_samples_position,
            "features_used": result.features_used,
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
        with open(EGS_METADATA_PATH, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        print(f"[OK] Metadata saved: {EGS_METADATA_PATH}")

        # Append to training history
        self._append_history(metadata)

    def _append_history(self, metadata: dict) -> None:
        """訓練履歴をJSONファイルに追記。"""
        history: list[dict] = []
        if EGS_HISTORY_PATH.exists():
            try:
                with open(EGS_HISTORY_PATH, encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, ValueError):
                history = []

        history.append(metadata)

        with open(EGS_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        print(f"[OK] Training history updated: {EGS_HISTORY_PATH} ({len(history)} entries)")

    @staticmethod
    def print_info() -> None:
        """保存済みモデルの情報を表示。"""
        if not EGS_METADATA_PATH.exists():
            print("[INFO] No EGS model metadata found.")
            print(f"  Run: uv run python -m src.egs_model_trainer")
            return

        with open(EGS_METADATA_PATH, encoding="utf-8") as f:
            meta = json.load(f)

        print("=" * 60)
        print("  EGS Model Info")
        print("=" * 60)
        print(f"  Type:     {meta['model_type']}")
        print(f"  Trained:  {meta['trained_at']}")
        print(f"  Features: {len(meta['features_used'])}")
        for feat in meta["features_used"]:
            print(f"    - {feat}")
        print()
        print("  --- CutClassifier ---")
        print(f"  Samples:  {meta['n_samples_cut']}")
        print(f"  ROC-AUC:  {meta['cut_roc_auc_cv']}")
        print(f"  Brier:    {meta['cut_brier_cv']}")
        print(f"  Accuracy: {meta['cut_accuracy_cv']}")
        print()
        print("  --- PositionRegressor ---")
        print(f"  Samples:  {meta['n_samples_position']}")
        print(f"  MAE(pct): {meta['pos_mae_cv']}")
        print(f"  R2:       {meta['pos_r2_cv']}")
        print(f"  MAE(raw): {meta['pos_mae_raw_cv']} places")
        print()
        print(f"  Files:")
        print(f"    {CUT_MODEL_PATH} {'[OK]' if CUT_MODEL_PATH.exists() else '[MISSING]'}")
        print(f"    {POS_MODEL_PATH} {'[OK]' if POS_MODEL_PATH.exists() else '[MISSING]'}")


#-----CLI-----

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="EGS Model Trainer")
    parser.add_argument("--info", action="store_true", help="Show model info")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate only (no retrain)")
    args = parser.parse_args()

    if args.info:
        EGSModelTrainer.print_info()
        return 0

    if args.evaluate:
        # 既存モデルのCV評価のみ（再訓練しない）
        EGSModelTrainer.print_info()
        return 0

    # 訓練実行
    print("=" * 60)
    print("  EGS Model Training")
    print("=" * 60)

    trainer = EGSModelTrainer()
    result = trainer.train()

    if result is None:
        print("[ERROR] Training failed.")
        return 1

    print()
    print("=" * 60)
    print("  Training Complete!")
    print("=" * 60)
    print(f"  CutClassifier:     ROC-AUC={result.cut_roc_auc_cv:.4f}, "
          f"Brier={result.cut_brier_cv:.4f}")
    print(f"  PositionRegressor: MAE={result.pos_mae_cv:.4f} "
          f"(~{result.pos_mae_raw_cv:.1f} places), R2={result.pos_r2_cv:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
