#!/usr/bin/env node
import { readFile, writeFile } from "node:fs/promises";
import { basename } from "node:path";
import { validateBytes } from "gltf-validator";

function usage() {
  console.error("Usage: node tools/gltf_validate.mjs <model.glb|model.gltf> [report.json]");
  process.exit(2);
}

const [, , inputPath, outputPath] = process.argv;
if (!inputPath) {
  usage();
}

const bytes = await readFile(inputPath);
const report = await validateBytes(new Uint8Array(bytes), {
  uri: basename(inputPath),
  maxIssues: 2000,
});

const json = JSON.stringify(report, null, 2);
if (outputPath) {
  await writeFile(outputPath, `${json}\n`);
} else {
  console.log(json);
}

const issues = report.issues || {};
const total =
  Number(issues.numErrors || 0) +
  Number(issues.numWarnings || 0) +
  Number(issues.numInfos || 0) +
  Number(issues.numHints || 0);

if (Number(issues.numErrors || 0) > 0) {
  process.exit(1);
}
if (total > 0) {
  process.exit(0);
}
