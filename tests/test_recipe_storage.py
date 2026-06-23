"""Round-trip tests for the Phase-1 storage layer (SeedRow,
RecipeRow, RecipeStepRow + their repository helpers).

In-memory SQLite — no external dependencies. Confirms the
dataclass ↔ SQL mapping works in both directions.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from advtest_ext.core.recipe import MutationSurface, Recipe, Step
from advtest_ext.core.seed import Seed
from advtest_ext.core.surface import Surface
from advtest_ext.storage.models import Base
from advtest_ext.storage.repository import (
    get_recipe,
    get_seed,
    insert_recipe,
    insert_seed,
    list_recipes,
    list_seeds,
)


def _in_memory_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


# ----------------------------------------------------------------------
# Seed round-trip
# ----------------------------------------------------------------------


def test_insert_and_get_seed() -> None:
    session = _in_memory_session()
    seed = Seed(
        id="seed-1",
        kind="human_chat",
        title="V1 - Persona Forgery",
        source_format="docx_report",
        raw_artefact_path="./red_team.docx",
        taxonomy_hints=["persona_forgery"],
        submitted_by="red_teamer_a",
        payload={"extracted_turns": [{"index": 1, "actor": "B"}]},
    )
    insert_seed(session, seed=seed)
    session.commit()

    row = get_seed(session, "seed-1")
    assert row is not None
    assert row.kind == "human_chat"
    assert row.title == "V1 - Persona Forgery"
    assert row.taxonomy_hints == ["persona_forgery"]
    assert row.payload["extracted_turns"][0]["actor"] == "B"


def test_insert_seed_is_idempotent() -> None:
    session = _in_memory_session()
    seed = Seed(
        id="seed-1",
        kind="dataset_entry",
        title="harmbench:HB-0042",
        source_format="harmbench_jsonl",
        raw_artefact_path="x",
        submitted_by="ingest",
        payload={"entry_id": "HB-0042"},
    )
    insert_seed(session, seed=seed)
    session.commit()
    # Re-insert same id — should return existing row, not raise.
    row_again = insert_seed(session, seed=seed)
    session.commit()
    assert row_again.id == "seed-1"
    # And only one row exists.
    assert len(list_seeds(session)) == 1


def test_list_seeds_filters_by_kind() -> None:
    session = _in_memory_session()
    for i, kind in enumerate(["human_chat", "dataset_entry", "human_chat"]):
        insert_seed(
            session,
            seed=Seed(
                id=f"seed-{i}",
                kind=kind,
                title=f"t{i}",
                source_format="docx_report" if kind == "human_chat" else "harmbench_jsonl",
                raw_artefact_path="x",
                submitted_by="x",
            ),
        )
    session.commit()
    assert len(list_seeds(session, kind="human_chat")) == 2
    assert len(list_seeds(session, kind="dataset_entry")) == 1
    assert len(list_seeds(session)) == 3


# ----------------------------------------------------------------------
# Recipe round-trip
# ----------------------------------------------------------------------


def _seed_for_recipe(session: Session, seed_id: str = "seed-x") -> None:
    insert_seed(
        session,
        seed=Seed(
            id=seed_id,
            kind="human_chat",
            title="t",
            source_format="docx_report",
            raw_artefact_path="x",
            submitted_by="x",
        ),
    )


def _surface() -> Surface:
    return Surface(app="docs", region="sidebar", account_role="recipient")


def test_insert_and_get_recipe_with_steps() -> None:
    session = _in_memory_session()
    _seed_for_recipe(session)
    recipe = Recipe(
        id="r-1",
        seed_id="seed-x",
        name="V1 walkthrough",
        content_hash="sha256:abc",
        steps=[
            Step(
                id="plant_email",
                intent="plant authority-claim email",
                surface=_surface(),
                operation="send_email",
                params={"subject": "x", "body": "y"},
            ),
            Step(
                id="open_doc",
                intent="open the carrier doc",
                surface=_surface(),
                operation="open_document",
                params={"document_id": "D1"},
            ),
        ],
        signals=[{"kind": "token_appears", "name": "engineer", "token": "Engineer"}],
        mutation_surface=MutationSurface(
            preserve=["step.plant_email.intent"],
            allowed=[{"step": "plant_email", "fields": ["subject", "body"]}],
        ),
        taxonomy={"attack_class": ["persona_forgery"]},
    )
    insert_recipe(session, recipe=recipe)
    session.commit()

    row = get_recipe(session, "r-1")
    assert row is not None
    assert row.name == "V1 walkthrough"
    assert row.content_hash == "sha256:abc"
    assert len(row.steps) == 2
    # Steps come back in declared order via ORM ``order_by``.
    assert row.steps[0].step_id == "plant_email"
    assert row.steps[1].step_id == "open_doc"
    assert row.steps[0].params == {"subject": "x", "body": "y"}
    # Surface round-trips as a dict.
    assert row.steps[0].surface["app"] == "docs"
    # Mutation surface stored as a plain dict.
    assert row.mutation_surface["preserve"] == ["step.plant_email.intent"]
    # Signals + taxonomy round-trip.
    assert row.signals[0]["token"] == "Engineer"
    assert row.taxonomy["attack_class"] == ["persona_forgery"]


def test_insert_recipe_is_idempotent() -> None:
    session = _in_memory_session()
    _seed_for_recipe(session)
    recipe = Recipe(
        id="r-1",
        seed_id="seed-x",
        name="x",
        content_hash="x",
    )
    insert_recipe(session, recipe=recipe)
    session.commit()
    insert_recipe(session, recipe=recipe)
    session.commit()
    assert len(list_recipes(session)) == 1


def test_list_recipes_filters_by_seed_and_state() -> None:
    session = _in_memory_session()
    _seed_for_recipe(session, "seed-a")
    _seed_for_recipe(session, "seed-b")
    # Three recipes, two seeds, mixed draft states.
    for i, (sid, state) in enumerate(
        [
            ("seed-a", "approved"),
            ("seed-a", "draft"),
            ("seed-b", "approved"),
        ]
    ):
        r = Recipe(id=f"r-{i}", seed_id=sid, name="x", content_hash=str(i))
        insert_recipe(session, recipe=r)
        # Set draft_state on the row directly (Recipe dataclass
        # doesn't expose it; storage default is approved).
        get_recipe(session, f"r-{i}").draft_state = state  # type: ignore[union-attr]
    session.commit()

    assert len(list_recipes(session, seed_id="seed-a")) == 2
    assert len(list_recipes(session, seed_id="seed-b")) == 1
    assert len(list_recipes(session, draft_state="approved")) == 2
    assert len(list_recipes(session, draft_state="draft")) == 1
    assert len(list_recipes(session)) == 3


def test_seed_deletion_cascades_to_recipes_and_steps() -> None:
    """SeedRow → RecipeRow → RecipeStepRow chain should delete cleanly
    when the seed is removed. Useful when an operator rejects an
    ingested seed."""
    session = _in_memory_session()
    _seed_for_recipe(session, "doomed")
    recipe = Recipe(
        id="r-doomed",
        seed_id="doomed",
        name="x",
        content_hash="x",
        steps=[
            Step(
                id="s1",
                intent="x",
                surface=_surface(),
                operation="noop",
            ),
        ],
    )
    insert_recipe(session, recipe=recipe)
    session.commit()
    assert get_recipe(session, "r-doomed") is not None

    seed_row = get_seed(session, "doomed")
    session.delete(seed_row)
    session.commit()

    assert get_recipe(session, "r-doomed") is None
    # Step row should also be gone.
    from sqlalchemy import select

    from advtest_ext.storage.models import RecipeStepRow

    remaining = list(session.scalars(select(RecipeStepRow)))
    assert remaining == []
