import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { deflateSync } from "node:zlib";

type Site = "data360" | "znceedi";
type System = "trace" | "agri" | "logistic";
type Color = readonly [number, number, number];
type Point = readonly [number, number];

const OUTPUT_ROOT = resolve(process.cwd(), "public/brand");
const sites: Site[] = ["data360", "znceedi"];
const systems: System[] = ["trace", "agri", "logistic"];
const palettes: Record<
  Site,
  { primary: string; secondary: string; glow: string; rgb: Color; accent: Color }
> = {
  data360: {
    primary: "#2563EB",
    secondary: "#38BDF8",
    glow: "#A5F3FC",
    rgb: [37, 99, 235],
    accent: [56, 189, 248],
  },
  znceedi: {
    primary: "#078C72",
    secondary: "#2DD4BF",
    glow: "#99F6E4",
    rgb: [7, 140, 114],
    accent: [45, 212, 191],
  },
};

let crcTable: number[] | undefined;
function crc32(data: Buffer) {
  crcTable ??= Array.from({ length: 256 }, (_, start) => {
    let value = start;
    for (let bit = 0; bit < 8; bit += 1)
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    return value >>> 0;
  });
  let crc = 0xffffffff;
  for (const byte of data) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, data: Buffer) {
  const name = Buffer.from(type);
  const chunk = Buffer.alloc(data.length + 12);
  chunk.writeUInt32BE(data.length, 0);
  name.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(crc32(Buffer.concat([name, data])), data.length + 8);
  return chunk;
}

function encodePng(width: number, height: number, pixels: Uint8Array) {
  const raw = Buffer.alloc(height * (width * 4 + 1));
  for (let row = 0; row < height; row += 1) {
    const target = row * (width * 4 + 1);
    raw[target] = 0;
    raw.set(pixels.subarray(row * width * 4, (row + 1) * width * 4), target + 1);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from("89504e470d0a1a0a", "hex"),
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(raw, { level: 9 })),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

class Canvas {
  readonly pixels: Uint8Array;
  constructor(readonly size: number) {
    this.pixels = new Uint8Array(size * size * 4);
  }
  blend(x: number, y: number, color: Color, alpha = 1) {
    if (x < 0 || y < 0 || x >= this.size || y >= this.size || alpha <= 0) return;
    const offset = (Math.floor(y) * this.size + Math.floor(x)) * 4;
    const previous = this.pixels[offset + 3] / 255;
    const next = alpha + previous * (1 - alpha);
    for (let channel = 0; channel < 3; channel += 1) {
      this.pixels[offset + channel] = Math.round(
        (color[channel] * alpha + this.pixels[offset + channel] * previous * (1 - alpha)) / next
      );
    }
    this.pixels[offset + 3] = Math.round(next * 255);
  }
  circle(cx: number, cy: number, radius: number, color: Color) {
    const edge = 1.25;
    for (let y = Math.floor(cy - radius - edge); y <= cy + radius + edge; y += 1) {
      for (let x = Math.floor(cx - radius - edge); x <= cx + radius + edge; x += 1) {
        const distance = Math.hypot(x + 0.5 - cx, y + 0.5 - cy);
        this.blend(x, y, color, Math.max(0, Math.min(1, radius + edge / 2 - distance)));
      }
    }
  }
  line([x1, y1]: Point, [x2, y2]: Point, width: number, color: Color) {
    const lengthSquared = (x2 - x1) ** 2 + (y2 - y1) ** 2;
    const radius = width / 2;
    for (
      let y = Math.floor(Math.min(y1, y2) - radius - 1);
      y <= Math.max(y1, y2) + radius + 1;
      y += 1
    ) {
      for (
        let x = Math.floor(Math.min(x1, x2) - radius - 1);
        x <= Math.max(x1, x2) + radius + 1;
        x += 1
      ) {
        const t = Math.max(
          0,
          Math.min(1, ((x + 0.5 - x1) * (x2 - x1) + (y + 0.5 - y1) * (y2 - y1)) / lengthSquared)
        );
        const distance = Math.hypot(x + 0.5 - (x1 + t * (x2 - x1)), y + 0.5 - (y1 + t * (y2 - y1)));
        this.blend(x, y, color, Math.max(0, Math.min(1, radius + 0.6 - distance)));
      }
    }
  }
  polygon(points: Point[], color: Color) {
    const minX = Math.floor(Math.min(...points.map(([x]) => x)));
    const maxX = Math.ceil(Math.max(...points.map(([x]) => x)));
    const minY = Math.floor(Math.min(...points.map(([, y]) => y)));
    const maxY = Math.ceil(Math.max(...points.map(([, y]) => y)));
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) {
        let inside = false;
        for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
          const [xi, yi] = points[i];
          const [xj, yj] = points[j];
          if (
            yi > y + 0.5 !== yj > y + 0.5 &&
            x + 0.5 < ((xj - xi) * (y + 0.5 - yi)) / (yj - yi) + xi
          )
            inside = !inside;
        }
        if (inside) this.blend(x, y, color);
      }
    }
  }
}

function scalePoint(point: Point, scale: number): Point {
  return [point[0] * scale, point[1] * scale];
}

function drawMark(canvas: Canvas, site: Site, system: System, compact = false) {
  const scale = canvas.size / 512;
  const p = palettes[site];
  const point = (x: number, y: number): Point => scalePoint([x, y], scale);
  const circle = (x: number, y: number, radius: number, color: Color) =>
    canvas.circle(x * scale, y * scale, radius * scale, color);
  const line = (a: Point, b: Point, width: number, color: Color) =>
    canvas.line(scalePoint(a, scale), scalePoint(b, scale), width * scale, color);
  const polygon = (points: Point[], color: Color) =>
    canvas.polygon(
      points.map((item) => scalePoint(item, scale)),
      color
    );

  if (system === "trace") {
    const shield: Point[] =
      site === "data360"
        ? [
            [256, 92],
            [386, 142],
            [364, 322],
            [256, 420],
            [148, 322],
            [126, 142],
          ]
        : [
            [256, 88],
            [378, 136],
            [390, 236],
            [350, 346],
            [256, 424],
            [162, 346],
            [122, 236],
            [134, 136],
          ];
    polygon(shield, p.rgb);
    polygon(
      [
        [256, 130],
        [342, 164],
        [326, 301],
        [256, 367],
        [186, 301],
        [170, 164],
      ],
      site === "data360" ? [8, 31, 67] : [6, 57, 52]
    );
    line([194, 251], [239, 296], 27, p.accent);
    line([239, 296], [327, 205], 27, p.accent);
    const nodes: Point[] =
      site === "data360"
        ? [
            [151, 157],
            [361, 157],
            [256, 396],
          ]
        : [
            [144, 205],
            [368, 205],
            [256, 397],
          ];
    for (const [x, y] of nodes) circle(x, y, 15, p.accent);
    if (!compact) {
      line([173, 111], [130, 92], 8, p.accent);
      line([339, 111], [382, 92], 8, p.accent);
      circle(256, 455, 8, p.accent);
    }
  } else if (system === "agri") {
    const leafA: Point[] =
      site === "data360"
        ? [
            [251, 361],
            [141, 300],
            [126, 160],
            [260, 208],
          ]
        : [
            [252, 370],
            [126, 304],
            [146, 145],
            [270, 218],
          ];
    const leafB: Point[] =
      site === "data360"
        ? [
            [261, 350],
            [373, 290],
            [387, 145],
            [251, 203],
          ]
        : [
            [260, 362],
            [389, 298],
            [366, 137],
            [245, 215],
          ];
    polygon(leafA, p.rgb);
    polygon(leafB, p.accent);
    line([256, 378], [253, 205], 20, site === "data360" ? [165, 243, 252] : [153, 246, 228]);
    line([252, 280], site === "data360" ? [168, 224] : [160, 237], 14, [226, 252, 250]);
    line([254, 304], site === "data360" ? [340, 225] : [350, 244], 14, [226, 252, 250]);
    for (const [x, y] of (site === "data360"
      ? [
          [150, 170],
          [374, 155],
          [256, 382],
        ]
      : [
          [151, 161],
          [363, 151],
          [256, 385],
        ]) as Point[])
      circle(x, y, 14, p.accent);
    if (!compact) {
      line([108, 336], [72, 358], 8, p.accent);
      line([404, 329], [442, 348], 8, p.accent);
    }
  } else {
    const body: Point[] =
      site === "data360"
        ? [
            [111, 236],
            [317, 236],
            [375, 289],
            [407, 289],
            [407, 361],
            [111, 361],
          ]
        : [
            [105, 235],
            [310, 235],
            [373, 284],
            [407, 294],
            [407, 360],
            [105, 360],
          ];
    polygon(body, p.rgb);
    polygon(
      [
        [131, 255],
        [292, 255],
        [331, 289],
        [131, 289],
      ],
      p.accent
    );
    circle(178, 363, 38, [226, 252, 250]);
    circle(345, 363, 38, [226, 252, 250]);
    circle(178, 363, 19, p.rgb);
    circle(345, 363, 19, p.rgb);
    const snowCenter = site === "data360" ? point(346, 161) : point(332, 160);
    for (let angle = 0; angle < Math.PI; angle += Math.PI / 3) {
      const dx = Math.cos(angle) * 60 * scale;
      const dy = Math.sin(angle) * 60 * scale;
      canvas.line(
        [snowCenter[0] - dx, snowCenter[1] - dy],
        [snowCenter[0] + dx, snowCenter[1] + dy],
        13 * scale,
        p.accent
      );
    }
    circle(snowCenter[0] / scale, snowCenter[1] / scale, 16, [226, 252, 250]);
    if (!compact) {
      line([89, 209], [70, 186], 8, p.accent);
      line([423, 386], [455, 386], 8, p.accent);
    }
  }
}

function svgFor(site: Site, system: System) {
  const p = palettes[site];
  const background = site === "data360" ? "#07172E" : "#052E2B";
  const siteShape =
    site === "data360"
      ? "M96 256 160 96h192l64 160-64 160H160Z"
      : "M256 72c102 0 184 82 184 184s-82 184-184 184S72 358 72 256 154 72 256 72Z";
  const motif =
    system === "trace"
      ? `<path class="mark" d="M256 112 374 154l-20 165-98 88-98-88-20-165Z"/><path class="signal" d="m194 252 45 45 86-91"/>`
      : system === "agri"
        ? `<path class="mark" d="M254 371C144 345 118 244 143 137c83 25 119 85 111 234Z"/><path class="accent" d="M258 365c113-29 141-128 111-232-80 30-116 91-111 232Z"/><path class="signal" d="M256 382V205m-2 76-84-59m85 81 89-68"/>`
        : `<path class="mark" d="M105 238h205l65 52h33v72H105Z"/><circle class="wheel" cx="178" cy="363" r="31"/><circle class="wheel" cx="345" cy="363" r="31"/><path class="signal" d="M342 103v116m-50-87 100 58m-100 0 100-58"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="${site}-${system} brand motion">
  <defs><linearGradient id="brand-gradient" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${p.primary}"/><stop offset="1" stop-color="${p.secondary}"/></linearGradient></defs>
  <path d="${siteShape}" fill="${background}" opacity=".98"/>
  <g fill="url(#brand-gradient)" stroke-linecap="round" stroke-linejoin="round">${motif}</g>
  <circle cx="116" cy="256" r="10" fill="${p.glow}"/><circle cx="396" cy="256" r="10" fill="${p.secondary}"/>
  <style>.mark,.accent{fill:url(#brand-gradient)}.signal{fill:none;stroke:${p.glow};stroke-width:18}.wheel{fill:${p.glow};stroke:${p.primary};stroke-width:13}</style>
</svg>
`;
}

function write(path: string, data: string | Buffer) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, data);
}

for (const site of sites) {
  for (const system of systems) {
    const name = `${site}-${system}`;
    const logo = new Canvas(512);
    drawMark(logo, site, system, false);
    write(resolve(OUTPUT_ROOT, "logos", `${name}.png`), encodePng(512, 512, logo.pixels));
    const favicon = new Canvas(64);
    drawMark(favicon, site, system, true);
    write(resolve(OUTPUT_ROOT, "favicons", `${name}.png`), encodePng(64, 64, favicon.pixels));
    write(resolve(OUTPUT_ROOT, "motion", `${name}.svg`), svgFor(site, system));
  }
}

console.log("Generated 18 product brand assets in public/brand");
