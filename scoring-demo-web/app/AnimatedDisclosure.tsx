"use client";

import { useEffect, useRef, type MouseEvent, type ReactNode } from "react";

type AnimatedDisclosureProps = {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  bodyClassName?: string;
};

/** Native disclosure semantics, with an interruptible transition between heights. */
export function AnimatedDisclosure({ summary, children, defaultOpen = false, className, bodyClassName }: AnimatedDisclosureProps) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const animationRef = useRef<Animation | null>(null);
  const desiredOpenRef = useRef(defaultOpen);

  useEffect(() => () => {
    const animation = animationRef.current;
    if (animation) {
      animation.onfinish = null;
      animation.cancel();
      animationRef.current = null;
    }
  }, []);

  function toggle(event: MouseEvent<HTMLElement>) {
    event.preventDefault();
    const details = detailsRef.current;
    if (!details) return;

    const heading = event.currentTarget;
    const opening = !desiredOpenRef.current;
    desiredOpenRef.current = opening;
    details.dataset.expanded = String(opening);
    heading.setAttribute("aria-expanded", String(opening));

    // Capture the current visual height before cancelling, so rapid reversals
    // continue from the visible position rather than either endpoint.
    const from = details.getBoundingClientRect().height;
    const previous = animationRef.current;
    if (previous) {
      previous.onfinish = null;
      previous.cancel();
      animationRef.current = null;
    }

    const settle = () => {
      details.open = opening;
      details.style.removeProperty("height");
      details.style.removeProperty("overflow");
      details.removeAttribute("data-animating");
    };

    if (typeof details.animate !== "function" || typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      settle();
      return;
    }

    // Keep the content rendered throughout closing. All measurements and the
    // first keyframe happen synchronously, before the browser's next paint.
    details.open = true;
    details.style.height = "auto";
    const expandedHeight = details.getBoundingClientRect().height;
    const styles = window.getComputedStyle(details);
    const frameHeight = [styles.borderTopWidth, styles.borderBottomWidth, styles.paddingTop, styles.paddingBottom]
      .reduce((total, value) => total + (Number.parseFloat(value) || 0), 0);
    const collapsedHeight = heading.getBoundingClientRect().height + frameHeight;
    const to = opening ? expandedHeight : collapsedHeight;
    if (Math.abs(from - to) < 1) {
      settle();
      return;
    }

    details.style.height = `${from}px`;
    details.style.overflow = "hidden";
    details.dataset.animating = "true";
    const fullTravel = Math.max(expandedHeight - collapsedHeight, 1);
    const duration = Math.max(100, 280 * Math.min(Math.abs(to - from) / fullTravel, 1));
    try {
      const animation = details.animate([{ height: `${from}px` }, { height: `${to}px` }], {
        duration,
        easing: "cubic-bezier(.22, 1, .36, 1)",
        fill: "forwards",
      });
      animationRef.current = animation;
      animation.onfinish = () => {
        if (animationRef.current !== animation) return;
        animationRef.current = null;
        animation.onfinish = null;
        settle();
        animation.cancel();
      };
    } catch {
      // Older or restricted browser environments retain a usable disclosure.
      settle();
    }
  }

  return <details ref={detailsRef} className={className} open={defaultOpen || undefined} onToggle={event => {
    // Also follow native changes, such as the browser revealing a search match.
    if (animationRef.current) return;
    const details = event.currentTarget;
    desiredOpenRef.current = details.open;
    details.dataset.expanded = String(details.open);
    details.firstElementChild?.setAttribute("aria-expanded", String(details.open));
  }}>
    <summary onClick={toggle}>{summary}</summary>
    <div className={bodyClassName}>{children}</div>
  </details>;
}
