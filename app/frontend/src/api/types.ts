export interface Interval {
  point: number;
  lo: number;
  hi: number;
}

export type MetricIntervals = Record<string, Interval>;

export interface Dataset {
  id: number;
  name: string;
  source_url: string;
  license: string;
  sha256: string;
  fetched_at: string;
  file_name: string | null;
  row_count: number;
  description: string;
  /** 'complete' | 'loading' (ingest still streaming). Absent on older backends. */
  status?: string;
  file_mtime?: string | null;
}

export interface RunMetrics {
  point_ci: MetricIntervals;
  delta_vs_baseline: MetricIntervals | null;
}

export interface Run {
  id: number;
  experiment: string;
  dataset_id: number | null;
  role: "baseline" | "ours";
  method: string;
  params: Record<string, unknown>;
  metrics: RunMetrics;
  seed: number;
  split_kind: "random" | "temporal";
  n_train: number;
  n_test: number;
  git_hash: string;
  duration_s: number;
  started_at: string;
}

export interface Info {
  version: string;
  schema_version: number;
  datasets: { name: string; sha256: string }[];
  last_run: Record<string, unknown> | null;
}

export interface RegisterResponse {
  status: string;
  detail: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface AuditVerify {
  valid: boolean;
  entries: number;
  head: string;
  broken_at: number | null;
  reason: string | null;
}

export interface AvailableFile {
  file_name: string;
  size_bytes: number;
}

export interface IngestRequest {
  file_name: string;
  name: string;
  source_url: string;
  license: string;
  description: string;
  id_column: string | null;
  time_column: string | null;
  label_column: string | null;
  group_column: string | null;
}

export interface IngestResult {
  created: boolean;
  dataset: Dataset;
}
