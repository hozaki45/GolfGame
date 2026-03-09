"""Result Collector - 大会終了後のデータ収集・結果蓄積モジュール。

大会終了後にESPN結果を取得し、事前予測と比較して精度を評価する。
蓄積データはPhase 2 MLモデル（グループ勝者予測）の訓練に使用。

Usage:
    uv run python -m src.result_collector                  # 最新大会の結果収集
    uv run python -m src.result_collector --date 20260222  # 日付指定
    uv run python -m src.result_collector --status         # 蓄積状況表示
    uv run python -m src.result_collector --accuracy       # 全大会の精度レポート
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from tabulate import tabulate


#-----Result Collection-----

def collect_results(
    tournament_id: int | None = None,
    espn_date: str = "",
) -> bool:
    """大会結果を収集し、予測精度を評価する。

    1. ESPN APIから実際の結果を取得
    2. golfgame.db の results テーブルに保存
    3. ML予測が保存済みなら精度を算出
    4. 精度レポートを表示・保存

    Args:
        tournament_id: 対象の大会DB ID（Noneなら最新大会）
        espn_date: ESPN API用の日付 (YYYYMMDD)

    Returns:
        True: 結果保存成功
    """
    from src.database import (
        save_tournament_results,
        get_ml_accuracy,
        get_accumulation_status,
        get_connection,
    )

    print("=" * 70)
    print("  Post-Tournament Result Collection")
    print("=" * 70)

    # Step 1: ESPN結果取得・保存
    print("\n[STEP 1] Fetching ESPN results and saving to database...")
    success = save_tournament_results(
        tournament_id=tournament_id,
        espn_date=espn_date,
    )

    if not success:
        print("[ERROR] Failed to collect results")
        return False

    # 保存した大会のIDを取得
    conn = get_connection()
    try:
        if tournament_id:
            tid = tournament_id
        else:
            row = conn.execute(
                "SELECT id FROM tournaments WHERE status='results_saved' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            tid = row["id"] if row else None
    finally:
        conn.close()

    if not tid:
        print("[WARN] Could not determine tournament ID")
        return True  # 結果自体は保存済み

    # Step 2: ML予測精度を算出
    print("\n[STEP 2] Evaluating prediction accuracy...")
    accuracy = get_ml_accuracy(tid)

    if accuracy["tournaments"]:
        report = format_accuracy_report(accuracy, single_tournament=True)
        print(report)

        # レポート保存
        out_dir = Path("data/output")
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = out_dir / f"accuracy_report_{date_str}.txt"
        report_path.write_text(report, encoding="utf-8")
        print(f"[OK] Accuracy report saved: {report_path}")
    else:
        print("[INFO] No ML predictions found for this tournament")
        print("[INFO] ML predictions are saved when running the full pipeline (run_pipeline.py)")

    # Step 2.5: ゲームスコア比較 (ML vs EGS) + レビューHTML生成
    print("\n[STEP 2.5] Game score comparison...")
    try:
        from src.post_tournament_analyzer import (
            analyze_tournament,
            format_game_score_comparison,
        )
        from src.review_report import save_review_html

        review_data = analyze_tournament(tid)
        if review_data:
            comparison = format_game_score_comparison(review_data)
            print(comparison)

            review_path = save_review_html(review_data)
            print(f"[OK] Review report: {review_path}")
    except Exception as e:
        print(f"[WARN] Game score comparison skipped: {e}")

    # Step 3: 蓄積状況表示
    print("\n[STEP 3] Accumulation status...")
    status = get_accumulation_status()
    report_status = format_status_report(status)
    print(report_status)

    # ポータルページ更新
    try:
        from src.portal import generate_portal
        generate_portal()
    except Exception as e:
        print(f"[WARN] Portal update skipped: {e}")

    return True


#-----Batch Collection (Step 0)-----

def collect_all_pending() -> dict:
    """終了済みの全大会の結果を一括収集する（Step 0用）。

    Returns:
        {
            "collected": int,
            "failed": int,
            "tournaments": list,
            "accuracy": dict|None,
        }
    """
    from src.database import (
        get_pending_result_tournaments,
        save_tournament_results,
        get_ml_accuracy,
    )

    pending = get_pending_result_tournaments()

    if not pending:
        return {
            "collected": 0,
            "failed": 0,
            "tournaments": [],
            "accuracy": None,
        }

    collected = 0
    failed = 0
    collected_tournaments: list[dict] = []

    for t in pending:
        print(f"\n  Processing: {t['name']} (end_date={t['end_date']})...")

        # end_dateからESPN日付パラメータを生成 (YYYY-MM-DD → YYYYMMDD)
        espn_date = t["end_date"].replace("-", "") if t["end_date"] else ""

        success = save_tournament_results(
            tournament_id=t["id"],
            espn_date=espn_date,
        )

        if success:
            collected += 1
            collected_tournaments.append(t)
            print(f"  [OK] Results saved for '{t['name']}'")

            # 振り返りレポート生成 + ゲームスコア比較出力
            try:
                from src.post_tournament_analyzer import (
                    analyze_tournament,
                    format_game_score_comparison,
                )
                from src.review_report import save_review_html

                review_data = analyze_tournament(t["id"])
                if review_data:
                    # ゲームスコア比較をコンソール出力
                    comparison = format_game_score_comparison(review_data)
                    print(comparison)

                    review_path = save_review_html(review_data)
                    print(f"  [OK] Review report: {review_path}")
            except Exception as e:
                print(f"  [WARN] Review report skipped: {e}")
        else:
            failed += 1
            print(f"  [WARN] Could not collect results for '{t['name']}'")

    # 精度計算（収集した全大会分）
    accuracy = None
    if collected > 0:
        accuracy = get_ml_accuracy()

    # ポータルページ更新
    try:
        from src.portal import generate_portal
        generate_portal()
    except Exception as e:
        print(f"  [WARN] Portal update skipped: {e}")

    return {
        "collected": collected,
        "failed": failed,
        "tournaments": collected_tournaments,
        "accuracy": accuracy,
    }


def format_step0_summary(result: dict) -> str:
    """Step 0（自動結果収集）のコンソール表示フォーマット。

    Args:
        result: collect_all_pending() の戻り値

    Returns:
        フォーマット済み文字列
    """
    lines: list[str] = []

    collected = result["collected"]
    failed = result["failed"]

    lines.append("")
    lines.append("=" * 70)
    lines.append("  [STEP 0] Post-Tournament Result Collection")
    lines.append("=" * 70)

    if collected == 0 and failed == 0:
        lines.append("  No completed tournaments pending result collection.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  Collected: {collected} tournament(s)")
    if failed:
        lines.append(f"  Failed:    {failed} tournament(s)")
    lines.append("")

    for t in result["tournaments"]:
        lines.append(f"    - {t['name']} (ended {t['end_date']})")
    lines.append("")

    # 精度レポート（ML予測がある場合のみ）
    accuracy = result.get("accuracy")
    if accuracy and accuracy.get("tournaments"):
        report = format_accuracy_report(accuracy, single_tournament=(collected == 1))
        lines.append(report)
    else:
        lines.append("  [INFO] No ML predictions found - accuracy comparison skipped")
        lines.append("")

    return "\n".join(lines)


#-----Accuracy Report-----

def format_accuracy_report(
    accuracy: dict,
    single_tournament: bool = False,
) -> str:
    """ML予測精度のフォーマット済みレポートを生成。

    Args:
        accuracy: get_ml_accuracy() の戻り値
        single_tournament: True なら単一大会表示

    Returns:
        フォーマット済み文字列
    """
    lines: list[str] = []
    summary = accuracy.get("summary")
    tournaments = accuracy.get("tournaments", [])

    if not summary or not tournaments:
        return "  No accuracy data available."

    lines.append("")
    lines.append("=" * 70)
    if single_tournament:
        lines.append("  PREDICTION ACCURACY REPORT")
    else:
        lines.append("  CUMULATIVE PREDICTION ACCURACY (All Tournaments)")
    lines.append("=" * 70)
    lines.append("")

    # 大会別テーブル
    table_data = []
    for t in tournaments:
        ml_pct = f"{t['ml_correct_1st']/t['groups']*100:.0f}%" if t["groups"] else "-"
        odds_pct = f"{t['odds_correct_1st']/t['groups']*100:.0f}%" if t["groups"] else "-"
        table_data.append([
            t["tournament_id"],
            t["name"][:35],
            t["groups"],
            f"{t['ml_correct_1st']}/{t['groups']}",
            ml_pct,
            f"{t['odds_correct_1st']}/{t['groups']}",
            odds_pct,
            t["model_version"],
        ])

    headers = ["ID", "Tournament", "Groups", "ML Correct", "ML%",
               "Odds Correct", "Odds%", "Model"]
    lines.append(tabulate(table_data, headers=headers, tablefmt="simple"))
    lines.append("")

    # サマリー
    lines.append("-" * 70)
    lines.append(f"  Overall ML  #1 Accuracy:  {summary['ml_correct_1st']}/{summary['total_groups']} "
                 f"({summary['ml_accuracy_1st']:.1%})")
    lines.append(f"  Overall Odds #1 Accuracy: {summary['odds_correct_1st']}/{summary['total_groups']} "
                 f"({summary['odds_accuracy_1st']:.1%})")

    if summary["total_groups"] > 0:
        lines.append(f"  ML Top-2 Accuracy:        {summary['ml_top2_correct']}/{summary['total_groups']} "
                     f"({summary['ml_top2_correct']/summary['total_groups']:.1%})")

    diff = summary["ml_accuracy_1st"] - summary["odds_accuracy_1st"]
    if diff > 0:
        lines.append(f"  ML vs Odds:  ML is {diff:.1%} BETTER than odds-only")
    elif diff < 0:
        lines.append(f"  ML vs Odds:  Odds is {-diff:.1%} better than ML")
    else:
        lines.append(f"  ML vs Odds:  Tied")

    lines.append("")

    # Phase 2 進捗
    lines.append("-" * 70)
    if summary["phase2_ready"]:
        lines.append("  Phase 2 Status: READY - Sufficient group results accumulated!")
        lines.append("  Run: uv run python -m src.ml_predictor --train-phase2")
    else:
        lines.append(f"  Phase 2 Status: Collecting... ({summary['phase2_progress']} groups)")
        remaining = 50 - summary["total_groups"]
        est_tournaments = max(1, remaining // 10)
        lines.append(f"  Estimated: ~{est_tournaments} more tournaments needed")
    lines.append("")

    return "\n".join(lines)


#-----Status Report-----

def format_status_report(status: dict) -> str:
    """結果蓄積状況のフォーマット済みレポートを生成。

    Args:
        status: get_accumulation_status() の戻り値

    Returns:
        フォーマット済み文字列
    """
    lines: list[str] = []

    lines.append("")
    lines.append("=" * 70)
    lines.append("  DATA ACCUMULATION STATUS")
    lines.append("=" * 70)
    lines.append("")

    table_data = [
        ["Total tournaments in DB", status["total_tournaments"]],
        ["With ESPN results", status["with_results"]],
        ["With ML predictions", status["with_ml_predictions"]],
        ["Total group results", status["total_group_results"]],
        ["ML predictions + results", status["ml_with_results"]],
    ]
    lines.append(tabulate(table_data, tablefmt="simple"))
    lines.append("")

    # Phase 2プログレスバー
    progress = status["ml_with_results"]
    target = 50
    bar_len = 30
    filled = int(min(progress / target, 1.0) * bar_len)
    bar = ">" * filled + "." * (bar_len - filled)

    lines.append(f"  Phase 2 Progress: [{bar}] {status['phase2_progress']}")

    if status["phase2_ready"]:
        lines.append("  Status: READY for Phase 2 training!")
    else:
        lines.append(f"  Status: Need {max(0, 50 - progress)} more group results with ML predictions")
    lines.append("")

    # 使い方ガイド
    lines.append("-" * 70)
    lines.append("  Workflow:")
    lines.append("    1. Before tournament: uv run python run_pipeline.py")
    lines.append("       (saves odds, stats, ML predictions to DB)")
    lines.append("    2. After tournament:  uv run python -m src.result_collector")
    lines.append("       (fetches ESPN results, evaluates accuracy)")
    lines.append("    3. Check status:      uv run python -m src.result_collector --status")
    lines.append("")

    return "\n".join(lines)


#-----CLI-----

def main() -> None:
    """CLIエントリポイント。"""
    sys.stdout.reconfigure(encoding="utf-8")

    import argparse
    parser = argparse.ArgumentParser(
        description="Post-tournament result collection & accuracy evaluation",
    )
    parser.add_argument(
        "--date", type=str, default="",
        help="ESPN date (YYYYMMDD) for specific tournament lookup",
    )
    parser.add_argument(
        "--tournament-id", type=int, default=None,
        help="Database tournament ID to collect results for",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show data accumulation status only",
    )
    parser.add_argument(
        "--accuracy", action="store_true",
        help="Show cumulative accuracy report (all tournaments)",
    )

    args = parser.parse_args()

    if args.status:
        from src.database import get_accumulation_status
        status = get_accumulation_status()
        print(format_status_report(status))
        return

    if args.accuracy:
        from src.database import get_ml_accuracy
        accuracy = get_ml_accuracy()
        report = format_accuracy_report(accuracy, single_tournament=False)
        if report.strip():
            print(report)
        else:
            print("[INFO] No accuracy data yet.")
            print("[INFO] Run the full pipeline before a tournament, then collect results after.")
        return

    # デフォルト: 結果収集
    success = collect_results(
        tournament_id=args.tournament_id,
        espn_date=args.date,
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
