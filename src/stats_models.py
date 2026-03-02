"""Data models for PGA Tour player statistics and predictions.

This module defines the core data structures for storing and managing
player performance statistics from the PGA Tour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class PlayerStats:
    """PGA Tour player statistics for tournament prediction.

    This class stores comprehensive player performance metrics including
    Strokes Gained statistics, traditional stats, and prediction scores.
    """

    name: str
    tournament_id: Optional[int] = None

    # === Strokes Gained Metrics (Most Predictive) ===
    sg_approach: Optional[float] = None      # Strokes Gained: Approach the Green
    sg_off_tee: Optional[float] = None       # Strokes Gained: Off-the-Tee
    sg_tee_to_green: Optional[float] = None  # Strokes Gained: Tee-to-Green
    sg_total: Optional[float] = None         # Strokes Gained: Total
    sg_putting: Optional[float] = None       # Strokes Gained: Putting
    sg_around_green: Optional[float] = None  # Strokes Gained: Around-the-Green

    # === Traditional Statistics ===
    greens_in_regulation_pct: Optional[float] = None  # GIR percentage
    driving_distance: Optional[float] = None          # Average driving distance (yards)
    driving_accuracy_pct: Optional[float] = None      # Fairways hit percentage
    scrambling_pct: Optional[float] = None            # Scrambling percentage
    scoring_average: Optional[float] = None           # Adjusted scoring average

    # === Form & Performance ===
    recent_form_rank: Optional[int] = None   # Rank based on last 3 tournaments

    # === Prediction Output ===
    prediction_score: Optional[float] = None  # Composite score (0-100, higher=better)
    confidence: Optional[str] = None          # "High", "Medium", "Low"

    # === Metadata ===
    data_source: str = "pgatour_scraping"     # Source of the data
    fetched_at: str = ""                      # ISO timestamp of data fetch

    def __post_init__(self) -> None:
        """Set fetched_at to current time if not provided."""
        if not self.fetched_at:
            self.fetched_at = datetime.utcnow().isoformat()

    def has_sufficient_data(self, min_required: int = 3) -> bool:
        """Check if player has enough data for prediction.

        Args:
            min_required: Minimum number of non-None stats required

        Returns:
            True if player has sufficient data
        """
        core_stats = [
            self.sg_approach,
            self.sg_off_tee,
            self.sg_tee_to_green,
            self.greens_in_regulation_pct,
            self.scoring_average
        ]
        available = sum(1 for stat in core_stats if stat is not None)
        return available >= min_required

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "tournament_id": self.tournament_id,
            "sg_approach": self.sg_approach,
            "sg_off_tee": self.sg_off_tee,
            "sg_tee_to_green": self.sg_tee_to_green,
            "sg_total": self.sg_total,
            "sg_putting": self.sg_putting,
            "sg_around_green": self.sg_around_green,
            "gir_pct": self.greens_in_regulation_pct,
            "driving_distance": self.driving_distance,
            "driving_accuracy_pct": self.driving_accuracy_pct,
            "scrambling_pct": self.scrambling_pct,
            "scoring_average": self.scoring_average,
            "recent_form_rank": self.recent_form_rank,
            "prediction_score": self.prediction_score,
            "confidence": self.confidence,
            "data_source": self.data_source,
            "fetched_at": self.fetched_at
        }


@dataclass
class TournamentStats:
    """Collection of player statistics for a tournament.

    This class aggregates all player stats for a specific tournament,
    enabling batch processing and analysis.
    """

    tournament_name: str
    players: list[PlayerStats]
    fetched_at: str
    source: str = "pgatour_scraping"

    def __post_init__(self) -> None:
        """Set fetched_at to current time if not provided."""
        if not self.fetched_at:
            self.fetched_at = datetime.utcnow().isoformat()

    @property
    def player_count(self) -> int:
        """Number of players with stats."""
        return len(self.players)

    @property
    def players_with_sufficient_data(self) -> int:
        """Count of players with enough data for prediction."""
        return sum(1 for p in self.players if p.has_sufficient_data())

    def get_player(self, name: str) -> Optional[PlayerStats]:
        """Get stats for a specific player by name.

        Args:
            name: Player name (case-insensitive)

        Returns:
            PlayerStats if found, None otherwise
        """
        name_lower = name.lower()
        for player in self.players:
            if player.name.lower() == name_lower:
                return player
        return None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "tournament_name": self.tournament_name,
            "player_count": self.player_count,
            "players_with_sufficient_data": self.players_with_sufficient_data,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "players": [p.to_dict() for p in self.players]
        }
