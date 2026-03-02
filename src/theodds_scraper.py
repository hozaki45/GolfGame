"""The Odds API Scraper - Golf Outright Odds.

Fetches PGA Tour outright winner odds from The Odds API.
Supplements Vegas Insider data with additional bookmakers.
Free tier: 500 requests/month. Sign up at https://the-odds-api.com
"""

from __future__ import annotations

import sys
from datetime import datetime

import requests
import yaml

from src.odds_scraper import (
    PlayerOdds,
    TournamentOdds,
    american_to_decimal,
    american_to_implied_prob,
)


class TheOddsScraper:
    """Fetches golf odds from The Odds API."""

    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        api_cfg = config.get("theodds_api", {})
        self.api_key = api_cfg.get("api_key", "")
        self.regions = api_cfg.get("regions", "us")
        self.odds_format = api_cfg.get("odds_format", "american")
        self.enabled = api_cfg.get("enabled", False)
        self.timeout = 30

    def list_golf_sports(self) -> list[dict]:
        """List currently active golf events.

        This endpoint is free and does not consume API credits.

        Returns:
            List of golf sport dicts with 'key', 'title', 'active' fields.
        """
        if not self.api_key:
            print("[WARN] The Odds API key not configured")
            return []

        url = f"{self.BASE_URL}/sports/"
        params = {"apiKey": self.api_key}

        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            sports = resp.json()

            golf_sports = [
                s for s in sports
                if s.get("group", "").lower() == "golf" and s.get("active", False)
            ]

            if golf_sports:
                print(f"[INFO] Found {len(golf_sports)} active golf events:")
                for gs in golf_sports:
                    print(f"  - {gs['title']} ({gs['key']})")
            else:
                print("[INFO] No active golf events found on The Odds API")

            return golf_sports

        except requests.RequestException as e:
            print(f"[ERROR] Failed to list sports: {e}")
            return []

    def fetch_odds(self, sport_key: str) -> TournamentOdds | None:
        """Fetch outright odds for a specific golf event.

        Consumes 1 API credit per region.

        Args:
            sport_key: The sport key (e.g. 'golf_pga_championship_winner').

        Returns:
            TournamentOdds or None.
        """
        if not self.api_key:
            print("[WARN] The Odds API key not configured")
            return None

        url = f"{self.BASE_URL}/sports/{sport_key}/odds/"
        params = {
            "apiKey": self.api_key,
            "regions": self.regions,
            "markets": "outrights",
            "oddsFormat": self.odds_format,
        }

        try:
            print(f"[INFO] Fetching odds from The Odds API for {sport_key}...")
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()

            # Log remaining credits
            remaining = resp.headers.get("x-requests-remaining", "?")
            used = resp.headers.get("x-requests-used", "?")
            print(f"[INFO] API credits: {remaining} remaining, {used} used")

            data = resp.json()
            return self._parse_response(data, sport_key)

        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch odds from The Odds API: {e}")
            return None

    def _parse_response(self, data: list[dict], sport_key: str) -> TournamentOdds | None:
        """Parse The Odds API response into TournamentOdds.

        Args:
            data: List of event dicts from the API.
            sport_key: Sport key used for the request.

        Returns:
            TournamentOdds or None.
        """
        if not data:
            print("[WARN] No events returned from The Odds API")
            return None

        # Use the first event (there's typically one per sport key)
        event = data[0]
        tournament_name = event.get("sport_title", sport_key)
        bookmaker_list = event.get("bookmakers", [])

        if not bookmaker_list:
            print("[WARN] No bookmakers returned for this event")
            return None

        # Collect all bookmaker names
        bookmaker_names = [bm.get("title", bm.get("key", "")) for bm in bookmaker_list]
        print(f"[INFO] The Odds API bookmakers: {bookmaker_names}")

        # Build player odds: aggregate across bookmakers
        player_odds_map: dict[str, dict[str, int]] = {}

        for bm in bookmaker_list:
            bm_name = bm.get("title", bm.get("key", ""))
            markets = bm.get("markets", [])

            for market in markets:
                if market.get("key") != "outrights":
                    continue

                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("name", "")
                    price = outcome.get("price")

                    if not player_name or price is None:
                        continue

                    # price is already in American format if oddsFormat=american
                    odds_val = int(price)
                    player_odds_map.setdefault(player_name, {})[bm_name] = odds_val

        # Convert to PlayerOdds list
        players = []
        for name, odds_by_book in player_odds_map.items():
            # Find best odds (lowest = favorite)
            # But for "best odds" display, we want the odds as-is
            # Best book = the one with lowest odds (most favorable for the player to win)
            best_book = min(odds_by_book, key=lambda k: odds_by_book[k])
            best_odds = odds_by_book[best_book]
            decimal = american_to_decimal(best_odds)
            implied_prob = american_to_implied_prob(best_odds)

            players.append(PlayerOdds(
                name=name,
                odds_by_book=odds_by_book,
                best_odds=best_odds,
                best_book=best_book,
                decimal_odds=decimal,
                implied_probability=implied_prob,
            ))

        print(f"[INFO] Parsed {len(players)} players from The Odds API")

        return TournamentOdds(
            tournament_name=tournament_name,
            source="The Odds API",
            bookmakers=bookmaker_names,
            players=players,
            fetched_at=datetime.now().isoformat(),
        )

    def run(self, tournament_name: str = "") -> TournamentOdds | None:
        """Find and fetch golf odds for the current active event.

        Args:
            tournament_name: Optional tournament name hint for matching.

        Returns:
            TournamentOdds or None.
        """
        if not self.enabled:
            print("[INFO] The Odds API is disabled in config")
            return None

        if not self.api_key:
            print("[WARN] The Odds API key not set - skipping")
            return None

        # Find active golf events
        golf_sports = self.list_golf_sports()
        if not golf_sports:
            return None

        # Try to match tournament name to an active event
        if tournament_name:
            name_lower = tournament_name.lower()
            for gs in golf_sports:
                title_lower = gs.get("title", "").lower()
                # Check both directions for partial match
                if name_lower in title_lower or title_lower in name_lower:
                    return self.fetch_odds(gs["key"])
            # Also try matching individual words (e.g. "Genesis" in "Genesis Invitational")
            name_words = [w for w in name_lower.split() if len(w) > 3]
            for gs in golf_sports:
                title_lower = gs.get("title", "").lower()
                if any(w in title_lower for w in name_words):
                    return self.fetch_odds(gs["key"])

        # No tournament_name given - return first event only if there's exactly one
        if len(golf_sports) == 1:
            return self.fetch_odds(golf_sports[0]["key"])

        # Multiple events and no name match - don't guess
        event_names = [gs["title"] for gs in golf_sports]
        print(f"[WARN] Could not match tournament to The Odds API events: {event_names}")
        print("[INFO] The Odds API may not cover this week's PGA Tour event")
        return None


def run(tournament_name: str = "") -> TournamentOdds | None:
    """Pipeline entry point for The Odds API scraper.

    Args:
        tournament_name: Optional tournament name.

    Returns:
        TournamentOdds or None.
    """
    scraper = TheOddsScraper()
    return scraper.run(tournament_name)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    result = run()
    if result:
        print(f"\n{'='*70}")
        print(f"Tournament: {result.tournament_name}")
        print(f"Source: {result.source}")
        print(f"Bookmakers: {', '.join(result.bookmakers)}")
        print(f"Players: {len(result.players)}")
        print(f"{'='*70}")
        for p in sorted(result.players, key=lambda x: x.best_odds)[:20]:
            odds_str = f"+{p.best_odds}" if p.best_odds > 0 else str(p.best_odds)
            print(f"  {p.name:<25} {odds_str:>9} {p.best_book:<15} {p.implied_probability:>7.1%}")
    else:
        print("[INFO] No odds data from The Odds API")
