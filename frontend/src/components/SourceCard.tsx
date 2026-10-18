import { ChevronDown } from "lucide-react";
import type { Source } from "../api/types";

interface Props {
  source: Source;
  index?: number;
}

export default function SourceCard({ source, index }: Props) {
  return (
    <details className="group overflow-hidden rounded-xl border border-line bg-surface transition-colors hover:border-accent/40">
      <summary className="flex cursor-pointer list-none items-center gap-2.5 px-3 py-2.5 [&::-webkit-details-marker]:hidden">
        <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-md bg-accent/12 px-1 text-[11px] font-semibold text-accent">
          {index ?? "·"}
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
          {source.filename}
        </span>
        <span className="shrink-0 text-xs text-faint">p.{source.page}</span>
        <ChevronDown
          size={14}
          className="shrink-0 text-faint transition-transform group-open:rotate-180"
        />
      </summary>
      <div className="border-t border-line px-3 py-2.5">
        <div className="mb-1.5 text-[11px] text-faint">
          chunk #{source.chunk_index}
        </div>
        <pre className="whitespace-pre-wrap font-sans text-xs leading-5 text-muted">
          {source.excerpt}
        </pre>
      </div>
    </details>
  );
}
