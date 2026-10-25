/** Device routing for the public 시연판: phones land on /m, everything else on the 3D twin; a manual choice sticks. */
export const VIEW_KEY = "twin-view";
const PHONE_MAX_WIDTH = 767;
const COARSE_SMALL_MAX_WIDTH = 900;

export function isPhone(): boolean {
  if (typeof window === "undefined") return false;
  const w = window.innerWidth;
  const coarse = typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;
  return w <= PHONE_MAX_WIDTH || (coarse && w <= COARSE_SMALL_MAX_WIDTH);
}

export function chosenView(): "pc" | "mobile" | null {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    return v === "pc" || v === "mobile" ? v : null;
  } catch {
    return null;
  }
}

/** Where the site root should go on this device. */
export function landingPath(): "/m" | "/twin" {
  const c = chosenView();
  if (c === "pc") return "/twin";
  if (c === "mobile") return "/m";
  return isPhone() ? "/m" : "/twin";
}
