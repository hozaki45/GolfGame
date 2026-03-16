"""3モデル バックテストレポート生成。

ML予測、EGS v1、EGS v2 の3モデルの予測と実際の大会結果を比較し、
精度・的中率を可視化するHTMLダッシュボードを生成する。

毎週 results.yml ワークフローで自動更新される。

Usage:
    uv run python -m src.model_backtest_report
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


# ----- Constants -----

GOLFGAME_DB = Path("data/golfgame.db")
PGA_STATS_DB = Path("data/pga_stats.db")
MODEL_DIR = Path("data/models")
OUTPUT_DIR = Path("data/output")

V2_CUT_MODEL_PATH = MODEL_DIR / "egs_v2_cut_classifier.joblib"
V2_POS_MODEL_PATH = MODEL_DIR / "egs_v2_position_regressor.joblib"


# ----- Data Loading -----

@dataclass
class GroupResult:
    """グループ内の1選手の予測 vs 実績。"""
    tournament_id: int
    tournament_name: str
    group_id: int
    player_name: str
    # Actual
    espn_position: int | None
    group_rank: int | None    # 1 = グループ内1位
    made_cut: bool
    # ML prediction
    ml_score: float
    ml_rank: int
    # EGS v1 prediction
    egs_v1: float | None
    egs_v1_rank: int | None
    # EGS v2 prediction (computed at report time)
    egs_v2: float | None
    egs_v2_rank: int | None


def load_tournament_data() -> list[dict]:
    """全完了大会のデータをロード。"""
    if not GOLFGAME_DB.exists():
        print("[WARN] golfgame.db not found")
        return []

    conn = sqlite3.connect(str(GOLFGAME_DB))
    conn.row_factory = sqlite3.Row

    tournaments = conn.execute(
        "SELECT id, name, end_date FROM tournaments "
        "WHERE status = 'results_saved' ORDER BY id"
    ).fetchall()

    all_data = []
    for t in tournaments:
        tid = t["id"]
        tname = t["name"]
        tend = t["end_date"] or ""

        rows = conn.execute(
            "SELECT mp.group_id, mp.player_name, "
            "mp.ml_score, mp.ml_rank_in_group, "
            "mp.egs, mp.egs_rank_in_group, "
            "mp.odds_component, mp.stats_component, mp.fit_component, "
            "mp.p_cut, mp.handicap, "
            "r.espn_position, r.group_rank, r.espn_status, "
            "gp.wgr "
            "FROM ml_predictions mp "
            "LEFT JOIN results r ON mp.tournament_id = r.tournament_id "
            "  AND mp.player_name = r.player_name "
            "LEFT JOIN group_players gp ON mp.tournament_id = gp.tournament_id "
            "  AND mp.player_name = gp.player_name "
            "WHERE mp.tournament_id = ? "
            "ORDER BY mp.group_id, mp.ml_rank_in_group",
            (tid,),
        ).fetchall()

        if not rows:
            continue

        groups: dict[int, list[dict]] = {}
        for r in rows:
            gid = r["group_id"]
            if gid not in groups:
                groups[gid] = []
            groups[gid].append({
                "tournament_id": tid,
                "tournament_name": tname,
                "end_date": tend,
                "group_id": gid,
                "player_name": r["player_name"],
                "espn_position": r["espn_position"],
                "group_rank": r["group_rank"],
                "made_cut": r["espn_position"] is not None,
                "espn_status": r["espn_status"] or "",
                "ml_score": r["ml_score"] or 0,
                "ml_rank": r["ml_rank_in_group"] or 99,
                "egs_v1": r["egs"],
                "egs_v1_rank": r["egs_rank_in_group"],
                "odds_component": r["odds_component"] or 0,
                "wgr": r["wgr"] or "999",
            })

        all_data.append({
            "tournament_id": tid,
            "tournament_name": tname,
            "end_date": tend,
            "groups": groups,
        })

    conn.close()
    return all_data


# ----- Accuracy Computation -----

def compute_accuracy(tournaments: list[dict]) -> dict:
    """3モデルの精度を算出。

    指標:
    - group_winner_hit: グループ1位を当てた割合
    - top2_hit: グループ上位2名に入った割合
    - avg_rank_error: 予測ランク - 実際ランクの平均絶対誤差
    """
    models = {
        "ml": {"winner_hits": 0, "top2_hits": 0, "rank_errors": [], "total_groups": 0},
        "egs_v1": {"winner_hits": 0, "top2_hits": 0, "rank_errors": [], "total_groups": 0},
        "egs_v2": {"winner_hits": 0, "top2_hits": 0, "rank_errors": [], "total_groups": 0},
    }

    tournament_results = []

    for t in tournaments:
        t_result = {
            "name": t["tournament_name"],
            "end_date": t["end_date"],
            "groups": [],
        }

        for gid, players in t["groups"].items():
            # Skip groups without results
            has_results = any(p["group_rank"] is not None for p in players)
            if not has_results:
                continue

            # Actual group winner
            actual_winner = None
            actual_ranks = {}
            for p in players:
                if p["group_rank"] is not None:
                    actual_ranks[p["player_name"]] = p["group_rank"]
                    if p["group_rank"] == 1:
                        actual_winner = p["player_name"]

            if not actual_winner:
                continue

            # ML prediction: rank 1 = pick
            ml_sorted = sorted(players, key=lambda x: x["ml_rank"])
            ml_pick = ml_sorted[0]["player_name"] if ml_sorted else None

            # EGS v1 prediction: rank 1 = pick
            egs_v1_sorted = sorted(players, key=lambda x: x["egs_v1"] if x["egs_v1"] is not None else 9999)
            egs_v1_pick = egs_v1_sorted[0]["player_name"] if egs_v1_sorted and egs_v1_sorted[0]["egs_v1"] is not None else None

            # EGS v2 prediction: rank 1 = pick (from egs_v2 field)
            egs_v2_sorted = sorted(players, key=lambda x: x.get("egs_v2") if x.get("egs_v2") is not None else 9999)
            egs_v2_pick = egs_v2_sorted[0]["player_name"] if egs_v2_sorted and egs_v2_sorted[0].get("egs_v2") is not None else None

            actual_top2 = [p["player_name"] for p in players
                           if p["group_rank"] is not None and p["group_rank"] <= 2]

            group_result = {
                "group_id": gid,
                "actual_winner": actual_winner,
                "actual_top2": actual_top2,
                "ml_pick": ml_pick,
                "egs_v1_pick": egs_v1_pick,
                "egs_v2_pick": egs_v2_pick,
                "players": [],
            }

            for p in players:
                ar = actual_ranks.get(p["player_name"])
                group_result["players"].append({
                    "name": p["player_name"],
                    "ml_rank": p["ml_rank"],
                    "egs_v1_rank": p["egs_v1_rank"],
                    "egs_v2_rank": p.get("egs_v2_rank"),
                    "actual_rank": ar,
                    "espn_position": p["espn_position"],
                    "made_cut": p["made_cut"],
                })

            # ML accuracy
            models["ml"]["total_groups"] += 1
            if ml_pick == actual_winner:
                models["ml"]["winner_hits"] += 1
            if ml_pick in actual_top2:
                models["ml"]["top2_hits"] += 1
            for p in players:
                ar = actual_ranks.get(p["player_name"])
                if ar is not None:
                    models["ml"]["rank_errors"].append(abs(p["ml_rank"] - ar))

            # EGS v1 accuracy
            if egs_v1_pick:
                models["egs_v1"]["total_groups"] += 1
                if egs_v1_pick == actual_winner:
                    models["egs_v1"]["winner_hits"] += 1
                if egs_v1_pick in actual_top2:
                    models["egs_v1"]["top2_hits"] += 1
                for p in players:
                    ar = actual_ranks.get(p["player_name"])
                    if ar is not None and p["egs_v1_rank"] is not None:
                        models["egs_v1"]["rank_errors"].append(abs(p["egs_v1_rank"] - ar))

            # EGS v2 accuracy
            if egs_v2_pick:
                models["egs_v2"]["total_groups"] += 1
                if egs_v2_pick == actual_winner:
                    models["egs_v2"]["winner_hits"] += 1
                if egs_v2_pick in actual_top2:
                    models["egs_v2"]["top2_hits"] += 1
                for p in players:
                    ar = actual_ranks.get(p["player_name"])
                    if ar is not None and p.get("egs_v2_rank") is not None:
                        models["egs_v2"]["rank_errors"].append(abs(p["egs_v2_rank"] - ar))

            t_result["groups"].append(group_result)

        tournament_results.append(t_result)

    # Summarize
    summary = {}
    for model_name, data in models.items():
        total = data["total_groups"]
        summary[model_name] = {
            "total_groups": total,
            "winner_hit_rate": data["winner_hits"] / total * 100 if total > 0 else 0,
            "top2_hit_rate": data["top2_hits"] / total * 100 if total > 0 else 0,
            "winner_hits": data["winner_hits"],
            "top2_hits": data["top2_hits"],
            "avg_rank_error": float(np.mean(data["rank_errors"])) if data["rank_errors"] else 0,
        }

    return {
        "summary": summary,
        "tournament_results": tournament_results,
    }


# ----- HTML Report -----

def generate_backtest_report() -> Path:
    """3モデルバックテストHTMLレポートを生成。"""
    print("[INFO] Loading tournament data...")
    tournaments = load_tournament_data()

    if not tournaments:
        print("[WARN] No tournament data found")
        return OUTPUT_DIR / "backtest.html"

    print(f"[INFO] Loaded {len(tournaments)} tournaments")

    # NOTE: v2 predictions are not stored in golfgame.db yet,
    # so v2 columns will show as N/A until integrated into the pipeline.
    # For now, we still compute and display the comparison framework.

    print("[INFO] Computing accuracy metrics...")
    results = compute_accuracy(tournaments)
    summary = results["summary"]
    tournament_results = results["tournament_results"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3モデル バックテスト比較</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --ml-color: #3b82f6; --v1-color: #f97316; --v2-color: #22c55e;
    --win: #22c55e; --lose: #ef4444; --neutral: #64748b;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; margin-bottom: 8px; font-size: 1.6rem; }}
.subtitle {{ text-align: center; color: var(--muted); margin-bottom: 24px; font-size: 0.9rem; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
.card h2 {{ font-size: 1.1rem; margin-bottom: 12px; color: var(--accent); }}
.full-width {{ grid-column: 1 / -1; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }}
.mono {{ font-family: 'Consolas', monospace; font-weight: 600; }}
.ml-val {{ color: var(--ml-color); }}
.v1-val {{ color: var(--v1-color); }}
.v2-val {{ color: var(--v2-color); }}
.hit {{ color: var(--win); font-weight: bold; }}
.miss {{ color: var(--lose); }}
.na {{ color: var(--neutral); }}
.summary-box {{ display: flex; gap: 16px; justify-content: center; margin-bottom: 24px; flex-wrap: wrap; }}
.summary-item {{ text-align: center; padding: 16px 20px; background: var(--surface); border-radius: 12px; border: 1px solid var(--border); min-width: 120px; }}
.summary-item .label {{ font-size: 0.75rem; color: var(--muted); margin-bottom: 4px; }}
.summary-item .value {{ font-size: 1.3rem; font-weight: 700; }}
.model-badge {{ display: inline-block; padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 0.8rem; margin: 0 4px; }}
.badge-ml {{ background: rgba(59,130,246,0.2); color: var(--ml-color); }}
.badge-v1 {{ background: rgba(249,115,22,0.2); color: var(--v1-color); }}
.badge-v2 {{ background: rgba(34,197,94,0.2); color: var(--v2-color); }}
.tournament-header {{ background: rgba(255,255,255,0.03); padding: 12px 16px; border-radius: 8px; margin: 16px 0 8px; }}
.tournament-header h3 {{ font-size: 1rem; color: var(--text); }}
.tournament-header .date {{ font-size: 0.8rem; color: var(--muted); }}
canvas {{ max-height: 300px; }}
.tab-buttons {{ display: flex; gap: 8px; margin-bottom: 16px; }}
.tab-btn {{ padding: 8px 16px; border: 1px solid var(--border); background: transparent;
    color: var(--muted); border-radius: 8px; cursor: pointer; font-size: 0.85rem; }}
.tab-btn.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
@media (max-width: 768px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
<h1>3モデル バックテスト比較</h1>
<p class="subtitle">
    <span class="model-badge badge-ml">ML予測</span>
    <span class="model-badge badge-v1">EGS v1</span>
    <span class="model-badge badge-v2">EGS v2</span>
    &mdash; 生成日時: {now}
</p>

<div class="summary-box">
    <div class="summary-item">
        <div class="label">対象大会数</div>
        <div class="value">{len(tournament_results)}</div>
    </div>
    <div class="summary-item">
        <div class="label">対象グループ数</div>
        <div class="value">{summary['ml']['total_groups']}</div>
    </div>
"""

    # Winner hit rates
    for model, label, cls in [("ml", "ML予測", "ml-val"), ("egs_v1", "EGS v1", "v1-val"), ("egs_v2", "EGS v2", "v2-val")]:
        rate = summary[model]["winner_hit_rate"]
        html += f"""    <div class="summary-item">
        <div class="label">{label} 的中率</div>
        <div class="value {cls}">{rate:.1f}%</div>
    </div>
"""

    html += """</div>

<div class="grid">
<!-- Accuracy Chart -->
<div class="card">
<h2>グループ1位 的中率</h2>
<canvas id="winnerChart"></canvas>
</div>

<div class="card">
<h2>グループ上位2名 的中率</h2>
<canvas id="top2Chart"></canvas>
</div>

<!-- Metrics Table -->
<div class="card full-width">
<h2>モデル別 精度サマリー</h2>
<table>
<thead>
<tr><th>指標</th><th class="ml-val">ML予測</th><th class="v1-val">EGS v1</th><th class="v2-val">EGS v2</th></tr>
</thead>
<tbody>
"""

    def best_marker(vals, higher_better=True):
        """値のリストから最良を太字にするマーカーを返す。"""
        valid = [(i, v) for i, v in enumerate(vals) if v is not None and v > 0]
        if not valid:
            return [""] * len(vals)
        if higher_better:
            best_i = max(valid, key=lambda x: x[1])[0]
        else:
            best_i = min(valid, key=lambda x: x[1])[0]
        return ["hit" if i == best_i else "" for i in range(len(vals))]

    rows_data = [
        ("グループ1位 的中率", [summary[m]["winner_hit_rate"] for m in ["ml", "egs_v1", "egs_v2"]], "%", True),
        ("上位2名 的中率", [summary[m]["top2_hit_rate"] for m in ["ml", "egs_v1", "egs_v2"]], "%", True),
        ("的中数 / 合計", None, None, None),
        ("平均ランク誤差", [summary[m]["avg_rank_error"] for m in ["ml", "egs_v1", "egs_v2"]], "", False),
    ]

    for label, vals, unit, higher_better in rows_data:
        if vals is None:
            # Special row: hit counts
            html += "<tr>"
            html += f"<td>{label}</td>"
            for m, cls in [("ml", "ml-val"), ("egs_v1", "v1-val"), ("egs_v2", "v2-val")]:
                html += f'<td class="mono {cls}">{summary[m]["winner_hits"]} / {summary[m]["total_groups"]}</td>'
            html += "</tr>\n"
            continue

        marks = best_marker(vals, higher_better)
        html += "<tr>"
        html += f"<td>{label}</td>"
        for i, (m, cls) in enumerate(zip(["ml", "egs_v1", "egs_v2"], ["ml-val", "v1-val", "v2-val"])):
            v = vals[i]
            mark = marks[i]
            if v is not None and v > 0:
                html += f'<td class="mono {cls} {mark}">{v:.1f}{unit}</td>'
            else:
                html += '<td class="na">N/A</td>'
        html += "</tr>\n"

    html += """</tbody></table></div>

<!-- Tournament Detail -->
<div class="card full-width">
<h2>大会別 詳細結果</h2>
<div class="tab-buttons" id="tabButtons"></div>
<div id="tabContents"></div>
</div>
</div>
"""

    # Tournament tabs data as JSON for JS rendering
    tabs_data = []
    for t in tournament_results:
        tab = {"name": t["name"], "date": t["end_date"], "groups": []}
        for g in t["groups"]:
            group_data = {
                "group_id": g["group_id"],
                "actual_winner": g["actual_winner"],
                "ml_pick": g["ml_pick"],
                "egs_v1_pick": g["egs_v1_pick"],
                "egs_v2_pick": g["egs_v2_pick"],
                "ml_hit": g["ml_pick"] == g["actual_winner"],
                "v1_hit": g["egs_v1_pick"] == g["actual_winner"] if g["egs_v1_pick"] else None,
                "v2_hit": g["egs_v2_pick"] == g["actual_winner"] if g["egs_v2_pick"] else None,
                "players": g["players"],
            }
            tab["groups"].append(group_data)
        tabs_data.append(tab)

    # Chart data
    ml_rates = []
    v1_rates = []
    v2_rates = []
    t_labels = []
    for t in tournaments:
        t_labels.append(t["tournament_name"][:20])
        ml_hits = v1_hits = v2_hits = 0
        total = 0
        for gid, players in t["groups"].items():
            has_results = any(p["group_rank"] is not None for p in players)
            if not has_results:
                continue
            total += 1
            actual_winner = None
            for p in players:
                if p["group_rank"] == 1:
                    actual_winner = p["player_name"]
            if not actual_winner:
                continue
            ml_sorted = sorted(players, key=lambda x: x["ml_rank"])
            if ml_sorted and ml_sorted[0]["player_name"] == actual_winner:
                ml_hits += 1
            egs_sorted = sorted(players, key=lambda x: x["egs_v1"] if x["egs_v1"] is not None else 9999)
            if egs_sorted and egs_sorted[0]["egs_v1"] is not None and egs_sorted[0]["player_name"] == actual_winner:
                v1_hits += 1
        ml_rates.append(ml_hits / total * 100 if total > 0 else 0)
        v1_rates.append(v1_hits / total * 100 if total > 0 else 0)
        v2_rates.append(0)  # v2 not in golfgame.db yet

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
    let html = '<div class="tournament-header"><h3>' + t.name + '</h3><span class="date">' + (t.date || '') + '</span></div>';
    html += '<table><thead><tr><th>Grp</th><th>実際の1位</th><th>ML予測</th><th>EGS v1</th><th>EGS v2</th></tr></thead><tbody>';
    t.groups.forEach(g => {{
        const mlClass = g.ml_hit ? 'hit' : 'miss';
        const v1Class = g.v1_hit === null ? 'na' : (g.v1_hit ? 'hit' : 'miss');
        const v2Class = g.v2_hit === null ? 'na' : (g.v2_hit ? 'hit' : 'miss');
        const mlIcon = g.ml_hit ? '&#x2714;' : '&#x2718;';
        const v1Icon = g.v1_hit === null ? '-' : (g.v1_hit ? '&#x2714;' : '&#x2718;');
        const v2Icon = g.v2_hit === null ? '-' : (g.v2_hit ? '&#x2714;' : '&#x2718;');
        html += '<tr>';
        html += '<td>G' + g.group_id + '</td>';
        html += '<td><strong>' + g.actual_winner + '</strong></td>';
        html += '<td class="' + mlClass + '">' + mlIcon + ' ' + (g.ml_pick || '-') + '</td>';
        html += '<td class="' + v1Class + '">' + v1Icon + ' ' + (g.egs_v1_pick || '-') + '</td>';
        html += '<td class="' + v2Class + '">' + v2Icon + ' ' + (g.egs_v2_pick || '-') + '</td>';
        html += '</tr>';
    }});
    html += '</tbody></table>';
    div.innerHTML = html;
    contentContainer.appendChild(div);
}});

// Winner hit rate chart
new Chart(document.getElementById('winnerChart').getContext('2d'), {{
    type: 'bar',
    data: {{
        labels: {json.dumps(t_labels, ensure_ascii=False)},
        datasets: [
            {{ label: 'ML', data: {json.dumps(ml_rates)}, backgroundColor: 'rgba(59,130,246,0.7)', borderRadius: 4 }},
            {{ label: 'EGS v1', data: {json.dumps(v1_rates)}, backgroundColor: 'rgba(249,115,22,0.7)', borderRadius: 4 }},
            {{ label: 'EGS v2', data: {json.dumps(v2_rates)}, backgroundColor: 'rgba(34,197,94,0.7)', borderRadius: 4 }},
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#94a3b8', maxRotation: 45 }}, grid: {{ color: '#334155' }} }},
            y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }}, title: {{ display: true, text: '的中率 (%)', color: '#94a3b8' }} }}
        }}
    }}
}});

// Top 2 hit rate (summary)
const summaryData = {json.dumps({m: summary[m] for m in ["ml", "egs_v1", "egs_v2"]}, ensure_ascii=False)};
new Chart(document.getElementById('top2Chart').getContext('2d'), {{
    type: 'doughnut',
    data: {{
        labels: ['ML (' + summaryData.ml.top2_hit_rate.toFixed(1) + '%)',
                 'EGS v1 (' + summaryData.egs_v1.top2_hit_rate.toFixed(1) + '%)',
                 'EGS v2 (' + summaryData.egs_v2.top2_hit_rate.toFixed(1) + '%)'],
        datasets: [{{
            data: [summaryData.ml.top2_hit_rate, summaryData.egs_v1.top2_hit_rate, summaryData.egs_v2.top2_hit_rate],
            backgroundColor: ['rgba(59,130,246,0.7)', 'rgba(249,115,22,0.7)', 'rgba(34,197,94,0.7)'],
            borderWidth: 0,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#e2e8f0' }} }} }}
    }}
}});
</script>
<p style="text-align:center;color:var(--muted);margin-top:20px;font-size:0.8rem;">
    毎週月曜に自動更新 &mdash; 大会結果の収集後にバックテストを再計算
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
