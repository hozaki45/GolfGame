"""Group Analyzer - Picks CSV + Odds Cross-Reference.

Groups players from the picks CSV by their Group ID,
then sorts each group by lowest odds first (favorite = most likely to win).
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fuzzywuzzy import fuzz
from tabulate import tabulate

from src.odds_scraper import OddsScraper, PlayerOdds, american_to_implied_prob, run as run_odds


@dataclass
class GroupPlayer:
    """A player with group and odds information."""
    name: str
    group_id: int
    wgr: str
    fedex_rank: str
    best_odds: int | None
    best_book: str
    implied_prob: float
    odds_display: str
    odds_by_book: dict[str, int]  # bookmaker -> American odds

    # Stats-based prediction fields (Phase 3)
    stats: object | None = None  # PlayerStats from stats_models
    stats_prediction_score: float | None = None
    stats_rank_in_group: int | None = None
    odds_vs_stats_agreement: str | None = None  # "Strong Match", "Partial Match", "Disagree", "N/A"

    # Course Fit fields (Phase 4)
    course_fit_score: float | None = None      # 0-100
    course_fit_rank: int | None = None         # グループ内フィットランク
    player_type: str | None = None             # "Power Hitter" etc.

    # ML Integration fields (Phase 5)
    ml_score: float | None = None              # 0-100 統合MLスコア
    ml_rank_in_group: int | None = None        # グループ内MLランク
    ml_confidence: str | None = None           # "High" / "Medium" / "Low"
    ml_model_version: str | None = None        # モデルバージョン

    # Game Score Optimization fields (Phase 6)
    egs: float | None = None                   # Expected Game Score（低い=良い）
    egs_rank_in_group: int | None = None       # グループ内EGSランク (1=最適)
    p_cut: float | None = None                 # CUT確率
    handicap: int | None = None                # ハンデキャップ

    # Major Affinity fields (Phase 7)
    major_affinity_score: float | None = None   # 0-100 メジャー適性スコア


@dataclass
class GroupAnalysisResult:
    """Complete result of group analysis."""
    groups: dict[int, list[GroupPlayer]]
    bookmakers: list[str]
    tournament_name: str
    generated_at: str


def load_picks_csv(csv_path: str) -> list[dict]:
    """Load the field CSV into a list of dicts.

    Args:
        csv_path: Path to the picks CSV file.

    Returns:
        List of row dicts.
    """
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def match_odds_to_player(
    player_name: str,
    odds_list: list[PlayerOdds],
    threshold: int = 80,
) -> PlayerOdds | None:
    """Find matching odds for a player using fuzzy matching.

    Args:
        player_name: Player name from picks CSV.
        odds_list: List of PlayerOdds from Vegas Insider.
        threshold: Minimum fuzzy match score.

    Returns:
        Best matching PlayerOdds or None.
    """
    name_lower = player_name.lower().strip()

    # Exact match first
    for p in odds_list:
        if p.name.lower().strip() == name_lower:
            return p

    # Fuzzy match
    best_score = 0
    best_match = None
    for p in odds_list:
        score = max(
            fuzz.ratio(name_lower, p.name.lower().strip()),
            fuzz.token_sort_ratio(name_lower, p.name.lower().strip()),
        )
        if score > best_score:
            best_score = score
            best_match = p

    if best_match and best_score >= threshold:
        return best_match
    return None


def analyze_groups(
    csv_path: str,
    odds_list: list[PlayerOdds],
) -> dict[int, list[GroupPlayer]]:
    """Group players and sort each group by best odds.

    Args:
        csv_path: Path to the picks CSV file.
        odds_list: List of PlayerOdds from scraper.

    Returns:
        Dict mapping group_id to sorted list of GroupPlayers.
    """
    rows = load_picks_csv(csv_path)
    groups: dict[int, list[GroupPlayer]] = {}
    no_odds_count = 0

    for row in rows:
        name = row["Golfer"]
        group_id = int(row["Group ID"])
        wgr = row.get("currentWGR", "")
        fedex_rank = row.get("FedEx Rank", "")

        odds_match = match_odds_to_player(name, odds_list)

        if odds_match:
            best_odds = odds_match.best_odds
            best_book = odds_match.best_book
            implied_prob = odds_match.implied_probability
            odds_by_book = odds_match.odds_by_book
            if best_odds > 0:
                odds_display = f"+{best_odds}"
            else:
                odds_display = str(best_odds)
        else:
            best_odds = None
            best_book = "-"
            implied_prob = 0.0
            odds_by_book = {}
            odds_display = "N/A"
            no_odds_count += 1

        gp = GroupPlayer(
            name=name,
            group_id=group_id,
            wgr=wgr,
            fedex_rank=fedex_rank,
            best_odds=best_odds,
            best_book=best_book,
            implied_prob=implied_prob,
            odds_display=odds_display,
            odds_by_book=odds_by_book,
        )
        groups.setdefault(group_id, []).append(gp)

    if no_odds_count:
        print(f"[WARN] {no_odds_count} players without odds data")

    # Sort each group by lowest odds first (favorite on top)
    # Lower American odds = higher win probability = better pick
    # Players without odds go to the bottom
    for gid in groups:
        groups[gid].sort(key=lambda p: (
            0 if p.best_odds is not None else 1,
            p.best_odds if p.best_odds is not None else 999999,
        ))

    return groups


def _fmt_odds(odds: int) -> str:
    """Format American odds with +/- sign."""
    return f"+{odds}" if odds > 0 else str(odds)


def _collect_bookmakers(groups: dict[int, list[GroupPlayer]]) -> list[str]:
    """Collect all bookmaker names across all players."""
    books: set[str] = set()
    for players in groups.values():
        for p in players:
            books.update(p.odds_by_book.keys())
    # Fixed display order
    preferred = ["Bet365", "BetMGM", "DraftKings", "Caesars", "FanDuel", "RiversCasino"]
    return [b for b in preferred if b in books] + sorted(books - set(preferred))


def format_report(
    groups: dict[int, list[GroupPlayer]],
    bookmakers: list[str] | None = None,
) -> str:
    """Generate a formatted report of groups sorted by odds.

    Args:
        groups: Dict mapping group_id to sorted GroupPlayers.
        bookmakers: List of bookmaker names. Auto-detected if None.

    Returns:
        Formatted report string.
    """
    if bookmakers is None:
        bookmakers = _collect_bookmakers(groups)

    lines = []

    # ── Section 1: Best Odds Overview ──
    lines.append("=" * 90)
    lines.append("  GROUP ANALYSIS - Players Sorted by Lowest Odds (Favorite First)")
    lines.append("=" * 90)
    lines.append("")

    for gid in sorted(groups.keys()):
        players = groups[gid]
        lines.append(f"  GROUP {gid}")
        lines.append("-" * 90)

        table_data = []
        for rank, p in enumerate(players, 1):
            table_data.append([
                rank,
                p.name,
                p.wgr,
                p.fedex_rank if p.fedex_rank else "-",
                p.odds_display,
                p.best_book,
                f"{p.implied_prob:.1%}" if p.best_odds is not None else "-",
            ])

        headers = ["#", "Player", "WGR", "FedEx", "Best Odds", "Book", "Impl%"]
        lines.append(tabulate(table_data, headers=headers, tablefmt="simple"))
        lines.append("")

    # ── Section 2: Per-Bookmaker Rankings ──
    lines.append("=" * 90)
    lines.append("  BOOKMAKER-BY-BOOKMAKER RANKINGS")
    lines.append("=" * 90)
    lines.append("")

    for gid in sorted(groups.keys()):
        players = groups[gid]
        lines.append(f"  GROUP {gid}")
        lines.append("-" * 90)

        # Build table: each row is a player, columns are rank per bookmaker
        # First build the header row with all odds per book
        table_data = []
        for p in players:
            row = [p.name]
            for book in bookmakers:
                odds_val = p.odds_by_book.get(book)
                row.append(_fmt_odds(odds_val) if odds_val is not None else "-")
            row.append(p.odds_display)  # Best column
            table_data.append(row)

        headers = ["Player"] + bookmakers + ["Best"]
        lines.append(tabulate(table_data, headers=headers, tablefmt="simple"))
        lines.append("")

        # Ranking per bookmaker (lowest odds = favorite first)
        lines.append(f"  GROUP {gid} - Ranking by Bookmaker (Favorite First)")
        rank_data = []
        for book in bookmakers:
            # Sort players by this bookmaker's odds (lowest first = favorite)
            # Players without odds for this book go last
            sorted_by_book = sorted(
                players,
                key=lambda p, b=book: (
                    0 if b in p.odds_by_book else 1,
                    p.odds_by_book.get(b, 999999),
                ),
            )
            rank_data.append(
                [book] + [f"{i+1}. {p.name}" for i, p in enumerate(sorted_by_book)]
            )

        # Transpose: bookmaker rows → position columns
        rank_headers = ["Book"] + [f"#{i+1}" for i in range(len(players))]
        lines.append(tabulate(rank_data, headers=rank_headers, tablefmt="simple"))
        lines.append("")

    # ── Section 3: Summary - Best pick per group per bookmaker ──
    lines.append("=" * 90)
    lines.append("  RECOMMENDED PICKS - Best Odds per Group (Overall)")
    lines.append("-" * 90)

    summary_data = []
    for gid in sorted(groups.keys()):
        top = groups[gid][0]
        summary_data.append([
            f"Group {gid}",
            top.name,
            top.wgr,
            top.odds_display,
            top.best_book,
            f"{top.implied_prob:.1%}" if top.best_odds is not None else "-",
        ])

    headers = ["Group", "Player", "WGR", "Odds", "Book", "Impl%"]
    lines.append(tabulate(summary_data, headers=headers, tablefmt="simple"))
    lines.append("")

    # Per-bookmaker best picks
    lines.append("")
    lines.append("=" * 90)
    lines.append("  RECOMMENDED PICKS - Best per Group by Each Bookmaker")
    lines.append("-" * 90)

    for book in bookmakers:
        book_summary = []
        for gid in sorted(groups.keys()):
            players = groups[gid]
            # Find player with lowest odds (favorite) at this bookmaker
            players_with_book = [p for p in players if book in p.odds_by_book]
            if players_with_book:
                best = min(players_with_book, key=lambda p: p.odds_by_book[book])
                odds_val = best.odds_by_book[book]
                book_summary.append([
                    f"Group {gid}",
                    best.name,
                    _fmt_odds(odds_val),
                ])
            else:
                book_summary.append([f"Group {gid}", "-", "-"])

        lines.append(f"\n  {book}")
        headers = ["Group", "Player", "Odds"]
        lines.append(tabulate(book_summary, headers=headers, tablefmt="simple"))

    lines.append("")
    return "\n".join(lines)


def run(
    csv_path: str | None = None,
    tournament_name: str = "",
    stats: list | None = None,
) -> GroupAnalysisResult | None:
    """Run the group analysis pipeline.

    Args:
        csv_path: Path to picks CSV. If None, uses the latest file in data/picks/.
        tournament_name: Optional tournament name override.
        stats: Optional list of PlayerStats. If provided, adds stats-based predictions.

    Returns:
        GroupAnalysisResult or None on failure.
    """
    # Find latest CSV if not specified
    if not csv_path:
        picks_dir = Path("data/picks")
        if not picks_dir.exists():
            print("[ERROR] No picks data directory found. Run picks_downloader first.")
            return None
        csv_files = sorted(picks_dir.glob("field_*.csv"), reverse=True)
        if not csv_files:
            print("[ERROR] No picks CSV files found. Run picks_downloader first.")
            return None
        csv_path = str(csv_files[0])
        print(f"[INFO] Using latest picks CSV: {csv_path}")

    # Fetch odds (Vegas Insider + The Odds API if enabled)
    print("[INFO] Fetching odds...")
    odds_data = run_odds(tournament_name=tournament_name)
    if not odds_data:
        print("[ERROR] Failed to fetch odds data")
        return None

    print(f"[INFO] Got odds for {len(odds_data.players)} players from {odds_data.source}")

    # Analyze groups
    groups = analyze_groups(csv_path, odds_data.players)
    bookmakers = odds_data.bookmakers
    resolved_name = tournament_name or odds_data.tournament_name or "PGA Tour Event"
    print(f"[INFO] Analyzed {sum(len(v) for v in groups.values())} players in {len(groups)} groups")

    # Add stats-based predictions if stats are provided
    if stats:
        print(f"[INFO] Adding stats-based predictions for {len(stats)} players...")
        try:
            from src.stats_analyzer import create_predictor

            # Load config for predictor
            import yaml
            config_path = Path("config.yaml")
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
            else:
                config = {}

            # Create predictor and add prediction scores
            predictor = create_predictor(config)
            stats_with_predictions = predictor.add_predictions_to_stats(stats)

            # Create lookup dict: player name -> PlayerStats
            stats_lookup = {s.name.lower().strip(): s for s in stats_with_predictions}

            # Add stats to each GroupPlayer
            for group_id, players in groups.items():
                for player in players:
                    player_name_lower = player.name.lower().strip()

                    # Find matching stats (exact match first)
                    player_stats = stats_lookup.get(player_name_lower)

                    # Fuzzy match if exact match fails
                    if not player_stats:
                        best_score = 0
                        best_match = None
                        for stats_name, ps in stats_lookup.items():
                            score = max(
                                fuzz.ratio(player_name_lower, stats_name),
                                fuzz.token_sort_ratio(player_name_lower, stats_name),
                            )
                            if score > best_score:
                                best_score = score
                                best_match = ps
                        if best_match and best_score >= 80:
                            player_stats = best_match

                    if player_stats:
                        player.stats = player_stats
                        player.stats_prediction_score = player_stats.prediction_score

                # Calculate stats rank in group
                players_with_stats = [p for p in players if p.stats_prediction_score is not None]
                players_with_stats.sort(key=lambda p: p.stats_prediction_score, reverse=True)

                for rank, player in enumerate(players_with_stats, 1):
                    player.stats_rank_in_group = rank

                # Calculate odds vs stats agreement
                for player in players:
                    if player.best_odds is not None and player.stats_rank_in_group is not None:
                        # Find odds rank (already sorted by odds in analyze_groups)
                        odds_rank = players.index(player) + 1

                        # Compare ranks
                        rank_diff = abs(odds_rank - player.stats_rank_in_group)
                        if rank_diff == 0:
                            player.odds_vs_stats_agreement = "Strong Match"
                        elif rank_diff <= 1:
                            player.odds_vs_stats_agreement = "Partial Match"
                        else:
                            player.odds_vs_stats_agreement = "Disagree"
                    else:
                        player.odds_vs_stats_agreement = "N/A"

            print(f"[OK] Stats predictions added to {sum(1 for g in groups.values() for p in g if p.stats is not None)} players")

        except Exception as e:
            print(f"[WARN] Failed to add stats predictions: {e}")

    # Generate text report
    report = format_report(groups, bookmakers=bookmakers)

    # Save to data/output/
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"group_analysis_{date_str}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"[INFO] Saved report to {report_path}")

    # Print to console
    print()
    print(report)

    return GroupAnalysisResult(
        groups=groups,
        bookmakers=bookmakers,
        tournament_name=resolved_name,
        generated_at=datetime.now().isoformat(),
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
