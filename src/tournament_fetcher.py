"""PGA Tour大会スケジュール・結果データ取得モジュール。

PGA Tour GraphQL APIからスケジュールと大会結果（順位・賞金額）を取得し、
ローカルDBにキャッシュする。コースフィット分析の基盤データ。
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

from .pga_stats_db import PGAStatsDB


#-----GraphQL API Constants-----

GRAPHQL_URL = "https://orchestrator.pgatour.com/graphql"
API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"


class TournamentFetcher:
    """PGA Tour GraphQL APIから大会スケジュール・結果を取得。"""

    def __init__(self, db: PGAStatsDB | None = None, rate_limit: float = 1.0):
        """初期化。

        Args:
            db: PGAStatsDBインスタンス（Noneの場合は自動作成）
            rate_limit: APIリクエスト間隔（秒）
        """
        self.db = db or PGAStatsDB()
        self.rate_limit_seconds = rate_limit
        self.last_request_time = 0.0

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
        })

    def _rate_limit(self) -> None:
        """レート制限を遵守。"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)

    def _graphql_query(self, query: str) -> dict:
        """GraphQLクエリを実行。

        Args:
            query: GraphQLクエリ文字列

        Returns:
            レスポンスのdata部分
        """
        self._rate_limit()
        self.last_request_time = time.time()

        response = self.session.post(
            GRAPHQL_URL,
            json={"query": query},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()

        if "errors" in result:
            raise RuntimeError(f"GraphQL errors: {result['errors']}")

        return result.get("data", {})

    #-----Schedule-----

    def fetch_schedule(self, year: int) -> list[dict]:
        """指定年のPGA Tourスケジュールを取得（コース情報含む）。

        Args:
            year: シーズン年（2012以降）

        Returns:
            [{tournament_id, tournament_name, year, course_name, city,
              state, state_code, country, country_code, purse}, ...]
        """
        query = """
        {
            schedule(tourCode: "R", year: "%d") {
                completed {
                    month
                    year
                    tournaments {
                        id
                        tournamentName
                        courseName
                        city
                        state
                        stateCode
                        country
                        countryCode
                        purse
                    }
                }
            }
        }
        """ % year

        data = self._graphql_query(query)
        schedule = data.get("schedule", {})
        completed = schedule.get("completed", [])

        tournaments = []
        for month_group in completed:
            for t in month_group.get("tournaments", []):
                tid = t.get("id", "")
                name = t.get("tournamentName", "")
                if tid and name:
                    tournaments.append({
                        "tournament_id": tid,
                        "tournament_name": name,
                        "year": year,
                        "course_name": t.get("courseName", ""),
                        "city": t.get("city", ""),
                        "state": t.get("state", ""),
                        "state_code": t.get("stateCode", ""),
                        "country": t.get("country", ""),
                        "country_code": t.get("countryCode", ""),
                        "purse": t.get("purse", ""),
                    })

        return tournaments

    #-----Tournament Results-----

    def fetch_tournament_results(
        self, tournament_id: str, year: int
    ) -> list[dict]:
        """大会結果（順位・賞金額）を取得。

        Args:
            tournament_id: トーナメントID（例: "R2024034"）
            year: 年

        Returns:
            [{player_id, player_name, position, total_score, prize_money, fedex_points}, ...]
        """
        # tournamentPastResults の year は 年×10 形式
        year_param = year * 10

        query = """
        {
            tournamentPastResults(id: "%s", year: %d) {
                id
                players {
                    id
                    position
                    total
                    parRelativeScore
                    player {
                        id
                        firstName
                        lastName
                        displayName
                    }
                    additionalData
                }
            }
        }
        """ % (tournament_id, year_param)

        try:
            data = self._graphql_query(query)
        except Exception as e:
            print(f"[WARN] Failed to fetch results for {tournament_id} ({year}): {e}")
            return []

        past_results = data.get("tournamentPastResults", {})
        if not past_results:
            return []

        players_data = past_results.get("players", [])
        results = []

        for p in players_data:
            player_info = p.get("player", {})
            player_id = player_info.get("id", "")
            player_name = player_info.get("displayName", "")

            if not player_id or not player_name:
                continue

            # 順位をパース（"T3", "1", "CUT" など）
            position_str = p.get("position", "")
            position = self._parse_position(position_str)

            total_score = p.get("parRelativeScore", "")

            # additionalData: [0]=FedExポイント, [1]=賞金額
            additional = p.get("additionalData", [])
            prize_money = self._parse_prize_money(additional)
            fedex_points = self._parse_fedex_points(additional)

            results.append({
                "tournament_id": tournament_id,
                "year": year,
                "player_id": player_id,
                "player_name": player_name,
                "position": position,
                "total_score": total_score,
                "prize_money": prize_money,
                "fedex_points": fedex_points,
            })

        return results

    @staticmethod
    def _parse_position(pos_str: str) -> int | None:
        """順位文字列を数値に変換。"T3" → 3, "CUT" → None"""
        if not pos_str:
            return None
        # "T3", "T10" → 3, 10
        cleaned = pos_str.replace("T", "").replace("t", "").strip()
        try:
            return int(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_prize_money(additional_data: list) -> float | None:
        """additionalDataから賞金額をパース。"$3,600,000.00" → 3600000.0"""
        if not additional_data or len(additional_data) < 2:
            return None
        raw = additional_data[1]
        if not raw or not isinstance(raw, str):
            return None
        cleaned = raw.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_fedex_points(additional_data: list) -> float | None:
        """additionalDataからFedExポイントをパース。"""
        if not additional_data:
            return None
        raw = additional_data[0]
        if not raw or not isinstance(raw, str):
            return None
        cleaned = raw.replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None

    #-----Batch Collection-----

    def collect_historical(
        self,
        start_year: int = 2018,
        end_year: int | None = None,
    ) -> dict:
        """指定期間の全大会結果を一括取得してDBに保存。

        Args:
            start_year: 取得開始年
            end_year: 取得終了年（デフォルト: 現在年-1）

        Returns:
            {year: {tournaments_fetched, total_results}}
        """
        if end_year is None:
            end_year = datetime.now().year - 1

        summary: dict[int, dict] = {}

        for year in range(start_year, end_year + 1):
            print(f"\n{'='*60}")
            print(f"  Year {year}")
            print(f"{'='*60}")

            # スケジュール取得
            print(f"[INFO] Fetching schedule for {year}...")
            tournaments = self.fetch_schedule(year)
            if not tournaments:
                print(f"[WARN] No tournaments found for {year}")
                summary[year] = {"tournaments_fetched": 0, "total_results": 0}
                continue

            print(f"[OK] Found {len(tournaments)} tournaments")

            # 大会マスタ保存
            self.db.save_tournaments(tournaments)

            year_results = 0
            tournaments_fetched = 0

            for idx, t in enumerate(tournaments, 1):
                tid = t["tournament_id"]
                name = t["tournament_name"]

                # 既にDBにある場合はスキップ
                if self.db.has_tournament_results(tid, year):
                    print(f"  [{idx}/{len(tournaments)}] {name}: cached (skip)")
                    tournaments_fetched += 1
                    continue

                print(f"  [{idx}/{len(tournaments)}] {name}...", end=" ")

                results = self.fetch_tournament_results(tid, year)
                if results:
                    saved = self.db.save_tournament_results(results)
                    with_money = sum(1 for r in results if r.get("prize_money"))
                    print(f"{len(results)} players ({with_money} with prize money)")
                    year_results += saved
                    tournaments_fetched += 1
                else:
                    print("no data")

            summary[year] = {
                "tournaments_fetched": tournaments_fetched,
                "total_results": year_results,
            }
            print(f"\n[OK] Year {year}: {tournaments_fetched} tournaments, {year_results} new results")

        # サマリー表示
        print(f"\n{'='*60}")
        print(f"  Collection Summary ({start_year}-{end_year})")
        print(f"{'='*60}")
        total_t = sum(s["tournaments_fetched"] for s in summary.values())
        total_r = sum(s["total_results"] for s in summary.values())
        print(f"  Total Tournaments: {total_t}")
        print(f"  Total New Results: {total_r:,}")

        return summary


#-----CLI-----

def main() -> None:
    """CLIエントリポイント。"""
    parser = argparse.ArgumentParser(description="PGA Tour tournament data fetcher")
    parser.add_argument(
        "--schedule", type=int, metavar="YEAR",
        help="Fetch and display schedule for given year",
    )
    parser.add_argument(
        "--results", nargs=2, metavar=("TOURNAMENT_ID", "YEAR"),
        help="Fetch results for a specific tournament (e.g. R2024034 2024)",
    )
    parser.add_argument(
        "--collect", action="store_true",
        help="Collect historical tournament results",
    )
    parser.add_argument(
        "--start-year", type=int, default=2018,
        help="Start year for historical collection (default: 2018)",
    )
    parser.add_argument(
        "--end-year", type=int, default=None,
        help="End year for historical collection (default: last completed year)",
    )
    parser.add_argument(
        "--db-status", action="store_true",
        help="Show database status",
    )

    args = parser.parse_args()
    fetcher = TournamentFetcher()

    if args.db_status:
        fetcher.db.print_status()
        return

    if args.schedule:
        tournaments = fetcher.fetch_schedule(args.schedule)
        print(f"\nPGA Tour Schedule {args.schedule}: {len(tournaments)} tournaments")
        print(f"{'ID':<12} {'Tournament Name':<40} {'Course':<35} {'Location'}")
        print(f"{'-'*12} {'-'*40} {'-'*35} {'-'*30}")
        for t in tournaments:
            course = t.get("course_name", "") or ""
            city = t.get("city", "") or ""
            state = t.get("state_code", "") or ""
            location = f"{city}, {state}" if city and state else city or state
            print(f"{t['tournament_id']:<12} {t['tournament_name']:<40} {course:<35} {location}")
        return

    if args.results:
        tid, year_str = args.results
        year = int(year_str)
        results = fetcher.fetch_tournament_results(tid, year)
        if not results:
            print(f"No results found for {tid} ({year})")
            return

        print(f"\nResults for {tid} ({year}): {len(results)} players")
        print(f"{'Pos':<6} {'Player':<30} {'Score':<10} {'Prize Money':<15}")
        print(f"{'-'*6} {'-'*30} {'-'*10} {'-'*15}")
        for r in results[:20]:
            pos = r.get("position", "-") or "-"
            money = f"${r['prize_money']:,.0f}" if r.get("prize_money") else "-"
            print(f"{pos:<6} {r['player_name']:<30} {r.get('total_score', '-'):<10} {money:<15}")

        # DBに保存
        db = PGAStatsDB()
        saved = db.save_tournament_results(results)
        print(f"\n[OK] Saved {saved} results to database")
        return

    if args.collect:
        fetcher.collect_historical(
            start_year=args.start_year,
            end_year=args.end_year,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    main()
