import { useEffect, useRef } from "react";
import type { PipelineLogEvent } from "../../types/api";

interface ConsoleLogProps {
  logs: PipelineLogEvent[];
}

function getLineColor(level: string): string {
  switch (level) {
    case "success": return "text-green-400";
    case "error": return "text-red-400";
    case "warning": return "text-yellow-400";
    default: return "text-[var(--console-fg)]";
  }
}

function isStepLine(line: string): boolean {
  return /\[STEP \d+\]/.test(line);
}

export function ConsoleLog({ logs }: ConsoleLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  return (
    <div
      className="bg-[var(--console-bg)] rounded-lg p-4 h-[400px] overflow-y-auto font-mono text-sm"
      style={{ colorScheme: "dark" }}
    >
      {logs.length === 0 && (
        <p className="text-gray-500 italic">
          Click "Run Pipeline" to start...
        </p>
      )}
      {logs.map((log, i) => (
        <div
          key={i}
          className={`${getLineColor(log.level)} ${
            isStepLine(log.line) ? "font-bold text-white mt-2" : ""
          } leading-relaxed`}
        >
          {log.line}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
