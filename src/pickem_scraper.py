"""Pick'em 履歴データスクレイパー。

jflynn87.pythonanywhere.com から過去の大会スコア・ピックデータを取得し、
golfgame.db の pickem_* テーブルに保存する。

Usage:
    uv run python -m src.pickem_scraper                    # 全PK (1-410)
    uv run python -m src.pickem_scraper --pk 409           # 単一大会
    uv run python -m src.pickem_scraper --range 400 410    # PK範囲指定
    uv run python -m src.pickem_scraper --resume           # 未取得のみ
    uv run python -m src.pickem_scraper --status           # 取得状況表示
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from src.database import get_connection


#-----Constants-----

BASE_URL = "http://jflynn87.pythonanywhere.com"
LOGIN_URL = f"{BASE_URL}/accounts/login/"
SCORES_URL_TEMPLATE = f"{BASE_URL}/golf_app/api_scores_view/{{pk}}"
CSV_URL_TEMPLATE = f"{BASE_URL}/golf_app/field_csv_signed_url?pk={{pk}}"

# PK 1-410 が有効範囲
DEFAULT_PK_MIN = 1
DEFAULT_PK_MAX = 410

# レート制限（秒）
RATE_LIMIT = 1.0


def _safe_int(value: str | None) -> int | None:
    """文字列を安全にintに変換。"""
    if value is None:
        return None
    value = value.strip()
    if not value or value == "n/a":
        return None
    try:
        return int(value)
    except ValueError:
        return None


#-----Data Classes-----

@dataclass
class UserPick:
    """ユーザーの1グループ分のピック。"""
    username: str
    group_num: int
    picked_player: str


@dataclass
class UserScore:
    """ユーザーの大会スコア。"""
    username: str
    total_score: int
    bonus: int = 0


@dataclass
class TournamentData:
    """1大会分のパース結果。"""
    pk: int
    name: str
    picks: list[UserPick] = field(default_factory=list)
    scores: list[UserScore] = field(default_factory=list)
    num_groups: int = 0
    num_users: int = 0


@dataclass
class FieldPlayer:
    """CSVから取得したフィールド選手データ。"""
    espn_id: str
    player_name: str
    group_id: int
    current_wgr: int | None = None
    sow_wgr: int | None = None
    soy_wgr: int | None = None
    prior_year_finish: str = ""
    handicap: int | None = None
    fedex_rank: int | None = None
    fedex_points: int | None = None
    season_played: int | None = None
    season_won: int | None = None
    season_top10: int | None = None
    season_top29: int | None = None
    season_top49: int | None = None
    season_over50: int | None = None
    season_cut: int | None = None
    tournament_history: dict = field(default_factory=dict)


@dataclass
class ScrapeResult:
    """スクレイピング全体の結果サマリー。"""
    total_attempted: int = 0
    total_scraped: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    total_picks: int = 0
    total_scores: int = 0


#-----Scraper-----

class PickemScraper:
    """Pick'em 履歴データスクレイパー。"""

    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        picks_cfg = config.get("picks_site", {})
        self.username = picks_cfg.get("username", "")
        self.password = picks_cfg.get("password", "")
        self.timeout = picks_cfg.get("timeout", 30)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        self._logged_in = False

    def login(self) -> bool:
        """Django CSRF認証でログイン。"""
        print("[INFO] Logging into jflynn87.pythonanywhere.com...")
        try:
            resp = self.session.get(LOGIN_URL, timeout=self.timeout)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
            if not csrf_input:
                print("[ERROR] CSRF token not found on login page")
                return False

            csrf_token = csrf_input["value"]
            login_data = {
                "csrfmiddlewaretoken": csrf_token,
                "username": self.username,
                "password": self.password,
                "next": "/",
            }
            resp = self.session.post(
                LOGIN_URL,
                data=login_data,
                headers={"Referer": LOGIN_URL},
                timeout=self.timeout,
            )

            if "login" in resp.url.lower() and resp.status_code == 200:
                print("[ERROR] Login failed - check credentials")
                return False

            print("[OK] Login successful")
            self._logged_in = True
            return True

        except requests.RequestException as e:
            print(f"[ERROR] Login request failed: {e}")
            return False

    def _ensure_logged_in(self) -> bool:
        """ログイン状態を確認し、必要なら再ログイン。"""
        if self._logged_in:
            return True
        return self.login()

    def scrape_tournament(self, pk: int) -> TournamentData | None:
        """単一大会のデータをスクレイピング。

        Args:
            pk: PythonAnywhere上の大会PK

        Returns:
            パース結果 or None（404/空/エラー時）
        """
        if not self._ensure_logged_in():
            return None

        url = SCORES_URL_TEMPLATE.format(pk=pk)
        try:
            resp = self.session.get(url, timeout=self.timeout)

            if resp.status_code == 404:
                return None
            if resp.status_code == 500:
                return None
            resp.raise_for_status()

            # ログインページにリダイレクトされた場合は再ログイン
            if "login" in resp.url.lower():
                print("[WARN] Session expired, re-logging in...")
                self._logged_in = False
                if not self.login():
                    return None
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()

            return self._parse_scores_page(resp.text, pk)

        except requests.RequestException as e:
            print(f"[ERROR] PK {pk}: Request failed - {e}")
            return None

    def _parse_scores_page(self, html: str, pk: int) -> TournamentData | None:
        """HTMLをパースして大会データを抽出。

        パース戦略:
        1. <h2> タグから大会名を取得
        2. テーブルの各行をパース
        3. グループ列（2列目以降）から username と group_num, picked_player を抽出
        4. Player列から username と total_score を抽出（username はグループ列から既知）
        """
        soup = BeautifulSoup(html, "html.parser")

        # 大会名を取得（<h3> タグに格納）
        h3 = soup.find("h3")
        if not h3:
            return None
        tournament_name = h3.get_text(strip=True)
        if not tournament_name or "error" in tournament_name.lower():
            return None

        # テーブルを検索
        table = soup.find("table")
        if not table:
            return None

        rows = table.find_all("tr")
        if len(rows) < 2:
            return None

        # ヘッダー解析: 列数からグループ数を推定
        header_row = rows[0]
        headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]

        # ヘッダー例: ["Player", "Bonus", "1", "2", "3", ..., "9"]
        # グループ列はヘッダーが数字の列
        group_columns: list[int] = []
        for i, h in enumerate(headers):
            if h.isdigit():
                group_columns.append(i)

        num_groups = len(group_columns)
        if num_groups == 0:
            return None

        # データ行をパース
        data = TournamentData(pk=pk, name=tournament_name, num_groups=num_groups)
        usernames_found: set[str] = set()

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]

            # Phase 1: グループ列からユーザー名とピックを抽出
            # 形式: "username : {group_num}{player_name}" or "username:{group_num}{player_name}"
            row_picks: list[UserPick] = []
            row_username: str | None = None

            for col_idx in group_columns:
                if col_idx >= len(cell_texts):
                    continue
                cell_text = cell_texts[col_idx]
                if not cell_text:
                    continue

                pick_info = self._parse_group_cell(cell_text)
                if pick_info:
                    uname, gnum, player = pick_info
                    row_username = uname
                    usernames_found.add(uname)
                    row_picks.append(UserPick(
                        username=uname,
                        group_num=gnum,
                        picked_player=player,
                    ))

            # Phase 2: Player列からスコアを抽出
            if row_username and len(cell_texts) > 0:
                player_cell = cell_texts[0]
                score = self._extract_score(player_cell, row_username)
                bonus = 0
                if len(cell_texts) > 1:
                    bonus_text = cell_texts[1].rstrip(".").strip()
                    if bonus_text.lstrip("-").isdigit():
                        try:
                            bonus = int(bonus_text)
                        except ValueError:
                            pass

                if score is not None:
                    data.scores.append(UserScore(
                        username=row_username,
                        total_score=score,
                        bonus=bonus,
                    ))

            data.picks.extend(row_picks)

        data.num_users = len(usernames_found)
        if data.num_users == 0 or len(data.picks) == 0:
            return None

        return data

    def _parse_group_cell(self, cell_text: str) -> tuple[str, int, str] | None:
        """グループセルをパースして (username, group_num, player_name) を返す。

        形式例:
        - "jcarl62 : 1Tommy Fleetwood...."
        - "jcarl62:1Tommy Fle"
        - "Hiro : 3Scottie Sc...."
        """
        # 末尾のドットを除去
        cell_text = cell_text.rstrip(".")

        # "username : {digit}{player}" or "username:{digit}{player}"
        match = re.match(r"^(.+?)\s*:\s*(\d+)(.+)$", cell_text)
        if not match:
            return None

        username = match.group(1).strip()
        group_num = int(match.group(2))
        player = match.group(3).strip()

        if not username or not player:
            return None

        return (username, group_num, player)

    def _extract_score(self, player_cell: str, known_username: str) -> int | None:
        """Player列からスコアを抽出。

        形式: "jcarl62276 / ..." (例: "jcarl62276 / ...")
        known_username を使って安全にスコア部分を分離。
        """
        # 末尾のドットを除去してから " / " で分割
        player_cell = player_cell.rstrip(".")
        parts = player_cell.split("/")
        first_part = parts[0].strip()

        # known_usernameで始まっているか確認
        if first_part.lower().startswith(known_username.lower()):
            score_str = first_part[len(known_username):]
            # 数字部分を抽出
            score_match = re.match(r"^(-?\d+)", score_str)
            if score_match:
                return int(score_match.group(1))

        return None

    def save_tournament_data(self, data: TournamentData) -> int | None:
        """パース結果をDBに保存。

        Returns:
            pickem_tournaments.id or None
        """
        conn = get_connection()
        try:
            now = datetime.now().isoformat()

            # pickem_tournaments に upsert
            existing = conn.execute(
                "SELECT id FROM pickem_tournaments WHERE pk = ?",
                (data.pk,)
            ).fetchone()

            if existing:
                pt_id = existing["id"]
                conn.execute(
                    """UPDATE pickem_tournaments
                       SET name=?, num_groups=?, num_users=?, scraped_at=?
                       WHERE id=?""",
                    (data.name, data.num_groups, data.num_users, now, pt_id),
                )
                # 既存データをクリア
                conn.execute("DELETE FROM pickem_scores WHERE pickem_tournament_id=?", (pt_id,))
                conn.execute("DELETE FROM pickem_picks WHERE pickem_tournament_id=?", (pt_id,))
            else:
                cur = conn.execute(
                    """INSERT INTO pickem_tournaments (pk, name, num_groups, num_users, scraped_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (data.pk, data.name, data.num_groups, data.num_users, now),
                )
                pt_id = cur.lastrowid

            # pickem_users に upsert
            for username in {s.username for s in data.scores} | {p.username for p in data.picks}:
                conn.execute(
                    """INSERT INTO pickem_users (username, first_seen_pk, last_seen_pk)
                       VALUES (?, ?, ?)
                       ON CONFLICT(username) DO UPDATE SET
                           first_seen_pk = MIN(first_seen_pk, excluded.first_seen_pk),
                           last_seen_pk = MAX(last_seen_pk, excluded.last_seen_pk)""",
                    (username, data.pk, data.pk),
                )

            # pickem_scores
            for s in data.scores:
                conn.execute(
                    """INSERT OR REPLACE INTO pickem_scores
                       (pickem_tournament_id, username, total_score, bonus)
                       VALUES (?, ?, ?, ?)""",
                    (pt_id, s.username, s.total_score, s.bonus),
                )

            # pickem_picks
            for p in data.picks:
                conn.execute(
                    """INSERT OR REPLACE INTO pickem_picks
                       (pickem_tournament_id, username, group_num, picked_player)
                       VALUES (?, ?, ?, ?)""",
                    (pt_id, p.username, p.group_num, p.picked_player),
                )

            conn.commit()
            return pt_id

        except sqlite3.Error as e:
            print(f"[ERROR] DB save failed for PK {data.pk}: {e}")
            conn.rollback()
            return None

        finally:
            conn.close()

    #-----CSV Field Data-----

    def scrape_field_csv(self, pk: int) -> list[FieldPlayer] | None:
        """PK指定でCSV（グループ構成 + 選手統計）を取得。

        Args:
            pk: PythonAnywhere上の大会PK

        Returns:
            FieldPlayerリスト or None（取得失敗時）
        """
        if not self._ensure_logged_in():
            return None

        url = CSV_URL_TEMPLATE.format(pk=pk)
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return None

            data = resp.json()
            csv_url = data.get("url", "")
            if not csv_url:
                return None

            csv_resp = self.session.get(csv_url, timeout=self.timeout)
            if csv_resp.status_code != 200:
                return None

            return self._parse_field_csv(csv_resp.text)

        except (requests.RequestException, ValueError) as e:
            print(f"[ERROR] PK {pk}: CSV fetch failed - {e}")
            return None

    def _parse_field_csv(self, csv_text: str) -> list[FieldPlayer]:
        """CSVテキストをFieldPlayerリストにパース。"""
        reader = csv.DictReader(io.StringIO(csv_text))
        players: list[FieldPlayer] = []

        # 固定カラム名
        fixed_keys = {
            "ESPN ID", "Golfer", "Group ID", "currentWGR", "sow_WGR", "soy_WGR",
            "prior year finish", "handicap", "FedEx Rank", "FedEx Points",
            "Season Played", "Season Won", "Season 2-10", "Season 11-29",
            "Season 30 - 49", "Season > 50", "Season Cut",
        }

        for row in reader:
            golfer = row.get("Golfer", "").strip()
            if not golfer:
                continue

            # 大会履歴カラム（固定カラム以外）をJSON化
            history = {}
            for k, v in row.items():
                if k not in fixed_keys and v and v.strip() != "n/a":
                    history[k] = v.strip()

            players.append(FieldPlayer(
                espn_id=row.get("ESPN ID", ""),
                player_name=golfer,
                group_id=_safe_int(row.get("Group ID", "0")),
                current_wgr=_safe_int(row.get("currentWGR")),
                sow_wgr=_safe_int(row.get("sow_WGR")),
                soy_wgr=_safe_int(row.get("soy_WGR")),
                prior_year_finish=row.get("prior year finish", ""),
                handicap=_safe_int(row.get("handicap")),
                fedex_rank=_safe_int(row.get("FedEx Rank")),
                fedex_points=_safe_int(row.get("FedEx Points")),
                season_played=_safe_int(row.get("Season Played")),
                season_won=_safe_int(row.get("Season Won")),
                season_top10=_safe_int(row.get("Season 2-10")),
                season_top29=_safe_int(row.get("Season 11-29")),
                season_top49=_safe_int(row.get("Season 30 - 49")),
                season_over50=_safe_int(row.get("Season > 50")),
                season_cut=_safe_int(row.get("Season Cut")),
                tournament_history=history,
            ))

        return players

    def save_field_data(self, pk: int, players: list[FieldPlayer]) -> bool:
        """フィールドデータをDBに保存。

        Args:
            pk: 大会PK
            players: FieldPlayerリスト

        Returns:
            保存成功/失敗
        """
        conn = get_connection()
        try:
            # pickem_tournaments.id を取得
            row = conn.execute(
                "SELECT id FROM pickem_tournaments WHERE pk = ?", (pk,)
            ).fetchone()
            if not row:
                # ピックデータがない大会でもCSVだけ保存可能にする
                now = datetime.now().isoformat()
                cur = conn.execute(
                    """INSERT OR IGNORE INTO pickem_tournaments (pk, num_groups, scraped_at)
                       VALUES (?, ?, ?)""",
                    (pk, max((p.group_id for p in players), default=0), now),
                )
                if cur.lastrowid:
                    pt_id = cur.lastrowid
                else:
                    row = conn.execute(
                        "SELECT id FROM pickem_tournaments WHERE pk = ?", (pk,)
                    ).fetchone()
                    pt_id = row["id"]
            else:
                pt_id = row["id"]

            # 既存フィールドデータをクリア
            conn.execute(
                "DELETE FROM pickem_field_players WHERE pickem_tournament_id = ?",
                (pt_id,),
            )

            for p in players:
                conn.execute(
                    """INSERT INTO pickem_field_players
                       (pickem_tournament_id, espn_id, player_name, group_id,
                        current_wgr, sow_wgr, soy_wgr, prior_year_finish, handicap,
                        fedex_rank, fedex_points,
                        season_played, season_won, season_top10, season_top29,
                        season_top49, season_over50, season_cut,
                        tournament_history)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pt_id, p.espn_id, p.player_name, p.group_id,
                     p.current_wgr, p.sow_wgr, p.soy_wgr, p.prior_year_finish, p.handicap,
                     p.fedex_rank, p.fedex_points,
                     p.season_played, p.season_won, p.season_top10, p.season_top29,
                     p.season_top49, p.season_over50, p.season_cut,
                     json.dumps(p.tournament_history, ensure_ascii=False) if p.tournament_history else None),
                )

            conn.commit()
            return True

        except sqlite3.Error as e:
            print(f"[ERROR] DB save field data failed for PK {pk}: {e}")
            conn.rollback()
            return False

        finally:
            conn.close()

    def scrape_all_fields(
        self,
        pk_min: int = DEFAULT_PK_MIN,
        pk_max: int = DEFAULT_PK_MAX,
        resume: bool = False,
    ) -> tuple[int, int]:
        """全PKのCSVフィールドデータを一括取得。

        Args:
            pk_min: 開始PK
            pk_max: 終了PK（含む）
            resume: True なら既にDB登録済みのPKをスキップ

        Returns:
            (成功数, スキップ/失敗数)
        """
        if not self._ensure_logged_in():
            print("[ERROR] Login failed, aborting")
            return (0, 0)

        # resume 用: 既存PKの field データ有無
        existing_pks: set[int] = set()
        if resume:
            conn = get_connection()
            try:
                rows = conn.execute("""
                    SELECT DISTINCT pt.pk
                    FROM pickem_field_players pf
                    JOIN pickem_tournaments pt ON pt.id = pf.pickem_tournament_id
                """).fetchall()
                existing_pks = {r["pk"] for r in rows}
            finally:
                conn.close()
            print(f"[INFO] Resume mode: {len(existing_pks)} PKs already have field data")

        total = pk_max - pk_min + 1
        scraped = 0
        skipped = 0
        print(f"[INFO] Scraping field CSVs for PK range {pk_min}-{pk_max} ({total} PKs)")

        for pk in range(pk_min, pk_max + 1):
            if resume and pk in existing_pks:
                skipped += 1
                continue

            players = self.scrape_field_csv(pk)
            if players:
                if self.save_field_data(pk, players):
                    scraped += 1
                    groups = len(set(p.group_id for p in players))
                    if scraped % 20 == 0 or scraped <= 3:
                        print(f"[OK] PK {pk}: {len(players)} players, {groups} groups")
            else:
                skipped += 1

            time.sleep(RATE_LIMIT)

            if (pk - pk_min + 1) % 50 == 0:
                pct = (pk - pk_min + 1) / total * 100
                print(f"[INFO] Progress: {pk - pk_min + 1}/{total} ({pct:.0f}%) - {scraped} CSVs saved")

        print(f"\n[INFO] Field CSV scraping complete:")
        print(f"  Scraped: {scraped}")
        print(f"  Skipped: {skipped}")

        return (scraped, skipped)

    def scrape_all(
        self,
        pk_min: int = DEFAULT_PK_MIN,
        pk_max: int = DEFAULT_PK_MAX,
        resume: bool = False,
    ) -> ScrapeResult:
        """PK範囲を一括スクレイピング。

        Args:
            pk_min: 開始PK
            pk_max: 終了PK（含む）
            resume: True なら既にDB登録済みのPKをスキップ

        Returns:
            スクレイピング結果サマリー
        """
        if not self._ensure_logged_in():
            print("[ERROR] Login failed, aborting")
            return ScrapeResult()

        # resume 用: 既存PK取得
        existing_pks: set[int] = set()
        if resume:
            conn = get_connection()
            try:
                rows = conn.execute("SELECT pk FROM pickem_tournaments").fetchall()
                existing_pks = {r["pk"] for r in rows}
            finally:
                conn.close()
            print(f"[INFO] Resume mode: {len(existing_pks)} tournaments already scraped")

        result = ScrapeResult()
        total = pk_max - pk_min + 1
        print(f"[INFO] Scraping PK range {pk_min}-{pk_max} ({total} PKs)")

        for pk in range(pk_min, pk_max + 1):
            result.total_attempted += 1

            if resume and pk in existing_pks:
                result.total_skipped += 1
                continue

            data = self.scrape_tournament(pk)

            if data is None:
                result.total_skipped += 1
            else:
                pt_id = self.save_tournament_data(data)
                if pt_id is not None:
                    result.total_scraped += 1
                    result.total_picks += len(data.picks)
                    result.total_scores += len(data.scores)
                    print(
                        f"[OK] PK {pk}: {data.name} "
                        f"({data.num_users} users, {len(data.picks)} picks)"
                    )
                else:
                    result.total_errors += 1

            # レート制限
            time.sleep(RATE_LIMIT)

            # 進捗表示（10件ごと）
            if result.total_attempted % 10 == 0:
                pct = result.total_attempted / total * 100
                print(
                    f"[INFO] Progress: {result.total_attempted}/{total} "
                    f"({pct:.0f}%) - {result.total_scraped} scraped"
                )

        print(f"\n[INFO] Scraping complete:")
        print(f"  Attempted: {result.total_attempted}")
        print(f"  Scraped:   {result.total_scraped}")
        print(f"  Skipped:   {result.total_skipped}")
        print(f"  Errors:    {result.total_errors}")
        print(f"  Picks:     {result.total_picks}")
        print(f"  Scores:    {result.total_scores}")

        return result


#-----Status-----

def show_status() -> None:
    """DB内のpick'emデータ状況を表示。"""
    conn = get_connection()
    try:
        t_count = conn.execute("SELECT COUNT(*) as c FROM pickem_tournaments").fetchone()["c"]
        p_count = conn.execute("SELECT COUNT(*) as c FROM pickem_picks").fetchone()["c"]
        s_count = conn.execute("SELECT COUNT(*) as c FROM pickem_scores").fetchone()["c"]
        u_count = conn.execute("SELECT COUNT(*) as c FROM pickem_users").fetchone()["c"]

        # フィールドデータ
        f_count = conn.execute("SELECT COUNT(*) as c FROM pickem_field_players").fetchone()["c"]
        f_tournaments = conn.execute(
            "SELECT COUNT(DISTINCT pickem_tournament_id) as c FROM pickem_field_players"
        ).fetchone()["c"]

        print(f"[INFO] Pick'em DB Status:")
        print(f"  Tournaments:    {t_count}")
        print(f"  Users:          {u_count}")
        print(f"  Picks:          {p_count}")
        print(f"  Scores:         {s_count}")
        print(f"  Field players:  {f_count} ({f_tournaments} tournaments)")

        if t_count > 0:
            # 直近5大会
            recent = conn.execute(
                "SELECT pk, name, num_users, num_groups FROM pickem_tournaments ORDER BY pk DESC LIMIT 5"
            ).fetchall()
            print(f"\n  Recent tournaments:")
            for r in recent:
                print(f"    PK {r['pk']}: {r['name']} ({r['num_users']} users, {r['num_groups']} groups)")

            # シーズン推定
            pks = conn.execute("SELECT pk FROM pickem_tournaments ORDER BY pk").fetchall()
            pk_list = [r["pk"] for r in pks]
            print(f"\n  PK range: {min(pk_list)} - {max(pk_list)}")

            # ユーザー一覧
            users = conn.execute(
                "SELECT username, first_seen_pk, last_seen_pk FROM pickem_users ORDER BY username"
            ).fetchall()
            print(f"\n  Users:")
            for u in users:
                print(f"    {u['username']}: PK {u['first_seen_pk']} - {u['last_seen_pk']}")

    finally:
        conn.close()


#-----CLI-----

def main():
    """CLI エントリポイント。"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Pick'em 履歴データスクレイパー")
    parser.add_argument("--pk", type=int, help="単一大会PKを指定")
    parser.add_argument("--range", nargs=2, type=int, metavar=("MIN", "MAX"),
                        help="PK範囲を指定")
    parser.add_argument("--resume", action="store_true",
                        help="既にDB登録済みのPKをスキップ")
    parser.add_argument("--fields", action="store_true",
                        help="CSVフィールドデータ（グループ構成）を取得")
    parser.add_argument("--status", action="store_true",
                        help="DB内のデータ状況を表示")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    scraper = PickemScraper()

    if args.fields:
        # CSVフィールドデータ取得モード
        if args.pk:
            if not scraper.login():
                print("[ERROR] Login failed")
                return
            players = scraper.scrape_field_csv(args.pk)
            if players:
                scraper.save_field_data(args.pk, players)
                groups = len(set(p.group_id for p in players))
                print(f"[OK] PK {args.pk}: {len(players)} players, {groups} groups")
                for gid in sorted(set(p.group_id for p in players)):
                    g_players = [p for p in players if p.group_id == gid]
                    names = ", ".join(p.player_name for p in g_players)
                    print(f"  Group {gid}: {names}")
            else:
                print(f"[WARN] PK {args.pk}: No CSV data")
        elif args.range:
            pk_min, pk_max = args.range
            scraper.scrape_all_fields(pk_min=pk_min, pk_max=pk_max, resume=args.resume)
        else:
            scraper.scrape_all_fields(resume=args.resume)

    elif args.pk:
        # 単一PK（ピックデータ）
        if not scraper.login():
            print("[ERROR] Login failed")
            return
        data = scraper.scrape_tournament(args.pk)
        if data:
            pt_id = scraper.save_tournament_data(data)
            print(f"[OK] PK {args.pk}: {data.name}")
            print(f"  Users:  {data.num_users}")
            print(f"  Groups: {data.num_groups}")
            print(f"  Picks:  {len(data.picks)}")
            print(f"  Scores: {len(data.scores)}")
            if pt_id:
                print(f"  DB ID:  {pt_id}")
        else:
            print(f"[WARN] PK {args.pk}: No data found (404 or empty)")

    elif args.range:
        pk_min, pk_max = args.range
        scraper.scrape_all(pk_min=pk_min, pk_max=pk_max, resume=args.resume)

    else:
        # 全PK (デフォルト)
        scraper.scrape_all(resume=args.resume)


if __name__ == "__main__":
    main()
