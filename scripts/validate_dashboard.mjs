import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import vm from "node:vm";

const html = readFileSync("index.html", "utf8");

// Integration contract checks: validate monthly data shape and duplicate handlers.
const normalizedHtml = html.replace(/\\s+/g, " ");
if (!normalizedHtml.includes("window.monthlyReportRow=currentMonthlyRecord;")) {
  throw new Error("Monthly report public state must expose the raw Supabase row");
}
if (!normalizedHtml.includes("window.monthlySnapshot=currentMonthlyRecord?.totals||null;")) {
  throw new Error("Monthly snapshot must expose the raw totals object");
}
if (!normalizedHtml.includes("showMonthlyReport(window.monthlyReportRow)")) {
  throw new Error("Monthly report view consumer is missing");
}

const dailyDateFilter = "filter(row=>/^\\d{4}-\\d{2}-\\d{2}$/.test(row.date)";
if (!html.includes(dailyDateFilter)) {
  throw new Error("Daily Supabase reports must use a valid YYYY-MM-DD date regex");
}

const dailyNormalizerStart = html.indexOf("window.setSupabaseDailyReports = function(rows){");
const dailyNormalizerEnd = html.indexOf("\n };", dailyNormalizerStart);
if (dailyNormalizerStart < 0 || dailyNormalizerEnd < 0) throw new Error("Daily report normalizer is missing");
const dailyNormalizer = html.slice(dailyNormalizerStart, dailyNormalizerEnd);
if (!dailyNormalizer.includes("row.status==='validated'")) throw new Error("Daily KPIs must only consume validated rows");
if (!dailyNormalizer.includes("row.report_date||row.period_start")) throw new Error("Daily report date fallback is missing");
if (!dailyNormalizer.includes("Number.isFinite(row.metrics.total)")) throw new Error("Daily report totals must be finite numbers");
if (!dailyNormalizer.includes("dailyReports=[...latestPerDay.values()]")) throw new Error("Daily latest-per-day deduplication is missing");

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
