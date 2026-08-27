"""Generate the small test documents committed under tests/data.

Run manually when fixtures need regenerating (fpdf2 required):

    python scripts/make_test_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

DATA = Path(__file__).resolve().parents[1] / "tests" / "data"

FEVER_TEXT = (
    "Fever Management\n\n"
    "A fever is a temporary increase in body temperature, often caused by an illness. "
    "For adults, a fever is generally defined as a temperature of 38 C (100.4 F) or higher. "
    "Rest and fluids are the first-line supportive care described in this guide.\n\n"
    "When to Seek Help\n\n"
    "Seek immediate medical attention if a fever reaches 40 C (104 F), lasts longer than "
    "three days, or is accompanied by a stiff neck, confusion, or difficulty breathing. "
    "These can be signs of a serious infection that requires professional evaluation.\n\n"
    "Children and Fevers\n\n"
    "For infants younger than three months, any fever of 38 C or higher requires immediate "
    "medical evaluation. For older children, supportive care with fluids and rest is "
    "usually sufficient unless warning signs appear."
)

HYDRATION_TEXT = (
    "Hydration and Dehydration\n\n"
    "Dehydration occurs when the body loses more fluids than it takes in. Common signs "
    "include thirst, dark urine, fatigue, and dizziness. Oral rehydration solutions "
    "contain the right balance of salts and glucose to restore fluid levels quickly.\n\n"
    "Treatment Guidelines\n\n"
    "Mild dehydration can be treated at home with water and electrolyte drinks. Drink "
    "slowly and rest in a cool place. Severe dehydration is a medical emergency that may "
    "require intravenous fluids under professional care."
)


def build_pdf(path: Path, pages: list[list[str]]) -> None:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    for page_number, blocks in enumerate(pages, start=1):
        pdf.add_page()
        if page_number > 1:
            pdf.set_font("Helvetica", "I", 8)
            pdf.cell(0, 6, f"First Aid Handbook - page marker {page_number}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        for block in blocks:
            pdf.multi_cell(0, 6, block)
            pdf.ln(2)
    pdf.output(str(path))


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)

    # Multi-page PDF with a repeated header/footer line and two sections.
    build_pdf(
        DATA / "sample_first_aid.pdf",
        [
            ["Fever Management", FEVER_TEXT],
            ["Hydration and Dehydration", HYDRATION_TEXT],
        ],
    )

    # A PDF with no extractable text (simulates a scanned document).
    empty = FPDF()
    empty.add_page()
    empty.output(str(DATA / "scanned_empty.pdf"))

    (DATA / "burn_care.md").write_text(
        "# Burn Care\n\n"
        "Cool the burn under cool running water for at least 20 minutes.\n\n"
        "## When to call emergency services\n\n"
        "Call emergency services for burns larger than the person's hand, "
        "burns on the face, or burns that appear white or charred.\n",
        encoding="utf-8",
    )

    (DATA / "first_aid_faq.json").write_text(
        '{\n  "faq": [\n    {"question": "How long should you cool a burn?", '
        '"answer": "At least 20 minutes under cool running water."},\n'
        '    {"question": "What helps mild dehydration?", '
        '"answer": "Water and electrolyte drinks, sipped slowly while resting."}\n  ]\n}\n',
        encoding="utf-8",
    )

    print(f"Fixtures written to {DATA}")


if __name__ == "__main__":
    main()
