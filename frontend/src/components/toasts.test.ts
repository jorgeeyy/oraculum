import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  dismissToast,
  pushToast,
  resetToasts,
  subscribeToasts,
  type Toast,
} from "./toasts";

/**
 * Live view of the store: read `view.list` at assertion time (it is
 * replaced on every update — never destructure it).
 */
function collect(): { list: Toast[]; stop: () => void } {
  const view: { list: Toast[]; stop: () => void } = {
    list: [],
    stop: () => {},
  };
  view.stop = subscribeToasts((toasts) => {
    view.list = toasts;
  });
  return view;
}

beforeEach(() => {
  vi.useFakeTimers();
  resetToasts();
});

afterEach(() => {
  vi.useRealTimers();
  resetToasts();
});

describe("toast store", () => {
  it("push notifies subscribers and dismiss removes it", () => {
    const view = collect();

    const id = pushToast({ kind: "error", message: "boom" });
    expect(id).toBeGreaterThan(0);
    expect(view.list).toHaveLength(1);
    expect(view.list[0]).toMatchObject({
      kind: "error",
      message: "boom",
      sticky: false,
    });

    dismissToast(id);
    expect(view.list).toHaveLength(0);
  });

  it("subscribe replays the current list immediately", () => {
    pushToast({ kind: "info", message: "already here" });

    const seen: Toast[] = [];
    const stop = subscribeToasts((toasts) => seen.push(...toasts));

    expect(seen.map((t) => t.message)).toEqual(["already here"]);
    stop();
  });

  it("auto-dismisses non-sticky toasts after their lifetime", () => {
    const view = collect();
    pushToast({ kind: "success", message: "saved" });

    expect(view.list).toHaveLength(1);
    vi.advanceTimersByTime(4000); // success TTL
    expect(view.list).toHaveLength(0);
  });

  it("keeps sticky toasts past every lifetime (governance states)", () => {
    const view = collect();
    pushToast({ kind: "warn", message: "budget exhausted", sticky: true });

    vi.advanceTimersByTime(60_000);
    expect(view.list).toHaveLength(1);
    expect(view.list[0].message).toBe("budget exhausted");
  });

  it("dedupes an identical message pushed twice in quick succession", () => {
    const view = collect();

    const first = pushToast({ kind: "warn", message: "Session expired" });
    const second = pushToast({ kind: "warn", message: "Session expired" });

    expect(first).toBeGreaterThan(0);
    expect(second).toBe(0); // parallel 401s collapse into one toast
    expect(view.list).toHaveLength(1);
  });

  it("allows the same message again after the dedupe window", () => {
    const view = collect();
    pushToast({ kind: "warn", message: "Session expired" });

    vi.advanceTimersByTime(2600);

    expect(
      pushToast({ kind: "warn", message: "Session expired" }),
    ).toBeGreaterThan(0);
    expect(view.list).toHaveLength(2); // both alive: 8s TTL outlasts the 2.5s window
  });

  it("keeps distinct messages as separate toasts", () => {
    const view = collect();
    pushToast({ kind: "error", message: "first" });
    pushToast({ kind: "error", message: "second" });

    expect(view.list.map((t) => t.message)).toEqual(["first", "second"]);
  });

  it("unsubscribed listeners stop receiving updates", () => {
    const view = collect();
    pushToast({ kind: "info", message: "before" });
    const before = view.list.length;

    view.stop();
    pushToast({ kind: "info", message: "after" });

    expect(view.list).toHaveLength(before);
  });
});
