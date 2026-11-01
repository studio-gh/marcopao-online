import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import vm from "node:vm";

const html = readFileSync("index.html", "utf8");

// Integration contract checks: these caught the monthly-row shape regression.
const dashboardLines = html.split(/\\r?\\n/);
const reportDateListeners = dashboardLines.filter(line =>
  line.includes("document.getElementById('reportDateFilter')?.addEventListener('change',applyFilters)")
);
if (reportDateListeners.length !== 1) {
  throw new Error(`Expected one report-date change listener, found ${reportDateListeners.length}`);
}
if (!dashboardLines.some(line => line.trim() === "window.monthlyReportRow=currentMonthlyRecord;")) {
  throw new Error("Monthly report public state must expose the raw Supabase row");
}
if (!dashboardLines.some(line => line.trim() === "window.monthlySnapshot=currentMonthlyRecord?.totals||null;")) {
  throw new Error("Monthly snapshot must expose the raw totals object");
}
if (!dashboardLines.some(line => line.includes("showMonthlyReport(window.monthlyReportRow)"))) {
  throw new Error("Monthly report view consumer is missing");
}

const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
if (!scripts.length) throw new Error("No inline JavaScript found in index.html");

for (const [index, match] of scripts.entries()) {
  const attrs = match[1] || "";
  const source = match[2] || "";
  const filename = `index.html:inline-script-${index + 1}`;
  if (/\btype\s*=\s*["']module["']/i.test(attrs)) {
    const directory = mkdtempSync(join(tmpdir(), "marcopao-dashboard-check-"));
    const path = join(directory, `inline-${index + 1}.mjs`);
    try {
      writeFileSync(path, source, "utf8");
      const result = spawnSync(process.execPath, ["--check", path], { encoding: "utf8" });
      if (result.status !== 0) {
        throw new Error(`Syntax error in ${filename}:\n${result.stderr || result.stdout}`);
      }
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  } else {
    new vm.Script(source, { filename });
  }
}
const insightsResult = spawnSync(process.execPath, ["--check", "report-insights.js"], { encoding: "utf8" });
if (insightsResult.status !== 0) {
  throw new Error(`Syntax error in report-insights.js:\n${insightsResult.stderr || insightsResult.stdout}`);
}
console.log(`Dashboard JS syntax OK: ${scripts.length} inline script(s) and report-insights.js.`);
