from __future__ import annotations

from copy import deepcopy

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Competition, Country, Player, Season, SquadMembership, Team
from app.db.session import Base
from app.db.sync_catalog import SyncOptions, main, sync_catalog
from app.core.config import settings


class FakeProvider:
    def __init__(self, *, remaining: int = 50) -> None:
        self.remaining = remaining
        self.team_calls: list[dict] = []
        self.squad_calls: list[str] = []
        self.teams = [
            {
                "team": {
                    "id": "40",
                    "name": "Alianza Lima",
                    "code": "ALI",
                    "country": "Peru",
                    "national": False,
                    "logo": "https://img.test/alianza.png",
                }
            }
        ]
        self.squads = [
            {
                "team": {"id": "40", "name": "Alianza Lima"},
                "players": [
                    {
                        "id": "701",
                        "name": "Jugador Uno",
                        "number": 9,
                        "position": "Attacker",
                        "photo": "https://img.test/701.png",
                    }
                ],
            }
        ]

    def get_status(self) -> dict:
        return {"requests": {"current": 1, "limit_day": 100}}

    @property
    def quota_snapshot(self) -> dict[str, int]:
        return {"remaining": self.remaining, "limit": 100, "cooldown_seconds": 0}

    def can_fetch_optional(self, reserve: int = 10) -> bool:
        return self.remaining > reserve

    def get_teams(self, **kwargs) -> list[dict]:
        self.team_calls.append(kwargs)
        return deepcopy(self.teams)

    def get_squads(self, *, team_id: str) -> list[dict]:
        self.squad_calls.append(team_id)
        self.remaining -= 1
        return deepcopy(self.squads)


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)


def _options(*, dry_run: bool = False) -> SyncOptions:
    return SyncOptions(
        league_id="281",
        season=2026,
        competition_name="Liga 1",
        country="Peru",
        country_code="PER",
        dry_run=dry_run,
    )


def test_catalog_sync_is_idempotent_and_persists_logos_players_and_memberships() -> None:
    factory = _session_factory()
    provider = FakeProvider()

    first = sync_catalog(
        _options(),
        provider=provider,  # type: ignore[arg-type]
        session_factory=factory,
        initialize_schema=False,
    )
    second = sync_catalog(
        _options(),
        provider=provider,  # type: ignore[arg-type]
        session_factory=factory,
        initialize_schema=False,
    )

    assert first.teams_created == 1
    assert first.players_created == 1
    assert first.memberships_created == 1
    assert second.teams_created == 0
    assert second.players_created == 0
    assert second.memberships_created == 0
    assert provider.team_calls == [
        {"league_id": "281", "season": 2026},
        {"league_id": "281", "season": 2026},
    ]
    assert provider.squad_calls == ["40", "40"]

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Country)) == 1
        assert session.scalar(select(func.count()).select_from(Competition)) == 1
        assert session.scalar(select(func.count()).select_from(Season)) == 1
        assert session.scalar(select(func.count()).select_from(Team)) == 1
        assert session.scalar(select(func.count()).select_from(Player)) == 1
        assert session.scalar(select(func.count()).select_from(SquadMembership)) == 1
        team = session.scalar(select(Team))
        player = session.scalar(select(Player))
        membership = session.scalar(select(SquadMembership))
        assert team is not None and team.logo_url == "https://img.test/alianza.png"
        assert player is not None and player.photo_url == "https://img.test/701.png"
        assert membership is not None and membership.shirt_number == 9
        assert membership.position == "Attacker"


def test_catalog_sync_dry_run_rolls_back_and_quota_reserve_skips_squads() -> None:
    factory = _session_factory()
    provider = FakeProvider(remaining=15)

    report = sync_catalog(
        _options(dry_run=True),
        provider=provider,  # type: ignore[arg-type]
        session_factory=factory,
        initialize_schema=False,
    )

    assert report.dry_run is True
    assert report.teams_upserted == 1
    assert report.squads_requested == 0
    assert report.squads_skipped == 1
    assert report.quota_remaining == 15
    assert provider.squad_calls == []
    assert report.warnings == [
        "Plantilla omitida para team_id=40: reserva de cuota alcanzada."
    ]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Country)) == 0
        assert session.scalar(select(func.count()).select_from(Team)) == 0
        assert session.scalar(select(func.count()).select_from(Player)) == 0


def test_catalog_cli_rejects_missing_api_key_without_exposing_credentials(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(settings, "api_football_key", "")

    exit_code = main(
        [
            "--league-id",
            "281",
            "--season",
            "2026",
            "--competition-name",
            "Liga 1",
            "--country",
            "Peru",
            "--country-code",
            "PER",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "API_FOOTBALL_KEY no está configurada" in captured.err
