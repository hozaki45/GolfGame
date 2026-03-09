import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TrainingHistoryResponse } from "../../types/api";
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Brain,
  Target,
  BarChart3,
  Info,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

/** メトリクスの説明定義 */
const METRIC_DESCRIPTIONS: Record<string, { short: string; detail: string; ideal: string }> = {
  "ROC-AUC": {
    short: "カット予測の識別力",
    detail:
      "ROC曲線下の面積。モデルが「カット通過する選手」と「カット落ちする選手」をどれだけ正しく区別できるかを示します。0.5はランダム（コイン投げ）、1.0は完璧な識別。",
    ideal: "高いほど良い（0.5→ランダム、0.7+→実用的、0.8+→優秀）",
  },
  "Brier Score": {
    short: "確率予測の正確さ",
    detail:
      "予測確率と実際の結果(0/1)の二乗誤差の平均。「カット通過確率70%」と予測して実際に通過したら誤差は(1-0.7)²=0.09。予測確率が現実に近いほど低くなります。",
    ideal: "低いほど良い（0→完璧、0.25→ランダム相当）",
  },
  Accuracy: {
    short: "カット予測の正答率",
    detail:
      "「カット通過/落ち」の二択予測が正しかった割合。全選手中、モデルの予測が当たった割合です。",
    ideal: "高いほど良い（0.5→コイン投げ、0.66→現在のモデル水準）",
  },
  "MAE %": {
    short: "順位予測の平均誤差（割合）",
    detail:
      "予測順位と実際の順位の差を、フィールドサイズで割った値の平均。例：150人中30位ずれたら 30/150=0.20。フィールドサイズに依存しない正規化された誤差指標です。",
    ideal: "低いほど良い（0→完璧、0.22→現在のモデル水準）",
  },
  "R²": {
    short: "順位予測の説明力",
    detail:
      "決定係数。モデルの予測が実際の順位のばらつきをどれだけ説明できるかを示します。1.0なら完璧に予測、0なら平均値を予測するのと同程度、負なら平均より悪い。",
    ideal: "高いほど良い（0→平均並み、0.14→現在の水準、1.0→完璧）",
  },
  "MAE places": {
    short: "順位予測の平均誤差（順位数）",
    detail:
      "予測順位と実際の最終順位の差（絶対値）の平均。例：16.0なら平均して16位ずれている。最も直感的な精度指標です。",
    ideal: "低いほど良い（0→完璧、16→現在の水準、ゴルフの不確実性を考えると妥当）",
  },
};

/** 特徴量の説明定義 */
const FEATURE_DESCRIPTIONS: Record<string, string> = {
  sg_approach: "Strokes Gained: Approach — アプローチショット（グリーンを狙うショット）の平均的な選手との差",
  sg_off_tee: "Strokes Gained: Off the Tee — ティーショットの平均的な選手との差",
  sg_tee_to_green: "Strokes Gained: Tee to Green — ティーからグリーンまでの全ショットの総合力",
  gir_pct: "Greens in Regulation % — 規定打数以内にグリーンに乗せた割合",
  scrambling_pct: "Scrambling % — GIRを逃した後にパー以上を取った割合（ショートゲームの強さ）",
  scoring_average: "Scoring Average — 平均スコア（低いほど良い）",
  scoring_average_rank: "スコア平均のフィールド内順位 — その大会フィールド内での相対的な位置",
  field_size: "フィールドサイズ — 大会の出場選手数（大会規模の指標）",
  field_strength: "フィールド強度 — 出場選手のスコア平均の平均値（大会レベルの指標）",
  player_relative_strength: "選手の相対強度 — フィールド平均に対するその選手のスコア平均の差（最重要指標）",
};

export function TrainingTab() {
  const [showGuide, setShowGuide] = useState(false);
  const { data, isLoading, error } = useQuery<TrainingHistoryResponse>({
    queryKey: ["training-history"],
    queryFn: async () => {
      const res = await fetch("/api/training/history");
      if (!res.ok) throw new Error("Failed to fetch training history");
      return res.json();
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        Loading training history...
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-20 text-destructive">
        Failed to load training history: {String(error)}
      </div>
    );
  }

  if (!data || data.count === 0) {
    return (
      <div className="text-center py-20">
        <Brain className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
        <h2 className="text-lg font-semibold mb-2">No Training History</h2>
        <p className="text-muted-foreground">
          EGS model training history will appear here after the first training run.
        </p>
      </div>
    );
  }

  const entries = data.entries;
  const latest = entries[entries.length - 1];
  const prev = entries.length > 1 ? entries[entries.length - 2] : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-bold flex items-center gap-2">
            <Brain className="w-5 h-5" />
            EGS Training History
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            {entries.length} training run{entries.length > 1 ? "s" : ""} · Last
            trained: {formatDate(latest.trained_at)}
          </p>
        </div>
        <button
          onClick={() => setShowGuide((v) => !v)}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md bg-card border border-border text-muted-foreground hover:text-foreground transition-colors"
        >
          <Info className="w-3.5 h-3.5" />
          {showGuide ? "ガイドを閉じる" : "指標の見方"}
          {showGuide ? (
            <ChevronUp className="w-3 h-3" />
          ) : (
            <ChevronDown className="w-3 h-3" />
          )}
        </button>
      </div>

      {/* Guide Panel */}
      {showGuide && <GuidePanel />}

      {/* Model Overview */}
      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="font-semibold text-sm mb-2 flex items-center gap-2">
          <Brain className="w-4 h-4" />
          EGS 2-Stage Model
        </h3>
        <p className="text-xs text-muted-foreground leading-relaxed">
          EGS（Expected Game Score）モデルは2段階構成です。
          <strong> Stage 1: CutClassifier</strong> が各選手のカット通過確率を予測し、
          <strong> Stage 2: PositionRegressor</strong>
          がカット通過した選手の最終順位を予測します。
          この2つの予測を組み合わせて、各グループの期待ゲームスコアを算出します。
          毎週月曜に新しい大会結果を含めて再訓練され、モデルが継続的に改善されます。
        </p>
      </div>

      {/* Metric Cards */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="font-semibold text-sm">CutClassifier Metrics</h3>
          <span className="text-xs text-muted-foreground">
            — 選手がカットを通過するかどうかの予測精度
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <MetricCard
            label="ROC-AUC"
            value={latest.cut_roc_auc_cv}
            prev={prev?.cut_roc_auc_cv}
            format="4"
            higherBetter
            description={METRIC_DESCRIPTIONS["ROC-AUC"]}
          />
          <MetricCard
            label="Brier Score"
            value={latest.cut_brier_cv}
            prev={prev?.cut_brier_cv}
            format="4"
            higherBetter={false}
            description={METRIC_DESCRIPTIONS["Brier Score"]}
          />
          <MetricCard
            label="Accuracy"
            value={latest.cut_accuracy_cv}
            prev={prev?.cut_accuracy_cv}
            format="4"
            higherBetter
            description={METRIC_DESCRIPTIONS["Accuracy"]}
          />
        </div>
      </div>

      <div>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="font-semibold text-sm">PositionRegressor Metrics</h3>
          <span className="text-xs text-muted-foreground">
            — カット通過後の最終順位の予測精度
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <MetricCard
            label="MAE %"
            value={latest.pos_mae_cv}
            prev={prev?.pos_mae_cv}
            format="4"
            higherBetter={false}
            description={METRIC_DESCRIPTIONS["MAE %"]}
          />
          <MetricCard
            label="R²"
            value={latest.pos_r2_cv}
            prev={prev?.pos_r2_cv}
            format="4"
            higherBetter
            description={METRIC_DESCRIPTIONS["R²"]}
          />
          <MetricCard
            label="MAE places"
            value={latest.pos_mae_raw_cv}
            prev={prev?.pos_mae_raw_cv}
            format="1"
            higherBetter={false}
            description={METRIC_DESCRIPTIONS["MAE places"]}
          />
        </div>
      </div>

      {/* Feature Importance */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="font-semibold text-sm">Feature Importance（特徴量重要度）</h3>
          <span className="text-xs text-muted-foreground">
            — モデルが予測に各特徴量をどれだけ使っているか
          </span>
        </div>
        <p className="text-xs text-muted-foreground mb-3 leading-relaxed">
          バーが長いほど、その特徴量がモデルの予測に大きく影響しています。
          Permutation Importance（その特徴量をシャッフルした時にどれだけ精度が落ちるか）で計測。
          値が高い特徴量を改善すればモデル全体の精度向上につながります。
        </p>
        <div className="grid md:grid-cols-2 gap-4">
          <FeatureImportanceCard
            title="CutClassifier"
            subtitle="カット通過/落ちの予測に影響する特徴量"
            icon={<Target className="w-4 h-4" />}
            importance={latest.cut_feature_importance}
          />
          <FeatureImportanceCard
            title="PositionRegressor"
            subtitle="最終順位の予測に影響する特徴量"
            icon={<BarChart3 className="w-4 h-4" />}
            importance={latest.pos_feature_importance}
          />
        </div>
      </div>

      {/* History Table */}
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="font-semibold text-sm">All Training Runs</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            毎回の訓練結果。データが増えるにつれてメトリクスの推移が確認できます。
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground text-xs uppercase">
                <th className="px-4 py-2 text-left">#</th>
                <th className="px-4 py-2 text-left">Date</th>
                <th className="px-4 py-2 text-right">ROC-AUC</th>
                <th className="px-4 py-2 text-right">Brier</th>
                <th className="px-4 py-2 text-right">Accuracy</th>
                <th className="px-4 py-2 text-right">MAE%</th>
                <th className="px-4 py-2 text-right">R²</th>
                <th className="px-4 py-2 text-right">MAE places</th>
                <th className="px-4 py-2 text-right">Samples</th>
              </tr>
            </thead>
            <tbody>
              {[...entries].reverse().map((entry, i) => (
                <tr
                  key={i}
                  className={`border-b border-border/50 ${i === 0 ? "bg-primary/5" : ""}`}
                >
                  <td className="px-4 py-2 font-medium">{entries.length - i}</td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {formatDate(entry.trained_at)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {entry.cut_roc_auc_cv.toFixed(4)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {entry.cut_brier_cv.toFixed(4)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {entry.cut_accuracy_cv.toFixed(4)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {entry.pos_mae_cv.toFixed(4)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {entry.pos_r2_cv.toFixed(4)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono">
                    {entry.pos_mae_raw_cv.toFixed(1)}
                  </td>
                  <td className="px-4 py-2 text-right text-muted-foreground">
                    {entry.n_samples_cut.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** 指標の見方ガイドパネル */
function GuidePanel() {
  return (
    <div className="bg-card border border-border rounded-lg p-5 space-y-4">
      <h3 className="font-bold text-sm flex items-center gap-2">
        <Info className="w-4 h-4 text-blue-400" />
        指標の見方ガイド
      </h3>

      <div className="space-y-3">
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            CutClassifier（カット予測）のメトリクス
          </h4>
          <div className="space-y-2">
            {(["ROC-AUC", "Brier Score", "Accuracy"] as const).map((key) => (
              <MetricGuideRow key={key} name={key} info={METRIC_DESCRIPTIONS[key]} />
            ))}
          </div>
        </div>

        <div className="border-t border-border pt-3">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            PositionRegressor（順位予測）のメトリクス
          </h4>
          <div className="space-y-2">
            {(["MAE %", "R²", "MAE places"] as const).map((key) => (
              <MetricGuideRow key={key} name={key} info={METRIC_DESCRIPTIONS[key]} />
            ))}
          </div>
        </div>

        <div className="border-t border-border pt-3">
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            特徴量（Features）
          </h4>
          <div className="grid gap-1.5">
            {Object.entries(FEATURE_DESCRIPTIONS).map(([feat, desc]) => (
              <div key={feat} className="text-xs leading-relaxed">
                <span className="font-mono font-medium text-foreground">{feat}</span>
                <span className="text-muted-foreground"> — {desc}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricGuideRow({
  name,
  info,
}: {
  name: string;
  info: { short: string; detail: string; ideal: string };
}) {
  return (
    <div className="text-xs leading-relaxed">
      <span className="font-semibold text-foreground">{name}</span>
      <span className="text-muted-foreground"> — {info.short}</span>
      <p className="text-muted-foreground mt-0.5 pl-3">{info.detail}</p>
      <p className="text-green-500/80 mt-0.5 pl-3">{info.ideal}</p>
    </div>
  );
}

function MetricCard({
  label,
  value,
  prev,
  format,
  higherBetter,
  description,
}: {
  label: string;
  value: number;
  prev?: number;
  format: "4" | "1";
  higherBetter: boolean;
  description: { short: string; detail: string; ideal: string };
}) {
  const formatted = format === "4" ? value.toFixed(4) : value.toFixed(1);
  let trend: "up" | "down" | "same" = "same";
  let trendValue = "";

  if (prev !== undefined) {
    const diff = value - prev;
    if (Math.abs(diff) >= 0.0001) {
      trend = diff > 0 ? "up" : "down";
      trendValue = Math.abs(diff).toFixed(4);
    }
  }

  const isGood = trend === "same" ? null : (trend === "up") === higherBetter;

  return (
    <div className="bg-card border border-border rounded-lg p-3">
      <div className="text-xs text-muted-foreground uppercase tracking-wide">
        {label}
      </div>
      <div className="text-xl font-bold mt-1 font-mono">{formatted}</div>
      <div className="text-[11px] text-muted-foreground mt-1 leading-snug">
        {description.short}
      </div>
      {prev !== undefined && (
        <div className="flex items-center gap-1 mt-1.5 text-xs">
          {trend === "up" && (
            <TrendingUp
              className={`w-3 h-3 ${isGood ? "text-green-500" : "text-red-500"}`}
            />
          )}
          {trend === "down" && (
            <TrendingDown
              className={`w-3 h-3 ${isGood ? "text-green-500" : "text-red-500"}`}
            />
          )}
          {trend === "same" && <Minus className="w-3 h-3 text-muted-foreground" />}
          <span
            className={
              trend === "same"
                ? "text-muted-foreground"
                : isGood
                ? "text-green-500"
                : "text-red-500"
            }
          >
            {trend === "same" ? "No change" : trendValue}
          </span>
        </div>
      )}
    </div>
  );
}

function FeatureImportanceCard({
  title,
  subtitle,
  icon,
  importance,
}: {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  importance: Record<string, number>;
}) {
  const sorted = Object.entries(importance).sort(([, a], [, b]) => b - a);
  const max = sorted.length > 0 ? sorted[0][1] : 1;

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          {icon}
          <h3 className="font-semibold text-sm">{title}</h3>
        </div>
        <p className="text-[11px] text-muted-foreground mt-0.5">{subtitle}</p>
      </div>
      <div className="p-4 space-y-2">
        {sorted.map(([feat, val]) => {
          const desc = FEATURE_DESCRIPTIONS[feat];
          return (
            <div key={feat} className="group">
              <div className="flex items-center gap-3 text-sm">
                <span className="w-44 truncate text-muted-foreground font-mono text-xs">
                  {feat}
                </span>
                <div className="flex-1 h-4 bg-muted rounded overflow-hidden">
                  <div
                    className={`h-full rounded ${
                      title.includes("Cut") ? "bg-green-500/60" : "bg-blue-500/60"
                    }`}
                    style={{ width: `${(val / max) * 100}%` }}
                  />
                </div>
                <span className="w-14 text-right font-mono text-xs">
                  {val.toFixed(4)}
                </span>
              </div>
              {desc && (
                <div className="hidden group-hover:block text-[11px] text-muted-foreground pl-2 mt-0.5 mb-1">
                  {desc}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatDate(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}
