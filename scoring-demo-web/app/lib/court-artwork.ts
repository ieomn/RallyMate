// One ground-plane projection for every court marking. In perspective the
// halfway line is the intersection of the diagonals, not half the SVG height.
export const COURT_LENGTH = 23.77;
export const DOUBLES_WIDTH = 10.97;
export const SINGLES_WIDTH = 8.23;
export const NET_DISTANCE = COURT_LENGTH / 2;
export const SERVICE_DISTANCE = 6.4;

export function courtArtworkPoint(x: number, distance: number): [number, number] {
  const t = distance / COURT_LENGTH;
  const nearScale = 222 / 434;
  const denominator = 1 - (1 - nearScale) * t;
  const centerX = (284 + (264 * nearScale - 284) * t) / denominator;
  const y = (53 + (333 * nearScale - 53) * t) / denominator;
  return [centerX + x * 222 / DOUBLES_WIDTH / denominator, y];
}

export function courtArtworkPath(points: Array<[number, number]>, closed = false): string {
  return points.map(([x, distance], index) => `${index ? "L" : "M"}${courtArtworkPoint(x, distance).map(value => value.toFixed(2)).join(" ")}`).join("") + (closed ? "Z" : "");
}
