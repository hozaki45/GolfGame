"""大会終了後の予測振り返り分析モジュール。

事前のML予測と実際の大会結果を比較し、
グループ別・シグナル別・信頼度別の精度分析を行う。

Usage:
    from src.post_tournament_analyzer import analyze_tournament
    review_data = analyze_tournament(tournament_id=3)
"""

from __future__ import annotations


#-----メインAPI-----

def analyze_tournament(tournament_id: int) -> dict | None:
    """大会の事前予測 vs 実績を包括的に分析する。

    Args:
        tournament_id: 対象大会のDB ID

    Returns:
        ReviewData dict or None (データ不足時)
    """
    from src.database import get_review_data

    raw = get_review_data(tournament_id)
    if not raw:
        print(f"[WARN] No review data for tournament_id={tournament_id}")
        return None

    groups = _analyze_groups(raw)
    if not groups:
        print("[WARN] No analyzable groups found")
        return None

    signal_accuracy = _analyze_signal_accuracy(groups)
    confidence = _analyze_confidence(groups)
    upsets, upset_patterns = _analyze_upsets(groups)
    game_score = _analyze_game_score(groups, raw["tournament"])
    summary = _generate_summary(
        groups, signal_accuracy, confidence, upsets, game_score,
    )

    return {
        "tournament": raw["tournament"],
        "summary": summary,
        "groups": groups,
        "signal_accuracy": signal_accuracy,
        "confidence_calibration": confidence,
        "upsets": upsets,
        "upset_patterns": upset_patterns,
        "game_score": game_score,
    }


#-----グループ別分析-----

def _analyze_groups(raw: dict) -> list[dict]:
    """グループ別の予測 vs 実績比較を計算する。"""
    from fuzzywuzzy import fuzz

    analyzed: list[dict] = []

    # group_players から WGR をルックアップ
    wgr_map: dict[str, int] = {}
    for gid, gdata in raw["groups"].items():
        for gp in gdata.get("players", []):
            name_lower = gp["player_name"].lower().strip()
            try:
                wgr_map[name_lower] = int(gp.get("wgr") or 9999)
            except (ValueError, TypeError):
                wgr_map[name_lower] = 9999

    for gid, gdata in sorted(raw["groups"].items()):
        preds = gdata.get("predictions", [])
        results = gdata.get("results", [])

        if not preds or not results:
            continue

        # 予測と結果をマッチング（fuzzy matching）
        players: list[dict] = []
        for p in preds:
            p_name = p["player_name"]
            actual_rank = None
            espn_position = None
            score = None
            rounds_played = None
            espn_status = ""

            # 結果からマッチ
            p_lower = p_name.lower().strip()
            for r in results:
                r_lower = r["player_name"].lower().strip()
                match_score = max(
                    fuzz.ratio(p_lower, r_lower),
                    fuzz.token_sort_ratio(p_lower, r_lower),
                )
                if match_score >= 80:
                    actual_rank = r["group_rank"]
                    espn_position = r["espn_position"]
                    score = r["score"]
                    rounds_played = r.get("rounds_played")
                    espn_status = r.get("espn_status") or ""
                    break

            # WGR: 予測名 or fuzzyマッチしたgroup_players名
            wgr = wgr_map.get(p_lower, 9999)
            if wgr == 9999:
                for gp_name, gp_wgr in wgr_map.items():
                    ms = max(fuzz.ratio(p_lower, gp_name), fuzz.token_sort_ratio(p_lower, gp_name))
                    if ms >= 80:
                        wgr = gp_wgr
                        break

            ml_rank = p["ml_rank_in_group"]
            rank_delta = (ml_rank - actual_rank) if actual_rank else None

            players.append({
                "name": p_name,
                "ml_score": p["ml_score"] or 0,
                "ml_rank": ml_rank,
                "odds_component": p["odds_component"] or 0,
                "stats_component": p["stats_component"] or 0,
                "fit_component": p["fit_component"] or 0,
                "crowd_component": p["crowd_component"] or 0,
                "egs": p.get("egs"),
                "actual_rank": actual_rank,
                "espn_position": espn_position,
                "score": score,
                "rank_delta": rank_delta,
                "wgr": wgr,
                "rounds_played": rounds_played,
                "espn_status": espn_status,
            })

        # ML #1 pick
        ml_pick = min(players, key=lambda x: x["ml_rank"])
        predicted_winner = ml_pick["name"]
        actual_winner_candidates = [
            p for p in players if p["actual_rank"] == 1
        ]
        actual_winner = actual_winner_candidates[0]["name"] if actual_winner_candidates else "N/A"

        prediction_correct = ml_pick["actual_rank"] == 1
        prediction_top2 = ml_pick["actual_rank"] is not None and ml_pick["actual_rank"] <= 2
        is_upset = not prediction_correct and ml_pick["actual_rank"] is not None

        # シグナル別: このグループで各シグナル単独なら正解だったか
        signal_details = _check_signal_picks(players)

        # 信頼度再計算
        has_stats = ml_pick["stats_component"] > 0
        has_fit = ml_pick["fit_component"] > 0
        model_version = raw["tournament"].get("model_version", "unknown")
        confidence = _derive_confidence(has_stats, has_fit, model_version)

        analyzed.append({
            "group_id": gid,
            "predicted_winner": predicted_winner,
            "actual_winner": actual_winner,
            "prediction_correct": prediction_correct,
            "prediction_top2": prediction_top2,
            "upset": is_upset,
            "confidence": confidence,
            "players": sorted(players, key=lambda x: x["ml_rank"]),
            "signal_details": signal_details,
        })

    return analyzed


def _check_signal_picks(players: list[dict]) -> dict:
    """各シグナル単独で選んだ場合の正解判定。"""
    result = {}
    for signal in ("odds_component", "stats_component", "fit_component"):
        key = signal.replace("_component", "")
        # シグナル値が全員0ならスキップ
        vals = [p[signal] for p in players]
        if max(vals) == 0:
            result[f"{key}_pick_correct"] = None
            continue
        top_by_signal = max(players, key=lambda x: x[signal])
        result[f"{key}_pick_correct"] = top_by_signal["actual_rank"] == 1

    return result


def _derive_confidence(has_stats: bool, has_fit: bool, model_version: str) -> str:
    """コンポーネント値から信頼度レベルを再計算する。"""
    signals = 1  # odds は常にある
    if has_stats:
        signals += 1
    if has_fit:
        signals += 1
    if signals >= 3 and model_version != "fixed_v0" and model_version != "proxy_v1":
        return "High"
    elif signals >= 2:
        return "Medium"
    else:
        return "Low"


#-----ゲームスコアリング-----

def _is_cut_player(player: dict) -> bool:
    """CUT/WD/DQ/MDF/DNS判定（espn_status + rounds_played + scoreフォールバック）。"""
    status = (player.get("espn_status") or "").upper()
    if status in ("STATUS_CUT", "STATUS_WD", "STATUS_DQ", "STATUS_MDF"):
        return True
    rp = player.get("rounds_played")
    if rp is not None and rp < 4:
        return True
    # フォールバック: scoreベース判定
    score = player.get("score")
    if not score or score == "":
        return player.get("espn_position") is None
    s = str(score).strip().upper()
    if s in ("CUT", "WD", "DQ", "MDF", "DNS"):
        return True
    try:
        s_clean = s.replace("+", "").replace("-", "")
        if s_clean in ("E", ""):
            return False
        float(s_clean)
        return False
    except ValueError:
        return True


def _is_post_cut_wd(player: dict) -> bool:
    """カット通過後のWD判定（3ラウンド以上プレーしたがSTATUS_WD）。"""
    status = (player.get("espn_status") or "").upper()
    rp = player.get("rounds_played") or 0
    if status == "STATUS_WD" and rp >= 3:
        return True
    # フォールバック: rounds_played 3 でscoreが数値 → post-cut WD
    if rp == 3:
        score = player.get("score", "")
        try:
            s = str(score).replace("+", "").replace("-", "")
            if s in ("E", ""):
                return True
            float(s)
            return True
        except (ValueError, TypeError):
            pass
    return False


def _calc_handicap(wgr: int, field_size: int) -> int:
    """ハンデ = round(WGR/100), max = round(0.13 * field_size)。"""
    hc = round(wgr / 100)
    max_hc = round(0.13 * field_size) if field_size > 0 else 20
    return min(hc, max_hc)


def _calc_cut_count(groups: list[dict]) -> int:
    """カット通過者数を算出する。"""
    all_players = [p for g in groups for p in g["players"]]

    # rounds_played がある場合: 4ラウンド以上 = 通過
    players_with_rp = [p for p in all_players if p.get("rounds_played") is not None]
    if players_with_rp:
        return sum(1 for p in players_with_rp if not _is_cut_player(p))

    # フォールバック: espn_positionでソートしてスコアの非単調性を検出
    with_pos = sorted(
        [p for p in all_players if p.get("espn_position") is not None],
        key=lambda p: p["espn_position"],
    )
    if not with_pos:
        return len(all_players) // 2

    def _score_to_num(s: str | None) -> float:
        if not s:
            return 999
        s = str(s).strip().upper()
        if s == "E":
            return 0
        try:
            return float(s.replace("+", ""))
        except (ValueError, TypeError):
            return 999

    # スコアが非単調になる地点を検出
    scores = [_score_to_num(p.get("score")) for p in with_pos]
    # 連続して前のスコアより良くなる（=数値が下がる）場合、その前が cut line
    drop_count = 0
    for i in range(1, len(scores)):
        if scores[i] < scores[i - 1] - 1:
            drop_count += 1
            if drop_count >= 2:
                return i - 1
        else:
            drop_count = 0

    # 検出できない場合: 全選手の約半分を推定
    return len(all_players) // 2


def _calc_player_game_score(
    player: dict,
    group_id: int,
    handicap: int,
    cut_count: int,
    group_cut_count: int,
) -> int:
    """1選手のゲームスコアを計算する。

    Rules:
    - Made cut: espn_position - handicap
    - Post-cut WD: cut_count - handicap (no group penalty)
    - CUT (groups 4+): (cut_count + 1) - handicap
    - CUT (groups 1-3): (cut_count + 1) - handicap + group_cut_count
    """
    if _is_post_cut_wd(player):
        return cut_count - handicap

    if _is_cut_player(player):
        base = (cut_count + 1) - handicap
        if group_id <= 3:
            base += group_cut_count
        return base

    pos = player.get("espn_position")
    if pos is None:
        return (cut_count + 1) - handicap
    return pos - handicap


def _calc_bonuses(
    picks: list[dict],
    groups: list[dict],
    field_size: int,
    cut_count: int,
) -> dict:
    """各種ボーナスを計算する。

    Returns:
        {"winning_pick": int, "best_in_group": int, "no_cut": int,
         "total": int, "details": list[str]}
    """
    winning_pick = 0
    best_in_group = 0
    details: list[str] = []

    for pick_info in picks:
        pick = pick_info["player"]
        gid = pick_info["group_id"]

        # Winning Pick: ピックがトーナメント優勝者
        if pick.get("espn_position") == 1:
            bonus = 50 + gid * 2
            winning_pick += bonus
            details.append(f"Winner pick G{gid}: -{bonus}")

        # Best in Group: ピックがグループ内カット通過者の最低ゲームスコア
        group = next((g for g in groups if g["group_id"] == gid), None)
        if group and len(group["players"]) >= 5:
            # カット通過者のみ（トーナメント優勝者・CUT選手は除外）
            cut_passing = [
                p for p in group["players"]
                if not _is_cut_player(p) and p.get("espn_position") != 1
            ]
            if cut_passing:
                # 各選手のゲームスコアを計算
                group_cut_cnt = sum(1 for p in group["players"] if _is_cut_player(p))
                best_gs = min(
                    _calc_player_game_score(
                        p, gid,
                        _calc_handicap(p.get("wgr", 9999), field_size),
                        cut_count, group_cut_cnt,
                    )
                    for p in cut_passing
                )
                pick_gs = pick_info["game_score"]
                if pick_gs <= best_gs and not _is_cut_player(pick):
                    best_in_group += 10
                    details.append(f"Best in G{gid}: -10")

    # No Cut Bonus
    no_cut = field_size - cut_count if field_size > 0 else 0

    total = winning_pick + best_in_group + no_cut
    if no_cut > 0:
        details.append(f"No cut bonus: -{no_cut}")

    return {
        "winning_pick": winning_pick,
        "best_in_group": best_in_group,
        "no_cut": no_cut,
        "total": total,
        "details": details,
    }


def _score_strategy_game(
    groups: list[dict],
    sort_key: str,
    field_size: int,
    cut_count: int,
) -> dict:
    """指定戦略の全ピックでゲームスコアを計算する。

    G1は2ピック、G2-9は1ピック。
    """
    raw_sum = 0
    per_group: list[dict] = []
    all_picks: list[dict] = []

    for g in groups:
        players = g["players"]
        gid = g["group_id"]
        if not players:
            continue

        # シグナル値が全員0/Noneの場合はスキップ
        vals = [p.get(sort_key, 0) or 0 for p in players]
        if max(vals) == 0 and sort_key not in ("ml_score", "egs"):
            continue

        # egsが全員Noneの場合はスキップ
        if sort_key == "egs" and all(p.get("egs") is None for p in players):
            continue

        # ピック数: G1=2, 他=1
        n_picks = 2 if gid == 1 else 1
        # EGSは低い方が良い（昇順）、他は高い方が良い（降順）
        if sort_key == "egs":
            sorted_players = sorted(
                players,
                key=lambda p: p.get(sort_key) if p.get(sort_key) is not None else 9999,
            )
        else:
            sorted_players = sorted(players, key=lambda p: p.get(sort_key, 0), reverse=True)
        picks_in_group = sorted_players[:n_picks]

        # グループ内CUT選手数
        group_cut_count = sum(1 for p in players if _is_cut_player(p))

        group_data = {
            "group_id": gid,
            "picks": [],
            "bonuses": {},
            "group_total": 0,
        }

        for pick in picks_in_group:
            hc = _calc_handicap(pick.get("wgr", 9999), field_size)
            gs = _calc_player_game_score(pick, gid, hc, cut_count, group_cut_count)
            is_cut = _is_cut_player(pick)

            pick_data = {
                "name": pick["name"],
                "wgr": pick.get("wgr", 9999),
                "handicap": hc,
                "espn_pos": pick.get("espn_position"),
                "is_cut": is_cut,
                "game_score": gs,
                "won": pick.get("actual_rank") == 1,
            }
            group_data["picks"].append(pick_data)
            raw_sum += gs

            all_picks.append({
                "player": pick,
                "group_id": gid,
                "game_score": gs,
            })

        per_group.append(group_data)

    # ボーナス計算
    bonuses = _calc_bonuses(all_picks, groups, field_size, cut_count)
    total = raw_sum - bonuses["total"]

    # per_groupにボーナス情報を追加
    for pg in per_group:
        pg["group_total"] = sum(p["game_score"] for p in pg["picks"])

    return {
        "total": total,
        "raw_sum": raw_sum,
        "bonuses": bonuses,
        "groups_won": sum(
            1 for pg in per_group
            for p in pg["picks"]
            if p["won"]
        ),
        "per_group": per_group,
    }


def _analyze_game_score(groups: list[dict], tournament: dict) -> dict:
    """公式ルール準拠のゲームスコア分析を実行する。

    ハンデ、CUTスコアリング、G1の2ピック、ボーナスを含む。
    """
    field_size = tournament.get("field_size") or 0
    if field_size == 0:
        # フォールバック: グループ内選手数の合計
        all_players = [p for g in groups for p in g["players"]]
        field_size = len(all_players)

    cut_count = _calc_cut_count(groups)

    # 各戦略のスコア計算
    strategies = {
        "ml": "ml_score",
        "odds": "odds_component",
        "stats": "stats_component",
        "fit": "fit_component",
        "game": "egs",
    }

    result: dict = {}
    for name, key in strategies.items():
        result[name] = _score_strategy_game(groups, key, field_size, cut_count)

    # 理論最適値（各グループの勝者を選んだ場合）
    opt_sum = 0
    opt_bonuses_total = 0
    for g in groups:
        gid = g["group_id"]
        winner = next(
            (p for p in g["players"] if p.get("actual_rank") == 1), None,
        )
        if winner:
            hc = _calc_handicap(winner.get("wgr", 9999), field_size)
            group_cut_count = sum(1 for p in g["players"] if _is_cut_player(p))
            gs = _calc_player_game_score(winner, gid, hc, cut_count, group_cut_count)
            opt_sum += gs
            # Winning pick bonus (winner at position 1)
            if winner.get("espn_position") == 1:
                opt_bonuses_total += 50 + gid * 2

    # Optimal gets no-cut bonus too
    no_cut = field_size - cut_count if field_size > 0 else 0
    opt_bonuses_total += no_cut

    result["optimal"] = {
        "total": opt_sum - opt_bonuses_total,
        "raw_sum": opt_sum,
        "bonuses": {"total": opt_bonuses_total},
    }

    result["field_size"] = field_size
    result["cut_count"] = cut_count

    # EGSデータなしの場合はgame戦略を除外
    if "game" in result and not result["game"].get("per_group"):
        del result["game"]

    # 最良戦略の判定
    valid = {
        k: v["total"] for k, v in result.items()
        if k not in ("optimal", "field_size", "cut_count") and isinstance(v, dict) and "per_group" in v
    }
    result["best_strategy"] = min(valid, key=valid.get) if valid else "ml"

    return result


#-----シグナル精度分析-----

def _analyze_signal_accuracy(groups: list[dict]) -> dict:
    """シグナル別の精度を大会全体で集計する。"""
    accuracy: dict[str, dict] = {}

    for signal in ("odds", "stats", "fit"):
        key = f"{signal}_pick_correct"
        valid = [g for g in groups if g["signal_details"].get(key) is not None]
        correct = sum(1 for g in valid if g["signal_details"][key])
        total = len(valid)
        accuracy[f"{signal}_only"] = {
            "correct": correct,
            "total": total,
            "rate": correct / total if total else 0,
        }

    # 統合ML
    correct_ml = sum(1 for g in groups if g["prediction_correct"])
    total_ml = len(groups)
    accuracy["combined_ml"] = {
        "correct": correct_ml,
        "total": total_ml,
        "rate": correct_ml / total_ml if total_ml else 0,
    }

    # どのシグナルが最良/最悪か
    rates = {k: v["rate"] for k, v in accuracy.items() if v["total"] > 0}
    accuracy["best_signal"] = max(rates, key=rates.get) if rates else "N/A"
    accuracy["worst_signal"] = min(rates, key=rates.get) if rates else "N/A"

    return accuracy


#-----信頼度キャリブレーション-----

def _analyze_confidence(groups: list[dict]) -> dict:
    """信頼度レベルごとの正答率を集計する。"""
    cal: dict[str, dict] = {}

    for level in ("High", "Medium", "Low"):
        matched = [g for g in groups if g["confidence"] == level]
        correct = sum(1 for g in matched if g["prediction_correct"])
        total = len(matched)
        cal[level] = {
            "total": total,
            "correct": correct,
            "rate": correct / total if total else 0,
        }

    return cal


#-----アップセット分析-----

def _analyze_upsets(groups: list[dict]) -> tuple[list[dict], dict]:
    """ML #1 pick が負けたグループを詳細分析する。"""
    upsets: list[dict] = []

    for g in groups:
        if not g["upset"]:
            continue

        ml_pick = next(p for p in g["players"] if p["ml_rank"] == 1)
        actual_winner = next(
            (p for p in g["players"] if p["actual_rank"] == 1), None
        )
        if not actual_winner:
            continue

        # アップセットの特徴分析
        traits: list[str] = []
        if actual_winner["fit_component"] > ml_pick["fit_component"]:
            traits.append("fit advantage")
        if actual_winner["stats_component"] > ml_pick["stats_component"]:
            traits.append("stats advantage")
        if actual_winner["odds_component"] < ml_pick["odds_component"] * 0.5:
            traits.append("odds underdog")

        upsets.append({
            "group_id": g["group_id"],
            "ml_pick": ml_pick["name"],
            "ml_pick_score": ml_pick["ml_score"],
            "ml_pick_actual_rank": ml_pick["actual_rank"],
            "actual_winner": actual_winner["name"],
            "actual_winner_ml_score": actual_winner["ml_score"],
            "actual_winner_ml_rank": actual_winner["ml_rank"],
            "score_gap": ml_pick["ml_score"] - actual_winner["ml_score"],
            "upset_traits": traits,
        })

    # パターン検出
    patterns: dict = {"common_traits": [], "avg_score_gap": 0}
    if upsets:
        all_traits: list[str] = []
        for u in upsets:
            all_traits.extend(u["upset_traits"])
        # 2回以上出現するtrait
        from collections import Counter
        trait_counts = Counter(all_traits)
        patterns["common_traits"] = [
            f"{t} ({c}/{len(upsets)})"
            for t, c in trait_counts.most_common()
            if c >= 2 or len(upsets) <= 2
        ]
        patterns["avg_score_gap"] = sum(u["score_gap"] for u in upsets) / len(upsets)

    return upsets, patterns


#-----サマリー生成-----

def _generate_summary(
    groups: list[dict],
    signal_accuracy: dict,
    confidence: dict,
    upsets: list[dict],
    game_score: dict | None = None,
) -> dict:
    """全体サマリーとキーテイクアウェイを自動生成する。"""
    total = len(groups)
    ml_correct = sum(1 for g in groups if g["prediction_correct"])
    ml_top2 = sum(1 for g in groups if g["prediction_top2"])
    ml_rate = ml_correct / total if total else 0
    top2_rate = ml_top2 / total if total else 0

    odds_data = signal_accuracy.get("odds_only", {})
    odds_rate = odds_data.get("rate", 0)

    headline = _generate_headline(ml_rate, odds_rate, total)
    takeaways = _generate_takeaways(
        ml_rate, odds_rate, top2_rate, total, ml_correct,
        signal_accuracy, confidence, upsets, game_score,
    )

    return {
        "total_groups": total,
        "ml_correct": ml_correct,
        "ml_win_rate": ml_rate,
        "ml_top2": ml_top2,
        "ml_top2_rate": top2_rate,
        "odds_win_rate": odds_rate,
        "headline": headline,
        "key_takeaways": takeaways,
        "best_signal": signal_accuracy.get("best_signal", "N/A"),
        "worst_signal": signal_accuracy.get("worst_signal", "N/A"),
    }


def _generate_headline(ml_rate: float, odds_rate: float, total: int) -> str:
    """ヘッドライン文を自動生成する。"""
    ml_pct = f"{ml_rate:.0%}"
    odds_pct = f"{odds_rate:.0%}"

    diff = ml_rate - odds_rate
    if abs(diff) < 0.01:
        return f"ML and Odds tied at {ml_pct} accuracy across {total} groups"
    elif diff > 0:
        return f"ML outperformed Odds: {ml_pct} vs {odds_pct} across {total} groups"
    else:
        return f"Odds edged ML: {odds_pct} vs {ml_pct} across {total} groups"


def _generate_takeaways(
    ml_rate: float, odds_rate: float, top2_rate: float,
    total: int, ml_correct: int,
    signal_accuracy: dict, confidence: dict, upsets: list[dict],
    game_score: dict | None = None,
) -> list[str]:
    """3-6個のキーテイクアウェイを自動生成する。"""
    takeaways: list[str] = []

    # 1. 総合成績
    takeaways.append(
        f"ML predicted {ml_correct}/{total} group winners ({ml_rate:.0%}), "
        f"Top-2 accuracy: {top2_rate:.0%}"
    )

    # 2. Game Score
    if game_score:
        ml_gs = game_score.get("ml", {})
        opt_gs = game_score.get("optimal", {})
        best_st = game_score.get("best_strategy", "ml")
        if ml_gs.get("per_group"):
            opt_total = opt_gs.get("total", 0)
            ml_total = ml_gs["total"]
            gap = ml_total - opt_total
            takeaways.append(
                f"Game Score: ML={ml_total} (optimal={opt_total}, gap={gap})"
            )
            if best_st != "ml":
                best_total = game_score[best_st]["total"]
                takeaways.append(
                    f"Best strategy: {best_st} ({best_total}) vs ML ({ml_total})"
                )

    # 3. ML vs Odds
    diff = ml_rate - odds_rate
    if diff > 0.05:
        takeaways.append(
            f"ML integration added value: +{diff:.0%} over odds-only baseline"
        )
    elif diff < -0.05:
        takeaways.append(
            f"Odds-only outperformed ML by {-diff:.0%} - consider reweighting signals"
        )
    else:
        takeaways.append("ML and odds performed similarly this tournament")

    # 4. シグナル比較
    best = signal_accuracy.get("best_signal", "N/A")
    worst = signal_accuracy.get("worst_signal", "N/A")
    if best != worst and best != "N/A":
        best_rate = signal_accuracy.get(best, {}).get("rate", 0)
        worst_rate = signal_accuracy.get(worst, {}).get("rate", 0)
        best_label = best.replace("_only", "").replace("combined_", "")
        worst_label = worst.replace("_only", "").replace("combined_", "")
        takeaways.append(
            f"Best signal: {best_label} ({best_rate:.0%}), "
            f"Worst: {worst_label} ({worst_rate:.0%})"
        )

    # 5. 信頼度
    high = confidence.get("High", {})
    medium = confidence.get("Medium", {})
    if high["total"] > 0 and medium["total"] > 0:
        if high["rate"] > medium["rate"]:
            takeaways.append(
                f"Confidence calibration good: High={high['rate']:.0%}, Medium={medium['rate']:.0%}"
            )
        else:
            takeaways.append(
                f"Confidence miscalibrated: High={high['rate']:.0%} vs Medium={medium['rate']:.0%}"
            )
    elif medium["total"] > 0:
        takeaways.append(
            f"All groups rated Medium confidence - accuracy: {medium['rate']:.0%}"
        )

    # 6. アップセット
    if upsets:
        takeaways.append(
            f"{len(upsets)} upset(s) detected where ML #1 pick lost their group"
        )

    return takeaways[:6]


#-----ゲームスコア比較出力-----

def format_game_score_comparison(review_data: dict) -> str:
    """ML vs EGS vs 各戦略のゲームスコア比較をフォーマットする。

    Args:
        review_data: analyze_tournament() の戻り値

    Returns:
        フォーマット済み文字列 (コンソール出力用)
    """
    gs = review_data.get("game_score")
    if not gs:
        return "  [INFO] No game score data available"

    tournament = review_data.get("tournament", {})
    name = tournament.get("name", "Unknown")

    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  GAME SCORE COMPARISON: {name}")
    lines.append("=" * 70)

    field_size = gs.get("field_size", 0)
    cut_count = gs.get("cut_count", 0)
    lines.append(f"  Field: {field_size}  Cut: {cut_count}  Made cut: {field_size - cut_count}")
    lines.append("")

    # --- 戦略別サマリーテーブル ---
    lines.append("  Strategy        Raw Score  Bonuses  TOTAL  Groups Won")
    lines.append("  " + "-" * 62)

    strategy_names = {
        "ml": "ML Pick",
        "game": "EGS Pick",
        "odds": "Odds Only",
        "stats": "Stats Only",
        "fit": "Course Fit",
        "optimal": "Optimal*",
    }
    strategy_order = ["ml", "game", "odds", "stats", "fit", "optimal"]

    best_total = None
    best_key = None
    for key in strategy_order:
        data = gs.get(key)
        if not data or not isinstance(data, dict):
            continue
        total = data.get("total", 0)
        if key != "optimal":
            if best_total is None or total < best_total:
                best_total = total
                best_key = key

    for key in strategy_order:
        data = gs.get(key)
        if not data or not isinstance(data, dict):
            continue

        label = strategy_names.get(key, key)
        total = data.get("total", 0)
        raw = data.get("raw_sum", 0)
        bonuses = data.get("bonuses", {}).get("total", 0)
        groups_won = data.get("groups_won", "-")
        if key == "optimal":
            groups_won = "-"

        marker = " <-- BEST" if key == best_key else ""
        lines.append(
            f"  {label:<16} {raw:>9}  {'-' + str(bonuses):>7}  {total:>5}{marker}"
            + (f"  {groups_won}" if groups_won != "-" else "")
        )

    lines.append("")
    lines.append(f"  * Optimal = hindsight (picking each group winner)")
    lines.append("")

    # --- ML vs EGS 詳細比較 ---
    ml_data = gs.get("ml")
    egs_data = gs.get("game")

    if ml_data and egs_data and ml_data.get("per_group") and egs_data.get("per_group"):
        lines.append("  " + "-" * 62)
        lines.append("  ML vs EGS: Group-by-Group Detail")
        lines.append("  " + "-" * 62)
        lines.append(
            f"  {'G':>3}  {'ML Pick':<20} {'GS':>4}  {'EGS Pick':<20} {'GS':>4}  {'Better':>6}"
        )
        lines.append("  " + "-" * 62)

        ml_groups = {pg["group_id"]: pg for pg in ml_data["per_group"]}
        egs_groups = {pg["group_id"]: pg for pg in egs_data["per_group"]}

        ml_wins = 0
        egs_wins = 0
        ties = 0

        all_gids = sorted(set(list(ml_groups.keys()) + list(egs_groups.keys())))
        for gid in all_gids:
            ml_g = ml_groups.get(gid)
            egs_g = egs_groups.get(gid)

            if not ml_g or not egs_g:
                continue

            # 各グループのピック名とスコア
            ml_picks_str = ", ".join(p["name"].split()[-1][:12] for p in ml_g["picks"])
            ml_gs = ml_g["group_total"]
            egs_picks_str = ", ".join(p["name"].split()[-1][:12] for p in egs_g["picks"])
            egs_gs = egs_g["group_total"]

            if ml_gs < egs_gs:
                better = "ML"
                ml_wins += 1
            elif egs_gs < ml_gs:
                better = "EGS"
                egs_wins += 1
            else:
                better = "Tie"
                ties += 1

            # 同じピックの場合
            ml_names = set(p["name"] for p in ml_g["picks"])
            egs_names = set(p["name"] for p in egs_g["picks"])
            if ml_names == egs_names:
                better = "Same"

            lines.append(
                f"  {gid:>3}  {ml_picks_str:<20} {ml_gs:>4}  "
                f"{egs_picks_str:<20} {egs_gs:>4}  {better:>6}"
            )

        lines.append("  " + "-" * 62)

        ml_total = ml_data["total"]
        egs_total = egs_data["total"]
        diff = ml_total - egs_total

        lines.append(f"  ML  Total: {ml_total} (raw {ml_data['raw_sum']} - bonus {ml_data['bonuses']['total']})")
        lines.append(f"  EGS Total: {egs_total} (raw {egs_data['raw_sum']} - bonus {egs_data['bonuses']['total']})")
        lines.append("")

        if diff > 0:
            lines.append(f"  --> EGS wins by {diff} points")
        elif diff < 0:
            lines.append(f"  --> ML wins by {-diff} points")
        else:
            lines.append(f"  --> Tied")

        lines.append(f"  Group wins: ML={ml_wins}, EGS={egs_wins}, Tie/Same={ties}")

    # --- ボーナス詳細 ---
    for strat_key, strat_name in [("ml", "ML"), ("game", "EGS")]:
        data = gs.get(strat_key)
        if data and data.get("bonuses", {}).get("details"):
            lines.append(f"  {strat_name} bonuses: {', '.join(data['bonuses']['details'])}")

    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)
