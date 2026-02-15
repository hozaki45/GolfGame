"""Value Analysis Engine.

Compares betting odds with tournament performance to find value bets.
Uses position-based probability model and Kelly criterion for sizing.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tabulate import tabulate
import yaml

from src.player_matcher import MatchedPlayer
from src.odds_scraper import american_to_implied_prob


@dataclass
class ValueBet:
    """A value bet recommendation."""
    name: str
    position: int
    score: str
    best_odds: int
    best_book: str
    implied_prob: float      # Bookmaker's implied probability
    model_prob: float        # Our model's probability
    edge: float              # model_prob - implied_prob
    kelly_fraction: float    # Recommended bet size as fraction of bankroll
    expected_value: float    # EV per unit bet


class ValueAnalyzer:
    """Analyzes matched player data to find value bets."""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        analysis_cfg = config.get("analysis", {})
        self.min_edge_pct = analysis_cfg.get("min_edge_percent", 2.0)
        self.kelly_fraction = analysis_cfg.get("kelly_fraction", 0.25)
        self.bankroll = analysis_cfg.get("bankroll", 1000.0)

    def estimate_model_probability(
        self,
        players: list[MatchedPlayer],
    ) -> dict[str, float]:
        """Estimate win probability based on tournament position and score.

        Uses a position-based model where probability decreases with rank.
        Also factors in score relative to leader.

        Args:
            players: List of matched players.

        Returns:
            Dict mapping player name to model probability.
        """
        if not players:
            return {}

        # Sort by position
        sorted_players = sorted(players, key=lambda p: p.position)
        n = len(sorted_players)

        # Parse leader score for relative calculations
        leader_score = self._parse_score(sorted_players[0].score)

        probabilities: dict[str, float] = {}
        raw_scores: dict[str, float] = {}

        for p in sorted_players:
            player_score = self._parse_score(p.score)
            strokes_back = player_score - leader_score if leader_score is not None and player_score is not None else p.position

            # Position-based component (exponential decay)
            pos_score = math.exp(-0.15 * (p.position - 1))

            # Strokes-back component (stronger decay for more strokes behind)
            if isinstance(strokes_back, (int, float)):
                stroke_score = math.exp(-0.3 * abs(strokes_back))
            else:
                stroke_score = pos_score

            # Combined raw score
            raw_scores[p.name] = pos_score * 0.5 + stroke_score * 0.5

        # Normalize to probabilities (sum to 1.0)
        total = sum(raw_scores.values())
        if total > 0:
            for name, score in raw_scores.items():
                probabilities[name] = score / total

        return probabilities

    def find_value_bets(self, players: list[MatchedPlayer]) -> list[ValueBet]:
        """Find value bets where model probability exceeds implied probability.

        Args:
            players: List of matched players.

        Returns:
            List of ValueBet recommendations, sorted by edge.
        """
        model_probs = self.estimate_model_probability(players)
        value_bets = []

        for p in players:
            model_prob = model_probs.get(p.name, 0.0)
            implied_prob = p.implied_probability
            edge = model_prob - implied_prob

            # Calculate Kelly criterion fraction
            if p.decimal_odds > 1.0 and model_prob > 0:
                b = p.decimal_odds - 1.0  # Net odds (profit per unit)
                q = 1.0 - model_prob
                kelly = (model_prob * b - q) / b
                kelly = max(0.0, kelly) * self.kelly_fraction  # Fractional Kelly
            else:
                kelly = 0.0

            # Expected value per unit bet
            if p.decimal_odds > 1.0:
                ev = model_prob * (p.decimal_odds - 1.0) - (1.0 - model_prob)
            else:
                ev = 0.0

            value_bets.append(ValueBet(
                name=p.name,
                position=p.position,
                score=p.score,
                best_odds=p.best_odds,
                best_book=p.best_book,
                implied_prob=implied_prob,
                model_prob=model_prob,
                edge=edge,
                kelly_fraction=kelly,
                expected_value=ev,
            ))

        # Sort by edge (highest first)
        value_bets.sort(key=lambda x: x.edge, reverse=True)
        return value_bets

    def generate_report(self, value_bets: list[ValueBet]) -> str:
        """Generate a formatted analysis report.

        Args:
            value_bets: List of value bets.

        Returns:
            Formatted report string.
        """
        # Filter to bets with positive edge
        positive_edge = [vb for vb in value_bets if vb.edge > 0]
        min_edge = self.min_edge_pct / 100.0

        lines = []
        lines.append("=" * 85)
        lines.append("  GOLF BETTING VALUE ANALYSIS")
        lines.append("=" * 85)
        lines.append("")

        # Value bets table
        lines.append(f"  VALUE BETS (edge > {self.min_edge_pct}%)")
        lines.append("-" * 85)

        table_data = []
        for vb in positive_edge:
            if vb.edge >= min_edge:
                kelly_bet = vb.kelly_fraction * self.bankroll
                table_data.append([
                    vb.name,
                    vb.position,
                    vb.score,
                    f"+{vb.best_odds}" if vb.best_odds > 0 else str(vb.best_odds),
                    vb.best_book,
                    f"{vb.implied_prob:.1%}",
                    f"{vb.model_prob:.1%}",
                    f"{vb.edge:+.1%}",
                    f"${kelly_bet:.0f}" if kelly_bet > 0 else "-",
                ])

        if table_data:
            headers = ["Player", "Pos", "Score", "Odds", "Book", "Impl%", "Model%", "Edge", "Kelly$"]
            lines.append(tabulate(table_data, headers=headers, tablefmt="simple"))
        else:
            lines.append("  No value bets found above minimum edge threshold.")

        lines.append("")
        lines.append("-" * 85)

        # Full leaderboard with odds
        lines.append(f"\n  FULL LEADERBOARD (top 30)")
        lines.append("-" * 85)

        full_data = []
        for vb in value_bets[:30]:
            full_data.append([
                vb.name,
                vb.position,
                vb.score,
                f"+{vb.best_odds}" if vb.best_odds > 0 else str(vb.best_odds),
                vb.best_book,
                f"{vb.implied_prob:.1%}",
                f"{vb.model_prob:.1%}",
                f"{vb.edge:+.1%}",
            ])

        headers = ["Player", "Pos", "Score", "Odds", "Book", "Impl%", "Model%", "Edge"]
        lines.append(tabulate(full_data, headers=headers, tablefmt="simple"))

        # Summary stats
        lines.append("")
        lines.append(f"  Total players analyzed: {len(value_bets)}")
        lines.append(f"  Value bets found: {len([vb for vb in positive_edge if vb.edge >= min_edge])}")
        lines.append(f"  Bankroll: ${self.bankroll:.0f}")
        lines.append(f"  Kelly fraction: {self.kelly_fraction:.0%}")

        return "\n".join(lines)

    def save_report(self, value_bets: list[ValueBet], data_dir: str = "data") -> Path:
        """Save analysis results to CSV.

        Args:
            value_bets: List of value bets.
            data_dir: Base data directory.

        Returns:
            Path to saved file.
        """
        output_dir = Path(data_dir) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = output_dir / f"analysis_{date_str}.csv"

        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Player", "Position", "Score", "Best_Odds", "Best_Book",
                "Implied_Prob", "Model_Prob", "Edge", "Kelly_Fraction",
                "Kelly_Bet", "Expected_Value",
            ])
            for vb in value_bets:
                writer.writerow([
                    vb.name,
                    vb.position,
                    vb.score,
                    vb.best_odds,
                    vb.best_book,
                    f"{vb.implied_prob:.4f}",
                    f"{vb.model_prob:.4f}",
                    f"{vb.edge:.4f}",
                    f"{vb.kelly_fraction:.4f}",
                    f"{vb.kelly_fraction * self.bankroll:.2f}",
                    f"{vb.expected_value:.4f}",
                ])

        print(f"[INFO] Saved analysis to {filepath}")
        return filepath

    def _parse_score(self, score_str: str) -> float | None:
        """Parse a golf score string to numeric value.

        Args:
            score_str: Score like "-19", "E", "+3", "N/A".

        Returns:
            Numeric score relative to par, or None.
        """
        if not score_str or score_str in ("N/A", "--", ""):
            return None
        if score_str.upper() == "E":
            return 0.0
        try:
            return float(score_str)
        except ValueError:
            return None


def run(matched_players: list[MatchedPlayer]) -> list[ValueBet]:
    """Pipeline entry point for value analysis.

    Args:
        matched_players: List of matched players.

    Returns:
        List of value bets.
    """
    analyzer = ValueAnalyzer()
    value_bets = analyzer.find_value_bets(matched_players)

    report = analyzer.generate_report(value_bets)
    print(report)

    analyzer.save_report(value_bets)
    return value_bets
