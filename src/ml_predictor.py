"""ML統合予測モデル。

ODDS・STATS・CourseFitの3つの予測信号を機械学習で最適統合し、
統一された ML Prediction Score (0-100) を算出する。

Phase 1 (Proxy): PGA大会結果+シーズン統計でGBTモデルを訓練
Phase 2 (Full): グループ結果蓄積後、グループ勝者予測モデルを訓練
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler

from .pga_stats_db import PGAStatsDB


#-----Constants-----

MODEL_DIR = Path("data/models")
STATS_MODEL_PATH = MODEL_DIR / "stats_predictor_v1.joblib"
METADATA_PATH = MODEL_DIR / "model_metadata.json"
WEIGHTS_PATH = MODEL_DIR / "ensemble_weights.json"

# 学習に使用する統計特徴量
STAT_FEATURES = [
    "sg_approach", "sg_off_tee", "sg_tee_to_green",
    "sg_putting", "sg_around_green",
    "gir_pct", "driving_distance", "driving_accuracy_pct",
    "scrambling_pct", "scoring_average",
]

# デフォルトのアンサンブルウェイト（市場効率性研究に基づく理論値）
DEFAULT_WEIGHTS = {
    "odds": 0.45,
    "stats": 0.35,
    "course_fit": 0.20,
}


#-----Data Classes-----

@dataclass
class IntegratedPrediction:
    """統合ML予測結果。"""
    player_name: str
    ml_score: float              # 0-100 統合スコア
    odds_component: float        # オッズ由来スコア (0-100)
    stats_component: float       # ML統計スコア (0-100)
    fit_component: float         # コースフィットスコア (0-100)
    crowd_component: float = 0.0 # 群衆知恵スコア (0-100)
    major_affinity_component: float = 0.0  # メジャー適性スコア (0-100)
    ml_rank_in_group: int = 0    # グループ内MLランク
    confidence: str = "Medium"   # "High" / "Medium" / "Low"
    model_version: str = "fixed_v0"


@dataclass
class TrainingResult:
    """モデル訓練結果のサマリー。"""
    model_version: str
    n_samples: int
    n_features: int
    features_used: list[str]
    r2_train: float
    r2_cv_mean: float
    r2_cv_std: float
    mae_cv_mean: float
    feature_importance: dict[str, float]
    trained_at: str = field(default_factory=lambda: datetime.now().isoformat())


#-----Historical Trainer (Phase 1)-----

class HistoricalTrainer:
    """Phase 1: PGA大会結果+シーズン統計でGBTモデルを訓練。

    ターゲット変数: log1p(prize_money)
    特徴量: シーズン統計値（StandardScaler正規化）
    """

    def __init__(
        self,
        n_estimators: int = 100,
        max_depth: int = 4,
        min_samples_leaf: int = 10,
        learning_rate: float = 0.1,
    ):
        self.model = GradientBoostingRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            learning_rate=learning_rate,
            random_state=42,
        )
        self.scaler = StandardScaler()
        self.features_used: list[str] = []

    def build_training_data(self, db: PGAStatsDB) -> pd.DataFrame:
        """pga_tournament_results + pga_season_stats をJOINして訓練データ構築。

        全大会の結果を対象に、各選手のその年のシーズン統計をJOINする。
        prize_money > 0 かつ少なくとも1つの統計値がある行のみ使用。

        Args:
            db: PGAStatsDBインスタンス

        Returns:
            DataFrame: [player_name, year, prize_money, sg_approach, ...]
        """
        conn = db._get_conn()
        try:
            rows = conn.execute("""
                SELECT
                    tr.player_id,
                    tr.player_name,
                    tr.year,
                    tr.tournament_id,
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
                WHERE tr.prize_money IS NOT NULL AND tr.prize_money > 0
                GROUP BY tr.player_id, tr.tournament_id, tr.year
                HAVING sg_approach IS NOT NULL OR sg_off_tee IS NOT NULL
                    OR sg_tee_to_green IS NOT NULL OR gir_pct IS NOT NULL
            """).fetchall()

            if not rows:
                return pd.DataFrame()

            df = pd.DataFrame([dict(r) for r in rows])
            print(f"[INFO] Built training dataset: {len(df)} samples, "
                  f"{df['year'].nunique()} years, {df['tournament_id'].nunique()} tournaments")
            return df

        finally:
            conn.close()

    def train(self, db: PGAStatsDB | None = None) -> TrainingResult | None:
        """モデルを訓練し、結果を保存。

        Args:
            db: PGAStatsDBインスタンス

        Returns:
            TrainingResult or None（データ不足時）
        """
        if db is None:
            db = PGAStatsDB()

        # 訓練データ構築
        df = self.build_training_data(db)
        if df.empty or len(df) < 50:
            print(f"[WARN] Insufficient training data: {len(df)} samples (need 50+)")
            return None

        # 利用可能な特徴量を検出
        available_features = []
        for feat in STAT_FEATURES:
            if feat in df.columns and df[feat].notna().sum() > len(df) * 0.1:
                available_features.append(feat)

        if len(available_features) < 3:
            print(f"[WARN] Not enough features: {len(available_features)} (need 3+)")
            return None

        self.features_used = available_features
        print(f"[INFO] Using {len(available_features)} features: {available_features}")

        # 欠損値の処理（中央値で埋める）
        X = df[available_features].copy()
        for col in available_features:
            median_val = X[col].median()
            X[col] = X[col].fillna(median_val)

        # ターゲット変数: log1p(prize_money)
        y = np.log1p(df["prize_money"].values)

        # スケーリング
        X_scaled = self.scaler.fit_transform(X)

        # 交差検証
        cv_r2 = cross_val_score(self.model, X_scaled, y, cv=5, scoring="r2")
        cv_mae = cross_val_score(self.model, X_scaled, y, cv=5, scoring="neg_mean_absolute_error")

        print(f"[INFO] Cross-validation R2: {cv_r2.mean():.4f} (+/- {cv_r2.std():.4f})")
        print(f"[INFO] Cross-validation MAE: {-cv_mae.mean():.4f} (+/- {cv_mae.std():.4f})")

        # 全データで訓練
        self.model.fit(X_scaled, y)
        r2_train = self.model.score(X_scaled, y)
        print(f"[INFO] Training R2: {r2_train:.4f}")

        # 特徴量重要度
        importance = dict(zip(
            available_features,
            self.model.feature_importances_.tolist(),
        ))
        sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)
        print("[INFO] Feature Importance:")
        for feat, imp in sorted_imp:
            bar = "#" * int(imp * 50)
            print(f"  {feat:<25} {imp:.4f} {bar}")

        # モデル保存
        self._save_model(importance, len(df), cv_r2, cv_mae)

        return TrainingResult(
            model_version="proxy_v1",
            n_samples=len(df),
            n_features=len(available_features),
            features_used=available_features,
            r2_train=r2_train,
            r2_cv_mean=float(cv_r2.mean()),
            r2_cv_std=float(cv_r2.std()),
            mae_cv_mean=float(-cv_mae.mean()),
            feature_importance=importance,
        )

    def _save_model(
        self,
        feature_importance: dict,
        n_samples: int,
        cv_r2: np.ndarray,
        cv_mae: np.ndarray,
    ) -> None:
        """モデルとメタデータを保存。"""
        import joblib

        MODEL_DIR.mkdir(parents=True, exist_ok=True)

        # モデル保存
        model_data = {
            "model": self.model,
            "scaler": self.scaler,
            "features_used": self.features_used,
        }
        joblib.dump(model_data, STATS_MODEL_PATH)
        print(f"[OK] Model saved: {STATS_MODEL_PATH}")

        # メタデータ保存
        metadata = {
            "model_version": "proxy_v1",
            "model_type": "GradientBoostingRegressor",
            "n_samples": n_samples,
            "n_features": len(self.features_used),
            "features_used": self.features_used,
            "r2_cv_mean": float(cv_r2.mean()),
            "r2_cv_std": float(cv_r2.std()),
            "mae_cv_mean": float(-cv_mae.mean()),
            "feature_importance": feature_importance,
            "trained_at": datetime.now().isoformat(),
            "scaler_mean": self.scaler.mean_.tolist(),
            "scaler_scale": self.scaler.scale_.tolist(),
        }
        METADATA_PATH.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[OK] Metadata saved: {METADATA_PATH}")

    def predict_score(self, stats_dict: dict[str, float | None]) -> float | None:
        """選手の統計値から予測スコアを算出。

        Args:
            stats_dict: {stat_name: stat_value}

        Returns:
            0-100のスコア。データ不足ならNone
        """
        if not self.features_used:
            return None

        # 特徴量ベクトル作成
        values = []
        missing_count = 0
        for feat in self.features_used:
            val = stats_dict.get(feat)
            if val is None:
                missing_count += 1
                values.append(0)  # scalerで補正される
            else:
                values.append(val)

        if missing_count > len(self.features_used) * 0.5:
            return None  # 半分以上欠損なら予測不可

        X = pd.DataFrame([values], columns=self.features_used)
        X_scaled = self.scaler.transform(X)
        pred_log = self.model.predict(X_scaled)[0]

        return pred_log  # 生の予測値（後でmin-maxスケーリングして0-100にする）


#-----Ensemble Predictor-----

class EnsemblePredictor:
    """3つの予測信号を統合してML Prediction Scoreを算出。

    Phase 1: 固定 or 学習済みウェイトで合算
    Phase 2: グループ結果学習モデルで予測（将来実装）
    """

    def __init__(self, weights: dict[str, float] | None = None):
        """初期化。

        Args:
            weights: {"odds": 0.45, "stats": 0.35, "course_fit": 0.20}
        """
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self.model_version = "fixed_v0"

    def predict(
        self,
        odds_score: float,
        stats_score: float | None,
        fit_score: float | None,
        crowd_score: float | None = None,
        major_affinity_score: float | None = None,
    ) -> float:
        """信号を統合してスコアを算出。

        各入力は0-100スケールを想定。Noneの場合は残りの信号で
        ウェイトを再配分する。

        Args:
            odds_score: オッズ由来スコア (0-100)
            stats_score: ML統計スコア (0-100)
            fit_score: コースフィットスコア (0-100)
            crowd_score: 群衆知恵スコア (0-100)
            major_affinity_score: メジャー適性スコア (0-100)

        Returns:
            0-100の統合スコア
        """
        components = {}
        active_weights = {}

        # オッズは常に利用可能
        components["odds"] = odds_score
        active_weights["odds"] = self.weights["odds"]

        if stats_score is not None:
            components["stats"] = stats_score
            active_weights["stats"] = self.weights["stats"]

        if fit_score is not None:
            components["course_fit"] = fit_score
            active_weights["course_fit"] = self.weights["course_fit"]

        if crowd_score is not None and "crowd" in self.weights:
            components["crowd"] = crowd_score
            active_weights["crowd"] = self.weights["crowd"]

        if major_affinity_score is not None and "major_affinity" in self.weights:
            components["major_affinity"] = major_affinity_score
            active_weights["major_affinity"] = self.weights["major_affinity"]

        # ウェイトを正規化（合計1.0）
        total_weight = sum(active_weights.values())
        if total_weight <= 0:
            return odds_score  # フォールバック

        normalized = {k: v / total_weight for k, v in active_weights.items()}

        # 加重平均
        score = sum(components[k] * normalized[k] for k in components)
        return max(0.0, min(100.0, score))

    def get_confidence(
        self,
        has_stats: bool,
        has_fit: bool,
        model_version: str,
        has_crowd: bool = False,
        has_major_affinity: bool = False,
    ) -> str:
        """信頼度を判定。

        Args:
            has_stats: ML統計スコアがあるか
            has_fit: コースフィットスコアがあるか
            model_version: 使用モデルバージョン
            has_crowd: 群衆知恵スコアがあるか
            has_major_affinity: メジャー適性スコアがあるか

        Returns:
            "High" / "Medium" / "Low"
        """
        signals = 1  # オッズは常にある
        if has_stats:
            signals += 1
        if has_fit:
            signals += 1
        if has_crowd:
            signals += 1
        if has_major_affinity:
            signals += 1

        if signals >= 3 and model_version != "fixed_v0":
            return "High"
        elif signals >= 2:
            return "Medium"
        else:
            return "Low"


#-----Model Loading & Selection-----

def get_active_model() -> tuple[str, Any]:
    """利用可能な最良のモデルを自動選択。

    Returns:
        (model_version, model_data_or_weights)
        "proxy_v1": ヒストリカルGBTモデル
        "fixed_v0": 理論的固定ウェイト
    """
    # Phase 1: Proxy model
    if STATS_MODEL_PATH.exists():
        try:
            import joblib
            model_data = joblib.load(STATS_MODEL_PATH)
            print(f"[INFO] Loaded ML model: proxy_v1 ({STATS_MODEL_PATH})")
            return "proxy_v1", model_data
        except Exception as e:
            print(f"[WARN] Failed to load model: {e}")

    # フォールバック: 固定ウェイト
    print("[INFO] No trained model found, using fixed weights (fixed_v0)")
    return "fixed_v0", DEFAULT_WEIGHTS.copy()


def _odds_to_score(implied_prob: float, group_probs: list[float]) -> float:
    """グループ内のimplied probabilityを0-100スコアに変換。

    グループ内の最大prob = 100, 最小prob = 0 として線形変換。

    Args:
        implied_prob: 選手のimplied probability
        group_probs: グループ内全選手のimplied probability

    Returns:
        0-100のスコア
    """
    if not group_probs:
        return 50.0
    min_p = min(group_probs)
    max_p = max(group_probs)
    if max_p == min_p:
        return 50.0
    return ((implied_prob - min_p) / (max_p - min_p)) * 100.0


def _stats_to_score(
    raw_pred: float,
    group_preds: list[float],
) -> float:
    """グループ内の生予測値を0-100スコアに変換。

    Args:
        raw_pred: 選手の生予測値
        group_preds: グループ内全選手の生予測値

    Returns:
        0-100のスコア
    """
    if not group_preds:
        return 50.0
    min_v = min(group_preds)
    max_v = max(group_preds)
    if max_v == min_v:
        return 50.0
    return ((raw_pred - min_v) / (max_v - min_v)) * 100.0


#-----Pipeline Integration-----

def run_ml_prediction(
    groups: dict,
    tournament_name: str = "",
    course_fit: dict | None = None,
    config: dict | None = None,
) -> dict | None:
    """パイプライン統合用エントリポイント。

    各グループの選手にML統合スコアを付与する。

    Args:
        groups: {group_id: [GroupPlayer, ...]}
        tournament_name: トーナメント名
        course_fit: コースフィット分析結果
        config: ml_prediction設定

    Returns:
        {
            "predictions": {player_name: IntegratedPrediction},
            "model_version": str,
            "model_info": dict,
        }
    """
    config = config or {}

    # モデル選択
    model_version, model_data = get_active_model()

    # HistoricalTrainer（proxy_v1の場合）
    trainer = None
    if model_version == "proxy_v1" and isinstance(model_data, dict):
        trainer = HistoricalTrainer()
        trainer.model = model_data["model"]
        trainer.scaler = model_data["scaler"]
        trainer.features_used = model_data["features_used"]

    # アンサンブルウェイト
    weights = config.get("default_weights", DEFAULT_WEIGHTS)
    ensemble = EnsemblePredictor(weights)
    ensemble.model_version = model_version

    # コースフィットスコアマップ
    fit_scores: dict[str, float] = {}
    if course_fit and course_fit.get("scores"):
        for s in course_fit["scores"]:
            fit_scores[s["player_name"]] = s.get("fit_score", 0) or 0

    # 群衆知恵スコアマップ
    crowd_scores: dict[str, float] = {}
    if "crowd" in weights:
        try:
            from .pickem_features import get_crowd_score_for_group
            all_names = [p.name for players in groups.values() for p in players]
            crowd_scores = get_crowd_score_for_group(all_names)
            if crowd_scores:
                print(f"[INFO] Crowd wisdom signals: {len(crowd_scores)} players")
        except Exception as e:
            print(f"[WARN] Could not load crowd signals: {e}")

    # メジャーアフィニティスコアマップ
    major_scores: dict[str, float | None] = {}
    major_data = None
    ma_config = config.get("major_affinity", {}) if config else {}
    if ma_config.get("enabled", False):
        try:
            from .major_affinity import compute_major_affinity
            major_data = compute_major_affinity(groups, tournament_name, ma_config)
            if major_data and major_data.get("is_major"):
                major_scores = major_data.get("scores", {})
        except Exception as e:
            print(f"[WARN] Could not compute major affinity: {e}")

    # 各グループで予測
    predictions: dict[str, IntegratedPrediction] = {}
    total_scored = 0

    for gid in sorted(groups.keys()):
        players = groups[gid]

        # Step 1: オッズスコア算出
        group_probs = [p.implied_prob for p in players if p.implied_prob > 0]
        odds_scores = {}
        for p in players:
            if p.implied_prob > 0:
                odds_scores[p.name] = _odds_to_score(p.implied_prob, group_probs)
            else:
                odds_scores[p.name] = 50.0

        # Step 2: ML統計スコア算出
        stats_raw: dict[str, float] = {}
        if trainer:
            for p in players:
                if p.stats is not None:
                    stats_dict = {}
                    stat_obj = p.stats
                    for feat in trainer.features_used:
                        # PlayerStatsのフィールドマッピング
                        if feat == "gir_pct":
                            val = getattr(stat_obj, "greens_in_regulation_pct", None)
                        else:
                            val = getattr(stat_obj, feat, None)
                        stats_dict[feat] = val

                    raw = trainer.predict_score(stats_dict)
                    if raw is not None:
                        stats_raw[p.name] = raw

        group_raw_list = list(stats_raw.values()) if stats_raw else []
        stats_scores: dict[str, float | None] = {}
        for p in players:
            if p.name in stats_raw:
                stats_scores[p.name] = _stats_to_score(stats_raw[p.name], group_raw_list)
            else:
                stats_scores[p.name] = None

        # Step 3: 統合スコア算出
        group_predictions: list[tuple[str, float]] = []
        for p in players:
            odds_sc = odds_scores.get(p.name, 50.0)
            stats_sc = stats_scores.get(p.name)
            fit_sc = fit_scores.get(p.name)
            crowd_sc = crowd_scores.get(p.name)
            major_sc = major_scores.get(p.name)

            ml_score = ensemble.predict(odds_sc, stats_sc, fit_sc, crowd_sc, major_sc)
            confidence = ensemble.get_confidence(
                has_stats=stats_sc is not None,
                has_fit=fit_sc is not None,
                model_version=model_version,
                has_crowd=crowd_sc is not None,
                has_major_affinity=major_sc is not None,
            )

            pred = IntegratedPrediction(
                player_name=p.name,
                ml_score=ml_score,
                odds_component=odds_sc,
                stats_component=stats_sc if stats_sc is not None else 0.0,
                fit_component=fit_sc if fit_sc is not None else 0.0,
                crowd_component=crowd_sc if crowd_sc is not None else 0.0,
                major_affinity_component=major_sc if major_sc is not None else 0.0,
                confidence=confidence,
                model_version=model_version,
            )
            predictions[p.name] = pred
            group_predictions.append((p.name, ml_score))
            total_scored += 1

        # グループ内ランク付け（スコア降順）
        group_predictions.sort(key=lambda x: x[1], reverse=True)
        for rank, (name, _) in enumerate(group_predictions, 1):
            predictions[name].ml_rank_in_group = rank

    # モデル情報
    model_info = {"version": model_version}
    if METADATA_PATH.exists():
        try:
            meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
            model_info.update({
                "n_samples": meta.get("n_samples"),
                "r2_cv": meta.get("r2_cv_mean"),
                "features": meta.get("features_used"),
                "trained_at": meta.get("trained_at"),
                "feature_importance": meta.get("feature_importance"),
            })
        except Exception:
            pass

    print(f"[OK] ML predictions: {total_scored} players scored "
          f"(model={model_version}, weights=odds:{weights.get('odds', 0.45)}/"
          f"stats:{weights.get('stats', 0.35)}/fit:{weights.get('course_fit', 0.20)})")

    # GroupPlayer に ML スコアを付与（EGS 最適化の前に必要）
    for gid_ml, players_ml in groups.items():
        for p_ml in players_ml:
            pred = predictions.get(p_ml.name)
            if pred:
                p_ml.ml_score = pred.ml_score
                p_ml.ml_rank_in_group = pred.ml_rank_in_group
                p_ml.ml_confidence = pred.confidence
                p_ml.ml_model_version = pred.model_version
            # メジャーアフィニティスコアを付与
            ma_sc = major_scores.get(p_ml.name)
            if ma_sc is not None:
                p_ml.major_affinity_score = ma_sc

    # Game Score Optimization (EGS)
    egs_result = None
    egs_v2_result = None
    game_cfg = config.get("game_optimization", {}) if config else {}
    if game_cfg.get("enabled", False):
        try:
            from .game_optimizer import optimize_picks, format_egs_report

            # EGS v1
            egs_result = optimize_picks(groups, config=game_cfg, model_version="v1")

            # GroupPlayer に EGS データを付与
            for name, pegs in egs_result.player_egs.items():
                for gid_inner, players_inner in groups.items():
                    for p_inner in players_inner:
                        if p_inner.name == name:
                            p_inner.egs = pegs.egs
                            p_inner.egs_rank_in_group = pegs.egs_rank_in_group
                            p_inner.p_cut = pegs.p_cut
                            p_inner.handicap = pegs.handicap

            print(format_egs_report(egs_result))

            # EGS v2
            try:
                egs_v2_result = optimize_picks(groups, config=game_cfg, model_version="v2")
                print("[OK] EGS v2 optimization complete")
            except Exception as e2:
                print(f"[WARN] EGS v2 optimization failed (non-critical): {e2}")
        except Exception as e:
            print(f"[WARN] Game optimization failed: {e}")

    return {
        "predictions": predictions,
        "model_version": model_version,
        "model_info": model_info,
        "weights": weights,
        "egs_result": egs_result,
        "egs_v2_result": egs_v2_result,
        "major_data": major_data,
    }


#-----Text Report-----

def format_ml_report(
    groups: dict,
    ml_result: dict,
) -> str:
    """ML統合予測の見やすいテキストレポートを生成。

    Args:
        groups: {group_id: [GroupPlayer, ...]}
        ml_result: run_ml_prediction() の戻り値

    Returns:
        フォーマット済みレポート文字列
    """
    from tabulate import tabulate

    predictions = ml_result.get("predictions", {})
    model_info = ml_result.get("model_info", {})
    weights = ml_result.get("weights", {})
    model_version = ml_result.get("model_version", "unknown")

    lines: list[str] = []

    # ── ヘッダー ──
    lines.append("")
    lines.append("=" * 90)
    lines.append("  ML INTEGRATED PREDICTION")
    lines.append("=" * 90)
    lines.append(f"  Model: {model_version}   |   "
                 f"Weights: Odds {weights.get('odds', 0.45):.0%} / "
                 f"Stats {weights.get('stats', 0.35):.0%} / "
                 f"Fit {weights.get('course_fit', 0.20):.0%}")

    if model_info.get("n_samples"):
        lines.append(f"  Training: {model_info['n_samples']:,} samples   |   "
                     f"R2(CV): {model_info.get('r2_cv', 0):.4f}")
    lines.append("")

    # ── セクション1: グループ別 ML推奨ピック（1位のみ） ──
    lines.append("-" * 90)
    lines.append("  TOP PICKS (ML #1 per Group)")
    lines.append("-" * 90)

    top_data = []
    for gid in sorted(groups.keys()):
        players = groups[gid]
        # ML 1位を探す
        best_pred = None
        best_player = None
        for p in players:
            pred = predictions.get(p.name)
            if pred and (best_pred is None or pred.ml_score > best_pred.ml_score):
                best_pred = pred
                best_player = p

        if best_pred and best_player:
            odds_rank = [pp.name for pp in players].index(best_player.name) + 1
            agreement = ""
            if odds_rank == 1:
                agreement = "=== AGREE"
            elif odds_rank == 2:
                agreement = "~  Close"
            else:
                agreement = "X  Differ"

            top_data.append([
                f"G{gid}",
                best_player.name,
                f"{best_pred.ml_score:.1f}",
                f"{best_pred.odds_component:.0f}",
                f"{best_pred.stats_component:.0f}",
                f"{best_pred.fit_component:.0f}",
                best_pred.confidence,
                f"#{odds_rank}",
                agreement,
            ])

    headers = ["Grp", "ML #1 Pick", "ML", "Odds", "Stats", "Fit",
               "Conf", "OddsRk", "Agreement"]
    lines.append(tabulate(top_data, headers=headers, tablefmt="simple",
                          colalign=("left", "left", "right", "right", "right",
                                    "right", "center", "center", "left")))
    lines.append("")

    # Odds vs ML 一致率
    agree_count = sum(1 for row in top_data if "AGREE" in row[-1])
    close_count = sum(1 for row in top_data if "Close" in row[-1])
    total = len(top_data)
    lines.append(f"  Odds vs ML Agreement: {agree_count}/{total} exact match, "
                 f"{agree_count + close_count}/{total} within top-2")
    lines.append("")

    # ── セクション2: グループ別 詳細ランキング ──
    lines.append("=" * 90)
    lines.append("  DETAILED RANKINGS BY GROUP")
    lines.append("=" * 90)
    lines.append("")

    for gid in sorted(groups.keys()):
        players = groups[gid]
        lines.append(f"  GROUP {gid}")
        lines.append("-" * 90)

        # MLスコアでソート
        scored = []
        for p in players:
            pred = predictions.get(p.name)
            scored.append((p, pred))
        scored.sort(key=lambda x: x[1].ml_score if x[1] else -1, reverse=True)

        table_data = []
        for p, pred in scored:
            if pred is None:
                continue

            odds_rank = [pp.name for pp in players].index(p.name) + 1
            stats_rank = p.stats_rank_in_group if p.stats_rank_in_group else "-"

            # スコアバー（20文字幅）
            bar_len = int(pred.ml_score / 100 * 20)
            bar = ">" * bar_len + "." * (20 - bar_len)

            # ランク変動マーカー
            ml_rank = pred.ml_rank_in_group
            diff = odds_rank - ml_rank
            if diff > 0:
                arrow = f"+{diff}^"  # MLの方が上
            elif diff < 0:
                arrow = f"{diff}v"   # MLの方が下
            else:
                arrow = " =="        # 同じ

            table_data.append([
                f"#{ml_rank}",
                p.name[:22],
                f"{pred.ml_score:.1f}",
                bar,
                f"{pred.odds_component:.0f}",
                f"{pred.stats_component:.0f}",
                f"{pred.fit_component:.0f}",
                f"#{odds_rank}",
                arrow,
            ])

        headers = ["ML#", "Player", "ML", "Score Bar",
                   "Odds", "Stats", "Fit", "O#", "Shift"]
        lines.append(tabulate(table_data, headers=headers, tablefmt="simple",
                              colalign=("center", "left", "right", "left",
                                        "right", "right", "right", "center", "right")))
        lines.append("")

    # ── セクション3: 信号間の不一致ハイライト ──
    lines.append("=" * 90)
    lines.append("  SIGNAL DISAGREEMENTS (Odds vs ML ranking differs)")
    lines.append("=" * 90)
    lines.append("")

    disagree_data = []
    for gid in sorted(groups.keys()):
        players = groups[gid]
        for p in players:
            pred = predictions.get(p.name)
            if not pred:
                continue
            odds_rank = [pp.name for pp in players].index(p.name) + 1
            ml_rank = pred.ml_rank_in_group
            if odds_rank != ml_rank:
                disagree_data.append([
                    f"G{gid}",
                    p.name[:25],
                    f"#{odds_rank}",
                    f"#{ml_rank}",
                    f"{odds_rank - ml_rank:+d}",
                    f"O:{pred.odds_component:.0f}  S:{pred.stats_component:.0f}  F:{pred.fit_component:.0f}",
                ])

    if disagree_data:
        headers = ["Grp", "Player", "OddsRk", "MLRk", "Shift", "Components"]
        lines.append(tabulate(disagree_data, headers=headers, tablefmt="simple"))
    else:
        lines.append("  All groups: Odds and ML rankings agree perfectly.")
    lines.append("")

    # ── フッター ──
    lines.append("-" * 90)
    lines.append(f"  Total: {sum(len(g) for g in groups.values())} players, "
                 f"{len(groups)} groups  |  "
                 f"Model: {model_version}  |  "
                 f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 90)
    lines.append("")

    return "\n".join(lines)


def save_ml_report(
    groups: dict,
    ml_result: dict,
    output_dir: str = "data/output",
) -> Path:
    """ML統合レポートをテキストファイルに保存。

    Args:
        groups: {group_id: [GroupPlayer, ...]}
        ml_result: run_ml_prediction() の戻り値
        output_dir: 出力ディレクトリ

    Returns:
        保存先パス
    """
    report = format_ml_report(groups, ml_result)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ml_prediction_{date_str}.txt"
    path.write_text(report, encoding="utf-8")

    return path


#-----CLI-----

def main() -> None:
    """CLIエントリポイント。"""
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="ML Integrated Predictor")
    parser.add_argument(
        "--train", action="store_true",
        help="Train Phase 1 model on historical data",
    )
    parser.add_argument(
        "--info", action="store_true",
        help="Show current model information",
    )
    parser.add_argument(
        "--n-estimators", type=int, default=100,
        help="Number of GBT estimators (default: 100)",
    )
    parser.add_argument(
        "--max-depth", type=int, default=4,
        help="Max tree depth (default: 4)",
    )

    args = parser.parse_args()

    if args.train:
        print("=" * 60)
        print("  ML Model Training (Phase 1: Historical GBT)")
        print("=" * 60)

        trainer = HistoricalTrainer(
            n_estimators=args.n_estimators,
            max_depth=args.max_depth,
        )
        result = trainer.train()

        if result:
            print(f"\n{'='*60}")
            print(f"  Training Complete")
            print(f"{'='*60}")
            print(f"  Model: {result.model_version}")
            print(f"  Samples: {result.n_samples:,}")
            print(f"  Features: {result.n_features}")
            print(f"  R2 (CV): {result.r2_cv_mean:.4f} (+/- {result.r2_cv_std:.4f})")
            print(f"  MAE (CV): {result.mae_cv_mean:.4f}")
            print(f"  Saved to: {STATS_MODEL_PATH}")
        else:
            print("\n[ERROR] Training failed. Run historical collection first:")
            print("  uv run python -m src.tournament_fetcher --collect --start-year 2018")
        return

    if args.info:
        print("=" * 60)
        print("  ML Model Information")
        print("=" * 60)

        version, _ = get_active_model()
        print(f"\n  Active Model: {version}")

        if METADATA_PATH.exists():
            meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
            print(f"  Type: {meta.get('model_type')}")
            print(f"  Samples: {meta.get('n_samples', 0):,}")
            print(f"  Features: {meta.get('n_features', 0)}")
            print(f"  R2 (CV): {meta.get('r2_cv_mean', 0):.4f}")
            print(f"  MAE (CV): {meta.get('mae_cv_mean', 0):.4f}")
            print(f"  Trained: {meta.get('trained_at', 'N/A')}")

            fi = meta.get("feature_importance", {})
            if fi:
                print(f"\n  Feature Importance:")
                for feat, imp in sorted(fi.items(), key=lambda x: x[1], reverse=True):
                    bar = "#" * int(imp * 50)
                    print(f"    {feat:<25} {imp:.4f} {bar}")
        else:
            print("  No model metadata found.")
            print("  Train: uv run python -m src.ml_predictor --train")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
