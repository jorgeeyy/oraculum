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
