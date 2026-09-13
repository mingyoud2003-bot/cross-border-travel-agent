from fastapi import FastAPI
from fastapi.testclient import TestClient

from tripflow_agent import LocationCandidate, StayCandidate, TransportCandidate
from tripflow_api import build_tripflow_router
from tripflow_imports import DocumentExtraction
from tripflow_service import TripFlowService


class FakeDocumentExtractor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract(self, *, filename, mime_type, data, instructions=""):
        self.calls.append(filename)
        assert mime_type == "application/pdf"
        assert data.startswith(b"%PDF-")
        return DocumentExtraction(
            transports=[
                TransportCandidate(
                    mode="train",
                    operator="Deutsche Bahn",
                    service_number="ICE 105",
                    origin=LocationCandidate(
                        name="Berlin Hbf", city="Berlin", timezone="Europe/Berlin"
                    ),
                    destination=LocationCandidate(
                        name="Köln Hbf", city="Cologne", timezone="Europe/Berlin"
                    ),
                    departure_at="2026-10-26T08:45:00+01:00",
                    arrival_at="2026-10-26T13:12:00+01:00",
                    source_page=1,
                    source_excerpt="ICE 105 Berlin Hbf to Köln Hbf",
                )
            ],
            stays=[
                StayCandidate(
                    property_name="Hotel One",
                    city="Paris",
                    check_in="2026-10-26",
                    source_page=2,
                    source_excerpt="Hotel One Paris, check-in 26 October",
                    missing_fields=["check_out"],
                )
            ],
        )


def import_client():
    extractor = FakeDocumentExtractor()
    app = FastAPI()
    app.include_router(
        build_tripflow_router(
            TripFlowService(), document_extractor=extractor
        )
    )
    return TestClient(app), extractor


def create_trip(api: TestClient) -> dict:
    return api.post("/api/trips", json={"title": "Imported journey"}).json()


def upload_pdf(api: TestClient, trip_id: str):
    return api.post(
        f"/api/trips/{trip_id}/imports",
        data={"instructions": "This is my Europe trip"},
        files=[("files", ("bookings.pdf", b"%PDF-1.7 fake fixture", "application/pdf"))],
    )


def test_batch_upload_extracts_multiple_candidates_without_mutating_trip():
    api, extractor = import_client()
    trip = create_trip(api)

    response = upload_pdf(api, trip["id"])

    assert response.status_code == 201
    batch = response.json()
    assert batch["status"] == "completed"
    assert len(batch["documents"]) == 1
    assert len(batch["candidates"]) == 2
    assert batch["candidates"][0]["source_page"] == 1
    assert batch["candidates"][1]["missing_fields"] == ["check_out"]
    assert extractor.calls == ["bookings.pdf"]
    assert api.get(f"/api/trips/{trip['id']}").json()["reservations"] == []


def test_complete_candidate_requires_explicit_confirmation_and_keeps_pdf_provenance():
    api, _ = import_client()
    trip = create_trip(api)
    batch = upload_pdf(api, trip["id"]).json()
    candidate = batch["candidates"][0]

    response = api.post(
        f"/api/trips/{trip['id']}/imports/{batch['id']}/candidates/{candidate['id']}/confirm",
        headers={"If-Match": "1"},
        json={},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["batch"]["candidates"][0]["review_status"] == "confirmed"
    reservation = result["trip"]["reservations"][0]
    assert reservation["origin"]["city"] == "柏林"
    assert reservation["destination"]["city"] == "科隆"
    assert reservation["provenance"]["departure_at"]["source_type"] == "pdf"
    assert reservation["provenance"]["departure_at"]["source_id"] == batch["documents"][0]["id"]
    assert reservation["provenance"]["origin.timezone"]["source_type"] == "catalog"
    assert reservation["provenance"]["origin.timezone"]["source_id"] == "DEBER"
    assert "第 1 页" in reservation["provenance"]["departure_at"]["source_excerpt"]


def test_incomplete_candidate_must_be_completed_then_retains_image_or_pdf_source():
    api, _ = import_client()
    trip = create_trip(api)
    batch = upload_pdf(api, trip["id"]).json()
    candidate = batch["candidates"][1]
    path = f"/api/trips/{trip['id']}/imports/{batch['id']}/candidates/{candidate['id']}/confirm"

    rejected = api.post(path, headers={"If-Match": "1"}, json={})
    completed = api.post(
        path,
        headers={"If-Match": "1"},
        json={
            "stay": {
                "property_name": "Hotel One",
                "city": "Paris",
                "check_in": "2026-10-26",
                "check_out": "2026-10-28",
                "source": {"source_type": "provider", "source_id": "client-spoof"},
            }
        },
    )

    assert rejected.status_code == 422
    assert "补全" in rejected.json()["detail"]
    assert completed.status_code == 200
    provenance = completed.json()["trip"]["reservations"][0]["provenance"]["check_out"]
    assert provenance["source_type"] == "form"
    assert provenance["source_id"] == candidate["id"]
    property_source = completed.json()["trip"]["reservations"][0]["provenance"][
        "property_name"
    ]
    assert property_source["source_type"] == "pdf"
    assert property_source["source_id"] == batch["documents"][0]["id"]


def test_identical_files_are_not_sent_twice_to_model():
    api, extractor = import_client()
    trip = create_trip(api)
    content = b"%PDF-1.7 same file"

    response = api.post(
        f"/api/trips/{trip['id']}/imports",
        files=[
            ("files", ("one.pdf", content, "application/pdf")),
            ("files", ("copy.pdf", content, "application/pdf")),
        ],
    )

    assert response.status_code == 201
    assert [item["status"] for item in response.json()["documents"]] == ["parsed", "duplicate"]
    assert extractor.calls == ["one.pdf"]


def test_invalid_file_is_isolated_as_failed_batch_without_model_call():
    api, extractor = import_client()
    trip = create_trip(api)

    response = api.post(
        f"/api/trips/{trip['id']}/imports",
        files=[("files", ("fake.pdf", b"not really pdf", "application/pdf"))],
    )

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["documents"][0]["status"] == "failed"
    assert response.json()["candidates"] == []
    assert extractor.calls == []


def test_import_candidate_write_honors_trip_version():
    api, _ = import_client()
    trip = create_trip(api)
    batch = upload_pdf(api, trip["id"]).json()
    candidate = batch["candidates"][0]

    response = api.post(
        f"/api/trips/{trip['id']}/imports/{batch['id']}/candidates/{candidate['id']}/confirm",
        headers={"If-Match": "2"},
        json={},
    )

    assert response.status_code == 409
    restored = api.get(f"/api/trips/{trip['id']}/imports/{batch['id']}").json()
    assert restored["candidates"][0]["review_status"] == "pending"
