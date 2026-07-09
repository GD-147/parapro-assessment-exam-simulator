#!/usr/bin/env python3
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TXT_DIR = ROOT / "imports" / "parapro_assessment_exams" / "txt"
PDF_IMPORT_DIR = ROOT / "imports" / "parapro_assessment_exams" / "pdf"
DATA_DIR = ROOT / "packs" / "parapro-assessment" / "data"
PDF_OUT_DIR = ROOT / "packs" / "parapro-assessment" / "pdf"
CONFIG_PATH = ROOT / "packs" / "parapro-assessment" / "config.json"

ID_RE = re.compile(r"^PARAPRO(\d+)-(\d{3})$")
KEY_RE = re.compile(
    r"^(PARAPRO(\d+)-(\d{3}))\s+—\s+Correct:\s*([A-D])\s+—\s+Correct Answer:\s*(.*?)\s+—\s+Explanation:\s*(.*)$"
)

EXPECTED_TOTAL = 90
EXPECTED_RANGES = {
    "Reading": range(1, 31),
    "Mathematics": range(31, 61),
    "Writing": range(61, 91),
}


def fail(msg):
    print(f"IMPORT ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read_text(path):
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def clean(s):
    return str(s).strip()


def parse_question_blocks(question_text, source_name):
    lines = question_text.splitlines()
    blocks = []
    current = []

    def is_id_line(line):
        return bool(ID_RE.match(line.strip()))

    for line in lines:
        if is_id_line(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            if current:
                current.append(line)

    if current:
        blocks.append(current)

    questions = {}
    for block in blocks:
        qid = clean(block[0])
        m = ID_RE.match(qid)
        if not m:
            fail(f"{source_name}: invalid question ID line: {qid}")

        exam_no = int(m.group(1))
        item_no = int(m.group(2))

        fields = {
            "id": qid,
            "examNo": exam_no,
            "itemNo": item_no,
            "section": None,
            "type": None,
            "category": None,
            "skill": None,
            "prompt": None,
            "choices": {},
        }

        body = block[1:]

        section_idx = type_idx = category_idx = skill_idx = prompt_idx = None
        choice_idxs = {}

        for i, line in enumerate(body):
            stripped = line.strip()
            if stripped.startswith("Section:"):
                section_idx = i
            elif stripped.startswith("Type:"):
                type_idx = i
            elif stripped.startswith("Category:"):
                category_idx = i
            elif stripped.startswith("Skill:"):
                skill_idx = i
            elif stripped == "Prompt:" or stripped.startswith("Prompt:"):
                prompt_idx = i
            elif re.match(r"^[A-D]\)\s*", stripped):
                choice_idxs[stripped[0]] = i

        missing = []
        for name, idx in [
            ("Section", section_idx),
            ("Type", type_idx),
            ("Category", category_idx),
            ("Skill", skill_idx),
            ("Prompt", prompt_idx),
        ]:
            if idx is None:
                missing.append(name)
        for letter in ["A", "B", "C", "D"]:
            if letter not in choice_idxs:
                missing.append(f"{letter})")
        if missing:
            fail(f"{source_name}: {qid} missing required field(s): {', '.join(missing)}")

        fields["section"] = clean(body[section_idx].split(":", 1)[1])
        fields["type"] = clean(body[type_idx].split(":", 1)[1]).lower()
        fields["category"] = clean(body[category_idx].split(":", 1)[1])
        fields["skill"] = clean(body[skill_idx].split(":", 1)[1])

        if fields["section"].lower() != "full":
            fail(f"{source_name}: {qid} Section must be Full")
        if fields["type"] != "mcq":
            fail(f"{source_name}: {qid} Type must be mcq")
        if fields["category"] not in EXPECTED_RANGES:
            fail(f"{source_name}: {qid} Category must be Reading, Mathematics, or Writing")

        expected_range = EXPECTED_RANGES[fields["category"]]
        if item_no not in expected_range:
            fail(f"{source_name}: {qid} item number does not match Category {fields['category']}")

        a_idx = choice_idxs["A"]
        b_idx = choice_idxs["B"]
        c_idx = choice_idxs["C"]
        d_idx = choice_idxs["D"]

        if not (prompt_idx < a_idx < b_idx < c_idx < d_idx):
            fail(f"{source_name}: {qid} fields must appear in order Prompt, A, B, C, D")

        prompt_line = body[prompt_idx].strip()
        prompt_parts = []
        if prompt_line.startswith("Prompt:") and prompt_line != "Prompt:":
            prompt_parts.append(prompt_line.split(":", 1)[1].strip())
        prompt_parts.extend(body[prompt_idx + 1:a_idx])
        prompt = "\n".join(prompt_parts).strip()
        if not prompt:
            fail(f"{source_name}: {qid} has empty Prompt")

        fields["prompt"] = prompt

        for letter, start_idx, end_idx in [
            ("A", a_idx, b_idx),
            ("B", b_idx, c_idx),
            ("C", c_idx, d_idx),
            ("D", d_idx, len(body)),
        ]:
            first = body[start_idx].strip()
            first_text = re.sub(r"^[A-D]\)\s*", "", first).strip()
            continuation = [line.strip() for line in body[start_idx + 1:end_idx] if line.strip()]
            choice_text = " ".join([first_text] + continuation).strip()
            if not choice_text:
                fail(f"{source_name}: {qid} has empty choice {letter}")
            fields["choices"][letter] = choice_text

        if qid in questions:
            fail(f"Duplicate question ID found: {qid}")

        questions[qid] = fields

    return questions


def parse_answer_key(key_text, source_name):
    keys = {}
    for raw_line in key_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("PARAPRO"):
            continue

        m = KEY_RE.match(line)
        if not m:
            fail(f"{source_name}: invalid answer key line:\n{line}")

        qid = m.group(1)
        exam_no = int(m.group(2))
        item_no = int(m.group(3))
        correct = m.group(4)
        correct_answer = clean(m.group(5))
        explanation = clean(m.group(6))

        if not explanation:
            fail(f"{source_name}: {qid} answer key missing explanation")

        if qid in keys:
            fail(f"{source_name}: duplicate answer key line for {qid}")

        keys[qid] = {
            "id": qid,
            "examNo": exam_no,
            "itemNo": item_no,
            "correct": correct,
            "correctAnswer": correct_answer,
            "explanation": explanation,
        }

    return keys


def parse_source(path):
    text = read_text(path)
    marker = "PART B — ANSWER KEY + EXPLANATIONS"
    if marker not in text:
        fail(f"{path.name}: missing '{marker}'")

    part_a, part_b = text.split(marker, 1)

    # Drop title and PART A heading if present; parser starts at first ID anyway.
    questions = parse_question_blocks(part_a, path.name)
    keys = parse_answer_key(part_b, path.name)

    if not questions:
        fail(f"{path.name}: no questions found")
    if not keys:
        fail(f"{path.name}: no answer key lines found")

    for qid, q in questions.items():
        if qid not in keys:
            fail(f"{path.name}: missing answer key line for {qid}")
        k = keys[qid]
        if k["correctAnswer"] != q["choices"][k["correct"]]:
            fail(
                f"{path.name}: {qid} Correct Answer text does not match choice {k['correct']}.\n"
                f"Choice text: {q['choices'][k['correct']]}\n"
                f"Key text: {k['correctAnswer']}"
            )

    for qid in keys:
        if qid not in questions:
            fail(f"{path.name}: answer key line exists without question block: {qid}")

    return questions, keys


def build_exam_json(exam_no, questions, keys):
    expected_ids = [f"PARAPRO{exam_no}-{i:03d}" for i in range(1, EXPECTED_TOTAL + 1)]

    missing = [qid for qid in expected_ids if qid not in questions]
    extra = sorted(qid for qid in questions if qid not in expected_ids)

    if missing:
        fail(f"Exam {exam_no}: missing question IDs: {', '.join(missing[:10])}" + (" ..." if len(missing) > 10 else ""))
    if extra:
        fail(f"Exam {exam_no}: unexpected question IDs: {', '.join(extra[:10])}" + (" ..." if len(extra) > 10 else ""))

    out_questions = []
    for qid in expected_ids:
        q = questions[qid]
        k = keys[qid]
        out_questions.append({
            "id": qid,
            "itemType": "mcq_single",
            "type": "mcq",
            "section": "full",
            "category": q["category"],
            "skill": q["skill"],
            "prompt": q["prompt"],
            "choices": q["choices"],
            "correct": k["correct"],
            "correctAnswer": k["correctAnswer"],
            "explanation": k["explanation"],
        })

    return {
        "title": f"ParaPro Assessment Practice Test {exam_no:02d}",
        "section": "full",
        "questions": out_questions,
    }


def update_config(exam_files, printables):
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    cfg["examId"] = "parapro-assessment"
    cfg["brandName"] = "ParaPro Assessment Exam Simulator"
    cfg["theme"] = "dark"
    cfg["logoPath"] = "../packs/parapro-assessment/assets/logo.png?v=1"
    cfg["accessPassword"] = "PARAPROREADY"
    cfg["practiceChunkSize"] = 10
    cfg["nav"] = ["Home", "Exam Simulator", "Printable Practice Exams"]

    cfg["sections"] = [{
        "id": "full",
        "label": "ParaPro Assessment Practice Test",
        "timeMin": 150,
        "timeLabel": "Recommended practice time: 150 minutes",
        "examQuestions": 90,
        "type": "mcq",
        "examFiles": exam_files,
    }]

    cfg["printables"] = printables

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_pdfs_and_build_printables():
    PDF_OUT_DIR.mkdir(parents=True, exist_ok=True)
    printables = []

    for pdf in sorted(PDF_IMPORT_DIR.glob("*.pdf")):
        dest = PDF_OUT_DIR / pdf.name
        shutil.copy2(pdf, dest)

        stem = pdf.stem.lower()
        label_base = pdf.stem.replace("_", " ").replace("-", " ").strip().title()
        m = re.search(r"(\d+)", pdf.stem)
        num = f"{int(m.group(1)):02d}" if m else ""

        if "reading" in stem:
            label = f"ParaPro Assessment Reading Practice Test {num}".strip()
        elif "mathematics" in stem or "math" in stem:
            label = f"ParaPro Assessment Mathematics Practice Test {num}".strip()
        elif "writing" in stem:
            label = f"ParaPro Assessment Writing Practice Test {num}".strip()
        elif m:
            label = f"ParaPro Assessment Practice Test {num}"
        else:
            label = label_base

        printables.append({
            "label": label,
            "file": pdf.name,
        })

    return printables


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    txt_files = sorted(TXT_DIR.glob("*.txt"))

    if not txt_files:
        print("No TXT files found. Config will stay with empty examFiles unless PDFs are present.")

    exams = {}

    for path in txt_files:
        questions, keys = parse_source(path)

        exam_numbers = {q["examNo"] for q in questions.values()} | {k["examNo"] for k in keys.values()}
        if len(exam_numbers) != 1:
            fail(f"{path.name}: a single source file must contain only one exam number")
        exam_no = exam_numbers.pop()

        if exam_no not in exams:
            exams[exam_no] = {"questions": {}, "keys": {}}

        for qid, q in questions.items():
            if qid in exams[exam_no]["questions"]:
                fail(f"Duplicate question ID across files: {qid}")
            exams[exam_no]["questions"][qid] = q

        for qid, k in keys.items():
            if qid in exams[exam_no]["keys"]:
                fail(f"Duplicate answer key ID across files: {qid}")
            exams[exam_no]["keys"][qid] = k

    exam_files = []

    for exam_no in sorted(exams):
        questions = exams[exam_no]["questions"]
        keys = exams[exam_no]["keys"]

        if len(questions) != EXPECTED_TOTAL:
            fail(f"Exam {exam_no}: expected 90 total questions, found {len(questions)}")
        if len(keys) != EXPECTED_TOTAL:
            fail(f"Exam {exam_no}: expected 90 answer key lines, found {len(keys)}")

        data = build_exam_json(exam_no, questions, keys)
        out_name = f"parapro_assessment_exam_{exam_no:02d}.json"
        out_path = DATA_DIR / out_name
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        exam_files.append(out_name)
        print(f"OK: wrote {out_name} ({len(data['questions'])} questions)")

    printables = copy_pdfs_and_build_printables()
    update_config(exam_files, printables)

    print(f"OK: config updated with {len(exam_files)} exam file(s) and {len(printables)} printable PDF(s).")
    if exam_files:
        print("EXAM FILES:")
        for f in exam_files:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
