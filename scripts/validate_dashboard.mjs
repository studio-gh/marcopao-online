import { readFileSync } from "node:fs";
import vm from "node:vm";
const html = readFileSync("index.html", "utf8");
const scripts = [...html.matchAll(/<script>([\\s\\S]*?)<\\/script>/gi)].map(m => m[1]);
if (!scripts.length) throw new Error("No inline JavaScript found in index.html");
for (const [index, source] of scripts.entries()) {
  new vm.Script(source, { filename: `index.html:inline-script-${index + 1}` });
}
console.log(`Dashboard JS syntax OK: ${scripts.length} inline script(s).`);
