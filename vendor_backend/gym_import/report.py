"""The check report as one self-contained HTML file the owner can open
in any browser (and print). Contains members' names and amounts, so it is
saved next to the source file on the owner's own PC, nowhere else."""
import html
from datetime import datetime


CSS = """
:root{--ink:#172033;--muted:#667085;--line:#E4E7EC;--ok:#067647;--okl:#ECFDF3;--bad:#B42318;--badl:#FEF3F2;--wn:#B54708;--wnl:#FFFAEB;--p:#6D5DF6}
*{box-sizing:border-box}body{font-family:Inter,Segoe UI,Arial,sans-serif;color:var(--ink);margin:0;background:#F6F7FB}
.wrap{max-width:1100px;margin:auto;padding:24px 16px 60px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.muted{color:var(--muted)}.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.kpi{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px}
.kpi b{display:block;font-size:22px;margin-top:4px}.banner{border-radius:12px;padding:14px 16px;font-weight:600;margin:14px 0}
.ok-b{background:var(--okl);color:var(--ok)}.bad-b{background:var(--badl);color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}.scroll{overflow-x:auto}
.tag{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;font-weight:600}.t-ok{background:var(--okl);color:var(--ok)}
.t-bad{background:var(--badl);color:var(--bad)}.t-wn{background:var(--wnl);color:var(--wn)}code{background:#F2F4F7;padding:1px 5px;border-radius:5px}
@media print{body{background:#fff}.card{break-inside:avoid}}
"""


def render(imp, check, not_moved, source_path, sha, today):
    e = html.escape
    probs = check.problems
    areas = []
    for area in dict.fromkeys(r[0] for r in check.rows):
        rows = [r for r in check.rows if r[0] == area]
        bad = sum(1 for r in rows if not r[4])
        body = "".join(
            f"<tr><td>{e(str(item))}</td><td>{e(str(old))}</td><td>{e(str(new))}</td>"
            f"<td>{'<span class=\"tag t-ok\">Same</span>' if ok else '<span class=\"tag t-bad\">Different</span>'}</td>"
            f"<td class='muted'>{e(note)}</td></tr>" for _, item, old, new, ok, note in rows)
        areas.append(f"<h2>{e(area)} <span class='muted' style='font-weight:500;font-size:13px'>· {len(rows) - bad} of {len(rows)} match</span></h2>"
                     f"<div class='card scroll'><table><thead><tr><th>What</th><th>v1.2.0</th><th>New system</th><th></th><th>Note</th></tr></thead>"
                     f"<tbody>{body}</tbody></table></div>")
    changes = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td></tr>" for a, b in imp.changes) or \
        "<tr><td colspan=2 class='muted'>Nothing was changed on the way in.</td></tr>"
    cleanup = "".join(f"<tr><td><span class='tag t-wn'>{e(a)}</span></td><td>{e(b)}</td><td>{e(c)}</td></tr>"
                      for a, b, c in imp.cleanup) or "<tr><td colspan=3 class='muted'>Nothing to clean up.</td></tr>"
    skipped = "".join(f"<tr><td>{e(t)}</td><td>{len(v)}</td><td>{e('; '.join(f'#{i}: {why}' for i, why in v[:10]))}</td></tr>"
                      for t, v in imp.skipped.items())
    nm = "".join(f"<tr><td><code>{e(t)}</td><td>{n}</td><td>{e(why)}</td></tr>" for t, n, why in not_moved)
    money_rows = {r[1]: r for r in check.rows if r[0] == "Money"}

    def k(label, key):
        r = money_rows.get(key)
        return f"<div class='kpi'><span class='muted'>{label}</span><b>₹{r[3]:,}</b></div>" if r else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Import check · {e(imp.gym_name)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Import check: {e(imp.gym_name)}</h1>
<div class="muted">From FitDesk v1.2.0 · {datetime.now():%d %b %Y, %I:%M %p} · checked as of {today:%d %b %Y}</div>
<div class="banner {'ok-b' if not probs else 'bad-b'}">{'Everything matches: every count, every rupee, every receipt and every member.' if not probs else f'{len(probs)} difference(s) found — see the rows marked Different below.'}</div>
<div class="kpis"><div class="kpi"><span class="muted">Members</span><b>{imp.counts.get('members', 0)}</b></div>
<div class="kpi"><span class="muted">Receipts</span><b>{imp.counts.get('membership_transactions', 0)}</b></div>
{k('Billed', 'Total billed (fee − discount + GST)')}{k('Collected', 'Total collected')}{k('Dues', 'Dues still to collect')}</div>
<div class="card"><b>The new gym</b><div class="muted" style="margin-top:6px">Gym: {e(imp.gym_name)} · Licence key <code>{e(imp.licence.key)}</code> ·
Owner setup code <code>{e(getattr(imp, 'setup_code', ''))}</code> (valid for a limited time; use it on the sign-in page to create the owner login).<br>
Next numbers: member <code>{e(imp.next_codes['member'])}</code>, receipt <code>{e(imp.next_codes['receipt'])}</code>, staff <code>{e(imp.next_codes['staff'])}</code>,
trainer <code>{e(imp.next_codes['trainer'])}</code>.</div></div>
<h2>To clean up (your decision)</h2><div class="card scroll"><table><thead><tr><th>Kind</th><th>What</th><th>Suggestion</th></tr></thead><tbody>{cleanup}</tbody></table></div>
<h2>Changed on the way in</h2><div class="card scroll"><table><thead><tr><th>What</th><th>Detail</th></tr></thead><tbody>{changes}</tbody></table></div>
{''.join(areas)}
<h2>Not moved (stays in v1.2.0)</h2><div class="card scroll"><table><thead><tr><th>Table</th><th>Rows</th><th>Why</th></tr></thead><tbody>{nm or '<tr><td colspan=3 class=muted>—</td></tr>'}</tbody></table></div>
{f"<h2>Skipped rows</h2><div class='card scroll'><table><thead><tr><th>Table</th><th>Rows</th><th>Why</th></tr></thead><tbody>{skipped}</tbody></table></div>" if skipped else ''}
<p class="muted" style="margin-top:24px">Source: <code>{e(str(source_path))}</code> · SHA-256 <code>{sha[:16]}…</code>. The source file was only read, never changed.
This report contains members' personal details: keep it on this PC.</p>
</div></body></html>"""
