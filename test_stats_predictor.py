"""Test script for stats-based prediction engine."""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.stats_models import PlayerStats
from src.stats_analyzer import create_predictor


def main():
    print("=" * 60)
    print("  Stats Predictor Test")
    print("=" * 60)

    # Create mock config
    config = {
        "stats_source": {
            "scraping": {
                "stats_to_fetch": [
                    {"name": "sg_approach", "weight": 0.30},
                    {"name": "sg_off_tee", "weight": 0.25},
                    {"name": "sg_tee_to_green", "weight": 0.20},
                    {"name": "gir_pct", "weight": 0.10},
                    {"name": "scoring_average", "weight": 0.08},
                    {"name": "scrambling_pct", "weight": 0.07},
                ]
            }
        }
    }

    # Create mock player stats (simulating 3 players in a group)
    players = [
        PlayerStats(
            name="Scottie Scheffler",
            sg_approach=2.1,
            sg_off_tee=1.8,
            sg_tee_to_green=2.5,
            greens_in_regulation_pct=72.5,
            scoring_average=69.2,
            scrambling_pct=62.0,
        ),
        PlayerStats(
            name="Rory McIlroy",
            sg_approach=1.9,
            sg_off_tee=2.0,
            sg_tee_to_green=2.3,
            greens_in_regulation_pct=70.0,
            scoring_average=69.5,
            scrambling_pct=60.0,
        ),
        PlayerStats(
            name="Brooks Koepka",
            sg_approach=1.5,
            sg_off_tee=1.2,
            sg_tee_to_green=1.8,
            greens_in_regulation_pct=68.0,
            scoring_average=70.2,
            scrambling_pct=58.0,
        ),
    ]

    print(f"\nTesting predictor with {len(players)} mock players...")

    # Create predictor
    predictor = create_predictor(config)
    print(f"[OK] Predictor created with weights: {predictor.weights}")

    # Add predictions
    players_with_predictions = predictor.add_predictions_to_stats(players)

    # Display results
    print("\n" + "=" * 60)
    print("  PREDICTION RESULTS")
    print("=" * 60)

    for i, player in enumerate(
        sorted(players_with_predictions, key=lambda p: p.prediction_score or 0, reverse=True),
        1,
    ):
        print(f"\n#{i} {player.name}")
        print(f"  Prediction Score: {player.prediction_score:.1f}/100")
        print(f"  Confidence: {player.confidence}")
        print(f"  Key Stats:")
        print(f"    SG Approach: {player.sg_approach}")
        print(f"    SG Off-the-Tee: {player.sg_off_tee}")
        print(f"    SG Tee-to-Green: {player.sg_tee_to_green}")
        print(f"    GIR%: {player.greens_in_regulation_pct}%")
        print(f"    Scoring Avg: {player.scoring_average}")

    print("\n" + "=" * 60)
    print("  TEST COMPLETE")
    print("=" * 60)
    print("\nExpected behavior:")
    print("  - Scottie Scheffler should have the highest score (best stats)")
    print("  - All players should have 'High' confidence (5+ stats available)")
    print("  - Scores should be normalized to 0-100 scale")


if __name__ == "__main__":
    main()
