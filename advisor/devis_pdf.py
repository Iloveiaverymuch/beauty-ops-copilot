"""PDF generator for customer quotes (Devis Provisoire).

Generates a print-ready quotation PDF with mandatory legal disclaimers:
"Important : Ce document constitue une estimation indicative et un devis provisoire.
Une commande définitive doit être passée pour confirmer les prix et stocks finaux."
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
]


def _find_chrome() -> str | None:
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def generate_devis_html(data: dict[str, Any]) -> str:
    sku = str(data.get("sku", "SKU-PROV"))
    name = str(data.get("name", "Produit sélectionné"))
    days = int(data.get("days", 30))
    units = int(data.get("units", 1))
    size_ml = data.get("size_ml", 50)
    total_eur = float(data.get("total_eur", 0.0))
    savings_eur = float(data.get("savings_eur", 0.0))
    usage = data.get("usage_ml_per_day", 1.0)
    unit_price = round(total_eur / max(units, 1), 2)
    
    devis_id = "DEV-" + hashlib.sha256(f"{sku}|{days}|{time.time()}".encode()).hexdigest()[:8].upper()
    now_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    
    eur = lambda v: f"{v:.2f}".replace(".", ",") + " €"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Devis Provisoire — {devis_id}</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 15mm 15mm;
  }}
  * {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    color: #1e293b;
    margin: 0;
    padding: 20px;
    font-size: 11pt;
    line-height: 1.5;
  }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    border-bottom: 2px solid #2d6a4f;
    padding-bottom: 15px;
    margin-bottom: 25px;
  }}
  .brand {{
    font-size: 18pt;
    font-weight: 800;
    color: #2d6a4f;
  }}
  .sub-brand {{
    font-size: 9pt;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .devis-meta {{
    text-align: right;
    font-size: 9.5pt;
    color: #475569;
  }}
  .devis-title {{
    font-size: 14pt;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 4px;
  }}
  .badge-prov {{
    display: inline-block;
    background: #fef3c7;
    border: 1px solid #fde68a;
    color: #b45309;
    font-size: 8pt;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    text-transform: uppercase;
  }}
  .info-box {{
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 25px;
    display: flex;
    justify-content: space-between;
  }}
  .info-col h4 {{
    margin: 0 0 4px;
    font-size: 8.5pt;
    text-transform: uppercase;
    color: #64748b;
  }}
  .info-col p {{
    margin: 0;
    font-weight: 600;
    font-size: 10.5pt;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 25px;
  }}
  th, td {{
    border: 1px solid #e2e8f0;
    padding: 10px 12px;
    text-align: left;
  }}
  th {{
    background: #f1f5f9;
    color: #334155;
    font-size: 9pt;
    text-transform: uppercase;
  }}
  td.num {{ text-align: right; }}
  th.num {{ text-align: right; }}
  .totals {{
    width: 280px;
    margin-left: auto;
    margin-bottom: 40px;
  }}
  .totals table {{
    margin-bottom: 0;
  }}
  .totals td {{
    padding: 6px 10px;
  }}
  .totals tr.final {{
    font-weight: 800;
    font-size: 13pt;
    background: #f0fdf4;
    color: #166534;
  }}
  .disclaimer {{
    border-top: 1.5px solid #cbd5e1;
    padding-top: 15px;
    margin-top: 40px;
    font-size: 8.5pt;
    color: #64748b;
    line-height: 1.5;
    background: #fafaf9;
    padding: 14px 18px;
    border-radius: 6px;
  }}
  .disclaimer strong {{
    color: #b45309;
  }}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="brand">LÉA CONSEIL & RETAIL</div>
    <div class="sub-brand">Plateforme d'estimation & Devis personnalisé</div>
  </div>
  <div class="devis-meta">
    <div class="devis-title">DEVIS PROVISOIRE</div>
    <div>Référence : <b>{devis_id}</b></div>
    <div>Date d'émission : {now_str}</div>
    <div>Validité estimée : 30 jours</div>
    <div style="margin-top:4px;"><span class="badge-prov">Estimation Provisoire</span></div>
  </div>
</div>

<div class="info-box">
  <div class="info-col">
    <h4>Projet Client</h4>
    <p>Routine Soin & Aménagement</p>
  </div>
  <div class="info-col">
    <h4>Durée prévue</h4>
    <p>{days} jours ({usage} ml/jour)</p>
  </div>
  <div class="info-col">
    <h4>Conditionnement</h4>
    <p>{units} flacon(s) de {size_ml} ml</p>
  </div>
</div>

<table>
  <thead>
    <tr>
      <th>Réf. SKU</th>
      <th>Désignation de l'article</th>
      <th class="num">Format</th>
      <th class="num">Quantité</th>
      <th class="num">Prix Unitaire</th>
      <th class="num">Total TTC</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>{sku}</code></td>
      <td><b>{name}</b><br><span style="font-size:8.5pt; color:#64748b;">Estimation volumétrique calculée selon le rythme d'application prescrit</span></td>
      <td class="num">{size_ml} ml</td>
      <td class="num">{units}</td>
      <td class="num">{eur(unit_price)}</td>
      <td class="num"><b>{eur(total_eur)}</b></td>
    </tr>
  </tbody>
</table>

<div class="totals">
  <table>
    <tr>
      <td>Sous-total Brut TTC :</td>
      <td class="num">{eur(total_eur + savings_eur)}</td>
    </tr>
    {"<tr><td>Remise / Économie cure :</td><td class='num' style='color:#16a34a;'>-" + eur(savings_eur) + "</td></tr>" if savings_eur > 0 else ""}
    <tr class="final">
      <td>TOTAL ESTIMÉ :</td>
      <td class="num">{eur(total_eur)}</td>
    </tr>
  </table>
</div>

<div class="disclaimer">
  <strong>Mentions obligatoires &amp; Conditions de validité :</strong><br>
  Ce document est une <strong>estimation indicative et un devis provisoire</strong> calculé par notre conseiller virtuel en fonction des informations fournies.
  <br>
  <strong>Une commande réelle et définitive doit impérativement être validée</strong> sur notre boutique en ligne ou en point de vente pour confirmer la disponibilité des stocks et les tarifs finaux applicables au moment de l'achat. Ce document ne constitue pas une réservation de stock ni une facture.
</div>

</body>
</html>"""


def _generate_fallback_pdf(data: dict[str, Any], dest_path: Path) -> Path:
    """Pure-python fallback to produce a valid PDF when Chrome headless is unavailable or restricted in CI."""
    sku = str(data.get("sku", "SKU-PROV"))
    name = str(data.get("name", "Produit selectionne"))
    days = data.get("days", 30)
    units = data.get("units", 1)
    total_eur = float(data.get("total_eur", 0.0))
    devis_id = "DEV-" + hashlib.sha256(f"{sku}|{days}|{time.time()}".encode()).hexdigest()[:8].upper()

    lines = [
        f"BEAUTY OPS / CONSEILLERE LEA - DEVIS PROVISOIRE ({devis_id})",
        f"Article: {name} (SKU: {sku})",
        f"Duree prevue: {days} jours - Quantite recommandee: {units} unite(s)",
        f"Total TTC estime: {total_eur:.2f} EUR",
        "",
        "Mentions obligatoires & Conditions de validite :",
        "Ce document est une estimation indicative et un devis provisoire.",
        "Une commande reelle et definitive doit imperativement etre validee",
        "sur notre boutique en ligne ou en point de vente pour confirmer",
        "la disponibilite des stocks et les prix finaux applicables.",
    ]

    stream_content = "BT\n/F1 12 Tf\n18 TL\n50 780 Td\n"
    for l in lines:
        safe_l = l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_content += f"({safe_l}) Tj T*\n"
    stream_content += "ET\n"

    stream_bytes = stream_content.encode("latin-1", errors="replace")
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(len(stream_bytes)).encode() + b" >>\nstream\n"
        + stream_bytes +
        b"\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n9\n%%EOF\n"
    )
    dest_path.write_bytes(pdf)
    return dest_path


def generate_devis_pdf(data: dict[str, Any]) -> Path:
    """Renders HTML and converts to a temporary PDF via headless Chrome with sandbox-safe flags and CI fallback."""
    html_content = generate_devis_html(data)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html_content)
        html_path = f.name

    pdf_path = Path(html_path.replace(".html", ".pdf"))
    chrome = _find_chrome()
    if not chrome:
        try:
            os.unlink(html_path)
        except Exception:
            pass
        return _generate_fallback_pdf(data, pdf_path)

    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-software-rasterizer",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        f"file://{html_path}",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if res.returncode != 0 or not pdf_path.exists() or pdf_path.stat().st_size == 0:
            return _generate_fallback_pdf(data, pdf_path)
    except Exception:
        return _generate_fallback_pdf(data, pdf_path)
    finally:
        try:
            os.unlink(html_path)
        except Exception:
            pass

    return pdf_path

