"""3モデル バックテストレポート生成 — ゲームスコアベース。

ゴルフゲームの実際のルールに基づいてゲームスコアを計算し、
ML予測・EGS v1・EGS v2 の3モデルがどのピックを選び、
それぞれ何点になったかを比較するHTMLダッシュボードを生成する。

ルール参照: http://jflynn87.pythonanywhere.com/golf_app/about/

ゲームスコア = Rank - Handicap  (低い方が良い)
CUTペナルティ:
  G1-3: (カット通過者数+1) - HC + グループ内カット通過者数
  G4+:  (カット通過者数+1) - HC

毎週 results.yml ワークフローで自動更新される。

Usage:
    uv run python -m src.model_backtest_report
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


# ----- Constants -----

GOLFGAME_DB = Path("data/golfgame.db")
PGA_STATS_DB = Path("data/pga_stats.db")
MODEL_DIR = Path("data/models")
V2_CUT_PATH = MODEL_DIR / "egs_v2_cut_classifier.joblib"
V2_POS_PATH = MODEL_DIR / "egs_v2_position_regressor.joblib"
OUTPUT_DIR = Path("data/output")


# ----- Game Score Calculation -----

def calc_game_score(
    espn_position: int | None,
    handicap: int,
    group_id: int,
    made_cut_count: int,
    group_made_cut_count: int,
) -> float:
    """実際のゲームルールに基づくピックスコアを算出。

    CUT選手は全員「カット通過者数+1」の順位に統一。
    つまり予選落ち選手の中で最も良い順位を全員に適用する。

    Args:
        espn_position: 最終順位 (None = CUT)
        handicap: ハンデキャップ (WGR/100)
        group_id: グループID (1-9)
        made_cut_count: 大会全体のカット通過者数
        group_made_cut_count: 同グループ内のカット通過者数（未使用）

    Returns:
        ゲームスコア (低い方が良い)
    """
    if espn_position is not None:
        # Made cut: Rank - Handicap
        return espn_position - handicap
    else:
        # Missed cut: 全CUT選手を同一順位 (カット通過者数+1) に統一
        return (made_cut_count + 1) - handicap


def calc_handicap(wgr_str: str | None, field_size: int) -> int:
    """WGRからハンデキャップを算出。"""
    if not wgr_str:
        return 0
    try:
        wgr = int(wgr_str)
    except (ValueError, TypeError):
        return 0
    hc = round(wgr / 100)
    max_hc = round(0.13 * field_size)
    return min(hc, max_hc)


# ----- EGS v2 Prediction -----

def _load_v2_model():
    """v2モデルをロード。"""
    if not V2_CUT_PATH.exists() or not V2_POS_PATH.exists():
        return None, None, []
    try:
        import joblib
        cut_data = joblib.load(V2_CUT_PATH)
        pos_data = joblib.load(V2_POS_PATH)
        return cut_data["model"], pos_data["model"], cut_data["features_used"]
    except Exception as e:
        print(f"[WARN] v2 model load failed: {e}")
        return None, None, []


def _build_v2_player_features(
    player_name: str,
    year: int,
    tournament_id: str,
    field_size: int,
    field_scoring_avg: float | None,
    player_stats: dict,
    career_data: dict,
    recent_data: dict,
    course_data: dict,
    features_list: list[str],
) -> np.ndarray | None:
    """1選手のv2特徴量ベクトルを構築。"""
    sa = player_stats.get("scoring_average")

    values = []
    for feat in features_list:
        if feat in player_stats and player_stats[feat] is not None:
            values.append(player_stats[feat])
        elif feat == "scoring_average_rank":
            values.append(career_data.get("scoring_average_rank", np.nan))
        elif feat == "field_size":
            values.append(float(field_size))
        elif feat == "field_strength":
            values.append(float(field_scoring_avg) if field_scoring_avg else np.nan)
        elif feat == "player_relative_strength":
            if sa is not None and field_scoring_avg is not None:
                values.append(field_scoring_avg - sa)
            else:
                values.append(np.nan)
        elif feat == "career_cut_rate":
            values.append(career_data.get("career_cut_rate", np.nan))
        elif feat == "career_avg_position_pct":
            values.append(career_data.get("career_avg_position_pct", np.nan))
        elif feat == "career_tournaments_played":
            values.append(career_data.get("career_tournaments_played", 0))
        elif feat == "year_over_year_trend":
            values.append(career_data.get("year_over_year_trend", np.nan))
        elif feat == "course_history_avg_pos":
            values.append(course_data.get("avg_pos", np.nan))
        elif feat == "course_history_cut_rate":
            values.append(course_data.get("cut_rate", np.nan))
        elif feat == "recent_3t_avg_pos_pct":
            values.append(recent_data.get("avg_pos_pct", np.nan))
        elif feat == "recent_3t_cut_rate":
            values.append(recent_data.get("cut_rate", np.nan))
        elif feat == "recent_3t_best_pos_pct":
            values.append(recent_data.get("best_pos_pct", np.nan))
        elif feat == "momentum":
            values.append(recent_data.get("momentum", np.nan))
        elif feat == "recent_vs_season":
            values.append(recent_data.get("recent_vs_season", np.nan))
        else:
            values.append(np.nan)

    return np.array(values, dtype=np.float64).reshape(1, -1)


def compute_v2_egs_for_tournament(groups: dict, field_size: int, made_cut_count: int) -> dict:
    """大会の全選手にv2 EGSを計算。

    Returns:
        {player_name: {"egs_v2": float, "egs_v2_rank": int}} or empty dict
    """
    v2_cut, v2_pos, v2_features = _load_v2_model()
    if v2_cut is None:
        return {}

    if not PGA_STATS_DB.exists():
        return {}

    conn = sqlite3.connect(str(PGA_STATS_DB))
    conn.row_factory = sqlite3.Row

    # Collect all player names
    all_players = []
    for gid, players in groups.items():
        for p in players:
            all_players.append(p)

    # Get season stats for year 2026 (or latest available)
    year = 2026
    stats_rows = conn.execute(
        "SELECT player_name, stat_name, stat_value FROM pga_season_stats WHERE year = ?",
        (year,),
    ).fetchall()
    player_stats_map: dict[str, dict] = {}
    for r in stats_rows:
        name = r["player_name"]
        if name not in player_stats_map:
            player_stats_map[name] = {}
        stat_name_map = {
            "sg_approach": "sg_approach", "sg_off_tee": "sg_off_tee",
            "sg_tee_to_green": "sg_tee_to_green", "gir_pct": "gir_pct",
            "scrambling_pct": "scrambling_pct", "scoring_average": "scoring_average",
        }
        mapped = stat_name_map.get(r["stat_name"])
        if mapped:
            player_stats_map[name] = player_stats_map.get(name, {})
            player_stats_map[name][mapped] = r["stat_value"]

    # Scoring average rank
    sa_list = [(name, s.get("scoring_average", 999)) for name, s in player_stats_map.items()
               if s.get("scoring_average") is not None]
    sa_list.sort(key=lambda x: x[1])
    sa_rank_map = {name: rank + 1 for rank, (name, _) in enumerate(sa_list)}

    # Field scoring average
    sa_values = []
    for p in all_players:
        stats = player_stats_map.get(p["player_name"], {})
        if stats.get("scoring_average") is not None:
            sa_values.append(stats["scoring_average"])
    field_scoring_avg = np.mean(sa_values) if sa_values else None

    # Career data (past 3 years from pga_tournament_results)
    career_rows = conn.execute(
        "SELECT player_name, year, position, "
        "CASE WHEN position IS NOT NULL THEN 1 ELSE 0 END as made_cut, "
        "fi.field_size, fi.made_cut_count "
        "FROM pga_tournament_results tr "
        "LEFT JOIN ("
        "  SELECT tournament_id, year as fy, COUNT(*) as field_size, "
        "  COUNT(CASE WHEN position IS NOT NULL THEN 1 END) as made_cut_count "
        "  FROM pga_tournament_results GROUP BY tournament_id, year"
        ") fi ON tr.tournament_id = fi.tournament_id AND tr.year = fi.fy "
        "WHERE tr.year >= ? AND tr.year <= ? "
        "ORDER BY tr.player_name, tr.year, tr.tournament_id",
        (year - 3, year),
    ).fetchall()

    # Build career + recent data per player
    from collections import defaultdict
    player_history = defaultdict(list)
    for r in career_rows:
        mc = r["made_cut_count"] or 1
        pos_pct = r["position"] / mc if r["position"] is not None and mc > 0 else None
        player_history[r["player_name"]].append({
            "year": r["year"],
            "made_cut": r["made_cut"],
            "position_pct": pos_pct,
        })

    # Previous year scoring average
    prev_sa_rows = conn.execute(
        "SELECT player_name, stat_value FROM pga_season_stats "
        "WHERE stat_name = 'scoring_average' AND year = ?", (year - 1,)
    ).fetchall()
    prev_sa_map = {r["player_name"]: r["stat_value"] for r in prev_sa_rows}

    conn.close()

    # Compute v2 EGS for each player
    e_cut_count = made_cut_count  # actual cut count from results

    v2_results: dict[str, dict] = {}
    group_v2_egs: dict[int, list] = {}

    for gid, players in groups.items():
        group_made_cut = sum(1 for p in players if p["made_cut"])
        group_v2_egs[gid] = []

        for p in players:
            name = p["player_name"]
            stats = player_stats_map.get(name, {})
            history = player_history.get(name, [])

            # Career data (past years only, not current)
            past = [h for h in history if h["year"] < year]
            if past:
                career_cut_rate = sum(h["made_cut"] for h in past) / len(past)
                pos_vals = [h["position_pct"] for h in past if h["position_pct"] is not None]
                career_avg_pos = np.mean(pos_vals) if pos_vals else np.nan
            else:
                career_cut_rate = np.nan
                career_avg_pos = np.nan

            career = {
                "career_cut_rate": career_cut_rate,
                "career_avg_position_pct": career_avg_pos,
                "career_tournaments_played": len(past),
                "scoring_average_rank": sa_rank_map.get(name, np.nan),
                "year_over_year_trend": (
                    stats.get("scoring_average", 0) - prev_sa_map.get(name, 0)
                    if stats.get("scoring_average") and name in prev_sa_map else np.nan
                ),
            }

            # Recent 3 tournaments
            current_year_hist = [h for h in history if h["year"] == year]
            last_3 = current_year_hist[-3:] if len(current_year_hist) >= 3 else current_year_hist
            if last_3:
                r_cuts = [h["made_cut"] for h in last_3]
                r_pos = [h["position_pct"] for h in last_3 if h["position_pct"] is not None]
                recent = {
                    "cut_rate": sum(r_cuts) / len(r_cuts),
                    "avg_pos_pct": np.mean(r_pos) if r_pos else np.nan,
                    "best_pos_pct": min(r_pos) if r_pos else np.nan,
                    "momentum": np.polyfit(range(len(r_pos)), r_pos, 1)[0] if len(r_pos) >= 2 else 0.0,
                    "recent_vs_season": (np.mean(r_pos) - 0.5) if r_pos else np.nan,
                }
            else:
                recent = {"cut_rate": np.nan, "avg_pos_pct": np.nan,
                          "best_pos_pct": np.nan, "momentum": np.nan, "recent_vs_season": np.nan}

            course = {"avg_pos": np.nan, "cut_rate": np.nan}  # simplified for backtest

            features = _build_v2_player_features(
                name, year, "", field_size, field_scoring_avg,
                stats, career, recent, course, v2_features,
            )

            if features is not None:
                try:
                    p_cut = float(v2_cut.predict_proba(features)[0][0])  # P(missed cut)
                    pos_pct = float(v2_pos.predict(features)[0])
                    pos_pct = max(0.01, min(1.0, pos_pct))
                    e_pos = pos_pct * e_cut_count

                    # EGS calculation (same formula as game_optimizer)
                    group_size = len(players)
                    cut_rate = 1 - (made_cut_count / field_size) if field_size > 0 else 0.43
                    e_group_cut = round(group_size * cut_rate)

                    if gid <= 3:
                        e_cut_score = (e_cut_count + 1) - p["handicap"] + e_group_cut
                    else:
                        e_cut_score = (e_cut_count + 1) - p["handicap"]

                    e_made_cut_score = e_pos - p["handicap"]
                    egs_v2 = p_cut * e_cut_score + (1 - p_cut) * e_made_cut_score

                    v2_results[name] = {"egs_v2": egs_v2}
                    group_v2_egs[gid].append((name, egs_v2))
                except Exception:
                    pass

    # Assign v2 ranks within groups
    for gid, items in group_v2_egs.items():
        items.sort(key=lambda x: x[1])
        for rank, (name, _) in enumerate(items, 1):
            if name in v2_results:
                v2_results[name]["egs_v2_rank"] = rank

    return v2_results


# ----- Data Loading & Simulation -----

def load_and_simulate() -> list[dict]:
    """全完了大会をロードし、4モデルのゲームスコアをシミュレーション。"""
    if not GOLFGAME_DB.exists():
        return []

    conn = sqlite3.connect(str(GOLFGAME_DB))
    conn.row_factory = sqlite3.Row

    tournaments = conn.execute(
        "SELECT id, name, end_date FROM tournaments "
        "WHERE status = 'results_saved' ORDER BY id"
    ).fetchall()

    all_results = []

    for t in tournaments:
        tid = t["id"]
        tname = t["name"]
        tend = t["end_date"] or ""

        rows = conn.execute(
            "SELECT mp.group_id, mp.player_name, mp.ml_score, mp.ml_rank_in_group, "
            "mp.egs, mp.egs_rank_in_group, mp.handicap, "
            "r.espn_position, r.group_rank, "
            "gp.wgr "
            "FROM ml_predictions mp "
            "LEFT JOIN results r ON mp.tournament_id = r.tournament_id "
            "  AND mp.player_name = r.player_name "
            "LEFT JOIN group_players gp ON mp.tournament_id = gp.tournament_id "
            "  AND mp.player_name = gp.player_name "
            "WHERE mp.tournament_id = ?", (tid,)
        ).fetchall()

        if not rows:
            continue

        # Build groups
        field_size = len(rows)
        made_cut_count = sum(1 for r in rows if r["espn_position"] is not None)

        groups: dict[int, list[dict]] = {}
        for r in rows:
            gid = r["group_id"]
            if gid not in groups:
                groups[gid] = []

            hc = r["handicap"]
            if hc is None:
                hc = calc_handicap(r["wgr"], field_size)

            groups[gid].append({
                "player_name": r["player_name"],
                "espn_position": r["espn_position"],
                "group_rank": r["group_rank"],
                "made_cut": r["espn_position"] is not None,
                "handicap": hc,
                "ml_score": r["ml_score"] or 0,
                "ml_rank": r["ml_rank_in_group"] or 99,
                "egs_v1": r["egs"],
                "egs_v1_rank": r["egs_rank_in_group"],
                "wgr": r["wgr"],
            })

        # Calculate game scores for each player
        for gid, players in groups.items():
            group_made_cut = sum(1 for p in players if p["made_cut"])
            for p in players:
                p["game_score"] = calc_game_score(
                    p["espn_position"], p["handicap"],
                    gid, made_cut_count, group_made_cut,
                )

        # Compute v2 EGS predictions
        v2_predictions = compute_v2_egs_for_tournament(groups, field_size, made_cut_count)
        for gid, players in groups.items():
            for p in players:
                v2_data = v2_predictions.get(p["player_name"], {})
                p["egs_v2"] = v2_data.get("egs_v2")
                p["egs_v2_rank"] = v2_data.get("egs_v2_rank")

        # Simulate picks for each model
        # G1: pick 2, G2-9: pick 1
        model_picks = {"ml": {}, "egs_v1": {}, "egs_v2": {}, "odds": {}}
        model_total_scores = {"ml": 0, "egs_v1": 0, "egs_v2": 0, "odds": 0}

        # Also track the "best possible" score (oracle)
        oracle_total = 0

        group_details = []

        for gid in sorted(groups.keys()):
            players = groups[gid]
            n_picks = 2 if gid == 1 else 1

            # Oracle: best possible game_score in group
            sorted_by_game = sorted(players, key=lambda p: p["game_score"])
            oracle_picks = [p["player_name"] for p in sorted_by_game[:n_picks]]
            oracle_score = sum(p["game_score"] for p in sorted_by_game[:n_picks])
            oracle_total += oracle_score

            # ML picks: sorted by ml_score descending
            ml_sorted = sorted(players, key=lambda p: p["ml_score"], reverse=True)
            ml_picks_names = [p["player_name"] for p in ml_sorted[:n_picks]]
            ml_score = sum(p["game_score"] for p in ml_sorted[:n_picks])
            model_picks["ml"][gid] = ml_picks_names
            model_total_scores["ml"] += ml_score

            # EGS v1 picks: sorted by egs ascending (lower = better)
            egs_sorted = sorted(players, key=lambda p: p["egs_v1"] if p["egs_v1"] is not None else 9999)
            has_egs = egs_sorted[0]["egs_v1"] is not None if egs_sorted else False
            egs_picks_names = [p["player_name"] for p in egs_sorted[:n_picks]] if has_egs else []
            egs_score = sum(p["game_score"] for p in egs_sorted[:n_picks]) if has_egs else None
            model_picks["egs_v1"][gid] = egs_picks_names
            if egs_score is not None:
                model_total_scores["egs_v1"] += egs_score

            # EGS v2 picks: sorted by egs_v2 ascending (lower = better)
            egs_v2_sorted = sorted(players, key=lambda p: p["egs_v2"] if p["egs_v2"] is not None else 9999)
            has_v2 = egs_v2_sorted[0]["egs_v2"] is not None if egs_v2_sorted else False
            egs_v2_picks_names = [p["player_name"] for p in egs_v2_sorted[:n_picks]] if has_v2 else []
            egs_v2_score = sum(p["game_score"] for p in egs_v2_sorted[:n_picks]) if has_v2 else None
            model_picks["egs_v2"][gid] = egs_v2_picks_names
            if egs_v2_score is not None:
                model_total_scores["egs_v2"] += egs_v2_score

            # Odds picks: use wgr as proxy for "favorite"
            odds_by_wgr = sorted(players, key=lambda p: int(p["wgr"] or "999"))
            odds_picks_names = [p["player_name"] for p in odds_by_wgr[:n_picks]]
            odds_score = sum(p["game_score"] for p in odds_by_wgr[:n_picks])
            model_picks["odds"][gid] = odds_picks_names
            model_total_scores["odds"] += odds_score

            group_details.append({
                "group_id": gid,
                "n_picks": n_picks,
                "oracle_picks": oracle_picks,
                "oracle_score": oracle_score,
                "players": [{
                    "name": p["player_name"],
                    "pos": p["espn_position"],
                    "hc": p["handicap"],
                    "game_score": p["game_score"],
                    "ml_rank": p["ml_rank"],
                    "egs_v1_rank": p["egs_v1_rank"],
                    "egs_v2_rank": p.get("egs_v2_rank"),
                    "made_cut": p["made_cut"],
                } for p in sorted(players, key=lambda x: x["game_score"])],
                "ml_picks": ml_picks_names,
                "ml_score": ml_score,
                "egs_v1_picks": egs_picks_names,
                "egs_v1_score": egs_score,
                "egs_v2_picks": egs_v2_picks_names,
                "egs_v2_score": egs_v2_score,
                "odds_picks": odds_picks_names,
                "odds_score": odds_score,
            })

        all_results.append({
            "tournament_id": tid,
            "name": tname,
            "end_date": tend,
            "field_size": field_size,
            "made_cut_count": made_cut_count,
            "model_totals": model_total_scores,
            "oracle_total": oracle_total,
            "groups": group_details,
        })

    conn.close()
    return all_results


# ----- HTML Report -----

def generate_backtest_report() -> Path:
    """ゲームスコアベースの3モデルバックテストHTMLを生成。"""
    print("[INFO] Loading and simulating tournament data...")
    tournaments = load_and_simulate()

    if not tournaments:
        print("[WARN] No tournament data found")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUTPUT_DIR / "backtest.html"
        p.write_text("<html><body>No data</body></html>", encoding="utf-8")
        return p

    print(f"[INFO] Simulated {len(tournaments)} tournaments")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Aggregate stats
    cumulative = {"ml": 0, "egs_v1": 0, "egs_v2": 0, "odds": 0, "oracle": 0}
    per_tournament = []
    for t in tournaments:
        cumulative["ml"] += t["model_totals"]["ml"]
        cumulative["egs_v1"] += t["model_totals"]["egs_v1"]
        cumulative["egs_v2"] += t["model_totals"]["egs_v2"]
        cumulative["odds"] += t["model_totals"]["odds"]
        cumulative["oracle"] += t["oracle_total"]
        per_tournament.append({
            "name": t["name"][:25],
            "ml": t["model_totals"]["ml"],
            "egs_v1": t["model_totals"]["egs_v1"],
            "egs_v2": t["model_totals"]["egs_v2"],
            "odds": t["model_totals"]["odds"],
            "oracle": t["oracle_total"],
        })

    # Find winner per tournament
    for pt in per_tournament:
        scores = {"ML": pt["ml"], "EGS v1": pt["egs_v1"], "EGS v2": pt["egs_v2"], "Odds": pt["odds"]}
        pt["winner"] = min(scores, key=scores.get)

    # Count wins
    win_counts = {"ML": 0, "EGS v1": 0, "EGS v2": 0, "Odds": 0}
    for pt in per_tournament:
        win_counts[pt["winner"]] += 1

    t_labels = json.dumps([p["name"] for p in per_tournament], ensure_ascii=False)
    ml_scores = json.dumps([p["ml"] for p in per_tournament])
    v1_scores = json.dumps([p["egs_v1"] for p in per_tournament])
    v2_scores = json.dumps([p["egs_v2"] for p in per_tournament])
    odds_scores = json.dumps([p["odds"] for p in per_tournament])
    oracle_scores = json.dumps([p["oracle"] for p in per_tournament])

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3モデル ゲームスコア バックテスト</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --ml-color: #3b82f6; --v1-color: #f97316; --v2-color: #22c55e; --odds-color: #a855f7;
    --oracle-color: #64748b;
    --win: #22c55e; --lose: #ef4444;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; margin-bottom: 8px; font-size: 1.5rem; }}
.subtitle {{ text-align: center; color: var(--muted); margin-bottom: 24px; font-size: 0.85rem; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
.card h2 {{ font-size: 1.05rem; margin-bottom: 12px; color: var(--accent); }}
.full-width {{ grid-column: 1 / -1; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
th, td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; }}
.mono {{ font-family: 'Consolas', monospace; font-weight: 600; }}
.ml-val {{ color: var(--ml-color); }}
.v1-val {{ color: var(--v1-color); }}
.v2-val {{ color: var(--v2-color); }}
.odds-val {{ color: var(--odds-color); }}
.badge-v2 {{ background: rgba(34,197,94,0.2); color: var(--v2-color); }}
.best {{ color: var(--win); font-weight: bold; }}
.pick-marker {{ font-weight: bold; }}
.summary-box {{ display: flex; gap: 14px; justify-content: center; margin-bottom: 24px; flex-wrap: wrap; }}
.summary-item {{ text-align: center; padding: 14px 18px; background: var(--surface); border-radius: 12px; border: 1px solid var(--border); min-width: 110px; }}
.summary-item .label {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 4px; }}
.summary-item .value {{ font-size: 1.3rem; font-weight: 700; }}
.model-badge {{ display: inline-block; padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 0.8rem; margin: 0 3px; }}
.badge-ml {{ background: rgba(59,130,246,0.2); color: var(--ml-color); }}
.badge-v1 {{ background: rgba(249,115,22,0.2); color: var(--v1-color); }}
.badge-odds {{ background: rgba(168,85,247,0.2); color: var(--odds-color); }}
.tournament-header {{ background: rgba(255,255,255,0.03); padding: 10px 14px; border-radius: 8px; margin: 14px 0 8px; display: flex; justify-content: space-between; align-items: center; }}
.tournament-header h3 {{ font-size: 0.95rem; }}
.tournament-header .scores {{ font-size: 0.8rem; color: var(--muted); }}
canvas {{ max-height: 320px; }}
.tab-buttons {{ display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }}
.tab-btn {{ padding: 6px 14px; border: 1px solid var(--border); background: transparent;
    color: var(--muted); border-radius: 8px; cursor: pointer; font-size: 0.8rem; }}
.tab-btn.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.score-rule {{ background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 0.82rem; color: var(--muted); line-height: 1.6; }}
.score-rule code {{ color: var(--text); background: rgba(255,255,255,0.06); padding: 1px 6px; border-radius: 4px; }}
@media (max-width: 768px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
<h1>3モデル ゲームスコア バックテスト</h1>
<p class="subtitle">
    <span class="model-badge badge-ml">ML予測</span>
    <span class="model-badge badge-v1">EGS v1</span>
    <span class="model-badge badge-v2">EGS v2</span>
    <span class="model-badge badge-odds">Oddsベース (WGR順)</span>
    &mdash; {now}
</p>

<div class="score-rule">
    <strong>ゲームスコア計算ルール:</strong>
    カット通過: <code>順位 - ハンデ</code> &nbsp;|&nbsp;
    CUT (全グループ共通): <code>(カット通過者数+1) - ハンデ</code> &nbsp;|&nbsp;
    G1は2名、G2-9は1名をピック &nbsp;|&nbsp;
    <strong>合計点が低いほど良い</strong>
</div>

<div class="summary-box">
    <div class="summary-item">
        <div class="label">対象大会数</div>
        <div class="value">{len(tournaments)}</div>
    </div>
    <div class="summary-item">
        <div class="label">ML予測 累計</div>
        <div class="value ml-val">{cumulative['ml']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">EGS v1 累計</div>
        <div class="value v1-val">{cumulative['egs_v1']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">EGS v2 累計</div>
        <div class="value v2-val">{cumulative['egs_v2']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">Odds 累計</div>
        <div class="value odds-val">{cumulative['odds']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">最適解 (Oracle)</div>
        <div class="value" style="color:var(--oracle-color)">{cumulative['oracle']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">ML 週間勝利</div>
        <div class="value ml-val">{win_counts['ML']}</div>
    </div>
    <div class="summary-item">
        <div class="label">EGS v1 週間勝利</div>
        <div class="value v1-val">{win_counts['EGS v1']}</div>
    </div>
    <div class="summary-item">
        <div class="label">EGS v2 週間勝利</div>
        <div class="value v2-val">{win_counts['EGS v2']}</div>
    </div>
</div>

<div class="grid">
<div class="card">
<h2>大会別ゲームスコア (低い方が良い)</h2>
<canvas id="scoreChart"></canvas>
</div>

<div class="card">
<h2>累計ゲームスコア推移</h2>
<canvas id="cumulativeChart"></canvas>
</div>

<!-- Per-tournament summary table -->
<div class="card full-width">
<h2>大会別サマリー</h2>
<table>
<thead>
<tr><th>大会</th><th>日付</th><th class="ml-val">ML予測</th><th class="v1-val">EGS v1</th><th class="v2-val">EGS v2</th><th class="odds-val">Odds</th><th>最適解</th><th>週間勝者</th></tr>
</thead>
<tbody>
"""

    for i, t in enumerate(tournaments):
        pt = per_tournament[i]
        scores = {"ML": pt["ml"], "EGS v1": pt["egs_v1"], "EGS v2": pt["egs_v2"], "Odds": pt["odds"]}
        best_val = min(scores.values())

        def mark(v):
            cls = "best" if v == best_val else ""
            return f'<td class="mono {cls}">{v:.0f}</td>'

        badges = {"ML": "badge-ml", "EGS v1": "badge-v1", "EGS v2": "badge-v2", "Odds": "badge-odds"}
        winner_badge = f'<span class="model-badge {badges[pt["winner"]]}">{pt["winner"]}</span>'

        html += f"""<tr>
<td>{t['name'][:35]}</td>
<td style="color:var(--muted)">{t['end_date']}</td>
{mark(pt['ml'])}
{mark(pt['egs_v1'])}
{mark(pt['egs_v2'])}
{mark(pt['odds'])}
<td class="mono" style="color:var(--oracle-color)">{pt['oracle']:.0f}</td>
<td>{winner_badge}</td>
</tr>
"""

    # Cumulative totals row
    best_cum = min(cumulative["ml"], cumulative["egs_v1"], cumulative["egs_v2"], cumulative["odds"])

    def cum_mark(v):
        return "best" if v == best_cum else ""

    html += f"""<tr style="border-top:2px solid var(--border);font-weight:700">
<td>累計</td><td></td>
<td class="mono ml-val {cum_mark(cumulative['ml'])}">{cumulative['ml']:.0f}</td>
<td class="mono v1-val {cum_mark(cumulative['egs_v1'])}">{cumulative['egs_v1']:.0f}</td>
<td class="mono v2-val {cum_mark(cumulative['egs_v2'])}">{cumulative['egs_v2']:.0f}</td>
<td class="mono odds-val {cum_mark(cumulative['odds'])}">{cumulative['odds']:.0f}</td>
<td class="mono" style="color:var(--oracle-color)">{cumulative['oracle']:.0f}</td>
<td></td>
</tr>
"""

    html += """</tbody></table></div>

<!-- Tournament Detail Tabs -->
<div class="card full-width">
<h2>大会別 グループ詳細</h2>
<div class="tab-buttons" id="tabButtons"></div>
<div id="tabContents"></div>
</div>
</div>
"""

    # Build tab data
    tabs_data = []
    for t in tournaments:
        tab = {
            "name": t["name"],
            "date": t["end_date"],
            "totals": t["model_totals"],
            "oracle": t["oracle_total"],
            "groups": [],
        }
        for g in t["groups"]:
            tab["groups"].append({
                "gid": g["group_id"],
                "n_picks": g["n_picks"],
                "ml_picks": g["ml_picks"],
                "ml_score": g["ml_score"],
                "egs_v1_picks": g["egs_v1_picks"],
                "egs_v1_score": g["egs_v1_score"],
                "egs_v2_picks": g.get("egs_v2_picks", []),
                "egs_v2_score": g.get("egs_v2_score"),
                "odds_picks": g["odds_picks"],
                "odds_score": g["odds_score"],
                "oracle_picks": g["oracle_picks"],
                "oracle_score": g["oracle_score"],
                "players": g["players"],
            })
        tabs_data.append(tab)

    # Cumulative chart data
    cum_ml = []
    cum_v1 = []
    cum_v2 = []
    cum_odds = []
    cum_oracle = []
    running = {"ml": 0, "v1": 0, "v2": 0, "odds": 0, "oracle": 0}
    for pt in per_tournament:
        running["ml"] += pt["ml"]
        running["v1"] += pt["egs_v1"]
        running["v2"] += pt["egs_v2"]
        running["odds"] += pt["odds"]
        running["oracle"] += pt["oracle"]
        cum_ml.append(running["ml"])
        cum_v1.append(running["v1"])
        cum_v2.append(running["v2"])
        cum_odds.append(running["odds"])
        cum_oracle.append(running["oracle"])

    html += f"""
<script>
const tabsData = {json.dumps(tabs_data, ensure_ascii=False, default=str)};

// Render tabs
const btnContainer = document.getElementById('tabButtons');
const contentContainer = document.getElementById('tabContents');
tabsData.forEach((t, i) => {{
    const btn = document.createElement('button');
    btn.className = 'tab-btn' + (i === tabsData.length - 1 ? ' active' : '');
    btn.textContent = t.name.substring(0, 25);
    btn.onclick = () => {{
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + i).classList.add('active');
    }};
    btnContainer.appendChild(btn);

    const div = document.createElement('div');
    div.id = 'tab-' + i;
    div.className = 'tab-content' + (i === tabsData.length - 1 ? ' active' : '');

    let h = '<div class="tournament-header"><h3>' + t.name + '</h3>';
    h += '<span class="scores">ML=' + t.totals.ml.toFixed(0) + ' | v1=' + t.totals.egs_v1.toFixed(0) + ' | v2=' + t.totals.egs_v2.toFixed(0) + ' | Odds=' + t.totals.odds.toFixed(0) + ' | Oracle=' + t.oracle.toFixed(0) + '</span></div>';

    t.groups.forEach(g => {{
        const best = Math.min(g.ml_score, g.egs_v1_score || 9999, g.odds_score);
        h += '<table style="margin-bottom:12px"><thead><tr>';
        h += '<th colspan="6" style="color:var(--text)">G' + g.gid + ' (' + g.n_picks + '名ピック)';
        h += ' &mdash; <span class="ml-val">ML=' + g.ml_score.toFixed(0) + '</span>';
        if (g.egs_v1_score !== null) h += ' <span class="v1-val">v1=' + g.egs_v1_score.toFixed(0) + '</span>';
        if (g.egs_v2_score !== null) h += ' <span class="v2-val">v2=' + g.egs_v2_score.toFixed(0) + '</span>';
        h += ' <span class="odds-val">Odds=' + g.odds_score.toFixed(0) + '</span>';
        h += ' <span style="color:var(--oracle-color)">Best=' + g.oracle_score.toFixed(0) + '</span>';
        h += '</th></tr>';
        h += '<tr><th>#</th><th>選手</th><th>順位</th><th>HC</th><th>スコア</th><th>ピック</th></tr></thead><tbody>';

        g.players.forEach((p, idx) => {{
            const pos = p.pos !== null ? p.pos : 'CUT';
            const picks = [];
            if (g.ml_picks.includes(p.name)) picks.push('<span class="ml-val">ML</span>');
            if (g.egs_v1_picks.includes(p.name)) picks.push('<span class="v1-val">v1</span>');
            if (g.egs_v2_picks.includes(p.name)) picks.push('<span class="v2-val">v2</span>');
            if (g.odds_picks.includes(p.name)) picks.push('<span class="odds-val">Odds</span>');
            if (g.oracle_picks.includes(p.name)) picks.push('<span style="color:var(--oracle-color)">Best</span>');
            const pickStr = picks.length > 0 ? picks.join(' ') : '';
            const isBest = g.oracle_picks.includes(p.name);
            const rowStyle = isBest ? 'background:rgba(34,197,94,0.05)' : '';
            h += '<tr style="' + rowStyle + '">';
            h += '<td>' + (idx+1) + '</td>';
            h += '<td>' + p.name + '</td>';
            h += '<td class="mono">' + pos + '</td>';
            h += '<td class="mono">' + p.hc + '</td>';
            h += '<td class="mono">' + p.game_score.toFixed(0) + '</td>';
            h += '<td class="pick-marker">' + pickStr + '</td>';
            h += '</tr>';
        }});
        h += '</tbody></table>';
    }});

    div.innerHTML = h;
    contentContainer.appendChild(div);
}});

// Per-tournament score chart
new Chart(document.getElementById('scoreChart').getContext('2d'), {{
    type: 'bar',
    data: {{
        labels: {t_labels},
        datasets: [
            {{ label: 'ML', data: {ml_scores}, backgroundColor: 'rgba(59,130,246,0.7)', borderRadius: 4 }},
            {{ label: 'EGS v1', data: {v1_scores}, backgroundColor: 'rgba(249,115,22,0.7)', borderRadius: 4 }},
            {{ label: 'EGS v2', data: {v2_scores}, backgroundColor: 'rgba(34,197,94,0.7)', borderRadius: 4 }},
            {{ label: 'Odds', data: {odds_scores}, backgroundColor: 'rgba(168,85,247,0.7)', borderRadius: 4 }},
            {{ label: 'Oracle', data: {oracle_scores}, backgroundColor: 'rgba(100,116,139,0.4)', borderRadius: 4 }},
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }}, grid: {{ color: '#334155' }} }},
            y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }},
                 title: {{ display: true, text: 'Game Score (low = good)', color: '#94a3b8' }} }}
        }}
    }}
}});

// Cumulative chart
new Chart(document.getElementById('cumulativeChart').getContext('2d'), {{
    type: 'line',
    data: {{
        labels: {t_labels},
        datasets: [
            {{ label: 'ML', data: {json.dumps(cum_ml)}, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', fill: false, tension: 0.3 }},
            {{ label: 'EGS v1', data: {json.dumps(cum_v1)}, borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.1)', fill: false, tension: 0.3 }},
            {{ label: 'EGS v2', data: {json.dumps(cum_v2)}, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', fill: false, tension: 0.3 }},
            {{ label: 'Odds', data: {json.dumps(cum_odds)}, borderColor: '#a855f7', backgroundColor: 'rgba(168,85,247,0.1)', fill: false, tension: 0.3 }},
            {{ label: 'Oracle', data: {json.dumps(cum_oracle)}, borderColor: '#64748b', backgroundColor: 'rgba(100,116,139,0.1)', fill: false, tension: 0.3, borderDash: [5, 5] }},
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
            y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }},
                 title: {{ display: true, text: 'Cumulative Score', color: '#94a3b8' }} }}
        }}
    }}
}});
</script>
<p style="text-align:center;color:var(--muted);margin-top:20px;font-size:0.78rem;">
    毎週月曜に自動更新 &mdash; Oracle = 各グループで最も低いスコアの選手をピックした場合の理論最適値
</p>
</div>
</body>
</html>
"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "backtest.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"[OK] Backtest report saved: {output_path}")
    return output_path


# ----- CLI -----

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    generate_backtest_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
