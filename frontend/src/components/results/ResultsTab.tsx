import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, CheckCircle, XCircle } from "lucide-react";
import { useSSE } from "../../hooks/useSSE";
import { ConsoleLog } from "../pipeline/ConsoleLog";
import type { AccuracyReport } from "../../types/api";

export function ResultsTab() {
  const [espnDate, setEspnDate] = useState("");
  const {
    logs, isRunning, isComplete, finalStatus,
    start, clearLogs,
  } = useSSE();

  const { data: accuracy, refetch: refetchAccuracy } = useQuery<AccuracyReport>({
    queryKey: ["accuracy"],
    queryFn: () => fetch("/api/accuracy").then((r) => r.json()),
  });

  const handleCollect = () => {
    let url = "/api/results/collect";
    const params = new URLSearchParams();
    if (espnDate) params.set("espn_date", espnDate);
    if (params.toString()) url += "?" + params.toString();
    start(url);
  };

  // 収集完了後にデータを再取得
  useEffect(() => {
    if (isComplete && finalStatus === "success") {
      refetchAccuracy();
    }
  }, [isComplete, finalStatus, refetchAccuracy]);

  return (
    <div className="space-y-6">
      {/* 結果収集フォーム */}
      <div className="bg-card rounded-xl border border-border p-6 shadow-sm">
        <h2 className="text-lg font-semibold mb-4">Post-Tournament Result Collection</h2>
        <div className="flex items-end gap-4 mb-4">
          <div className="flex-1 max-w-xs">
            <label className="block text-sm font-medium text-muted-foreground mb-1">
              ESPN Date (optional)
            </label>
            <input
              type="text"
              value={espnDate}
              onChange={(e) => setEspnDate(e.target.value)}
              placeholder="YYYYMMDD"
              className="w-full px-3 py-2 rounded-lg border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <button
            onClick={handleCollect}
            disabled={isRunning}
            className={`flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm transition-colors ${
              isRunning
                ? "bg-muted text-muted-foreground cursor-not-allowed"
                : "bg-primary text-primary-foreground hover:opacity-90"
            }`}
          >
            <RefreshCw className={`w-4 h-4 ${isRunning ? "animate-spin" : ""}`} />
            {isRunning ? "Collecting..." : "Collect Results"}
          </button>
        </div>

        {/* ステータス表示 */}
        {isComplete && (
          <div className={`flex items-center gap-2 text-sm font-medium ${
            finalStatus === "success" ? "text-success" : "text-destructive"
          }`}>
            {finalStatus === "success" ? (
              <><CheckCircle className="w-4 h-4" /> Collection complete</>
            ) : (
              <><XCircle className="w-4 h-4" /> Collection failed</>
            )}
          </div>
        )}
      </div>

      {/* コンソール出力 */}
      {logs.length > 0 && (
        <div className="bg-card rounded-xl border border-border p-4 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-muted-foreground">Console Output</h3>
            <button
              onClick={clearLogs}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Clear
            </button>
          </div>
          <ConsoleLog logs={logs} />
        </div>
      )}

      {/* ML精度テーブル */}
      {accuracy?.summary && (
        <div className="bg-card rounded-xl border border-border p-6 shadow-sm">
          <h2 className="text-lg font-semibold mb-4">ML Prediction Accuracy</h2>

          {/* サマリー */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div className="bg-secondary rounded-lg p-3 text-center">
              <p className="text-2xl font-bold text-primary">
                {(accuracy.summary.ml_accuracy_1st * 100).toFixed(0)}%
              </p>
              <p className="text-xs text-muted-foreground">ML #1 Accuracy</p>
            </div>
            <div className="bg-secondary rounded-lg p-3 text-center">
              <p className="text-2xl font-bold">
                {(accuracy.summary.odds_accuracy_1st * 100).toFixed(0)}%
              </p>
              <p className="text-xs text-muted-foreground">Odds #1 Accuracy</p>
            </div>
            <div className="bg-secondary rounded-lg p-3 text-center">
              <p className="text-2xl font-bold">
                {accuracy.summary.total_groups}
              </p>
              <p className="text-xs text-muted-foreground">Total Groups</p>
            </div>
            <div className="bg-secondary rounded-lg p-3 text-center">
              <p className="text-2xl font-bold">
                {accuracy.summary.ml_top2_correct}
              </p>
              <p className="text-xs text-muted-foreground">ML Top-2 Correct</p>
            </div>
          </div>

          {/* 大会別テーブル */}
          {accuracy.tournaments.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left py-2 px-3 font-medium text-muted-foreground">Tournament</th>
                    <th className="text-center py-2 px-3 font-medium text-muted-foreground">Groups</th>
                    <th className="text-center py-2 px-3 font-medium text-muted-foreground">ML Correct</th>
                    <th className="text-center py-2 px-3 font-medium text-muted-foreground">Odds Correct</th>
                    <th className="text-center py-2 px-3 font-medium text-muted-foreground">Model</th>
                  </tr>
                </thead>
                <tbody>
                  {accuracy.tournaments.map((t) => (
                    <tr key={t.tournament_id} className="border-b border-border/50">
                      <td className="py-2 px-3">{t.name}</td>
                      <td className="py-2 px-3 text-center">{t.groups}</td>
                      <td className="py-2 px-3 text-center font-medium">
                        {t.ml_correct_1st}/{t.groups}
                      </td>
                      <td className="py-2 px-3 text-center">
                        {t.odds_correct_1st}/{t.groups}
                      </td>
                      <td className="py-2 px-3 text-center">
                        <span className="px-2 py-0.5 rounded-full bg-accent text-xs font-medium">
                          {t.model_version}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
