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

if (!normalizedHtml.includes("FATURAMENTO DOS MESES CAPTURADOS")) {
  throw new Error("Annual overview must not imply a complete YTD when only partial months are captured");
}
if (!normalizedHtml.includes('id="ytdPeriodList"') || !normalizedHtml.includes("Ver períodos considerados")) {
  throw new Error("Captured monthly periods must be available in a compact expandable list");
}
if (!normalizedHtml.includes("Mês corrente atualizado até")) {
  throw new Error("Open-month revenue must disclose its actual capture cutoff without a long inline paragraph");
}
if (!normalizedHtml.includes("Nenhum relatório mensal foi validado em")) {
  throw new Error("Empty annual history must not display a misleading zero or partial YTD total");
}

const dailyDateFilter = 'filter(row=>/^\\\\d{4}-\\\\d{2}-\\\\d{2}$/.test(row.date)';
if (!html.includes(dailyDateFilter)) {
  throw new Error('Daily Supabase reports must validate YYYY-MM-DD dates');
}

const dailyNormalizerStart = html.indexOf("window.setSupabaseDailyReports = function(rows){");
const dailyNormalizerEnd = html.indexOf("render();renderGroupBars();applyFilters();", dailyNormalizerStart);
if (dailyNormalizerStart < 0 || dailyNormalizerEnd < 0) throw new Error("Daily report normalizer is missing");
const dailyNormalizer = html.slice(dailyNormalizerStart, dailyNormalizerEnd);
if (!dailyNormalizer.includes("['validated','needs_review'].includes(row.status)")) throw new Error("Daily KPIs must only consume approved daily rows and surface review warnings");
if (!dailyNormalizer.includes("row.methods.length===0&&row.warnings.length>0")) throw new Error("Empty incomplete daily captures must not be counted as zero-sales days");
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
const dailyNormalizerBody = html.slice(dailyNormalizerStart, dailyNormalizerEnd);
if (!dailyNormalizerBody.includes('row.date>=windowStart&&row.date<=currentDayKey')) throw new Error('Seven-day window must filter daily reports to the rolling date range');
if (!dailyNormalizerBody.includes('window.marcopaoDailyWindow={start:windowStart,end:currentDayKey,rows:sevenDayRows,total:sevenDayTotal,coupons:sevenDayCoupons,daysWithReport:new Set(sevenDayRows.map(row=>row.date)).size}')) throw new Error('Seven-day aggregate contract is missing');

if (!html.includes(".from('dashboard_daily_report_summaries')")) throw new Error("Daily dashboard must read from the safe daily Supabase view");
if (!html.includes("window.setSupabaseDailyReports(dailyRows)")) throw new Error("Daily dashboard must receive rows from the dedicated daily read model");
if (!html.includes("Mês corrente atualizado até")) throw new Error("Monthly period summary must remain concise");
console.log(`Dashboard JS syntax OK: ${scripts.length} inline script(s) and report-insights.js.`);
