/** パイプラインSSEイベントのペイロード */
export interface PipelineLogEvent {
  line: string;
  step: number;
  level: "info" | "success" | "warning" | "error";
}

/** パイプライン状態 */
export interface PipelineStatus {
  status: "idle" | "running" | "completed" | "error";
  current_step: number;
  last_exit_code: number | null;
}

/** 大会サマリー */
export interface TournamentSummary {
  id: number;
  name: string;
  start_date: string;
  status: string;
  num_players: number;
  num_bookmakers: number;
  has_results: boolean;
}

/** 蓄積状況 */
export interface AccumulationStatus {
  total_tournaments: number;
  with_results: number;
  with_ml_predictions: number;
  total_group_results: number;
  ml_with_results: number;
  phase2_ready: boolean;
  phase2_progress: string;
}

/** ML精度サマリー */
export interface AccuracySummary {
  total_groups: number;
  ml_correct_1st: number;
  ml_top2_correct: number;
  odds_correct_1st: number;
  ml_accuracy_1st: number;
  odds_accuracy_1st: number;
  phase2_ready: boolean;
  phase2_progress: string;
}

/** 大会別ML精度 */
export interface TournamentAccuracy {
  tournament_id: number;
  name: string;
  groups: number;
  ml_correct_1st: number;
  odds_correct_1st: number;
  model_version: string;
}

/** ML精度レポート全体 */
export interface AccuracyReport {
  tournaments: TournamentAccuracy[];
  summary: AccuracySummary | null;
}

/** EGS訓練履歴エントリ */
export interface TrainingHistoryEntry {
  model_type: string;
  trained_at: string;
  n_samples_cut: number;
  n_samples_position: number;
  features_used: string[];
  cut_roc_auc_cv: number;
  cut_brier_cv: number;
  cut_accuracy_cv: number;
  pos_mae_cv: number;
  pos_r2_cv: number;
  pos_mae_raw_cv: number;
  cut_feature_importance: Record<string, number>;
  pos_feature_importance: Record<string, number>;
}

/** EGS訓練履歴レスポンス */
export interface TrainingHistoryResponse {
  entries: TrainingHistoryEntry[];
  count: number;
}

/** タブID */
export type TabId = "pipeline" | "report" | "results" | "accumulation" | "training";
