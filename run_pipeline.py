"""Full analysis pipeline - Download picks, fetch odds, generate HTML report.

This script is the main entry point for both local runs and GitHub Actions.
"""

from __future__ import annotations

import sys


def _verify_odds_tournament(
    result,
    espn_player_names: list[str],
    espn_tournament_name: str,
) -> None:
    """Verify that fetched odds belong to the correct tournament.

    Compares player names from the odds source against ESPN player list
    and picks CSV players to detect tournament mismatches.
    """
    from fuzzywuzzy import fuzz

    picks_players = {
        p.name.lower().strip()
        for group in result.groups.values()
        for p in group
    }
    odds_players = {
        p.name.lower().strip()
        for group in result.groups.values()
        for p in group
        if p.best_odds is not None
    }

    if not odds_players:
        print("[WARN] Odds verification: no odds data available")
        return

    # Check 1: How many picks CSV players have matching odds?
    picks_with_odds = len(odds_players)
    total_picks = len(picks_players)
    odds_match_pct = picks_with_odds / total_picks * 100 if total_picks else 0

    # Check 2: If ESPN player list available, check overlap with odds
    espn_overlap_pct = 0.0
    if espn_player_names:
        espn_lower = {n.lower().strip() for n in espn_player_names}
        overlap = 0
        for odds_name in odds_players:
            # Exact or fuzzy match against ESPN names
            if odds_name in espn_lower:
                overlap += 1
                continue
            best = max(
                (fuzz.token_sort_ratio(odds_name, en) for en in espn_lower),
                default=0,
            )
            if best >= 80:
                overlap += 1
        espn_overlap_pct = overlap / len(odds_players) * 100

    print(f"[INFO] Odds verification: {picks_with_odds}/{total_picks} "
          f"picks have odds ({odds_match_pct:.0f}%)")
    if espn_player_names:
        print(f"[INFO] Odds-ESPN player overlap: {espn_overlap_pct:.0f}%")

    # Warning thresholds
    if odds_match_pct < 30:
        print(f"[WARN] Very low odds match rate ({odds_match_pct:.0f}%). "
              f"Odds may be for a different tournament!")
        print(f"[WARN] Expected: {espn_tournament_name}")
        print(f"[WARN] Odds source: {result.tournament_name}")
    elif espn_player_names and espn_overlap_pct < 40:
        print(f"[WARN] Low ESPN-odds player overlap ({espn_overlap_pct:.0f}%). "
              f"Odds may be stale or for a different tournament!")
        print(f"[WARN] Expected: {espn_tournament_name}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("  GolfGame Analysis Pipeline")
    print("=" * 60)

    # Step 0: 前回大会の結果自動収集
    try:
        from src.result_collector import collect_all_pending, format_step0_summary

        step0_result = collect_all_pending()
        print(format_step0_summary(step0_result))

        if step0_result["collected"] > 0:
            from src.database import get_accumulation_status
            from src.result_collector import format_status_report
            status = get_accumulation_status()
            print(format_status_report(status))
    except Exception as e:
        print(f"[WARN] Step 0 result collection skipped: {e}")

    # Step 1: Download picks CSV
    print("\n[STEP 1] Downloading picks CSV...")
    from src.picks_downloader import run as download_picks

    csv_result = download_picks()
    if not csv_result.success:
        print(f"[ERROR] Picks download failed: {csv_result.message}")
        return 1
    print(f"[OK] CSV saved: {csv_result.filepath}")

    # Step 1.5: ESPN から大会情報を取得（大会名の正規ソース）
    tournament_start = ""
    tournament_end = ""
    espn_event_id = ""
    espn_tournament_name = ""
    espn_player_names: list[str] = []
    try:
        from src.espn_scraper import ESPNScraper
        espn = ESPNScraper()
        espn_data = espn.parse_tournament(espn.fetch_leaderboard())
        if espn_data:
            espn_tournament_name = espn_data.name
            espn_event_id = espn_data.event_id
            espn_player_names = [p.name for p in espn_data.players]
            if espn_data.start_date:
                tournament_start = espn_data.start_date[:10]
            if espn_data.end_date:
                tournament_end = espn_data.end_date[:10]
            print(f"[OK] ESPN tournament: {espn_tournament_name}")
            print(f"[OK] Tournament dates: {tournament_start} ~ {tournament_end}")
    except Exception as e:
        print(f"[WARN] ESPN fetch failed (non-critical): {e}")

    # Step 2: Run group analysis (fetches odds + generates text report)
    print("\n[STEP 2] Running group analysis...")
    from src.group_analyzer import run as run_analysis

    result = run_analysis(
        csv_path=str(csv_result.filepath),
        tournament_name=espn_tournament_name,
    )
    if not result:
        print("[ERROR] Group analysis failed")
        return 1
    print(f"[OK] Analyzed {len(result.groups)} groups")

    # Step 2.5: オッズの大会一致検証
    _verify_odds_tournament(result, espn_player_names, espn_tournament_name)

    # Step 3: Generate HTML report
    print("\n[STEP 3] Generating HTML report...")
    from src.html_report import save_html

    html_path = save_html(result)
    print(f"[OK] HTML report saved: {html_path}")

    # Step 4: Save to local database
    print("\n[STEP 4] Saving odds to database...")
    from src.database import save_tournament_odds

    try:
        tid = save_tournament_odds(
            analysis=result,
            picks_pk=csv_result.tournament_pk,
            espn_event_id=espn_event_id,
            start_date=tournament_start,
            end_date=tournament_end,
        )
        print(f"[OK] Saved odds to database (tournament id={tid})")
    except Exception as e:
        print(f"[WARN] Database save failed (non-critical): {e}")
        tid = None

    # Step 5: Fetch player statistics (PGA Tour GraphQL + local DB cache)
    print("\n[STEP 5] Fetching player statistics...")
    import yaml
    from pathlib import Path

    player_stats = None

    # Load config
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if config.get("stats_source", {}).get("enabled", False):
            from src.stats_scraper import create_stats_client

            # Get list of all players
            player_names = [p.name for group in result.groups.values() for p in group]

            try:
                # create_stats_client handles DB cache internally
                client = create_stats_client(config)
                player_stats = client.fetch_player_stats(player_names)

                with_data = sum(1 for s in player_stats if s.has_sufficient_data())
                print(f"[OK] Stats ready for {len(player_stats)} players ({with_data} with data)")

                # Also save to tournament-specific DB for backward compatibility
                if tid:
                    from src.database import save_tournament_stats
                    save_tournament_stats(tid, player_stats)
            except Exception as e:
                print(f"[WARN] Stats fetch failed (non-critical): {e}")
                import traceback
                traceback.print_exc()
                player_stats = None
        else:
            print("[INFO] Stats source disabled in config, skipping stats fetch")
    else:
        print("[WARN] Config file not found, skipping stats fetch")

    # Step 6: Re-run group analysis with stats if available
    if player_stats:
        print("\n[STEP 6] Re-running group analysis with stats-based predictions...")
        from src.group_analyzer import run as run_analysis

        result_with_stats = run_analysis(
            csv_path=str(csv_result.filepath),
            tournament_name=espn_tournament_name,
            stats=player_stats,
        )
        if result_with_stats:
            result = result_with_stats  # Update result with stats-enhanced version
            print(f"[OK] Stats-based predictions added")

            # Re-generate HTML report with stats
            print("\n[STEP 7] Re-generating HTML report with stats...")
            html_path = save_html(result)
            print(f"[OK] HTML report updated: {html_path}")
        else:
            print("[WARN] Failed to re-run analysis with stats, using odds-only version")

    # Step 8: Course Fit Analysis
    course_fit_result = None
    if config_path.exists():
        cf_config = config.get("course_fit", {}) if config else {}
        if cf_config.get("enabled", False) and player_stats:
            print("\n[STEP 8] Course Fit Analysis...")
            try:
                from src.course_fit import run_course_fit_analysis
                from src.pga_stats_db import PGAStatsDB

                tournament_num = cf_config.get("tournament_num", "")
                mode = cf_config.get("mode", "recent")
                n_years = cf_config.get("n_years", 3)

                # 大会番号が未指定の場合、大会名から自動検索
                if not tournament_num and result.tournament_name:
                    db = PGAStatsDB()
                    tournament_num = db.find_tournament_num_by_name(result.tournament_name)
                    if tournament_num:
                        print(f"[INFO] Auto-detected tournament number: {tournament_num}")
                    else:
                        print(f"[WARN] Could not detect tournament number from '{result.tournament_name}'")

                if tournament_num:
                    course_fit_result = run_course_fit_analysis(
                        groups=result.groups,
                        tournament_num=tournament_num,
                        mode=mode,
                        n_years=n_years,
                    )

                    if course_fit_result and course_fit_result.get("profile"):
                        # HTML再生成（コースフィット付き）
                        print("\n[STEP 8b] Re-generating HTML report with course fit...")
                        html_path = save_html(result, course_fit=course_fit_result)
                        print(f"[OK] HTML report updated: {html_path}")
                else:
                    print("[INFO] No tournament number available, skipping course fit")

            except Exception as e:
                print(f"[WARN] Course fit analysis failed (non-critical): {e}")
                import traceback
                traceback.print_exc()
        else:
            if not cf_config.get("enabled", False):
                print("\n[INFO] Course fit analysis disabled in config")

    # Step 9: ML Integrated Prediction
    ml_result = None
    if config_path.exists():
        ml_config = config.get("ml_prediction", {}) if config else {}
        if ml_config.get("enabled", False):
            print("\n[STEP 9] ML Integrated Prediction...")
            try:
                from src.ml_predictor import run_ml_prediction, format_ml_report, save_ml_report

                ml_result = run_ml_prediction(
                    groups=result.groups,
                    tournament_name=result.tournament_name,
                    course_fit=course_fit_result,
                    config=ml_config,
                )

                if ml_result and ml_result.get("predictions"):
                    # NOTE: GroupPlayerへのMLスコア付与は run_ml_prediction() 内で
                    # EGS最適化前に実施済み

                    # テキストレポート出力
                    report = format_ml_report(result.groups, ml_result)
                    print(report)

                    # テキストレポート保存
                    report_path = save_ml_report(result.groups, ml_result)
                    print(f"[OK] ML report saved: {report_path}")

                    # ML予測をDBに保存
                    if tid:
                        from src.database import save_ml_predictions
                        save_ml_predictions(tid, result.groups, ml_result)

                    # HTML再生成（ML + EGS v1/v2付き）
                    egs_result = ml_result.get("egs_result")
                    egs_v2_result = ml_result.get("egs_v2_result")
                    print("\n[STEP 9b] Re-generating HTML report with ML predictions...")
                    html_path = save_html(
                        result,
                        course_fit=course_fit_result,
                        ml_result=ml_result,
                        egs_result=egs_result,
                        egs_v2_result=egs_v2_result,
                    )
                    print(f"[OK] HTML report updated: {html_path}")
                    if egs_result:
                        print(f"[OK] Game Strategy tab included "
                              f"(agree={egs_result.agree_count}/{egs_result.total_groups})")
                    if egs_v2_result:
                        print(f"[OK] Pick Comparison tab included (v2 loaded)")

            except Exception as e:
                print(f"[WARN] ML prediction failed (non-critical): {e}")
                import traceback
                traceback.print_exc()
        else:
            print("\n[INFO] ML prediction disabled in config")

    # ポータルページ生成
    from src.portal import generate_portal
    generate_portal()

    print("\n" + "=" * 60)
    print("  Pipeline complete!")
    print(f"  Open {html_path} in a browser to view the report.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
