# BALLDONTLIE Free Tier - 利用可能データ分析

## 検証日: 2026-02-22

## ✅ 利用可能なエンドポイント

### 1. `/pga/v1/players` ⭐⭐⭐⭐⭐
**最重要エンドポイント**

**取得可能データ:**
```json
{
  "id": 185,
  "display_name": "Scottie Scheffler",
  "owgr": 1,  ← 世界ランキング（最重要！）
  "country": "United States",
  "country_code": "USA",
  "height": "6'3\"",
  "weight": "200",
  "birth_date": "Jun 21, 1996",
  "turned_pro": "2018",
  "school": "University of Texas",
  "residence_city": "Dallas",
  "active": true
}
```

**効率:**
- 1リクエストで100選手取得可能（`per_page=100`）
- 72選手 → **1リクエストのみ**
- 5 req/min制限でも十分

**予測への活用:**
- **OWGR** → 選手の総合力を示す公式ランキング
- Lower rank = Stronger player
- Scottie Scheffler (OWGR: 1) > Rory McIlroy (OWGR: 2)

---

### 2. `/pga/v1/tournaments` ⭐⭐⭐
**トーナメント履歴データ**

**取得可能データ:**
```json
{
  "id": 1,
  "season": 2024,
  "name": "The Sentry",
  "status": "COMPLETED",
  "purse": "$20,000,000",
  "champion": {
    "id": 104,
    "display_name": "Chris Kirk",
    "owgr": 78
  },
  "courses": [{
    "course": {
      "name": "Plantation Course at Kapalua",
      "par": 73,
      "yardage": "7,596",
      "architect": "Bill Coore / Ben Crenshaw"
    }
  }]
}
```

**予測への活用:**
- 優勝履歴（champion データ）
- コース情報（par, yardage）→ コース難易度
- 賞金額（purse）→ トーナメントの重要度

**フィルタリング:**
- `?season=2024` ✅
- `?status=COMPLETED` ✅
- `?status=IN_PROGRESS` ✅

---

## ❌ 利用不可エンドポイント

### 1. `/pga/v1/season_averages` ❌
**エラー:** 401 Unauthorized
**理由:** 有料プラン（GOAT $39.99/月以上）が必要
**含まれるデータ:** Strokes Gained, GIR%, driving stats など

### 2. その他のエンドポイント ❌
**エラー:** 404 Route not found
- `/events`
- `/rounds`
- `/leaderboards`
- `/scores`

---

## 🎯 Free Tier 最適化戦略

### データ取得効率化

**従来（有料プラン想定）:**
```
72選手 × 2リクエスト/選手 = 144リクエスト
144 ÷ 5 req/min = 約29分
```

**最適化後（Free tier）:**
```
1リクエスト = 100選手取得
72選手 → 1リクエスト = 約3秒
```

**削減率:** 99.8% (29分 → 3秒) ⚡

### 予測モデル戦略

**使用データ:**
1. **OWGR** (プライマリ) - 選手の総合力
2. Tournament history (オプション) - 優勝履歴
3. Course data (オプション) - コース相性

**予測計算:**
```python
# OWGRベーススコア (0-100)
base_score = 100 - normalize(owgr, min=1, max=200)

# 例:
# Scottie Scheffler (OWGR: 1) → 100点
# Rory McIlroy (OWGR: 2) → 99.5点
# OWGR 100位 → 50点
# OWGR 200位 → 0点
```

**信頼度判定:**
- OWGR あり → High
- OWGR なし → Low

---

## 💰 コスト比較

| データソース | 月額コスト | データ品質 | Free tier制限 |
|-------------|----------|-----------|--------------|
| **BALLDONTLIE Free** | $0 | ⭐⭐⭐ (OWGR) | 5 req/min |
| BALLDONTLIE GOAT | $39.99 | ⭐⭐⭐⭐⭐ (全統計) | 無制限 |
| Data Golf API | $10-50 | ⭐⭐⭐⭐⭐ (プロ向け) | プランによる |

**結論:** Free tier でも OWGR ベースの実用的な予測が可能！

---

## 📝 実装メモ

### 実装済み
- ✅ Bulk player fetch (`_fetch_all_players_bulk()`)
- ✅ OWGR extraction and storage
- ✅ Fuzzy player name matching

### 今後の拡張候補
- [ ] Tournament history integration (優勝履歴ボーナス)
- [ ] Course difficulty factor (par, yardage based)
- [ ] Recent tournament performance tracking

---

## 🎉 結論

BALLDONTLIE Free tier は **実用十分**：
- ✅ 高速（1リクエスト）
- ✅ 信頼性の高いデータ（OWGR公式ランキング）
- ✅ コスト $0
- ✅ 既存システムに統合可能

**推奨:** Free tier で運用開始、必要に応じて有料プラン検討
