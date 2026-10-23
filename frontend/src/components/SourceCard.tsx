import type { Source } from "../api/types";

interface Props {
  source: Source;
  index?: number;
  active?: boolean;
  onOpen?: () => void;
}

export default function SourceCard({ source, index, active, onOpen }: Props) {
  return (
    <button
      onClick={onOpen}
      title={`Show ${source.filename} in the evidence panel`}
      className={`flex w-full items-center gap-2.5 rounded-xl border bg-surface px-3 py-2 text-left transition-colors ${
        active
          ? "border-accent/60 ring-1 ring-accent/30"
          : "border-line hover:border-accent/40"
      }`}
    >
      <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-md bg-accent/12 px-1 text-[11px] font-semibold text-accent">
        {index ?? "·"}
      </span>
      <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
        {source.filename}
      </span>
      <span className="shrink-0 text-xs text-faint">p.{source.page}</span>
    </button>
  );
}
