"""GolfGame - PGA Tour Betting Analysis Pipeline.

Fetches PGA Tour leaderboard data and betting odds,
then analyzes value betting opportunities.
"""

from __future__ import annotations

import sys

from src import espn_scraper, odds_scraper, player_matcher, value_analyzer


def run_pipeline():
    """Run the full analysis pipeline."""
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 70)
    print("  GolfGame - PGA Tour Betting Analysis")
    print("=" * 70)
    print()

    # Step 1: Fetch ESPN leaderboard
    print("[INFO] ▶ Step 1: Fetching ESPN leaderboard...")
    tournament = espn_scraper.run()
    if not tournament:
        print("[ERROR] ✘ No active tournament found on ESPN")
        return
    print(f"[INFO] ✔ Step 1 complete: {tournament.name} ({len(tournament.players)} players)")
    print()

    # Step 2: Fetch betting odds
    print("[INFO] ▶ Step 2: Fetching betting odds...")
    odds = odds_scraper.run(tournament_name=tournament.name)
    if not odds:
        print("[ERROR] ✘ Failed to fetch odds data")
        return
    print(f"[INFO] ✔ Step 2 complete: {len(odds.players)} players with odds")
    print()

    # Step 3: Match players
    print("[INFO] ▶ Step 3: Matching players...")
    matched = player_matcher.run(tournament, odds)
    if not matched:
        print("[ERROR] ✘ No players matched between sources")
        return
    print(f"[INFO] ✔ Step 3 complete: {len(matched)} players matched")
    print()

    # Step 4: Value analysis
    print("[INFO] ▶ Step 4: Running value analysis...")
    value_bets = value_analyzer.run(matched)
    print(f"[INFO] ✔ Step 4 complete: {len(value_bets)} players analyzed")
    print()

    print("=" * 70)
    print("  Pipeline complete!")
    print("=" * 70)


if __name__ == "__main__":
    run_pipeline()
