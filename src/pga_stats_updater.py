"""PGA Stats DB アップデーター。

pga_stats.db に最新シーズン（2026年）のデータを追加・更新する。
- シーズン統計（season stats）: GraphQL API から取得
- 大会結果（tournament results）: GraphQL API から完了済み大会の結果を取得

results.yml ワークフローから毎週呼び出され、新しい大会結果を自動的に蓄積する。

Usage:
    uv run python -m src.pga_stats_updater            # 2026年データを更新
    uv run python -m src.pga_stats_updater --year 2025 # 指定年を更新
    uv run python -m src.pga_stats_updater --status    # DB状態を表示
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .pga_stats_db import PGAStatsDB
from .stats_scraper import PGATourGraphQLClient
from .tournament_fetcher import TournamentFetcher


def update_season_stats(year: int, config: dict) -> int:
    """シーズン統計を更新（キャッシュが古い場合のみ再取得）。

    Args:
        year: 対象シーズン年
        config: config.yaml 設定辞書

    Returns:
        取得した統計カテゴリ数
    """
    db = PGAStatsDB()
    client = PGATourGraphQLClient(config)

    # 現在シーズンのキャッシュを無効化して最新データを取得
    current_year = datetime.now().year
    if year >= current_year:
        db.invalidate_current_season()

    missing = db.get_missing_stats(year, client.PRIMARY_STAT_IDS)
    if not missing:
        print(f"[INFO] Season stats {year}: already up-to-date")
        return 0

    print(f"[INFO] Fetching {len(missing)} stat categories for {year}...")
    data = client.fetch_all_stats_for_year(year, stat_ids=missing)
    db.save_stats_bulk(year, data, client.STAT_IDS)

    total_rows = sum(len(rows) for rows in data.values())
    print(f"[OK] Season stats {year}: saved {total_rows} player records "
          f"({len(missing)} categories)")
    return len(missing)


def update_tournament_results(year: int) -> dict:
    """完了済み大会の結果を更新（未取得分のみ）。

    Args:
        year: 対象シーズン年

    Returns:
        {"new_tournaments": int, "new_results": int, "skipped": int}
    """
    fetcher = TournamentFetcher()

    print(f"[INFO] Fetching {year} schedule...")
    tournaments = fetcher.fetch_schedule(year)
    if not tournaments:
        print(f"[WARN] No completed tournaments found for {year}")
        return {"new_tournaments": 0, "new_results": 0, "skipped": 0}

    print(f"[INFO] Found {len(tournaments)} completed tournaments for {year}")

    # 大会マスタ保存
    fetcher.db.save_tournaments(tournaments)

    new_tournaments = 0
    new_results = 0
    skipped = 0

    for idx, t in enumerate(tournaments, 1):
        tid = t["tournament_id"]
        name = t["tournament_name"]

        if fetcher.db.has_tournament_results(tid, year):
            skipped += 1
            continue

        print(f"  [{idx}/{len(tournaments)}] {name}...", end=" ")
        results = fetcher.fetch_tournament_results(tid, year)
        if results:
            saved = fetcher.db.save_tournament_results(results)
            print(f"{len(results)} players")
            new_tournaments += 1
            new_results += saved
        else:
            print("no data")

    summary = {
        "new_tournaments": new_tournaments,
        "new_results": new_results,
        "skipped": skipped,
    }
    print(f"[OK] Tournament results {year}: "
          f"{new_tournaments} new tournaments, "
          f"{new_results} new results, "
          f"{skipped} already cached")
    return summary


def update_all(year: int | None = None, config: dict | None = None) -> None:
    """シーズン統計 + 大会結果を一括更新。

    Args:
        year: 対象年（Noneの場合は現在年）
        config: config.yaml 設定辞書（Noneの場合は自動読み込み）
    """
    if year is None:
        year = datetime.now().year

    if config is None:
        import yaml
        from pathlib import Path
        config_path = Path("config.yaml")
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        else:
            # ワークフロー用の最小設定
            config = {
                "stats_source": {
                    "graphql": {
                        "default_year": year,
                        "rate_limit_seconds": 1,
                        "timeout": 30,
                    }
                },
                "output": {"data_dir": "data"},
            }

    print("=" * 60)
    print(f"  PGA Stats DB Update ({year})")
    print("=" * 60)

    # Step 1: シーズン統計
    print(f"\n--- Step 1: Season Stats ({year}) ---")
    stats_updated = update_season_stats(year, config)

    # Step 2: 大会結果
    print(f"\n--- Step 2: Tournament Results ({year}) ---")
    results_summary = update_tournament_results(year)

    # Summary
    print()
    print("=" * 60)
    print(f"  Update Complete ({year})")
    print("=" * 60)
    print(f"  Stats categories refreshed: {stats_updated}")
    print(f"  New tournaments:            {results_summary['new_tournaments']}")
    print(f"  New player results:         {results_summary['new_results']}")
    print(f"  Tournaments already cached: {results_summary['skipped']}")


def print_status() -> None:
    """pga_stats.db の現在の状態を表示。"""
    db = PGAStatsDB()
    conn = db._get_conn()
    try:
        print("=" * 60)
        print("  PGA Stats DB Status")
        print("=" * 60)

        # Season stats
        rows = conn.execute("""
            SELECT year, COUNT(DISTINCT stat_id) as stats,
                   COUNT(DISTINCT player_id) as players
            FROM pga_season_stats
            GROUP BY year ORDER BY year DESC LIMIT 10
        """).fetchall()
        print("\n  Season Stats (latest 10 years):")
        for r in rows:
            print(f"    {r['year']}: {r['stats']} stats, {r['players']} players")

        # Tournament results
        rows2 = conn.execute("""
            SELECT year, COUNT(DISTINCT tournament_id) as tournaments,
                   COUNT(*) as results
            FROM pga_tournament_results
            GROUP BY year ORDER BY year DESC LIMIT 10
        """).fetchall()
        print("\n  Tournament Results (latest 10 years):")
        for r in rows2:
            print(f"    {r['year']}: {r['tournaments']} tournaments, "
                  f"{r['results']} player results")

        # Fetch log for current year
        current_year = datetime.now().year
        log = conn.execute("""
            SELECT stat_id, fetched_at FROM pga_fetch_log
            WHERE year = ? ORDER BY fetched_at DESC
        """, (current_year,)).fetchall()
        if log:
            print(f"\n  Fetch Log ({current_year}):")
            for r in log:
                print(f"    stat_id={r['stat_id']}: {r['fetched_at']}")
        else:
            print(f"\n  Fetch Log ({current_year}): No data fetched yet")

        print("=" * 60)
    finally:
        conn.close()


# ----- CLI -----

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="PGA Stats DB Updater")
    parser.add_argument(
        "--year", type=int, default=None,
        help="Target year (default: current year)",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show DB status",
    )
    parser.add_argument(
        "--stats-only", action="store_true",
        help="Update season stats only",
    )
    parser.add_argument(
        "--results-only", action="store_true",
        help="Update tournament results only",
    )
    args = parser.parse_args()

    if args.status:
        print_status()
        return 0

    year = args.year or datetime.now().year

    if args.stats_only:
        import yaml
        from pathlib import Path
        config_path = Path("config.yaml")
        config = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        update_season_stats(year, config)
    elif args.results_only:
        update_tournament_results(year)
    else:
        update_all(year)

    return 0


if __name__ == "__main__":
    sys.exit(main())
