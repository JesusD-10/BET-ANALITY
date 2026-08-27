from datetime import date, time

from app.db.football_txt import parse_football_txt


def test_parses_internationals_score_between_teams_and_ignores_scorers() -> None:
    text = """
= Copa América 2024

# Date       Thu Jun 20 - Sun Jul 14 2024 (24d)
# Teams      16

▪ Group A
Thu Jun 20
  Argentina              2-0 Canada                   @ Atlanta, United States
     (Julián Álvarez 49' Lautaro Martínez 88')
Fri Jun 21
  Perú                   0-0 Curaçao                  @ Düsseldorf, Germany
"""

    matches = parse_football_txt(
        text,
        member_name="internationals-master/copa_america/2024_copa_america.txt",
    )

    assert len(matches) == 2
    first, second = matches
    assert first.competition == "Copa América"
    assert first.season_label == "2024"
    assert first.match_date == date(2024, 6, 20)
    assert (first.home_team, first.away_team) == ("Argentina", "Canada")
    assert (first.home_score, first.away_score) == (2, 0)
    assert first.round == "Group A"
    assert first.venue == "Atlanta, United States"
    assert first.kickoff_precision == "date-only"
    assert first.status == "FINALIZADO"
    assert (second.home_team, second.away_team) == ("Perú", "Curaçao")
    assert second.venue == "Düsseldorf, Germany"


def test_parses_world_cup_time_offset_half_time_and_inline_comment() -> None:
    text = """
= World Cup 2026      # in Canada, USA, and Mexico

Group A | Mexico South Africa South Korea Czech Republic
▪ Group A
Thu June 11
  (73) 13:00 UTC-6 Mexico  2-0 (1-0)  South Africa @ Mexico City   ## 1A v 2B
"""

    [match] = parse_football_txt(
        text,
        member_name="worldcup-master/2026--canada-usa-mexico/cup.txt",
    )

    assert match.competition == "World Cup"
    assert match.match_date == date(2026, 6, 11)
    assert match.kickoff_time == time(13, 0)
    assert match.kickoff_utc_offset == "-06:00"
    assert match.kickoff_precision == "datetime-offset"
    assert (match.home_score, match.away_score) == (2, 0)
    assert (match.half_time_home_score, match.half_time_away_score) == (1, 0)
    assert match.venue == "Mexico City"


def test_parses_world_cup_versus_then_score_and_same_line_date() -> None:
    text = """
= World Cup 2014

▪ Group A
Thu Jun 12 17:00 UTC-3  Brazil v Croatia   3-1 (1-1) @ Arena de São Paulo, São Paulo
"""

    [match] = parse_football_txt(
        text,
        member_name="worldcup-master/2014--brazil/cup.txt",
    )

    assert match.match_date == date(2014, 6, 12)
    assert match.kickoff_time == time(17, 0)
    assert match.kickoff_utc_offset == "-03:00"
    assert (match.home_team, match.away_team) == ("Brazil", "Croatia")
    assert (match.home_score, match.away_score) == (3, 1)
    assert (match.half_time_home_score, match.half_time_away_score) == (1, 1)


def test_parses_scheduled_versus_fixture() -> None:
    text = """
= FIFA World Cup 2026

# Comments and metadata must not become records.
Thu Jun 11
  Mexico                  v  South Africa             @ Mexico City, Mexico
  (A scorer line that should be ignored 3-2)
"""

    [match] = parse_football_txt(
        text,
        member_name="internationals-master/fifa_world_cup/2026_fifa_world_cup.txt",
    )

    assert match.competition == "FIFA World Cup"
    assert match.match_date == date(2026, 6, 11)
    assert (match.home_team, match.away_team) == ("Mexico", "South Africa")
    assert match.home_score is None
    assert match.away_score is None
    assert match.status == "PROGRAMADO"
    assert match.venue == "Mexico City, Mexico"


def test_uses_last_parenthesized_pair_as_half_time_for_extra_time() -> None:
    text = """
= World Cup 2026

▪ Round of 32
Mon Jun 29
  (74) 16:30 UTC-4 Germany 1-1 a.e.t. (1-1, 0-1), 3-4 pen. Paraguay @ Boston (Foxborough)
"""

    [match] = parse_football_txt(
        text,
        member_name="worldcup-master/2026--canada-usa-mexico/cup_finals.txt",
    )

    assert (match.home_team, match.away_team) == ("Germany", "Paraguay")
    assert (match.home_score, match.away_score) == (1, 1)
    assert (match.half_time_home_score, match.half_time_away_score) == (0, 1)
    assert match.round == "Round of 32"


def test_parses_penalty_score_written_before_regulation_score() -> None:
    text = """
= World Cup 2006

▪ Round of 16
Mon Jun 26
  Switzerland 0-3 pen. 0-0 a.e.t. (0-0) Ukraine @ Köln
"""

    [match] = parse_football_txt(
        text,
        member_name="worldcup-master/2006--germany/cup_finals.txt",
    )

    assert (match.home_team, match.away_team) == ("Switzerland", "Ukraine")
    assert (match.home_score, match.away_score) == (0, 0)
    assert (match.half_time_home_score, match.half_time_away_score) == (0, 0)


def test_infers_year_and_competition_from_member_when_header_omits_year() -> None:
    text = """
= Copa América

▪ Final
Sun Jul 14
  Argentina 1-0 Colombia @ Miami Gardens, United States
"""

    [match] = parse_football_txt(
        text,
        member_name="internationals-master/copa_america/2024_copa_america.txt",
    )

    assert match.competition == "Copa América"
    assert match.season_start == 2024
    assert match.season_end == 2024
    assert match.match_date == date(2024, 7, 14)


def test_parses_day_before_month_and_local_time_without_offset() -> None:
    text = """
= World Cup 1998

▪ Group A
10 June 19:30 Brazil 2-1 Scotland @ Stade de France, Saint-Denis
"""

    [match] = parse_football_txt(
        text,
        member_name="worldcup-master/1998--france/cup.txt",
    )

    assert match.match_date == date(1998, 6, 10)
    assert match.kickoff_time == time(19, 30)
    assert match.kickoff_utc_offset is None
    assert match.kickoff_precision == "datetime-local-unknown"


def test_rejects_metadata_tables_comments_and_score_lines_without_venue() -> None:
    text = """
= World Cup 1930

Group 1 | Argentina Chile France Mexico
▪ Matchday 1 | July 13
# France 4-1 Mexico @ this is still a comment
July 13
  France 4-1 Mexico
  (Lucien Laurent 19' Marcel Langiller 40')
"""

    assert (
        parse_football_txt(
            text,
            member_name="worldcup-master/1930--uruguay/cup.txt",
        )
        == []
    )


def test_returns_no_records_without_a_reliable_year() -> None:
    text = """
= Friendly Tournament
Thu Jun 20
  Team One 2-1 Team Two @ Somewhere
"""

    assert parse_football_txt(text, member_name="fixtures/cup.txt") == []
