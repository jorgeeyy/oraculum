import { useRef, useState } from "react";
import { Loader2, Upload } from "lucide-react";

interface Props {
  onFile: (file: File) => void;
  busy: boolean;
}

export default function UploadDropzone({ onFile, busy }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div
      className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-2xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
        dragOver
          ? "border-accent bg-accent/8"
          : "border-line bg-surface hover:border-accent/50"
      } ${busy ? "pointer-events-none opacity-70" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files[0];
        if (file && !busy) onFile(file);
      }}
    >
      {busy ? (
        <span className="flex items-center gap-2 text-sm text-muted">
          <Loader2 size={16} className="animate-spin text-accent" />
          Uploading and indexing…
        </span>
      ) : (
        <>
          <span className="mb-1 flex h-10 w-10 items-center justify-center rounded-xl bg-accent/10 text-accent">
            <Upload size={18} strokeWidth={2} />
          </span>
          <span className="text-sm font-medium">
            Drop a file here <span className="text-muted">or click to browse</span>
          </span>
          <span className="text-xs text-faint">
            .pdf · .txt · .md — up to 10 MB
          </span>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && !busy) onFile(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
