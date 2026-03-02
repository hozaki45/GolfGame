"""ゲームスコア最適化モジュール。

Expected Game Score (EGS) に基づくピック最適化。
ml_score による「誰が上位に来るか」予測ではなく、
実際のゲームルール（ハンデ・CUTペナルティ・ボーナス）を考慮して
合計ゲームスコアを最小化するピックを選択する。

P(cut) と E[position] の推定にMLモデル (HistGradientBoosting) を使用。
モデル未訓練の場合はヒューリスティックにフォールバック。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import numpy as np
import yaml


#-----Constants-----

EGS_MODEL_DIR = Path("data/models")
CUT_MODEL_PATH = EGS_MODEL_DIR / "egs_cut_classifier.joblib"
POS_MODEL_PATH = EGS_MODEL_DIR / "egs_position_regressor.joblib"

# WGRレンジ → P(cut) デフォルトテーブル
DEFAULT_WGR_CUT_TABLE: list[tuple[int, float]] = [
    (10, 0.05),
    (30, 0.10),
    (50, 0.15),
    (100, 0.22),
    (150, 0.30),
    (200, 0.38),
    (999, 0.50),
]

DEFAULT_CUT_RATE = 0.43  # PGA平均: フィールドの約43%がカット


#-----ML Model State-----

_cut_classifier = None
_position_regressor = None
_egs_features: list[str] = []
_ml_models_loaded = False


def _load_egs_models() -> bool:
    """EGS用MLモデルをロード。初回呼び出し時に1回だけ実行。"""
    global _cut_classifier, _position_regressor, _egs_features, _ml_models_loaded
    _ml_models_loaded = True

    if not CUT_MODEL_PATH.exists() or not POS_MODEL_PATH.exists():
        print("[INFO] EGS ML models not found, using heuristic estimation")
        return False

    try:
        import joblib
        cut_data = joblib.load(CUT_MODEL_PATH)
        pos_data = joblib.load(POS_MODEL_PATH)
        _cut_classifier = cut_data["model"]
        _position_regressor = pos_data["model"]
        _egs_features = cut_data["features_used"]
        print(f"[OK] EGS ML models loaded ({len(_egs_features)} features)")
        return True
    except Exception as e:
        print(f"[WARN] Failed to load EGS models: {e}")
        _cut_classifier = None
        _position_regressor = None
        return False


def _build_feature_vector(
    wgr: int,
    player_stats: dict[str, float] | None,
    field_size: int | None,
    field_scoring_avg: float | None,
) -> np.ndarray | None:
    """選手の特徴量ベクトルを構築。

    Returns:
        shape (1, n_features) の numpy array、または構築不可なら None
    """
    if not _egs_features or player_stats is None:
        return None

    values = []
    has_any_stat = False
    for feat in _egs_features:
        if feat in player_stats and player_stats[feat] is not None:
            values.append(player_stats[feat])
            has_any_stat = True
        elif feat == "scoring_average_rank":
            values.append(float(wgr) if wgr else np.nan)
        elif feat == "field_size":
            values.append(float(field_size) if field_size else np.nan)
        elif feat == "field_strength":
            values.append(float(field_scoring_avg) if field_scoring_avg else np.nan)
        elif feat == "player_relative_strength":
            sa = player_stats.get("scoring_average")
            if sa is not None and field_scoring_avg is not None:
                values.append(field_scoring_avg - sa)
            else:
                values.append(np.nan)
        else:
            values.append(np.nan)

    if not has_any_stat:
        return None

    return np.array(values, dtype=np.float64).reshape(1, -1)


def _predict_p_cut_ml(
    wgr: int,
    player_stats: dict[str, float] | None,
    field_size: int | None,
    field_scoring_avg: float | None,
) -> float | None:
    """MLモデルでP(cut)を予測。"""
    if _cut_classifier is None:
        return None
    features = _build_feature_vector(wgr, player_stats, field_size, field_scoring_avg)
    if features is None:
        return None
    proba = _cut_classifier.predict_proba(features)[0]
    # classes_: [0=missed_cut, 1=made_cut] → P(cut) = proba[0]
    return float(proba[0])


def _predict_e_position_ml(
    wgr: int,
    player_stats: dict[str, float] | None,
    field_size: int | None,
    field_scoring_avg: float | None,
    e_cut_count: int,
) -> float | None:
    """MLモデルでE[position | made_cut]を予測。"""
    if _position_regressor is None:
        return None
    features = _build_feature_vector(wgr, player_stats, field_size, field_scoring_avg)
    if features is None:
        return None
    position_pct = float(_position_regressor.predict(features)[0])
    position_pct = max(0.01, min(1.0, position_pct))
    return position_pct * e_cut_count


#-----Data Classes-----

@dataclass
class PlayerEGS:
    """1選手のExpected Game Score算出結果。"""
    player_name: str
    group_id: int
    wgr: int
    handicap: int
    p_cut: float              # CUT確率
    e_position: float         # E[position | make_cut]
    e_cut_score: float        # CUT時のゲームスコア
    e_made_cut_score: float   # カット通過時のE[game_score]
    e_bonuses: float          # E[bonuses]
    egs: float                # 最終EGS値（低い=良い）
    egs_rank_in_group: int = 0
    estimation_method: str = "heuristic"  # "ml" or "heuristic"


@dataclass
class EGSResult:
    """全グループのEGS最適化結果。"""
    picks: dict[int, list[str]]          # {group_id: [player_name, ...]}
    ml_picks: dict[int, list[str]]       # {group_id: [ml_pick_name, ...]}
    player_egs: dict[str, PlayerEGS]     # {player_name: PlayerEGS}
    total_egs: float
    ml_total_egs: float
    field_params: dict
    agree_count: int                     # ML一致グループ数
    total_groups: int


#-----Field Parameters-----

def _estimate_field_params(
    groups: dict,
    field_size: int | None = None,
    config: dict | None = None,
) -> dict:
    """フィールドパラメータ推定。

    Args:
        groups: {group_id: [GroupPlayer, ...]}
        field_size: 既知の場合はフィールドサイズ
        config: game_optimization config
    """
    if field_size is None or field_size == 0:
        field_size = sum(len(players) for players in groups.values())

    cut_rate = DEFAULT_CUT_RATE
    if config:
        cut_rate = config.get("cut_rate_default", DEFAULT_CUT_RATE)

    e_cut_count = round(field_size * (1 - cut_rate))
    max_handicap = round(0.13 * field_size)

    # グループ別の平均CUT数推定
    group_sizes: dict[int, int] = {}
    for gid, players in groups.items():
        group_sizes[gid] = len(players)

    return {
        "field_size": field_size,
        "cut_rate": cut_rate,
        "e_cut_count": e_cut_count,
        "max_handicap": max_handicap,
        "group_sizes": group_sizes,
    }


#-----Handicap-----

def _calc_handicap(wgr: int, max_handicap: int) -> int:
    """ハンデキャップ算出。round(WGR/100), max=max_handicap。"""
    hc = round(wgr / 100)
    return min(hc, max_handicap)


#-----P(cut) Estimation-----

def _p_cut_from_wgr(wgr: int, wgr_table: list[tuple[int, float]] | None = None) -> float:
    """WGRからCUT確率を推定。"""
    table = wgr_table or DEFAULT_WGR_CUT_TABLE
    for threshold, p_cut in table:
        if wgr <= threshold:
            return p_cut
    return 0.50


def _p_cut_from_odds(implied_prob: float) -> float:
    """オッズ implied probability からCUT確率を推定。"""
    if implied_prob <= 0:
        return 0.45
    return max(0.05, 0.50 - implied_prob * 5.0)


def _p_cut_from_season(season_played: int, season_cut: int) -> float | None:
    """シーズンCUT実績からCUT確率を推定。"""
    if season_played is None or season_played < 3:
        return None
    if season_cut is None:
        return None
    return season_cut / season_played


def estimate_p_cut(
    wgr: int,
    implied_prob: float,
    season_data: dict | None = None,
    wgr_table: list[tuple[int, float]] | None = None,
    player_stats: dict[str, float] | None = None,
    field_size: int | None = None,
    field_scoring_avg: float | None = None,
) -> tuple[float, str]:
    """CUT確率の推定。MLモデル利用可能ならML推定、なければヒューリスティック。

    Args:
        wgr: World Golf Ranking
        implied_prob: オッズからの暗示的勝率
        season_data: {season_played, season_cut} or None
        wgr_table: WGR→P(cut) テーブル
        player_stats: SG stats dict (ML推定用)
        field_size: フィールドサイズ (ML推定用)
        field_scoring_avg: フィールド平均scoring_average (ML推定用)

    Returns:
        (推定CUT確率, 推定方法 "ml"/"heuristic")
    """
    # ML推定を試行
    if _ml_models_loaded and _cut_classifier is not None and player_stats is not None:
        ml_p_cut = _predict_p_cut_ml(wgr, player_stats, field_size, field_scoring_avg)
        if ml_p_cut is not None:
            return ml_p_cut, "ml"

    # フォールバック: 既存の3ソース加重平均
    weights: list[float] = []
    values: list[float] = []

    # Source A: シーズン実績（最も信頼性が高い）
    p_season = None
    if season_data:
        p_season = _p_cut_from_season(
            season_data.get("season_played"),
            season_data.get("season_cut"),
        )
    if p_season is not None:
        weights.append(0.50)
        values.append(p_season)

    # Source B: WGR prior
    p_wgr = _p_cut_from_wgr(wgr, wgr_table)
    w_wgr = 0.30 if p_season is not None else 0.50
    weights.append(w_wgr)
    values.append(p_wgr)

    # Source C: オッズ
    if implied_prob > 0:
        p_odds = _p_cut_from_odds(implied_prob)
        w_odds = 0.20 if p_season is not None else 0.50
        weights.append(w_odds)
        values.append(p_odds)

    total_w = sum(weights)
    return sum(w * v for w, v in zip(weights, values)) / total_w, "heuristic"


#-----E[position] Estimation-----

def _build_field_ranking(groups: dict) -> list[tuple[str, float, int]]:
    """全選手をimplied_prob降順でランク付け。

    Returns:
        [(player_name, implied_prob, field_rank), ...]
    """
    all_players: list[tuple[str, float]] = []
    for players in groups.values():
        for p in players:
            prob = getattr(p, "implied_prob", 0.0) or 0.0
            all_players.append((p.name, prob))

    all_players.sort(key=lambda x: x[1], reverse=True)
    return [(name, prob, rank + 1) for rank, (name, prob) in enumerate(all_players)]


def estimate_e_position(
    field_rank: int,
    ml_score: float | None,
    group_ml_mean: float,
    group_ml_std: float,
    e_cut_count: int,
    player_stats: dict[str, float] | None = None,
    field_size: int | None = None,
    field_scoring_avg: float | None = None,
    wgr: int | None = None,
) -> float:
    """E[position | make_cut] の推定。MLモデル利用可能ならML推定。

    Args:
        field_rank: フィールド内オッズランク (1=本命)
        ml_score: MLスコア (0-100)
        group_ml_mean: グループ内MLスコア平均
        group_ml_std: グループ内MLスコア標準偏差
        e_cut_count: 推定カット通過者数
        player_stats: SG stats dict (ML推定用)
        field_size: フィールドサイズ (ML推定用)
        field_scoring_avg: フィールド平均scoring_average (ML推定用)
        wgr: World Golf Ranking (ML推定用)

    Returns:
        推定順位 (1〜e_cut_count)
    """
    # ML推定を試行
    if _ml_models_loaded and _position_regressor is not None and player_stats is not None:
        ml_e_pos = _predict_e_position_ml(
            wgr or 9999, player_stats, field_size, field_scoring_avg, e_cut_count,
        )
        if ml_e_pos is not None:
            return max(1.0, min(ml_e_pos, float(e_cut_count)))

    # フォールバック: 既存ヒューリスティック
    e_pos = 1.0 + (field_rank - 1) * 0.7

    if ml_score is not None and group_ml_std > 0:
        ml_deviation = (ml_score - group_ml_mean) / group_ml_std
        e_pos -= ml_deviation * 3.0

    return max(1.0, min(e_pos, float(e_cut_count)))


#-----E[bonuses] Estimation-----

def _estimate_bonuses(
    group_id: int,
    implied_prob: float,
    odds_rank_in_group: int,
) -> float:
    """期待ボーナス推定。

    Args:
        group_id: グループID
        implied_prob: 勝利確率
        odds_rank_in_group: グループ内オッズランク (1=本命)

    Returns:
        期待ボーナス値（差し引かれるので正の値）
    """
    # Winning Pick Bonus: (50 + group_id * 2) × P(win)
    e_winning = (50 + group_id * 2) * implied_prob

    # Best in Group Bonus: 10 × P(best_in_group)
    p_best_map = {1: 0.35, 2: 0.25, 3: 0.15}
    p_best = p_best_map.get(odds_rank_in_group, 0.05)
    e_best = 10 * p_best

    return e_winning + e_best


#-----Core EGS Computation-----

def compute_player_egs(
    player,
    group_id: int,
    field_params: dict,
    field_rank: int,
    group_ml_mean: float,
    group_ml_std: float,
    odds_rank_in_group: int,
    season_data: dict | None = None,
    wgr_table: list[tuple[int, float]] | None = None,
    field_scoring_avg: float | None = None,
) -> PlayerEGS:
    """1選手のEGSを算出する。

    Args:
        player: GroupPlayer オブジェクト
        group_id: グループID
        field_params: estimate_field_params() の戻り値
        field_rank: フィールド内オッズランク
        group_ml_mean: グループ内MLスコア平均
        group_ml_std: グループ内MLスコア標準偏差
        odds_rank_in_group: グループ内オッズランク
        season_data: シーズン実績データ
        wgr_table: WGR→P(cut) テーブル
        field_scoring_avg: フィールド平均scoring_average (ML推定用)
    """
    wgr = int(player.wgr) if player.wgr else 9999
    max_hc = field_params["max_handicap"]
    e_cut_count = field_params["e_cut_count"]
    field_size = field_params["field_size"]
    implied_prob = getattr(player, "implied_prob", 0.0) or 0.0
    ml_score = getattr(player, "ml_score", None)

    # PlayerStats からスタッツ dict を抽出 (ML推定用)
    player_stats_dict = None
    stat_obj = getattr(player, "stats", None)
    if stat_obj is not None:
        player_stats_dict = {
            "sg_approach": getattr(stat_obj, "sg_approach", None),
            "sg_off_tee": getattr(stat_obj, "sg_off_tee", None),
            "sg_tee_to_green": getattr(stat_obj, "sg_tee_to_green", None),
            "gir_pct": getattr(stat_obj, "greens_in_regulation_pct", None),
            "scrambling_pct": getattr(stat_obj, "scrambling_pct", None),
            "scoring_average": getattr(stat_obj, "scoring_average", None),
        }

    handicap = _calc_handicap(wgr, max_hc)
    p_cut, method = estimate_p_cut(
        wgr, implied_prob, season_data, wgr_table,
        player_stats=player_stats_dict,
        field_size=field_size,
        field_scoring_avg=field_scoring_avg,
    )

    e_pos = estimate_e_position(
        field_rank, ml_score, group_ml_mean, group_ml_std, e_cut_count,
        player_stats=player_stats_dict,
        field_size=field_size,
        field_scoring_avg=field_scoring_avg,
        wgr=wgr,
    )

    # CUT時のゲームスコア
    group_size = field_params["group_sizes"].get(group_id, 10)
    e_group_cut_count = round(group_size * (1 - field_params["cut_rate"]))

    if group_id <= 3:
        e_cut_score = (e_cut_count + 1) - handicap + e_group_cut_count
    else:
        e_cut_score = (e_cut_count + 1) - handicap

    # カット通過時のゲームスコア
    e_made_cut_score = e_pos - handicap

    # ボーナス
    e_bonuses = _estimate_bonuses(group_id, implied_prob, odds_rank_in_group)

    # EGS算出
    egs = (
        p_cut * e_cut_score
        + (1 - p_cut) * e_made_cut_score
        - e_bonuses
    )

    return PlayerEGS(
        player_name=player.name,
        group_id=group_id,
        wgr=wgr,
        handicap=handicap,
        p_cut=p_cut,
        e_position=e_pos,
        e_cut_score=e_cut_score,
        e_made_cut_score=e_made_cut_score,
        e_bonuses=e_bonuses,
        egs=egs,
        estimation_method=method,
    )


#-----Optimization-----

def optimize_picks(
    groups: dict,
    field_size: int | None = None,
    season_data: dict[str, dict] | None = None,
    config: dict | None = None,
) -> EGSResult:
    """全グループのEGS最適ピックを選出する。

    Args:
        groups: {group_id: [GroupPlayer, ...]}
        field_size: フィールドサイズ（既知の場合）
        season_data: {player_name: {season_played, season_cut}} or None
        config: game_optimization config section

    Returns:
        EGSResult
    """
    # WGRテーブル読み込み
    wgr_table = None
    if config and "wgr_cut_table" in config:
        raw = config["wgr_cut_table"]
        wgr_table = sorted(
            [(int(k), float(v)) for k, v in raw.items()],
            key=lambda x: x[0],
        )

    # MLモデルロード (初回のみ)
    global _ml_models_loaded
    use_ml = True
    if config:
        use_ml = config.get("use_ml_models", True)
    if use_ml and not _ml_models_loaded:
        _load_egs_models()

    field_params = _estimate_field_params(groups, field_size, config)
    field_ranking = _build_field_ranking(groups)
    field_rank_map = {name: rank for name, _, rank in field_ranking}

    # フィールド平均 scoring_average (ML推定用)
    field_scoring_avg = None
    sa_values = []
    for players in groups.values():
        for p in players:
            stat_obj = getattr(p, "stats", None)
            if stat_obj is not None:
                sa = getattr(stat_obj, "scoring_average", None)
                if sa is not None:
                    sa_values.append(sa)
    if sa_values:
        field_scoring_avg = sum(sa_values) / len(sa_values)

    all_egs: dict[str, PlayerEGS] = {}
    picks: dict[int, list[str]] = {}
    ml_picks: dict[int, list[str]] = {}

    for gid in sorted(groups.keys()):
        players = groups[gid]
        if not players:
            continue

        # グループ内MLスコアの統計量
        ml_scores = [
            getattr(p, "ml_score", None) or 0.0
            for p in players
        ]
        ml_mean = sum(ml_scores) / len(ml_scores) if ml_scores else 50.0
        ml_std = (
            (sum((s - ml_mean) ** 2 for s in ml_scores) / len(ml_scores)) ** 0.5
            if len(ml_scores) > 1 else 1.0
        )

        # グループ内オッズランク
        sorted_by_odds = sorted(
            players,
            key=lambda p: getattr(p, "implied_prob", 0.0) or 0.0,
            reverse=True,
        )
        odds_rank_map = {p.name: rank + 1 for rank, p in enumerate(sorted_by_odds)}

        # 各選手のEGS算出
        group_egs: list[PlayerEGS] = []
        for p in players:
            sd = season_data.get(p.name) if season_data else None
            pegs = compute_player_egs(
                p, gid, field_params,
                field_rank=field_rank_map.get(p.name, len(field_ranking)),
                group_ml_mean=ml_mean,
                group_ml_std=ml_std,
                odds_rank_in_group=odds_rank_map.get(p.name, len(players)),
                season_data=sd,
                wgr_table=wgr_table,
                field_scoring_avg=field_scoring_avg,
            )
            group_egs.append(pegs)
            all_egs[p.name] = pegs

        # EGSランク付け（昇順: 低いほど良い）
        group_egs.sort(key=lambda x: x.egs)
        for rank, pegs in enumerate(group_egs, 1):
            pegs.egs_rank_in_group = rank
            all_egs[pegs.player_name] = pegs

        # ピック数: G1=2, 他=1
        n_picks = 2 if gid == 1 else 1

        if n_picks == 2 and len(group_egs) >= 2:
            # G1: 全ペア列挙、EGS合計最小ペア選択
            best_pair = None
            best_pair_egs = float("inf")
            for a, b in combinations(group_egs, 2):
                pair_egs = a.egs + b.egs
                if pair_egs < best_pair_egs:
                    best_pair = (a, b)
                    best_pair_egs = pair_egs
            picks[gid] = [best_pair[0].player_name, best_pair[1].player_name]
        else:
            picks[gid] = [group_egs[0].player_name] if group_egs else []

        # MLピック（比較用）
        sorted_by_ml = sorted(
            players,
            key=lambda p: getattr(p, "ml_score", None) or 0.0,
            reverse=True,
        )
        ml_picks[gid] = [p.name for p in sorted_by_ml[:n_picks]]

    # 合計EGS
    total_egs = sum(all_egs[name].egs for names in picks.values() for name in names)
    ml_total_egs = sum(
        all_egs[name].egs for names in ml_picks.values() for name in names
        if name in all_egs
    )

    # ML一致数
    agree_count = 0
    for gid in picks:
        if set(picks[gid]) == set(ml_picks.get(gid, [])):
            agree_count += 1

    return EGSResult(
        picks=picks,
        ml_picks=ml_picks,
        player_egs=all_egs,
        total_egs=total_egs,
        ml_total_egs=ml_total_egs,
        field_params=field_params,
        agree_count=agree_count,
        total_groups=len(picks),
    )


#-----Text Report-----

def _count_estimation_methods(egs_result: EGSResult) -> str:
    """推定方法の集計文字列を返す。"""
    ml_count = sum(1 for p in egs_result.player_egs.values() if p.estimation_method == "ml")
    h_count = sum(1 for p in egs_result.player_egs.values() if p.estimation_method == "heuristic")
    if ml_count > 0 and h_count > 0:
        return f"ML={ml_count}, Heuristic={h_count}"
    elif ml_count > 0:
        return f"All ML ({ml_count} players)"
    else:
        return f"All Heuristic ({h_count} players)"


def format_egs_report(egs_result: EGSResult) -> str:
    """EGS分析のテキストレポート生成。"""
    lines = [
        "=" * 60,
        "Game Strategy - Expected Game Score (EGS) Analysis",
        "=" * 60,
        "",
        f"Field Size: {egs_result.field_params['field_size']}",
        f"E[Cut Count]: {egs_result.field_params['e_cut_count']}",
        f"Max Handicap: {egs_result.field_params['max_handicap']}",
        f"ML vs EGS Agreement: {egs_result.agree_count}/{egs_result.total_groups} groups",
        f"Estimation: {_count_estimation_methods(egs_result)}",
        f"EGS Total (EGS picks): {egs_result.total_egs:.1f}",
        f"EGS Total (ML picks):  {egs_result.ml_total_egs:.1f}",
        "",
    ]

    for gid in sorted(egs_result.picks.keys()):
        egs_names = egs_result.picks[gid]
        ml_names = egs_result.ml_picks.get(gid, [])
        agree = set(egs_names) == set(ml_names)
        n_picks = 2 if gid == 1 else 1

        lines.append(f"--- Group {gid} ({n_picks} pick{'s' if n_picks > 1 else ''}) "
                      f"{'[Agree]' if agree else '[Differ]'} ---")

        if not agree:
            lines.append(f"  ML Pick:  {', '.join(ml_names)}")
            lines.append(f"  EGS Pick: {', '.join(egs_names)}")
        else:
            lines.append(f"  Pick: {', '.join(egs_names)}")

        # グループ全選手のEGS
        group_players = [
            pegs for pegs in egs_result.player_egs.values()
            if pegs.group_id == gid
        ]
        group_players.sort(key=lambda x: x.egs)

        lines.append(f"  {'#':>3} {'Player':<22} {'WGR':>4} {'HC':>3} "
                      f"{'P(cut)':>6} {'E[pos]':>6} {'EGS':>7} {'Est':>3}")
        for i, pegs in enumerate(group_players, 1):
            marker = ""
            if pegs.player_name in egs_names:
                marker = " <- EGS"
            if pegs.player_name in ml_names and pegs.player_name not in egs_names:
                marker = " <- ML"
            est_tag = "[ML]" if pegs.estimation_method == "ml" else "[H]"
            lines.append(
                f"  {i:>3} {pegs.player_name:<22} {pegs.wgr:>4} {pegs.handicap:>3} "
                f"{pegs.p_cut:>5.0%} {pegs.e_position:>6.1f} {pegs.egs:>7.1f} {est_tag}{marker}"
            )
        lines.append("")

    return "\n".join(lines)


def run(
    groups: dict,
    field_size: int | None = None,
    config_path: str = "config.yaml",
) -> EGSResult:
    """パイプラインエントリーポイント。

    Args:
        groups: {group_id: [GroupPlayer, ...]}
        field_size: フィールドサイズ
        config_path: 設定ファイルパス
    """
    config = None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            full_config = yaml.safe_load(f)
        config = full_config.get("ml_prediction", {}).get("game_optimization", {})
    except Exception:
        pass

    if config and not config.get("enabled", True):
        print("[INFO] Game optimization disabled in config")
        return None

    print("[INFO] Running Game Score Optimization (EGS)...")
    result = optimize_picks(groups, field_size=field_size, config=config)

    report = format_egs_report(result)
    print(report)

    return result
