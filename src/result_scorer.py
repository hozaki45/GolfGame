"""Result Scorer - Compare bookmaker predictions vs actual tournament results.

Scoring system:
  For each group, each bookmaker has a predicted ranking (sorted by lowest odds).
  After the tournament, players are ranked within each group by actual finish position.

  For each position in a group:
    - If the bookmaker's predicted player at rank K matches the actual player at rank K
      → the bookmaker earns K points (1 for 1st, 2 for 2nd, etc.)
    - If they don't match → 0 points for that position

  Additionally, for the "Top Pick" metric:
    - The bookmaker's #1 pick's actual finish position within the group becomes the score
    - Lower = better (1 = perfect prediction)

  Higher total match points = better prediction accuracy.
  Lower top-pick total = better favorite prediction.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from fuzzywuzzy import fuzz
from tabulate import tabulate

from src.espn_scraper import ESPNScraper, PlayerLeaderboard, TournamentInfo
from src.group_analyzer import GroupAnalysisResult, GroupPlayer


@dataclass
class BookmakerScore:
    """Scoring result for a single bookmaker."""
    name: str
    match_points: int       # Total points from correct position matches
    matches_detail: dict[int, list[int]]  # group_id -> list of matched positions
    top_pick_total: int     # Sum of actual finish positions of #1 picks
    top_pick_detail: dict[int, tuple[str, int]]  # group_id -> (player_name, actual_rank)
    groups_scored: int      # Number of groups with data


def _fuzzy_match_name(name1: str, name2: str, threshold: int = 80) -> bool:
    """Check if two player names match using fuzzy matching."""
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    if n1 == n2:
        return True
    score = max(
        fuzz.ratio(n1, n2),
        fuzz.token_sort_ratio(n1, n2),
    )
    return score >= threshold


def get_actual_group_rankings(
    tournament: TournamentInfo,
    groups: dict[int, list[GroupPlayer]],
) -> dict[int, list[str]]:
    """Rank players within each group by their actual tournament finish position.

    Args:
        tournament: ESPN tournament results.
        groups: Groups from the picks CSV with player assignments.

    Returns:
        Dict mapping group_id to list of player names sorted by actual finish.
        Players who missed the cut or withdrew are placed at the bottom.
    """
    # Build ESPN position lookup
    espn_lookup: dict[str, int] = {}
    for p in tournament.players:
        espn_lookup[p.name.lower().strip()] = p.position

    result: dict[int, list[str]] = {}

    for gid, players in groups.items():
        # For each group player, find their ESPN position
        player_positions: list[tuple[str, int]] = []
        for gp in players:
            # Try to find in ESPN data
            matched_pos = 999
            gp_lower = gp.name.lower().strip()

            # Exact match
            if gp_lower in espn_lookup:
                matched_pos = espn_lookup[gp_lower]
            else:
                # Fuzzy match
                for espn_name, pos in espn_lookup.items():
                    if _fuzzy_match_name(gp.name, espn_name):
                        matched_pos = pos
                        break

            player_positions.append((gp.name, matched_pos))

        # Sort by actual position (lower = better)
        player_positions.sort(key=lambda x: x[1])
        result[gid] = [name for name, _ in player_positions]

    return result


def score_bookmakers(
    analysis: GroupAnalysisResult,
    actual_rankings: dict[int, list[str]],
) -> list[BookmakerScore]:
    """Score each bookmaker's prediction accuracy.

    Args:
        analysis: Group analysis result with bookmaker odds.
        actual_rankings: Actual player rankings per group.

    Returns:
        List of BookmakerScore sorted by match_points descending (best first).
    """
    scores: list[BookmakerScore] = []

    for book in analysis.bookmakers:
        match_points = 0
        matches_detail: dict[int, list[int]] = {}
        top_pick_total = 0
        top_pick_detail: dict[int, tuple[str, int]] = {}
        groups_scored = 0

        for gid in sorted(analysis.groups.keys()):
            players = analysis.groups[gid]
            actual = actual_rankings.get(gid, [])
            if not actual:
                continue

            # Build bookmaker's predicted ranking for this group
            players_with_book = [p for p in players if book in p.odds_by_book]
            if not players_with_book:
                continue

            groups_scored += 1
            predicted = sorted(players_with_book, key=lambda p: p.odds_by_book[book])
            predicted_names = [p.name for p in predicted]

            # Score position matches
            matched_positions: list[int] = []
            max_check = min(len(predicted_names), len(actual))
            for pos in range(max_check):
                if _fuzzy_match_name(predicted_names[pos], actual[pos]):
                    rank = pos + 1
                    match_points += rank
                    matched_positions.append(rank)

            matches_detail[gid] = matched_positions

            # Score top pick
            top_pick_name = predicted_names[0]
            actual_rank = 999
            for i, actual_name in enumerate(actual):
                if _fuzzy_match_name(top_pick_name, actual_name):
                    actual_rank = i + 1
                    break

            top_pick_total += actual_rank
            top_pick_detail[gid] = (top_pick_name, actual_rank)

        scores.append(BookmakerScore(
            name=book,
            match_points=match_points,
            matches_detail=matches_detail,
            top_pick_total=top_pick_total,
            top_pick_detail=top_pick_detail,
            groups_scored=groups_scored,
        ))

    # Sort by match_points descending (higher = more correct predictions)
    scores.sort(key=lambda s: s.match_points, reverse=True)
    return scores


def format_score_report(
    scores: list[BookmakerScore],
    actual_rankings: dict[int, list[str]],
    analysis: GroupAnalysisResult,
) -> str:
    """Generate formatted text report of bookmaker scores.

    Args:
        scores: Scored bookmaker results.
        actual_rankings: Actual rankings per group.
        analysis: Original group analysis.

    Returns:
        Formatted report string.
    """
    lines = []

    # ── Section 1: Overall Rankings ──
    lines.append("=" * 80)
    lines.append("  BOOKMAKER PREDICTION ACCURACY RANKING")
    lines.append("=" * 80)
    lines.append("")

    table_data = []
    for rank, s in enumerate(scores, 1):
        table_data.append([
            rank,
            s.name,
            s.match_points,
            s.top_pick_total,
            s.groups_scored,
        ])

    headers = ["Rank", "Bookmaker", "Match Pts", "Top Pick Score", "Groups"]
    lines.append(tabulate(table_data, headers=headers, tablefmt="simple"))
    lines.append("")
    lines.append("  Match Pts: Higher = better (more positions correctly predicted)")
    lines.append("  Top Pick Score: Lower = better (top picks finished closer to 1st)")
    lines.append("")

    # ── Section 2: Top Pick Detail ──
    lines.append("=" * 80)
    lines.append("  TOP PICK RESULTS (Lower Score = Better)")
    lines.append("=" * 80)
    lines.append("")

    # Sort by top_pick_total ascending for this section
    by_top = sorted(scores, key=lambda s: s.top_pick_total)
    for rank, s in enumerate(by_top, 1):
        lines.append(f"  #{rank} {s.name} (Total: {s.top_pick_total})")
        detail_data = []
        for gid in sorted(s.top_pick_detail.keys()):
            player, actual_rank = s.top_pick_detail[gid]
            mark = "OK" if actual_rank == 1 else ""
            detail_data.append([f"Group {gid}", player, actual_rank, mark])
        headers = ["Group", "Top Pick", "Actual", ""]
        lines.append(tabulate(detail_data, headers=headers, tablefmt="simple"))
        lines.append("")

    # ── Section 3: Actual Group Results ──
    lines.append("=" * 80)
    lines.append("  ACTUAL GROUP RESULTS")
    lines.append("=" * 80)
    lines.append("")

    for gid in sorted(actual_rankings.keys()):
        actual = actual_rankings[gid]
        lines.append(f"  Group {gid}: {' > '.join(actual)}")
    lines.append("")

    # ── Section 4: Position Match Detail ──
    lines.append("=" * 80)
    lines.append("  POSITION MATCH DETAIL")
    lines.append("=" * 80)
    lines.append("")

    for gid in sorted(analysis.groups.keys()):
        actual = actual_rankings.get(gid, [])
        if not actual:
            continue

        lines.append(f"  Group {gid}")
        lines.append("-" * 80)

        table_data = []
        for pos, actual_name in enumerate(actual):
            row = [pos + 1, actual_name]
            for s in scores:
                players_with = [p for p in analysis.groups[gid] if s.name in p.odds_by_book]
                predicted = sorted(players_with, key=lambda p: p.odds_by_book[s.name])
                if pos < len(predicted):
                    pred_name = predicted[pos].name
                    match = "O" if _fuzzy_match_name(pred_name, actual_name) else "X"
                    row.append(f"{match} {pred_name}")
                else:
                    row.append("-")
            table_data.append(row)

        headers = ["#", "Actual"] + [s.name for s in scores]
        lines.append(tabulate(table_data, headers=headers, tablefmt="simple"))
        lines.append("")

    return "\n".join(lines)


def run(
    csv_path: str | None = None,
    analysis: GroupAnalysisResult | None = None,
) -> str | None:
    """Run the result scoring pipeline.

    Args:
        csv_path: Path to picks CSV. If None, uses latest.
        analysis: Pre-computed group analysis. If None, runs fresh analysis.

    Returns:
        Formatted score report string, or None on failure.
    """
    # Step 1: Get group analysis (with bookmaker odds)
    if analysis is None:
        from src.group_analyzer import run as run_group_analysis
        analysis = run_group_analysis(csv_path=csv_path)
        if analysis is None:
            print("[ERROR] Group analysis failed")
            return None

    # Step 2: Fetch actual tournament results from ESPN
    print("[INFO] Fetching actual tournament results from ESPN...")
    espn = ESPNScraper()
    try:
        raw = espn.fetch_leaderboard()
        tournament = espn.parse_tournament(raw)
    except Exception as e:
        print(f"[ERROR] Failed to fetch ESPN results: {e}")
        return None

    if not tournament:
        print("[ERROR] No tournament data from ESPN")
        return None

    # Check if tournament has actual scores
    has_scores = any(p.score != "E" or p.round_scores for p in tournament.players)
    if not has_scores:
        print("[WARN] Tournament has not started yet - all scores are Even")
        print("[INFO] Run this again after the tournament starts or ends for actual results")

    print(f"[INFO] Tournament: {tournament.name}")
    print(f"[INFO] ESPN players: {len(tournament.players)}")

    # Step 3: Get actual group rankings
    actual_rankings = get_actual_group_rankings(tournament, analysis.groups)
    print(f"[INFO] Ranked {len(actual_rankings)} groups")

    # Step 4: Score bookmakers
    scores = score_bookmakers(analysis, actual_rankings)

    # Step 5: Generate report
    report = format_score_report(scores, actual_rankings, analysis)

    # Save report
    from datetime import datetime
    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"bookmaker_scores_{date_str}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"[INFO] Saved score report to {report_path}")

    print()
    print(report)
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
