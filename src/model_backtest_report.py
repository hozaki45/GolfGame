"""3モデル バックテストレポート生成 — ゲームスコアベース。

ゴルフゲームの実際のルールに基づいてゲームスコアを計算し、
ML予測・EGS v1・EGS v2 の3モデルがどのピックを選び、
それぞれ何点になったかを比較するHTMLダッシュボードを生成する。

ルール参照: http://jflynn87.pythonanywhere.com/golf_app/about/

ゲームスコア = Rank - Handicap  (低い方が良い)
CUTペナルティ:
  G1-3: (カット通過者数+1) - HC + グループ内カット通過者数
  G4+:  (カット通過者数+1) - HC

毎週 results.yml ワークフローで自動更新される。

Usage:
    uv run python -m src.model_backtest_report
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


# ----- Constants -----

GOLFGAME_DB = Path("data/golfgame.db")
OUTPUT_DIR = Path("data/output")


# ----- Game Score Calculation -----

def calc_game_score(
    espn_position: int | None,
    handicap: int,
    group_id: int,
    made_cut_count: int,
    group_made_cut_count: int,
) -> float:
    """実際のゲームルールに基づくピックスコアを算出。

    Args:
        espn_position: 最終順位 (None = CUT)
        handicap: ハンデキャップ (WGR/100)
        group_id: グループID (1-9)
        made_cut_count: 大会全体のカット通過者数
        group_made_cut_count: 同グループ内のカット通過者数

    Returns:
        ゲームスコア (低い方が良い)
    """
    if espn_position is not None:
        # Made cut: Rank - Handicap
        return espn_position - handicap
    else:
        # Missed cut penalty
        base = (made_cut_count + 1) - handicap
        if group_id <= 3:
            return base + group_made_cut_count
        else:
            return base


def calc_handicap(wgr_str: str | None, field_size: int) -> int:
    """WGRからハンデキャップを算出。"""
    if not wgr_str:
        return 0
    try:
        wgr = int(wgr_str)
    except (ValueError, TypeError):
        return 0
    hc = round(wgr / 100)
    max_hc = round(0.13 * field_size)
    return min(hc, max_hc)


# ----- Data Loading & Simulation -----

def load_and_simulate() -> list[dict]:
    """全完了大会をロードし、3モデルのゲームスコアをシミュレーション。"""
    if not GOLFGAME_DB.exists():
        return []

    conn = sqlite3.connect(str(GOLFGAME_DB))
    conn.row_factory = sqlite3.Row

    tournaments = conn.execute(
        "SELECT id, name, end_date FROM tournaments "
        "WHERE status = 'results_saved' ORDER BY id"
    ).fetchall()

    all_results = []

    for t in tournaments:
        tid = t["id"]
        tname = t["name"]
        tend = t["end_date"] or ""

        rows = conn.execute(
            "SELECT mp.group_id, mp.player_name, mp.ml_score, mp.ml_rank_in_group, "
            "mp.egs, mp.egs_rank_in_group, mp.handicap, "
            "r.espn_position, r.group_rank, "
            "gp.wgr "
            "FROM ml_predictions mp "
            "LEFT JOIN results r ON mp.tournament_id = r.tournament_id "
            "  AND mp.player_name = r.player_name "
            "LEFT JOIN group_players gp ON mp.tournament_id = gp.tournament_id "
            "  AND mp.player_name = gp.player_name "
            "WHERE mp.tournament_id = ?", (tid,)
        ).fetchall()

        if not rows:
            continue

        # Build groups
        field_size = len(rows)
        made_cut_count = sum(1 for r in rows if r["espn_position"] is not None)

        groups: dict[int, list[dict]] = {}
        for r in rows:
            gid = r["group_id"]
            if gid not in groups:
                groups[gid] = []

            hc = r["handicap"]
            if hc is None:
                hc = calc_handicap(r["wgr"], field_size)

            groups[gid].append({
                "player_name": r["player_name"],
                "espn_position": r["espn_position"],
                "group_rank": r["group_rank"],
                "made_cut": r["espn_position"] is not None,
                "handicap": hc,
                "ml_score": r["ml_score"] or 0,
                "ml_rank": r["ml_rank_in_group"] or 99,
                "egs_v1": r["egs"],
                "egs_v1_rank": r["egs_rank_in_group"],
                "wgr": r["wgr"],
            })

        # Calculate game scores for each player
        for gid, players in groups.items():
            group_made_cut = sum(1 for p in players if p["made_cut"])
            for p in players:
                p["game_score"] = calc_game_score(
                    p["espn_position"], p["handicap"],
                    gid, made_cut_count, group_made_cut,
                )

        # Simulate picks for each model
        # G1: pick 2, G2-9: pick 1
        model_picks = {"ml": {}, "egs_v1": {}, "odds": {}}
        model_total_scores = {"ml": 0, "egs_v1": 0, "odds": 0}

        # Also track the "best possible" score (oracle)
        oracle_total = 0

        group_details = []

        for gid in sorted(groups.keys()):
            players = groups[gid]
            n_picks = 2 if gid == 1 else 1

            # Oracle: best possible game_score in group
            sorted_by_game = sorted(players, key=lambda p: p["game_score"])
            oracle_picks = [p["player_name"] for p in sorted_by_game[:n_picks]]
            oracle_score = sum(p["game_score"] for p in sorted_by_game[:n_picks])
            oracle_total += oracle_score

            # ML picks: sorted by ml_score descending
            ml_sorted = sorted(players, key=lambda p: p["ml_score"], reverse=True)
            ml_picks_names = [p["player_name"] for p in ml_sorted[:n_picks]]
            ml_score = sum(p["game_score"] for p in ml_sorted[:n_picks])
            model_picks["ml"][gid] = ml_picks_names
            model_total_scores["ml"] += ml_score

            # EGS v1 picks: sorted by egs ascending (lower = better)
            egs_sorted = sorted(players, key=lambda p: p["egs_v1"] if p["egs_v1"] is not None else 9999)
            has_egs = egs_sorted[0]["egs_v1"] is not None if egs_sorted else False
            egs_picks_names = [p["player_name"] for p in egs_sorted[:n_picks]] if has_egs else []
            egs_score = sum(p["game_score"] for p in egs_sorted[:n_picks]) if has_egs else None
            model_picks["egs_v1"][gid] = egs_picks_names
            if egs_score is not None:
                model_total_scores["egs_v1"] += egs_score

            # Odds picks: sorted by odds_component (highest implied prob = pick)
            # We use group position order (index 0 = favorite)
            odds_sorted = sorted(players, key=lambda p: p["ml_rank"])  # odds rank approx
            # Actually use the original group order (player at index 0 = best odds)
            # Since we don't have raw odds, use wgr as proxy for "favorite"
            odds_by_wgr = sorted(players, key=lambda p: int(p["wgr"] or "999"))
            odds_picks_names = [p["player_name"] for p in odds_by_wgr[:n_picks]]
            odds_score = sum(p["game_score"] for p in odds_by_wgr[:n_picks])
            model_picks["odds"][gid] = odds_picks_names
            model_total_scores["odds"] += odds_score

            group_details.append({
                "group_id": gid,
                "n_picks": n_picks,
                "oracle_picks": oracle_picks,
                "oracle_score": oracle_score,
                "players": [{
                    "name": p["player_name"],
                    "pos": p["espn_position"],
                    "hc": p["handicap"],
                    "game_score": p["game_score"],
                    "ml_rank": p["ml_rank"],
                    "egs_v1_rank": p["egs_v1_rank"],
                    "made_cut": p["made_cut"],
                } for p in sorted(players, key=lambda x: x["game_score"])],
                "ml_picks": ml_picks_names,
                "ml_score": ml_score,
                "egs_v1_picks": egs_picks_names,
                "egs_v1_score": egs_score,
                "odds_picks": odds_picks_names,
                "odds_score": odds_score,
            })

        all_results.append({
            "tournament_id": tid,
            "name": tname,
            "end_date": tend,
            "field_size": field_size,
            "made_cut_count": made_cut_count,
            "model_totals": model_total_scores,
            "oracle_total": oracle_total,
            "groups": group_details,
        })

    conn.close()
    return all_results


# ----- HTML Report -----

def generate_backtest_report() -> Path:
    """ゲームスコアベースの3モデルバックテストHTMLを生成。"""
    print("[INFO] Loading and simulating tournament data...")
    tournaments = load_and_simulate()

    if not tournaments:
        print("[WARN] No tournament data found")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUTPUT_DIR / "backtest.html"
        p.write_text("<html><body>No data</body></html>", encoding="utf-8")
        return p

    print(f"[INFO] Simulated {len(tournaments)} tournaments")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Aggregate stats
    cumulative = {"ml": 0, "egs_v1": 0, "odds": 0, "oracle": 0}
    per_tournament = []
    for t in tournaments:
        cumulative["ml"] += t["model_totals"]["ml"]
        cumulative["egs_v1"] += t["model_totals"]["egs_v1"]
        cumulative["odds"] += t["model_totals"]["odds"]
        cumulative["oracle"] += t["oracle_total"]
        per_tournament.append({
            "name": t["name"][:25],
            "ml": t["model_totals"]["ml"],
            "egs_v1": t["model_totals"]["egs_v1"],
            "odds": t["model_totals"]["odds"],
            "oracle": t["oracle_total"],
        })

    # Find winner per tournament
    for pt in per_tournament:
        scores = {"ML": pt["ml"], "EGS v1": pt["egs_v1"], "Odds": pt["odds"]}
        pt["winner"] = min(scores, key=scores.get)

    # Count wins
    win_counts = {"ML": 0, "EGS v1": 0, "Odds": 0}
    for pt in per_tournament:
        win_counts[pt["winner"]] += 1

    t_labels = json.dumps([p["name"] for p in per_tournament], ensure_ascii=False)
    ml_scores = json.dumps([p["ml"] for p in per_tournament])
    v1_scores = json.dumps([p["egs_v1"] for p in per_tournament])
    odds_scores = json.dumps([p["odds"] for p in per_tournament])
    oracle_scores = json.dumps([p["oracle"] for p in per_tournament])

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3モデル ゲームスコア バックテスト</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --ml-color: #3b82f6; --v1-color: #f97316; --v2-color: #22c55e; --odds-color: #a855f7;
    --oracle-color: #64748b;
    --win: #22c55e; --lose: #ef4444;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; margin-bottom: 8px; font-size: 1.5rem; }}
.subtitle {{ text-align: center; color: var(--muted); margin-bottom: 24px; font-size: 0.85rem; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
.card h2 {{ font-size: 1.05rem; margin-bottom: 12px; color: var(--accent); }}
.full-width {{ grid-column: 1 / -1; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
th, td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; }}
.mono {{ font-family: 'Consolas', monospace; font-weight: 600; }}
.ml-val {{ color: var(--ml-color); }}
.v1-val {{ color: var(--v1-color); }}
.odds-val {{ color: var(--odds-color); }}
.best {{ color: var(--win); font-weight: bold; }}
.pick-marker {{ font-weight: bold; }}
.summary-box {{ display: flex; gap: 14px; justify-content: center; margin-bottom: 24px; flex-wrap: wrap; }}
.summary-item {{ text-align: center; padding: 14px 18px; background: var(--surface); border-radius: 12px; border: 1px solid var(--border); min-width: 110px; }}
.summary-item .label {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 4px; }}
.summary-item .value {{ font-size: 1.3rem; font-weight: 700; }}
.model-badge {{ display: inline-block; padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 0.8rem; margin: 0 3px; }}
.badge-ml {{ background: rgba(59,130,246,0.2); color: var(--ml-color); }}
.badge-v1 {{ background: rgba(249,115,22,0.2); color: var(--v1-color); }}
.badge-odds {{ background: rgba(168,85,247,0.2); color: var(--odds-color); }}
.tournament-header {{ background: rgba(255,255,255,0.03); padding: 10px 14px; border-radius: 8px; margin: 14px 0 8px; display: flex; justify-content: space-between; align-items: center; }}
.tournament-header h3 {{ font-size: 0.95rem; }}
.tournament-header .scores {{ font-size: 0.8rem; color: var(--muted); }}
canvas {{ max-height: 320px; }}
.tab-buttons {{ display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }}
.tab-btn {{ padding: 6px 14px; border: 1px solid var(--border); background: transparent;
    color: var(--muted); border-radius: 8px; cursor: pointer; font-size: 0.8rem; }}
.tab-btn.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.score-rule {{ background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 0.82rem; color: var(--muted); line-height: 1.6; }}
.score-rule code {{ color: var(--text); background: rgba(255,255,255,0.06); padding: 1px 6px; border-radius: 4px; }}
@media (max-width: 768px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
<h1>3モデル ゲームスコア バックテスト</h1>
<p class="subtitle">
    <span class="model-badge badge-ml">ML予測</span>
    <span class="model-badge badge-v1">EGS v1</span>
    <span class="model-badge badge-odds">Oddsベース (WGR順)</span>
    &mdash; {now}
</p>

<div class="score-rule">
    <strong>ゲームスコア計算ルール:</strong>
    カット通過: <code>順位 - ハンデ</code> &nbsp;|&nbsp;
    CUT (G1-3): <code>(カット通過者数+1) - ハンデ + グループ内カット通過者数</code> &nbsp;|&nbsp;
    CUT (G4+): <code>(カット通過者数+1) - ハンデ</code> &nbsp;|&nbsp;
    G1は2名、G2-9は1名をピック &nbsp;|&nbsp;
    <strong>合計点が低いほど良い</strong>
</div>

<div class="summary-box">
    <div class="summary-item">
        <div class="label">対象大会数</div>
        <div class="value">{len(tournaments)}</div>
    </div>
    <div class="summary-item">
        <div class="label">ML予測 累計</div>
        <div class="value ml-val">{cumulative['ml']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">EGS v1 累計</div>
        <div class="value v1-val">{cumulative['egs_v1']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">Odds 累計</div>
        <div class="value odds-val">{cumulative['odds']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">最適解 (Oracle)</div>
        <div class="value" style="color:var(--oracle-color)">{cumulative['oracle']:.0f}</div>
    </div>
    <div class="summary-item">
        <div class="label">ML 週間勝利数</div>
        <div class="value ml-val">{win_counts['ML']}</div>
    </div>
    <div class="summary-item">
        <div class="label">EGS v1 週間勝利数</div>
        <div class="value v1-val">{win_counts['EGS v1']}</div>
    </div>
</div>

<div class="grid">
<div class="card">
<h2>大会別ゲームスコア (低い方が良い)</h2>
<canvas id="scoreChart"></canvas>
</div>

<div class="card">
<h2>累計ゲームスコア推移</h2>
<canvas id="cumulativeChart"></canvas>
</div>

<!-- Per-tournament summary table -->
<div class="card full-width">
<h2>大会別サマリー</h2>
<table>
<thead>
<tr><th>大会</th><th>日付</th><th class="ml-val">ML予測</th><th class="v1-val">EGS v1</th><th class="odds-val">Odds</th><th>最適解</th><th>週間勝者</th></tr>
</thead>
<tbody>
"""

    for i, t in enumerate(tournaments):
        pt = per_tournament[i]
        scores = {"ML": pt["ml"], "EGS v1": pt["egs_v1"], "Odds": pt["odds"]}
        best_val = min(scores.values())

        def mark(v, name):
            cls = "best" if v == best_val else ""
            return f'<td class="mono {cls}">{v:.0f}</td>'

        winner_badge = ""
        if pt["winner"] == "ML":
            winner_badge = '<span class="model-badge badge-ml">ML</span>'
        elif pt["winner"] == "EGS v1":
            winner_badge = '<span class="model-badge badge-v1">EGS v1</span>'
        else:
            winner_badge = '<span class="model-badge badge-odds">Odds</span>'

        html += f"""<tr>
<td>{t['name'][:35]}</td>
<td style="color:var(--muted)">{t['end_date']}</td>
{mark(pt['ml'], 'ML')}
{mark(pt['egs_v1'], 'EGS v1')}
{mark(pt['odds'], 'Odds')}
<td class="mono" style="color:var(--oracle-color)">{pt['oracle']:.0f}</td>
<td>{winner_badge}</td>
</tr>
"""

    # Cumulative totals row
    best_cum = min(cumulative["ml"], cumulative["egs_v1"], cumulative["odds"])

    def cum_mark(v):
        return "best" if v == best_cum else ""

    html += f"""<tr style="border-top:2px solid var(--border);font-weight:700">
<td>累計</td><td></td>
<td class="mono ml-val {cum_mark(cumulative['ml'])}">{cumulative['ml']:.0f}</td>
<td class="mono v1-val {cum_mark(cumulative['egs_v1'])}">{cumulative['egs_v1']:.0f}</td>
<td class="mono odds-val {cum_mark(cumulative['odds'])}">{cumulative['odds']:.0f}</td>
<td class="mono" style="color:var(--oracle-color)">{cumulative['oracle']:.0f}</td>
<td></td>
</tr>
"""

    html += """</tbody></table></div>

<!-- Tournament Detail Tabs -->
<div class="card full-width">
<h2>大会別 グループ詳細</h2>
<div class="tab-buttons" id="tabButtons"></div>
<div id="tabContents"></div>
</div>
</div>
"""

    # Build tab data
    tabs_data = []
    for t in tournaments:
        tab = {
            "name": t["name"],
            "date": t["end_date"],
            "totals": t["model_totals"],
            "oracle": t["oracle_total"],
            "groups": [],
        }
        for g in t["groups"]:
            tab["groups"].append({
                "gid": g["group_id"],
                "n_picks": g["n_picks"],
                "ml_picks": g["ml_picks"],
                "ml_score": g["ml_score"],
                "egs_v1_picks": g["egs_v1_picks"],
                "egs_v1_score": g["egs_v1_score"],
                "odds_picks": g["odds_picks"],
                "odds_score": g["odds_score"],
                "oracle_picks": g["oracle_picks"],
                "oracle_score": g["oracle_score"],
                "players": g["players"],
            })
        tabs_data.append(tab)

    # Cumulative chart data
    cum_ml = []
    cum_v1 = []
    cum_odds = []
    cum_oracle = []
    running = {"ml": 0, "v1": 0, "odds": 0, "oracle": 0}
    for pt in per_tournament:
        running["ml"] += pt["ml"]
        running["v1"] += pt["egs_v1"]
        running["odds"] += pt["odds"]
        running["oracle"] += pt["oracle"]
        cum_ml.append(running["ml"])
        cum_v1.append(running["v1"])
        cum_odds.append(running["odds"])
        cum_oracle.append(running["oracle"])

    html += f"""
<script>
const tabsData = {json.dumps(tabs_data, ensure_ascii=False, default=str)};

// Render tabs
const btnContainer = document.getElementById('tabButtons');
const contentContainer = document.getElementById('tabContents');
tabsData.forEach((t, i) => {{
    const btn = document.createElement('button');
    btn.className = 'tab-btn' + (i === tabsData.length - 1 ? ' active' : '');
    btn.textContent = t.name.substring(0, 25);
    btn.onclick = () => {{
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + i).classList.add('active');
    }};
    btnContainer.appendChild(btn);

    const div = document.createElement('div');
    div.id = 'tab-' + i;
    div.className = 'tab-content' + (i === tabsData.length - 1 ? ' active' : '');

    let h = '<div class="tournament-header"><h3>' + t.name + '</h3>';
    h += '<span class="scores">ML=' + t.totals.ml.toFixed(0) + ' | EGS=' + t.totals.egs_v1.toFixed(0) + ' | Odds=' + t.totals.odds.toFixed(0) + ' | Oracle=' + t.oracle.toFixed(0) + '</span></div>';

    t.groups.forEach(g => {{
        const best = Math.min(g.ml_score, g.egs_v1_score || 9999, g.odds_score);
        h += '<table style="margin-bottom:12px"><thead><tr>';
        h += '<th colspan="6" style="color:var(--text)">G' + g.gid + ' (' + g.n_picks + '名ピック)';
        h += ' &mdash; <span class="ml-val">ML=' + g.ml_score.toFixed(0) + '</span>';
        if (g.egs_v1_score !== null) h += ' <span class="v1-val">EGS=' + g.egs_v1_score.toFixed(0) + '</span>';
        h += ' <span class="odds-val">Odds=' + g.odds_score.toFixed(0) + '</span>';
        h += ' <span style="color:var(--oracle-color)">Best=' + g.oracle_score.toFixed(0) + '</span>';
        h += '</th></tr>';
        h += '<tr><th>#</th><th>選手</th><th>順位</th><th>HC</th><th>スコア</th><th>ピック</th></tr></thead><tbody>';

        g.players.forEach((p, idx) => {{
            const pos = p.pos !== null ? p.pos : 'CUT';
            const picks = [];
            if (g.ml_picks.includes(p.name)) picks.push('<span class="ml-val">ML</span>');
            if (g.egs_v1_picks.includes(p.name)) picks.push('<span class="v1-val">EGS</span>');
            if (g.odds_picks.includes(p.name)) picks.push('<span class="odds-val">Odds</span>');
            if (g.oracle_picks.includes(p.name)) picks.push('<span style="color:var(--oracle-color)">Best</span>');
            const pickStr = picks.length > 0 ? picks.join(' ') : '';
            const isBest = g.oracle_picks.includes(p.name);
            const rowStyle = isBest ? 'background:rgba(34,197,94,0.05)' : '';
            h += '<tr style="' + rowStyle + '">';
            h += '<td>' + (idx+1) + '</td>';
            h += '<td>' + p.name + '</td>';
            h += '<td class="mono">' + pos + '</td>';
            h += '<td class="mono">' + p.hc + '</td>';
            h += '<td class="mono">' + p.game_score.toFixed(0) + '</td>';
            h += '<td class="pick-marker">' + pickStr + '</td>';
            h += '</tr>';
        }});
        h += '</tbody></table>';
    }});

    div.innerHTML = h;
    contentContainer.appendChild(div);
}});

// Per-tournament score chart
new Chart(document.getElementById('scoreChart').getContext('2d'), {{
    type: 'bar',
    data: {{
        labels: {t_labels},
        datasets: [
            {{ label: 'ML', data: {ml_scores}, backgroundColor: 'rgba(59,130,246,0.7)', borderRadius: 4 }},
            {{ label: 'EGS v1', data: {v1_scores}, backgroundColor: 'rgba(249,115,22,0.7)', borderRadius: 4 }},
            {{ label: 'Odds', data: {odds_scores}, backgroundColor: 'rgba(168,85,247,0.7)', borderRadius: 4 }},
            {{ label: 'Oracle', data: {oracle_scores}, backgroundColor: 'rgba(100,116,139,0.4)', borderRadius: 4 }},
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }}, grid: {{ color: '#334155' }} }},
            y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }},
                 title: {{ display: true, text: 'Game Score (low = good)', color: '#94a3b8' }} }}
        }}
    }}
}});

// Cumulative chart
new Chart(document.getElementById('cumulativeChart').getContext('2d'), {{
    type: 'line',
    data: {{
        labels: {t_labels},
        datasets: [
            {{ label: 'ML', data: {json.dumps(cum_ml)}, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', fill: false, tension: 0.3 }},
            {{ label: 'EGS v1', data: {json.dumps(cum_v1)}, borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.1)', fill: false, tension: 0.3 }},
            {{ label: 'Odds', data: {json.dumps(cum_odds)}, borderColor: '#a855f7', backgroundColor: 'rgba(168,85,247,0.1)', fill: false, tension: 0.3 }},
            {{ label: 'Oracle', data: {json.dumps(cum_oracle)}, borderColor: '#64748b', backgroundColor: 'rgba(100,116,139,0.1)', fill: false, tension: 0.3, borderDash: [5, 5] }},
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
            y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }},
                 title: {{ display: true, text: 'Cumulative Score', color: '#94a3b8' }} }}
        }}
    }}
}});
</script>
<p style="text-align:center;color:var(--muted);margin-top:20px;font-size:0.78rem;">
    毎週月曜に自動更新 &mdash; Oracle = 各グループで最も低いスコアの選手をピックした場合の理論最適値
</p>
</div>
</body>
</html>
"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "backtest.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"[OK] Backtest report saved: {output_path}")
    return output_path


# ----- CLI -----

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    generate_backtest_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
