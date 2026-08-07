import fs from "node:fs";
import path from "node:path";

export function toKebabCase(name: string): string {
  return name
    .replace(/([a-z\d])([A-Z])/g, "$1-$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1-$2")
    .toLowerCase();
}

export function collectElementPlusComponentNames(source: string): string[] {
  const names = new Set<string>();
  for (const match of source.matchAll(/<(?:El|el-)([A-Za-z][A-Za-z0-9-]*)\b/g)) {
    const componentName = match[1];
    if (componentName) names.add(toKebabCase(componentName));
  }
  return [...names].sort();
}

export function createElementPlusStyleIncludes(
  used: Iterable<string>,
  available: ReadonlySet<string>
): string[] {
  return [...new Set(used)]
    .filter((name) => available.has(name))
    .sort()
    .map((name) => `element-plus/es/components/${name}/style/index`);
}

export function scanElementPlusStyleIncludes(
  sourceRoot: string,
  componentsRoot: string
): string[] {
  try {
    const sources: string[] = [];
    const walk = (directory: string) => {
      for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        const target = path.join(directory, entry.name);
        if (entry.isDirectory()) {
          walk(target);
        } else if (entry.name.endsWith(".vue")) {
          sources.push(fs.readFileSync(target, "utf8"));
        }
      }
    };

    walk(sourceRoot);
    const available = new Set(
      fs
        .readdirSync(componentsRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
    );

    return createElementPlusStyleIncludes(
      sources.flatMap(collectElementPlusComponentNames),
      available
    );
  } catch {
    return [];
  }
}
