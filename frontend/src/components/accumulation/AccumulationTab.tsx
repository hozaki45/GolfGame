import { useQuery } from "@tanstack/react-query";
import { Database, Trophy, BarChart3, CheckCircle, Clock } from "lucide-react";
import type { AccumulationStatus, TournamentSummary } from "../../types/api";

export function AccumulationTab() {
  const { data: status, isLoading: statusLoading } = useQuery<AccumulationStatus>({
    queryKey: ["accumulation"],
    queryFn: () => fetch("/api/accumulation").then((r) => r.json()),
  });

  const { data: tournaments, isLoading: tournamentsLoading } = useQuery<TournamentSummary[]>({
    queryKey: ["tournaments"],
    queryFn: () => fetch("/api/tournaments").then((r) => r.json()),
  });

  const progressPercent = status
    ? Math.min((status.ml_with_results / 50) * 100, 100)
    : 0;

  return (
    <div className="space-y-6">
      {/* Phase 2 プログレス */}
      <div className="bg-card rounded-xl border border-border p-6 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <Trophy className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-semibold">Phase 2 ML Model Progress</h2>
        </div>

        {statusLoading ? (
          <div className="h-20 flex items-center justify-center text-muted-foreground">
            Loading...
          </div>
        ) : status ? (
          <>
            {/* プログレスバー */}
            <div className="mb-3">
              <div className="flex justify-between text-sm mb-1">
                <span className="text-muted-foreground">Group results with ML predictions</span>
                <span className="font-medium">{status.phase2_progress}</span>
              </div>
              <div className="w-full bg-secondary rounded-full h-4 overflow-hidden">
                <div
                  className="bg-primary h-full rounded-full transition-all duration-500"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>

            {status.phase2_ready ? (
              <p className="text-sm text-success font-medium">
                Phase 2 is ready! Enough data has been accumulated for full ML training.
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                Need {Math.max(0, 50 - status.ml_with_results)} more group results.
                Estimated ~{Math.max(1, Math.ceil((50 - status.ml_with_results) / 10))} more tournaments.
              </p>
            )}
          </>
        ) : null}
      </div>

      {/* 蓄積状況カード */}
      {status && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          {[
            { label: "Total Tournaments", value: status.total_tournaments, icon: Database },
            { label: "With Results", value: status.with_results, icon: CheckCircle },
            { label: "With ML Predictions", value: status.with_ml_predictions, icon: BarChart3 },
            { label: "Group Results", value: status.total_group_results, icon: Trophy },
            { label: "ML + Results", value: status.ml_with_results, icon: Trophy },
          ].map((item) => (
            <div key={item.label} className="bg-card rounded-xl border border-border p-4 shadow-sm text-center">
              <item.icon className="w-5 h-5 text-primary mx-auto mb-2" />
              <p className="text-2xl font-bold">{item.value}</p>
              <p className="text-xs text-muted-foreground mt-1">{item.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* 大会履歴テーブル */}
      <div className="bg-card rounded-xl border border-border p-6 shadow-sm">
        <h2 className="text-lg font-semibold mb-4">Tournament History</h2>

        {tournamentsLoading ? (
          <p className="text-muted-foreground">Loading...</p>
        ) : tournaments && tournaments.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">ID</th>
                  <th className="text-left py-2 px-3 font-medium text-muted-foreground">Tournament</th>
                  <th className="text-center py-2 px-3 font-medium text-muted-foreground">Status</th>
                  <th className="text-center py-2 px-3 font-medium text-muted-foreground">Players</th>
                  <th className="text-center py-2 px-3 font-medium text-muted-foreground">Bookmakers</th>
                  <th className="text-center py-2 px-3 font-medium text-muted-foreground">Results</th>
                </tr>
              </thead>
              <tbody>
                {tournaments.map((t) => (
                  <tr key={t.id} className="border-b border-border/50">
                    <td className="py-2 px-3 text-muted-foreground">{t.id}</td>
                    <td className="py-2 px-3 font-medium">{t.name}</td>
                    <td className="py-2 px-3 text-center">
                      <StatusBadge status={t.status} />
                    </td>
                    <td className="py-2 px-3 text-center">{t.num_players}</td>
                    <td className="py-2 px-3 text-center">{t.num_bookmakers}</td>
                    <td className="py-2 px-3 text-center">
                      {t.has_results ? (
                        <CheckCircle className="w-4 h-4 text-success mx-auto" />
                      ) : (
                        <Clock className="w-4 h-4 text-muted-foreground mx-auto" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            No tournaments yet. Run the pipeline to save the first tournament.
          </p>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    odds_saved: "bg-blue-100 text-blue-700",
    results_saved: "bg-green-100 text-green-700",
    scheduled: "bg-gray-100 text-gray-700",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${styles[status] || styles.scheduled}`}>
      {status.replace("_", " ")}
    </span>
  );
}
