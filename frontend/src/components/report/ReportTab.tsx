import { ExternalLink } from "lucide-react";

export function ReportTab() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Analysis Report</h2>
        <a
          href="/api/report"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
        >
          <ExternalLink className="w-4 h-4" /> Open in New Tab
        </a>
      </div>
      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
        <iframe
          src="/api/report"
          title="GolfGame Analysis Report"
          className="w-full border-0"
          style={{ height: "calc(100vh - 220px)" }}
        />
      </div>
    </div>
  );
}
