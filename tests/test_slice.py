import pytest

from backend.app.services import documents, llm


def make_pdf(pages: list[str]) -> bytes:
    """Build a minimal valid text PDF, one page per string (no PDF library needed)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


LECTURE = make_pdf([
    "Encapsulation hides internal state behind methods of a class.",
    "Polymorphism lets one interface call different implementations through virtual functions.",
])


def test_chunks_keep_page_numbers_and_overlap():
    long_page = "x" * 2500
    chunks = documents.chunk_pages(["", long_page, "short"])
    assert [p for p, _ in chunks] == [2, 2, 2, 3]  # empty page 1 skipped
    assert len(chunks[0][1]) == documents.CHUNK_CHARS
    assert chunks[-1] == (3, "short")


def test_extract_pages_and_bad_pdf():
    assert documents.extract_pages(LECTURE)[1].startswith("Polymorphism")
    with pytest.raises(documents.BadDocument):
        documents.extract_pages(b"%PDF-1.4 garbage")


def test_upload_and_grounded_chat(client, monkeypatch):
    prompts = []
    monkeypatch.setattr(llm, "generate", lambda p: prompts.append(p) or "Polymorphism uses virtual functions [1].")

    course = client.post("/api/courses", json={"name": "OOP"}).json()
    up = client.post(f"/api/courses/{course['id']}/documents", files={"file": ("oop.pdf", LECTURE, "application/pdf")})
    assert up.status_code == 201, up.text
    assert up.json()["page_count"] == 2

    r = client.post("/api/chat", json={"course_id": course["id"], "question": "What is polymorphism?"}).json()
    assert r["answer"].startswith("Polymorphism")
    assert r["sources"][0]["page"] == 2  # real local embedding ranks the right page first
    assert "Use ONLY the sources" in prompts[0]


def test_chat_survives_llm_outage_and_rejects_non_pdf(client, monkeypatch):
    def down(_):
        raise llm.LLMError("quota reached")

    monkeypatch.setattr(llm, "generate", down)
    cid = client.post("/api/courses", json={"name": "OOP"}).json()["id"]
    assert client.post(f"/api/courses/{cid}/documents", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 400
    client.post(f"/api/courses/{cid}/documents", files={"file": ("oop.pdf", LECTURE, "application/pdf")})

    r = client.post("/api/chat", json={"course_id": cid, "question": "encapsulation"})
    assert r.status_code == 503
    assert r.json()["sources"], "passages still returned when the LLM is down"
    assert client.post("/api/chat", json={"course_id": 999, "question": "x"}).status_code == 404
