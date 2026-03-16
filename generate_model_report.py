"""モデル概要書PDF生成スクリプト。

PGA Tour Pick'em 予測モデルの設計・信号・バックテスト結果を
日本語PDFドキュメントとして出力する。

Usage:
    uv run python generate_model_report.py
"""

from __future__ import annotations

from datetime import datetime
from fpdf import FPDF


#-----PDF Settings-----

FONT_PATH = "C:/Windows/Fonts/msgothic.ttc"
FONT_NAME = "Gothic"
OUTPUT_FILE = "docs/model_overview_report.pdf"


class ModelReportPDF(FPDF):
    """モデル概要書PDF。"""

    def __init__(self) -> None:
        super().__init__()
        self.render_color_fonts = False
        self.add_font(FONT_NAME, "", FONT_PATH)
        self.add_font(FONT_NAME, "B", FONT_PATH)
        self.set_auto_page_break(auto=True, margin=20)

    def header(self) -> None:
        if self.page_no() > 1:
            self.set_font(FONT_NAME, "", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 6, "PGA Tour Pick'em Ensemble Prediction Model - Overview", align="L")
            self.cell(0, 6, f"Page {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
            self.line(10, 14, 200, 14)
            self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(FONT_NAME, "", 7)
        self.set_text_color(160, 160, 160)
        self.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d')} | Confidential", align="C")

    def section_title(self, num: str, title: str) -> None:
        """セクションタイトル。"""
        self.set_font(FONT_NAME, "B", 14)
        self.set_text_color(0, 51, 102)
        self.cell(0, 10, f"{num}. {title}", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(0, 102, 51)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def subsection_title(self, title: str) -> None:
        """サブセクションタイトル。"""
        self.set_font(FONT_NAME, "B", 11)
        self.set_text_color(0, 76, 153)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body_text(self, text: str) -> None:
        """本文テキスト。"""
        self.set_font(FONT_NAME, "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 6, text)
        self.ln(2)

    def bullet(self, text: str, indent: int = 10) -> None:
        """箇条書き。"""
        self.set_font(FONT_NAME, "", 10)
        self.set_text_color(30, 30, 30)
        self.set_x(self.l_margin + indent)
        avail_w = self.w - self.r_margin - self.get_x()
        self.multi_cell(avail_w, 6, f"  - {text}")
        self.ln(1)

    def key_value(self, key: str, value: str, indent: int = 10) -> None:
        """キー: バリュー行。"""
        self.set_font(FONT_NAME, "B", 10)
        self.set_text_color(30, 30, 30)
        x = self.get_x()
        self.set_x(x + indent)
        self.cell(55, 6, key)
        self.set_font(FONT_NAME, "", 10)
        avail_w = self.w - self.r_margin - self.get_x()
        self.multi_cell(avail_w, 6, value)
        self.ln(1)

    def table_header(self, cols: list[tuple[str, int]]) -> None:
        """テーブルヘッダー。"""
        self.set_font(FONT_NAME, "B", 9)
        self.set_fill_color(0, 51, 102)
        self.set_text_color(255, 255, 255)
        for label, w in cols:
            self.cell(w, 7, label, border=1, align="C", fill=True)
        self.ln()

    def table_row(self, cols: list[tuple[str, int]], fill: bool = False) -> None:
        """テーブル行。"""
        self.set_font(FONT_NAME, "", 9)
        self.set_text_color(30, 30, 30)
        if fill:
            self.set_fill_color(240, 245, 250)
        for value, w in cols:
            self.cell(w, 6, value, border=1, align="C", fill=fill)
        self.ln()


def generate() -> None:
    """モデル概要書PDFを生成。"""
    pdf = ModelReportPDF()

    #==========================================================
    # 表紙
    #==========================================================
    pdf.add_page()
    pdf.ln(50)
    pdf.set_font(FONT_NAME, "B", 24)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 14, "PGA Tour Pick'em", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 14, "Ensemble Prediction Model", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font(FONT_NAME, "", 16)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, "- Model Overview Document -", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(30)

    pdf.set_font(FONT_NAME, "", 11)
    pdf.set_text_color(60, 60, 60)
    info_lines = [
        f"Version: 2.0 (5-Signal Ensemble)",
        f"Date: {datetime.now().strftime('%Y-%m-%d')}",
        "Data Coverage: 2019-2025 PGA Tour Season",
        "Backtest: 233 tournaments, 919 groups, 6,675 observations",
    ]
    for line in info_lines:
        pdf.cell(0, 8, line, align="C", new_x="LMARGIN", new_y="NEXT")

    #==========================================================
    # 1. モデル概要
    #==========================================================
    pdf.add_page()
    pdf.section_title("1", "Model Overview / モデル概要")

    pdf.body_text(
        "本モデルは、PGA Tour Pick'em（グループ対決形式のゴルフ予測ゲーム）において、"
        "各グループの優勝者を予測するアンサンブル予測モデルである。"
    )
    pdf.body_text(
        "7名1グループの中から最終ラウンド終了時に最も順位が高い選手（グループ勝者）を予測する。"
        "5つの独立した予測信号を加重平均し、スコアの最も高い選手をピックする。"
    )

    pdf.subsection_title("1.1 予測タスク定義")
    pdf.key_value("入力:", "7名の選手グループ（トーナメント毎に5-8グループ）")
    pdf.key_value("出力:", "グループ内の予測勝者 (Top-1) / Top-2")
    pdf.key_value("ベースライン:", "ランダム選択 = 14.3% (1/7)")
    pdf.key_value("モデル精度:", "Winner = 25.9% / Top-2 = 43.6% (テスト期間)")

    pdf.subsection_title("1.2 アーキテクチャ概要")
    pdf.body_text(
        "線形アンサンブル（加重平均）方式を採用。各信号Si を 0-100 にスケーリングし、"
        "ウェイト Wi で加重合計したスコアが最大の選手を予測勝者とする。"
    )
    pdf.body_text(
        "  Score(player) = W1*S1 + W2*S2 + W3*S3 + W4*S4 + W5*S5"
    )
    pdf.body_text(
        "ウェイト制約: W1 + W2 + W3 + W4 + W5 = 1.0  (各 Wi >= 0)"
    )

    #==========================================================
    # 2. 5信号の定義
    #==========================================================
    pdf.add_page()
    pdf.section_title("2", "Signal Definitions / 5信号の定義")

    # Signal 1: Odds/Ranking
    pdf.subsection_title("Signal 1: Odds / Ranking (S1)")
    pdf.body_text(
        "ブックメーカーオッズに基づく市場の集合知信号。実オッズデータが利用可能な"
        "大会では暗示確率（implied probability）を使用し、オッズが無い大会では"
        "世界ランキング（OWGR）をプロキシとして代用する。"
    )
    pdf.key_value("データソース:", "Vegas Insider / The Odds API / OWGR")
    pdf.key_value("計算方法:", "オッズ → 暗示確率 → グループ内0-100正規化")
    pdf.key_value("フォールバック:", "WGR逆数 → グループ内0-100正規化")
    pdf.key_value("カバレッジ:", "100% (WGRフォールバック含む)")
    pdf.key_value("単独的中率:", "13.5% (WGRプロキシ使用時)")

    pdf.ln(2)

    # Signal 2: Stats
    pdf.subsection_title("Signal 2: Stats / SG統計 (S2)")
    pdf.body_text(
        "当該シーズンのStrokes Gained (SG)統計に基づくスキル信号。"
        "大会時点でのSG Total, SG Off-the-Tee, SG Approach, SG Around-the-Green, "
        "SG Puttingを統合した総合力指標。"
    )
    pdf.key_value("データソース:", "PGA Tour GraphQL API (pga_stats.db)")
    pdf.key_value("計算方法:", "SG Total → グループ内0-100正規化")
    pdf.key_value("カバレッジ:", "74% (PGA統計の取得可否に依存)")
    pdf.key_value("単独的中率:", "23.4%")

    pdf.ln(2)

    # Signal 3: Course Fit
    pdf.subsection_title("Signal 3: Course Fit / コースフィット (S3)")
    pdf.body_text(
        "選手の全大会における過去のパフォーマンス平均を指標化した信号。"
        "全トーナメントの過去出場記録（tournament_history）から平均順位を算出し、"
        "全体的な安定性・実力を測る。"
    )
    pdf.key_value("データソース:", "pickem_field_players.tournament_history (JSON)")
    pdf.key_value("計算方法:", "全大会平均順位の逆数 → グループ内0-100正規化")
    pdf.key_value("カバレッジ:", "100%")
    pdf.key_value("単独的中率:", "24.4%")

    pdf.ln(2)

    # Signal 4: Crowd
    pdf.subsection_title("Signal 4: Crowd / 群衆知 (S4)")
    pdf.body_text(
        "Pick'em参加者のピック率（pick_pct）に基づく群衆の知恵信号。"
        "多くの参加者が選んだ選手ほど高いスコアを得る。市場参加者の集合的判断を反映。"
    )
    pdf.key_value("データソース:", "pickem_field_players.pick_pct")
    pdf.key_value("計算方法:", "pick_pct → グループ内0-100正規化")
    pdf.key_value("カバレッジ:", "37% (pick_pctデータの有無に依存)")
    pdf.key_value("単独的中率:", "26.6%")

    pdf.ln(2)

    # Signal 5: Affinity
    pdf.subsection_title("Signal 5: Affinity / 大会親和性 (S5)")
    pdf.body_text(
        "選手のその特定大会における過去の出場頻度と成績に基づく親和性信号。"
        "ある選手が特定の大会に繰り返し出場し、好成績を残している場合に高スコアとなる。"
        "出場回数ボーナス (最大1.5倍) を平均順位スコアに乗じる。"
    )
    pdf.key_value("データソース:", "pickem_field_players.tournament_history (JSON)")
    pdf.key_value("計算方法:", "(100/平均順位) x 出場回数ボーナス → 0-100正規化")
    pdf.key_value("出場回数ボーナス:", "min(1.0 + (N-1)*0.2, 1.5)")
    pdf.key_value("カバレッジ:", "85%")
    pdf.key_value("単独的中率:", "27.0% (5信号中最高)")

    #==========================================================
    # 3. 信号比較テーブル
    #==========================================================
    pdf.add_page()
    pdf.section_title("3", "Signal Comparison / 信号比較")

    cols = [
        ("Signal", 35), ("Source", 35), ("Coverage", 20),
        ("Winner%", 20), ("Top-2%", 20), ("GBT Imp.", 25),
        ("Weight", 20),
    ]
    pdf.table_header(cols)
    rows = [
        ("S1: Odds/Rank", "Odds/OWGR", "100%", "13.5%", "26.3%", "0.101", "0.05"),
        ("S2: Stats", "SG Total", "74%", "23.4%", "40.6%", "0.194", "0.20"),
        ("S3: Course Fit", "History Avg", "100%", "24.4%", "44.3%", "0.177", "0.40"),
        ("S4: Crowd", "Pick Pct", "37%", "26.6%", "41.5%", "0.167", "0.35"),
        ("S5: Affinity", "Tourn Hist", "85%", "27.0%", "39.6%", "0.361", "0.00"),
    ]
    for i, row in enumerate(rows):
        vals = [(v, cols[j][1]) for j, v in enumerate(row)]
        pdf.table_row(vals, fill=(i % 2 == 1))

    pdf.ln(4)
    pdf.body_text(
        "GBT Imp. = Gradient Boosting Tree による特徴量重要度。"
        "Affinityは非線形モデル (GBT) では最重要特徴量 (0.361) だが、"
        "線形グリッドサーチでは最適ウェイト0.00となる。これはCourse Fitと高い相関を持ち、"
        "線形結合では冗長となるためである。GBTのような非線形モデルでは "
        "Affinityの独自の情報を活用できる。"
    )

    pdf.subsection_title("GBT (Gradient Boosting Tree) 分析")
    pdf.body_text(
        "GradientBoostingClassifier (scikit-learn) による非線形分析。"
        "Cross-validation accuracy: 0.826 (+/- 0.006)。"
        "線形モデルでは捉えられない信号間の交互作用を検出。"
    )
    pdf.body_text("Feature Importance Ranking:")
    pdf.bullet("affinity_signal:  0.361 (1st)")
    pdf.bullet("stats_signal:     0.194 (2nd)")
    pdf.bullet("fit_signal:       0.177 (3rd)")
    pdf.bullet("crowd_signal:     0.167 (4th)")
    pdf.bullet("ranking_signal:   0.101 (5th)")

    #==========================================================
    # 4. バックテスト手法
    #==========================================================
    pdf.add_page()
    pdf.section_title("4", "Backtest Methodology / バックテスト手法")

    pdf.subsection_title("4.1 データソース")
    pdf.body_text(
        "Point-in-Time データを使用。各大会開催時点でのスナップショットデータのみを使い、"
        "将来データの漏洩 (look-ahead bias) を排除している。"
    )
    pdf.bullet("pickem_field_players: 410大会、29,520選手レコード (PK 11-410)")
    pdf.bullet("pga_tournaments: PGA Tour大会結果 (2004-2025)")
    pdf.bullet("pga_stats.db: SG統計 22年分、25,000+レコード")
    pdf.bullet("odds_snapshots: ブックメーカーオッズ 35スナップショット、15,127エントリ")

    pdf.subsection_title("4.2 大会リンキング")
    pdf.body_text(
        "Pick'em大会名とPGA Tour大会名のファジーマッチング (fuzzywuzzy, threshold=70)。"
        "PK番号からPGAシーズン年をマッピングし、該当年の大会結果と照合する。"
    )
    pdf.key_value("リンク成功:", "233 / 283 大会 (82.3%)")
    pdf.key_value("スキップ:", "50大会 (名称不一致または結果なし)")

    pdf.subsection_title("4.3 グラウンドトゥルース")
    pdf.body_text(
        "PGA Tour大会結果の最終順位を使用。各グループ内で最も順位が高い選手をグループ勝者とする。"
        "順位の解析: 'T9'→9, 'CUT'→80, 'WD/DQ'→除外。"
    )

    pdf.subsection_title("4.4 ウォークフォワード検証")
    pdf.body_text(
        "時系列分割による out-of-sample 検証。学習期間で最適化したウェイトを "
        "テスト期間に適用し、汎化性能を測定する。"
    )
    pdf.key_value("学習期間:", "PK 13-317 (163大会, 3,403観測)")
    pdf.key_value("テスト期間:", "PK 318-409 (70大会, 3,272観測)")
    pdf.key_value("分割比:", "Train 70% / Test 30% (時系列順)")

    #==========================================================
    # 5. バックテスト結果
    #==========================================================
    pdf.add_page()
    pdf.section_title("5", "Backtest Results / バックテスト結果")

    pdf.subsection_title("5.1 全体結果サマリー")

    result_cols = [("Metric", 60), ("Value", 40)]
    pdf.table_header(result_cols)
    result_rows = [
        ("Total Tournaments", "233"),
        ("Total Groups", "919"),
        ("Total Observations", "6,675"),
        ("Real Odds Tournaments", "7"),
        ("WGR Proxy Tournaments", "226"),
    ]
    for i, (k, v) in enumerate(result_rows):
        pdf.table_row([(k, 60), (v, 40)], fill=(i % 2 == 1))

    pdf.ln(4)

    pdf.subsection_title("5.2 最適ウェイト (Grid Search)")
    weight_cols = [
        ("S1: Odds", 30), ("S2: Stats", 30), ("S3: Fit", 30),
        ("S4: Crowd", 30), ("S5: Affinity", 30),
    ]
    pdf.table_header(weight_cols)
    pdf.table_row([
        ("0.05", 30), ("0.20", 30), ("0.40", 30),
        ("0.35", 30), ("0.00", 30),
    ])

    pdf.ln(4)

    pdf.subsection_title("5.3 精度比較")
    acc_cols = [("Method", 50), ("Winner %", 35), ("Top-2 %", 35)]
    pdf.table_header(acc_cols)
    acc_rows = [
        ("Random Baseline (1/7)", "14.3%", "28.6%"),
        ("Default Weights", "22.3%", "38.3%"),
        ("Equal Weights", "31.2%", "N/A"),
        ("Optimal (Train)", "33.8%", "52.0%"),
        ("Optimal (Test)", "25.9%", "43.6%"),
    ]
    for i, (m, w, t) in enumerate(acc_rows):
        pdf.table_row([(m, 50), (w, 35), (t, 35)], fill=(i % 2 == 1))

    pdf.ln(4)
    pdf.body_text(
        "最適化ウェイトにより、テスト期間でランダムベースライン (14.3%) の約1.8倍の精度を達成。"
        "学習期間との精度差 (overfit gap) は 7.9ポイントであり、"
        "中程度の過学習が見られるが、テスト精度は十分に有意である。"
    )

    pdf.subsection_title("5.4 Overfit Analysis")
    pdf.key_value("Train Accuracy:", "33.8%")
    pdf.key_value("Test Accuracy:", "25.9%")
    pdf.key_value("Overfit Gap:", "7.9 percentage points")
    pdf.body_text(
        "線形モデルの単純性により、過学習リスクは限定的。グリッドサーチの探索空間は "
        "4,183通りのウェイト組み合わせに制限されている (ステップ0.05)。"
    )

    #==========================================================
    # 6. データパイプライン
    #==========================================================
    pdf.add_page()
    pdf.section_title("6", "Data Pipeline / データパイプライン")

    pdf.subsection_title("6.1 データ収集フロー")
    pdf.body_text("毎週のパイプライン実行で以下のデータを収集・蓄積する:")
    pdf.bullet("Pick'em CSV: グループ編成、選手情報、WGR、FedEx順位、統計、過去成績")
    pdf.bullet("オッズデータ: 複数ブックメーカーのオッズ → odds_snapshots に追記蓄積")
    pdf.bullet("PGA Tour統計: SG各カテゴリ → pga_stats.db に保存")
    pdf.bullet("大会結果: ESPN/PGA → results テーブルに保存")

    pdf.subsection_title("6.2 オッズデータ蓄積設計")
    pdf.body_text(
        "odds_snapshotsテーブルは追記専用 (append-only) 設計。パイプライン実行の度に "
        "全選手の全ブックメーカーオッズをタイムスタンプ付きで蓄積する。"
        "データは蓄積のみで削除しない。"
    )
    pdf.key_value("現在の蓄積量:", "15,127エントリ (35スナップショット)")
    pdf.key_value("1大会あたり:", "約72選手 x 6ブックメーカー = 432レコード")
    pdf.key_value("年間蓄積見込:", "約19,440レコード (45大会)")

    pdf.subsection_title("6.3 データベース構成")
    pdf.body_text("SQLite3ベースのローカルデータベース (data/pga_stats.db):")
    pdf.bullet("pickem_field_players: Pick'em選手フィールドデータ")
    pdf.bullet("pga_tournaments: PGA Tour大会結果")
    pdf.bullet("pga_player_stats: SG統計データ (22年分)")
    pdf.bullet("odds: 現行表示用オッズ (上書き)")
    pdf.bullet("odds_snapshots: オッズ蓄積 (追記のみ)")
    pdf.bullet("backtest_results: バックテスト観測データ")
    pdf.bullet("backtest_runs: バックテスト実行履歴")

    #==========================================================
    # 7. 技術スタック
    #==========================================================
    pdf.add_page()
    pdf.section_title("7", "Technical Stack / 技術スタック")

    pdf.subsection_title("7.1 言語・フレームワーク")
    pdf.bullet("Python 3.10+ (uv パッケージマネージャ)")
    pdf.bullet("pandas / numpy: データ処理・数値計算")
    pdf.bullet("scikit-learn: GradientBoostingClassifier, cross_val_score")
    pdf.bullet("fuzzywuzzy: ファジー文字列マッチング")
    pdf.bullet("SQLite3: ローカルデータベース")

    pdf.subsection_title("7.2 データソース")
    pdf.bullet("PGA Tour GraphQL API: orchestrator.pgatour.com/graphql (無料)")
    pdf.bullet("Pick'em CSV: PGA Tour Pick'em公式サイト")
    pdf.bullet("Vegas Insider / The Odds API: ブックメーカーオッズ")

    pdf.subsection_title("7.3 主要ファイル構成")
    pdf.bullet("src/backtester.py: バックテスト & ML最適化エンジン (~700行)")
    pdf.bullet("src/database.py: データベース操作 & テーブル定義")
    pdf.bullet("src/pga_stats_db.py: PGA統計データアクセス")
    pdf.bullet("src/stats_scraper.py: PGA Tour GraphQL スクレイパー")
    pdf.bullet("run_pipeline.py: 週次パイプライン実行")
    pdf.bullet("config.yaml: モデル設定 (ウェイト、閾値)")

    #==========================================================
    # 8. 今後の改善方針
    #==========================================================
    pdf.section_title("8", "Future Improvements / 今後の改善方針")

    pdf.subsection_title("8.1 オッズデータの蓄積と活用")
    pdf.body_text(
        "現在、実オッズデータがある大会は7件のみ。2-3年のデータ蓄積により、"
        "Signal 1 (Odds) の精度が大幅に向上する見込み。WGRプロキシ (13.5%) から "
        "実オッズベースの高精度信号への移行を段階的に進める。"
    )

    pdf.subsection_title("8.2 非線形モデルの検討")
    pdf.body_text(
        "GBT分析ではAffinity信号が最重要特徴量 (0.361) であり、"
        "線形モデルでは活用しきれない非線形パターンの存在を示唆。"
        "データ蓄積量が十分になった段階で、GBTベースの予測モデルへの移行を検討する。"
    )

    pdf.subsection_title("8.3 天候・コース条件の統合")
    pdf.body_text(
        "天候条件やコースセットアップの変化を信号として追加することで、"
        "コンディション依存の予測精度向上が期待できる。"
    )

    pdf.subsection_title("8.4 リアルタイム更新")
    pdf.body_text(
        "大会期間中のラウンド毎結果をフィードバックし、"
        "Day 2以降のピック予測を動的に更新するリアルタイム機能の実装。"
    )

    #==========================================================
    # Appendix
    #==========================================================
    pdf.add_page()
    pdf.section_title("A", "Appendix / 付録")

    pdf.subsection_title("A.1 PK-to-Year マッピング")
    pdf.body_text("Pick'em PK番号からPGAシーズン年への変換テーブル:")
    pk_cols = [("PK Range", 50), ("PGA Season", 40)]
    pdf.table_header(pk_cols)
    pk_rows = [
        ("PK 11-90", "2019"), ("PK 91-139", "2020"), ("PK 140-198", "2021"),
        ("PK 199-253", "2022"), ("PK 254-313", "2023"), ("PK 314-355", "2024"),
        ("PK 356+", "2025"),
    ]
    for i, (r, y) in enumerate(pk_rows):
        pdf.table_row([(r, 50), (y, 40)], fill=(i % 2 == 1))

    pdf.ln(4)

    pdf.subsection_title("A.2 スコア計算例")
    pdf.body_text("Player A のスコア計算 (最適ウェイト適用):")
    pdf.body_text(
        "  S1(Odds) = 45.2, S2(Stats) = 78.3, S3(Fit) = 62.1, "
        "S4(Crowd) = 85.0, S5(Affinity) = 71.5"
    )
    pdf.body_text(
        "  Score = 0.05*45.2 + 0.20*78.3 + 0.40*62.1 + 0.35*85.0 + 0.00*71.5"
    )
    pdf.body_text(
        "  Score = 2.26 + 15.66 + 24.84 + 29.75 + 0.00 = 72.51"
    )

    pdf.ln(4)

    pdf.subsection_title("A.3 グリッドサーチ設定")
    pdf.key_value("ステップ幅:", "0.05")
    pdf.key_value("W1 (Odds) 範囲:", "0.05 - 0.50")
    pdf.key_value("W2 (Stats) 範囲:", "0.05 - 0.50")
    pdf.key_value("W3 (Fit) 範囲:", "0.00 - 0.40")
    pdf.key_value("W4 (Crowd) 範囲:", "0.00 - 0.40")
    pdf.key_value("W5 (Affinity) 範囲:", "0.00 - 0.40")
    pdf.key_value("制約:", "W1+W2+W3+W4+W5 = 1.0")
    pdf.key_value("探索組み合わせ:", "4,183通り")

    #==========================================================
    # Output
    #==========================================================
    pdf.output(OUTPUT_FILE)
    print(f"[INFO] Model overview report generated: {OUTPUT_FILE}")
    print(f"[INFO] Pages: {pdf.pages_count}")


if __name__ == "__main__":
    generate()
