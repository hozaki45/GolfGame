"""ESPN PGA Tour Leaderboard Scraper.

Fetches current tournament leaderboard data from the ESPN public JSON API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

import requests
import yaml


@dataclass
class PlayerLeaderboard:
    """A player's tournament leaderboard entry."""
    name: str
    position: int
    score: str          # e.g. "-19", "E", "+3"
    round_scores: list[str]  # e.g. ["-7", "-5", "-4", "-3"]
    country: str
    athlete_id: str


@dataclass
class TournamentInfo:
    """Current tournament metadata."""
    name: str
    event_id: str
    start_date: str
    end_date: str
    players: list[PlayerLeaderboard]


class ESPNScraper:
    """Scrapes PGA Tour leaderboard data from ESPN's public API."""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        espn_cfg = config["espn"]
        self.base_url = espn_cfg["base_url"]
        self.timeout = espn_cfg["timeout"]

    def fetch_leaderboard(self) -> dict:
        """Fetch raw JSON from ESPN scoreboard API.

        Returns:
            Raw API response as dict.

        Raises:
            requests.RequestException: On network errors.
        """
        url = f"{self.base_url}/scoreboard"
        print(f"[INFO] Fetching ESPN leaderboard from {url}")
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def parse_tournament(self, data: dict) -> TournamentInfo | None:
        """Parse raw ESPN JSON into TournamentInfo.

        Args:
            data: Raw ESPN API response.

        Returns:
            TournamentInfo with player list, or None if no active tournament.
        """
        # Events can be at top-level or nested under leagues
        events = data.get("events", [])
        if not events:
            leagues = data.get("leagues", [])
            if leagues:
                events = leagues[0].get("events", [])
        if not events:
            print("[WARN] No active events found")
            return None

        event = events[0]
        tournament_name = event.get("name", "Unknown")
        event_id = event.get("id", "")
        start_date = event.get("date", "")
        end_date = event.get("endDate", "")

        competitions = event.get("competitions", [])
        if not competitions:
            print("[WARN] No competitions found in event")
            return None

        competitors = competitions[0].get("competitors", [])
        players = []

        for i, comp in enumerate(competitors):
            athlete = comp.get("athlete", {})
            name = athlete.get("displayName") or athlete.get("fullName", "Unknown")

            score = comp.get("score", "N/A")
            country = ""
            flag = athlete.get("flag", {})
            if flag:
                country = flag.get("alt", "")

            round_scores = []
            for ls in comp.get("linescores", []):
                round_scores.append(ls.get("displayValue", "N/A"))

            athlete_id = comp.get("id", "")
            position = comp.get("order", i + 1)

            players.append(PlayerLeaderboard(
                name=name,
                position=position,
                score=score,
                round_scores=round_scores,
                country=country,
                athlete_id=athlete_id,
            ))

        print(f"[INFO] Parsed {len(players)} players for {tournament_name}")
        return TournamentInfo(
            name=tournament_name,
            event_id=event_id,
            start_date=start_date,
            end_date=end_date,
            players=players,
        )

    def save_raw_data(self, data: dict, data_dir: str = "data") -> Path:
        """Save raw ESPN JSON to data/raw/.

        Args:
            data: Raw API response.
            data_dir: Base data directory.

        Returns:
            Path to saved file.
        """
        raw_dir = Path(data_dir) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = raw_dir / f"espn_{date_str}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Saved raw ESPN data to {filepath}")
        return filepath


def run(config: dict | None = None) -> TournamentInfo | None:
    """Pipeline entry point for ESPN scraper.

    Args:
        config: Optional config dict. If None, loads from config.yaml.

    Returns:
        TournamentInfo with current tournament data, or None.
    """
    scraper = ESPNScraper()
    raw_data = scraper.fetch_leaderboard()
    scraper.save_raw_data(raw_data)
    return scraper.parse_tournament(raw_data)


if __name__ == "__main__":
    tournament = run()
    if tournament:
        print(f"\n{'='*60}")
        print(f"Tournament: {tournament.name}")
        print(f"{'='*60}")
        for p in tournament.players[:20]:
            rounds = " | ".join(p.round_scores) if p.round_scores else "N/A"
            print(f"  {p.position:>3}. {p.name:<25} {p.score:>5}  [{rounds}]")
    else:
        print("[INFO] No active tournament found")
