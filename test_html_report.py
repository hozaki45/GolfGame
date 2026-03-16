"""Test script for HTML report generation with stats."""

from __future__ import annotations

import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

from src.group_analyzer import GroupAnalysisResult, GroupPlayer
from src.html_report import save_html
from src.stats_models import PlayerStats


def main():
    print("=" * 60)
    print("  HTML Report Test (with Stats)")
    print("=" * 60)

    # Create mock stats
    scheffler_stats = PlayerStats(
        name="Scottie Scheffler",
        sg_approach=2.1,
        sg_off_tee=1.8,
        sg_tee_to_green=2.5,
        greens_in_regulation_pct=72.5,
        scoring_average=69.2,
        scrambling_pct=62.0,
        prediction_score=93.8,
        confidence="High",
    )

    mcilroy_stats = PlayerStats(
        name="Rory McIlroy",
        sg_approach=1.9,
        sg_off_tee=2.0,
        sg_tee_to_green=2.3,
        greens_in_regulation_pct=70.0,
        scoring_average=69.5,
        scrambling_pct=60.0,
        prediction_score=72.8,
        confidence="High",
    )

    koepka_stats = PlayerStats(
        name="Brooks Koepka",
        sg_approach=1.5,
        sg_off_tee=1.2,
        sg_tee_to_green=1.8,
        greens_in_regulation_pct=68.0,
        scoring_average=70.2,
        scrambling_pct=58.0,
        prediction_score=45.0,
        confidence="High",
    )

    # Create mock GroupPlayers
    group1 = [
        GroupPlayer(
            name="Scottie Scheffler",
            group_id=1,
            wgr="1",
            fedex_rank="2",
            best_odds=-200,
            best_book="DraftKings",
            implied_prob=0.667,
            odds_display="-200",
            odds_by_book={"DraftKings": -200, "FanDuel": -190, "Bet365": -210},
            stats=scheffler_stats,
            stats_prediction_score=93.8,
            stats_rank_in_group=1,
            odds_vs_stats_agreement="Strong Match",
        ),
        GroupPlayer(
            name="Rory McIlroy",
            group_id=1,
            wgr="2",
            fedex_rank="5",
            best_odds=-150,
            best_book="FanDuel",
            implied_prob=0.600,
            odds_display="-150",
            odds_by_book={"DraftKings": -160, "FanDuel": -150, "Bet365": -155},
            stats=mcilroy_stats,
            stats_prediction_score=72.8,
            stats_rank_in_group=2,
            odds_vs_stats_agreement="Partial Match",
        ),
        GroupPlayer(
            name="Brooks Koepka",
            group_id=1,
            wgr="15",
            fedex_rank="12",
            best_odds=+120,
            best_book="Bet365",
            implied_prob=0.455,
            odds_display="+120",
            odds_by_book={"DraftKings": +130, "FanDuel": +125, "Bet365": +120},
            stats=koepka_stats,
            stats_prediction_score=45.0,
            stats_rank_in_group=3,
            odds_vs_stats_agreement="Strong Match",
        ),
    ]

    # Create result
    result = GroupAnalysisResult(
        groups={1: group1},
        bookmakers=["DraftKings", "FanDuel", "Bet365"],
        tournament_name="Test Tournament (2025)",
        generated_at=datetime.now().isoformat(),
    )

    print("\n[INFO] Generating HTML report with mock data...")
    html_path = save_html(result, "data/output/test_index.html")

    print(f"\n[OK] HTML report generated: {html_path}")
    print("\nExpected tabs:")
    print("  1. Recommended Picks")
    print("  2. Group Overview")
    print("  3. Bookmaker Odds")
    print("  4. Rankings")
    print("  5. By Bookmaker")
    print("  6. Stats Rankings ← NEW")
    print("  7. Odds vs Stats ← NEW")
    print("  8. Player Details ← NEW")

    print(f"\nOpen {html_path} in a browser to verify all 8 tabs work correctly.")


if __name__ == "__main__":
    main()
