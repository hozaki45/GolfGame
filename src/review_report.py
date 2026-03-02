"""大会振り返りHTMLレポート生成モジュール。

post_tournament_analyzer.py の分析結果を
2025 Bento Dashboard テーマのHTMLページとして出力する。

Usage:
    from src.review_report import save_review_html
    save_review_html(review_data)
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path


#-----メインエントリーポイント-----

def generate_review_html(review_data: dict) -> str:
    """振り返りHTMLページを生成する。"""
    t = review_data["tournament"]
    chart_data = _build_chart_data(review_data)
    chart_json = json.dumps(chart_data, ensure_ascii=False)
    chart_json = chart_json.replace("</", "<\\/")

    parts = [
        _head(t["name"]),
        "<body>",
        '<div class="orb orb-1"></div><div class="orb orb-2"></div><div class="orb orb-3"></div>',
        _header(review_data),
        _nav(),
        '<div class="container">',
        _section_overview(review_data),
        _section_group_results(review_data),
        _section_insights(review_data),
        "</div>",
        _footer(),
        _script(chart_json),
        "</body></html>",
    ]
    return "\n".join(parts)


def save_review_html(
    review_data: dict,
    output_dir: str = "data/output",
) -> Path:
    """振り返りHTMLを生成・保存する。"""
    html = generate_review_html(review_data)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    tid = review_data["tournament"]["id"]
    archive_path = out / f"review_{tid}.html"
    archive_path.write_text(html, encoding="utf-8")

    latest_path = out / "review.html"
    shutil.copy2(archive_path, latest_path)

    print(f"[INFO] Saved review report to {latest_path}")
    return latest_path


#-----ユーティリティ-----

def _esc(text) -> str:
    """HTML特殊文字エスケープ。"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(rate: float) -> str:
    """0-1のrateをパーセント文字列に変換。"""
    return f"{rate:.0%}"


#-----HTML構築ブロック-----

def _head(tournament_name: str) -> str:
    """2025 Bento Dashboard CSS + review固有スタイル。"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(tournament_name)} - Post-Tournament Review</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.5.0/chart.umd.min.js"></script>
<style>
:root {{
  --bg: #09090b;
  --surface: rgba(255,255,255,0.04);
  --surface-hover: rgba(255,255,255,0.07);
  --border: rgba(255,255,255,0.06);
  --border-hover: rgba(255,255,255,0.12);
  --text: #fafafa;
  --text2: #a1a1aa;
  --text3: #52525b;
  --accent: #22c55e;
  --accent2: #3b82f6;
  --accent3: #f59e0b;
  --accent4: #ef4444;
  --glow: 0 0 40px rgba(34,197,94,0.08);
  --radius: 16px;
  --radius-lg: 24px;
  --correct: #22c55e;
  --wrong: #ef4444;
}}
*{{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:'Inter',system-ui,-apple-system,sans-serif;
  background:var(--bg); color:var(--text);
  min-height:100vh; overflow-x:hidden;
  -webkit-font-smoothing:antialiased;
}}

/* ---- Animated gradient orbs ---- */
.orb {{ position:fixed; border-radius:50%; filter:blur(100px); pointer-events:none; z-index:0; opacity:0.35; }}
.orb-1 {{ width:600px; height:600px; top:-150px; left:-100px;
  background:radial-gradient(circle, rgba(34,197,94,0.4) 0%, transparent 70%);
  animation: float1 20s ease-in-out infinite; }}
.orb-2 {{ width:500px; height:500px; top:40%; right:-120px;
  background:radial-gradient(circle, rgba(59,130,246,0.3) 0%, transparent 70%);
  animation: float2 25s ease-in-out infinite; }}
.orb-3 {{ width:400px; height:400px; bottom:-80px; left:40%;
  background:radial-gradient(circle, rgba(245,158,11,0.2) 0%, transparent 70%);
  animation: float3 22s ease-in-out infinite; }}
@keyframes float1 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(60px,40px) }} }}
@keyframes float2 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(-50px,30px) }} }}
@keyframes float3 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(30px,-40px) }} }}

/* ---- Dot grid ---- */
body::before {{
  content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image: radial-gradient(rgba(255,255,255,0.05) 1px, transparent 1px);
  background-size: 24px 24px;
}}

/* ---- Header ---- */
.header {{
  position:sticky; top:0; z-index:100;
  background:rgba(9,9,11,0.75);
  backdrop-filter:blur(20px); -webkit-backdrop-filter:blur(20px);
  border-bottom:1px solid var(--border);
  padding:14px 24px;
}}
.header h1 {{ font-size:1.3em; font-weight:800; letter-spacing:-0.02em; }}
.header .sub {{ font-size:0.78em; color:var(--text3); margin-top:2px; }}
.header .headline {{
  font-size:0.85em; color:var(--text2); margin-top:6px;
  font-style:italic;
}}

/* ---- Nav pill tabs ---- */
.nav {{
  position:sticky; top:55px; z-index:99;
  background:rgba(9,9,11,0.7);
  backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-bottom:1px solid var(--border);
  padding:8px 16px; display:flex; gap:4px;
}}
.tab {{
  padding:8px 18px; cursor:pointer;
  font-size:0.8em; font-weight:500; white-space:nowrap;
  color:var(--text3); border-radius:8px; border:none;
  transition: all 0.2s; font-family:'Inter',system-ui,sans-serif;
  background:transparent;
}}
.tab:hover {{ color:var(--text2); background:var(--surface); }}
.tab.active {{
  color:var(--bg); background:var(--accent);
  font-weight:600; box-shadow: 0 0 20px rgba(34,197,94,0.25);
}}

.container {{ max-width:1200px; margin:0 auto; padding:16px; position:relative; z-index:1; }}
.section {{ display:none; }}
.section.active {{ display:block; animation:fadeIn 0.3s ease; }}
@keyframes fadeIn {{ from{{opacity:0;transform:translateY(8px)}} to{{opacity:1;transform:translateY(0)}} }}

/* ---- Scroll reveal ---- */
.reveal {{
  opacity:0; transform:translateY(24px);
  transition: opacity 0.6s cubic-bezier(0.16,1,0.3,1), transform 0.6s cubic-bezier(0.16,1,0.3,1);
}}
.reveal.visible {{ opacity:1; transform:translateY(0); }}

/* ---- Big number cards ---- */
.stat-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; margin:16px 0; }}
.stat-card {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); padding:20px; text-align:center;
  transition: all 0.3s;
}}
.stat-card:hover {{ border-color:var(--border-hover); box-shadow:var(--glow); }}
.stat-card .label {{ font-size:0.72em; color:var(--text3); text-transform:uppercase; letter-spacing:0.06em; font-weight:600; }}
.stat-card .big {{
  font-size:2.8em; font-weight:900; letter-spacing:-0.04em; line-height:1.2; margin:4px 0;
}}
.stat-card .detail {{ font-size:0.75em; color:var(--text3); }}
.stat-card.correct .big {{ color:var(--correct); }}
.stat-card.neutral .big {{
  background:linear-gradient(135deg, #22c55e, #3b82f6);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
}}

/* ---- Chart container ---- */
.chart-container {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); padding:20px; margin:16px 0;
}}
.chart-container h3 {{
  font-size:0.82em; font-weight:600; color:var(--text2);
  margin-bottom:12px; text-transform:uppercase; letter-spacing:0.04em;
}}
.chart-wrap {{ height:250px; position:relative; }}

/* ---- Bento group result cards ---- */
.bento {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:12px; }}
.result-card {{
  background:var(--surface); border:2px solid var(--border);
  border-radius:var(--radius); overflow:hidden;
  transition: all 0.35s cubic-bezier(0.16,1,0.3,1);
}}
.result-card:hover {{ box-shadow: 0 20px 40px rgba(0,0,0,0.3); }}
.result-card.correct {{ border-color: rgba(34,197,94,0.35); }}
.result-card.wrong {{ border-color: rgba(239,68,68,0.35); }}
.result-hdr {{
  display:flex; justify-content:space-between; align-items:center;
  padding:14px 18px;
}}
.result-badge {{
  padding:3px 12px; border-radius:100px; font-size:0.65em; font-weight:700;
  letter-spacing:0.03em; text-transform:uppercase;
}}
.result-badge.correct {{ background:rgba(34,197,94,0.15); color:var(--correct); }}
.result-badge.wrong {{ background:rgba(239,68,68,0.15); color:var(--wrong); }}
.grp-badge {{ font-size:0.7em; font-weight:700; color:var(--text3); letter-spacing:0.08em; text-transform:uppercase; }}

.result-picks {{
  padding:4px 18px 8px; font-size:0.82em;
}}
.result-picks .predicted {{ color:var(--text3); }}
.result-picks .actual {{ color:var(--text); font-weight:600; margin-top:2px; }}

.result-table {{
  width:100%; border-collapse:collapse; font-size:0.75em;
}}
.result-table th {{
  text-align:left; padding:6px 12px; font-weight:600;
  color:var(--text3); border-top:1px solid var(--border);
  border-bottom:1px solid var(--border); font-size:0.88em;
  text-transform:uppercase; letter-spacing:0.04em;
}}
.result-table td {{
  padding:6px 12px; border-bottom:1px solid rgba(255,255,255,0.03);
  color:var(--text2);
}}
.result-table tr.winner td {{ color:var(--correct); font-weight:600; }}
.delta-up {{ color:var(--correct); }}
.delta-down {{ color:var(--wrong); }}
.delta-same {{ color:var(--text3); }}

/* ---- Insight cards ---- */
.insight-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; margin:16px 0; }}
.insight-card {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); padding:18px;
}}
.insight-card h4 {{
  font-size:0.78em; font-weight:600; color:var(--text2);
  margin-bottom:10px; text-transform:uppercase; letter-spacing:0.04em;
}}
.upset-item {{
  padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03);
  font-size:0.82em;
}}
.upset-item:last-child {{ border-bottom:none; }}
.upset-item .group-label {{ color:var(--accent3); font-weight:600; }}
.upset-item .detail {{ color:var(--text3); margin-top:2px; }}
.upset-item .traits {{
  display:flex; gap:4px; margin-top:4px; flex-wrap:wrap;
}}
.trait-chip {{
  background:rgba(245,158,11,0.1); color:var(--accent3);
  padding:1px 8px; border-radius:4px; font-size:0.78em; font-weight:500;
}}

.takeaway-list {{ list-style:none; }}
.takeaway-list li {{
  padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.03);
  font-size:0.82em; color:var(--text2); line-height:1.5;
}}
.takeaway-list li:last-child {{ border-bottom:none; }}
.takeaway-list li::before {{ content:'→ '; color:var(--accent); font-weight:600; }}

.footer {{
  text-align:center; padding:20px 16px; color:var(--text3);
  font-size:0.72em; margin-top:24px;
  border-top:1px solid var(--border);
  position:relative; z-index:1;
}}
.footer a {{ color:var(--accent); text-decoration:none; font-weight:500; }}
.footer a:hover {{ text-decoration:underline; }}

/* ---- Counter animation ---- */
.counter {{ display:inline-block; }}

@media(max-width:768px){{
  .header h1 {{ font-size:1.05em; }}
  .tab {{ padding:7px 14px; font-size:0.78em; }}
  .stat-grid {{ grid-template-columns:1fr 1fr; }}
  .big {{ font-size:2em!important; }}
  .nav {{ top:50px; }}
}}
</style>
</head>"""


def _header(review_data: dict) -> str:
    """ヘッダー: トーナメント名 + 結果ヘッドライン。"""
    t = review_data["tournament"]
    s = review_data["summary"]

    dates = ""
    if t.get("start_date") and t.get("end_date"):
        dates = f" | {t['start_date']} - {t['end_date']}"

    model = t.get("model_version", "")
    model_tag = f'<span style="background:var(--accent2);color:#fff;padding:2px 10px;border-radius:6px;font-size:0.68em;font-weight:700;">{_esc(model)}</span>' if model else ""

    return f"""<div class="header">
<h1>Post-Tournament Review</h1>
<div class="sub">{_esc(t['name'])}{dates} {model_tag}</div>
<div class="headline">{_esc(s['headline'])}</div>
</div>"""


def _nav() -> str:
    """3タブナビゲーション。"""
    return """<div class="nav">
<button class="tab active" onclick="showSection('overview')">Overview</button>
<button class="tab" onclick="showSection('groups')">Group Results</button>
<button class="tab" onclick="showSection('insights')">Insights</button>
</div>"""


def _section_overview(review_data: dict) -> str:
    """Tab 1: 概要 — 大数字カード + シグナル精度チャート。"""
    s = review_data["summary"]
    sa = review_data["signal_accuracy"]

    html = '<div id="sec-overview" class="section active">'

    # 大数字カード
    html += '<div class="stat-grid">'
    html += f"""<div class="stat-card correct reveal">
<div class="label">ML Win Rate</div>
<div class="big"><span class="counter" data-target="{s['ml_win_rate']*100:.0f}">{s['ml_win_rate']*100:.0f}</span>%</div>
<div class="detail">{s['ml_correct']}/{s['total_groups']} groups</div>
</div>"""

    odds_r = sa.get("odds_only", {})
    html += f"""<div class="stat-card reveal">
<div class="label">Odds Win Rate</div>
<div class="big" style="color:var(--accent2);"><span class="counter" data-target="{s['odds_win_rate']*100:.0f}">{s['odds_win_rate']*100:.0f}</span>%</div>
<div class="detail">{odds_r.get('correct',0)}/{odds_r.get('total',0)} groups</div>
</div>"""

    html += f"""<div class="stat-card neutral reveal">
<div class="label">ML Top-2 Rate</div>
<div class="big"><span class="counter" data-target="{s['ml_top2_rate']*100:.0f}">{s['ml_top2_rate']*100:.0f}</span>%</div>
<div class="detail">{s['ml_top2']}/{s['total_groups']} groups</div>
</div>"""

    upset_count = len(review_data.get("upsets", []))
    html += f"""<div class="stat-card reveal">
<div class="label">Upsets</div>
<div class="big" style="color:var(--accent3);">{upset_count}</div>
<div class="detail">ML #1 lost</div>
</div>"""
    html += "</div>"

    # シグナル精度比較チャート
    html += """<div class="chart-container reveal">
<h3>Signal Accuracy Comparison</h3>
<div class="chart-wrap"><canvas id="chart-signal"></canvas></div>
</div>"""

    # Game Score セクション
    gs = review_data.get("game_score")
    if gs:
        ml_gs = gs.get("ml", {})
        opt_gs = gs.get("optimal", {})
        best_st = gs.get("best_strategy", "ml")

        if ml_gs.get("per_group"):
            html += '<div class="chart-container reveal">'
            html += '<h3>Game Score (Lower = Better)</h3>'

            # メインスコアカード
            ml_bonuses = ml_gs.get("bonuses", {})
            html += '<div class="stat-grid" style="margin-bottom:16px;">'
            html += f"""<div class="stat-card reveal">
<div class="label">ML Game Score</div>
<div class="big" style="color:var(--accent);">{ml_gs['total']}</div>
<div class="detail">Raw={ml_gs['raw_sum']} - Bonus={ml_bonuses.get('total', 0)}</div>
</div>"""

            odds_gs = gs.get("odds", {})
            if odds_gs.get("per_group"):
                html += f"""<div class="stat-card reveal">
<div class="label">Odds Score</div>
<div class="big" style="color:var(--accent2);">{odds_gs['total']}</div>
<div class="detail">Raw={odds_gs['raw_sum']} - Bonus={odds_gs.get('bonuses', {}).get('total', 0)}</div>
</div>"""

            html += f"""<div class="stat-card reveal">
<div class="label">Optimal</div>
<div class="big" style="color:var(--text3);">{opt_gs['total']}</div>
<div class="detail">Raw={opt_gs['raw_sum']} - Bonus={opt_gs.get('bonuses', {}).get('total', 0)}</div>
</div>"""
            html += "</div>"

            # フィールド情報
            field_size = gs.get("field_size", 0)
            cut_count = gs.get("cut_count", 0)
            if field_size:
                html += f'<div style="font-size:0.75em;color:var(--text3);margin-bottom:12px;">'
                html += f'Field: {field_size} players | Cut: {cut_count} made | '
                html += f'Bonus breakdown: '
                for d in ml_bonuses.get("details", []):
                    html += f'{_esc(d)} '
                html += '</div>'

            # 戦略比較チャート
            html += '<div class="chart-wrap"><canvas id="chart-position"></canvas></div>'
            html += "</div>"

            # グループ別Game Score詳細テーブル
            html += '<div class="chart-container reveal">'
            html += '<h3>Game Score by Group</h3>'
            html += '<table class="result-table"><thead><tr>'
            html += '<th>Group</th><th>Pick</th><th>WGR</th><th>HC</th><th>ESPN</th><th>CUT</th><th>Game</th>'
            html += '</tr></thead><tbody>'
            for pg in ml_gs["per_group"]:
                for p in pg["picks"]:
                    won_cls = ' class="winner"' if p.get("won") else ""
                    cut_mark = "CUT" if p["is_cut"] else ""
                    espn_pos = p["espn_pos"] if p["espn_pos"] is not None else "-"
                    html += f'<tr{won_cls}><td>G{pg["group_id"]}</td>'
                    html += f'<td>{_esc(p["name"])}</td>'
                    html += f'<td>{p["wgr"]}</td>'
                    html += f'<td>{p["handicap"]}</td>'
                    html += f'<td>{espn_pos}</td>'
                    html += f'<td>{cut_mark}</td>'
                    html += f'<td>{p["game_score"]}</td></tr>'
            html += '</tbody></table></div>'

    html += "</div>"
    return html


def _section_group_results(review_data: dict) -> str:
    """Tab 2: グループ別結果カード。"""
    groups = review_data["groups"]

    html = '<div id="sec-groups" class="section">'
    html += '<div class="bento">'

    for g in groups:
        correct = g["prediction_correct"]
        cls = "correct" if correct else "wrong"
        badge_text = "CORRECT" if correct else "UPSET" if g["upset"] else "MISS"

        html += f'<div class="result-card {cls} reveal">'
        html += f"""<div class="result-hdr">
<span class="grp-badge">Group {g['group_id']}</span>
<span class="result-badge {cls}">{badge_text}</span>
</div>"""

        html += '<div class="result-picks">'
        html += f'<div class="predicted">Predicted: {_esc(g["predicted_winner"])}</div>'
        html += f'<div class="actual">Actual: {_esc(g["actual_winner"])}</div>'
        html += "</div>"

        # ランキング比較テーブル
        html += '<table class="result-table">'
        html += "<thead><tr><th>#</th><th>Player</th><th>ML</th><th>Pred</th><th>Actual</th><th>ESPN</th><th>Δ</th></tr></thead>"
        html += "<tbody>"

        for p in g["players"]:
            is_winner = p["actual_rank"] == 1
            row_cls = ' class="winner"' if is_winner else ""

            ml_score = f"{p['ml_score']:.0f}" if p["ml_score"] else "-"
            pred_rank = f"#{p['ml_rank']}"
            actual_rank = f"#{p['actual_rank']}" if p["actual_rank"] else "N/A"

            # ESPN順位表示
            espn_pos = p.get("espn_position")
            if espn_pos is not None:
                espn_str = str(espn_pos)
            else:
                score_val = p.get("score", "")
                if score_val and str(score_val).strip().upper() in ("CUT", "WD", "DQ", "MDF"):
                    espn_str = f'<span style="color:var(--accent3);">{str(score_val).strip().upper()}</span>'
                else:
                    espn_str = "-"

            # rank_delta表示
            delta = p.get("rank_delta")
            if delta is None:
                delta_str = '<span class="delta-same">-</span>'
            elif delta == 0:
                delta_str = '<span class="delta-same">=</span>'
            elif delta > 0:
                delta_str = f'<span class="delta-up">↑{delta}</span>'
            else:
                delta_str = f'<span class="delta-down">↓{abs(delta)}</span>'

            html += f"<tr{row_cls}><td>{p['ml_rank']}</td><td>{_esc(p['name'])}</td>"
            html += f"<td>{ml_score}</td><td>{pred_rank}</td><td>{actual_rank}</td>"
            html += f"<td>{espn_str}</td><td>{delta_str}</td></tr>"

        html += "</tbody></table>"
        html += "</div>"

    html += "</div></div>"
    return html


def _section_insights(review_data: dict) -> str:
    """Tab 3: インサイト — キャリブレーション + アップセット + テイクアウェイ。"""
    html = '<div id="sec-insights" class="section">'

    # 信頼度キャリブレーション
    html += """<div class="chart-container reveal">
<h3>Confidence Calibration</h3>
<div class="chart-wrap"><canvas id="chart-confidence"></canvas></div>
</div>"""

    html += '<div class="insight-grid">'

    # アップセットカード
    upsets = review_data.get("upsets", [])
    upset_patterns = review_data.get("upset_patterns", {})
    html += '<div class="insight-card reveal">'
    html += f'<h4>Upset Analysis ({len(upsets)} group{"s" if len(upsets) != 1 else ""})</h4>'

    if upsets:
        for u in upsets:
            html += '<div class="upset-item">'
            html += f'<div><span class="group-label">Group {u["group_id"]}</span>: '
            html += f'{_esc(u["ml_pick"])} (ML #{u.get("ml_pick_actual_rank", "?")}) '
            html += f'lost to {_esc(u["actual_winner"])} (ML #{u["actual_winner_ml_rank"]})</div>'
            html += f'<div class="detail">Score gap: {u["score_gap"]:.1f} points</div>'

            if u.get("upset_traits"):
                html += '<div class="traits">'
                for trait in u["upset_traits"]:
                    html += f'<span class="trait-chip">{_esc(trait)}</span>'
                html += "</div>"
            html += "</div>"

        if upset_patterns.get("common_traits"):
            html += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border);font-size:0.78em;color:var(--text3);">'
            html += "Common patterns: " + ", ".join(upset_patterns["common_traits"])
            html += "</div>"
    else:
        html += '<div style="font-size:0.82em;color:var(--text3);">No upsets — all ML #1 picks won their group!</div>'

    html += "</div>"

    # キーテイクアウェイ
    takeaways = review_data.get("summary", {}).get("key_takeaways", [])
    html += '<div class="insight-card reveal">'
    html += "<h4>Key Takeaways</h4>"
    html += '<ul class="takeaway-list">'
    for t in takeaways:
        html += f"<li>{_esc(t)}</li>"
    html += "</ul></div>"

    html += "</div>"  # insight-grid

    # グループ結果サマリーチャート
    html += """<div class="chart-container reveal">
<h3>Group Results Summary</h3>
<div class="chart-wrap"><canvas id="chart-groups"></canvas></div>
</div>"""

    html += "</div>"
    return html


def _footer() -> str:
    """フッター。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<div class="footer">
Post-Tournament Review generated {now} |
<a href="index.html">View Pre-Tournament Dashboard →</a>
</div>"""


#-----Chart.js データ構築-----

def _build_chart_data(review_data: dict) -> dict:
    """Chart.js用JSONデータを構築する。"""
    sa = review_data["signal_accuracy"]
    conf = review_data["confidence_calibration"]
    groups = review_data["groups"]

    # 1. シグナル精度比較バーチャート
    signal_labels = []
    signal_values = []
    signal_colors = []
    signal_map = [
        ("odds_only", "Odds", "#3b82f6"),
        ("stats_only", "Stats", "#22c55e"),
        ("fit_only", "Fit", "#f59e0b"),
        ("combined_ml", "ML Combined", "#a78bfa"),
    ]
    for key, label, color in signal_map:
        d = sa.get(key, {})
        if d.get("total", 0) > 0:
            signal_labels.append(label)
            signal_values.append(round(d["rate"] * 100, 1))
            signal_colors.append(color)

    # 2. 信頼度キャリブレーションバーチャート
    conf_labels = []
    conf_values = []
    conf_colors = ["#22c55e", "#f59e0b", "#ef4444"]
    conf_details = []
    for i, level in enumerate(["High", "Medium", "Low"]):
        d = conf.get(level, {})
        if d.get("total", 0) > 0:
            conf_labels.append(level)
            conf_values.append(round(d["rate"] * 100, 1))
            conf_details.append(f"{d['correct']}/{d['total']}")
        elif d.get("total", 0) == 0 and level in conf:
            conf_labels.append(level)
            conf_values.append(0)
            conf_details.append("0/0")

    # 3. グループ結果横棒チャート
    group_labels = [f"G{g['group_id']}" for g in groups]
    group_correct = [1 if g["prediction_correct"] else 0 for g in groups]
    group_wrong = [0 if g["prediction_correct"] else 1 for g in groups]

    # 4. Game Scoreバーチャート
    gs = review_data.get("game_score", {})
    pos_labels = []
    pos_values = []
    pos_colors = []
    pos_map = [
        ("ml", "ML", "#22c55e"),
        ("odds", "Odds", "#3b82f6"),
        ("stats", "Stats", "#f59e0b"),
        ("fit", "Fit", "#a78bfa"),
        ("optimal", "Optimal", "#52525b"),
    ]
    for key, label, color in pos_map:
        d = gs.get(key, {})
        if key == "optimal":
            if d.get("total") is not None:
                pos_labels.append(label)
                pos_values.append(d["total"])
                pos_colors.append(color)
        elif d.get("per_group"):
            pos_labels.append(label)
            pos_values.append(d["total"])
            pos_colors.append(color)

    return {
        "signal": {
            "labels": signal_labels,
            "values": signal_values,
            "colors": signal_colors,
        },
        "confidence": {
            "labels": conf_labels,
            "values": conf_values,
            "colors": conf_colors[:len(conf_labels)],
            "details": conf_details,
        },
        "groups": {
            "labels": group_labels,
            "correct": group_correct,
            "wrong": group_wrong,
        },
        "position": {
            "labels": pos_labels,
            "values": pos_values,
            "colors": pos_colors,
        },
    }


def _script(chart_json: str) -> str:
    """Chart.js初期化 + タブ切替 + アニメーション。"""
    return f"""<script>
const CD = {chart_json};

/* ---- Tab switching ---- */
function showSection(id) {{
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const sec = document.getElementById('sec-' + id);
  if (sec) sec.classList.add('active');
  event.target.classList.add('active');
  initCharts();
}}

/* ---- Scroll reveal ---- */
const observer = new IntersectionObserver((entries) => {{
  entries.forEach((entry, i) => {{
    if (entry.isIntersecting) {{
      setTimeout(() => entry.target.classList.add('visible'), i * 60);
      observer.unobserve(entry.target);
    }}
  }});
}}, {{ threshold: 0.1 }});
document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

/* ---- Counter animation ---- */
function animateCounters() {{
  document.querySelectorAll('.counter').forEach(el => {{
    const target = parseFloat(el.dataset.target);
    if (isNaN(target) || el.dataset.animated) return;
    el.dataset.animated = '1';
    const duration = 1000;
    const start = performance.now();
    function step(now) {{
      const p = Math.min((now - start) / duration, 1);
      const ease = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * ease);
      if (p < 1) requestAnimationFrame(step);
    }}
    requestAnimationFrame(step);
  }});
}}
setTimeout(animateCounters, 300);

/* ---- Charts ---- */
let chartsInit = false;
function initCharts() {{
  if (chartsInit) return;
  chartsInit = true;

  const grid = 'rgba(255,255,255,0.06)';
  const textC = '#a1a1aa';
  const font = {{ family: "'Inter', system-ui, sans-serif" }};

  /* Signal accuracy bar chart */
  const sigCtx = document.getElementById('chart-signal');
  if (sigCtx && CD.signal.labels.length) {{
    new Chart(sigCtx, {{
      type: 'bar',
      data: {{
        labels: CD.signal.labels,
        datasets: [{{
          data: CD.signal.values,
          backgroundColor: CD.signal.colors,
          borderRadius: 6,
          barThickness: 32,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{ label: ctx => ctx.parsed.x + '%' }} }}
        }},
        scales: {{
          x: {{
            min: 0, max: 100,
            grid: {{ color: grid }},
            ticks: {{ color: textC, font, callback: v => v + '%' }}
          }},
          y: {{ grid: {{ display: false }}, ticks: {{ color: textC, font }} }}
        }}
      }}
    }});
  }}

  /* Confidence calibration bar chart */
  const confCtx = document.getElementById('chart-confidence');
  if (confCtx && CD.confidence.labels.length) {{
    new Chart(confCtx, {{
      type: 'bar',
      data: {{
        labels: CD.confidence.labels,
        datasets: [{{
          data: CD.confidence.values,
          backgroundColor: CD.confidence.colors,
          borderRadius: 6,
          barThickness: 40,
        }}]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{
            label: (ctx) => {{
              const d = CD.confidence.details[ctx.dataIndex];
              return ctx.parsed.y + '% (' + d + ')';
            }}
          }} }}
        }},
        scales: {{
          y: {{
            min: 0, max: 100,
            grid: {{ color: grid }},
            ticks: {{ color: textC, font, callback: v => v + '%' }}
          }},
          x: {{ grid: {{ display: false }}, ticks: {{ color: textC, font }} }}
        }}
      }}
    }});
  }}

  /* Group results stacked bar */
  const grpCtx = document.getElementById('chart-groups');
  if (grpCtx && CD.groups.labels.length) {{
    new Chart(grpCtx, {{
      type: 'bar',
      data: {{
        labels: CD.groups.labels,
        datasets: [
          {{
            label: 'Correct',
            data: CD.groups.correct,
            backgroundColor: '#22c55e',
            borderRadius: 4,
          }},
          {{
            label: 'Wrong',
            data: CD.groups.wrong,
            backgroundColor: '#ef4444',
            borderRadius: 4,
          }}
        ]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ labels: {{ color: textC, font }} }} }},
        scales: {{
          x: {{
            stacked: true, max: 1,
            grid: {{ color: grid }},
            ticks: {{ display: false }}
          }},
          y: {{
            stacked: true,
            grid: {{ display: false }},
            ticks: {{ color: textC, font }}
          }}
        }}
      }}
    }});
  }}

  /* Position Score comparison bar chart */
  const posCtx = document.getElementById('chart-position');
  if (posCtx && CD.position && CD.position.labels.length) {{
    new Chart(posCtx, {{
      type: 'bar',
      data: {{
        labels: CD.position.labels,
        datasets: [{{
          data: CD.position.values,
          backgroundColor: CD.position.colors,
          borderRadius: 6,
          barThickness: 32,
        }}]
      }},
      options: {{
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ callbacks: {{ label: ctx => 'Score: ' + ctx.parsed.x }} }}
        }},
        scales: {{
          x: {{
            grid: {{ color: grid }},
            ticks: {{ color: textC, font }}
          }},
          y: {{ grid: {{ display: false }}, ticks: {{ color: textC, font }} }}
        }}
      }}
    }});
  }}
}}

/* Init charts on page load */
initCharts();
</script>"""
