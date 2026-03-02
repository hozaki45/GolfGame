"""Golf Odds Scraper - Vegas Insider.

Fetches outright winner odds for PGA Tour tournaments from Vegas Insider.
Aggregates odds from multiple bookmakers (Bet365, BetMGM, DraftKings, etc.).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import yaml


@dataclass
class PlayerOdds:
    """A player's betting odds from multiple bookmakers."""
    name: str
    odds_by_book: dict[str, int]  # bookmaker -> American odds (e.g. {"DraftKings": 295})
    best_odds: int                 # Best (highest) American odds
    best_book: str                 # Bookmaker with best odds
    decimal_odds: float            # Best odds in decimal format
    implied_probability: float     # Implied probability from best odds


@dataclass
class TournamentOdds:
    """Betting odds for a tournament."""
    tournament_name: str
    source: str
    bookmakers: list[str]
    players: list[PlayerOdds]
    fetched_at: str


def american_to_decimal(american: int) -> float:
    """Convert American odds to decimal odds.

    Args:
        american: American odds (e.g. +280, -150).

    Returns:
        Decimal odds (e.g. 3.8, 1.67).
    """
    if american > 0:
        return (american / 100.0) + 1.0
    elif american < 0:
        return (100.0 / abs(american)) + 1.0
    return 0.0


def american_to_implied_prob(american: int) -> float:
    """Convert American odds to implied probability.

    Args:
        american: American odds.

    Returns:
        Implied probability (0.0 to 1.0).
    """
    if american > 0:
        return 100.0 / (american + 100.0)
    elif american < 0:
        return abs(american) / (abs(american) + 100.0)
    return 0.0


class OddsScraper:
    """Scrapes golf betting odds from Vegas Insider."""

    FUTURES_URL = "https://www.vegasinsider.com/golf/odds/futures/"

    def __init__(self, config_path: str = "config.yaml"):
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.timeout = 30

    def fetch_tournament_odds(self, tournament_name: str = "") -> TournamentOdds | None:
        """Fetch outright winner odds for the current PGA Tour tournament.

        Args:
            tournament_name: Tournament name (for logging, not used for URL).

        Returns:
            TournamentOdds or None.
        """
        print(f"[INFO] Fetching odds from Vegas Insider...")
        try:
            headers = {"User-Agent": self.user_agent}
            resp = requests.get(self.FUTURES_URL, headers=headers, timeout=self.timeout)
            resp.raise_for_status()

            return self._parse_html(resp.text, tournament_name)

        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch Vegas Insider: {e}")
            return None

    def _parse_html(self, html: str, tournament_name: str) -> TournamentOdds | None:
        """Parse Vegas Insider HTML to extract odds data.

        Args:
            html: Raw HTML string.
            tournament_name: Tournament name.

        Returns:
            TournamentOdds or None.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Get tournament name from page title if not provided
        if not tournament_name:
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
                # Extract tournament name from title like "The Pebble Beach Pro-Am Odds"
                match = re.match(r"(?:The\s+)?(.+?)\s+Odds", title)
                if match:
                    tournament_name = match.group(1)

        table = soup.find("table")
        if not table:
            print("[WARN] No odds table found on page")
            return None

        rows = table.find_all("tr")
        if len(rows) < 2:
            print("[WARN] Odds table has no data rows")
            return None

        # Parse header row to get bookmaker names
        header_cells = rows[0].find_all(["th", "td"])
        bookmakers = []
        for cell in header_cells[1:]:  # Skip first column (player/time)
            name = cell.get_text(strip=True)
            if name and name not in ("", "Time"):
                bookmakers.append(name)

        print(f"[INFO] Bookmakers: {bookmakers}")

        # Parse player rows
        players = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            player_name = cells[0].get_text(strip=True)
            if not player_name or len(player_name) < 3:
                continue

            # Skip non-player rows (navigation arrows, etc.)
            if player_name in ("›", "‹", ""):
                continue

            odds_by_book: dict[str, int] = {}
            for i, cell in enumerate(cells[1:]):
                if i >= len(bookmakers):
                    break
                odds_text = cell.get_text(strip=True)
                odds_val = self._parse_american_odds(odds_text)
                if odds_val is not None:
                    odds_by_book[bookmakers[i]] = odds_val

            if not odds_by_book:
                continue

            # Find best odds (highest value = best payout for positive odds)
            # Filter out extremely high odds (100000+) as they may be placeholders
            reasonable_odds = {k: v for k, v in odds_by_book.items() if v < 100000}
            if not reasonable_odds:
                reasonable_odds = odds_by_book

            best_book = max(reasonable_odds, key=lambda k: reasonable_odds[k])
            best_odds = reasonable_odds[best_book]
            decimal = american_to_decimal(best_odds)
            implied_prob = american_to_implied_prob(best_odds)

            players.append(PlayerOdds(
                name=player_name,
                odds_by_book=odds_by_book,
                best_odds=best_odds,
                best_book=best_book,
                decimal_odds=decimal,
                implied_probability=implied_prob,
            ))

        if not players:
            print("[WARN] No player odds parsed")
            return None

        print(f"[INFO] Parsed {len(players)} players with odds")
        return TournamentOdds(
            tournament_name=tournament_name or "Unknown",
            source="Vegas Insider",
            bookmakers=bookmakers,
            players=players,
            fetched_at=datetime.now().isoformat(),
        )

    def _parse_american_odds(self, text: str) -> int | None:
        """Parse American odds from text.

        Args:
            text: Odds string like "+280+", "-150", "+100000+".

        Returns:
            Integer odds value or None.
        """
        text = text.strip()
        if not text:
            return None

        # Remove trailing + signs (Vegas Insider uses "+280+" format)
        clean = re.sub(r"\+$", "", text)
        # Parse the number
        match = re.match(r"^([+-]?\d+)$", clean)
        if match:
            return int(match.group(1))
        return None

    def save_raw_data(self, data: TournamentOdds, data_dir: str = "data") -> Path:
        """Save tournament odds to data/raw/."""
        raw_dir = Path(data_dir) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = raw_dir / f"odds_{date_str}.json"

        out = {
            "tournament_name": data.tournament_name,
            "source": data.source,
            "bookmakers": data.bookmakers,
            "fetched_at": data.fetched_at,
            "players": [
                {
                    "name": p.name,
                    "odds_by_book": p.odds_by_book,
                    "best_odds": p.best_odds,
                    "best_book": p.best_book,
                    "decimal_odds": p.decimal_odds,
                    "implied_probability": p.implied_probability,
                }
                for p in data.players
            ],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Saved odds data to {filepath}")
        return filepath


def merge_odds(primary: TournamentOdds, secondary: TournamentOdds) -> TournamentOdds:
    """Merge odds from two sources.

    Players are matched by name. Bookmakers from secondary are added
    to primary players' odds_by_book. If the same bookmaker exists in both,
    secondary takes precedence (API data is typically more accurate).

    Args:
        primary: Primary odds data (e.g. Vegas Insider).
        secondary: Secondary odds data (e.g. The Odds API).

    Returns:
        Merged TournamentOdds.
    """
    from fuzzywuzzy import fuzz

    # Build lookup for secondary players
    sec_lookup: dict[str, PlayerOdds] = {}
    for p in secondary.players:
        sec_lookup[p.name.lower().strip()] = p

    merged_players = []
    matched_sec = set()

    for p in primary.players:
        # Try exact match
        p_lower = p.name.lower().strip()
        sec_player = sec_lookup.get(p_lower)

        # Try fuzzy match
        if not sec_player:
            best_score = 0
            best_match = None
            for sec_name, sec_p in sec_lookup.items():
                score = max(
                    fuzz.ratio(p_lower, sec_name),
                    fuzz.token_sort_ratio(p_lower, sec_name),
                )
                if score > best_score:
                    best_score = score
                    best_match = sec_p
            if best_match and best_score >= 80:
                sec_player = best_match

        # Merge odds_by_book
        merged_book = dict(p.odds_by_book)
        if sec_player:
            matched_sec.add(sec_player.name.lower().strip())
            for book, odds_val in sec_player.odds_by_book.items():
                if book not in merged_book:
                    merged_book[book] = odds_val

        # Recalculate best odds
        if merged_book:
            best_book = min(merged_book, key=lambda k: merged_book[k])
            best_odds = merged_book[best_book]
        else:
            best_book = p.best_book
            best_odds = p.best_odds

        merged_players.append(PlayerOdds(
            name=p.name,
            odds_by_book=merged_book,
            best_odds=best_odds,
            best_book=best_book,
            decimal_odds=american_to_decimal(best_odds),
            implied_probability=american_to_implied_prob(best_odds),
        ))

    # Add unmatched secondary players
    for sec_p in secondary.players:
        if sec_p.name.lower().strip() not in matched_sec:
            merged_players.append(sec_p)

    # Merge bookmaker lists (deduplicated, ordered)
    all_books = list(primary.bookmakers)
    for b in secondary.bookmakers:
        if b not in all_books:
            all_books.append(b)

    print(f"[INFO] Merged: {len(primary.players)} (Vegas Insider) + {len(secondary.players)} (The Odds API) = {len(merged_players)} players, {len(all_books)} bookmakers")

    return TournamentOdds(
        tournament_name=primary.tournament_name,
        source=f"{primary.source} + {secondary.source}",
        bookmakers=all_books,
        players=merged_players,
        fetched_at=datetime.now().isoformat(),
    )


def run(config: dict | None = None, tournament_name: str = "") -> TournamentOdds | None:
    """Pipeline entry point for odds scraper.

    Fetches from Vegas Insider and optionally merges with The Odds API.

    Args:
        config: Optional config dict.
        tournament_name: Tournament name from ESPN.

    Returns:
        TournamentOdds or None.
    """
    scraper = OddsScraper()
    result = scraper.fetch_tournament_odds(tournament_name)
    if result:
        scraper.save_raw_data(result)

    # Try to merge with The Odds API
    try:
        from src.theodds_scraper import TheOddsScraper
        theo_scraper = TheOddsScraper()
        if theo_scraper.enabled and theo_scraper.api_key:
            theo_result = theo_scraper.run(tournament_name)
            if theo_result and result:
                result = merge_odds(result, theo_result)
            elif theo_result and not result:
                result = theo_result
    except Exception as e:
        print(f"[WARN] The Odds API integration skipped: {e}")

    return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    odds = run(tournament_name="AT&T Pebble Beach Pro-Am")
    if odds:
        print(f"\n{'='*70}")
        print(f"Tournament: {odds.tournament_name}")
        print(f"Source: {odds.source}")
        print(f"Bookmakers: {', '.join(odds.bookmakers)}")
        print(f"Players: {len(odds.players)}")
        print(f"{'='*70}")
        print(f"{'Player':<25} {'Best Odds':>10} {'Book':<15} {'Prob':>8}")
        print(f"{'-'*25} {'-'*10} {'-'*15} {'-'*8}")
        for p in sorted(odds.players, key=lambda x: x.implied_probability, reverse=True)[:30]:
            print(f"  {p.name:<25} {'+' + str(p.best_odds):>9} {p.best_book:<15} {p.implied_probability:>7.1%}")
    else:
        print("[INFO] No odds data retrieved")
