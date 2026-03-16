"""EGS v1 vs v2 比較HTMLレポート生成。

v1 (Baseline) と v2 (Long/Short Memory) のメトリクスを
視覚的に比較するダッシュボードHTMLを生成する。

Usage:
    uv run python -c "from src.egs_v2_report import generate_comparison_report; generate_comparison_report()"
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


MODEL_DIR = Path("data/models")
OUTPUT_DIR = Path("data/output")
V1_METADATA_PATH = MODEL_DIR / "egs_model_metadata.json"
V2_METADATA_PATH = MODEL_DIR / "egs_v2_metadata.json"
V2_HISTORY_PATH = MODEL_DIR / "egs_v2_training_history.json"
V1_HISTORY_PATH = MODEL_DIR / "egs_training_history.json"


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None


def generate_comparison_report() -> Path:
    """v1 vs v2 比較HTMLを生成。"""
    v1 = _load_json(V1_METADATA_PATH) or {}
    v2 = _load_json(V2_METADATA_PATH) or {}
    v1_history = _load_json(V1_HISTORY_PATH) or []
    v2_history = _load_json(V2_HISTORY_PATH) or []

    # Feature importance data for charts
    v1_cut_fi = v1.get("cut_feature_importance", {})
    v1_pos_fi = v1.get("pos_feature_importance", {})
    v2_cut_fi = v2.get("cut_feature_importance", {})
    v2_pos_fi = v2.get("pos_feature_importance", {})

    # Metrics comparison
    metrics = [
        {"name": "CUT ROC-AUC", "v1": v1.get("cut_roc_auc_cv", 0), "v2": v2.get("cut_roc_auc_cv", 0), "higher_better": True},
        {"name": "CUT Brier Score", "v1": v1.get("cut_brier_cv", 0), "v2": v2.get("cut_brier_cv", 0), "higher_better": False},
        {"name": "CUT Accuracy", "v1": v1.get("cut_accuracy_cv", 0), "v2": v2.get("cut_accuracy_cv", 0), "higher_better": True},
        {"name": "Position MAE", "v1": v1.get("pos_mae_cv", 0), "v2": v2.get("pos_mae_cv", 0), "higher_better": False},
        {"name": "Position R2", "v1": v1.get("pos_r2_cv", 0), "v2": v2.get("pos_r2_cv", 0), "higher_better": True},
        {"name": "Position MAE (raw)", "v1": v1.get("pos_mae_raw_cv", 0), "v2": v2.get("pos_mae_raw_cv", 0), "higher_better": False},
    ]

    # v2 feature categorization
    long_features = [
        "career_cut_rate", "career_avg_position_pct", "career_tournaments_played",
        "year_over_year_trend", "course_history_avg_pos", "course_history_cut_rate",
    ]
    short_features = [
        "recent_3t_avg_pos_pct", "recent_3t_cut_rate", "recent_3t_best_pos_pct",
        "momentum", "recent_vs_season",
    ]

    def feat_category(name):
        if name in long_features:
            return "long"
        elif name in short_features:
            return "short"
        return "base"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EGS モデル比較: v1 vs v2</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
    --bg: #0f172a; --surface: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #3b82f6;
    --v1-color: #f97316; --v2-color: #22c55e;
    --long-color: #a855f7; --short-color: #06b6d4; --base-color: #64748b;
    --win: #22c55e; --lose: #ef4444;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ text-align: center; margin-bottom: 8px; font-size: 1.6rem; }}
.subtitle {{ text-align: center; color: var(--muted); margin-bottom: 24px; font-size: 0.9rem; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
.card h2 {{ font-size: 1.1rem; margin-bottom: 12px; color: var(--accent); }}
.card h3 {{ font-size: 0.95rem; margin-bottom: 8px; color: var(--muted); }}
.full-width {{ grid-column: 1 / -1; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }}
td {{ font-size: 0.95rem; }}
.metric-val {{ font-family: 'Consolas', monospace; font-weight: 600; }}
.v1-val {{ color: var(--v1-color); }}
.v2-val {{ color: var(--v2-color); }}
.winner {{ font-weight: bold; }}
.win {{ color: var(--win); }}
.lose {{ color: var(--lose); }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
.tag-long {{ background: rgba(168,85,247,0.2); color: var(--long-color); }}
.tag-short {{ background: rgba(6,182,212,0.2); color: var(--short-color); }}
.tag-base {{ background: rgba(100,116,139,0.2); color: var(--base-color); }}
.model-badge {{ display: inline-block; padding: 4px 16px; border-radius: 20px; font-weight: 700; font-size: 0.85rem; }}
.badge-v1 {{ background: rgba(249,115,22,0.2); color: var(--v1-color); border: 1px solid var(--v1-color); }}
.badge-v2 {{ background: rgba(34,197,94,0.2); color: var(--v2-color); border: 1px solid var(--v2-color); }}
.summary-box {{ display: flex; gap: 20px; justify-content: center; margin-bottom: 20px; flex-wrap: wrap; }}
.summary-item {{ text-align: center; padding: 16px 24px; background: var(--surface); border-radius: 12px; border: 1px solid var(--border); min-width: 140px; }}
.summary-item .label {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 4px; }}
.summary-item .value {{ font-size: 1.4rem; font-weight: 700; }}
canvas {{ max-height: 350px; }}
@media (max-width: 768px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<div class="container">
<h1>EGS モデル比較</h1>
<p class="subtitle">
    <span class="model-badge badge-v1">v1 ベースライン</span> vs
    <span class="model-badge badge-v2">v2 ロング/ショートメモリ</span>
    &mdash; 生成日時: {now}
</p>

<div class="summary-box">
    <div class="summary-item">
        <div class="label">v1 特徴量数</div>
        <div class="value v1-val">{len(v1.get('features_used', []))}</div>
    </div>
    <div class="summary-item">
        <div class="label">v2 特徴量数</div>
        <div class="value v2-val">{len(v2.get('features_used', []))}</div>
    </div>
    <div class="summary-item">
        <div class="label">v2 ロングメモリ</div>
        <div class="value" style="color:var(--long-color)">{v2.get('n_long_memory_features', 0)}</div>
    </div>
    <div class="summary-item">
        <div class="label">v2 ショートメモリ</div>
        <div class="value" style="color:var(--short-color)">{v2.get('n_short_memory_features', 0)}</div>
    </div>
    <div class="summary-item">
        <div class="label">訓練サンプル数</div>
        <div class="value">{v2.get('n_samples_cut', 0):,}</div>
    </div>
</div>

<div class="grid">
<!-- Metrics Table -->
<div class="card full-width">
<h2>パフォーマンス指標の比較</h2>
<table>
<thead>
<tr><th>指標</th><th>v1 ベースライン</th><th>v2 メモリ</th><th>差分</th><th>優位</th></tr>
</thead>
<tbody>
"""

    for m in metrics:
        diff = m["v2"] - m["v1"]
        if m["higher_better"]:
            winner = "v2" if diff > 0.0001 else ("v1" if diff < -0.0001 else "Tie")
        else:
            winner = "v2" if diff < -0.0001 else ("v1" if diff > 0.0001 else "Tie")
        winner_class = "win" if winner == "v2" else ("lose" if winner == "v1" else "")
        winner_label = f'<span class="{winner_class}">{winner}</span>'
        diff_sign = "+" if diff > 0 else ""
        html += f"""<tr>
    <td>{m['name']}</td>
    <td class="metric-val v1-val">{m['v1']:.4f}</td>
    <td class="metric-val v2-val">{m['v2']:.4f}</td>
    <td class="metric-val">{diff_sign}{diff:.4f}</td>
    <td class="winner">{winner_label}</td>
</tr>
"""

    html += """</tbody></table></div>

<!-- Radar Chart -->
<div class="card">
<h2>指標レーダーチャート</h2>
<canvas id="radarChart"></canvas>
</div>

<!-- Feature Count Chart -->
<div class="card">
<h2>特徴量アーキテクチャ</h2>
<canvas id="featureChart"></canvas>
</div>

<!-- CUT Feature Importance -->
<div class="card">
<h2>CUT分類器 - 特徴量重要度 (v2)</h2>
<canvas id="cutImportanceChart"></canvas>
</div>

<!-- Position Feature Importance -->
<div class="card">
<h2>順位回帰 - 特徴量重要度 (v2)</h2>
<canvas id="posImportanceChart"></canvas>
</div>

<!-- v2 Feature Details -->
<div class="card full-width">
<h2>v2 特徴量の詳細</h2>
<table>
<thead>
<tr><th>特徴量</th><th>カテゴリ</th><th>CUT重要度</th><th>順位重要度</th><th>説明</th></tr>
</thead>
<tbody>
"""

    feature_descriptions = {
        "sg_approach": "ストロークス・ゲインド: アプローチ",
        "sg_off_tee": "ストロークス・ゲインド: オフ・ザ・ティー",
        "sg_tee_to_green": "ストロークス・ゲインド: ティー・トゥ・グリーン",
        "gir_pct": "パーオン率 (%)",
        "scrambling_pct": "スクランブリング率 (%)",
        "scoring_average": "平均スコア",
        "scoring_average_rank": "平均スコア順位 (WGR代替指標)",
        "field_size": "大会フィールドサイズ",
        "field_strength": "フィールド強度 (出場選手の平均スコア)",
        "player_relative_strength": "選手の相対的強さ (フィールド平均との差)",
        "career_cut_rate": "CUT通過率 (過去3年間の通算)",
        "career_avg_position_pct": "平均順位パーセンタイル (過去3年)",
        "career_tournaments_played": "大会出場数 (過去3年)",
        "year_over_year_trend": "平均スコアの前年比変化 (成長/衰退)",
        "course_history_avg_pos": "同一大会での過去平均順位 (コース相性)",
        "course_history_cut_rate": "同一大会での過去CUT通過率",
        "recent_3t_avg_pos_pct": "直近3大会の平均順位パーセンタイル",
        "recent_3t_cut_rate": "直近3大会のCUT通過率",
        "recent_3t_best_pos_pct": "直近3大会の最高順位パーセンタイル",
        "momentum": "モメンタム (直近の順位改善傾向、回帰の傾き)",
        "recent_vs_season": "直近フォームとシーズン平均の乖離度",
    }

    for feat in v2.get("features_used", []):
        cat = feat_category(feat)
        tag_class = f"tag-{cat}"
        tag_label = {"long": "ロングメモリ", "short": "ショートメモリ", "base": "ベース"}[cat]
        cut_imp = v2_cut_fi.get(feat, 0)
        pos_imp = v2_pos_fi.get(feat, 0)
        desc = feature_descriptions.get(feat, feat)
        html += f"""<tr>
    <td><code>{feat}</code></td>
    <td><span class="tag {tag_class}">{tag_label}</span></td>
    <td class="metric-val">{cut_imp:.4f}</td>
    <td class="metric-val">{pos_imp:.4f}</td>
    <td style="color:var(--muted)">{desc}</td>
</tr>
"""

    html += "</tbody></table></div></div>"

    # Chart.js scripts
    # Prepare data for charts
    v2_cut_sorted = sorted(v2_cut_fi.items(), key=lambda x: -x[1])[:12]
    v2_pos_sorted = sorted(v2_pos_fi.items(), key=lambda x: -x[1])[:12]

    cut_labels = [f[0] for f in v2_cut_sorted]
    cut_values = [f[1] for f in v2_cut_sorted]
    cut_colors = [
        "'rgba(168,85,247,0.7)'" if feat_category(f[0]) == "long"
        else "'rgba(6,182,212,0.7)'" if feat_category(f[0]) == "short"
        else "'rgba(100,116,139,0.7)'"
        for f in v2_cut_sorted
    ]

    pos_labels = [f[0] for f in v2_pos_sorted]
    pos_values = [f[1] for f in v2_pos_sorted]
    pos_colors = [
        "'rgba(168,85,247,0.7)'" if feat_category(f[0]) == "long"
        else "'rgba(6,182,212,0.7)'" if feat_category(f[0]) == "short"
        else "'rgba(100,116,139,0.7)'"
        for f in v2_pos_sorted
    ]

    # Normalize metrics for radar chart (0-1 scale)
    def norm(val, mn, mx):
        return (val - mn) / (mx - mn) if mx != mn else 0.5

    html += f"""
<script>
const radarCtx = document.getElementById('radarChart').getContext('2d');
new Chart(radarCtx, {{
    type: 'radar',
    data: {{
        labels: ['ROC-AUC', 'Accuracy', '1-Brier', 'R2', '1-MAE'],
        datasets: [{{
            label: 'v1 ベースライン',
            data: [{v1.get('cut_roc_auc_cv',0)}, {v1.get('cut_accuracy_cv',0)},
                   {1-v1.get('cut_brier_cv',0)}, {v1.get('pos_r2_cv',0)},
                   {1-v1.get('pos_mae_cv',0)}],
            borderColor: '#f97316', backgroundColor: 'rgba(249,115,22,0.1)',
            pointBackgroundColor: '#f97316',
        }}, {{
            label: 'v2 メモリ',
            data: [{v2.get('cut_roc_auc_cv',0)}, {v2.get('cut_accuracy_cv',0)},
                   {1-v2.get('cut_brier_cv',0)}, {v2.get('pos_r2_cv',0)},
                   {1-v2.get('pos_mae_cv',0)}],
            borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)',
            pointBackgroundColor: '#22c55e',
        }}]
    }},
    options: {{
        responsive: true,
        scales: {{ r: {{
            beginAtZero: false, min: 0.5, max: 0.85,
            grid: {{ color: '#334155' }},
            pointLabels: {{ color: '#94a3b8' }},
            ticks: {{ color: '#64748b', backdropColor: 'transparent' }}
        }} }},
        plugins: {{ legend: {{ labels: {{ color: '#e2e8f0' }} }} }}
    }}
}});

const featCtx = document.getElementById('featureChart').getContext('2d');
new Chart(featCtx, {{
    type: 'doughnut',
    data: {{
        labels: ['ベース ({v2.get("n_base_features",10)})', 'ロングメモリ ({v2.get("n_long_memory_features",6)})', 'ショートメモリ ({v2.get("n_short_memory_features",5)})'],
        datasets: [{{
            data: [{v2.get('n_base_features',10)}, {v2.get('n_long_memory_features',6)}, {v2.get('n_short_memory_features',5)}],
            backgroundColor: ['rgba(100,116,139,0.7)', 'rgba(168,85,247,0.7)', 'rgba(6,182,212,0.7)'],
            borderColor: ['#64748b', '#a855f7', '#06b6d4'], borderWidth: 2,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{
            legend: {{ position: 'bottom', labels: {{ color: '#e2e8f0' }} }}
        }}
    }}
}});

const cutCtx = document.getElementById('cutImportanceChart').getContext('2d');
new Chart(cutCtx, {{
    type: 'bar', data: {{
        labels: {json.dumps(cut_labels)},
        datasets: [{{ data: {json.dumps(cut_values)},
            backgroundColor: [{','.join(cut_colors)}],
            borderRadius: 4,
        }}]
    }},
    options: {{
        responsive: true, indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }},
            y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0', font: {{ size: 11 }} }} }}
        }}
    }}
}});

const posCtx = document.getElementById('posImportanceChart').getContext('2d');
new Chart(posCtx, {{
    type: 'bar', data: {{
        labels: {json.dumps(pos_labels)},
        datasets: [{{ data: {json.dumps(pos_values)},
            backgroundColor: [{','.join(pos_colors)}],
            borderRadius: 4,
        }}]
    }},
    options: {{
        responsive: true, indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }},
            y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e8f0', font: {{ size: 11 }} }} }}
        }}
    }}
}});
</script>
<p style="text-align:center;color:var(--muted);margin-top:20px;font-size:0.8rem;">
    <span style="color:var(--base-color)">&#9632;</span> ベース
    <span style="color:var(--long-color)">&#9632;</span> ロングメモリ
    <span style="color:var(--short-color)">&#9632;</span> ショートメモリ
</p>
</div>
</body>
</html>
"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "model_comparison.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"[OK] Comparison report saved: {output_path}")
    return output_path
