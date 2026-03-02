"""Picks CSV Downloader - jflynn87.pythonanywhere.com.

Logs into the golf pick'em site and downloads the field CSV
for the current tournament from the Make Picks page.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import yaml


@dataclass
class PicksCSVResult:
    """Result of a CSV download attempt."""
    success: bool
    filepath: Path | None
    tournament_pk: str
    message: str


class PicksDownloader:
    """Downloads field CSV from jflynn87.pythonanywhere.com."""

    BASE_URL = "http://jflynn87.pythonanywhere.com"
    LOGIN_URL = f"{BASE_URL}/accounts/login/"
    PICKS_URL = f"{BASE_URL}/golf_app/new_field_list_1"
    CSV_SIGNED_URL = f"{BASE_URL}/golf_app/field_csv_signed_url"
    CREATE_CSV_URL = f"{BASE_URL}/golf_app/create_field_csv"

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

    def login(self) -> bool:
        """Log in to the site using Django CSRF authentication.

        Returns:
            True if login succeeded.
        """
        print("[INFO] Logging into jflynn87.pythonanywhere.com...")
        try:
            resp = self.session.get(self.LOGIN_URL, timeout=self.timeout)
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
                self.LOGIN_URL,
                data=login_data,
                headers={"Referer": self.LOGIN_URL},
                timeout=self.timeout,
            )

            if "login" in resp.url.lower() and resp.status_code == 200:
                print("[ERROR] Login failed - check credentials")
                return False

            print("[INFO] Login successful")
            return True

        except requests.RequestException as e:
            print(f"[ERROR] Login request failed: {e}")
            return False

    def get_tournament_pk(self) -> str | None:
        """Get the current tournament PK from the Make Picks page.

        Returns:
            Tournament PK string (e.g. '408') or None.
        """
        try:
            resp = self.session.get(self.PICKS_URL, timeout=self.timeout)
            resp.raise_for_status()

            import re
            match = re.search(r"createCSVDiv\(['\"](\d+)['\"]\)", resp.text)
            if match:
                pk = match.group(1)
                print(f"[INFO] Found tournament PK: {pk}")
                return pk

            print("[WARN] Could not find tournament PK on Make Picks page")
            return None

        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch Make Picks page: {e}")
            return None

    def get_csv_url(self, pk: str) -> str | None:
        """Get the signed CSV download URL.

        Args:
            pk: Tournament PK.

        Returns:
            Download URL string or None if CSV not yet generated.
        """
        try:
            url = f"{self.CSV_SIGNED_URL}?pk={pk}"
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()

            data = resp.json()
            csv_url = data.get("url", "")
            if csv_url:
                print(f"[INFO] CSV download URL found")
                return csv_url

            return None

        except (requests.RequestException, ValueError) as e:
            print(f"[WARN] Could not get CSV signed URL: {e}")
            return None

    def generate_csv(self, pk: str) -> bool:
        """Request the server to generate the CSV file.

        Args:
            pk: Tournament PK.

        Returns:
            True if generation was triggered successfully.
        """
        try:
            url = f"{self.CREATE_CSV_URL}?pk={pk}"
            print(f"[INFO] Requesting CSV generation for tournament {pk}...")
            resp = self.session.get(url, timeout=60)
            resp.raise_for_status()
            print(f"[INFO] CSV generation request sent (status {resp.status_code})")
            return True

        except requests.RequestException as e:
            print(f"[ERROR] CSV generation request failed: {e}")
            return False

    def download_csv(self, csv_url: str, data_dir: str = "data") -> Path | None:
        """Download the CSV file from the signed URL.

        Args:
            csv_url: Signed download URL.
            data_dir: Base data directory.

        Returns:
            Path to saved file or None.
        """
        try:
            resp = self.session.get(csv_url, timeout=self.timeout)
            resp.raise_for_status()

            output_dir = Path(data_dir) / "picks"
            output_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = output_dir / f"field_{date_str}.csv"

            filepath.write_text(resp.text, encoding="utf-8")
            print(f"[INFO] Saved CSV to {filepath}")
            return filepath

        except requests.RequestException as e:
            print(f"[ERROR] CSV download failed: {e}")
            return None

    def run(self, data_dir: str = "data") -> PicksCSVResult:
        """Run the full download pipeline.

        Args:
            data_dir: Base data directory.

        Returns:
            PicksCSVResult with download status.
        """
        # Step 1: Login
        if not self.login():
            return PicksCSVResult(False, None, "", "Login failed")

        # Step 2: Get tournament PK
        pk = self.get_tournament_pk()
        if not pk:
            return PicksCSVResult(False, None, "", "Could not find tournament PK")

        # Step 3: Try to get existing CSV URL
        csv_url = self.get_csv_url(pk)

        # Step 4: If no URL, generate CSV and retry
        if not csv_url:
            print("[INFO] No existing CSV found, generating...")
            if not self.generate_csv(pk):
                return PicksCSVResult(False, None, pk, "CSV generation failed")

            # Wait and retry a few times
            for attempt in range(5):
                time.sleep(3)
                print(f"[INFO] Checking for CSV (attempt {attempt + 1}/5)...")
                csv_url = self.get_csv_url(pk)
                if csv_url:
                    break

        if not csv_url:
            return PicksCSVResult(False, None, pk, "CSV URL not available after generation")

        # Step 5: Download
        filepath = self.download_csv(csv_url, data_dir)
        if filepath:
            return PicksCSVResult(True, filepath, pk, "CSV downloaded successfully")
        return PicksCSVResult(False, None, pk, "CSV download failed")


def run(config_path: str = "config.yaml", data_dir: str = "data") -> PicksCSVResult:
    """Pipeline entry point for picks CSV download.

    Args:
        config_path: Path to config file.
        data_dir: Base data directory.

    Returns:
        PicksCSVResult.
    """
    downloader = PicksDownloader(config_path)
    return downloader.run(data_dir)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    result = run()
    print()
    if result.success:
        print(f"[SUCCESS] CSV downloaded: {result.filepath}")
    else:
        print(f"[FAILED] {result.message}")
