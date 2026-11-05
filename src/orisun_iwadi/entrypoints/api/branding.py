"""OncoLake branding for the API: the landing page at / and the skinned docs page at /docs.

The landing page is built from the dataset definitions only (no database), so it still loads
during a database outage; its status badge asks /ready from the browser.
"""

from __future__ import annotations

import html
import pathlib
from urllib.parse import urlparse

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from ...domain.dataset import Dataset

BRAND = "OncoLake"
TAGLINE = "Curated public cancer research data, refreshed daily"
STATIC_DIR = pathlib.Path(__file__).parent / "static"

BAR = """
<header class="ol-bar">
  <img src="/static/mark.svg" alt="">
  <span class="ol-name">Onco<span>Lake</span></span>
  <span class="ol-tag">API</span>
  <nav><a href="/">Home</a><a href="/docs">API docs</a><a href="/ready">Status</a></nav>
</header>
"""


def docs_page(openapi_url: str) -> HTMLResponse:
    page = get_swagger_ui_html(
        openapi_url=openapi_url, title=f"{BRAND} API docs",
        swagger_favicon_url="/static/mark.svg",
        swagger_ui_parameters={"persistAuthorization": True, "defaultModelsExpandDepth": -1,
                               "displayRequestDuration": True},
    ).body.decode()
    page = page.replace("</head>", '<link rel="stylesheet" href="/static/docs.css"></head>', 1)
    page = page.replace("<body>", f"<body>{BAR}", 1)
    return HTMLResponse(page)


def _dataset_card(ds: Dataset) -> str:
    host = urlparse(ds.source_url).netloc or ds.source_url
    return f"""
      <article class="card">
        <h3>{html.escape(ds.name)}</h3>
        <p>{html.escape(ds.description)}</p>
        <p class="src">Source: <a href="{html.escape(ds.source_url)}">{html.escape(host)}</a></p>
      </article>"""


def landing_page(datasets: list[Dataset]) -> HTMLResponse:
    cards = "".join(_dataset_card(d) for d in datasets)
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{BRAND}</title>
<link rel="icon" href="/static/mark.svg" type="image/svg+xml">
<style>
  :root {{
    --bg: #F8FAFC; --surface: #FFFFFF; --ink: #0B1F2A; --muted: #475569;
    --line: #E2E8F0; --teal: #0F766E; --teal-soft: #CCFBF1; --amber: #F59E0B;
    --ok: #15803D; --bad: #B91C1C;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #0B1F2A; --surface: #10293A; --ink: #E2F3F1; --muted: #94A3B8;
      --line: #1E3A4C; --teal: #2DD4BF; --teal-soft: #134E4A; --ok: #4ADE80; --bad: #F87171;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink);
         font-family: Inter, "Segoe UI", Helvetica, Arial, sans-serif; line-height: 1.5; }}
  a {{ color: var(--teal); }}
  .wrap {{ max-width: 1040px; margin: 0 auto; padding: 0 24px; }}
  header {{ display: flex; align-items: center; gap: 12px; padding: 20px 0; }}
  header img {{ width: 40px; height: 40px; }}
  .name {{ font-size: 24px; font-weight: 700; letter-spacing: -0.4px; }}
  .name span {{ color: var(--teal); }}
  header nav {{ margin-left: auto; display: flex; gap: 20px; font-size: 15px; }}
  header nav a {{ text-decoration: none; }}
  .hero {{ padding: 48px 0 32px; }}
  .hero h1 {{ font-size: clamp(30px, 5vw, 46px); line-height: 1.1; margin: 0 0 16px;
             letter-spacing: -1px; max-width: 760px; }}
  .hero p {{ color: var(--muted); font-size: 18px; max-width: 680px; margin: 0 0 24px; }}
  .actions {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
  .btn {{ display: inline-block; padding: 10px 18px; border-radius: 8px; font-weight: 600;
         text-decoration: none; border: 1px solid var(--teal); }}
  .btn.primary {{ background: var(--teal); color: var(--bg); }}
  .status {{ display: inline-flex; align-items: center; gap: 8px; font-size: 14px;
            color: var(--muted); margin-left: 8px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--muted); }}
  .status.ok .dot {{ background: var(--ok); }}
  .status.bad .dot {{ background: var(--bad); }}
  h2 {{ font-size: 20px; margin: 40px 0 16px; }}
  .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }}
  .card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
          padding: 18px 20px; }}
  .card h3 {{ margin: 0 0 6px; font-size: 15px; font-family: ui-monospace, Menlo, monospace; }}
  .card p {{ margin: 0 0 8px; color: var(--muted); font-size: 14px; }}
  .card .src {{ margin: 0; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface);
          border: 1px solid var(--line); border-radius: 12px; overflow: hidden; font-size: 14px; }}
  th, td {{ text-align: left; padding: 12px 16px; border-bottom: 1px solid var(--line); }}
  tr:last-child td {{ border-bottom: 0; }}
  th {{ color: var(--muted); font-weight: 600; }}
  td code {{ background: var(--teal-soft); padding: 1px 6px; border-radius: 4px; }}
  footer {{ color: var(--muted); font-size: 13px; padding: 40px 0; }}
  @media (max-width: 640px) {{
    .wrap {{ padding: 0 16px; }}
    header nav {{ gap: 14px; font-size: 14px; }}
    .hero {{ padding: 28px 0 20px; }}
    th, td {{ padding: 10px 12px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <img src="/static/mark.svg" alt="">
    <span class="name">Onco<span>Lake</span></span>
    <nav><a href="/docs">API docs</a><a href="/ready">Status</a></nav>
  </header>

  <section class="hero">
    <h1>{TAGLINE}.</h1>
    <p>OncoLake collects five public cancer datasets every day, checks every record for
       quality, tracks where each one came from, and serves them through a secure API.</p>
    <div class="actions">
      <a class="btn primary" href="/docs">Open the API docs</a>
      <span class="status" id="status"><span class="dot"></span><span>Checking status…</span></span>
    </div>
  </section>

  <h2>Datasets</h2>
  <div class="grid">{cards}
  </div>

  <h2>Access</h2>
  <table>
    <tr><th>Role</th><th>Can use</th></tr>
    <tr><td><code>viewer</code></td><td>Dataset summaries and grouped counts (small groups hidden)</td></tr>
    <tr><td><code>researcher</code></td><td>+ Row-level data, filters, and CSV / Parquet exports</td></tr>
    <tr><td><code>admin</code></td><td>+ Quarantined records and the run log</td></tr>
  </table>

  <footer>{BRAND} · built with Dagster, PostgreSQL, S3 and FastAPI</footer>
</div>
<script>
  // The page itself needs no database; this only reports whether data can be served now.
  const el = document.getElementById("status");
  fetch("/ready", {{cache: "no-store"}})
    .then(r => {{
      el.classList.add(r.ok ? "ok" : "bad");
      el.lastElementChild.textContent = r.ok ? "All systems operational" : "Data temporarily unavailable";
    }})
    .catch(() => {{ el.classList.add("bad"); el.lastElementChild.textContent = "Status unknown"; }});
</script>
</body>
</html>""")
