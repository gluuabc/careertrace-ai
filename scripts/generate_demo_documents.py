"""Generate synthetic judge documents committed under demo/."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "demo"
NAVY = colors.HexColor("#16324F")
BLUE = colors.HexColor("#2878B5")
PALE_BLUE = colors.HexColor("#EAF4FB")
INK = colors.HexColor("#263238")
MUTED = colors.HexColor("#607D8B")


def _styles():
    styles = getSampleStyleSheet()
    return {
        "name": ParagraphStyle(
            "Name",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=25,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=3,
        ),
        "tagline": ParagraphStyle(
            "Tagline",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=MUTED,
            spaceAfter=11,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=BLUE,
            spaceBefore=9,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=INK,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=MUTED,
        ),
        "project": ParagraphStyle(
            "Project",
            parent=styles["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=NAVY,
            spaceAfter=4,
        ),
    }


def _document(path: Path, title: str):
    return SimpleDocTemplate(
        str(path),
        pagesize=letter,
        rightMargin=0.62 * inch,
        leftMargin=0.62 * inch,
        topMargin=0.52 * inch,
        bottomMargin=0.52 * inch,
        title=title,
        author="CareerTrace AI synthetic judge demo",
    )


def _white_page(canvas, document):
    canvas.saveState()
    canvas.setFillColor(colors.white)
    canvas.rect(0, 0, document.pagesize[0], document.pagesize[1], fill=1, stroke=0)
    canvas.restoreState()


def _build(path: Path, title: str, story) -> None:
    _document(path, title).build(
        story,
        onFirstPage=_white_page,
        onLaterPages=_white_page,
    )


def _header(story, styles, subtitle: str):
    story.extend(
        [
            Paragraph("Maya Chen", styles["name"]),
            Paragraph(subtitle, styles["tagline"]),
            Table(
                [["maya.chen.demo@example.com", "Boston, MA", "Expected May 2028"]],
                colWidths=[2.7 * inch, 1.8 * inch, 2.1 * inch],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                        ("TEXTCOLOR", (0, 0), (-1, -1), NAVY),
                        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                ),
            ),
        ]
    )


def build_resume() -> Path:
    styles = _styles()
    story = []
    _header(story, styles, "Computer Science Student | Applied AI and Data Systems")
    story.extend(
        [
            Paragraph("EDUCATION", styles["section"]),
            Paragraph(
                "<b>Northstar Institute of Technology</b> - B.S. Computer Science, "
                "expected 2028<br/>Relevant coursework: Data Structures, Database "
                "Systems, Machine Learning, Cloud Computing",
                styles["body"],
            ),
            Paragraph("SKILLS", styles["section"]),
            Paragraph(
                "<b>Languages:</b> Python, SQL, JavaScript, Java &nbsp;&nbsp; "
                "<b>AI/Data:</b> LangGraph, LangChain, pandas, scikit-learn &nbsp;&nbsp; "
                "<b>Cloud/Tools:</b> AWS, Git, Docker, Streamlit",
                styles["body"],
            ),
            Paragraph("EXPERIENCE", styles["section"]),
            KeepTogether(
                [
                    Paragraph(
                        "<b>Student Research Assistant</b> - Northstar AI Lab | "
                        "Sep 2025 - Present",
                        styles["body"],
                    ),
                    Paragraph(
                        "- Evaluated retrieval pipelines on synthetic academic data "
                        "and documented repeatable quality tests.<br/>- Built Python "
                        "tools that reduced experiment setup time by 30 percent.<br/>"
                        "- Presented model evaluation findings to a six-person lab team.",
                        styles["body"],
                    ),
                ]
            ),
            KeepTogether(
                [
                    Paragraph(
                        "<b>Software Engineering Intern</b> - Harbor Learning Labs | "
                        "May 2025 - Aug 2025",
                        styles["body"],
                    ),
                    Paragraph(
                        "- Developed SQL-backed analytics for a synthetic learning "
                        "platform.<br/>- Added automated API tests and improved error "
                        "diagnostics for the student dashboard.",
                        styles["body"],
                    ),
                ]
            ),
            Paragraph("PROJECTS", styles["section"]),
            Paragraph(
                "<b>Campus Opportunity Navigator</b> - Python, LangGraph, AWS<br/>"
                "Created a controlled career workflow that extracts evidence from "
                "student documents, asks for confirmation, and stores profile facts.",
                styles["body"],
            ),
            Paragraph(
                "<b>Study Group Matcher</b> - Python, SQL, Streamlit<br/>"
                "Built a privacy-conscious matching prototype using synthetic student "
                "preferences and explainable ranking rules.",
                styles["body"],
            ),
            Paragraph("LEADERSHIP", styles["section"]),
            Paragraph(
                "Project Lead, Open Source Student Club - coordinated four students, "
                "reviewed pull requests, and ran weekly beginner workshops.",
                styles["body"],
            ),
            Spacer(1, 4),
            Paragraph(
                "Synthetic document created exclusively for the CareerTrace AI judge demo. "
                "It contains no real personal information.",
                styles["small"],
            ),
        ]
    )
    output = OUTPUT_DIR / "Demo_Resume.pdf"
    _build(output, "CareerTrace Demo Resume", story)
    return output


def build_portfolio() -> Path:
    styles = _styles()
    styles["section"].spaceBefore = 5
    styles["section"].spaceAfter = 3
    styles["body"].leading = 11
    styles["body"].spaceAfter = 2
    story = []
    _header(story, styles, "Selected Technical Portfolio | Synthetic Demo")
    story.extend(
        [
            Paragraph("PORTFOLIO OVERVIEW", styles["section"]),
            Paragraph(
                "I build reliable, human-reviewed AI tools for education and career "
                "access. My work emphasizes traceable evidence, deterministic business "
                "rules, and clear user control.",
                styles["body"],
            ),
            Paragraph("01  CAMPUS OPPORTUNITY NAVIGATOR", styles["project"]),
            Table(
                [
                    ["Challenge", "Students had career information spread across resumes, portfolios, and notes."],
                    ["Contribution", "Designed a LangGraph workflow for extraction, validation, confirmation, and SQL persistence."],
                    ["Technology", "Python, LangGraph, LangChain, Amazon Bedrock, AWS S3, Streamlit, SQL"],
                    ["Outcome", "Produced explainable profile summaries while keeping the student in control of every saved fact."],
                ],
                colWidths=[1.15 * inch, 5.45 * inch],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
                        ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
                        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("LEADING", (0, 0), (-1, -1), 11),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7DCEB")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                ),
            ),
            Spacer(1, 8),
            Paragraph("02  STUDY GROUP MATCHER", styles["project"]),
            Table(
                [
                    ["Challenge", "Students needed compatible study partners without exposing private records."],
                    ["Contribution", "Implemented transparent scoring rules, SQL persistence, and a Streamlit review interface."],
                    ["Technology", "Python, SQLAlchemy, SQLite, Streamlit, pytest"],
                    ["Outcome", "Tested the complete prototype with synthetic records and documented privacy boundaries."],
                ],
                colWidths=[1.15 * inch, 5.45 * inch],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
                        ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
                        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("LEADING", (0, 0), (-1, -1), 11),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7DCEB")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                ),
            ),
            Paragraph("ADDITIONAL EVIDENCE", styles["section"]),
            Paragraph(
                "- Led code reviews and release planning for a four-person student team.<br/>"
                "- Wrote evaluation plans for retrieval quality and failure analysis.<br/>"
                "- Interested in machine learning engineering, AI product engineering, "
                "and responsible education technology.",
                styles["body"],
            ),
            Paragraph("NEXT DEVELOPMENT GOALS", styles["section"]),
            Paragraph(
                "Production MLOps, retrieval evaluation, cloud deployment, observability, "
                "and infrastructure as code.",
                styles["body"],
            ),
            Spacer(1, 5),
            Paragraph(
                "Synthetic document created exclusively for the CareerTrace AI judge demo. "
                "All people, organizations, projects, and metrics are fictional.",
                styles["small"],
            ),
        ]
    )
    output = OUTPUT_DIR / "Demo_Portfolio.pdf"
    _build(output, "CareerTrace Demo Portfolio", story)
    return output


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in (build_resume(), build_portfolio()):
        print(path)


if __name__ == "__main__":
    main()
