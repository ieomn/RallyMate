import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = fs.readFileSync(new URL("../app/lib/court-artwork.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { courtArtworkPoint: point, COURT_LENGTH, DOUBLES_WIDTH, NET_DISTANCE, SERVICE_DISTANCE } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const cross = (a, b) => a[0] * b[1] - a[1] * b[0];
const subtract = (a, b) => a.map((v, i) => v - b[i]);

test("the net sits at the projected court center on both diagonals", () => {
  const half = DOUBLES_WIDTH / 2;
  const center = point(0, NET_DISTANCE);
  for (const sign of [-1, 1]) {
    const far = point(sign * half, 0), near = point(-sign * half, COURT_LENGTH);
    assert.ok(Math.abs(cross(subtract(center, far), subtract(near, far))) < 1e-8);
  }
  assert.equal(NET_DISTANCE * 2, COURT_LENGTH);
});

test("service lines stay on opposite sides of the net in the same perspective", () => {
  const far = point(0, NET_DISTANCE - SERVICE_DISTANCE)[1];
  const net = point(0, NET_DISTANCE)[1];
  const near = point(0, NET_DISTANCE + SERVICE_DISTANCE)[1];
  assert.ok(far < net && net < near);
  assert.deepEqual(point(-DOUBLES_WIDTH / 2, 0), [173, 53]);
  const corner = point(DOUBLES_WIDTH / 2, COURT_LENGTH);
  assert.ok(Math.abs(corner[0] - 481) < 1e-8 && Math.abs(corner[1] - 333) < 1e-8);
});
