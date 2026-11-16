// TypeScript mirrors of the FastAPI Pydantic response/request schemas.

export interface Chunk {
  document_id: string;
  filename: string;
  page: number;
  chunk_index: number;
  token_count: number;
  text: string;
}

export interface DocumentRecord {
  id: string;
  original_filename: string;
  file_type: string;
  size_bytes: number;
  chunk_count: number | null;
  status: "pending" | "processing" | "completed" | "failed" | string;
  error_message: string | null;
  embedding_model: string | null;
  chunk_size_tokens: number | null;
  chunk_overlap_tokens: number | null;
  content_hash: string | null;
  created_at: string;
}

export interface UploadDocumentResponse extends DocumentRecord {
  chunks: Chunk[];
}

export interface DocumentContentResponse {
  document_id: string;
  filename: string;
  file_type: string;
  page_count: number;
  text: string;
}

export interface RetrievalResult {
  source_id: string;
  document_id: string;
  filename: string;
  page: number;
  chunk_index: number;
  text: string;
  score: number;
}

export interface RetrievalResponse {
  question: string;
  top_k: number;
  document_id: string | null;
  elapsed_ms: number;
  results: RetrievalResult[];
}

export interface Source {
  source_id: string;
  document_id: string;
  filename: string;
  page: number;
  chunk_index: number;
  excerpt: string;
}

export interface ChatResponse {
  question: string;
  answer: string;
  status: "answered" | "insufficient_evidence";
  sources: Source[];
  model: string;
  retrieval_ms: number;
  generation_ms: number;
  conversation_id: string | null;
  // Assistant message row when the exchange was persisted (null for
  // stateless requests) - the handle for per-answer feedback.
  message_id?: string | null;
}

// Thumbs feedback on an assistant answer: null = not rated / cleared.
export type FeedbackRating = "up" | "down" | null;

export interface FeedbackResponse {
  message_id: string;
  feedback: FeedbackRating;
}

// /chat/stream SSE event payloads.
export interface StreamMeta {
  model: string;
  retrieval_ms: number;
  rewritten: boolean;
  has_evidence: boolean;
}

export interface StreamDelta {
  text: string;
}

export interface StreamError {
  message: string;
}

export interface ReadyResponse {
  status: "ok" | "unavailable";
  service: string;
  dependencies: Record<string, string>;
}

export interface ChatRequest {
  question: string;
  top_k?: number;
  document_id?: string;
  conversation_id?: string | null;
}

export interface ConversationSummary {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface MessageRecord {
  id: string;
  role: string;
  content: string;
  status: string | null;
  feedback: FeedbackRating;
  sources: Source[];
  model: string | null;
  retrieval_ms: number | null;
  generation_ms: number | null;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: MessageRecord[];
}

export interface RetrievalRequest {
  question: string;
  top_k?: number;
  document_id?: string;
}

// -------------------------------------------------------------- identity

export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
  role: "admin" | "user";
  created_at: string;
  last_login_at: string | null;
}

export interface AuthConfigResponse {
  auth_required: boolean;
  registration_enabled: boolean;
}

// ----------------------------------------------------------------- admin

export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  role: "admin" | "user";
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  // Per-user budget overrides (USD); null = follow the global settings.
  daily_budget_usd: number | null;
  monthly_budget_usd: number | null;
}

export interface AdminOverview {
  version: string;
  git_commit: string | null;
  documents: number;
  chunks: number | null;
  conversations: number;
  messages: number;
  users: number;
  active_admins: number;
  chat_cache_enabled: boolean;
}

export interface UsageDay {
  day: string | null;
  answers: number;
  // Ledger columns (usage_events); zero when the day had no spend rows.
  requests: number;
  spend_usd: number;
  tokens_in: number;
  tokens_out: number;
}

// Lifetime counters: transcript answers/feedback + ledger spend.
export interface UsageTotals {
  answers: number;
  feedback_up: number;
  feedback_down: number;
  requests: number;
  spend_usd: number;
  tokens_in: number;
  tokens_out: number;
}

// Spend grouped by account or model over the window (admin rollups).
export interface UsageRollup {
  key: string; // email or model name; "unattributed" for NULL ledger rows
  requests: number;
  spend_usd: number;
  tokens_in: number;
  tokens_out: number;
}

export interface AdminUsage {
  days: number;
  daily: UsageDay[];
  totals: UsageTotals;
  by_user: UsageRollup[];
  by_model: UsageRollup[];
  spend_usd: number; // window spend (sum of daily)
}

// GET /usage/me — one account's own spend + effective budget state.
export interface MyUsage {
  days: number;
  daily: UsageDay[];
  totals: UsageTotals;
  spend_usd: number;
  daily_budget_usd: number | null; // effective limit (override or global)
  monthly_budget_usd: number | null;
  daily_spend_usd: number;
  monthly_spend_usd: number;
}

export interface FeedbackItem {
  message_id: string;
  conversation_id: string;
  feedback: "up" | "down";
  status: string | null;
  model: string | null;
  created_at: string;
  answer: string;
}

export interface AuditEvent {
  id: string;
  actor_label: string;
  actor_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  request_id: string | null;
  ip: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}
