// Shared 16-color cluster/paper palette — used by the 2D paper views,
// the cluster chips and the 3D concept graph's per-paper link colors,
// so the same paper reads as the same color everywhere.
export const PALETTE = [
  "#5b9dff", "#f0b429", "#3ecf8e", "#e46fb2", "#b07df9", "#5fd4d0",
  "#f0d04a", "#f07b5f", "#9aa7b5", "#5ec97a", "#c98bf5", "#5fbef9",
  "#f07b9c", "#8bf97b", "#f0c06b", "#6b8ef9",
];

// Mix a hex color toward white — used to keep 1px-ish lines readable on the
// dark graph background (pure palette hues sit too close to black once thin).
export function lighten(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16);
  const ch = (shift: number) => Math.round(((n >> shift) & 255) + (255 - ((n >> shift) & 255)) * amount);
  const to2 = (v: number) => v.toString(16).padStart(2, "0");
  return `#${to2(ch(16))}${to2(ch(8))}${to2(ch(0))}`;
}
