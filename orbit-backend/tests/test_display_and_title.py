from app.models.schemas import EducationLevel, FactCategory, ProposedFact


def test_school_display_value_includes_education_level():
    p = ProposedFact(
        category=FactCategory.SCHOOL,
        value="Carnegie Mellon",
        education_level=EducationLevel.UNDERGRAD,
        confidence=0.9,
    )
    assert p.display_value == "Carnegie Mellon (undergraduate)"


def test_school_display_value_alumni():
    p = ProposedFact(
        category=FactCategory.SCHOOL,
        value="CMU",
        education_level=EducationLevel.ALUMNI,
        confidence=0.9,
    )
    assert "alumni" in p.display_value.lower()


def test_job_display_value_unchanged():
    p = ProposedFact(
        category=FactCategory.JOB,
        value="Teaching Assistant",
        confidence=0.9,
    )
    assert p.display_value == "Teaching Assistant"


def test_context_fallback_title_truncates():
    from app.pipeline.context_extraction import _fallback_title

    long = "A" * 100 + ". more"
    assert len(_fallback_title(long)) <= 60
