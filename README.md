# GolfGame - Golf Betting Analysis

ゴルフ賭けの分析ツール：PGA Tour 大会のブックメーカーオッズを収集し、グループごとに最良のオッズを比較。試合結果と照合してブックメーカーの予想精度を評価します。

## 機能

- **ピックCSVダウンロード**: jflynn87.pythonanywhere.com からグループ分けされた選手リストを取得
- **ブックメーカーオッズ取得**: Vegas Insider (6社) + The Odds API (14社) から最新オッズを収集
- **グループ分析**: グループごとに最良オッズの選手を抽出し、ブックメーカー間で比較
- **HTMLレポート生成**: ブラウザで閲覧可能な分析レポート（タブナビゲーション、オッズ色分け）
- **データベース蓄積**: SQLite でオッズと結果を永続化
- **予想精度スコアリング**: 各ブックメーカーの予想が実際の結果とどれだけ合致したかを評価

## クイックスタート

### 1. セットアップ

```bash
# 依存関係インストール
uv sync

# 設定ファイル作成
cp config.yaml.example config.yaml

# config.yaml を編集:
# - picks_site: ユーザー名・パスワード
# - theodds_api: API キー (https://the-odds-api.com で取得)
```

### 2. パイプライン実行（大会開始前）

```bash
uv run python run_pipeline.py
```

これにより:
1. jflynn87 からピック CSV ダウンロード
2. Vegas Insider + The Odds API からオッズ取得
3. グループ分析レポート生成
4. HTML レポート生成 → `data/output/index.html`
5. **データベースに保存** → `data/golfgame.db`

### 3. 試合結果保存（大会終了後）

```bash
# 最新の大会の結果を保存
uv run python -m src.database results YYYYMMDD

# 例: 2026年2月12日の大会結果
uv run python -m src.database results 20260212
```

### 4. 履歴スコアリング

```bash
# データベース内の全大会を対象にブックメーカーの精度を評価
uv run python -m src.database scores
```

出力例:
```
  HISTORICAL BOOKMAKER ACCURACY (All Tournaments)

  Rank  Bookmaker       Total Match    Avg Match    Total Top    Avg Top
------  ------------  -------------  -----------  -----------  ---------
     1  BetMGM                   73           73           31         31
     2  Caesars                  70           70           25         25
```

- **Match Points**: 順位予想が当たった合計点（高い方が良い）
- **Top Pick Score**: 1位予想した選手の実際の順位合計（低い方が良い）

## データベースコマンド

```bash
# 大会一覧表示
uv run python -m src.database list

# 試合結果保存
uv run python -m src.database results [YYYYMMDD] [tournament_id]

# 履歴スコアリング
uv run python -m src.database scores
```

## データフロー

```
大会開始前:
  run_pipeline.py
    → CSVダウンロード
    → オッズ取得 (Vegas Insider + The Odds API)
    → HTMLレポート生成
    → データベースに保存 (status='odds_saved')

大会終了後:
  src.database results YYYYMMDD
    → ESPN APIから結果取得
    → データベースに結果保存 (status='results_saved')
    → グループ内順位を計算

履歴分析:
  src.database scores
    → 全大会の予想と結果を比較
    → ブックメーカー別の精度ランキング
```

## ファイル構成

```
C:\Users\hozak\GolfGame\
├── src/
│   ├── espn_scraper.py         # ESPN API (試合結果取得)
│   ├── odds_scraper.py         # Vegas Insider スクレイパー
│   ├── theodds_scraper.py      # The Odds API クライアント
│   ├── picks_downloader.py     # jflynn87 CSV ダウンロード
│   ├── group_analyzer.py       # グループ分析
│   ├── html_report.py          # HTML レポート生成
│   ├── result_scorer.py        # 単一大会のスコアリング
│   └── database.py             # SQLite データベース管理
├── data/
│   ├── picks/                  # ダウンロードした CSV
│   ├── raw/                    # 生データ（JSON）
│   ├── output/                 # HTML レポート + テキストレポート
│   └── golfgame.db             # SQLite データベース
├── run_pipeline.py             # メインパイプライン
├── config.yaml                 # 設定ファイル（要作成）
└── .github/workflows/
    └── analyze.yml             # GitHub Actions（GitHub Pages自動デプロイ）
```

## GitHub Pages 自動公開

1. GitHub リポジトリ設定:
   - Settings → Pages → Source: "GitHub Actions"
   - Settings → Secrets: `PICKS_USERNAME`, `PICKS_PASSWORD`, `THEODDS_API_KEY` を追加

2. Actions タブ → "Golf Betting Analysis" → "Run workflow" をクリック

3. デプロイ完了後、`https://あなたのユーザー名.github.io/GolfGame/` で閲覧可能

## ブックメーカー

### Vegas Insider（常時取得可能）
- Bet365, BetMGM, DraftKings, Caesars, FanDuel, RiversCasino

### The Odds API（メジャー大会のみ）
- 上記6社 + Unibet, theScore Bet, BetRivers, SportsBet, Bovada, Everygame, TAB, Betfair

**注意**: The Odds API は週次 PGA Tour イベントに対応していないため、Genesis Invitational のような通常大会では Vegas Insider のみのデータになります。マスターズ等のメジャー大会では両方のデータが統合されます。

## スコアリング方式

### Position Match Points（一致ポイント）
各グループの各順位で、ブックメーカーの予想（オッズの低い順）と実際の結果が一致したら、その順位番号がポイントになります。

例: Group 1 で 3位を当てたら +3点、5位を当てたら +5点

### Top Pick Score（トップピック得点）
各グループで最もオッズの低い選手（1位予想）が、実際にそのグループ内で何位だったかの合計。

例: 10グループすべてで1位予想が的中 → 10点（完璧）、すべて外れて5位だった → 50点（不正確）

## ライセンス

このプロジェクトは個人利用を目的としています。
