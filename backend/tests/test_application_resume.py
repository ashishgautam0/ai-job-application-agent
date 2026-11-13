"""Regression coverage for the backend-owned Settings resume attachment."""
import hashlib
from io import BytesIO
import pathlib
import sys
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from starlette.datastructures import Headers

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))

from backend.app.routers import profile as routes
import profile as profile_data


class Bucket:
    def __init__(self):
        self.files = {}

    def upload(self, path, data, options):
        if path in self.files and str(options.get("upsert", "false")).lower() != "true":
            raise RuntimeError("duplicate")
        self.files[path] = data

    def download(self, path):
        if path not in self.files:
            raise KeyError(path)
        return self.files[path]

    def remove(self, paths):
        for path in paths:
            self.files.pop(path, None)

    def list(self, prefix):
        start = prefix.rstrip("/") + "/"
        names = set()
        for path in self.files:
            if path.startswith(start):
                names.add(path[len(start):].split("/", 1)[0])
        return [{"name": name} for name in sorted(names)]

    def create_signed_url(self, path, expires):
        if path not in self.files:
            raise KeyError(path)
        return {"signedURL": f"https://storage.test/private files/{path}?expires={expires}"}


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bucket = Bucket()

        class Storage:
            def from_(_, name):
                self.assertEqual(name, "private-fixture")
                return self.bucket

        class Client:
            storage = Storage()

        self.client = Client()
        self.storage_patches = [
            patch.object(routes, "_storage_client", return_value=self.client),
            patch.object(routes, "_ensure_pdf_bucket", return_value=None),
            patch.object(routes, "_PDF_BUCKET", "private-fixture"),
        ]
        for item in self.storage_patches:
            item.start()
            self.addCleanup(item.stop)

    def pdf(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 700 Td (" + b"Python developer with project experience. " * 5 + b") Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        out = BytesIO()
        writer.write(out)
        return out.getvalue()

    def upload_file(self, name, raw, content_type="application/pdf"):
        return UploadFile(filename=name, file=BytesIO(raw), headers=Headers({"content-type": content_type}))

    def saved_row(self, raw, filename="new.pdf"):
        digest = hashlib.sha256(raw).hexdigest()
        return {
            "id": 2, "username": "subidh", "version": 2, "source_kind": "pdf",
            "source_filename": filename, "source_sha256": digest,
            "extraction_method": "pdf-profile-v1", "raw_text": "resume text " * 20,
            "extracted_facts": {"facts": {}, "readability": {"status": "readable"}},
            "corrections": {}, "evidence": {}, "readability": {"status": "readable"},
            "status": "pending_review", "created_at": None, "reviewed_at": None,
            "activated_at": None,
        }

    async def test_settings_upload_replaces_old_storage_object(self):
        self.bucket.files["application-resumes/legacy-token/resume.pdf"] = b"old pdf"
        raw = self.pdf()
        saved = self.saved_row(raw)
        with patch.object(routes, "get_latest_profile_snapshot", return_value={"source_sha256": "a" * 64}), \
             patch.object(routes, "create_resume_profile", return_value=saved):
            result = await routes.upload_resume(self.upload_file("new.pdf", raw))

        current = routes._application_path(hashlib.sha256(raw).hexdigest())
        self.assertEqual(result.source_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.bucket.files, {current: raw})

    async def test_invalid_or_failed_replacement_preserves_current_pdf(self):
        old_path = routes._application_path("a" * 64)
        self.bucket.files[old_path] = b"current"
        with self.assertRaises(HTTPException):
            await routes.upload_resume(self.upload_file("bad.pdf", b"not pdf"))
        self.assertEqual(self.bucket.files, {old_path: b"current"})

        raw = self.pdf()
        with patch.object(routes, "get_latest_profile_snapshot", return_value={"source_sha256": "a" * 64}), \
             patch.object(routes, "create_resume_profile", side_effect=RuntimeError("database unavailable")):
            # A database failure surfaces as a 503 with a message the user can
            # act on, not the raw exception: a bare RuntimeError reached the
            # browser as a 500 with no explanation.
            with self.assertRaises(HTTPException) as caught:
                await routes.upload_resume(self.upload_file("new.pdf", raw))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("temporarily unavailable", caught.exception.detail)
        self.assertEqual(self.bucket.files, {old_path: b"current"})

    def test_status_and_download_need_no_browser_token(self):
        raw = self.pdf()
        digest = hashlib.sha256(raw).hexdigest()
        latest = {"source_filename": "latest.pdf", "source_sha256": digest,
                  "version": 4, "status": "active"}
        self.bucket.files[routes._application_path(digest)] = raw
        with patch.object(routes, "get_latest_profile_snapshot", return_value=latest):
            status = routes.application_resume_status()
            response = routes.download_application_resume()
        self.assertTrue(status["available"])
        self.assertEqual(status["filename"], "latest.pdf")
        self.assertIsNone(status["size"])
        self.assertEqual(response.status_code, 307)
        self.assertIn("https://storage.test/private%20files/", response.headers["location"])

    def test_today_todo_upload_endpoint_is_removed(self):
        routes_by_path = {(route.path, tuple(sorted(route.methods or []))) for route in routes.router.routes}
        self.assertNotIn(("/application-resume", ("POST",)), routes_by_path)
        self.assertIn(("/resume", ("POST",)), routes_by_path)

    def test_today_todo_prompt_and_its_answer_fields_are_gone_from_settings(self):
        """The Today Todo prompt went first; the answers it carried have since
        moved into the Claude Desktop prompt text, so Settings holds neither.

        The Today Todo page itself went with the job scrapers that fed it, so
        neither it nor the Codex apply component it hosted may come back."""
        self.assertFalse(
            (ROOT.parent / "frontend/src/app/(app)/tonight").exists()
        )
        self.assertFalse(
            (ROOT.parent / "frontend/src/components/apply-with-codex.tsx").exists()
        )
        settings = (ROOT.parent / "frontend/src/app/(app)/settings/page.tsx").read_text()
        for gone in ("Today Todo application prompt", "Save Today Todo prompt",
                     "application-prompt-template", "application-prompt-editor",
                     "getRenderedApplicationPrompt"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, settings)
        self.assertFalse(
            (ROOT.parent / "frontend/src/lib/application-prompt-editor.ts").exists()
        )
        # The answers moved out of Settings entirely and into the prompt text.
        for answer in ("submission_authorization", "notice_period", "expected_ctc",
                       "relocation_preference", "Save application answers"):
            with self.subTest(answer=answer):
                self.assertNotIn(answer, settings)
        prompt = (ROOT.parent / "prompts/job-agent-desktop.md").read_text()
        self.assertIn("- Notice period: 15", prompt)
        self.assertIn("- Expected compensation: 700000", prompt)

    def test_profile_cleanup_preserves_rows_referenced_by_audited_drafts(self):
        class Result:
            def __init__(self, data): self.data = data

        class Query:
            def __init__(self, db, table): self.db, self.table, self.operation = db, table, None
            def select(self, *_): self.operation = "select"; return self
            def delete(self): self.operation = "delete"; return self
            def eq(self, *_): return self
            def neq(self, *_): return self
            def in_(self, _, values): self.values = values; return self
            def execute(self):
                if self.operation == "delete":
                    self.db.deleted.extend(self.values)
                    return Result([])
                if self.table == "resume_profiles":
                    return Result([{"id": 1}, {"id": 2}, {"id": 3}])
                return Result([{"resume_profile_id": 2}])

        class DB:
            deleted = []
            def table(self, name): return Query(self, name)

        db = DB()
        with patch.object(profile_data, "_get_client", return_value=db):
            removed = profile_data.prune_obsolete_resume_profiles(4)
        self.assertEqual(removed, 2)
        self.assertEqual(db.deleted, [1, 3])


if __name__ == "__main__":
    unittest.main()
