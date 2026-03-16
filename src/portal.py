"""ポータルページ (index.html) 生成モジュール。

ダッシュボードと振り返りレポートへのナビゲーションページを生成する。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def generate_portal(output_dir: str = "data/output") -> Path:
    """ポータル index.html を生成・保存する。

    既存の dashboard.html / review.html の有無を自動検出して
    リンクの有効/無効を切り替える。
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    has_dashboard = (out / "dashboard.html").exists()
    has_review = (out / "review.html").exists()
    has_training = (out / "training.html").exists()
    has_model_comparison = (out / "model_comparison.html").exists()
    has_backtest = (out / "backtest.html").exists()

    # 最新の review ファイルからトーナメント名を取得 (簡易)
    review_label = "Post-Tournament Review"
    if has_review:
        try:
            text = (out / "review.html").read_text(encoding="utf-8")
            import re
            m = re.search(r"<title>(.*?) - Post-Tournament Review</title>", text)
            if m:
                review_label = f"Review: {m.group(1)}"
        except Exception:
            pass

    dashboard_label = "Pre-Tournament Dashboard"
    if has_dashboard:
        try:
            text = (out / "dashboard.html").read_text(encoding="utf-8")
            import re
            m = re.search(r"<title>(.*?)</title>", text)
            if m and "Golf" not in m.group(1):
                dashboard_label = f"Dashboard: {m.group(1)}"
        except Exception:
            pass

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GolfGame - Home</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #09090b;
  --surface: rgba(255,255,255,0.04);
  --border: rgba(255,255,255,0.06);
  --border-hover: rgba(255,255,255,0.15);
  --text: #fafafa;
  --text2: #a1a1aa;
  --text3: #52525b;
  --accent: #22c55e;
  --accent2: #3b82f6;
  --accent3: #f59e0b;
  --radius: 20px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:'Inter',system-ui,sans-serif;
  background:var(--bg); color:var(--text);
  min-height:100vh; display:flex; flex-direction:column;
  align-items:center; justify-content:center;
  -webkit-font-smoothing:antialiased;
}}
body::before {{
  content:''; position:fixed; inset:0; z-index:0; pointer-events:none;
  background-image: radial-gradient(rgba(255,255,255,0.05) 1px, transparent 1px);
  background-size: 24px 24px;
}}
.orb {{ position:fixed; border-radius:50%; filter:blur(100px); pointer-events:none; z-index:0; opacity:0.3; }}
.orb-1 {{ width:500px; height:500px; top:-100px; left:-50px;
  background:radial-gradient(circle, rgba(34,197,94,0.4) 0%, transparent 70%);
  animation: float1 20s ease-in-out infinite; }}
.orb-2 {{ width:400px; height:400px; bottom:-80px; right:-60px;
  background:radial-gradient(circle, rgba(59,130,246,0.3) 0%, transparent 70%);
  animation: float2 25s ease-in-out infinite; }}
@keyframes float1 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(40px,30px) }} }}
@keyframes float2 {{ 0%,100%{{ transform:translate(0,0) }} 50%{{ transform:translate(-30px,20px) }} }}

.container {{
  position:relative; z-index:1;
  max-width:600px; width:100%; padding:24px;
}}
h1 {{
  font-size:2.2em; font-weight:900; letter-spacing:-0.03em;
  text-align:center; margin-bottom:8px;
  background:linear-gradient(135deg, #22c55e, #3b82f6);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
}}
.sub {{ text-align:center; color:var(--text3); font-size:0.85em; margin-bottom:32px; }}
.cards {{ display:flex; flex-direction:column; gap:12px; }}
.card {{
  display:block; text-decoration:none; color:var(--text);
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); padding:24px 28px;
  transition: all 0.3s cubic-bezier(0.16,1,0.3,1);
}}
.card:hover {{
  border-color:var(--border-hover);
  box-shadow: 0 0 40px rgba(34,197,94,0.08);
  transform: translateY(-2px);
}}
.card.disabled {{
  opacity:0.35; pointer-events:none;
}}
.card-icon {{ font-size:1.6em; margin-bottom:8px; }}
.card-title {{ font-size:1.1em; font-weight:700; margin-bottom:4px; }}
.card-desc {{ font-size:0.82em; color:var(--text2); line-height:1.5; }}
.card-status {{
  display:inline-block; margin-top:8px; padding:2px 10px;
  border-radius:6px; font-size:0.7em; font-weight:600;
  letter-spacing:0.03em; text-transform:uppercase;
}}
.card-status.ready {{ background:rgba(34,197,94,0.15); color:var(--accent); }}
.card-status.none {{ background:rgba(255,255,255,0.06); color:var(--text3); }}
.footer {{
  text-align:center; color:var(--text3); font-size:0.72em;
  margin-top:32px; position:relative; z-index:1;
}}
</style>
</head>
<body>
<div class="orb orb-1"></div>
<div class="orb orb-2"></div>
<div class="container">
<h1>GolfGame</h1>
<div class="sub">PGA Tour ベッティング分析 & ML予測プラットフォーム</div>
<div class="cards">"""

    # Dashboard card
    if has_dashboard:
        html += f"""
<a href="dashboard.html" class="card">
<div class="card-icon">&#x1F4CA;</div>
<div class="card-title">{_esc(dashboard_label)}</div>
<div class="card-desc">今週の大会のオッズ、選手統計、コースフィット、ML予測をまとめたダッシュボード。</div>
<span class="card-status ready">公開中</span>
</a>"""
    else:
        html += """
<div class="card disabled">
<div class="card-icon">&#x1F4CA;</div>
<div class="card-title">大会前ダッシュボード</div>
<div class="card-desc">毎週水曜に自動生成されます。現在はまだ公開されていません。</div>
<span class="card-status none">未生成</span>
</div>"""

    # Review card
    if has_review:
        html += f"""
<a href="review.html" class="card">
<div class="card-icon">&#x1F3C6;</div>
<div class="card-title">{_esc(review_label)}</div>
<div class="card-desc">ML予測の的中精度、ゲームスコア比較（ML vs EGS）、グループ別の結果振り返り。</div>
<span class="card-status ready">公開中</span>
</a>"""
    else:
        html += """
<div class="card disabled">
<div class="card-icon">&#x1F3C6;</div>
<div class="card-title">大会後レビュー</div>
<div class="card-desc">大会終了後の毎週月曜に自動生成されます。</div>
<span class="card-status none">未生成</span>
</div>"""

    # Training history card
    if has_training:
        html += """
<a href="training.html" class="card">
<div class="card-icon">&#x1F9E0;</div>
<div class="card-title">EGS 訓練履歴</div>
<div class="card-desc">MLモデルの訓練メトリクスの推移と、特徴量重要度のトレンドを表示。</div>
<span class="card-status ready">公開中</span>
</a>"""
    else:
        html += """
<div class="card disabled">
<div class="card-icon">&#x1F9E0;</div>
<div class="card-title">EGS 訓練履歴</div>
<div class="card-desc">モデル再訓練（毎週月曜）の後に自動生成されます。</div>
<span class="card-status none">未生成</span>
</div>"""

    # Model Comparison card (v1 vs v2)
    if has_model_comparison:
        html += """
<a href="model_comparison.html" class="card">
<div class="card-icon">&#x1F52C;</div>
<div class="card-title">EGS モデル比較 (v1 vs v2)</div>
<div class="card-desc">ベースライン(v1)とロング/ショートメモリモデル(v2)の精度比較。特徴量重要度、レーダーチャート付き。</div>
<span class="card-status ready">公開中</span>
</a>"""
    else:
        html += """
<div class="card disabled">
<div class="card-icon">&#x1F52C;</div>
<div class="card-title">EGS モデル比較 (v1 vs v2)</div>
<div class="card-desc">v2モデル訓練後に自動生成されます。</div>
<span class="card-status none">未生成</span>
</div>"""

    # Backtest card
    if has_backtest:
        html += """
<a href="backtest.html" class="card">
<div class="card-icon">&#x1F3AF;</div>
<div class="card-title">3モデル バックテスト比較</div>
<div class="card-desc">ML予測・EGS v1・EGS v2 の3モデルの予測と実際の大会結果を比較。グループ1位の的中率、大会別の詳細結果。</div>
<span class="card-status ready">公開中</span>
</a>"""
    else:
        html += """
<div class="card disabled">
<div class="card-icon">&#x1F3AF;</div>
<div class="card-title">3モデル バックテスト比較</div>
<div class="card-desc">大会結果の収集後に自動生成されます。</div>
<span class="card-status none">未生成</span>
</div>"""

    html += f"""
</div>
</div>
<div class="footer">最終更新: {now}</div>
</body>
</html>"""

    path = out / "index.html"
    path.write_text(html, encoding="utf-8")
    print(f"[INFO] Saved portal page to {path}")
    return path


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
