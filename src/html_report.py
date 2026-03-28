"""HTML Report Generator - Golf Betting Analysis Dashboard.

Chart.js 4.5.0ベースの予測ダッシュボード。
2025 Bento Dashboard テーマ: ドットグリッド + グラスカード + Glow UI。
4タブ構成: Dashboard / Odds / Players / Game Strategy
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.group_analyzer import GroupAnalysisResult, GroupPlayer


#----- ユーティリティ関数 -----


def _fmt_odds(odds: int | None) -> str:
    """オッズ整数を表示文字列に変換。"""
    if odds is None:
        return "-"
    return f"+{odds}" if odds > 0 else str(odds)


def _escape(text: str) -> str:
    """HTML特殊文字エスケープ。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wgr_to_score(wgr_str: str) -> float:
    """WGR文字列を0-100スコアに変換。WGR1→100, WGR201+→0。"""
    try:
        wgr = int(wgr_str)
        return max(0.0, min(100.0, 100.0 - (wgr - 1) * 0.5))
    except (ValueError, TypeError):
        return 0.0


#----- メインエントリーポイント -----


def generate_html(
    result: GroupAnalysisResult,
    course_fit: dict | None = None,
    ml_result: dict | None = None,
    egs_result=None,
    egs_v2_result=None,
) -> str:
    """5タブ構成のChart.jsダッシュボードHTML生成。"""
    chart_data = _build_chart_data(result.groups, ml_result, egs_result)
    chart_json = json.dumps(chart_data, ensure_ascii=False)
    chart_json = chart_json.replace("</", "<\\/")

    has_egs = egs_result is not None
    has_picks_comparison = has_egs and ml_result and ml_result.get("predictions")

    parts = [
        _head(result.tournament_name),
        "<body>",
        '<div class="orb orb-1"></div><div class="orb orb-2"></div><div class="orb orb-3"></div>',
        _header(result.tournament_name, result.generated_at, result.bookmakers, ml_result),
        _nav(has_egs=has_egs, has_picks_comparison=has_picks_comparison),
        '<div class="container">',
        _section_dashboard(result.groups, ml_result),
        _section_odds_tab(result.groups, result.bookmakers),
        _section_players_tab(result.groups, course_fit),
    ]
    if has_egs:
        parts.append(_section_game_tab(result.groups, ml_result, egs_result))
    if has_picks_comparison:
        parts.append(_section_picks_tab(result.groups, ml_result, egs_result, egs_v2_result))
    parts += [
        "</div>",
        _footer(result.generated_at),
        _script(chart_json),
        "</body></html>",
    ]
    return "\n".join(parts)


def save_html(
    result: GroupAnalysisResult,
    output_path: str = "data/output/dashboard.html",
    course_fit: dict | None = None,
    ml_result: dict | None = None,
    egs_result=None,
    egs_v2_result=None,
) -> Path:
    """HTML生成・ファイル保存。"""
    html = generate_html(
        result, course_fit=course_fit, ml_result=ml_result,
        egs_result=egs_result, egs_v2_result=egs_v2_result,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"[INFO] Saved HTML report to {path}")
    return path


#----- HTML構築ブロック -----


def _head(title: str) -> str:
    """2025 Bento Dashboard CSS + Chart.js 4.5.0 CDN。"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape(title)} - Golf Prediction Dashboard</title>
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
  --glow-accent: 0 0 60px rgba(34,197,94,0.12);
  --radius: 16px;
  --radius-lg: 24px;
}}
*{{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:'Inter',system-ui,-apple-system,sans-serif;
  background:var(--bg); color:var(--text);
  min-height:100vh;
  overflow-x:hidden;
  -webkit-font-smoothing:antialiased;
}}

/* ---- Animated gradient orbs ---- */
.orb {{
  position:fixed; border-radius:50%; filter:blur(100px);
  pointer-events:none; z-index:0; opacity:0.35;
}}
.orb-1 {{
  width:600px; height:600px; top:-150px; left:-100px;
  background:radial-gradient(circle, rgba(34,197,94,0.4) 0%, transparent 70%);
  animation: float1 20s ease-in-out infinite;
}}
.orb-2 {{
  width:500px; height:500px; top:40%; right:-120px;
  background:radial-gradient(circle, rgba(59,130,246,0.3) 0%, transparent 70%);
  animation: float2 25s ease-in-out infinite;
}}
.orb-3 {{
  width:400px; height:400px; bottom:-80px; left:40%;
  background:radial-gradient(circle, rgba(245,158,11,0.2) 0%, transparent 70%);
  animation: float3 22s ease-in-out infinite;
}}
@keyframes float1 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(60px,40px) }} }}
@keyframes float2 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(-50px,30px) }} }}
@keyframes float3 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(30px,-40px) }} }}

/* ---- Dot grid overlay ---- */
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
.header h1 {{
  font-size:1.3em; font-weight:800; color:var(--text);
  letter-spacing:-0.02em;
}}
.header .sub {{ font-size:0.78em; color:var(--text3); margin-top:2px; letter-spacing:0.02em; }}
.header-row {{ display:flex; align-items:center; gap:14px; margin-top:8px; flex-wrap:wrap; }}
.model-tag {{
  background:var(--accent); color:#000; padding:2px 10px;
  border-radius:6px; font-size:0.68em; font-weight:700;
  letter-spacing:0.02em;
}}
.weight-box {{ display:flex; align-items:center; gap:10px; }}
.weight-box canvas {{ width:52px!important; height:52px!important; }}
.weight-labels {{ font-size:0.72em; color:var(--text2); line-height:1.7; }}

/* ---- Nav pill tabs ---- */
.nav {{
  position:sticky; top:55px; z-index:99;
  background:rgba(9,9,11,0.7);
  backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px);
  border-bottom:1px solid var(--border);
  padding:8px 16px; display:flex; gap:4px; overflow-x:auto;
}}
.tab {{
  padding:8px 18px; cursor:pointer;
  font-size:0.8em; font-weight:500; white-space:nowrap;
  color:var(--text3);
  border-radius:8px; border:none;
  transition: all 0.2s cubic-bezier(0.4,0,0.2,1);
  font-family:'Inter',system-ui,sans-serif;
  background:transparent;
}}
.tab:hover {{ color:var(--text2); background:var(--surface); }}
.tab.active {{
  color:var(--bg); background:var(--accent);
  font-weight:600;
  box-shadow: 0 0 20px rgba(34,197,94,0.25);
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

/* ---- Animated gradient border wrapper ---- */
.glow-border {{
  position:relative; border-radius:var(--radius); padding:1px;
  background: linear-gradient(135deg, rgba(34,197,94,0.3), rgba(59,130,246,0.2), rgba(245,158,11,0.2), rgba(34,197,94,0.3));
  background-size:300% 300%;
  animation: borderShift 6s ease-in-out infinite;
}}
.glow-border > .dash-card {{ border:none; border-radius:calc(var(--radius) - 1px); }}
@keyframes borderShift {{
  0%,100%{{ background-position:0% 50% }}
  50%{{ background-position:100% 50% }}
}}

/* ---- Shimmer sweep ---- */
.shimmer {{
  position:relative; overflow:hidden;
}}
.shimmer::after {{
  content:''; position:absolute; top:0; left:-100%; width:60%; height:100%;
  background:linear-gradient(90deg, transparent, rgba(255,255,255,0.04), transparent);
  animation: shimmerSweep 4s ease-in-out infinite;
  pointer-events:none;
}}
@keyframes shimmerSweep {{
  0%{{ left:-100% }} 50%{{ left:120% }} 100%{{ left:120% }}
}}

/* ---- Animated gradient text ---- */
.gradient-text {{
  background: linear-gradient(135deg, #22c55e, #4ade80, #3b82f6, #22c55e);
  background-size:300% 300%;
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text;
  animation: textShift 4s ease-in-out infinite;
}}
@keyframes textShift {{
  0%,100%{{ background-position:0% 50% }} 50%{{ background-position:100% 50% }}
}}

/* ---- Pulse glow on High confidence ---- */
.conf-h {{
  animation: pulseGlow 2s ease-in-out infinite;
}}
@keyframes pulseGlow {{
  0%,100%{{ box-shadow:0 0 0 rgba(34,197,94,0) }}
  50%{{ box-shadow:0 0 12px rgba(34,197,94,0.3) }}
}}

/* ---- Star sparkle ---- */
@keyframes sparkle {{
  0%,100%{{ opacity:1; transform:scale(1) }}
  50%{{ opacity:0.6; transform:scale(1.3) }}
}}
.pick-name .star {{
  display:inline-block;
  animation: sparkle 2s ease-in-out infinite;
}}

/* ---- Counter animation placeholder ---- */
.counter {{ display:inline-block; }}

/* ---- Bento Dashboard cards ---- */
.bento {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:12px; }}

.dash-card {{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:var(--radius); overflow:hidden;
  transition: all 0.35s cubic-bezier(0.16,1,0.3,1);
  position:relative;
  transform-style:preserve-3d; perspective:800px;
}}
.dash-card::before {{
  content:''; position:absolute; inset:0; border-radius:var(--radius);
  background:linear-gradient(135deg, rgba(34,197,94,0.06) 0%, transparent 50%);
  opacity:0; transition:opacity 0.4s;
  pointer-events:none;
}}
.dash-card:hover {{
  border-color:var(--border-hover);
  box-shadow: var(--glow), 0 20px 40px rgba(0,0,0,0.3);
}}
.dash-card:hover::before {{ opacity:1; }}

.dash-hdr {{
  display:flex; justify-content:space-between; align-items:center;
  padding:14px 18px 0; cursor:pointer;
}}
.grp-badge {{
  font-size:0.7em; font-weight:700; color:var(--text3);
  letter-spacing:0.08em; text-transform:uppercase;
}}
.conf {{ padding:2px 10px; border-radius:100px; font-size:0.65em; font-weight:600; letter-spacing:0.03em; }}
.conf-h {{ background:rgba(34,197,94,0.12); color:var(--accent); border:1px solid rgba(34,197,94,0.2); }}
.conf-m {{ background:rgba(245,158,11,0.12); color:var(--accent3); border:1px solid rgba(245,158,11,0.2); }}
.conf-l {{ background:rgba(239,68,68,0.12); color:var(--accent4); border:1px solid rgba(239,68,68,0.2); }}

.dash-body {{ padding:12px 18px 14px; }}
.radar-wrap {{ width:100%; height:160px; margin-bottom:8px; }}
.pick-name {{
  font-size:0.92em; font-weight:600; color:var(--text); margin-bottom:2px;
  display:flex; align-items:center; gap:6px;
}}
.pick-name .star {{ color:var(--accent3); font-size:0.9em; }}
.pick-ml {{
  font-size:2.8em; font-weight:900; letter-spacing:-0.04em;
  line-height:1.1; margin:4px 0 10px;
}}
.ranks {{ display:flex; gap:4px; flex-wrap:wrap; }}
.rank-chip {{
  background:var(--surface); padding:3px 10px; border-radius:6px;
  border:1px solid var(--border);
  font-size:0.68em; color:var(--text3); font-weight:500;
  transition:border-color 0.15s;
}}
.rank-chip:hover {{ border-color:var(--border-hover); }}

.dash-toggle {{
  padding:8px 18px; text-align:center; cursor:pointer;
  color:var(--text3); font-size:0.72em; font-weight:500;
  border-top:1px solid var(--border);
  transition:all 0.15s; user-select:none;
}}
.dash-toggle:hover {{ color:var(--text2); background:var(--surface-hover); }}
.dash-detail {{
  border-top:1px solid var(--border);
  overflow:hidden;
  transition: max-height 0.4s cubic-bezier(0.16,1,0.3,1), opacity 0.3s ease;
  max-height:0; opacity:0;
}}
.dash-detail.open {{ max-height:2000px; opacity:1; }}
.detail-charts {{ display:grid; grid-template-columns:1fr; gap:16px; padding:12px 18px; max-width:420px; margin:0 auto; }}
.chart-box {{
  background:rgba(255,255,255,0.02); border:1px solid var(--border);
  border-radius:12px; padding:12px; min-height:180px;
}}
.chart-box h4 {{ font-size:0.7em; color:var(--text3); margin-bottom:8px; font-weight:600; text-transform:uppercase; letter-spacing:0.05em; }}

/* ---- Generic card ---- */
.card {{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:var(--radius); margin-bottom:12px; overflow:hidden;
  transition:border-color 0.2s;
}}
.card:hover {{ border-color:var(--border-hover); transform:translateY(-1px); transition:all 0.25s cubic-bezier(0.16,1,0.3,1); }}
.card-title {{
  padding:12px 16px; font-weight:600; font-size:0.82em;
  border-bottom:1px solid var(--border);
  color:var(--text2); letter-spacing:0.01em;
}}

/* ---- Table ---- */
.tbl-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:0.76em; }}
th {{
  text-align:left; padding:8px 10px;
  font-weight:600; color:var(--text3); white-space:nowrap;
  border-bottom:1px solid var(--border); font-size:0.88em;
  text-transform:uppercase; letter-spacing:0.04em;
}}
td {{ padding:7px 10px; border-bottom:1px solid rgba(255,255,255,0.03); white-space:nowrap; color:var(--text2); }}
tr {{ transition:background 0.1s; }}
tr:hover {{ background:rgba(255,255,255,0.03); }}
tr.fav td {{ color:var(--accent); font-weight:600; }}
.na {{ color:var(--text3); }}

/* ---- Badge ---- */
.badge {{ padding:2px 8px; border-radius:6px; font-size:0.68em; font-weight:600; display:inline-block; }}
.b-h {{ background:rgba(34,197,94,0.12); color:#22c55e; }}
.b-m {{ background:rgba(245,158,11,0.12); color:#f59e0b; }}
.b-l {{ background:rgba(239,68,68,0.12); color:#ef4444; }}
.b-na {{ background:rgba(255,255,255,0.04); color:var(--text3); }}

.book-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; margin-top:12px; }}

.footer {{
  text-align:center; padding:20px 16px; color:var(--text3);
  font-size:0.72em; margin-top:24px;
  border-top:1px solid var(--border);
  position:relative; z-index:1;
}}
.footer a {{ color:var(--accent); text-decoration:none; font-weight:500; }}
.footer a:hover {{ text-decoration:underline; }}

@media(max-width:768px){{
  .header h1 {{ font-size:1.05em; }}
  .tab {{ padding:7px 14px; font-size:0.78em; }}
  table {{ font-size:0.7em; }}
  td,th {{ padding:5px 7px; }}
  .detail-charts {{ grid-template-columns:1fr; }}
  .pick-ml {{ font-size:2em; }}
  .nav {{ top:50px; }}
}}
</style>
</head>"""


def _header(
    tournament: str, generated_at: str,
    bookmakers: list[str], ml_result: dict | None,
) -> str:
    """ヘッダー: トーナメント名 + モデル情報 + ドーナツチャート。"""
    try:
        dt = datetime.fromisoformat(generated_at)
        ts = dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        ts = generated_at

    has_ml = bool(ml_result and ml_result.get("predictions"))
    weights = ml_result.get("weights", {}) if ml_result else {}
    model_info = ml_result.get("model_info", {}) if ml_result else {}
    ver = ml_result.get("model_version", "") if ml_result else ""

    html = f"""<div class="header">
<h1>{_escape(tournament)}</h1>
<div class="sub">Generated {ts} &middot; {len(bookmakers)} bookmakers</div>"""

    if has_ml:
        w_odds = weights.get("odds", 0.45)
        w_stats = weights.get("stats", 0.35)
        w_fit = weights.get("course_fit", 0.20)
        n_samples = model_info.get("n_samples", 0)
        r2 = model_info.get("r2_cv")

        html += '<div class="header-row">'
        html += '<div class="weight-box">'
        html += '<canvas id="donut-weights"></canvas>'
        html += '<div class="weight-labels">'
        html += f'<span style="color:var(--accent2)">Odds {w_odds:.0%}</span><br>'
        html += f'<span style="color:var(--accent)">Stats {w_stats:.0%}</span><br>'
        html += f'<span style="color:var(--accent3)">Fit {w_fit:.0%}</span>'
        html += '</div></div>'
        html += f'<span class="model-tag">{_escape(ver)}</span>'
        if n_samples:
            html += f'<span style="font-size:0.7em;color:var(--text3)">{n_samples:,} samples</span>'
        if r2 is not None:
            html += f'<span style="font-size:0.7em;color:var(--text3)">R\u00B2 {r2:.4f}</span>'
        html += '</div>'

    html += "</div>"
    return html


def _nav(has_egs: bool = False, has_picks_comparison: bool = False) -> str:
    """タブ ピルナビゲーション。"""
    tabs = [
        ("dashboard", "Dashboard"),
        ("odds-tab", "Odds"),
        ("players-tab", "Players"),
    ]
    if has_egs:
        tabs.append(("game-tab", "Game Strategy"))
    if has_picks_comparison:
        tabs.append(("picks-tab", "Pick Comparison"))
    items = "".join(
        f'<div class="tab{" active" if i == 0 else ""}" '
        f'data-section="{sid}" onclick="showSection(\'{sid}\')">{label}</div>'
        for i, (sid, label) in enumerate(tabs)
    )
    return f'<div class="nav">{items}</div>'


#----- Dashboard タブ -----


def _section_dashboard(
    groups: dict[int, list[GroupPlayer]],
    ml_result: dict | None,
) -> str:
    """Bentoグリッド ダッシュボード。"""
    has_ml = bool(ml_result and ml_result.get("predictions"))
    predictions = ml_result.get("predictions", {}) if ml_result else {}

    html = '<div id="dashboard" class="section active"><div class="bento">'

    for gid in sorted(groups.keys()):
        players = groups[gid]
        html += _dash_card(gid, players, predictions, has_ml)

    html += "</div></div>"
    return html


def _dash_card(
    gid: int,
    players: list[GroupPlayer],
    predictions: dict,
    has_ml: bool,
) -> str:
    """1グループ分のBentoカード。"""
    if has_ml:
        ml_top, ml_pred = None, None
        for p in players:
            pred = predictions.get(p.name)
            if pred and (ml_pred is None or pred.ml_score > ml_pred.ml_score):
                ml_top, ml_pred = p, pred
        if ml_top is None:
            ml_top = players[0]
        pick_name = ml_top.name
        pick_score = f"{ml_pred.ml_score:.1f}" if ml_pred else "-"
        conf = ml_pred.confidence if ml_pred else ""
        conf_cls = {"High": "conf-h", "Medium": "conf-m", "Low": "conf-l"}.get(conf, "")
        odds_r = players.index(ml_top) + 1
        stats_r = ml_top.stats_rank_in_group or "-"
        fit_r = ml_top.course_fit_rank or "-"
        wgr = ml_top.wgr
    else:
        top = players[0]
        pick_name = top.name
        pick_score = ""
        conf, conf_cls = "", ""
        odds_r, stats_r = 1, top.stats_rank_in_group or "-"
        fit_r = top.course_fit_rank or "-"
        wgr = top.wgr

    html = '<div class="glow-border reveal"><div class="dash-card shimmer">'

    html += f'<div class="dash-hdr" onclick="toggleDetail({gid})">'
    html += f'<span class="grp-badge">Group {gid}</span>'
    if conf:
        html += f'<span class="conf {conf_cls}">{conf}</span>'
    html += "</div>"

    html += '<div class="dash-body">'
    if has_ml:
        html += f'<div class="radar-wrap"><canvas id="radar-{gid}"></canvas></div>'
    html += f'<div class="pick-name"><span class="star">&#9733;</span>{_escape(pick_name)}</div>'
    if has_ml:
        html += f'<div class="pick-ml gradient-text"><span class="counter" data-target="{pick_score}">{pick_score}</span></div>'
    else:
        odds_str = _fmt_odds(players[0].best_odds)
        html += f'<div style="font-size:1.4em;color:var(--text2);margin-bottom:6px">{odds_str}</div>'
    egs_r = ml_top.egs_rank_in_group if has_ml and ml_top and ml_top.egs_rank_in_group else None
    html += '<div class="ranks">'
    html += f'<span class="rank-chip">Odds #{odds_r}</span>'
    html += f'<span class="rank-chip">Stats #{stats_r}</span>'
    html += f'<span class="rank-chip">Fit #{fit_r}</span>'
    html += f'<span class="rank-chip">WGR #{wgr}</span>'
    if egs_r is not None:
        egs_cls = ' style="border-color:var(--accent);color:var(--accent)"' if egs_r == 1 else (
            ' style="border-color:var(--accent3);color:var(--accent3)"' if egs_r != 1 else ''
        )
        html += f'<span class="rank-chip"{egs_cls}>EGS #{egs_r}</span>'
    html += "</div></div>"

    html += (
        f'<div class="dash-toggle" id="toggle-{gid}" '
        f'onclick="toggleDetail({gid})">'
        f'Details ({len(players)})</div>'
    )

    html += f'<div class="dash-detail" id="detail-{gid}">'

    if has_ml:
        html += '<div class="detail-charts">'
        html += f'<div class="chart-box"><h4>ML Score</h4><canvas id="bar-{gid}"></canvas></div>'
        html += f'<div class="chart-box"><h4>Signal Mix</h4><canvas id="stacked-{gid}"></canvas></div>'
        html += "</div>"

    html += '<div class="tbl-wrap" style="padding:0 18px 12px"><table>'
    if has_ml:
        html += "<tr><th>#</th><th>Player</th><th>Score</th><th>Odds</th><th>Stats</th><th>Fit</th><th>WGR</th><th>Conf</th></tr>"
        scored = []
        for p in players:
            pred = predictions.get(p.name)
            scored.append((p, pred))
        scored.sort(key=lambda x: x[1].ml_score if x[1] else -1, reverse=True)
        for rank, (p, pred) in enumerate(scored, 1):
            if pred:
                cls = ' class="fav"' if pred.ml_rank_in_group == 1 else ""
                bc = {"High": "b-h", "Medium": "b-m", "Low": "b-l"}.get(pred.confidence, "b-na")
                sc_stats = f"{pred.stats_component:.1f}" if pred.stats_component else "-"
                sc_fit = f"{pred.fit_component:.1f}" if pred.fit_component else "-"
                html += (
                    f"<tr{cls}><td>{rank}</td><td>{_escape(p.name)}</td>"
                    f'<td style="color:var(--accent);font-weight:700">{pred.ml_score:.1f}</td>'
                    f"<td>{pred.odds_component:.1f}</td><td>{sc_stats}</td><td>{sc_fit}</td>"
                    f"<td>{p.wgr}</td>"
                    f'<td><span class="badge {bc}">{pred.confidence}</span></td></tr>'
                )
            else:
                html += (
                    f"<tr><td>{rank}</td><td>{_escape(p.name)}</td>"
                    f"<td>-</td><td>-</td><td>-</td><td>-</td><td>{p.wgr}</td>"
                    f'<td><span class="badge b-na">N/A</span></td></tr>'
                )
    else:
        html += "<tr><th>#</th><th>Player</th><th>WGR</th><th>Odds</th><th>Book</th><th>Impl%</th></tr>"
        for rank, p in enumerate(players, 1):
            cls = ' class="fav"' if rank == 1 else ""
            prob = f"{p.implied_prob:.1%}" if p.best_odds is not None else "-"
            html += (
                f"<tr{cls}><td>{rank}</td><td>{_escape(p.name)}</td>"
                f"<td>{p.wgr}</td><td>{_fmt_odds(p.best_odds)}</td>"
                f"<td>{_escape(p.best_book)}</td><td>{prob}</td></tr>"
            )
    html += "</table></div>"

    html += "</div></div></div>"  # dash-card, glow-border
    return html


#----- Odds タブ -----


def _section_odds_tab(
    groups: dict[int, list[GroupPlayer]],
    bookmakers: list[str],
) -> str:
    """オッズタブ: オッズマトリクス + ブックメーカー別ベストピック。"""
    html = '<div id="odds-tab" class="section">'

    for gid in sorted(groups.keys()):
        players = groups[gid]
        all_odds = [v for p in players for v in p.odds_by_book.values()]
        min_o = min(all_odds) if all_odds else 0
        max_o = max(all_odds) if all_odds else 1

        bk_h = "".join(f"<th>{_escape(b)}</th>" for b in bookmakers)
        rows = ""
        for i, p in enumerate(players):
            cells = ""
            for book in bookmakers:
                val = p.odds_by_book.get(book)
                if val is not None:
                    if max_o != min_o:
                        ratio = (val - min_o) / (max_o - min_o)
                        if ratio < 0.5:
                            bg = f"rgba(34,197,94,{0.04 + (1 - ratio * 2) * 0.12})"
                        else:
                            bg = f"rgba(245,158,11,{0.03 + (ratio - 0.5) * 2 * 0.10})"
                    else:
                        bg = "transparent"
                    cells += f'<td style="background:{bg}">{_fmt_odds(val)}</td>'
                else:
                    cells += '<td class="na">-</td>'
            cls = ' class="fav"' if i == 0 else ""
            rows += f"<tr{cls}><td>{_escape(p.name)}</td>{cells}<td style=\"color:var(--text);font-weight:600\">{_fmt_odds(p.best_odds)}</td></tr>\n"

        html += f"""<div class="card reveal">
<div class="card-title">Group {gid} &mdash; Odds Matrix</div>
<div class="tbl-wrap"><table>
<tr><th>Player</th>{bk_h}<th>Best</th></tr>
{rows}</table></div></div>"""

    html += '<div class="book-grid">'
    for book in bookmakers:
        rows = ""
        for gid in sorted(groups.keys()):
            pw = [p for p in groups[gid] if book in p.odds_by_book]
            if pw:
                best = min(pw, key=lambda p: p.odds_by_book[book])
                rows += f"<tr><td>G{gid}</td><td>{_escape(best.name)}</td><td>{_fmt_odds(best.odds_by_book[book])}</td></tr>\n"
            else:
                rows += f'<tr><td>G{gid}</td><td class="na">-</td><td class="na">-</td></tr>\n'
        html += f"""<div class="card reveal">
<div class="card-title">{_escape(book)} &mdash; Top Picks</div>
<div class="tbl-wrap"><table>
<tr><th>Grp</th><th>Player</th><th>Odds</th></tr>
{rows}</table></div></div>"""
    html += "</div>"

    html += "</div>"
    return html


#----- Players タブ -----


def _section_players_tab(
    groups: dict[int, list[GroupPlayer]],
    course_fit: dict | None,
) -> str:
    """プレイヤータブ: 統計 + コースフィット + 選手詳細。"""
    has_fit = course_fit and course_fit.get("profile") is not None
    profile = course_fit.get("profile") if course_fit else None
    score_map = {}
    player_types = {}
    if course_fit:
        score_map = {s["player_name"]: s for s in course_fit.get("scores", [])}
        player_types = course_fit.get("player_types", {})

    html = '<div id="players-tab" class="section">'

    if has_fit and profile:
        conf_color = {"High": "#22c55e", "Medium": "#f59e0b"}.get(
            profile.confidence, "#ef4444"
        )
        html += f"""<div class="card reveal">
<div class="card-title">Course Profile &mdash; {_escape(profile.course_name)}</div>
<div style="padding:12px 16px;font-size:0.78em;color:var(--text2);display:flex;gap:16px;flex-wrap:wrap">
<span>Years: {profile.years_analyzed}</span>
<span>Samples: {profile.n_samples}</span>
<span>R\u00B2: {profile.r_squared:.4f}</span>
<span style="color:{conf_color}">Confidence: {profile.confidence}</span>
</div></div>"""

    for gid in sorted(groups.keys()):
        players = groups[gid]

        has_stats = any(p.stats is not None for p in players)
        cols = ["#", "Player", "WGR", "FedEx", "Odds", "Book", "Impl%"]
        if has_stats:
            cols += ["Stats", "SG:App", "SG:OTT", "SG:TtG", "GIR%", "Scoring"]
        if has_fit:
            cols += ["Fit", "Rank", "Type"]
        th = "".join(f"<th>{c}</th>" for c in cols)

        rows = ""
        for rank, p in enumerate(players, 1):
            cls = ' class="fav"' if rank == 1 else ""
            prob = f"{p.implied_prob:.1%}" if p.best_odds is not None else "-"
            row = (
                f"<td>{rank}</td><td>{_escape(p.name)}</td>"
                f"<td>{p.wgr}</td><td>{p.fedex_rank or '-'}</td>"
                f"<td>{_fmt_odds(p.best_odds)}</td><td>{_escape(p.best_book)}</td>"
                f"<td>{prob}</td>"
            )
            if has_stats:
                if p.stats:
                    s = p.stats
                    sg_app = f"{s.sg_approach:.2f}" if s.sg_approach is not None else "-"
                    sg_ott = f"{s.sg_off_tee:.2f}" if s.sg_off_tee is not None else "-"
                    sg_ttg = f"{s.sg_tee_to_green:.2f}" if s.sg_tee_to_green is not None else "-"
                    gir = f"{s.greens_in_regulation_pct:.1f}%" if s.greens_in_regulation_pct is not None else "-"
                    scr = f"{s.scoring_average:.2f}" if s.scoring_average is not None else "-"
                    sps = f"{p.stats_prediction_score:.1f}" if p.stats_prediction_score is not None else "-"
                    row += f"<td>{sps}</td><td>{sg_app}</td><td>{sg_ott}</td><td>{sg_ttg}</td><td>{gir}</td><td>{scr}</td>"
                else:
                    row += '<td>-</td>' * 6
            if has_fit:
                sc = score_map.get(p.name, {})
                fs = sc.get("fit_score")
                fr = sc.get("fit_rank")
                pt = player_types.get(p.name, "-")
                fs_str = f"{fs:.1f}" if fs is not None else "-"
                fr_str = str(fr) if fr else "-"
                row += f"<td>{fs_str}</td><td>{fr_str}</td><td>{_escape(pt)}</td>"
            rows += f"<tr{cls}>{row}</tr>\n"

        html += f"""<div class="card reveal">
<div class="card-title">Group {gid} &mdash; Player Data</div>
<div class="tbl-wrap"><table>
<tr>{th}</tr>
{rows}</table></div></div>"""

    html += "</div>"
    return html


#----- フッター -----


#----- Game Strategy タブ -----


def _section_game_tab(
    groups: dict[int, list[GroupPlayer]],
    ml_result: dict | None,
    egs_result,
) -> str:
    """Game Strategyタブ: EGS最適化ピック表示。"""
    predictions = ml_result.get("predictions", {}) if ml_result else {}
    fp = egs_result.field_params
    pegs_map = egs_result.player_egs

    html = '<div id="game-tab" class="section">'

    # Summary Card
    html += '<div class="glow-border reveal"><div class="dash-card shimmer" style="padding:24px">'
    html += '<div class="dash-hdr"><span class="grp-badge" style="font-size:0.85em">Game Strategy Summary</span></div>'
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin:16px 0">'

    stats = [
        ("Total EGS", f"{egs_result.total_egs:.1f}", "var(--accent)"),
        ("ML EGS", f"{egs_result.ml_total_egs:.1f}", "var(--accent2)"),
        ("ML Agree", f"{egs_result.agree_count}/{egs_result.total_groups}", "var(--text)"),
        ("Field", str(fp.get("field_size", "?")), "var(--text2)"),
        ("E[Cut]", str(fp.get("e_cut_count", "?")), "var(--text2)"),
        ("Max HC", str(fp.get("max_handicap", "?")), "var(--text2)"),
    ]
    for label, value, color in stats:
        html += (
            f'<div style="text-align:center;padding:12px;background:var(--surface);border-radius:12px;border:1px solid var(--border)">'
            f'<div style="font-size:0.7em;color:var(--text3);text-transform:uppercase;letter-spacing:0.04em;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:1.4em;font-weight:800;color:{color}">{value}</div>'
            f'</div>'
        )
    html += '</div>'

    # EGS vs ML comparison chart
    html += '<div style="height:220px;margin-top:12px"><canvas id="egs-comparison-chart"></canvas></div>'
    html += '</div></div>'

    # Per-Group Cards
    for gid in sorted(egs_result.picks.keys()):
        egs_names = egs_result.picks[gid]
        ml_names = egs_result.ml_picks.get(gid, [])
        agree = set(egs_names) == set(ml_names)
        n_picks = 2 if gid == 1 else 1

        html += '<div class="glow-border reveal"><div class="dash-card shimmer" style="padding:20px">'

        # Header
        picks_label = f" ({n_picks} picks)" if n_picks > 1 else ""
        agree_badge = (
            f'<span style="color:var(--accent);font-size:0.75em;font-weight:600">'
            f'&#10003; ML &amp; EGS Agree</span>'
            if agree else
            f'<span style="color:var(--accent3);font-size:0.75em;font-weight:600">'
            f'&#9888; Picks Differ</span>'
        )
        html += (
            f'<div class="dash-hdr">'
            f'<span class="grp-badge">Group {gid}{picks_label}</span>'
            f'{agree_badge}'
            f'</div>'
        )

        # Pick cards (ML vs EGS side-by-side when they differ)
        if not agree:
            html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0">'
            # ML Pick card
            html += '<div style="padding:14px;background:rgba(59,130,246,0.06);border-radius:12px;border:1px solid rgba(59,130,246,0.15)">'
            html += '<div style="font-size:0.65em;color:var(--accent2);text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-bottom:6px">ML Pick</div>'
            for mn in ml_names:
                pred = predictions.get(mn)
                pegs = pegs_map.get(mn)
                html += f'<div style="font-weight:700;color:var(--text);margin-bottom:2px">{_escape(mn)}</div>'
                if pred:
                    html += f'<div style="font-size:0.78em;color:var(--text2)">ML: {pred.ml_score:.1f}</div>'
                if pegs:
                    html += f'<div style="font-size:0.78em;color:var(--text3)">EGS: {pegs.egs:.1f} | HC: {pegs.handicap} | P(cut): {pegs.p_cut:.0%}</div>'
            html += '</div>'

            # EGS Pick card
            html += '<div style="padding:14px;background:rgba(34,197,94,0.06);border-radius:12px;border:1px solid rgba(34,197,94,0.15)">'
            html += '<div style="font-size:0.65em;color:var(--accent);text-transform:uppercase;letter-spacing:0.05em;font-weight:700;margin-bottom:6px">EGS Pick</div>'
            for en in egs_names:
                pred = predictions.get(en)
                pegs = pegs_map.get(en)
                html += f'<div style="font-weight:700;color:var(--text);margin-bottom:2px">{_escape(en)}</div>'
                if pred:
                    html += f'<div style="font-size:0.78em;color:var(--text2)">ML: {pred.ml_score:.1f}</div>'
                if pegs:
                    html += f'<div style="font-size:0.78em;color:var(--text3)">EGS: {pegs.egs:.1f} | HC: {pegs.handicap} | P(cut): {pegs.p_cut:.0%}</div>'
            html += '</div>'
            html += '</div>'
        else:
            # Agree — compact display
            html += '<div style="margin:12px 0;padding:14px;background:rgba(34,197,94,0.04);border-radius:12px;border:1px solid rgba(34,197,94,0.1)">'
            for en in egs_names:
                pred = predictions.get(en)
                pegs = pegs_map.get(en)
                html += f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
                html += f'<span class="star" style="color:var(--accent3)">&#9733;</span>'
                html += f'<span style="font-weight:700;color:var(--text)">{_escape(en)}</span>'
                if pred:
                    html += f'<span style="font-size:0.82em;color:var(--accent2)">ML: {pred.ml_score:.1f}</span>'
                if pegs:
                    html += f'<span style="font-size:0.82em;color:var(--accent)">EGS: {pegs.egs:.1f}</span>'
                    html += f'<span style="font-size:0.75em;color:var(--text3)">HC: {pegs.handicap} | P(cut): {pegs.p_cut:.0%}</span>'
                html += '</div>'
            html += '</div>'

        # Player table
        group_players = [
            pegs_map[p.name] for p in groups[gid] if p.name in pegs_map
        ]
        group_players.sort(key=lambda x: x.egs)

        html += '<div class="tbl-wrap" style="padding:4px 0"><table>'
        html += '<tr><th>#</th><th>Player</th><th>WGR</th><th>HC</th><th>P(cut)</th><th>E[pos]</th><th>EGS</th><th>ML</th></tr>'
        for i, pegs in enumerate(group_players, 1):
            pred = predictions.get(pegs.player_name)
            ml_sc = f"{pred.ml_score:.1f}" if pred else "-"

            is_egs_pick = pegs.player_name in egs_names
            is_ml_pick = pegs.player_name in ml_names
            cls = ' class="fav"' if is_egs_pick else ""

            marker = ""
            if is_egs_pick and not is_ml_pick:
                marker = ' <span style="color:var(--accent);font-size:0.7em">EGS</span>'
            elif is_ml_pick and not is_egs_pick:
                marker = ' <span style="color:var(--accent2);font-size:0.7em">ML</span>'
            elif is_egs_pick and is_ml_pick:
                marker = ' <span style="color:var(--accent);font-size:0.7em">&#10003;</span>'

            egs_color = 'var(--accent)' if is_egs_pick else 'var(--text2)'
            html += (
                f'<tr{cls}><td>{i}</td>'
                f'<td>{_escape(pegs.player_name)}{marker}</td>'
                f'<td>{pegs.wgr}</td>'
                f'<td>{pegs.handicap}</td>'
                f'<td>{pegs.p_cut:.0%}</td>'
                f'<td>{pegs.e_position:.1f}</td>'
                f'<td style="color:{egs_color};font-weight:700">{pegs.egs:.1f}</td>'
                f'<td>{ml_sc}</td></tr>'
            )
        html += '</table></div>'

        html += '</div></div>'

    html += '</div>'
    return html


#----- Pick Comparison タブ -----


def _section_picks_tab(
    groups: dict[int, list[GroupPlayer]],
    ml_result: dict | None,
    egs_result,
    egs_v2_result=None,
) -> str:
    """3モデル (ML / EGS v1 / EGS v2) のピック比較タブ。"""
    predictions = ml_result.get("predictions", {}) if ml_result else {}
    egs_picks = egs_result.picks if egs_result else {}
    egs_v2_picks = egs_v2_result.picks if egs_v2_result else {}
    egs_player_map = egs_result.player_egs if egs_result else {}
    egs_v2_player_map = egs_v2_result.player_egs if egs_v2_result else {}
    has_v2 = bool(egs_v2_result)

    # 一致統計
    total_groups = len(groups)
    all_agree = 0
    ml_egs1_agree = 0
    ml_egs2_agree = 0
    egs1_egs2_agree = 0
    for gid in sorted(groups.keys()):
        n_picks = 2 if gid == 1 else 1
        ml_names = _get_ml_picks(groups[gid], predictions, n_picks)
        v1_names = set(egs_picks.get(gid, []))
        v2_names = set(egs_v2_picks.get(gid, [])) if has_v2 else set()
        ml_set = set(ml_names)
        if ml_set == v1_names:
            ml_egs1_agree += 1
        if has_v2 and ml_set == v2_names:
            ml_egs2_agree += 1
        if has_v2 and v1_names == v2_names:
            egs1_egs2_agree += 1
        if has_v2 and ml_set == v1_names == v2_names:
            all_agree += 1
        elif not has_v2 and ml_set == v1_names:
            all_agree += 1

    html = '<div id="picks-tab" class="section">'

    # サマリーカード
    html += '<div class="glass" style="margin-bottom:1.5rem;padding:1.5rem">'
    html += '<h2 style="margin:0 0 1rem;font-size:1.3rem">Pick Comparison</h2>'

    # 合意率バー
    models_label = "ML / EGS v1 / EGS v2" if has_v2 else "ML / EGS v1"
    html += f'<div style="display:flex;gap:1.5rem;flex-wrap:wrap;margin-bottom:1rem">'

    html += (
        f'<div style="text-align:center">'
        f'<div style="font-size:2rem;font-weight:800;color:#22c55e">{all_agree}/{total_groups}</div>'
        f'<div style="font-size:.75rem;opacity:.6">Full Agree</div></div>'
    )
    html += (
        f'<div style="text-align:center">'
        f'<div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{ml_egs1_agree}/{total_groups}</div>'
        f'<div style="font-size:.75rem;opacity:.6">ML = v1</div></div>'
    )
    if has_v2:
        html += (
            f'<div style="text-align:center">'
            f'<div style="font-size:1.5rem;font-weight:700;color:#a855f7">{ml_egs2_agree}/{total_groups}</div>'
            f'<div style="font-size:.75rem;opacity:.6">ML = v2</div></div>'
        )
        html += (
            f'<div style="text-align:center">'
            f'<div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{egs1_egs2_agree}/{total_groups}</div>'
            f'<div style="font-size:.75rem;opacity:.6">v1 = v2</div></div>'
        )
    html += '</div>'

    # EGS合計比較
    ml_total_egs = egs_result.ml_total_egs if egs_result else 0
    v1_total_egs = egs_result.total_egs if egs_result else 0
    v2_total_egs = egs_v2_result.total_egs if egs_v2_result else 0
    html += '<div style="display:flex;gap:1.5rem;flex-wrap:wrap">'
    html += (
        f'<div style="padding:.6rem 1rem;border-radius:.5rem;background:rgba(59,130,246,.15);border:1px solid rgba(59,130,246,.3)">'
        f'<span style="font-size:.7rem;opacity:.6">ML Total EGS</span><br>'
        f'<span style="font-size:1.2rem;font-weight:700;color:#3b82f6">{ml_total_egs:.1f}</span></div>'
    )
    html += (
        f'<div style="padding:.6rem 1rem;border-radius:.5rem;background:rgba(34,197,94,.15);border:1px solid rgba(34,197,94,.3)">'
        f'<span style="font-size:.7rem;opacity:.6">EGS v1 Total</span><br>'
        f'<span style="font-size:1.2rem;font-weight:700;color:#22c55e">{v1_total_egs:.1f}</span></div>'
    )
    if has_v2:
        html += (
            f'<div style="padding:.6rem 1rem;border-radius:.5rem;background:rgba(168,85,247,.15);border:1px solid rgba(168,85,247,.3)">'
            f'<span style="font-size:.7rem;opacity:.6">EGS v2 Total</span><br>'
            f'<span style="font-size:1.2rem;font-weight:700;color:#a855f7">{v2_total_egs:.1f}</span></div>'
        )
    html += '</div></div>'

    # グループ別ピック比較テーブル
    for gid in sorted(groups.keys()):
        players = groups[gid]
        n_picks = 2 if gid == 1 else 1
        ml_names = _get_ml_picks(players, predictions, n_picks)
        v1_names = egs_picks.get(gid, [])
        v2_names = egs_v2_picks.get(gid, []) if has_v2 else []

        ml_set = set(ml_names)
        v1_set = set(v1_names)
        v2_set = set(v2_names) if has_v2 else set()

        # 全一致チェック
        if has_v2:
            group_agree = ml_set == v1_set == v2_set
        else:
            group_agree = ml_set == v1_set

        badge_color = "#22c55e" if group_agree else "#f59e0b"
        badge_text = "AGREE" if group_agree else "DIFFER"

        html += '<div class="glass" style="margin-bottom:1rem;padding:1rem">'
        html += (
            f'<div style="display:flex;align-items:center;gap:.7rem;margin-bottom:.8rem">'
            f'<span style="font-weight:700;font-size:1.1rem">Group {gid}</span>'
            f'<span style="font-size:.65rem;padding:2px 8px;border-radius:99px;'
            f'background:{badge_color}22;color:{badge_color};border:1px solid {badge_color}44">'
            f'{badge_text}</span>'
            f'<span style="font-size:.7rem;opacity:.5">{n_picks} pick{"s" if n_picks > 1 else ""}</span>'
            f'</div>'
        )

        # ピック比較カード (横並び)
        html += '<div style="display:flex;gap:.8rem;flex-wrap:wrap;margin-bottom:.8rem">'

        # ML Pick カード
        html += _pick_card("ML", "#3b82f6", ml_names, predictions, egs_player_map)

        # EGS v1 カード
        html += _pick_card("EGS v1", "#22c55e", v1_names, predictions, egs_player_map)

        # EGS v2 カード
        if has_v2:
            html += _pick_card("EGS v2", "#a855f7", v2_names, predictions, egs_v2_player_map)

        html += '</div>'

        # 全選手テーブル
        html += '<div style="overflow-x:auto"><table class="tbl" style="width:100%;font-size:.75rem">'
        html += '<tr><th>#</th><th>Player</th><th>WGR</th>'
        html += '<th>ML</th><th>EGS v1</th>'
        if has_v2:
            html += '<th>EGS v2</th>'
        html += '<th>Pick</th></tr>'

        # 全選手の情報を収集してML scoreでソート
        rows = []
        for p in players:
            pred = predictions.get(p.name)
            ml_sc = pred.ml_score if pred else None
            pegs_v1 = egs_player_map.get(p.name)
            pegs_v2 = egs_v2_player_map.get(p.name)
            rows.append((p, ml_sc, pegs_v1, pegs_v2))
        rows.sort(key=lambda r: r[1] if r[1] is not None else -999, reverse=True)

        for rank, (p, ml_sc, pegs_v1, pegs_v2) in enumerate(rows, 1):
            in_ml = p.name in ml_set
            in_v1 = p.name in v1_set
            in_v2 = p.name in v2_set

            # ピックバッジ
            badges = []
            if in_ml:
                badges.append('<span style="color:#3b82f6;font-weight:700">ML</span>')
            if in_v1:
                badges.append('<span style="color:#22c55e;font-weight:700">v1</span>')
            if has_v2 and in_v2:
                badges.append('<span style="color:#a855f7;font-weight:700">v2</span>')
            pick_str = " ".join(badges) if badges else "-"

            # 行の背景色
            bg = ""
            if in_ml and in_v1 and (not has_v2 or in_v2):
                bg = "background:rgba(34,197,94,.08);"
            elif in_ml or in_v1 or in_v2:
                bg = "background:rgba(255,255,255,.03);"

            ml_display = f"{ml_sc:.1f}" if ml_sc is not None else "-"
            v1_display = f"{pegs_v1.egs:.1f}" if pegs_v1 else "-"
            v2_display = f"{pegs_v2.egs:.1f}" if pegs_v2 else "-"

            html += f'<tr style="{bg}">'
            html += f'<td>{rank}</td>'
            html += f'<td style="font-weight:{"700" if badges else "400"}">{_escape(p.name)}</td>'
            html += f'<td>{p.wgr or "-"}</td>'
            html += f'<td>{ml_display}</td>'
            html += f'<td>{v1_display}</td>'
            if has_v2:
                html += f'<td>{v2_display}</td>'
            html += f'<td>{pick_str}</td>'
            html += '</tr>'

        html += '</table></div></div>'

    html += '</div>'
    return html


def _get_ml_picks(
    players: list[GroupPlayer],
    predictions: dict,
    n_picks: int,
) -> list[str]:
    """グループ内のML上位N名を返す。"""
    scored = []
    for p in players:
        pred = predictions.get(p.name)
        sc = pred.ml_score if pred else None
        scored.append((p.name, sc if sc is not None else -1))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in scored[:n_picks]]


def _pick_card(
    label: str,
    color: str,
    names: list[str],
    predictions: dict,
    egs_map: dict,
) -> str:
    """1モデルのピックカードHTML。"""
    html = (
        f'<div style="flex:1;min-width:140px;padding:.7rem;border-radius:.6rem;'
        f'background:{color}11;border:1px solid {color}33">'
        f'<div style="font-size:.7rem;font-weight:700;color:{color};margin-bottom:.4rem">'
        f'{label}</div>'
    )
    for name in names:
        pred = predictions.get(name)
        pegs = egs_map.get(name)
        ml_sc = f"{pred.ml_score:.1f}" if pred and pred.ml_score is not None else "-"
        egs_sc = f"{pegs.egs:.1f}" if pegs else "-"
        hc = f"{pegs.handicap}" if pegs else "-"
        p_cut = f"{pegs.p_cut:.0%}" if pegs else "-"
        html += (
            f'<div style="font-weight:600;font-size:.85rem">{_escape(name)}</div>'
            f'<div style="font-size:.65rem;opacity:.7">'
            f'ML:{ml_sc} | EGS:{egs_sc} | HC:{hc} | P(cut):{p_cut}</div>'
        )
    html += '</div>'
    return html


def _footer(generated_at: str) -> str:
    try:
        dt = datetime.fromisoformat(generated_at)
        ts = dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        ts = generated_at
    return f"""<div class="footer">
Vegas Insider + The Odds API &middot; {ts} |
<a href="index.html">Home</a> | <a href="review.html">Post-Tournament Review</a>
</div>"""


#----- Chart.jsデータ構築 -----


def _build_chart_data(
    groups: dict[int, list[GroupPlayer]],
    ml_result: dict | None,
    egs_result=None,
) -> dict:
    """Chart.js初期化用のJSONデータ構築。"""
    predictions = ml_result.get("predictions", {}) if ml_result else {}
    weights = ml_result.get("weights", {}) if ml_result else {}
    has_ml = bool(predictions)
    has_egs = egs_result is not None

    data: dict = {
        "has_ml": has_ml,
        "has_egs": has_egs,
        "weights": {
            "odds": weights.get("odds", 0.45),
            "stats": weights.get("stats", 0.35),
            "fit": weights.get("course_fit", 0.20),
        },
        "groups": {},
    }

    # EGSデータの取得
    egs_picks = egs_result.picks if has_egs else {}
    egs_ml_picks = egs_result.ml_picks if has_egs else {}
    player_egs_map = egs_result.player_egs if has_egs else {}

    if has_egs:
        data["egs_summary"] = {
            "total_egs": round(egs_result.total_egs, 1),
            "ml_total_egs": round(egs_result.ml_total_egs, 1),
            "agree_count": egs_result.agree_count,
            "total_groups": egs_result.total_groups,
            "field_size": egs_result.field_params.get("field_size", 0),
            "e_cut_count": egs_result.field_params.get("e_cut_count", 0),
            "max_handicap": egs_result.field_params.get("max_handicap", 0),
        }

    for gid in sorted(groups.keys()):
        players_data = []
        for p in groups[gid]:
            pred = predictions.get(p.name)
            pegs = player_egs_map.get(p.name)

            player_d = {
                "name": p.name,
                "ml_score": round(pred.ml_score, 1) if pred else 0,
                "odds": round(pred.odds_component, 1) if pred else 0,
                "stats": round(pred.stats_component, 1) if pred else 0,
                "fit": round(pred.fit_component, 1) if pred else 0,
                "crowd": round(pred.crowd_component, 1) if pred else 0,
                "wgr_score": round(_wgr_to_score(p.wgr), 1),
                "confidence": pred.confidence if pred else "N/A",
                "ml_rank": pred.ml_rank_in_group if pred else 999,
            }
            if pegs:
                player_d["egs"] = round(pegs.egs, 1)
                player_d["egs_rank"] = pegs.egs_rank_in_group
                player_d["handicap"] = pegs.handicap
                player_d["p_cut"] = round(pegs.p_cut * 100, 0)
                player_d["e_position"] = round(pegs.e_position, 1)
                player_d["wgr"] = pegs.wgr
            players_data.append(player_d)

        players_data.sort(key=lambda x: x["ml_score"], reverse=True)

        group_d = {"players": players_data}
        if has_egs:
            group_d["egs_picks"] = egs_picks.get(gid, [])
            group_d["ml_picks"] = egs_ml_picks.get(gid, [])
            group_d["agree"] = set(egs_picks.get(gid, [])) == set(egs_ml_picks.get(gid, []))
        data["groups"][str(gid)] = group_d

    return data


#----- JavaScript -----


def _script(chart_json: str) -> str:
    """Chart.js + タブ + 展開 + Intersection Observer fade-in。"""
    return f"""<script>
const D = {chart_json};

/* タブ切替 */
function showSection(id) {{
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelector('[data-section="'+id+'"]').classList.add('active');
}}

/* 詳細展開 (smooth slide) */
const _created = new Set();
function toggleDetail(gid) {{
  const el = document.getElementById('detail-' + gid);
  const tg = document.getElementById('toggle-' + gid);
  const isOpen = el.classList.contains('open');
  if (isOpen) {{
    el.classList.remove('open');
  }} else {{
    el.classList.add('open');
    if (!_created.has(gid) && D.has_ml) {{
      _mkDetail(gid);
      _created.add(gid);
    }}
  }}
  const n = D.groups[gid] ? D.groups[gid].players.length : '?';
  tg.textContent = isOpen ? 'Details (' + n + ')' : 'Hide';
}}

/* ---- IntersectionObserver scroll reveal ---- */
(function() {{
  var obs = new IntersectionObserver(function(entries) {{
    entries.forEach(function(e,i) {{
      if (e.isIntersecting) {{
        /* stagger: each card delayed by 60ms */
        var idx = Array.from(e.target.parentElement.children).indexOf(e.target);
        setTimeout(function(){{ e.target.classList.add('visible'); }}, idx * 60);
        obs.unobserve(e.target);
      }}
    }});
  }}, {{ threshold: 0.08 }});
  document.addEventListener('DOMContentLoaded', function() {{
    document.querySelectorAll('.reveal').forEach(function(el){{ obs.observe(el); }});
  }});
}})();

/* ---- Counter animation ---- */
function animateCounters() {{
  document.querySelectorAll('.counter').forEach(function(el) {{
    var target = parseFloat(el.getAttribute('data-target'));
    if (isNaN(target)) return;
    var duration = 1200;
    var start = performance.now();
    el.textContent = '0.0';
    function step(now) {{
      var elapsed = now - start;
      var progress = Math.min(elapsed / duration, 1);
      /* ease-out cubic */
      var ease = 1 - Math.pow(1 - progress, 3);
      var current = (target * ease).toFixed(1);
      el.textContent = current;
      if (progress < 1) requestAnimationFrame(step);
    }}
    requestAnimationFrame(step);
  }});
}}

/* ---- 3D tilt on hover ---- */
function initTilt() {{
  document.querySelectorAll('.dash-card').forEach(function(card) {{
    card.addEventListener('mousemove', function(e) {{
      var rect = card.getBoundingClientRect();
      var x = (e.clientX - rect.left) / rect.width - 0.5;
      var y = (e.clientY - rect.top) / rect.height - 0.5;
      card.style.transform = 'perspective(800px) rotateY(' + (x * 6) + 'deg) rotateX(' + (-y * 6) + 'deg)';
    }});
    card.addEventListener('mouseleave', function() {{
      card.style.transform = '';
    }});
  }});
}}

/* 色定数 */
const C = {{
  accent:'#22c55e', accent2:'#3b82f6', accent3:'#f59e0b', accent4:'#ef4444',
  odds:'#3b82f6', stats:'#22c55e', fit:'#f59e0b',
  crowd:'#a78bfa', wgr:'#ef4444',
  grid:'rgba(255,255,255,0.06)', text:'#a1a1aa', text1:'#fafafa'
}};

function _confColor(c) {{
  if(c==='High') return C.accent;
  if(c==='Medium') return C.accent3;
  if(c==='Low') return C.accent4;
  return '#52525b';
}}

const _font = "'Inter',system-ui,sans-serif";

const _rOpt = {{
  responsive:true, maintainAspectRatio:false,
  animation:{{ duration:700, easing:'easeOutQuart' }},
  scales: {{ r: {{
    suggestedMin:0, suggestedMax:100,
    ticks:{{ display:false }},
    grid:{{ color:'rgba(255,255,255,0.06)', lineWidth:1 }},
    pointLabels:{{ color:'#71717a', font:{{size:9,family:_font,weight:'500'}} }},
    angleLines:{{ color:'rgba(255,255,255,0.04)' }}
  }} }},
  plugins: {{ legend:{{ display:false }} }}
}};

document.addEventListener('DOMContentLoaded', function() {{
  /* counter + tilt init */
  animateCounters();
  initTilt();

  if(!D.has_ml) return;

  /* ドーナツ */
  var dc = document.getElementById('donut-weights');
  if(dc) {{
    new Chart(dc, {{
      type:'doughnut',
      data: {{
        labels:['Odds','Stats','Fit'],
        datasets:[{{ data:[D.weights.odds*100, D.weights.stats*100, D.weights.fit*100],
          backgroundColor:[C.odds, C.stats, C.fit],
          borderColor:'rgba(9,9,11,0.8)', borderWidth:2,
          hoverOffset:4
        }}]
      }},
      options: {{ responsive:true, maintainAspectRatio:true, cutout:'65%',
        animation:{{ duration:800, easing:'easeOutQuart' }},
        plugins:{{ legend:{{display:false}}, tooltip:{{
          backgroundColor:'rgba(9,9,11,0.9)', borderColor:'rgba(255,255,255,0.1)', borderWidth:1,
          titleFont:{{family:_font,size:11}}, bodyFont:{{family:_font,size:11}},
          padding:8, cornerRadius:8
        }} }}
      }}
    }});
  }}

  /* レーダー */
  Object.keys(D.groups).forEach(function(gid) {{
    var g = D.groups[gid], ps = g.players;
    if(ps.length < 1) return;
    var cv = document.getElementById('radar-' + gid);
    if(!cv) return;
    var t1 = ps[0], t2 = ps.length > 1 ? ps[1] : null;
    var ds = [{{
      label: t1.name,
      data: [t1.odds, t1.stats, t1.fit, t1.wgr_score, t1.crowd],
      backgroundColor: 'rgba(34,197,94,0.08)',
      borderColor: '#22c55e', borderWidth: 2,
      pointBackgroundColor: '#22c55e', pointRadius: 3, pointHoverRadius: 6,
      pointBorderWidth:0
    }}];
    if(t2) {{
      ds.push({{
        label: t2.name,
        data: [t2.odds, t2.stats, t2.fit, t2.wgr_score, t2.crowd],
        backgroundColor: 'rgba(59,130,246,0.06)',
        borderColor: '#3b82f6', borderWidth: 1.5,
        pointBackgroundColor: '#3b82f6', pointRadius: 2, pointHoverRadius: 5,
        pointBorderWidth:0
      }});
    }}
    new Chart(cv, {{
      type: 'radar',
      data: {{ labels:['Odds','Stats','Fit','WGR','Crowd'], datasets:ds }},
      options: _rOpt
    }});
  }});
}});

/* ---- EGS Comparison Chart ---- */
if (D.has_egs && D.egs_summary) {{
  var egsCanvas = document.getElementById('egs-comparison-chart');
  if (egsCanvas) {{
    var gids = Object.keys(D.groups).sort(function(a,b){{ return parseInt(a)-parseInt(b); }});
    var mlEgs = [], egsOptimal = [], labels = [];
    gids.forEach(function(gid) {{
      var g = D.groups[gid];
      labels.push('G' + gid);
      var mlSum = 0, egsSum = 0;
      (g.ml_picks || []).forEach(function(name) {{
        var p = g.players.find(function(x){{ return x.name === name; }});
        if (p && p.egs !== undefined) mlSum += p.egs;
      }});
      (g.egs_picks || []).forEach(function(name) {{
        var p = g.players.find(function(x){{ return x.name === name; }});
        if (p && p.egs !== undefined) egsSum += p.egs;
      }});
      mlEgs.push(+mlSum.toFixed(1));
      egsOptimal.push(+egsSum.toFixed(1));
    }});
    new Chart(egsCanvas, {{
      type: 'bar',
      data: {{
        labels: labels,
        datasets: [
          {{ label: 'ML Pick EGS', data: mlEgs, backgroundColor: 'rgba(59,130,246,0.5)', borderColor: '#3b82f6', borderWidth: 1, borderRadius: 4 }},
          {{ label: 'EGS Pick', data: egsOptimal, backgroundColor: 'rgba(34,197,94,0.5)', borderColor: '#22c55e', borderWidth: 1, borderRadius: 4 }}
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        animation: {{ duration: 700, easing: 'easeOutQuart' }},
        scales: {{
          x: {{ grid: {{ color: C.grid }}, ticks: {{ color: C.text1, font: {{ family: _font, size: 11 }} }} }},
          y: {{ grid: {{ color: C.grid }}, ticks: {{ color: C.text, font: {{ family: _font, size: 10 }} }},
            title: {{ display: true, text: 'EGS (lower = better)', color: C.text, font: {{ family: _font, size: 10 }} }} }}
        }},
        plugins: {{
          legend: {{ labels: {{ color: C.text, font: {{ size: 10, family: _font }}, boxWidth: 10, padding: 12 }} }},
          tooltip: {{ backgroundColor: 'rgba(9,9,11,0.9)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
            titleFont: {{ family: _font, size: 11 }}, bodyFont: {{ family: _font, size: 11 }}, padding: 8, cornerRadius: 8 }}
        }}
      }}
    }});
  }}
}}

/* 詳細チャート（遅延生成） */
function _mkDetail(gid) {{
  var g = D.groups[gid], ps = g.players, w = D.weights;
  var _tt = {{
    backgroundColor:'rgba(9,9,11,0.9)', borderColor:'rgba(255,255,255,0.1)',
    borderWidth:1, titleFont:{{family:_font,size:11}}, bodyFont:{{family:_font,size:11}},
    padding:8, cornerRadius:8
  }};

  var bc = document.getElementById('bar-' + gid);
  if(bc) {{
    new Chart(bc, {{
      type:'bar',
      data: {{
        labels: ps.map(function(p){{ return p.name; }}),
        datasets:[{{
          data: ps.map(function(p){{ return p.ml_score; }}),
          backgroundColor: ps.map(function(p){{
            var c = _confColor(p.confidence);
            return c + '30';
          }}),
          borderColor: ps.map(function(p){{ return _confColor(p.confidence); }}),
          borderWidth:1, borderRadius:4
        }}]
      }},
      options: {{
        indexAxis:'y', responsive:true,
        animation:{{ duration:500, easing:'easeOutQuart' }},
        scales: {{
          x:{{ max:100, grid:{{color:C.grid}}, ticks:{{color:C.text, font:{{family:_font,size:10}}}} }},
          y:{{ grid:{{display:false}}, ticks:{{color:C.text1, font:{{size:10,family:_font,weight:'500'}}}} }}
        }},
        plugins:{{ legend:{{display:false}}, tooltip:_tt }}
      }}
    }});
  }}

  var sc = document.getElementById('stacked-' + gid);
  if(sc) {{
    new Chart(sc, {{
      type:'bar',
      data: {{
        labels: ps.map(function(p){{ return p.name; }}),
        datasets:[
          {{ label:'Odds', data:ps.map(function(p){{ return +(p.odds * w.odds).toFixed(1); }}),
             backgroundColor:'rgba(59,130,246,0.55)', borderColor:'#3b82f6', borderWidth:0, borderRadius:0 }},
          {{ label:'Stats', data:ps.map(function(p){{ return +(p.stats * w.stats).toFixed(1); }}),
             backgroundColor:'rgba(34,197,94,0.55)', borderColor:'#22c55e', borderWidth:0, borderRadius:0 }},
          {{ label:'Fit', data:ps.map(function(p){{ return +(p.fit * w.fit).toFixed(1); }}),
             backgroundColor:'rgba(245,158,11,0.55)', borderColor:'#f59e0b', borderWidth:0, borderRadius:0 }}
        ]
      }},
      options: {{
        indexAxis:'y', responsive:true,
        animation:{{ duration:500, easing:'easeOutQuart' }},
        scales: {{
          x:{{ stacked:true, grid:{{color:C.grid}}, ticks:{{color:C.text, font:{{family:_font,size:10}}}} }},
          y:{{ stacked:true, grid:{{display:false}}, ticks:{{color:C.text1, font:{{size:10,family:_font,weight:'500'}}}} }}
        }},
        plugins:{{
          legend:{{ labels:{{color:C.text, font:{{size:9,family:_font}}, boxWidth:8, padding:10}} }},
          tooltip:_tt
        }}
      }}
    }});
  }}
}}
</script>"""
