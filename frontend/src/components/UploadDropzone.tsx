import { useRef, useState } from "react";

interface Props {
  onFile: (file: File) => void;
  busy: boolean;
}

export default function UploadDropzone({ onFile, busy }: Props) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div
      className={`dropzone ${dragOver ? "dragover" : ""}`}
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
        "Uploading and indexing…"
      ) : (
        <>
          <strong>Drop a file here</strong> or click to browse
          <div className="muted">.pdf · .txt · .md — up to 10&nbsp;MB</div>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && !busy) onFile(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
