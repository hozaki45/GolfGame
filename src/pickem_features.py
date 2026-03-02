"""Pick'em 群衆知恵 (Crowd Wisdom) 特徴量抽出。

pick'em履歴データからML予測用の特徴量を算出する。
主に群衆のピック傾向をスコア化し、EnsemblePredictorの第4信号として使用。

Usage:
    uv run python -m src.pickem_features --summary
    uv run python -m src.pickem_features --player "Scottie Scheffler"
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass

from src.database import get_connection


#-----Data Classes-----

@dataclass
class CrowdSignal:
    """1選手のグループ内crowd wisdom信号。"""
    player_name: str
    pick_popularity: float    # 何%のユーザーがピックしたか (0.0-1.0)
    weighted_score: float     # ユーザー精度で重み付けしたピック率 (0.0-1.0)
    crowd_component: float    # 最終的な0-100スコア


#-----User Accuracy-----

def compute_user_accuracy(conn: sqlite3.Connection) -> dict[str, float]:
    """各ユーザーのグループ勝者的中率を算出。

    Pick'emの過去データから、各ユーザーが何%のグループで
    実際の勝者を正しくピックしたかを計算。

    Returns:
        {username: accuracy(0.0-1.0)}
    """
    # 全ピックデータを取得
    rows = conn.execute("""
        SELECT
            pp.username,
            pp.group_num,
            pp.picked_player,
            pt.pk,
            pt.name as tournament_name
        FROM pickem_picks pp
        JOIN pickem_tournaments pt ON pt.id = pp.pickem_tournament_id
        ORDER BY pt.pk, pp.username, pp.group_num
    """).fetchall()

    if not rows:
        return {}

    # グループ勝者を特定するには結果データが必要
    # 現時点ではpick'em上の勝者データがないため、
    # 単純な参加頻度ベースの信頼度スコアを使用
    user_picks: dict[str, int] = defaultdict(int)
    for r in rows:
        user_picks[r["username"]] += 1

    # 参加頻度で重み付け（多く参加 = より信頼できるユーザー）
    if not user_picks:
        return {}

    max_picks = max(user_picks.values())
    return {u: count / max_picks for u, count in user_picks.items()}


#-----Crowd Score-----

def compute_crowd_scores(
    player_names: list[str],
    group_num: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, CrowdSignal] | None:
    """指定選手リストに対するcrowd wisdom信号を算出。

    グループ内の選手がpick'em履歴で何回ピックされているかを集計し、
    群衆のコンセンサスを数値化する。

    Args:
        player_names: グループ内の選手名リスト
        group_num: グループ番号（None=全グループ集計）
        conn: DB接続（Noneなら自動接続）

    Returns:
        {player_name: CrowdSignal} or None（データなし時）
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        user_accuracy = compute_user_accuracy(conn)
        if not user_accuracy:
            return None

        # 各選手のピック数を集計（全大会の全グループから）
        # 選手名はfuzzy matchを考慮して部分一致検索
        player_pick_counts: dict[str, int] = defaultdict(int)
        player_weighted_counts: dict[str, float] = defaultdict(float)
        total_group_slots = 0  # 全グループのユーザー数合計

        for name in player_names:
            # 短縮名でマッチ（pick'emデータは短縮名を使用）
            normalized = _normalize_for_search(name)

            rows = conn.execute("""
                SELECT pp.username, pp.picked_player
                FROM pickem_picks pp
                WHERE LOWER(pp.picked_player) LIKE ?
            """, (f"%{normalized}%",)).fetchall()

            pick_count = len(rows)
            weighted_count = sum(
                user_accuracy.get(r["username"], 0.5)
                for r in rows
            )
            player_pick_counts[name] = pick_count
            player_weighted_counts[name] = weighted_count

        # ユニーク大会×グループの総数を取得
        total_slots_row = conn.execute("""
            SELECT COUNT(DISTINCT pickem_tournament_id || '-' || group_num) as c
            FROM pickem_picks
        """).fetchone()
        total_unique_slots = total_slots_row["c"] if total_slots_row else 1

        # ユーザー数の平均を取得
        avg_users_row = conn.execute(
            "SELECT AVG(num_users) as avg FROM pickem_tournaments WHERE num_users > 0"
        ).fetchone()
        avg_users = avg_users_row["avg"] if avg_users_row and avg_users_row["avg"] else 8

        # ピック率を計算
        total_picks = sum(player_pick_counts.values())
        if total_picks == 0:
            return None

        # グループ内での相対スコアを算出
        max_count = max(player_pick_counts.values()) if player_pick_counts else 1
        max_weighted = max(player_weighted_counts.values()) if player_weighted_counts else 1.0

        signals: dict[str, CrowdSignal] = {}
        for name in player_names:
            count = player_pick_counts.get(name, 0)
            weighted = player_weighted_counts.get(name, 0.0)

            popularity = count / max_count if max_count > 0 else 0.0
            w_score = weighted / max_weighted if max_weighted > 0 else 0.0

            # crowd_component: popularity(60%) + weighted_score(40%) → 0-100
            crowd_component = (popularity * 0.6 + w_score * 0.4) * 100.0

            signals[name] = CrowdSignal(
                player_name=name,
                pick_popularity=popularity,
                weighted_score=w_score,
                crowd_component=max(0.0, min(100.0, crowd_component)),
            )

        return signals

    finally:
        if close_conn:
            conn.close()


def get_crowd_score_for_group(
    player_names: list[str],
) -> dict[str, float]:
    """グループ内の各選手のcrowdスコア（0-100）を返す。

    ml_predictor.py から呼ばれるシンプルなインターフェース。

    Args:
        player_names: グループ内の選手名リスト

    Returns:
        {player_name: crowd_score(0-100)}
    """
    signals = compute_crowd_scores(player_names)
    if signals is None:
        return {}
    return {name: sig.crowd_component for name, sig in signals.items()}


def _normalize_for_search(name: str) -> str:
    """選手名を検索用に正規化。

    Pick'emでは短縮名（例: "Tommy Fle"）が使われるため、
    姓の最初の3文字以上でマッチさせる。
    """
    parts = name.strip().lower().split()
    if len(parts) >= 2:
        # 姓の最初の3文字を使用（短縮名対応）
        last_name = parts[-1][:3]
        first_name = parts[0]
        return f"{first_name}%{last_name}"
    return name.strip().lower()


#-----Summary Report-----

def show_summary() -> None:
    """pick'emデータのサマリーレポートを表示。"""
    conn = get_connection()
    try:
        # ユーザー精度
        accuracy = compute_user_accuracy(conn)
        if not accuracy:
            print("[INFO] No pick'em data available")
            return

        print("[INFO] User Engagement Scores:")
        for user, score in sorted(accuracy.items(), key=lambda x: -x[1]):
            print(f"  {user:15s}: {score:.2f}")

        # よくピックされる選手Top20
        rows = conn.execute("""
            SELECT picked_player, COUNT(*) as cnt
            FROM pickem_picks
            GROUP BY LOWER(picked_player)
            ORDER BY cnt DESC
            LIMIT 20
        """).fetchall()

        print(f"\n[INFO] Most Picked Players (Top 20):")
        for r in rows:
            print(f"  {r['picked_player']:25s}: {r['cnt']} times")

        # 大会あたりの平均グループ数
        avg = conn.execute(
            "SELECT AVG(num_groups) as avg FROM pickem_tournaments WHERE num_groups > 0"
        ).fetchone()
        print(f"\n[INFO] Average groups per tournament: {avg['avg']:.1f}")

    finally:
        conn.close()


def show_player_history(player_name: str) -> None:
    """特定選手のpick'em履歴を表示。"""
    conn = get_connection()
    try:
        normalized = _normalize_for_search(player_name)
        rows = conn.execute("""
            SELECT
                pt.name as tournament,
                pp.username,
                pp.group_num,
                pp.picked_player
            FROM pickem_picks pp
            JOIN pickem_tournaments pt ON pt.id = pp.pickem_tournament_id
            WHERE LOWER(pp.picked_player) LIKE ?
            ORDER BY pt.pk DESC, pp.username
        """, (f"%{normalized}%",)).fetchall()

        if not rows:
            print(f"[INFO] No picks found for '{player_name}'")
            return

        print(f"[INFO] Pick history for '{player_name}' ({len(rows)} picks):")
        current_tournament = ""
        for r in rows:
            if r["tournament"] != current_tournament:
                current_tournament = r["tournament"]
                print(f"\n  {current_tournament}:")
            print(f"    {r['username']:15s} Group {r['group_num']}: {r['picked_player']}")

    finally:
        conn.close()


#-----CLI-----

def main():
    """CLI エントリポイント。"""
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Pick'em Crowd Wisdom 特徴量")
    parser.add_argument("--summary", action="store_true", help="サマリーレポート")
    parser.add_argument("--player", type=str, help="選手のpick履歴を表示")
    args = parser.parse_args()

    if args.player:
        show_player_history(args.player)
    else:
        show_summary()


if __name__ == "__main__":
    main()
