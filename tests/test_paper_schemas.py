import pytest
from pydantic import ValidationError
from sqlalchemy import and_
from sqlalchemy.dialects import mysql

from app.schemas import AdminPaperUpdate
from app.services.paper_queries import visible_paper_conditions


def test_admin_update_rejects_empty_and_null_title() -> None:
    assert AdminPaperUpdate().model_fields_set == set()
    with pytest.raises(ValidationError):
        AdminPaperUpdate(title=None)
    with pytest.raises(ValidationError):
        AdminPaperUpdate(title="   ")


def test_admin_update_rejects_null_authors() -> None:
    with pytest.raises(ValidationError):
        AdminPaperUpdate(authors=None)


def test_admin_update_trims_authors_and_venue_abstract() -> None:
    payload = AdminPaperUpdate(
        authors=[" Alice ", "Bob", "Alice"],
        venue="  ",
        abstract="\n",
    )

    assert payload.authors == ["Alice", "Bob"]
    assert payload.venue is None
    assert payload.abstract is None


def test_admin_update_year_bounds() -> None:
    for year in (1899, 2101):
        with pytest.raises(ValidationError):
            AdminPaperUpdate(year=year)
    assert AdminPaperUpdate(year=1900).year == 1900
    assert AdminPaperUpdate(year=2100).year == 2100
    assert AdminPaperUpdate(year=None).year is None


def test_admin_update_forbids_doi() -> None:
    with pytest.raises(ValidationError):
        AdminPaperUpdate(doi="10.1000/example")


def test_visible_conditions_shape() -> None:
    expression = and_(*visible_paper_conditions())
    sql = str(
        expression.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "papers.status = 'ready'" in sql
    assert "papers.is_archived is false" in sql
