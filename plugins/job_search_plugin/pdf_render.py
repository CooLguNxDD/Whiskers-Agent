"""
PDF rendering module for the job search plugin.
"""

import io
import html
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors


def render_text_pdf(title: str, body_text: str, contact_header: dict | None = None) -> bytes:
    """Render plain text content into a single-column, ATS-friendly PDF.

    Uses standard Helvetica font. Paragraphs are wrapped on blank lines.
    ``contact_header`` (optional) is a {"name","email","phone","portfolio_url"}
    dict rendered as a compact line above the title — used to bake the
    job-specific portfolio URL into the resume ("bake & send").
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=54,   # 0.75 inch
        rightMargin=54,  # 0.75 inch
        topMargin=54,    # 0.75 inch
        bottomMargin=54, # 0.75 inch
    )

    styles = getSampleStyleSheet()

    # Create custom ParagraphStyles using standard Helvetica
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#111111'),
        spaceAfter=15
    )

    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#222222'),
        spaceAfter=10
    )

    contact_style = ParagraphStyle(
        'ContactHeader',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#444444'),
        spaceAfter=4,
    )

    story = []

    if contact_header:
        parts = []
        for field in ("name", "email", "phone"):
            v = contact_header.get(field)
            if v:
                parts.append(html.escape(str(v)))
        portfolio_url = contact_header.get("portfolio_url")
        if portfolio_url:
            # Escape the URL value only, then wrap the anchor around it — running
            # the already-built anchor string through html.escape() again would
            # turn <a href=...> into a literal &lt;a href=...&gt; in the PDF.
            escaped_href = html.escape(str(portfolio_url), quote=True)
            escaped_text = html.escape(str(portfolio_url))
            parts.append(f'<a href="{escaped_href}">{escaped_text}</a>')
        if parts:
            story.append(Paragraph(" &nbsp;|&nbsp; ".join(parts), contact_style))
            story.append(Spacer(1, 8))

    if title:
        story.append(Paragraph(html.escape(title), title_style))
        story.append(Spacer(1, 10))

    # Split body text into paragraphs by blank lines
    text_normalized = body_text.replace('\r\n', '\n')
    chunks = text_normalized.split('\n\n')

    for chunk in chunks:
        c = chunk.strip()
        if c:
            # Replace single newlines inside a paragraph with spaces for normal paragraph flow
            lines = [line.strip() for line in c.split('\n')]
            cleaned_chunk = " ".join(lines)
            story.append(Paragraph(html.escape(cleaned_chunk), body_style))
            story.append(Spacer(1, 6))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
