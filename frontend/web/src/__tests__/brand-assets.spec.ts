import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { inflateSync } from "node:zlib";

import { describe, expect, it } from "vitest";

const root = resolve(process.cwd(), "public/brand");
const brands = [
  "data360-trace",
  "data360-agri",
  "data360-logistic",
  "znceedi-trace",
  "znceedi-agri",
  "znceedi-logistic",
] as const;

function readPng(name: string, kind: "logos" | "favicons") {
  const path = resolve(root, kind, `${name}.png`);
  expect(existsSync(path), `${path} should exist`).toBe(true);
  const data = readFileSync(path);
  expect(data.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");

  const width = data.readUInt32BE(16);
  const height = data.readUInt32BE(20);
  const bitDepth = data[24];
  const colorType = data[25];
  expect(bitDepth).toBe(8);
  expect(colorType).toBe(6);

  const idat: Buffer[] = [];
  for (let offset = 8; offset < data.length; ) {
    const length = data.readUInt32BE(offset);
    const type = data.subarray(offset + 4, offset + 8).toString("ascii");
    if (type === "IDAT") idat.push(data.subarray(offset + 8, offset + 8 + length));
    offset += 12 + length;
  }
  const raw = inflateSync(Buffer.concat(idat));
  const stride = width * 4 + 1;
  expect(raw.length).toBe(stride * height);
  expect(Array.from({ length: height }, (_, row) => raw[row * stride])).toEqual(
    Array(height).fill(0)
  );
  expect(
    Array.from({ length: height }, (_, row) => raw.subarray(row * stride + 4, (row + 1) * stride))
      .flatMap((row) => [...row])
      .some((value, index) => index % 4 === 3 && value === 0)
  ).toBe(true);

  return { data, width, height };
}

describe("food logistics product brand assets", () => {
  it.each(brands)("provides transparent logo and square favicon for %s", (name) => {
    const logo = readPng(name, "logos");
    const favicon = readPng(name, "favicons");
    expect([logo.width, logo.height]).toEqual([512, 512]);
    expect([favicon.width, favicon.height]).toEqual([64, 64]);
  });

  it.each(brands)("provides safe vector motion artwork for %s", (name) => {
    const path = resolve(root, "motion", `${name}.svg`);
    expect(existsSync(path), `${path} should exist`).toBe(true);
    const svg = readFileSync(path, "utf8");
    expect(svg).toMatch(/<svg[^>]+viewBox="0 0 512 512"/);
    expect(svg).not.toMatch(/<text\b|<script\b|(?:href|src)=["']https?:\/\/|data:image\//i);
    expect(svg).toMatch(/<(?:path|circle|polygon)\b/);
  });

  it("keeps every product and Site combination visually unique", () => {
    for (const kind of ["logos", "favicons", "motion"] as const) {
      const hashes = brands.map((name) =>
        createHash("sha256")
          .update(readFileSync(resolve(root, kind, `${name}.${kind === "motion" ? "svg" : "png"}`)))
          .digest("hex")
      );
      expect(new Set(hashes).size, `${kind} should contain six unique assets`).toBe(brands.length);
    }
  });
});
