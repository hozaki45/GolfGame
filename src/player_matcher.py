"""Player Name Matcher.

Matches players between ESPN leaderboard data and Vegas Insider odds data.
Handles name variations and provides fuzzy matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fuzzywuzzy import fuzz
import yaml

from src.espn_scraper import PlayerLeaderboard
from src.odds_scraper import PlayerOdds


@dataclass
class MatchedPlayer:
    """A player with both leaderboard and odds data."""
    name: str
    # Leaderboard data
    position: int
    score: str
    round_scores: list[str]
    country: str
    # Odds data
    best_odds: int
    best_book: str
    decimal_odds: float
    implied_probability: float
    odds_by_book: dict[str, int]


class PlayerMatcher:
    """Matches player names between ESPN and odds data sources."""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        matching_cfg = config.get("matching", {})
        self.fuzzy_threshold = matching_cfg.get("fuzzy_threshold", 85)
        self.manual_overrides: dict[str, str] = matching_cfg.get("manual_overrides", {}) or {}

    def match_players(
        self,
        leaderboard: list[PlayerLeaderboard],
        odds: list[PlayerOdds],
    ) -> list[MatchedPlayer]:
        """Match ESPN leaderboard players with odds data.

        Args:
            leaderboard: Players from ESPN.
            odds: Players from odds scraper.

        Returns:
            List of matched players with both leaderboard and odds data.
        """
        matched = []
        unmatched_espn = []
        unmatched_odds_names = {self._normalize(p.name): p for p in odds}

        for lb_player in leaderboard:
            odds_player = self._find_match(lb_player.name, odds)
            if odds_player:
                matched.append(MatchedPlayer(
                    name=lb_player.name,
                    position=lb_player.position,
                    score=lb_player.score,
                    round_scores=lb_player.round_scores,
                    country=lb_player.country,
                    best_odds=odds_player.best_odds,
                    best_book=odds_player.best_book,
                    decimal_odds=odds_player.decimal_odds,
                    implied_probability=odds_player.implied_probability,
                    odds_by_book=odds_player.odds_by_book,
                ))
                # Remove from unmatched pool
                norm_name = self._normalize(odds_player.name)
                unmatched_odds_names.pop(norm_name, None)
            else:
                unmatched_espn.append(lb_player.name)

        if unmatched_espn:
            print(f"[WARN] {len(unmatched_espn)} ESPN players without odds: {unmatched_espn[:5]}...")
        if unmatched_odds_names:
            print(f"[WARN] {len(unmatched_odds_names)} odds players without leaderboard data")

        print(f"[INFO] Matched {len(matched)} players between ESPN and odds data")
        return matched

    def _find_match(self, espn_name: str, odds_list: list[PlayerOdds]) -> PlayerOdds | None:
        """Find the best matching odds player for an ESPN player name.

        Args:
            espn_name: Player name from ESPN.
            odds_list: List of odds players to search.

        Returns:
            Best matching PlayerOdds or None.
        """
        # Check manual overrides first
        for override_key, override_val in self.manual_overrides.items():
            if espn_name.lower() == override_val.lower():
                for p in odds_list:
                    if p.name.lower() == override_key.lower():
                        return p

        norm_espn = self._normalize(espn_name)

        # Try exact match first
        for p in odds_list:
            if self._normalize(p.name) == norm_espn:
                return p

        # Try fuzzy match
        best_score = 0
        best_match = None
        for p in odds_list:
            score = fuzz.ratio(norm_espn, self._normalize(p.name))
            # Also try token sort ratio for reordered names
            token_score = fuzz.token_sort_ratio(norm_espn, self._normalize(p.name))
            max_score = max(score, token_score)

            if max_score > best_score:
                best_score = max_score
                best_match = p

        if best_match and best_score >= self.fuzzy_threshold:
            return best_match

        return None

    def _normalize(self, name: str) -> str:
        """Normalize a player name for comparison.

        Args:
            name: Raw player name.

        Returns:
            Normalized lowercase name.
        """
        name = name.lower().strip()
        # Remove Jr., Sr., III, etc.
        name = re.sub(r"\s+(jr\.?|sr\.?|iii|ii|iv)$", "", name)
        # Remove extra whitespace
        name = re.sub(r"\s+", " ", name)
        return name


def run(leaderboard_data, odds_data) -> list[MatchedPlayer]:
    """Pipeline entry point for player matching.

    Args:
        leaderboard_data: TournamentInfo from ESPN.
        odds_data: TournamentOdds from odds scraper.

    Returns:
        List of MatchedPlayer.
    """
    matcher = PlayerMatcher()
    return matcher.match_players(leaderboard_data.players, odds_data.players)
