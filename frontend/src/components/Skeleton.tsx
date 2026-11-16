/**
 * Loading skeleton block (WS-8): a shimmering placeholder that matches the
 * shape of the content about to arrive, so lists and threads don't jump
 * from a centered spinner to full layout.
 */
export default function Skeleton({
  className = "",
}: {
  className?: string;
}) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-lg bg-surface ring-1 ring-line/60 ${className}`}
    />
  );
}
