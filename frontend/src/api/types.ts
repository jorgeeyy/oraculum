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
}

export interface AdminUsage {
  days: number;
  daily: UsageDay[];
  totals: {
    answers: number;
    feedback_up: number;
    feedback_down: number;
  };
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
