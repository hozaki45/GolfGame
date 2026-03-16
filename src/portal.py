"""ポータルページ (index.html) 生成モジュール。

ダッシュボードと振り返りレポートへのナビゲーションページを生成する。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def generate_portal(output_dir: str = "data/output") -> Path:
    """ポータル index.html を生成・保存する。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    has_dashboard = (out / "dashboard.html").exists()
    has_review = (out / "review.html").exists()
    has_training = (out / "training.html").exists()
    has_model_comparison = (out / "model_comparison.html").exists()
    has_backtest = (out / "backtest.html").exists()

    review_label = "大会後レビュー"
    if has_review:
        try:
            text = (out / "review.html").read_text(encoding="utf-8")
            import re
            m = re.search(r"<title>(.*?) - Post-Tournament Review</title>", text)
            if m:
                review_label = f"{m.group(1)} レビュー"
        except Exception:
            pass

    dashboard_label = "大会前ダッシュボード"
    if has_dashboard:
        try:
            text = (out / "dashboard.html").read_text(encoding="utf-8")
            import re
            m = re.search(r"<title>(.*?)</title>", text)
            if m and "Golf" not in m.group(1):
                dashboard_label = f"{m.group(1)}"
        except Exception:
            pass

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Card definitions: (href, icon, title, desc, accent_color, enabled)
    tournament_cards = [
        (
            "dashboard.html", has_dashboard,
            dashboard_label,
            "今週の大会のオッズ、選手統計、コースフィット、ML予測をまとめたダッシュボード",
            "#22c55e", "#16a34a",
            "毎週水曜 19:00 JST 自動更新",
        ),
        (
            "review.html", has_review,
            review_label,
            "ML予測の的中精度、ゲームスコア比較（ML vs EGS）、グループ別の結果振り返り",
            "#3b82f6", "#2563eb",
            "毎週月曜 18:00 JST 自動更新",
        ),
        (
            "backtest.html", has_backtest,
            "ゲームスコア バックテスト",
            "ML・EGS v1・EGS v2・Odds の4モデルが実際の大会で何点取ったかを比較。累計スコア推移付き",
            "#f97316", "#ea580c",
            "毎週月曜 自動更新",
        ),
    ]

    model_cards = [
        (
            "model_comparison.html", has_model_comparison,
            "EGS モデル比較 (v1 vs v2)",
            "ベースライン(v1)とロング/ショートメモリモデル(v2)の精度比較。レーダーチャート・特徴量重要度",
            "#a855f7", "#9333ea",
            "毎週月曜 再訓練後に更新",
        ),
        (
            "training.html", has_training,
            "EGS 訓練履歴",
            "MLモデルの訓練メトリクスの推移と特徴量重要度のトレンド",
            "#06b6d4", "#0891b2",
            "毎週月曜 再訓練後に更新",
        ),
    ]

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GolfGame</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #09090b;
  --surface: rgba(255,255,255,0.03);
  --surface-hover: rgba(255,255,255,0.06);
  --border: rgba(255,255,255,0.06);
  --border-hover: rgba(255,255,255,0.12);
  --text: #fafafa;
  --text2: #a1a1aa;
  --text3: #52525b;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:'Inter',system-ui,-apple-system,sans-serif;
  background:var(--bg); color:var(--text);
  min-height:100vh;
  -webkit-font-smoothing:antialiased;
}}

/* Background effects */
.bg-grid {{
  position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image: radial-gradient(rgba(255,255,255,0.03) 1px, transparent 1px);
  background-size: 32px 32px;
}}
.orb {{ position:fixed; border-radius:50%; filter:blur(120px); pointer-events:none; z-index:0; }}
.orb-1 {{ width:600px; height:600px; top:-150px; left:-100px;
  background:radial-gradient(circle, rgba(34,197,94,0.25) 0%, transparent 70%);
  animation: drift1 25s ease-in-out infinite; }}
.orb-2 {{ width:500px; height:500px; bottom:-100px; right:-80px;
  background:radial-gradient(circle, rgba(59,130,246,0.2) 0%, transparent 70%);
  animation: drift2 30s ease-in-out infinite; }}
.orb-3 {{ width:300px; height:300px; top:40%; left:50%;
  background:radial-gradient(circle, rgba(168,85,247,0.15) 0%, transparent 70%);
  animation: drift3 20s ease-in-out infinite; }}
@keyframes drift1 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(50px,40px) }} }}
@keyframes drift2 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(-40px,30px) }} }}
@keyframes drift3 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(-30px,-20px) }} }}

/* Layout */
.wrapper {{
  position:relative; z-index:1;
  max-width:880px; margin:0 auto; padding:48px 24px 32px;
}}

/* Header */
.header {{
  text-align:center; margin-bottom:48px;
}}
.logo {{
  font-size:3em; font-weight:900; letter-spacing:-0.04em;
  background:linear-gradient(135deg, #22c55e 0%, #3b82f6 50%, #a855f7 100%);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
  line-height:1.1;
}}
.tagline {{
  color:var(--text3); font-size:0.9em; margin-top:8px; letter-spacing:0.02em;
}}

/* Section */
.section {{
  margin-bottom:36px;
}}
.section-label {{
  display:flex; align-items:center; gap:10px;
  margin-bottom:16px; padding-left:4px;
}}
.section-label .dot {{
  width:8px; height:8px; border-radius:50%;
}}
.section-label span {{
  font-size:0.75em; font-weight:700; text-transform:uppercase;
  letter-spacing:0.1em; color:var(--text3);
}}

/* Card grid */
.card-grid {{
  display:grid; grid-template-columns:1fr 1fr; gap:14px;
}}
.card-grid.triple {{
  grid-template-columns:1fr 1fr 1fr;
}}

/* Card */
.card {{
  display:flex; flex-direction:column;
  text-decoration:none; color:var(--text);
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:16px;
  padding:0;
  overflow:hidden;
  transition: all 0.35s cubic-bezier(0.16,1,0.3,1);
  position:relative;
}}
.card::before {{
  content:''; position:absolute; top:0; left:0; right:0; height:3px;
  border-radius:16px 16px 0 0;
  opacity:0.8;
  transition: opacity 0.3s;
}}
.card:hover {{
  border-color:var(--border-hover);
  background:var(--surface-hover);
  transform:translateY(-3px);
  box-shadow:0 12px 40px rgba(0,0,0,0.3);
}}
.card:hover::before {{
  opacity:1;
}}
.card.disabled {{
  opacity:0.3; pointer-events:none;
}}
.card-body {{
  padding:20px 22px 18px;
  flex:1;
  display:flex; flex-direction:column;
}}
.card-title {{
  font-size:1em; font-weight:700; margin-bottom:6px;
  line-height:1.3;
}}
.card-desc {{
  font-size:0.78em; color:var(--text2); line-height:1.6;
  flex:1;
}}
.card-footer {{
  display:flex; align-items:center; justify-content:space-between;
  margin-top:12px;
  padding-top:10px;
  border-top:1px solid rgba(255,255,255,0.04);
}}
.card-schedule {{
  font-size:0.65em; color:var(--text3); letter-spacing:0.01em;
}}
.badge {{
  display:inline-block; padding:2px 10px;
  border-radius:6px; font-size:0.65em; font-weight:700;
  letter-spacing:0.04em;
}}
.badge-ready {{
  background:rgba(34,197,94,0.12); color:#22c55e;
}}
.badge-none {{
  background:rgba(255,255,255,0.04); color:var(--text3);
}}

/* Footer */
.footer {{
  text-align:center; color:var(--text3); font-size:0.7em;
  margin-top:40px; padding-top:20px;
  border-top:1px solid rgba(255,255,255,0.04);
}}

/* Responsive */
@media (max-width:640px) {{
  .card-grid, .card-grid.triple {{ grid-template-columns:1fr; }}
  .logo {{ font-size:2.2em; }}
  .wrapper {{ padding:32px 16px 24px; }}
}}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="orb orb-1"></div>
<div class="orb orb-2"></div>
<div class="orb orb-3"></div>

<div class="wrapper">
<div class="header">
  <div class="logo">GolfGame</div>
  <div class="tagline">PGA Tour ベッティング分析 & ML予測プラットフォーム</div>
</div>
"""

    # --- Tournament Analysis Section ---
    html += """
<div class="section">
<div class="section-label">
  <div class="dot" style="background:#22c55e"></div>
  <span>大会分析</span>
</div>
<div class="card-grid triple">
"""
    for href, enabled, title, desc, color1, color2, schedule in tournament_cards:
        if enabled:
            html += f"""<a href="{href}" class="card" style="--accent:{color1}">
<style>.card[style*="{color1}"]::before {{ background:linear-gradient(90deg,{color1},{color2}); }}</style>
<div class="card-body">
  <div class="card-title">{_esc(title)}</div>
  <div class="card-desc">{desc}</div>
  <div class="card-footer">
    <span class="card-schedule">{schedule}</span>
    <span class="badge badge-ready">LIVE</span>
  </div>
</div></a>
"""
        else:
            html += f"""<div class="card disabled">
<div class="card-body">
  <div class="card-title">{_esc(title)}</div>
  <div class="card-desc">{desc}</div>
  <div class="card-footer">
    <span class="card-schedule">{schedule}</span>
    <span class="badge badge-none">PENDING</span>
  </div>
</div></div>
"""

    html += "</div></div>"

    # --- Model Development Section ---
    html += """
<div class="section">
<div class="section-label">
  <div class="dot" style="background:#a855f7"></div>
  <span>モデル開発</span>
</div>
<div class="card-grid">
"""
    for href, enabled, title, desc, color1, color2, schedule in model_cards:
        if enabled:
            html += f"""<a href="{href}" class="card" style="--accent:{color1}">
<style>.card[style*="{color1}"]::before {{ background:linear-gradient(90deg,{color1},{color2}); }}</style>
<div class="card-body">
  <div class="card-title">{_esc(title)}</div>
  <div class="card-desc">{desc}</div>
  <div class="card-footer">
    <span class="card-schedule">{schedule}</span>
    <span class="badge badge-ready">LIVE</span>
  </div>
</div></a>
"""
        else:
            html += f"""<div class="card disabled">
<div class="card-body">
  <div class="card-title">{_esc(title)}</div>
  <div class="card-desc">{desc}</div>
  <div class="card-footer">
    <span class="card-schedule">{schedule}</span>
    <span class="badge badge-none">PENDING</span>
  </div>
</div></div>
"""

    html += "</div></div>"

    html += f"""
<div class="footer">最終更新: {now}</div>
</div>
</body>
</html>"""

    path = out / "index.html"
    path.write_text(html, encoding="utf-8")
    print(f"[INFO] Saved portal page to {path}")
    return path


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
