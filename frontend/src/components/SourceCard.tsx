import type { Source } from "../api/types";

export default function SourceCard({ source }: { source: Source }) {
  return (
    <details className="source-card">
      <summary>
        {source.filename} · p.{source.page} · chunk #{source.chunk_index}
      </summary>
      <pre>{source.excerpt}</pre>
    </details>
  );
}
