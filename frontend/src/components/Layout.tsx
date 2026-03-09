import { useState, type ReactNode } from "react";
import type { TabId } from "../types/api";
import { PipelineTab } from "./pipeline/PipelineTab";
import { ReportTab } from "./report/ReportTab";
import { ResultsTab } from "./results/ResultsTab";
import { AccumulationTab } from "./accumulation/AccumulationTab";
import { TrainingTab } from "./training/TrainingTab";
import {
  Play,
  BarChart3,
  RefreshCw,
  Database,
  Brain,
} from "lucide-react";

const TABS: { id: TabId; label: string; icon: ReactNode }[] = [
  { id: "pipeline", label: "Pipeline", icon: <Play className="w-4 h-4" /> },
  { id: "report", label: "Report", icon: <BarChart3 className="w-4 h-4" /> },
  { id: "results", label: "Results", icon: <RefreshCw className="w-4 h-4" /> },
  { id: "accumulation", label: "Accumulation", icon: <Database className="w-4 h-4" /> },
  { id: "training", label: "Training", icon: <Brain className="w-4 h-4" /> },
];

export function Layout() {
  const [activeTab, setActiveTab] = useState<TabId>("pipeline");

  return (
    <div className="min-h-screen bg-background">
      {/* ヘッダー */}
      <header className="bg-primary text-primary-foreground shadow-lg">
        <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">⛳</span>
            <h1 className="text-xl font-bold tracking-tight">GolfGame Dashboard</h1>
          </div>
          <span className="text-sm opacity-75">PGA Tour Betting Analysis</span>
        </div>
      </header>

      {/* タブナビゲーション */}
      <nav className="bg-card border-b border-border shadow-sm">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex gap-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
                  activeTab === tab.id
                    ? "border-primary text-primary"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
                }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </nav>

      {/* タブコンテンツ */}
      <main className="max-w-7xl mx-auto px-4 py-6">
        {activeTab === "pipeline" && <PipelineTab />}
        {activeTab === "report" && <ReportTab />}
        {activeTab === "results" && <ResultsTab />}
        {activeTab === "accumulation" && <AccumulationTab />}
        {activeTab === "training" && <TrainingTab />}
      </main>
    </div>
  );
}
