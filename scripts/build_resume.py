from pathlib import Path
from shutil import copyfile

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "Crystal-Hollis-Resume.pdf"
WEB_COPY = ROOT / "assets" / "resume.pdf"

NAVY = colors.HexColor("#022c52")
TEAL = colors.HexColor("#1cbb9d")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#465564")
LIGHT = colors.HexColor("#d7dee8")


def register_fonts():
    font_root = Path("/System/Library/Fonts")
    candidates = {
        "ResumeSans": font_root / "Helvetica.ttc",
        "ResumeSerif": font_root / "Times.ttc",
    }
    for name, path in candidates.items():
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
            except Exception:
                pass


def bullet(text, style):
    return Paragraph(f"<font color='#1cbb9d'>•</font>&nbsp; {text}", style)


def section_header(text, style):
    return KeepTogether([
        Paragraph(text.upper(), style),
        HRFlowable(width="100%", thickness=0.8, color=LIGHT, spaceBefore=1, spaceAfter=4),
    ])


def role_header(organization, location, role, timing, styles):
    left = Paragraph(f"<b>{organization}</b> <font color='#465564'>| {location}</font>", styles["role"])
    right = Paragraph(f"<b>{role}</b> <font color='#465564'>| {timing}</font>", styles["role_right"])
    table = Table([[left, right]], colWidths=[3.4 * inch, 3.9 * inch])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return table


def build_resume():
    register_fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    sans = "ResumeSans" if "ResumeSans" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    serif = "ResumeSerif" if "ResumeSerif" in pdfmetrics.getRegisteredFontNames() else "Times-Roman"
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="name", fontName=serif, fontSize=24, leading=25, textColor=NAVY, alignment=TA_CENTER, spaceAfter=2))
    styles.add(ParagraphStyle(name="headline", fontName=sans, fontSize=10.2, leading=12, textColor=TEAL, alignment=TA_CENTER, spaceAfter=3))
    styles.add(ParagraphStyle(name="contact", fontName=sans, fontSize=7.9, leading=9, textColor=MUTED, alignment=TA_CENTER, spaceAfter=5))
    styles.add(ParagraphStyle(name="section", fontName=sans, fontSize=9.2, leading=10, textColor=NAVY, spaceBefore=2, spaceAfter=0))
    styles.add(ParagraphStyle(name="body_small", fontName=sans, fontSize=7.75, leading=9.35, textColor=INK, spaceAfter=1.3))
    styles.add(ParagraphStyle(name="role", fontName=sans, fontSize=8.2, leading=9.5, textColor=NAVY))
    styles.add(ParagraphStyle(name="role_right", fontName=sans, fontSize=8.2, leading=9.5, textColor=NAVY, alignment=2))
    styles.add(ParagraphStyle(name="label", fontName=sans, fontSize=7.7, leading=9.1, textColor=NAVY))

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=letter,
        leftMargin=0.48 * inch,
        rightMargin=0.48 * inch,
        topMargin=0.38 * inch,
        bottomMargin=0.34 * inch,
        title="Crystal Hollis Resume",
        author="Crystal Hollis",
        subject="Data Analyst, AI and Automation, Enterprise Systems",
    )

    story = [
        Paragraph("CRYSTAL HOLLIS", styles["name"]),
        Paragraph("DATA ANALYST | AI &amp; AUTOMATION | ENTERPRISE SYSTEMS", styles["headline"]),
        Paragraph(
            "Dallas, TX&nbsp;&nbsp;|&nbsp;&nbsp;(940) 268-9701&nbsp;&nbsp;|&nbsp;&nbsp;"
            "<link href='mailto:crystaljhollis@gmail.com' color='#022c52'>crystaljhollis@gmail.com</link>&nbsp;&nbsp;|&nbsp;&nbsp;"
            "<link href='https://crystaljhollis.com' color='#022c52'>crystaljhollis.com</link>&nbsp;&nbsp;|&nbsp;&nbsp;"
            "<link href='https://linkedin.com/in/crystaljhollis' color='#022c52'>linkedin.com/in/crystaljhollis</link>",
            styles["contact"],
        ),
        section_header("Profile", styles["section"]),
        Paragraph(
            "Data analyst and automation professional connecting Python, R, GIS, enterprise data quality, and technical communication. "
            "Builds reliable workflows for complex operational information, documents limitations, and translates results for technical and nontechnical partners.",
            styles["body_small"],
        ),
        section_header("Technical Skills", styles["section"]),
    ]

    skills = Table([
        [Paragraph("<b>Languages</b>", styles["label"]), Paragraph("Python, R, SQL concepts, MATLAB, Kotlin, HTML/CSS", styles["body_small"])],
        [Paragraph("<b>Data &amp; ML</b>", styles["label"]), Paragraph("pandas, NumPy, Matplotlib, Jupyter, AutoGluon/AutoML, exploratory analysis, data quality", styles["body_small"])],
        [Paragraph("<b>Systems &amp; Tools</b>", styles["label"]), Paragraph("Workday Student, Ad Astra, Colleague, SharePoint, ArcGIS Pro/ArcPy, Excel, GitHub, Adobe Creative Suite", styles["body_small"])],
    ], colWidths=[1.1 * inch, 6.2 * inch])
    skills.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.extend([skills, section_header("Experience", styles["section"])])

    story.extend([
        role_header("DALLAS COLLEGE", "Dallas, TX", "Schedule Data Analyst", "Dec 2025 - Present", styles),
        bullet("Analyze and reconcile districtwide academic scheduling data across Workday Student, Ad Astra, Colleague, SharePoint, and Excel; validate course, room, and faculty assignments.", styles["body_small"]),
        bullet("Investigate root causes and exceptions, document audit-ready evidence, and coordinate issue resolution across campuses.", styles["body_small"]),
        Spacer(1, 1.5),
        role_header("SOUTHERN METHODIST UNIVERSITY", "Dallas, TX", "Freelance Post-Production Specialist &amp; Metadata Analyst", "2018 - Present", styles),
        bullet("Conduct post-production and metadata workflows supporting an archive of more than 2,300 video jobs.", styles["body_small"]),
        bullet("Built Python utilities for storage analysis, file renaming, shot-list generation, and metadata support; analyzed more than 120 TB across 302,933 folders.", styles["body_small"]),
        Spacer(1, 1.5),
        role_header("MAGNA MEDIA ARTS LLC", "Dallas, TX", "Managing Owner", "2020 - Present", styles),
        bullet("Led client multimedia production and edited more than 100 hours of video while managing requirements, budgets, invoicing, and delivery.", styles["body_small"]),
        section_header("Selected Projects", styles["section"]),
    ])

    projects = Table([
        [Paragraph("<b>Enterprise Media Storage Analysis</b>", styles["label"]), Paragraph("Python pipeline quantified 120+ TB across 302,933 folders and supported a 10-14 TB annual planning estimate.", styles["body_small"])],
        [Paragraph("<b>Hope Supply Geocoding Pipeline</b>", styles["label"]), Paragraph("Python and ArcPy workflow prepared 68 nonprofit partner locations for mapping with confidence-based manual review.", styles["body_small"])],
        [Paragraph("<b>TSA Claims Analysis</b>", styles["label"]), Paragraph("R analysis of 94,848 public records identified a 24.4% approval rate and documented highly skewed claim values.", styles["body_small"])],
        [Paragraph("<b>Reproductive Healthcare Research</b>", styles["label"]), Paragraph("Exploratory maternal mortality analysis paired with an intersectional research paper and explicit modeling limitations.", styles["body_small"])],
    ], colWidths=[2.15 * inch, 5.15 * inch])
    projects.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.extend([projects, section_header("Education & Credentials", styles["section"])])

    education = Table([
        [Paragraph("<b>Dallas College</b>", styles["label"]), Paragraph("Computer Information Technology Python Developer Level 1, May 2025; coursework in calculus, linear algebra, discrete mathematics, probability and statistics, data mining and machine learning, databases, GIS, and public health; Data Structures and Algorithms in progress", styles["body_small"])],
        [Paragraph("<b>University of North Texas</b>", styles["label"]), Paragraph("M.A. Interdisciplinary Studies, Interactive/Virtual/Digital Communication, 2015; B.A. Radio/Television/Film, Management minor, 2013", styles["body_small"])],
        [Paragraph("<b>Selected Credentials</b>", styles["label"]), Paragraph("AWS Academy Machine Learning Through Application; Google IT Automation with Python; Data Ethics: Managing Your Private Customer Data; Adult Mental Health First Aid", styles["body_small"])],
    ], colWidths=[1.65 * inch, 5.65 * inch])
    education.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story.append(education)

    doc.build(story)
    copyfile(OUTPUT, WEB_COPY)


if __name__ == "__main__":
    build_resume()
