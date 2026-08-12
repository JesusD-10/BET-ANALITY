from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import time
from zoneinfo import ZoneInfo

import httpx

from app.schemas.matches import H2HMatchItem, MatchSummary


SPORTS_TIMEZONE = ZoneInfo("America/Lima")


class SportmonksAPIError(RuntimeError):
    """Sportmonks returned a successful HTTP response with invalid data."""


class SportmonksProvider:
    """Adapter for the Sportmonks Football API v3.

    Provider IDs remain in their own ``sportmonks-*`` namespace. The API token
    is sent only in the Authorization header and is never embedded in URLs.
    """

    provider_name = "sportmonks"
    _FIXTURE_INCLUDES = "participants;league;state;venue;referees.type"
    _H2H_INCLUDES = "participants;league;state;scores"
    _HISTORY_INCLUDES = "participants;league;state;scores;statistics"
    # Official fixture statistic type IDs used by the betting taxonomy:
    # corners, total shots, fouls, straight reds, yellows, second-yellow reds,
    # and shots on target. Filtering avoids transporting unrelated metrics.
    _HISTORY_STATISTIC_FILTER = "fixtureStatisticTypes:34,42,56,83,84,85,86"
    _MAX_FIXTURE_PAGES = 20

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.sportmonks.com/v3/football",
        timeout: int = 15,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            # Sportmonks v3 expects the token itself as the header value.
            # Keeping it out of the query string prevents it appearing in URLs/logs.
            "Authorization": self.token,
            "Accept": "application/json",
        }

    def _request(
        self,
        endpoint: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = httpx.get(
            url,
            headers=self._headers(),
            params=params,
            timeout=self.timeout if timeout is None else max(0.1, timeout),
        )
        if response.status_code == 204:
            return {"data": [], "pagination": {"has_more": False}}
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise SportmonksAPIError(
                "Sportmonks no devolvió un documento JSON válido."
            ) from exc
        if not isinstance(payload, dict):
            raise SportmonksAPIError(
                "Sportmonks devolvió una respuesta con formato inesperado."
            )
        return payload

    def list_fixtures(
        self,
        match_date: date | None = None,
        *,
        timeout: float | None = None,
    ) -> list[MatchSummary]:
        selected_date = match_date or datetime.now(SPORTS_TIMEZONE).date()
        request_budget = float(self.timeout if timeout is None else timeout)
        deadline = time.monotonic() + max(0.1, request_budget)
        endpoint = f"fixtures/date/{selected_date.isoformat()}"
        page = 1
        fixtures: list[MatchSummary] = []
        seen_ids: set[str] = set()

        while page <= self._MAX_FIXTURE_PAGES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise httpx.TimeoutException(
                    "Sportmonks excedió el presupuesto de la agenda."
                )
            payload = self._request(
                endpoint,
                params={
                    "include": self._FIXTURE_INCLUDES,
                    "timezone": SPORTS_TIMEZONE.key,
                    "per_page": 50,
                    "page": page,
                },
                timeout=remaining,
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise SportmonksAPIError(
                    "Sportmonks devolvió una agenda sin una lista de partidos válida."
                )
            for item in data:
                if not isinstance(item, dict):
                    raise SportmonksAPIError(
                        "Sportmonks devolvió un partido con formato inesperado."
                    )
                # Placeholder fixtures intentionally lack stable participants
                # or kickoff data and must not invalidate the rest of the page.
                if item.get("placeholder") is True:
                    continue
                match = self._to_match_summary(item)
                if match.id not in seen_ids:
                    seen_ids.add(match.id)
                    fixtures.append(match)

            pagination = payload.get("pagination") or {}
            if not isinstance(pagination, dict):
                raise SportmonksAPIError(
                    "Sportmonks devolvió metadatos de paginación inválidos."
                )
            if not bool(pagination.get("has_more")):
                return fixtures
            page += 1

        raise SportmonksAPIError(
            "Sportmonks excedió el límite defensivo de páginas para una fecha."
        )

    def get_fixture(self, fixture_id: str) -> MatchSummary | None:
        clean_id = fixture_id.removeprefix("sportmonks-")
        if not clean_id.isdigit():
            return None
        endpoint = f"fixtures/{clean_id}"
        try:
            payload = self._request(
                endpoint,
                params={
                    "include": self._FIXTURE_INCLUDES,
                    "timezone": SPORTS_TIMEZONE.key,
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        data = payload.get("data")
        if data is None or data == []:
            return None
        if not isinstance(data, dict):
            raise SportmonksAPIError(
                "Sportmonks devolvió un detalle de partido con formato inesperado."
            )
        return self._to_match_summary(data)

    def get_head_to_head(
        self,
        team1_id: str,
        team2_id: str,
        limit: int = 5,
    ) -> list[H2HMatchItem]:
        if not str(team1_id).isdigit() or not str(team2_id).isdigit():
            return []
        bounded_limit = max(1, min(int(limit), 10))
        payload = self._request(
            f"fixtures/head-to-head/{team1_id}/{team2_id}",
            params={
                "include": self._H2H_INCLUDES,
                "timezone": SPORTS_TIMEZONE.key,
                "order": "desc",
                "per_page": 50,
            },
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise SportmonksAPIError(
                "Sportmonks devolvió un historial H2H con formato inesperado."
            )
        return self.normalize_history(data, bounded_limit)

    def get_team_last_matches(self, team_id: str, limit: int = 5) -> list[dict]:
        if not str(team_id).isdigit():
            return []
        bounded_limit = max(1, min(int(limit), 5))
        today = datetime.now(SPORTS_TIMEZONE).date()
        # The endpoint allows at most 100 calendar days. A 99-day delta is
        # an inclusive 100-day window.
        from_date = today - timedelta(days=99)
        payload = self._request(
            (
                f"fixtures/between/{from_date.isoformat()}/"
                f"{today.isoformat()}/{team_id}"
            ),
            params={
                "include": self._HISTORY_INCLUDES,
                "timezone": SPORTS_TIMEZONE.key,
                "order": "desc",
                "per_page": 50,
                "filters": self._HISTORY_STATISTIC_FILTER,
            },
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise SportmonksAPIError(
                "Sportmonks devolvió el historial del equipo con formato inesperado."
            )
        completed = [
            self._normalize_history_payload(item)
            for item in data
            if isinstance(item, dict) and self._history_item(item) is not None
        ]
        completed.sort(key=self._history_sort_value, reverse=True)
        return completed[:bounded_limit]

    @classmethod
    def normalize_history(
        cls,
        items: list[dict],
        limit: int = 5,
    ) -> list[H2HMatchItem]:
        normalized = [
            history
            for item in items
            if isinstance(item, dict)
            and (history := cls._history_item(item)) is not None
        ]
        normalized.sort(key=lambda match: match.date, reverse=True)
        return normalized[: max(0, int(limit))]

    @classmethod
    def _history_item(cls, item: dict) -> H2HMatchItem | None:
        state = item.get("state") or {}
        status = cls._normalize_status(
            state.get("state") or state.get("developer_name") or state.get("name"),
            item.get("state_id"),
        )
        if status != "FINALIZADO":
            return None

        home = cls._participant(item, "home")
        away = cls._participant(item, "away")
        league = item.get("league") or {}
        if home is None or away is None or not league.get("name"):
            return None
        current_score = cls._current_score(item.get("scores"), home, away)
        if current_score is None:
            return None
        home_goals, away_goals = current_score
        home_name = str(home.get("name") or "").strip()
        away_name = str(away.get("name") or "").strip()
        if not home_name or not away_name:
            return None
        kickoff = cls._kickoff_at(item)
        if kickoff is None:
            return None
        if home_goals > away_goals:
            winner = home_name
        elif away_goals > home_goals:
            winner = away_name
        else:
            winner = "Empate"
        return H2HMatchItem(
            date=kickoff.astimezone(SPORTS_TIMEZONE).date().isoformat(),
            competition=str(league["name"]),
            home_team=home_name,
            away_team=away_name,
            score=f"{home_goals} - {away_goals}",
            winner=winner,
        )

    def _to_match_summary(self, item: dict) -> MatchSummary:
        raw_fixture_id = item.get("id")
        if raw_fixture_id is None:
            raise SportmonksAPIError("Sportmonks devolvió un partido sin id.")
        home = self._participant(item, "home")
        away = self._participant(item, "away")
        kickoff = self._kickoff_at(item)
        if home is None or away is None or kickoff is None:
            raise SportmonksAPIError(
                "Sportmonks devolvió un partido sin participantes u hora válidos."
            )
        home_name = str(home.get("name") or "").strip()
        away_name = str(away.get("name") or "").strip()
        if not home_name or not away_name:
            raise SportmonksAPIError(
                "Sportmonks devolvió participantes sin nombre."
            )

        league = item.get("league") or {}
        state = item.get("state") or {}
        venue = item.get("venue") or {}
        referee_name = self._referee_name(item.get("referees"))
        fixture_id = str(raw_fixture_id)
        return MatchSummary(
            id=f"sportmonks-{fixture_id}",
            external_id=fixture_id,
            competition=league.get("name") or "Competición",
            kickoff_at=kickoff,
            home_team=home_name,
            away_team=away_name,
            home_team_id=self._optional_id(home.get("id")),
            away_team_id=self._optional_id(away.get("id")),
            home_logo=home.get("image_path"),
            away_logo=away.get("image_path"),
            venue=venue.get("name") if isinstance(venue, dict) else None,
            referee=referee_name,
            data_quality=0.96,
            odds_available=bool(item.get("has_odds")),
            status=self._normalize_status(
                state.get("state")
                or state.get("developer_name")
                or state.get("short_name")
                or state.get("name"),
                item.get("state_id"),
            ),
            source_provider=self.provider_name,
            source_url=f"{self.base_url}/fixtures/{fixture_id}",
        )

    @staticmethod
    def _participant(item: dict, location: str) -> dict | None:
        participants = item.get("participants")
        if not isinstance(participants, list):
            return None
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            meta = participant.get("meta") or {}
            if str(meta.get("location") or "").casefold() == location:
                return participant
        return None

    @staticmethod
    def _optional_id(value: object) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _kickoff_at(item: dict) -> datetime | None:
        timestamp = item.get("starting_at_timestamp")
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        raw_value = item.get("starting_at")
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(raw_value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SPORTS_TIMEZONE)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _history_sort_value(item: dict) -> float:
        kickoff = SportmonksProvider._kickoff_at(item)
        return kickoff.timestamp() if kickoff is not None else 0.0

    @classmethod
    def _normalize_history_payload(cls, item: dict) -> dict:
        """Expose a provider-neutral history contract for the AI prompt."""

        normalized = dict(item)
        home = cls._participant(item, "home")
        away = cls._participant(item, "away")
        kickoff = cls._kickoff_at(item)
        league = item.get("league") or {}
        if home is not None and away is not None:
            normalized["teams"] = {
                "home": {"id": home.get("id"), "name": home.get("name")},
                "away": {"id": away.get("id"), "name": away.get("name")},
            }
            current_score = cls._current_score(item.get("scores"), home, away)
            if current_score is not None:
                normalized["goals"] = {
                    "home": current_score[0],
                    "away": current_score[1],
                }
        if kickoff is not None:
            normalized["fixture"] = {"date": kickoff.isoformat()}
        if isinstance(league, dict) and league.get("name"):
            normalized["competition"] = league["name"]

        raw_statistics = item.get("statistics")
        if not isinstance(raw_statistics, list):
            return normalized

        metric_names = {
            34: "corners",
            42: "total_shots",
            56: "fouls",
            83: "red_cards",
            84: "yellow_cards",
            85: "red_cards",
            86: "shots_on_target",
        }
        by_participant: dict[str, dict] = {}
        for raw_metric in raw_statistics:
            if not isinstance(raw_metric, dict):
                continue
            try:
                type_id = int(raw_metric.get("type_id"))
            except (TypeError, ValueError):
                continue
            canonical_name = metric_names.get(type_id)
            data = raw_metric.get("data") or {}
            value = data.get("value") if isinstance(data, dict) else None
            metric_value = cls._metric_value(value)
            if canonical_name is None or metric_value is None:
                continue
            participant_id = str(raw_metric.get("participant_id") or "")
            location = str(raw_metric.get("location") or "").casefold()
            group_key = participant_id or location
            if not group_key:
                continue
            block = by_participant.setdefault(
                group_key,
                {
                    "participant_id": participant_id or None,
                    "location": location or None,
                },
            )
            if canonical_name == "red_cards" and canonical_name in block:
                block[canonical_name] = float(block[canonical_name]) + metric_value
            else:
                block[canonical_name] = metric_value

        canonical = list(by_participant.values())
        if canonical:
            normalized["provider_statistics"] = raw_statistics
            normalized["statistics"] = canonical
        return normalized

    @staticmethod
    def _metric_value(value: object) -> float | int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            candidate = value.strip().removesuffix("%").strip()
            try:
                parsed = float(candidate)
            except ValueError:
                return None
            return int(parsed) if parsed.is_integer() else parsed
        return None

    @staticmethod
    def _current_score(
        raw_scores: object,
        home: dict,
        away: dict,
    ) -> tuple[int, int] | None:
        if not isinstance(raw_scores, list):
            return None
        by_location: dict[str, int] = {}
        home_id = str(home.get("id"))
        away_id = str(away.get("id"))
        for score_item in raw_scores:
            if (
                not isinstance(score_item, dict)
                or str(score_item.get("description") or "").upper() != "CURRENT"
            ):
                continue
            score = score_item.get("score") or {}
            goals = score.get("goals") if isinstance(score, dict) else None
            if not isinstance(goals, int):
                continue
            location = str(score.get("participant") or "").casefold()
            if location not in {"home", "away"}:
                participant_id = str(score_item.get("participant_id"))
                if participant_id == home_id:
                    location = "home"
                elif participant_id == away_id:
                    location = "away"
            if location in {"home", "away"}:
                by_location[location] = goals
        if "home" not in by_location or "away" not in by_location:
            return None
        return by_location["home"], by_location["away"]

    @staticmethod
    def _referee_name(raw_referees: object) -> str | None:
        if not isinstance(raw_referees, list):
            return None
        candidates: list[tuple[bool, str]] = []
        for entry in raw_referees:
            if not isinstance(entry, dict):
                continue
            nested = entry.get("referee") or {}
            name = entry.get("name") or (
                nested.get("name") if isinstance(nested, dict) else None
            )
            if isinstance(name, str) and name.strip():
                raw_type = entry.get("type") or {}
                type_label = " ".join(
                    str(
                        raw_type.get(key)
                        if isinstance(raw_type, dict)
                        else ""
                    )
                    for key in ("name", "developer_name", "code")
                ).replace("_", " ").casefold()
                candidates.append(("head" in type_label, name.strip()))
        if not candidates:
            return None
        head = next((name for is_head, name in candidates if is_head), None)
        # A single untyped official is unambiguous; with several untyped
        # officials we prefer no referee over assigning an assistant as head.
        return head or (candidates[0][1] if len(candidates) == 1 else None)

    @staticmethod
    def _normalize_status(raw_status: object, state_id: object = None) -> str:
        normalized = " ".join(str(raw_status or "").replace("_", " ").split()).casefold()
        status_code = normalized.replace(" ", "_").upper()
        if status_code in {"NS", "TBA"} or any(
            token in normalized
            for token in ("not started", "scheduled", "starting", "to be announced")
        ):
            return "PROGRAMADO"
        if status_code in {"HT", "BREAK", "EXTRA_TIME_BREAK", "PEN_BREAK"} or any(
            token in normalized for token in ("half time", "break", "paused")
        ):
            return "EN PAUSA"
        if status_code in {
            "INPLAY_1ST_HALF",
            "INPLAY_2ND_HALF",
            "INPLAY_ET",
            "INPLAY_PENALTIES",
        } or any(token in normalized for token in ("inplay", "in play", "1st half", "2nd half", "live")):
            return "EN JUEGO"
        if status_code in {"FT", "AET", "FT_PEN", "WO", "AWARDED"} or any(
            token in normalized
            for token in ("finished", "after extra time", "after penalties", "full time", "awarded", "walk over")
        ):
            return "FINALIZADO"
        if "postpon" in normalized:
            return "POSPUESTO"
        if status_code == "DELAYED" or "delay" in normalized:
            return "RETRASADO"
        if status_code in {"AWAITING_UPDATES", "PENDING"}:
            return "PENDIENTE"
        if status_code == "DELETED":
            return "ELIMINADO"
        if any(token in normalized for token in ("cancel", "suspend", "abandon", "interrupt")):
            return "SUSPENDIDO"

        try:
            numeric_state = int(state_id)
        except (TypeError, ValueError):
            numeric_state = 0
        if numeric_state in {1, 13}:
            return "PROGRAMADO"
        if numeric_state in {5, 7, 8, 14, 17}:
            return "FINALIZADO"
        if numeric_state in {3, 4, 21, 25}:
            return "EN PAUSA"
        if numeric_state in {2, 6, 9, 22}:
            return "EN JUEGO"
        if numeric_state == 10:
            return "POSPUESTO"
        if numeric_state == 16:
            return "RETRASADO"
        if numeric_state == 19:
            return "PENDIENTE"
        if numeric_state == 26:
            return "PENDIENTE"
        if numeric_state == 20:
            return "ELIMINADO"
        if numeric_state in {11, 12, 15, 18}:
            return "SUSPENDIDO"
        return str(raw_status or "DESCONOCIDO").upper()
