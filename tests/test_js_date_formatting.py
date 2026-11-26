"""Regression test for the date/time formatter helpers in static/js/globals.js.

Bug: audit.js calls ``formatDateTime(d, { dateStyle: "short", timeStyle: "medium" })``.
The old implementation spread component defaults (year/month/day...) together
with dateStyle, which makes Intl throw
``TypeError: Can't set option year when dateStyle is used`` — breaking the whole
Audit history table render with "Couldn't load audit events (Can't set option
year when dateStyle is used)".
"""
import shutil
import subprocess
from pathlib import Path

import pytest

GLOBALS_JS = Path(__file__).resolve().parent.parent / "src" / "llm_gateway" / "static" / "js" / "globals.js"

NODE_SCRIPT = """
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('// ── Timezone');
const end = src.indexOf('// DataTables ships');
if (start < 0 || end < 0) { console.error('markers not found'); process.exit(2); }
global.window = { escapeHtml: (x) => String(x) };
eval(src.slice(start, end));
const d = new Date('2026-11-26T09:01:07.687906Z');
const out = {
  style: global.window.formatDateTime(d, { dateStyle: 'short', timeStyle: 'medium' }),
  dflt: global.window.formatDateTime(d),
  dateStyle: global.window.formatDate(d, { dateStyle: 'short' }),
  timeStyle: global.window.formatTime(d, { timeStyle: 'short' }),
};
for (const [k, v] of Object.entries(out)) {
  if (typeof v !== 'string' || v === '—' || !v.length) {
    console.error('formatter ' + k + ' returned: ' + v);
    process.exit(3);
  }
}
console.log('ok');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_formatters_accept_date_and_time_style_options(tmp_path):
    script = tmp_path / "check.js"
    script.write_text(NODE_SCRIPT)
    proc = subprocess.run(
        ["node", str(script), str(GLOBALS_JS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"globals.js formatters failed: {proc.stderr}"
    assert proc.stdout.strip() == "ok"
