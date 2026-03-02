"""Stats-Based Prediction Engine - Calculate prediction scores from player statistics.

Uses weighted composite scoring based on golf statistics research:
- Strokes Gained metrics (SG Approach, SG Off-the-Tee, etc.)
- Traditional stats (GIR%, Scrambling%, Scoring Average)
- Normalizes to 0-100 scale for comparability
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.stats_models import PlayerStats


def normalize_stat(
    value: float,
    min_val: float,
    max_val: float,
    invert: bool = False,
) -> float:
    """Normalize stat to 0-1 scale.

    Args:
        value: The stat value to normalize.
        min_val: Minimum value in dataset.
        max_val: Maximum value in dataset.
        invert: If True, higher values are worse (e.g., scoring average).

    Returns:
        Normalized value (0-1).
    """
    if max_val == min_val:
        return 0.5  # No variation, return middle value

    normalized = (value - min_val) / (max_val - min_val)

    if invert:
        # For stats where lower is better (e.g., scoring average)
        return 1.0 - normalized

    return normalized


def calculate_confidence(stats: PlayerStats) -> str:
    """Determine confidence level based on available stats.

    Args:
        stats: PlayerStats object.

    Returns:
        Confidence level: "High", "Medium", or "Low".
    """
    # Count how many key stats are available
    key_stats = [
        stats.sg_approach,
        stats.sg_off_tee,
        stats.sg_tee_to_green,
        stats.greens_in_regulation_pct,
        stats.scoring_average,
    ]
    available = sum(1 for s in key_stats if s is not None)

    if available >= 4:
        return "High"
    elif available >= 2:
        return "Medium"
    else:
        return "Low"


@dataclass
class DatasetStats:
    """Statistical boundaries for normalization."""

    sg_approach_min: float = 0.0
    sg_approach_max: float = 0.0
    sg_off_tee_min: float = 0.0
    sg_off_tee_max: float = 0.0
    sg_tee_to_green_min: float = 0.0
    sg_tee_to_green_max: float = 0.0
    gir_pct_min: float = 0.0
    gir_pct_max: float = 0.0
    scoring_avg_min: float = 0.0
    scoring_avg_max: float = 0.0
    scrambling_pct_min: float = 0.0
    scrambling_pct_max: float = 0.0


class StatsPredictor:
    """Prediction engine for stats-based analysis."""

    def __init__(self, config: dict):
        """Initialize predictor with config.

        Args:
            config: Configuration dict from config.yaml.
        """
        self.config = config
        stats_config = config.get("stats_source", {})
        scraping_config = stats_config.get("scraping", {})

        # Load weights from config
        stats_to_fetch = scraping_config.get("stats_to_fetch", [])
        self.weights = {}
        for stat_def in stats_to_fetch:
            stat_name = stat_def.get("name", "")
            weight = stat_def.get("weight", 0.0)
            self.weights[stat_name] = weight

        # Default weights if not in config
        if not self.weights:
            self.weights = {
                "sg_approach": 0.30,
                "sg_off_tee": 0.25,
                "sg_tee_to_green": 0.20,
                "gir_pct": 0.10,
                "scoring_average": 0.08,
                "scrambling_pct": 0.07,
            }

    def calculate_dataset_stats(self, all_stats: list[PlayerStats]) -> DatasetStats:
        """Calculate min/max values across all players for normalization.

        Args:
            all_stats: List of PlayerStats for all players.

        Returns:
            DatasetStats with min/max values.
        """
        ds = DatasetStats()

        # SG Approach
        sg_approach_values = [s.sg_approach for s in all_stats if s.sg_approach is not None]
        if sg_approach_values:
            ds.sg_approach_min = min(sg_approach_values)
            ds.sg_approach_max = max(sg_approach_values)

        # SG Off-the-Tee
        sg_off_tee_values = [s.sg_off_tee for s in all_stats if s.sg_off_tee is not None]
        if sg_off_tee_values:
            ds.sg_off_tee_min = min(sg_off_tee_values)
            ds.sg_off_tee_max = max(sg_off_tee_values)

        # SG Tee-to-Green
        sg_ttg_values = [s.sg_tee_to_green for s in all_stats if s.sg_tee_to_green is not None]
        if sg_ttg_values:
            ds.sg_tee_to_green_min = min(sg_ttg_values)
            ds.sg_tee_to_green_max = max(sg_ttg_values)

        # GIR %
        gir_values = [s.greens_in_regulation_pct for s in all_stats if s.greens_in_regulation_pct is not None]
        if gir_values:
            ds.gir_pct_min = min(gir_values)
            ds.gir_pct_max = max(gir_values)

        # Scoring Average (lower is better)
        scoring_values = [s.scoring_average for s in all_stats if s.scoring_average is not None]
        if scoring_values:
            ds.scoring_avg_min = min(scoring_values)
            ds.scoring_avg_max = max(scoring_values)

        # Scrambling %
        scrambling_values = [s.scrambling_pct for s in all_stats if s.scrambling_pct is not None]
        if scrambling_values:
            ds.scrambling_pct_min = min(scrambling_values)
            ds.scrambling_pct_max = max(scrambling_values)

        return ds

    def calculate_prediction_score(
        self,
        stats: PlayerStats,
        dataset_stats: DatasetStats,
    ) -> float:
        """Calculate weighted composite prediction score (0-100).

        Args:
            stats: PlayerStats for the player.
            dataset_stats: Min/max values for normalization.

        Returns:
            Prediction score (0-100).
        """
        score = 0.0
        total_weight = 0.0

        # SG Approach (30%)
        if stats.sg_approach is not None and "sg_approach" in self.weights:
            weight = self.weights["sg_approach"]
            normalized = normalize_stat(
                stats.sg_approach,
                dataset_stats.sg_approach_min,
                dataset_stats.sg_approach_max,
            )
            score += weight * normalized
            total_weight += weight

        # SG Off-the-Tee (25%)
        if stats.sg_off_tee is not None and "sg_off_tee" in self.weights:
            weight = self.weights["sg_off_tee"]
            normalized = normalize_stat(
                stats.sg_off_tee,
                dataset_stats.sg_off_tee_min,
                dataset_stats.sg_off_tee_max,
            )
            score += weight * normalized
            total_weight += weight

        # SG Tee-to-Green (20%)
        if stats.sg_tee_to_green is not None and "sg_tee_to_green" in self.weights:
            weight = self.weights["sg_tee_to_green"]
            normalized = normalize_stat(
                stats.sg_tee_to_green,
                dataset_stats.sg_tee_to_green_min,
                dataset_stats.sg_tee_to_green_max,
            )
            score += weight * normalized
            total_weight += weight

        # GIR % (10%)
        if stats.greens_in_regulation_pct is not None and "gir_pct" in self.weights:
            weight = self.weights["gir_pct"]
            normalized = normalize_stat(
                stats.greens_in_regulation_pct,
                dataset_stats.gir_pct_min,
                dataset_stats.gir_pct_max,
            )
            score += weight * normalized
            total_weight += weight

        # Scoring Average (8%, lower is better - invert)
        if stats.scoring_average is not None and "scoring_average" in self.weights:
            weight = self.weights["scoring_average"]
            normalized = normalize_stat(
                stats.scoring_average,
                dataset_stats.scoring_avg_min,
                dataset_stats.scoring_avg_max,
                invert=True,  # Lower scoring average is better
            )
            score += weight * normalized
            total_weight += weight

        # Scrambling % (7%)
        if stats.scrambling_pct is not None and "scrambling_pct" in self.weights:
            weight = self.weights["scrambling_pct"]
            normalized = normalize_stat(
                stats.scrambling_pct,
                dataset_stats.scrambling_pct_min,
                dataset_stats.scrambling_pct_max,
            )
            score += weight * normalized
            total_weight += weight

        # Normalize by total weight used (handles missing stats gracefully)
        if total_weight > 0:
            score = (score / total_weight) * 100
        else:
            score = 0.0

        return score

    def add_predictions_to_stats(
        self,
        all_stats: list[PlayerStats],
    ) -> list[PlayerStats]:
        """Add prediction scores and confidence to all PlayerStats.

        Args:
            all_stats: List of PlayerStats for all players.

        Returns:
            Updated list with prediction_score and confidence filled.
        """
        # Calculate dataset statistics for normalization
        dataset_stats = self.calculate_dataset_stats(all_stats)

        # Calculate prediction score for each player
        for stats in all_stats:
            stats.prediction_score = self.calculate_prediction_score(stats, dataset_stats)
            stats.confidence = calculate_confidence(stats)

        return all_stats


def create_predictor(config: dict) -> StatsPredictor:
    """Factory function to create StatsPredictor.

    Args:
        config: Configuration dict from config.yaml.

    Returns:
        StatsPredictor instance.
    """
    return StatsPredictor(config)
