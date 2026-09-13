from datetime import date

import pytest

from tripflow_agent import LocationCandidate, StayCandidate, TransportCandidate
from tripflow_imports import (
    DocumentExtraction,
    ImportCandidate,
    SourceDocument,
    candidates_from_extraction,
    mark_duplicates,
    normalize_document_extraction,
    stay_input_from_candidate,
    safe_filename,
    transport_input_from_candidate,
    validate_upload,
)


def test_upload_validation_checks_declared_type_and_magic_bytes():
    assert validate_upload("booking.pdf", "application/pdf", b"%PDF-1.7\n") == "application/pdf"
    assert validate_upload("ticket.png", "image/png", b"\x89PNG\r\n\x1a\nrest") == "image/png"

    with pytest.raises(ValueError, match="内容与声明类型不一致"):
        validate_upload("fake.pdf", "application/pdf", b"not a pdf")
    with pytest.raises(ValueError, match="仅支持"):
        validate_upload("booking.txt", "text/plain", b"booking")
    assert safe_filename(r"C:\\Users\\me\\booking.pdf") == "booking.pdf"


def test_document_normalizer_canonicalizes_known_cities_and_recomputes_missing_fields():
    extraction = DocumentExtraction(
        transports=[
            TransportCandidate(
                mode="train",
                operator="Deutsche Bahn",
                service_number="ICE 105",
                origin=LocationCandidate(name="Berlin Hbf", city="Berlin"),
                destination=LocationCandidate(name="Köln Hbf", city="Cologne"),
                departure_at="2026-10-26T08:45:00+01:00",
                arrival_at="2026-10-26T13:12:00+01:00",
                missing_fields=["wrong-model-value"],
            )
        ],
        stays=[
            StayCandidate(
                property_name="Hotel Unknown",
                city="Atlantis",
                check_in="2026-10-26",
                check_out=None,
            )
        ],
    )

    result = normalize_document_extraction(extraction)

    train = result.transports[0]
    assert train.origin.city == "柏林"
    assert train.origin.timezone == "Europe/Berlin"
    assert train.destination.city == "科隆"
    assert train.missing_fields == []
    assert result.stays[0].missing_fields == ["check_out"]


def test_imported_hong_kong_to_los_angeles_flight_gets_distinct_catalog_timezones():
    extraction = DocumentExtraction(
        transports=[
            TransportCandidate(
                mode="flight",
                operator="United Airlines",
                service_number="UA153",
                origin=LocationCandidate(name="香港国际T1", city="香港"),
                destination=LocationCandidate(name="洛杉矶国际T7", city="洛杉矶"),
                departure_at="2026-09-18T12:40:00",
                arrival_at="2026-09-18T11:10:00",
            )
        ]
    )
    document = SourceDocument(
        filename="flight.jpeg",
        mime_type="image/jpeg",
        sha256="b" * 64,
        size_bytes=1200,
        status="parsed",
    )

    normalized = normalize_document_extraction(extraction)
    candidate = candidates_from_extraction(normalized, document)[0]
    confirmed = transport_input_from_candidate(candidate, document.mime_type)

    assert candidate.missing_fields == []
    assert confirmed.origin.timezone == "Asia/Hong_Kong"
    assert confirmed.destination.timezone == "America/Los_Angeles"
    assert confirmed.arrival_at > confirmed.departure_at


def test_imported_san_francisco_flight_forces_impossible_arrival_date_correction():
    extraction = DocumentExtraction(
        transports=[
            TransportCandidate(
                mode="flight",
                operator="United Airlines",
                service_number="UA877",
                origin=LocationCandidate(name="旧金山国际", city="旧金山"),
                destination=LocationCandidate(name="香港国际T1", city="香港"),
                departure_at="2026-09-27T23:30:00",
                arrival_at="2026-09-28T05:00:00",
            )
        ]
    )

    flight = normalize_document_extraction(extraction).transports[0]

    assert flight.origin.city == "旧金山"
    assert flight.origin.timezone == "America/Los_Angeles"
    assert flight.destination.timezone == "Asia/Hong_Kong"
    assert flight.arrival_at is None
    assert flight.missing_fields == ["arrival_at"]


def test_one_document_can_produce_multiple_review_candidates_with_page_provenance():
    document = SourceDocument(
        filename="all-bookings.pdf",
        mime_type="application/pdf",
        sha256="a" * 64,
        size_bytes=1200,
        status="parsed",
    )
    extraction = DocumentExtraction(
        stays=[
            StayCandidate(
                property_name="Hotel One",
                city="Paris",
                check_in="2026-10-26",
                check_out="2026-10-28",
                source_page=2,
                source_excerpt="Hotel One Paris 26-28 Oct",
            ),
            StayCandidate(
                property_name="Hotel Two",
                city="Berlin",
                check_in="2026-10-28",
                check_out="2026-10-30",
                source_page=3,
                source_excerpt="Hotel Two Berlin 28-30 Oct",
            ),
        ]
    )

    candidates = candidates_from_extraction(normalize_document_extraction(extraction), document)

    assert len(candidates) == 2
    assert candidates[0].source_page == 2
    assert candidates[1].source_document_id == document.id
    assert candidates[0].source_excerpt == "Hotel One · 巴黎 · 2026-10-26 → 2026-10-28"


def test_duplicate_candidates_are_flagged_but_not_silently_removed():
    stay = StayCandidate(
        property_name="Hotel One",
        city="巴黎",
        check_in="2026-10-26",
        check_out="2026-10-28",
    )
    candidates = [
        ImportCandidate(
            kind="stay",
            source_document_id="doc_1",
            source_filename="one.pdf",
            stay=stay,
        ),
        ImportCandidate(
            kind="stay",
            source_document_id="doc_2",
            source_filename="two.pdf",
            stay=stay.model_copy(deep=True),
        ),
    ]

    mark_duplicates(candidates)

    assert candidates[0].duplicate_of is None
    assert candidates[1].duplicate_of == candidates[0].id


def test_confirmed_override_keeps_server_owned_document_source():
    candidate = ImportCandidate(
        kind="stay",
        source_document_id="doc_123",
        source_filename="hotel.png",
        source_page=1,
        source_excerpt="Hotel One Paris",
        stay=StayCandidate(property_name="Hotel One", city="Paris"),
        missing_fields=["check_in", "check_out"],
    )
    from tripflow_models import SourceInput, StayInput

    user_completion = StayInput(
        property_name="Hotel One",
        city="Paris",
        check_in=date(2026, 10, 26),
        check_out=date(2026, 10, 28),
        source=SourceInput(source_type="provider", source_id="spoofed"),
    )

    result = stay_input_from_candidate(candidate, "image/png", user_completion)

    assert result.source.source_type == "image"
    assert result.source.source_id == "doc_123"
    assert "hotel.png" in result.source.source_excerpt
