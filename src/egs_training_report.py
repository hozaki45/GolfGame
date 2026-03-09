"""EGS Training History HTML Report.

訓練履歴をグラフ付きHTMLダッシュボードとして生成する。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .egs_model_trainer import EGS_HISTORY_PATH


OUTPUT_DIR = Path("data/output")


def generate_training_report(output_dir: str = "data/output") -> Path | None:
    """訓練履歴HTMLレポートを生成する。

    Returns:
        Path to saved HTML file, or None if no history.
    """
    if not EGS_HISTORY_PATH.exists():
        print("[INFO] No training history found, skipping report")
        return None

    with open(EGS_HISTORY_PATH, encoding="utf-8") as f:
        history = json.load(f)

    if not history:
        print("[INFO] Training history is empty, skipping report")
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    html = _build_html(history)
    path = out / "training.html"
    path.write_text(html, encoding="utf-8")
    print(f"[OK] Training history report: {path}")
    return path


def _build_html(history: list[dict]) -> str:
    """Build complete HTML string for training history dashboard."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n = len(history)
    latest = history[-1]

    # Prepare data series for charts
    dates = []
    roc_auc = []
    brier = []
    accuracy = []
    mae = []
    r2 = []
    mae_raw = []
    n_cut = []
    n_pos = []

    for entry in history:
        ts = entry.get("trained_at", "")[:16].replace("T", " ")
        dates.append(ts)
        roc_auc.append(entry.get("cut_roc_auc_cv", 0))
        brier.append(entry.get("cut_brier_cv", 0))
        accuracy.append(entry.get("cut_accuracy_cv", 0))
        mae.append(entry.get("pos_mae_cv", 0))
        r2.append(entry.get("pos_r2_cv", 0))
        mae_raw.append(entry.get("pos_mae_raw_cv", 0))
        n_cut.append(entry.get("n_samples_cut", 0))
        n_pos.append(entry.get("n_samples_position", 0))

    # Feature importance table (latest)
    cut_fi = latest.get("cut_feature_importance", {})
    pos_fi = latest.get("pos_feature_importance", {})
    cut_fi_sorted = sorted(cut_fi.items(), key=lambda x: x[1], reverse=True)
    pos_fi_sorted = sorted(pos_fi.items(), key=lambda x: x[1], reverse=True)

    # Build metrics cards
    def _trend(values):
        if len(values) < 2:
            return ""
        diff = values[-1] - values[-2]
        if abs(diff) < 0.0001:
            return '<span style="color:#a1a1aa">→</span>'
        arrow = "↑" if diff > 0 else "↓"
        color = "#22c55e" if diff > 0 else "#ef4444"
        return f'<span style="color:{color}">{arrow} {abs(diff):.4f}</span>'

    def _trend_inv(values):
        """Lower is better."""
        if len(values) < 2:
            return ""
        diff = values[-1] - values[-2]
        if abs(diff) < 0.0001:
            return '<span style="color:#a1a1aa">→</span>'
        arrow = "↑" if diff > 0 else "↓"
        color = "#ef4444" if diff > 0 else "#22c55e"
        return f'<span style="color:{color}">{arrow} {abs(diff):.4f}</span>'

    # History table rows
    table_rows = ""
    for i, entry in enumerate(reversed(history)):
        ts = entry.get("trained_at", "")[:16].replace("T", " ")
        table_rows += f"""<tr>
<td>{n - i}</td>
<td>{ts}</td>
<td>{entry.get('cut_roc_auc_cv', 0):.4f}</td>
<td>{entry.get('cut_brier_cv', 0):.4f}</td>
<td>{entry.get('cut_accuracy_cv', 0):.4f}</td>
<td>{entry.get('pos_mae_cv', 0):.4f}</td>
<td>{entry.get('pos_r2_cv', 0):.4f}</td>
<td>{entry.get('pos_mae_raw_cv', 0):.1f}</td>
<td>{entry.get('n_samples_cut', 0)}</td>
</tr>"""

    # Feature descriptions
    feat_desc = {
        "sg_approach": "アプローチショットの巧さ",
        "sg_off_tee": "ティーショットの飛距離と正確性",
        "sg_tee_to_green": "ティー→グリーンの総合力",
        "gir_pct": "グリーンに規定打数で乗せた割合",
        "scrambling_pct": "ショートゲームの強さ",
        "scoring_average": "選手の平均スコア",
        "scoring_average_rank": "フィールド内スコア順位",
        "field_size": "大会の出場選手数",
        "field_strength": "フィールド全体のレベル",
        "player_relative_strength": "選手のフィールド内相対強度",
    }

    # Feature importance rows
    fi_rows = ""
    for feat, val in cut_fi_sorted:
        pos_val = pos_fi.get(feat, 0)
        desc = feat_desc.get(feat, "")
        fi_rows += f"""<tr>
<td>{feat}</td>
<td style="font-size:0.78em;color:var(--text3)">{desc}</td>
<td><div class="bar-container"><div class="bar cut-bar" style="width:{val*100:.0f}%"></div></div></td>
<td>{val:.4f}</td>
<td><div class="bar-container"><div class="bar pos-bar" style="width:{pos_val*100:.0f}%"></div></div></td>
<td>{pos_val:.4f}</td>
</tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EGS Training History</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #09090b;
  --surface: rgba(255,255,255,0.04);
  --border: rgba(255,255,255,0.06);
  --text: #fafafa;
  --text2: #a1a1aa;
  --text3: #52525b;
  --accent: #22c55e;
  --accent2: #3b82f6;
  --accent3: #f59e0b;
  --radius: 16px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:'Inter',system-ui,sans-serif;
  background:var(--bg); color:var(--text);
  -webkit-font-smoothing:antialiased;
  padding:24px;
}}
.header {{
  text-align:center; margin-bottom:32px;
}}
.header h1 {{
  font-size:1.8em; font-weight:900; letter-spacing:-0.03em;
  background:linear-gradient(135deg, #22c55e, #3b82f6);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text;
}}
.header .sub {{ color:var(--text3); font-size:0.85em; margin-top:4px; }}
.metrics {{
  display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr));
  gap:12px; margin-bottom:24px;
}}
.metric-card {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); padding:16px 20px;
}}
.metric-label {{ font-size:0.75em; color:var(--text2); text-transform:uppercase; letter-spacing:0.05em; }}
.metric-value {{ font-size:1.5em; font-weight:800; margin:4px 0; }}
.metric-trend {{ font-size:0.8em; }}
.charts {{
  display:grid; grid-template-columns:repeat(auto-fit, minmax(480px,1fr));
  gap:16px; margin-bottom:24px;
}}
.chart-card {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); padding:20px;
}}
.chart-card h3 {{
  font-size:0.9em; font-weight:700; margin-bottom:12px; color:var(--text2);
}}
.section-title {{
  font-size:1.1em; font-weight:700; margin:24px 0 12px;
}}
table {{
  width:100%; border-collapse:collapse; font-size:0.82em;
  background:var(--surface); border-radius:var(--radius); overflow:hidden;
}}
th, td {{ padding:10px 14px; text-align:left; border-bottom:1px solid var(--border); }}
th {{ color:var(--text2); font-weight:600; font-size:0.85em; text-transform:uppercase; letter-spacing:0.03em; }}
td {{ font-variant-numeric:tabular-nums; }}
tr:last-child td {{ border-bottom:none; }}
.bar-container {{ width:100px; height:12px; background:rgba(255,255,255,0.06); border-radius:6px; overflow:hidden; }}
.bar {{ height:100%; border-radius:6px; }}
.cut-bar {{ background:var(--accent); }}
.pos-bar {{ background:var(--accent2); }}
.footer {{
  text-align:center; color:var(--text3); font-size:0.72em; margin-top:32px;
}}
a.back {{ color:var(--accent); text-decoration:none; font-size:0.85em; }}
a.back:hover {{ text-decoration:underline; }}
.guide {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); padding:24px; margin-bottom:24px;
  line-height:1.8;
}}
.guide h2 {{
  font-size:1.1em; font-weight:800; margin-bottom:16px;
  color:var(--accent2);
}}
.guide h3 {{
  font-size:0.9em; font-weight:700; margin:16px 0 8px;
  color:var(--accent);
  border-bottom:1px solid var(--border); padding-bottom:4px;
}}
.guide p {{ font-size:0.82em; color:var(--text2); margin:4px 0; }}
.guide .term {{
  display:grid; grid-template-columns:140px 1fr; gap:4px 12px;
  margin:6px 0; font-size:0.82em;
}}
.guide .term dt {{
  color:var(--text); font-weight:600; font-family:monospace;
}}
.guide .term dd {{ color:var(--text2); }}
.guide .ideal {{ color:#22c55e; font-size:0.78em; }}
.guide .note {{ color:var(--accent3); font-size:0.78em; font-style:italic; }}
.toggle-btn {{
  background:var(--surface); border:1px solid var(--border);
  color:var(--text2); padding:6px 14px; border-radius:8px;
  cursor:pointer; font-size:0.8em; font-family:inherit;
  transition:all 0.2s;
}}
.toggle-btn:hover {{ border-color:var(--border-hover,rgba(255,255,255,0.15)); color:var(--text); }}
</style>
</head>
<body>

<div class="header">
  <div><a href="index.html" class="back">← Back to Home</a></div>
  <h1>EGS Training History</h1>
  <div class="sub">{n} training runs · Last trained: {dates[-1]}</div>
  <div style="margin-top:12px">
    <button class="toggle-btn" onclick="document.getElementById('guide').style.display=document.getElementById('guide').style.display==='none'?'block':'none'">
      📖 指標の見方ガイド（クリックで開閉）
    </button>
  </div>
</div>

<div class="guide" id="guide" style="display:none">
  <h2>📖 このページの見方</h2>

  <p>EGS（Expected Game Score）モデルは<strong>2段階構成</strong>の機械学習モデルです。</p>
  <p><strong>Stage 1: CutClassifier</strong> — 各選手が予選カットを通過するかどうかを予測</p>
  <p><strong>Stage 2: PositionRegressor</strong> — カット通過した選手の最終順位を予測</p>
  <p>この2つを組み合わせて、各グループの「期待ゲームスコア」を算出し、最適な選手選択を提案します。</p>
  <p style="margin-top:8px" class="note">※ 毎週月曜に新しい大会結果を含めて自動的に再訓練されます。データが増えるほど精度が向上します。</p>

  <h3>🎯 CutClassifier（カット予測）の指標</h3>

  <dl class="term">
    <dt>ROC-AUC</dt>
    <dd>
      モデルの「見分ける力」を示す指標。「カット通過する選手」と「落ちる選手」をどれだけ正しく区別できるかを表します。
      <br>コイン投げ（ランダム）なら 0.5、完璧に見分けられれば 1.0。
      <br><span class="ideal">→ 高いほど良い。0.7以上で実用的、0.8以上で優秀。</span>
    </dd>
  </dl>

  <dl class="term">
    <dt>ブライアスコア<br>(Brier Score)</dt>
    <dd>
      予測した確率がどれだけ正確かを示す指標。例：「70%の確率でカット通過」と予測して実際に通過したら誤差は小さく、外れたら大きくなります。
      <br><span class="ideal">→ 低いほど良い。0なら完璧、0.25はランダムと同じ。</span>
    </dd>
  </dl>

  <dl class="term">
    <dt>正答率<br>(Accuracy)</dt>
    <dd>
      「カット通過/落ち」の予測が当たった割合。全選手のうち、予測が正解だった割合です。最も直感的な指標。
      <br><span class="ideal">→ 高いほど良い。0.5ならコイン投げと同じ、0.66が現在の水準。</span>
    </dd>
  </dl>

  <h3>📊 PositionRegressor（順位予測）の指標</h3>

  <dl class="term">
    <dt>MAE %</dt>
    <dd>
      予測順位と実際の順位のズレを、出場人数で割った値の平均。フィールドの大きさに関係なく比較できる指標です。
      <br>例：150人中30位ずれたら 30÷150 = 0.20
      <br><span class="ideal">→ 低いほど良い。0なら完璧、0.22が現在の水準。</span>
    </dd>
  </dl>

  <dl class="term">
    <dt>決定係数<br>(R²)</dt>
    <dd>
      モデルの予測が実際の順位のばらつきをどれだけ説明できるかを示します。
      「平均順位をそのまま予測する」のと比べてどれだけ賢いか、と読み替えられます。
      <br><span class="ideal">→ 高いほど良い。0なら平均値予測と同じ、1.0なら完璧。ゴルフは不確実性が高く、0.14でも意味のある予測。</span>
    </dd>
  </dl>

  <dl class="term">
    <dt>MAE（順位数）<br>(MAE places)</dt>
    <dd>
      予測した順位と実際の最終順位が平均して何位ずれているか。最も分かりやすい指標。
      <br>例：16.0なら「平均して16位くらいずれる」という意味。
      <br><span class="ideal">→ 低いほど良い。ゴルフは運の要素が大きく、16位程度のズレは妥当な水準。</span>
    </dd>
  </dl>

  <h3>🧬 特徴量重要度（Feature Importance）</h3>
  <p>モデルが予測する時に、どの情報をどれだけ重視しているかを示すバーグラフです。バーが長いほどその情報が予測に大きく影響しています。</p>
  <p style="margin-top:4px" class="note">※ Permutation Importance で計測（その特徴量をシャッフルした時にどれだけ精度が落ちるか）</p>

  <dl class="term">
    <dt>player_relative_strength</dt>
    <dd>選手のフィールド内での相対強度。最も重要な指標で、フィールド平均に対するその選手の実力差を表す</dd>
  </dl>
  <dl class="term">
    <dt>field_size</dt>
    <dd>大会の出場選手数。大会規模によってカット率や順位分布が変わる</dd>
  </dl>
  <dl class="term">
    <dt>scoring_average_rank</dt>
    <dd>その選手のスコア平均のフィールド内順位</dd>
  </dl>
  <dl class="term">
    <dt>sg_tee_to_green</dt>
    <dd>Strokes Gained: Tee to Green（ティーからグリーンまでの総合力）</dd>
  </dl>
  <dl class="term">
    <dt>sg_approach</dt>
    <dd>Strokes Gained: Approach（グリーンを狙うアプローチショットの巧さ）</dd>
  </dl>
  <dl class="term">
    <dt>sg_off_tee</dt>
    <dd>Strokes Gained: Off the Tee（ティーショットの飛距離と正確性）</dd>
  </dl>
  <dl class="term">
    <dt>gir_pct</dt>
    <dd>Greens in Regulation %（規定打数以内にグリーンに乗せた割合）</dd>
  </dl>
  <dl class="term">
    <dt>scrambling_pct</dt>
    <dd>Scrambling %（グリーンを外した後にパー以上で上がった割合＝ショートゲームの強さ）</dd>
  </dl>
  <dl class="term">
    <dt>scoring_average</dt>
    <dd>選手の平均スコア（低いほど上手い）</dd>
  </dl>
  <dl class="term">
    <dt>field_strength</dt>
    <dd>フィールド強度（出場選手全体のレベル。メジャー大会ほど高い）</dd>
  </dl>

  <h3>📈 トレンドの見方</h3>
  <p><span style="color:#22c55e">緑の↑↓</span> = 改善方向への変化（良くなった）</p>
  <p><span style="color:#ef4444">赤の↑↓</span> = 悪化方向への変化（悪くなった）</p>
  <p><span style="color:#a1a1aa">→</span> = 変化なし</p>
  <p style="margin-top:8px" class="note">※ ROC-AUC・Accuracy・R² は高いほど良い / Brier・MAE は低いほど良い</p>
</div>

<div class="metrics">
  <div class="metric-card">
    <div class="metric-label">ROC-AUC (Cut)</div>
    <div class="metric-value">{roc_auc[-1]:.4f}</div>
    <div style="font-size:0.72em;color:var(--text3);margin-bottom:4px">カット予測の識別力</div>
    <div class="metric-trend">{_trend(roc_auc)}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Brier Score (Cut)</div>
    <div class="metric-value">{brier[-1]:.4f}</div>
    <div style="font-size:0.72em;color:var(--text3);margin-bottom:4px">確率予測の正確さ</div>
    <div class="metric-trend">{_trend_inv(brier)}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Accuracy (Cut)</div>
    <div class="metric-value">{accuracy[-1]:.4f}</div>
    <div style="font-size:0.72em;color:var(--text3);margin-bottom:4px">カット予測の正答率</div>
    <div class="metric-trend">{_trend(accuracy)}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">MAE % (Position)</div>
    <div class="metric-value">{mae[-1]:.4f}</div>
    <div style="font-size:0.72em;color:var(--text3);margin-bottom:4px">順位予測の平均誤差（割合）</div>
    <div class="metric-trend">{_trend_inv(mae)}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">R² (Position)</div>
    <div class="metric-value">{r2[-1]:.4f}</div>
    <div style="font-size:0.72em;color:var(--text3);margin-bottom:4px">順位予測の説明力</div>
    <div class="metric-trend">{_trend(r2)}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">MAE places</div>
    <div class="metric-value">{mae_raw[-1]:.1f}</div>
    <div style="font-size:0.72em;color:var(--text3);margin-bottom:4px">平均して何位ずれるか</div>
    <div class="metric-trend">{_trend_inv(mae_raw)}</div>
  </div>
</div>

<div class="charts">
  <div class="chart-card">
    <h3>CutClassifier Metrics</h3>
    <canvas id="cutChart"></canvas>
  </div>
  <div class="chart-card">
    <h3>PositionRegressor Metrics</h3>
    <canvas id="posChart"></canvas>
  </div>
  <div class="chart-card">
    <h3>Training Samples</h3>
    <canvas id="samplesChart"></canvas>
  </div>
</div>

<div class="section-title">Feature Importance (Latest)</div>
<table>
<thead><tr>
<th>Feature</th><th>説明</th><th>Cut (bar)</th><th>Cut</th><th>Position (bar)</th><th>Position</th>
</tr></thead>
<tbody>{fi_rows}</tbody>
</table>

<div class="section-title" style="margin-top:24px">All Training Runs</div>
<table>
<thead><tr>
<th>#</th><th>Date</th><th>ROC-AUC</th><th>Brier</th><th>Accuracy</th>
<th>MAE%</th><th>R²</th><th>MAE places</th><th>Samples</th>
</tr></thead>
<tbody>{table_rows}</tbody>
</table>

<div class="footer">Generated {now} · <a href="index.html" class="back">Home</a></div>

<script>
const labels = {json.dumps(dates)};
const chartDefaults = {{
  responsive: true,
  interaction: {{ mode: 'index', intersect: false }},
  scales: {{
    x: {{ ticks: {{ color: '#52525b', font: {{ size: 10 }} }}, grid: {{ color: 'rgba(255,255,255,0.04)' }} }},
    y: {{ ticks: {{ color: '#52525b' }}, grid: {{ color: 'rgba(255,255,255,0.06)' }} }}
  }},
  plugins: {{ legend: {{ labels: {{ color: '#a1a1aa', font: {{ size: 11 }} }} }} }}
}};

new Chart(document.getElementById('cutChart'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [
      {{ label: 'ROC-AUC', data: {json.dumps(roc_auc)}, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', tension: 0.3, fill: false }},
      {{ label: 'Accuracy', data: {json.dumps(accuracy)}, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', tension: 0.3, fill: false }},
      {{ label: 'Brier', data: {json.dumps(brier)}, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', tension: 0.3, fill: false }}
    ]
  }},
  options: chartDefaults
}});

new Chart(document.getElementById('posChart'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [
      {{ label: 'R²', data: {json.dumps(r2)}, borderColor: '#22c55e', tension: 0.3, fill: false }},
      {{ label: 'MAE %', data: {json.dumps(mae)}, borderColor: '#3b82f6', tension: 0.3, fill: false }}
    ]
  }},
  options: chartDefaults
}});

new Chart(document.getElementById('samplesChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [
      {{ label: 'Cut samples', data: {json.dumps(n_cut)}, backgroundColor: 'rgba(34,197,94,0.5)' }},
      {{ label: 'Position samples', data: {json.dumps(n_pos)}, backgroundColor: 'rgba(59,130,246,0.5)' }}
    ]
  }},
  options: chartDefaults
}});
</script>
</body>
</html>"""


if __name__ == "__main__":
    path = generate_training_report()
    if path:
        print(f"Open {path} in a browser to view.")
