import { Play, Square, Trash2, CheckCircle, XCircle } from "lucide-react";
import { useSSE } from "../../hooks/useSSE";
import { StepIndicator } from "./StepIndicator";
import { ConsoleLog } from "./ConsoleLog";

export function PipelineTab() {
  const {
    logs, currentStep, isRunning, isComplete, finalStatus,
    start, clearLogs,
  } = useSSE();

  const handleRun = () => {
    start("/api/pipeline/start");
  };

  return (
    <div className="space-y-6">
      {/* ステータスカード */}
      <div className="bg-card rounded-xl border border-border p-6 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Pipeline Execution</h2>
          <div className="flex items-center gap-2">
            {isComplete && finalStatus === "success" && (
              <span className="flex items-center gap-1 text-sm text-success font-medium">
                <CheckCircle className="w-4 h-4" /> Complete
              </span>
            )}
            {isComplete && finalStatus === "error" && (
              <span className="flex items-center gap-1 text-sm text-destructive font-medium">
                <XCircle className="w-4 h-4" /> Error
              </span>
            )}
            {isRunning && (
              <span className="text-sm text-primary font-medium animate-pulse">
                Running...
              </span>
            )}
          </div>
        </div>

        {/* ステップ進捗 */}
        <StepIndicator
          currentStep={currentStep}
          isRunning={isRunning}
          isComplete={isComplete}
          finalStatus={finalStatus}
        />
      </div>

      {/* アクションボタン */}
      <div className="flex gap-3">
        <button
          onClick={handleRun}
          disabled={isRunning}
          className={`flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm transition-colors ${
            isRunning
              ? "bg-muted text-muted-foreground cursor-not-allowed"
              : "bg-primary text-primary-foreground hover:opacity-90"
          }`}
        >
          {isRunning ? (
            <>
              <Square className="w-4 h-4" /> Running...
            </>
          ) : (
            <>
              <Play className="w-4 h-4" /> Run Pipeline
            </>
          )}
        </button>
        <button
          onClick={clearLogs}
          disabled={isRunning}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium text-sm border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-50"
        >
          <Trash2 className="w-4 h-4" /> Clear
        </button>
      </div>

      {/* コンソールログ */}
      <div className="bg-card rounded-xl border border-border p-4 shadow-sm">
        <h3 className="text-sm font-medium text-muted-foreground mb-3">Console Output</h3>
        <ConsoleLog logs={logs} />
      </div>
    </div>
  );
}
