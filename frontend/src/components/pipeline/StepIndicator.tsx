import { CheckCircle, Circle, Loader2, XCircle } from "lucide-react";

const STEP_LABELS = [
  "CSV DL",
  "Analysis",
  "HTML",
  "DB Save",
  "Stats",
  "Re-analyze",
  "HTML+Stats",
  "Course Fit",
  "ML Predict",
];

interface StepIndicatorProps {
  currentStep: number;
  isRunning: boolean;
  isComplete: boolean;
  finalStatus: "success" | "error" | null;
}

export function StepIndicator({
  currentStep,
  isRunning,
  isComplete,
  finalStatus,
}: StepIndicatorProps) {
  return (
    <div className="flex items-center gap-1 overflow-x-auto pb-2">
      {STEP_LABELS.map((label, i) => {
        const stepNum = i + 1;
        let status: "done" | "active" | "pending" | "error" = "pending";

        if (isComplete && finalStatus === "error" && stepNum === currentStep) {
          status = "error";
        } else if (stepNum < currentStep) {
          status = "done";
        } else if (stepNum === currentStep && isRunning) {
          status = "active";
        } else if (stepNum === currentStep && isComplete) {
          status = finalStatus === "success" ? "done" : "error";
        }

        return (
          <div key={stepNum} className="flex flex-col items-center gap-1 min-w-[60px]">
            <div className="relative">
              {status === "done" && (
                <CheckCircle className="w-8 h-8 text-success" />
              )}
              {status === "active" && (
                <Loader2 className="w-8 h-8 text-primary animate-spin" />
              )}
              {status === "error" && (
                <XCircle className="w-8 h-8 text-destructive" />
              )}
              {status === "pending" && (
                <Circle className="w-8 h-8 text-border" />
              )}
            </div>
            <span className={`text-[10px] leading-tight text-center ${
              status === "done" ? "text-success font-medium" :
              status === "active" ? "text-primary font-medium" :
              status === "error" ? "text-destructive font-medium" :
              "text-muted-foreground"
            }`}>
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
