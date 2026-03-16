import { useState, useCallback, useRef } from "react";
import type { PipelineLogEvent } from "../types/api";

export interface UseSSEResult {
  logs: PipelineLogEvent[];
  currentStep: number;
  isRunning: boolean;
  isComplete: boolean;
  finalStatus: "success" | "error" | null;
  start: (url: string) => void;
  clearLogs: () => void;
}

/** SSEストリーミングフック - パイプライン/結果収集の両方で使用 */
export function useSSE(): UseSSEResult {
  const [logs, setLogs] = useState<PipelineLogEvent[]>([]);
  const [currentStep, setCurrentStep] = useState(0);
  const [isRunning, setIsRunning] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [finalStatus, setFinalStatus] = useState<"success" | "error" | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const completedRef = useRef(false);

  const start = useCallback((url: string) => {
    // 前回のログをクリア
    setLogs([]);
    setCurrentStep(0);
    setIsRunning(true);
    setIsComplete(false);
    setFinalStatus(null);
    completedRef.current = false;

    // 既存の接続を閉じる
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.addEventListener("log", (e: MessageEvent) => {
      try {
        const event: PipelineLogEvent = JSON.parse(e.data);
        setLogs((prev) => [...prev, event]);
        if (event.step > 0) {
          setCurrentStep(event.step);
        }
      } catch {
        // JSON parse失敗は無視
      }
    });

    es.addEventListener("complete", (e: MessageEvent) => {
      completedRef.current = true;
      const status = e.data as "success" | "error";
      setFinalStatus(status);
      setIsRunning(false);
      setIsComplete(true);
      es.close();
    });

    es.addEventListener("error", () => {
      // EventSourceはSSEストリーム終了時にもerrorを発火する
      if (!completedRef.current) {
        setFinalStatus("error");
        setIsRunning(false);
        setIsComplete(true);
      }
      es.close();
    });
  }, []);

  const clearLogs = useCallback(() => {
    setLogs([]);
    setCurrentStep(0);
    setIsComplete(false);
    setFinalStatus(null);
  }, []);

  return { logs, currentStep, isRunning, isComplete, finalStatus, start, clearLogs };
}
