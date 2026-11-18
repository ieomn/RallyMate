import type { MouseEvent } from "react";

/** A fast tap can end between paints. Start the release on activation, including keyboard activation. */
export function playPressFeedback(event: MouseEvent<HTMLElement>) {
  if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const control = event.target instanceof Element ? event.target.closest<HTMLElement>("button:enabled, a[href], summary") : null;
  if (!control?.animate || !event.currentTarget.contains(control) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  // Keep the travel of a full-width record row as small as that of a compact button.
  const depth = Math.min(.055, 7 / Math.max(1, control.offsetWidth));
  control.getAnimations().find(animation => animation.id === "press-release")?.cancel();
  control.animate([
    { scale: String(1 - depth), offset: 0 },
    { scale: String(1 + depth * .18), offset: .62 },
    { scale: "1" },
  ], { id: "press-release", duration: 290, easing: "cubic-bezier(.18,.8,.32,1)" });
}
