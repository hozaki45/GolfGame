"""PGA Tour player statistics scraper.

This module fetches player performance statistics from various sources:
- PGA Tour GraphQL API (recommended - free, comprehensive, 2004-present)
- BALLDONTLIE API (fallback - free, OWGR only)
- PGA Tour website scraping (deprecated - JS rendering issues)
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import yaml
from fuzzywuzzy import fuzz

from .stats_models import PlayerStats, TournamentStats


#-----PGA Tour GraphQL API Client-----


class PGATourGraphQLClient:
    """PGA Tour GraphQL APIクライアント。

    orchestrator.pgatour.com/graphql から全統計データを取得。
    完全無料、2004年〜現在まで22年分のヒストリカルデータ対応。
    """

    GRAPHQL_URL = "https://orchestrator.pgatour.com/graphql"
    API_KEY = "da2-gsrx5bibzbb4njvhl7t37wqyl4"

    # 取得対象の統計ID一覧 (stat_id -> (field_name, weight))
    STAT_IDS = {
        "02568": ("sg_approach", 0.30),
        "02567": ("sg_off_tee", 0.25),
        "02674": ("sg_tee_to_green", 0.20),
        "02675": ("sg_total", 0.0),        # 参考用、予測には未使用
        "02564": ("sg_putting", 0.0),       # 参考用
        "02569": ("sg_around_green", 0.0),  # 参考用
        "103":   ("gir_pct", 0.10),
        "101":   ("driving_distance", 0.0), # 参考用
        "102":   ("driving_accuracy_pct", 0.0),  # 参考用
        "130":   ("scrambling_pct", 0.07),
        "120":   ("scoring_average", 0.08),
    }

    # 予測に使用する主要統計のみ
    PRIMARY_STAT_IDS = ["02568", "02567", "02674", "103", "130", "120"]

    def __init__(self, config: dict):
        """初期化。

        Args:
            config: config.yamlから読み込んだ設定辞書
        """
        self.config = config
        graphql_config = config.get("stats_source", {}).get("graphql", {})

        self.rate_limit_seconds = graphql_config.get("rate_limit_seconds", 1)
        self.timeout = graphql_config.get("timeout", 30)
        self.default_year = graphql_config.get("default_year", 2025)

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "x-api-key": self.API_KEY,
        })

        self.last_request_time = 0.0

        # データ保存ディレクトリ
        self.data_dir = Path(config.get("output", {}).get("data_dir", "data"))
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _rate_limit(self) -> None:
        """レート制限を遵守。"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)

    def _query_stat(self, stat_id: str, year: int) -> list[dict]:
        """単一の統計IDに対してGraphQLクエリを実行。

        Args:
            stat_id: PGA Tour統計ID (例: "02568")
            year: シーズン年

        Returns:
            選手データのリスト [{playerId, playerName, rank, statValue}, ...]
        """
        self._rate_limit()
        self.last_request_time = time.time()

        query = """
        {
            statDetails(tourCode: R, statId: "%s", year: %d) {
                statTitle
                rows {
                    ... on StatDetailsPlayer {
                        playerId
                        playerName
                        rank
                        stats {
                            statName
                            statValue
                        }
                    }
                }
            }
        }
        """ % (stat_id, year)

        try:
            response = self.session.post(
                self.GRAPHQL_URL,
                json={"query": query},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            stat_details = data.get("data", {}).get("statDetails", {})
            rows = stat_details.get("rows", [])
            title = stat_details.get("statTitle", "")

            # 各行からプライマリ統計値を抽出
            results = []
            for row in rows:
                player_id = row.get("playerId", "")
                player_name = row.get("playerName", "")
                rank = row.get("rank")
                stats_list = row.get("stats", [])

                # 最初のstat（メイン値）を取得
                stat_value = None
                measured_rounds = None
                if stats_list:
                    # statValue はカンマ区切りの数値文字列
                    raw_val = stats_list[0].get("statValue", "")
                    try:
                        stat_value = float(raw_val.replace(",", "").replace("%", ""))
                    except (ValueError, AttributeError):
                        pass

                    # 3番目のstat がMeasured Rounds（存在する場合）
                    if len(stats_list) >= 3:
                        raw_rounds = stats_list[2].get("statValue", "")
                        try:
                            measured_rounds = int(raw_rounds.replace(",", ""))
                        except (ValueError, AttributeError):
                            pass

                if player_name and stat_value is not None:
                    results.append({
                        "player_id": player_id,
                        "player_name": player_name,
                        "rank": rank,
                        "stat_value": stat_value,
                        "measured_rounds": measured_rounds,
                    })

            return results

        except Exception as e:
            print(f"[ERROR] GraphQL query failed for stat {stat_id}, year {year}: {e}")
            return []

    def fetch_all_stats_for_year(
        self, year: int, stat_ids: list[str] | None = None
    ) -> dict[str, list[dict]]:
        """指定年の全統計データを取得。

        Args:
            year: シーズン年
            stat_ids: 取得する統計IDリスト。Noneの場合は全11統計

        Returns:
            {stat_id: [{player_id, player_name, rank, stat_value, measured_rounds}, ...]}
        """
        if stat_ids is None:
            stat_ids = list(self.STAT_IDS.keys())

        all_data: dict[str, list[dict]] = {}

        for idx, stat_id in enumerate(stat_ids, 1):
            field_name = self.STAT_IDS.get(stat_id, (stat_id, 0))[0]
            print(f"[INFO] Fetching {field_name} (stat {stat_id}) ({idx}/{len(stat_ids)})...")

            rows = self._query_stat(stat_id, year)
            all_data[stat_id] = rows

            if rows:
                print(f"[OK]   {len(rows)} players")
            else:
                print(f"[WARN] No data for stat {stat_id} in {year}")

        return all_data

    def build_player_stats(
        self,
        year: int,
        player_names: list[str] | None = None,
        all_data: dict[str, list[dict]] | None = None,
    ) -> list[PlayerStats]:
        """統計データからPlayerStatsオブジェクトを構築。

        Args:
            year: シーズン年
            player_names: フィルタする選手名リスト。Noneの場合は全選手
            all_data: 事前取得済みデータ。Noneの場合はAPIから取得

        Returns:
            PlayerStatsオブジェクトのリスト
        """
        if all_data is None:
            all_data = self.fetch_all_stats_for_year(year)

        # 全選手名を収集（全統計からユニークな選手名を集約）
        all_players: dict[str, dict] = {}  # player_name -> {field_name: value}
        for stat_id, rows in all_data.items():
            field_name = self.STAT_IDS.get(stat_id, (stat_id, 0))[0]
            for row in rows:
                pname = row["player_name"]
                if pname not in all_players:
                    all_players[pname] = {"player_id": row["player_id"]}
                all_players[pname][field_name] = row["stat_value"]

        # player_namesが指定されている場合、fuzzy matchingでフィルタ
        if player_names:
            filtered: dict[str, dict] = {}
            for target_name in player_names:
                target_lower = target_name.lower().strip()

                # 完全一致を試行
                matched = None
                for pname in all_players:
                    if pname.lower().strip() == target_lower:
                        matched = pname
                        break

                # ファジーマッチ
                if not matched:
                    best_score = 0
                    for pname in all_players:
                        score = max(
                            fuzz.ratio(target_lower, pname.lower()),
                            fuzz.token_sort_ratio(target_lower, pname.lower()),
                        )
                        if score > best_score:
                            best_score = score
                            if score >= 80:
                                matched = pname

                if matched:
                    filtered[target_name] = all_players[matched]
                else:
                    # データなしでも空のエントリ作成
                    filtered[target_name] = {}

            build_from = filtered
        else:
            build_from = all_players

        # PlayerStatsオブジェクト構築
        results = []
        for pname, stats_dict in build_from.items():
            ps = PlayerStats(
                name=pname,
                sg_approach=stats_dict.get("sg_approach"),
                sg_off_tee=stats_dict.get("sg_off_tee"),
                sg_tee_to_green=stats_dict.get("sg_tee_to_green"),
                sg_total=stats_dict.get("sg_total"),
                sg_putting=stats_dict.get("sg_putting"),
                sg_around_green=stats_dict.get("sg_around_green"),
                greens_in_regulation_pct=stats_dict.get("gir_pct"),
                driving_distance=stats_dict.get("driving_distance"),
                driving_accuracy_pct=stats_dict.get("driving_accuracy_pct"),
                scrambling_pct=stats_dict.get("scrambling_pct"),
                scoring_average=stats_dict.get("scoring_average"),
                data_source="pgatour_graphql",
            )
            results.append(ps)

        return results

    def fetch_player_stats(self, player_names: list[str]) -> list[PlayerStats]:
        """パイプライン互換: 選手リストの統計データを取得。

        データベースキャッシュと連携して動作:
        1. DBにキャッシュがあればそれを使用
        2. なければAPIから取得してDBに保存

        Args:
            player_names: 選手名リスト

        Returns:
            PlayerStatsオブジェクトのリスト
        """
        from .pga_stats_db import PGAStatsDB

        db = PGAStatsDB()
        year = self.default_year

        # キャッシュから取得を試行
        cached = db.get_player_stats_for_year(year, player_names)
        if cached:
            with_data = sum(1 for ps in cached if ps.has_sufficient_data())
            print(f"[INFO] Using cached PGA Tour stats ({len(cached)} players, {with_data} with data)")
            return cached

        # APIから全統計を一括取得
        print(f"[INFO] Fetching PGA Tour stats via GraphQL API (year={year})...")
        all_data = self.fetch_all_stats_for_year(year, stat_ids=self.PRIMARY_STAT_IDS)

        # DBに保存
        db.save_stats_bulk(year, all_data, self.STAT_IDS)
        print(f"[OK] Saved to local database cache")

        # 指定選手のPlayerStatsを構築
        results = self.build_player_stats(year, player_names, all_data)
        with_data = sum(1 for ps in results if ps.has_sufficient_data())
        print(f"[OK] Built stats for {len(results)} players ({with_data} with sufficient data)")

        return results

    def fetch_historical(
        self,
        start_year: int = 2004,
        end_year: int | None = None,
        stat_ids: list[str] | None = None,
        all_stats: bool = False,
    ) -> None:
        """ヒストリカルデータを一括取得してDBに保存。

        Args:
            start_year: 取得開始年（デフォルト: 2004）
            end_year: 取得終了年（デフォルト: 現在年）
            stat_ids: 取得する統計IDリスト（デフォルト: 主要6統計）
            all_stats: Trueの場合は全11統計を取得（回帰分析用）
        """
        from .pga_stats_db import PGAStatsDB

        if end_year is None:
            end_year = datetime.now().year
        if stat_ids is None:
            stat_ids = list(self.STAT_IDS.keys()) if all_stats else self.PRIMARY_STAT_IDS

        db = PGAStatsDB()

        for year in range(start_year, end_year + 1):
            # 既にキャッシュ済みかチェック
            missing_stats = db.get_missing_stats(year, stat_ids)
            if not missing_stats:
                print(f"[INFO] Year {year}: already cached (skipping)")
                continue

            print(f"\n[INFO] Year {year}: fetching {len(missing_stats)} stat categories...")
            data = self.fetch_all_stats_for_year(year, stat_ids=missing_stats)
            db.save_stats_bulk(year, data, self.STAT_IDS)

            total_rows = sum(len(rows) for rows in data.values())
            print(f"[OK] Year {year}: saved {total_rows} player records")

        print(f"\n[OK] Historical data fetch complete ({start_year}-{end_year})")


#-----BALLDONTLIE API Client (Legacy)-----


class BallDontLieAPIClient:
    """Client for BALLDONTLIE PGA API.

    This API provides comprehensive PGA Tour statistics including
    strokes gained metrics in JSON format.
    """

    def __init__(self, config: dict):
        """Initialize the API client.

        Args:
            config: Configuration dictionary with api settings
        """
        self.api_config = config.get("stats_source", {}).get("api", {})

        self.api_key = self.api_config.get("balldontlie_api_key", "")
        self.base_url = self.api_config.get(
            "balldontlie_base_url",
            "https://api.balldontlie.io/pga/v1"
        )
        self.season = self.api_config.get("balldontlie_season", 2024)

        self.session = requests.Session()
        if self.api_key:
            # Try both Bearer token and direct API key formats
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "X-API-Key": self.api_key,
            })

        # Data directory for raw output
        self.data_dir = Path(config.get("output", {}).get("data_dir", "data"))
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _match_player_name(
        self, target_name: str, available_players: list[dict], threshold: int = 80
    ) -> Optional[dict]:
        """Match player name using fuzzy matching.

        Args:
            target_name: Name to match
            available_players: List of player dicts with 'first_name' and 'last_name'
            threshold: Minimum fuzzy match score (0-100)

        Returns:
            Best matching player dict if score >= threshold, None otherwise
        """
        best_match = None
        best_score = 0

        target_lower = target_name.lower()

        for player in available_players:
            # Construct full name
            full_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()

            # Try both ratio and token_sort_ratio
            score1 = fuzz.ratio(target_lower, full_name.lower())
            score2 = fuzz.token_sort_ratio(target_lower, full_name.lower())
            score = max(score1, score2)

            if score > best_score:
                best_score = score
                best_match = player

        if best_score >= threshold:
            return best_match
        return None

    def _search_player(self, player_name: str) -> Optional[dict]:
        """Search for a player by name and return player data.

        Args:
            player_name: Player name to search for

        Returns:
            Player dict with 'id', 'first_name', 'last_name', etc. or None
        """
        try:
            # BALLDONTLIE API player search endpoint
            url = f"{self.base_url}/players"
            params = {"search": player_name, "per_page": 10}

            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            players = data.get("data", [])

            if not players:
                return None

            # If multiple results, use fuzzy matching to find best match
            if len(players) == 1:
                return players[0]

            # Use fuzzy matching
            matched = self._match_player_name(player_name, players)
            return matched

        except Exception as e:
            print(f"[WARN] Failed to search player '{player_name}': {e}")
            return None

    def _fetch_season_averages(self, player_id: int) -> Optional[dict]:
        """Fetch season averages for a player.

        Args:
            player_id: BALLDONTLIE player ID

        Returns:
            Season averages dict or None
        """
        try:
            # BALLDONTLIE API season averages endpoint
            url = f"{self.base_url}/season_averages"
            params = {
                "season": self.season,
                "player_ids[]": player_id,
            }

            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            averages = data.get("data", [])

            if averages:
                return averages[0]
            return None

        except Exception as e:
            print(f"[WARN] Failed to fetch season averages for player {player_id}: {e}")
            return None

    def _fetch_all_players_bulk(self, per_page: int = 100) -> dict[str, dict]:
        """Fetch all players in bulk (Free tier optimization).

        Args:
            per_page: Number of players per page (max 100)

        Returns:
            Dict mapping player name (lowercase) to player data
        """
        try:
            url = f"{self.base_url}/players"
            params = {"per_page": per_page}

            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            players = data.get("data", [])

            # Create lookup dict
            player_lookup = {}
            for p in players:
                display_name = p.get("display_name", "").lower().strip()
                if display_name:
                    player_lookup[display_name] = p

            print(f"[OK] Fetched {len(player_lookup)} players in bulk (1 request)")
            return player_lookup

        except Exception as e:
            print(f"[WARN] Bulk player fetch failed: {e}")
            return {}

    def fetch_player_stats(self, player_names: list[str]) -> list[PlayerStats]:
        """Fetch statistics for a list of players.

        Args:
            player_names: List of player names to fetch stats for

        Returns:
            List of PlayerStats objects
        """
        if not self.api_key:
            print("[ERROR] BALLDONTLIE API key not configured")
            print("[INFO] Get a free key at https://app.balldontlie.io")
            return [PlayerStats(name=name, data_source="balldontlie_api") for name in player_names]

        print(f"[INFO] Fetching player data from BALLDONTLIE API (Free tier - OWGR-based)")
        print(f"[INFO] Free tier uses OWGR (world ranking) as skill indicator")

        results = []
        found_count = 0

        # For each player, use search endpoint (most efficient for Free tier)
        for i, name in enumerate(player_names, 1):
            # Rate limiting: 5 req/min
            if i > 1:
                time.sleep(13)  # Wait 13 seconds between requests

            player_data = self._search_player(name)

            if not player_data:
                print(f"[WARN] ({i}/{len(player_names)}) Player not found: '{name}'")
                results.append(PlayerStats(
                    name=name,
                    data_source="balldontlie_api_free",
                ))
                continue

            # Extract OWGR (world ranking)
            owgr = player_data.get("owgr")
            display_name = player_data.get("display_name", name)

            if owgr is None or owgr == "":
                print(f"[WARN] ({i}/{len(player_names)}) No OWGR for {display_name}")
                results.append(PlayerStats(
                    name=name,
                    data_source="balldontlie_api_free",
                ))
                continue

            # Create PlayerStats with OWGR as primary indicator
            # Store OWGR in scoring_average field temporarily
            # (Lower OWGR = better player, will be inverted in analyzer)
            stats = PlayerStats(
                name=name,
                data_source="balldontlie_api_free",
                scoring_average=float(owgr) if owgr else None,  # OWGR storage
            )

            results.append(stats)
            found_count += 1
            print(f"[OK] ({i}/{len(player_names)}) {display_name}: OWGR #{int(owgr)}")

        print(f"[OK] Successfully fetched data for {found_count}/{len(player_names)} players")

        return results


class PGATourStatsScraper:
    """Scraper for PGA Tour player statistics.

    This class fetches player performance metrics from pgatour.com/stats,
    including Strokes Gained metrics, GIR%, driving stats, and more.
    """

    # Stat categories with their URL suffixes and field names
    STAT_CATEGORIES = {
        "sg_approach": "/stat.02568.html",
        "sg_off_tee": "/stat.02567.html",
        "sg_tee_to_green": "/stat.02674.html",
        "sg_total": "/stat.02675.html",
        "sg_putting": "/stat.02564.html",
        "sg_around_green": "/stat.02569.html",
        "gir_pct": "/stat.103.html",
        "driving_distance": "/stat.101.html",
        "driving_accuracy_pct": "/stat.102.html",
        "scrambling_pct": "/stat.130.html",
        "scoring_average": "/stat.120.html",
    }

    def __init__(self, config: dict):
        """Initialize the scraper with configuration.

        Args:
            config: Configuration dictionary with stats_source settings
        """
        self.config = config.get("stats_source", {})
        self.scraping_config = self.config.get("scraping", {})

        self.base_url = self.scraping_config.get(
            "base_url", "https://www.pgatour.com/stats"
        )
        self.timeout = self.scraping_config.get("timeout", 30)
        self.rate_limit_seconds = self.scraping_config.get("rate_limit_seconds", 2)
        self.max_retries = self.scraping_config.get("max_retries", 3)
        self.user_agent = self.scraping_config.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )

        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

        # Data directory for raw output
        self.data_dir = Path(config.get("output", {}).get("data_dir", "data"))
        self.raw_dir = self.data_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _rate_limited_request(self, url: str) -> requests.Response:
        """Make a rate-limited HTTP request.

        Args:
            url: URL to fetch

        Returns:
            Response object

        Raises:
            requests.exceptions.RequestException: On request failure
        """
        # Wait to respect rate limit
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)

        # Make request with retries
        for attempt in range(self.max_retries):
            try:
                self.last_request_time = time.time()
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise
                print(f"[WARN] Request failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                time.sleep(2 ** attempt)  # Exponential backoff

        raise requests.exceptions.RequestException("Max retries exceeded")

    def _scrape_stat_page(self, stat_name: str, url_suffix: str) -> dict[str, float]:
        """Scrape a single stats page from PGA Tour using Playwright.

        Args:
            stat_name: Name of the statistic (e.g., "sg_approach")
            url_suffix: URL suffix (e.g., "/stat.02568.html")

        Returns:
            Dict mapping player name (lowercase) to stat value
        """
        if not PLAYWRIGHT_AVAILABLE:
            print("[ERROR] Playwright not installed. Install with: playwright install chromium")
            return {}

        url = f"{self.base_url}{url_suffix}"
        print(f"[INFO] Scraping {stat_name} from {url}")

        # Rate limiting
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self.last_request_time = time.time()

        try:
            with sync_playwright() as p:
                # Launch browser in headless mode
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)

                # Navigate to URL
                page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)

                # Wait for table to be rendered
                # PGA Tour may use different selectors, try multiple
                selectors = [
                    "table",  # Generic table
                    "[data-cy='player-stats-table']",  # React data attribute
                    ".table-styled",  # Class name
                    "div[role='table']",  # ARIA table
                ]

                table_found = False
                for selector in selectors:
                    try:
                        page.wait_for_selector(selector, timeout=5000, state="visible")
                        table_found = True
                        print(f"[DEBUG] Found table with selector: {selector}")
                        break
                    except:
                        continue

                if not table_found:
                    print(f"[WARN] No table found for {stat_name} (tried {len(selectors)} selectors)")
                    browser.close()
                    return {}

                # Get rendered HTML
                html = page.content()
                browser.close()

            # Parse with BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Try to find table with various selectors
            table = None
            for tag in ["table", "div"]:
                for attr, value in [
                    ("class", "table-styled"),
                    ("data-cy", "player-stats-table"),
                    ("role", "table"),
                ]:
                    table = soup.find(tag, {attr: value})
                    if table:
                        break
                if table:
                    break

            if not table:
                # Generic table fallback
                table = soup.find("table")

            if not table:
                print(f"[WARN] Could not parse table structure for {stat_name}")
                return {}

            # Extract rows (skip header)
            rows = table.find_all("tr")[1:] if table.name == "table" else table.find_all(attrs={"role": "row"})[1:]

            stats = {}
            for row in rows:
                cells = row.find_all("td") if table.name == "table" else row.find_all(attrs={"role": "cell"})
                if len(cells) < 3:
                    continue

                try:
                    # Typical structure: [Rank, Player Name, Value, ...]
                    player_cell = cells[1]
                    value_cell = cells[2]

                    # Extract player name
                    player_name = player_cell.get_text(strip=True)

                    # Extract stat value
                    stat_value_str = value_cell.get_text(strip=True)

                    # Parse value (handle percentages, commas)
                    stat_value_str = stat_value_str.replace(",", "").replace("%", "")
                    stat_value = float(stat_value_str)

                    stats[player_name.lower()] = stat_value

                except (ValueError, IndexError, AttributeError) as e:
                    print(f"[DEBUG] Failed to parse row for {stat_name}: {e}")
                    continue

            print(f"[OK] Found {len(stats)} players for {stat_name}")
            return stats

        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to fetch {stat_name}: {e}")
            return {}
        except Exception as e:
            print(f"[ERROR] Unexpected error scraping {stat_name}: {e}")
            return {}

    def _match_player_name(
        self, target_name: str, available_names: list[str], threshold: int = 80
    ) -> Optional[str]:
        """Match player name using fuzzy matching.

        Args:
            target_name: Name to match
            available_names: List of available names
            threshold: Minimum fuzzy match score (0-100)

        Returns:
            Best matching name if score >= threshold, None otherwise
        """
        best_match = None
        best_score = 0

        target_lower = target_name.lower()

        for name in available_names:
            # Try both ratio and token_sort_ratio
            score1 = fuzz.ratio(target_lower, name.lower())
            score2 = fuzz.token_sort_ratio(target_lower, name.lower())
            score = max(score1, score2)

            if score > best_score:
                best_score = score
                best_match = name

        if best_score >= threshold:
            return best_match
        return None

    def fetch_player_stats(self, player_names: list[str]) -> list[PlayerStats]:
        """Fetch statistics for a list of players.

        Args:
            player_names: List of player names to fetch stats for

        Returns:
            List of PlayerStats objects (may have None values if data missing)
        """
        print(f"[INFO] Fetching PGA Tour stats for {len(player_names)} players...")

        # Fetch all stat categories
        all_stats = {}
        stat_count = len(self.STAT_CATEGORIES)

        for idx, (stat_name, url_suffix) in enumerate(self.STAT_CATEGORIES.items(), 1):
            print(f"[INFO] Scraping {stat_name} ({idx}/{stat_count})...")
            all_stats[stat_name] = self._scrape_stat_page(stat_name, url_suffix)

        # Build PlayerStats for each player
        results = []
        fuzzy_threshold = self.config.get("matching", {}).get("fuzzy_threshold", 80)

        for player_name in player_names:
            stats = PlayerStats(name=player_name, data_source="pgatour_scraping")

            # Match player name to stats using fuzzy matching
            for stat_name, stat_dict in all_stats.items():
                if not stat_dict:
                    continue

                # Try exact match first (case-insensitive)
                matched_name = None
                if player_name.lower() in stat_dict:
                    matched_name = player_name.lower()
                else:
                    # Fuzzy match
                    matched_name = self._match_player_name(
                        player_name, list(stat_dict.keys()), fuzzy_threshold
                    )

                if matched_name:
                    stat_value = stat_dict[matched_name]

                    # Set the appropriate field
                    if stat_name == "sg_approach":
                        stats.sg_approach = stat_value
                    elif stat_name == "sg_off_tee":
                        stats.sg_off_tee = stat_value
                    elif stat_name == "sg_tee_to_green":
                        stats.sg_tee_to_green = stat_value
                    elif stat_name == "sg_total":
                        stats.sg_total = stat_value
                    elif stat_name == "sg_putting":
                        stats.sg_putting = stat_value
                    elif stat_name == "sg_around_green":
                        stats.sg_around_green = stat_value
                    elif stat_name == "gir_pct":
                        stats.greens_in_regulation_pct = stat_value
                    elif stat_name == "driving_distance":
                        stats.driving_distance = stat_value
                    elif stat_name == "driving_accuracy_pct":
                        stats.driving_accuracy_pct = stat_value
                    elif stat_name == "scrambling_pct":
                        stats.scrambling_pct = stat_value
                    elif stat_name == "scoring_average":
                        stats.scoring_average = stat_value

            results.append(stats)

        # Log summary
        with_data = sum(1 for s in results if s.has_sufficient_data())
        print(f"[OK] Fetched stats for {len(results)} players ({with_data} with sufficient data)")

        return results

    def run(self, player_names: list[str], tournament_name: str = "Unknown") -> TournamentStats:
        """Run the scraper and return tournament stats.

        Args:
            player_names: List of player names
            tournament_name: Name of the tournament

        Returns:
            TournamentStats object with all player stats
        """
        players = self.fetch_player_stats(player_names)

        tournament_stats = TournamentStats(
            tournament_name=tournament_name,
            players=players,
            fetched_at=datetime.utcnow().isoformat(),
            source="pgatour_scraping",
        )

        # Save to JSON for debugging
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = self.raw_dir / f"stats_{timestamp}.json"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(tournament_stats.to_dict(), f, indent=2, ensure_ascii=False)

        print(f"[OK] Saved raw stats to {json_path}")

        return tournament_stats


def create_stats_client(config: dict):
    """Factory function to create appropriate stats client based on config.

    Args:
        config: Configuration dictionary

    Returns:
        Stats client instance
    """
    provider = config.get("stats_source", {}).get("provider", "pgatour_graphql")

    if provider == "pgatour_graphql":
        print("[INFO] Using PGA Tour GraphQL API provider")
        return PGATourGraphQLClient(config)
    elif provider == "balldontlie_api":
        print("[INFO] Using BALLDONTLIE API provider")
        return BallDontLieAPIClient(config)
    elif provider == "pgatour_scraping":
        print("[INFO] Using PGA Tour web scraping provider")
        return PGATourStatsScraper(config)
    else:
        print(f"[WARN] Unknown provider '{provider}', defaulting to PGA Tour GraphQL")
        return PGATourGraphQLClient(config)


def main() -> None:
    """CLI entry point for standalone testing."""
    parser = argparse.ArgumentParser(description="Fetch PGA Tour player statistics")
    parser.add_argument(
        "--players",
        type=str,
        default="",
        help="Comma-separated list of player names",
    )
    parser.add_argument(
        "--output", type=str, default="data/test_stats.json", help="Output JSON file"
    )
    parser.add_argument(
        "--config", type=str, default="config.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["pgatour_graphql", "balldontlie_api", "pgatour_scraping"],
        help="Override config provider setting"
    )
    parser.add_argument(
        "--historical",
        action="store_true",
        help="Fetch historical data (2004-present) and save to local DB"
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2004,
        help="Start year for historical fetch (default: 2004)"
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="End year for historical fetch (default: current year)"
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Season year for stats fetch (default: from config)"
    )
    parser.add_argument(
        "--all-stats",
        action="store_true",
        help="Fetch all 11 stats (default: 6 primary stats). Required for regression analysis"
    )
    parser.add_argument(
        "--db-status",
        action="store_true",
        help="Show local database cache status"
    )

    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    else:
        print(f"[WARN] Config file not found: {config_path}, using defaults")
        config = {"stats_source": {"provider": "pgatour_graphql"}, "output": {"data_dir": "data"}}

    # Override provider if specified
    if args.provider:
        config["stats_source"]["provider"] = args.provider

    # Override year if specified
    if args.year:
        config.setdefault("stats_source", {}).setdefault("graphql", {})["default_year"] = args.year

    # DB status mode
    if args.db_status:
        from .pga_stats_db import PGAStatsDB
        db = PGAStatsDB()
        db.print_status()
        return

    # Historical fetch mode
    if args.historical:
        client = PGATourGraphQLClient(config)
        client.fetch_historical(
            start_year=args.start_year,
            end_year=args.end_year,
            all_stats=args.all_stats,
        )
        return

    # Normal fetch mode
    if not args.players:
        parser.error("--players is required (unless using --historical or --db-status)")

    # Parse player names
    player_names = [name.strip() for name in args.players.split(",")]

    # Create client based on provider
    client = create_stats_client(config)

    # Fetch stats
    players = client.fetch_player_stats(player_names)

    # Create tournament stats
    tournament_stats = TournamentStats(
        tournament_name="Test Tournament",
        players=players,
        fetched_at=datetime.utcnow().isoformat(),
        source=config.get("stats_source", {}).get("provider", "unknown"),
    )

    # Save output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(tournament_stats.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Saved stats to {output_path}")
    print(f"[INFO] Players with sufficient data: {tournament_stats.players_with_sufficient_data}/{tournament_stats.player_count}")


if __name__ == "__main__":
    main()
