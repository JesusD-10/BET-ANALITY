/* eslint-disable @next/next/no-img-element */
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Search,
  Star,
  X,
} from "lucide-react";
import AppShell, { PageHeader, ResponsibleNote } from "../components/AppShell";
import {
  ApiError,
  ApiTimeoutError,
  getMatches,
  isAbortError,
  type Match,
} from "../lib/api";
import { findLeagueByName } from "../lib/leagues";

const LIMA_TIME_ZONE = "America/Lima";
const PIN_STORAGE_KEY = "bet-anality:pins:v1";
const PIN_STORAGE_VERSION = 1;

function pollingMilliseconds(rawValue: string | undefined, fallbackSeconds: number): number {
  const parsed = Number(rawValue);
  const seconds = Number.isFinite(parsed) ? parsed : fallbackSeconds;
  return Math.min(600, Math.max(15, seconds)) * 1_000;
}

const LIVE_POLL_MS = pollingMilliseconds(
  process.env.NEXT_PUBLIC_LIVE_REFRESH_SECONDS,
  15,
);
const IDLE_POLL_MS = pollingMilliseconds(
  process.env.NEXT_PUBLIC_IDLE_REFRESH_SECONDS,
  45,
);

type StatusFilter = "all" | "live" | "finished" | "upcoming";
type MatchKind = "live" | "finished" | "upcoming" | "other";

type CompetitionPin = {
  key: string;
  name: string;
  country: string;
  countryCode: string;
  logo: string | null;
};

type CountryPin = {
  key: string;
  name: string;
  code: string;
};

type PinStore = {
  countries: CountryPin[];
  competitions: CompetitionPin[];
};

type CompetitionGroup = CompetitionPin & { matches: Match[] };
type CountryGroup = {
  key: string;
  name: string;
  code: string;
  competitions: CompetitionGroup[];
};

const STATUS_FILTERS: readonly { key: StatusFilter; label: string }[] = [
  { key: "all", label: "Todos" },
  { key: "live", label: "En vivo" },
  { key: "finished", label: "Finalizados" },
  { key: "upcoming", label: "Próximos" },
];
const LIVE_CODES = new Set(["1H", "HT", "2H", "BT", "ET", "P", "LIVE"]);
const FINISHED_CODES = new Set(["FT", "AET", "PEN", "AWD", "WO"]);
const UPCOMING_CODES = new Set(["NS", "TBD", "TBA", "SCHEDULED", "TIMED"]);
const STOPPED_CODES = new Set(["PST", "CANC", "ABD", "SUSP", "INT"]);

const timeFormatter = new Intl.DateTimeFormat("es-PE", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: LIMA_TIME_ZONE,
});
const longDateFormatter = new Intl.DateTimeFormat("es-PE", {
  weekday: "long",
  day: "2-digit",
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

function limaDateValue(value = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: LIMA_TIME_ZONE,
  }).formatToParts(value);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function isDateValue(value: string | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function shiftDate(value: string, days: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(
    date.getUTCDate(),
  ).padStart(2, "0")}`;
}

function formatLongDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const formatted = longDateFormatter.format(new Date(Date.UTC(year, month - 1, day, 12)));
  return formatted.slice(0, 1).toUpperCase() + formatted.slice(1);
}

function normalizeText(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("es")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function getMatchKind(match: Match, nowMs = Date.now()): MatchKind {
  const shortStatus = (match.status_short ?? "").trim().toUpperCase();
  const status = normalizeText(match.status ?? "");
  if (
    LIVE_CODES.has(shortStatus) ||
    status.startsWith("en juego") ||
    ["entretiempo", "en pausa", "descanso", "tiempo extra", "penales"].some((item) =>
      status.includes(item),
    )
  ) return "live";
  if (FINISHED_CODES.has(shortStatus) || status.startsWith("finalizado")) return "finished";
  if (UPCOMING_CODES.has(shortStatus) || ["programado", "por definir"].includes(status)) return "upcoming";
  if (
    STOPPED_CODES.has(shortStatus) ||
    ["pospuesto", "cancelado", "suspendido", "interrumpido", "eliminado"].some((item) =>
      status.includes(item),
    )
  ) return "other";
  const kickoff = new Date(match.kickoff_at).getTime();
  return Number.isFinite(kickoff) && kickoff >= nowMs ? "upcoming" : "other";
}

function statusPresentation(match: Match): { primary: string; secondary: string } {
  const kind = getMatchKind(match);
  const shortStatus = (match.status_short ?? "").trim().toUpperCase();
  const normalizedStatus = normalizeText(match.status ?? "");
  if (kind === "live") {
    if (shortStatus === "HT" || normalizedStatus.includes("entretiempo")) {
      return { primary: "Descanso", secondary: "EN VIVO" };
    }
    if (shortStatus === "P" || normalizedStatus.includes("penales")) {
      return { primary: "Penales", secondary: "EN VIVO" };
    }
    if (typeof match.elapsed === "number" && match.elapsed > 0) {
      return { primary: `${match.elapsed}′`, secondary: "EN VIVO" };
    }
    return { primary: "En vivo", secondary: "AHORA" };
  }
  if (kind === "finished") return { primary: "Final", secondary: "" };
  if (kind === "upcoming") {
    const kickoff = new Date(match.kickoff_at);
    return {
      primary: Number.isNaN(kickoff.getTime()) ? "Por definir" : timeFormatter.format(kickoff),
      secondary: "",
    };
  }
  const rawStatus = (match.status || "Estado pendiente").replaceAll("_", " ").toLocaleLowerCase("es");
  return { primary: rawStatus.slice(0, 1).toUpperCase() + rawStatus.slice(1), secondary: "" };
}

function competitionMetadata(match: Match): CompetitionPin {
  const knownLeague = findLeagueByName(match.competition);
  const country = match.country?.trim() || knownLeague?.country || "Internacional";
  const countryCode = match.country_code?.trim().toUpperCase() || knownLeague?.countryCode || "";
  const countryIdentity = normalizeText(countryCode || country) || "internacional";
  const competitionIdentity = normalizeText(match.competition) || "otra competicion";
  const sourceProvider = match.source_provider?.trim().toLocaleLowerCase("en") || "";
  const leagueId = match.league_id?.trim() || "";
  return {
    key: sourceProvider && leagueId
      ? `competition:${sourceProvider}:${leagueId}`
      : `competition:${countryIdentity}:${competitionIdentity.replaceAll(" ", "-")}`,
    name: match.competition || "Otra competición",
    country,
    countryCode,
    logo: match.competition_logo || knownLeague?.logoUrl || null,
  };
}

function countryMetadata(match: Match): CountryPin {
  const competition = competitionMetadata(match);
  return {
    key: `country:${normalizeText(competition.countryCode || competition.country) || "internacional"}`,
    name: competition.country,
    code: competition.countryCode,
  };
}

function flagEmoji(code: string): string {
  const normalized = code.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(normalized)) return "🌐";
  return String.fromCodePoint(...[...normalized].map((character) => 127397 + character.charCodeAt(0)));
}

function safePinStore(rawValue: string | null): PinStore {
  const emptyStore: PinStore = { countries: [], competitions: [] };
  if (!rawValue) return emptyStore;
  try {
    const parsed: unknown = JSON.parse(rawValue);
    const isCurrentPayload =
      typeof parsed === "object" && parsed !== null &&
      "version" in parsed && parsed.version === PIN_STORAGE_VERSION;
    if (!isCurrentPayload) return emptyStore;
    const competitionValues =
      "competitions" in parsed && Array.isArray(parsed.competitions) ? parsed.competitions : [];
    const countryValues = "countries" in parsed && Array.isArray(parsed.countries) ? parsed.countries : [];
    const competitions = competitionValues
      .filter(
        (item): item is CompetitionPin =>
          typeof item === "object" && item !== null &&
          "key" in item && typeof item.key === "string" &&
          "name" in item && typeof item.name === "string" &&
          "country" in item && typeof item.country === "string" &&
          "countryCode" in item && typeof item.countryCode === "string" &&
          "logo" in item && (typeof item.logo === "string" || item.logo === null),
      )
      .slice(0, 50);
    const countries = countryValues
      .filter(
        (item): item is CountryPin =>
          typeof item === "object" && item !== null &&
          "key" in item && typeof item.key === "string" &&
          "name" in item && typeof item.name === "string" &&
          "code" in item && typeof item.code === "string",
      )
      .slice(0, 50);
    return { countries, competitions };
  } catch {
    return emptyStore;
  }
}

function persistPins(pinStore: PinStore) {
  try {
    window.localStorage.setItem(
      PIN_STORAGE_KEY,
      JSON.stringify({ version: PIN_STORAGE_VERSION, ...pinStore }),
    );
  } catch {
    // La agenda sigue funcionando si el navegador bloquea localStorage.
  }
}

function SafeLogo({ src, fallback, className }: { src?: string | null; fallback: string; className: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (!src || failed) {
    return <span className={`${className} safe-logo-fallback`} aria-hidden="true">{fallback.slice(0, 2).toUpperCase()}</span>;
  }
  return <img src={src} alt="" className={className} onError={() => setFailed(true)} />;
}

function requestErrorMessage(requestError: unknown): string {
  if (requestError instanceof ApiTimeoutError) return "La agenda tardó demasiado en responder. Intenta nuevamente.";
  if (requestError instanceof ApiError && requestError.status >= 500) {
    return "El servicio de partidos no está disponible temporalmente.";
  }
  if (requestError instanceof ApiError) return requestError.detail;
  return "No se pudo conectar con el catálogo de partidos.";
}

function PartidosContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [today, setToday] = useState(() => limaDateValue());
  const requestedDate = searchParams.get("date");
  const selectedDate = isDateValue(requestedDate) ? requestedDate : today;
  const requestedFilter = searchParams.get("status") as StatusFilter | null;
  const activeFilter = STATUS_FILTERS.some((item) => item.key === requestedFilter)
    ? (requestedFilter as StatusFilter)
    : "all";
  const selectedCompetitionKey = searchParams.get("liga");
  const selectedCountryKey = searchParams.get("pais");
  const urlQuery = searchParams.get("q") ?? "";

  const [query, setQuery] = useState(urlQuery);
  const [debouncedQuery, setDebouncedQuery] = useState(urlQuery.trim());
  const lastUrlQuery = useRef(urlQuery);
  const [matches, setMatches] = useState<Match[]>([]);
  const [loadedRequestKey, setLoadedRequestKey] = useState("");
  const loadedRequestKeyRef = useRef("");
  const [source, setSource] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);
  const [pinStore, setPinStore] = useState<PinStore>({ countries: [], competitions: [] });
  const pins = pinStore.competitions;
  const countryPins = pinStore.countries;

  const updateParams = useCallback((updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (!value) params.delete(key);
      else params.set(key, value);
    }
    const nextQuery = params.toString();
    router.replace(nextQuery ? `/partidos?${nextQuery}` : "/partidos", { scroll: false });
  }, [router, searchParams]);

  useEffect(() => {
    if (selectedCompetitionKey && selectedCountryKey) updateParams({ pais: null });
  }, [selectedCompetitionKey, selectedCountryKey, updateParams]);

  useEffect(() => {
    const timer = window.setInterval(() => setToday(limaDateValue()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (urlQuery !== lastUrlQuery.current) {
      lastUrlQuery.current = urlQuery;
      setQuery(urlQuery);
      setDebouncedQuery(urlQuery.trim());
    }
  }, [urlQuery]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const cleanQuery = query.trim();
      setDebouncedQuery(cleanQuery);
      if (cleanQuery !== urlQuery) {
        lastUrlQuery.current = cleanQuery;
        updateParams({ q: cleanQuery || null });
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, updateParams, urlQuery]);

  useEffect(() => {
    const loadPins = () => setPinStore(safePinStore(window.localStorage.getItem(PIN_STORAGE_KEY)));
    loadPins();
    const onStorage = (event: StorageEvent) => {
      if (event.key === PIN_STORAGE_KEY) setPinStore(safePinStore(event.newValue));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const requestKey = `${selectedDate}:${debouncedQuery}`;

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let controller: AbortController | null = null;
    let pollTimer: number | null = null;

    const clearPoll = () => {
      if (pollTimer !== null) window.clearTimeout(pollTimer);
      pollTimer = null;
    };
    const schedulePoll = (hasLiveMatches: boolean) => {
      clearPoll();
      if (cancelled || selectedDate !== today) return;
      pollTimer = window.setTimeout(() => {
        if (document.visibilityState === "visible") void loadMatches(true);
        else schedulePoll(hasLiveMatches);
      }, hasLiveMatches ? LIVE_POLL_MS : IDLE_POLL_MS);
    };
    const loadMatches = async (isPoll: boolean) => {
      if (cancelled || inFlight) return;
      inFlight = true;
      const hasCurrentData = loadedRequestKeyRef.current === requestKey;
      setLoading(!hasCurrentData);
      setRefreshing(hasCurrentData);
      if (!isPoll || !hasCurrentData) setError("");
      controller = new AbortController();
      let hasLiveMatches = false;
      try {
        const data = await getMatches(debouncedQuery, selectedDate, controller.signal);
        if (cancelled || controller.signal.aborted) return;
        hasLiveMatches = data.matches.some((match) => getMatchKind(match) === "live");
        loadedRequestKeyRef.current = requestKey;
        setLoadedRequestKey(requestKey);
        setMatches(data.matches);
        setSource(data.source ?? "");
        setNotice(data.notice ?? "");
        setError("");
        setLastUpdated(new Date());
      } catch (requestError: unknown) {
        if (cancelled || (controller?.signal.aborted ?? false) || isAbortError(requestError)) return;
        setError(requestErrorMessage(requestError));
      } finally {
        inFlight = false;
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
          schedulePoll(hasLiveMatches);
        }
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible" && selectedDate === today && !inFlight) {
        clearPoll();
        void loadMatches(true);
      }
    };

    void loadMatches(false);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      cancelled = true;
      clearPoll();
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [debouncedQuery, requestKey, retryVersion, selectedDate, today]);

  const hasCurrentData = loadedRequestKey === requestKey;
  const currentMatches = useMemo(
    () => (hasCurrentData ? matches : []),
    [hasCurrentData, matches],
  );
  useEffect(() => {
    if (currentMatches.length === 0) return;
    const canonicalByIdentity = new Map<string, CompetitionPin>();
    for (const match of currentMatches) {
      const metadata = competitionMetadata(match);
      canonicalByIdentity.set(
        `${normalizeText(metadata.country)}:${normalizeText(metadata.name)}`,
        metadata,
      );
    }
    setPinStore((current) => {
      let changed = false;
      const migrated = current.competitions.map((pin) => {
        const canonical = canonicalByIdentity.get(
          `${normalizeText(pin.country)}:${normalizeText(pin.name)}`,
        );
        if (canonical && canonical.key !== pin.key) {
          changed = true;
          return canonical;
        }
        return pin;
      });
      const seen = new Set<string>();
      const competitions = migrated.filter((pin) => {
        if (seen.has(pin.key)) {
          changed = true;
          return false;
        }
        seen.add(pin.key);
        return true;
      });
      if (!changed) return current;
      const next = { ...current, competitions };
      persistPins(next);
      return next;
    });
  }, [currentMatches]);
  const pinnedKeys = useMemo(() => new Set(pins.map((pin) => pin.key)), [pins]);
  const pinnedCountryKeys = useMemo(
    () => new Set(countryPins.map((pin) => pin.key)),
    [countryPins],
  );
  const scopedMatches = useMemo(
    () => {
      if (selectedCompetitionKey) {
        const storedPin = pins.find((pin) => pin.key === selectedCompetitionKey);
        return currentMatches.filter((match) => {
          const metadata = competitionMetadata(match);
          return metadata.key === selectedCompetitionKey || Boolean(
            storedPin &&
            normalizeText(metadata.name) === normalizeText(storedPin.name) &&
            normalizeText(metadata.country) === normalizeText(storedPin.country)
          );
        });
      }
      if (selectedCountryKey) {
        return currentMatches.filter((match) => countryMetadata(match).key === selectedCountryKey);
      }
      return currentMatches;
    },
    [currentMatches, pins, selectedCompetitionKey, selectedCountryKey],
  );
  const filterCounts = useMemo(() => ({
    all: scopedMatches.length,
    live: scopedMatches.filter((match) => getMatchKind(match) === "live").length,
    finished: scopedMatches.filter((match) => getMatchKind(match) === "finished").length,
    upcoming: scopedMatches.filter((match) => getMatchKind(match) === "upcoming").length,
  }), [scopedMatches]);
  const filteredMatches = useMemo(
    () => activeFilter === "all" ? scopedMatches : scopedMatches.filter((match) => getMatchKind(match) === activeFilter),
    [activeFilter, scopedMatches],
  );

  const countryGroups = useMemo<CountryGroup[]>(() => {
    const countries = new Map<string, { name: string; code: string; competitions: Map<string, CompetitionGroup> }>();
    for (const match of filteredMatches) {
      const metadata = competitionMetadata(match);
      const countryKey = countryMetadata(match).key;
      let country = countries.get(countryKey);
      if (!country) {
        country = { name: metadata.country, code: metadata.countryCode, competitions: new Map() };
        countries.set(countryKey, country);
      }
      const competition = country.competitions.get(metadata.key);
      if (competition) competition.matches.push(match);
      else country.competitions.set(metadata.key, { ...metadata, matches: [match] });
    }
    return [...countries.entries()]
      .map(([key, country]) => ({
        key,
        name: country.name,
        code: country.code,
        competitions: [...country.competitions.values()]
          .map((competition) => ({
            ...competition,
            matches: [...competition.matches].sort(
              (left, right) => new Date(left.kickoff_at).getTime() - new Date(right.kickoff_at).getTime(),
            ),
          }))
          .sort((left, right) =>
            Number(pinnedKeys.has(right.key)) - Number(pinnedKeys.has(left.key)) || left.name.localeCompare(right.name, "es"),
          ),
      }))
      .sort((left, right) => {
        const leftPinned = pinnedCountryKeys.has(left.key) || left.competitions.some((competition) => pinnedKeys.has(competition.key));
        const rightPinned = pinnedCountryKeys.has(right.key) || right.competitions.some((competition) => pinnedKeys.has(competition.key));
        return Number(rightPinned) - Number(leftPinned) || left.name.localeCompare(right.name, "es");
      });
  }, [filteredMatches, pinnedCountryKeys, pinnedKeys]);

  const selectedCompetition = useMemo(() => {
    if (!selectedCompetitionKey) return null;
    const stored = pins.find((pin) => pin.key === selectedCompetitionKey);
    if (stored) return stored;
    for (const match of currentMatches) {
      const metadata = competitionMetadata(match);
      if (metadata.key === selectedCompetitionKey) return metadata;
    }
    return null;
  }, [currentMatches, pins, selectedCompetitionKey]);

  const selectedCountry = useMemo(() => {
    if (!selectedCountryKey) return null;
    const stored = countryPins.find((pin) => pin.key === selectedCountryKey);
    if (stored) return stored;
    for (const match of currentMatches) {
      const metadata = countryMetadata(match);
      if (metadata.key === selectedCountryKey) return metadata;
    }
    return null;
  }, [countryPins, currentMatches, selectedCountryKey]);

  const togglePin = (competition: CompetitionPin) => {
    setPinStore((current) => {
      const storedPin: CompetitionPin = {
        key: competition.key,
        name: competition.name,
        country: competition.country,
        countryCode: competition.countryCode,
        logo: competition.logo,
      };
      const competitions = current.competitions.some((pin) => pin.key === competition.key)
        ? current.competitions.filter((pin) => pin.key !== competition.key)
        : [...current.competitions, storedPin];
      const next = { ...current, competitions };
      persistPins(next);
      return next;
    });
  };

  const toggleCountryPin = (country: CountryPin) => {
    setPinStore((current) => {
      const countries = current.countries.some((pin) => pin.key === country.key)
        ? current.countries.filter((pin) => pin.key !== country.key)
        : [...current.countries, country];
      const next = { ...current, countries };
      persistPins(next);
      return next;
    });
  };
  const setDate = (date: string) => {
    if (isDateValue(date)) updateParams({ date });
  };

  const emptyMessage = debouncedQuery
    ? `No encontramos partidos para “${debouncedQuery}” en esta fecha.`
    : selectedCompetition
      ? `No hay partidos de ${selectedCompetition.name} con estos filtros.`
      : selectedCountry
        ? `No hay partidos de ${selectedCountry.name} con estos filtros.`
      : activeFilter === "live"
        ? "No hay partidos en vivo en este momento."
        : activeFilter === "finished"
          ? "No hay partidos finalizados en esta fecha."
          : activeFilter === "upcoming"
            ? "No hay próximos partidos en esta fecha."
            : "No hay partidos disponibles para esta fecha.";

  return (
    <AppShell>
      <PageHeader
        eyebrow="AGENDA · RESULTADOS EN TIEMPO REAL"
        title="Partidos"
        action={<Link className="outline-link" href="/">Volver al panorama <ArrowUpRight size={15} /></Link>}
      />

      <section className="scoreboard-controls" aria-label="Controles de la agenda">
        <div className="scoreboard-toolbar">
          <label className="search-wrap scoreboard-search">
            <Search size={18} aria-hidden="true" />
            <input
              aria-label="Buscar equipo, país o competición"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Buscar equipo, país o competición..."
            />
          </label>
          <p className="scoreboard-refresh-state" aria-live="polite">
            <RefreshCw className={refreshing ? "is-spinning" : ""} size={14} aria-hidden="true" />
            {refreshing ? "Actualizando resultados..." : lastUpdated ? `Actualizado ${timeFormatter.format(lastUpdated)}` : "Esperando datos"}
          </p>
        </div>

        <div className="agenda-command-bar">
          <nav className="agenda-status-filters" aria-label="Filtrar partidos por estado">
            {STATUS_FILTERS.map((filter) => (
              <button
                className={`agenda-filter-button ${activeFilter === filter.key ? "active" : ""}`}
                type="button"
                aria-pressed={activeFilter === filter.key}
                onClick={() => updateParams({ status: filter.key === "all" ? null : filter.key })}
                key={filter.key}
              >
                {filter.label} <span>{filterCounts[filter.key]}</span>
              </button>
            ))}
          </nav>
          <div className="agenda-date-nav">
            <button type="button" aria-label={`Ver ${formatLongDate(shiftDate(selectedDate, -1))}`} onClick={() => setDate(shiftDate(selectedDate, -1))}>
              <ChevronLeft size={17} aria-hidden="true" />
            </button>
            <label className="agenda-date-input">
              <CalendarDays size={16} aria-hidden="true" />
              <input type="date" aria-label="Elegir fecha de los partidos" value={selectedDate} onChange={(event) => setDate(event.target.value)} />
            </label>
            <button type="button" aria-label={`Ver ${formatLongDate(shiftDate(selectedDate, 1))}`} onClick={() => setDate(shiftDate(selectedDate, 1))}>
              <ChevronRight size={17} aria-hidden="true" />
            </button>
            <button className="agenda-today-button" type="button" disabled={selectedDate === today} onClick={() => setDate(today)}>Hoy</button>
          </div>
        </div>
      </section>

      {notice && <p className="data-notice">{notice}</p>}
      {error && (
        <div className="api-alert scoreboard-alert" role="alert">
          <span>{error}</span>
          <button className="text-button" type="button" onClick={() => setRetryVersion((value) => value + 1)}>Reintentar</button>
        </div>
      )}

      <div className="agenda-layout">
        <aside className="agenda-pins" aria-label="Países y competiciones anclados">
          <div className="agenda-pins-heading">
            <span><Star size={15} aria-hidden="true" /> Ancladas</span>
            <small>{countryPins.length + pins.length}</small>
          </div>
          <button
            className={`agenda-pin-all ${!selectedCompetitionKey && !selectedCountryKey ? "active" : ""}`}
            type="button"
            aria-pressed={!selectedCompetitionKey && !selectedCountryKey}
            onClick={() => updateParams({ liga: null, pais: null })}
          >
            Todos los partidos
          </button>
          {countryPins.length > 0 && <p className="agenda-pin-section-label">Países</p>}
          {countryPins.length > 0 && (
            <ul className="agenda-pin-list agenda-country-pin-list">
              {countryPins.map((pin) => (
                <li className={selectedCountryKey === pin.key ? "active" : ""} key={pin.key}>
                  <button
                    className="agenda-pin-select"
                    type="button"
                    aria-pressed={selectedCountryKey === pin.key}
                    onClick={() => updateParams({ pais: pin.key, liga: null })}
                  >
                    <span className="agenda-country-pin-icon" aria-hidden="true">{flagEmoji(pin.code)}</span>
                    <span><strong>{pin.name}</strong><small>{pin.code || "Región"}</small></span>
                  </button>
                  <button className="agenda-unpin-button" type="button" aria-label={`Desanclar ${pin.name}`} onClick={() => toggleCountryPin(pin)}>
                    <X size={14} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {pins.length > 0 && <p className="agenda-pin-section-label">Competiciones</p>}
          {pins.length ? (
            <ul className="agenda-pin-list">
              {pins.map((pin) => (
                <li className={selectedCompetitionKey === pin.key ? "active" : ""} key={pin.key}>
                  <button className="agenda-pin-select" type="button" aria-pressed={selectedCompetitionKey === pin.key} onClick={() => updateParams({ liga: pin.key, pais: null })}>
                    <SafeLogo src={pin.logo} fallback={pin.name} className="agenda-pin-logo" />
                    <span><strong>{pin.name}</strong><small>{pin.country}</small></span>
                  </button>
                  <button className="agenda-unpin-button" type="button" aria-label={`Desanclar ${pin.name}`} onClick={() => togglePin(pin)}>
                    <X size={14} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ) : countryPins.length === 0 ? <p className="agenda-pins-empty">Usa una estrella para mantener países o competiciones aquí.</p> : null}
        </aside>

        <section className="agenda-feed" aria-busy={loading || refreshing}>
          <header className="agenda-feed-header">
            <div><p className="section-kicker">{source ? `FUENTE · ${source}` : "AGENDA DEL DÍA"}</p><h2>{formatLongDate(selectedDate)}</h2></div>
            <span>{filteredMatches.length} {filteredMatches.length === 1 ? "partido" : "partidos"}</span>
          </header>
          {(selectedCompetitionKey || selectedCountryKey) && (
            <div className="agenda-active-scope">
              <span>Mostrando {selectedCompetition?.name ?? selectedCountry?.name ?? "la selección actual"}</span>
              <button type="button" onClick={() => updateParams({ liga: null, pais: null })}>Ver todos <X size={13} aria-hidden="true" /></button>
            </div>
          )}

          {loading && !hasCurrentData ? (
            <div className="empty-state">Consultando la agenda del {formatLongDate(selectedDate)}...</div>
          ) : !hasCurrentData && error ? (
            <div className="empty-state">No se pudo cargar esta agenda.</div>
          ) : countryGroups.length === 0 ? (
            <div className="empty-state">{emptyMessage}</div>
          ) : (
            <div className="scoreboard-country-list">
              {countryGroups.map((country) => {
                const countryMatchCount = country.competitions.reduce((total, competition) => total + competition.matches.length, 0);
                const countryPin: CountryPin = { key: country.key, name: country.name, code: country.code };
                const isCountryPinned = pinnedCountryKeys.has(country.key);
                return (
                  <section className="scoreboard-country" key={country.key}>
                    <header className="scoreboard-country-header">
                      <span className="scoreboard-country-flag" aria-hidden="true">{flagEmoji(country.code)}</span>
                      <span><small>PAÍS / REGIÓN</small><h2>{country.name}</h2></span>
                      <small>{countryMatchCount} {countryMatchCount === 1 ? "partido" : "partidos"}</small>
                      <button
                        className={`scoreboard-pin-button scoreboard-country-pin-button ${isCountryPinned ? "active" : ""}`}
                        type="button"
                        aria-pressed={isCountryPinned}
                        aria-label={`${isCountryPinned ? "Desanclar" : "Anclar"} ${country.name}`}
                        onClick={() => toggleCountryPin(countryPin)}
                      >
                        <Star size={16} fill={isCountryPinned ? "currentColor" : "none"} aria-hidden="true" />
                      </button>
                    </header>
                    {country.competitions.map((competition) => {
                      const isPinned = pinnedKeys.has(competition.key);
                      return (
                        <section className="scoreboard-competition" key={competition.key}>
                          <header className="scoreboard-competition-header">
                            <span className="scoreboard-competition-logo-shell">
                              <SafeLogo src={competition.logo} fallback={competition.name} className="scoreboard-competition-logo" />
                            </span>
                            <span className="scoreboard-competition-title"><h3>{competition.name}</h3><small>{competition.country}</small></span>
                            <button
                              className={`scoreboard-pin-button ${isPinned ? "active" : ""}`}
                              type="button"
                              aria-pressed={isPinned}
                              aria-label={`${isPinned ? "Desanclar" : "Anclar"} ${competition.name}`}
                              onClick={() => togglePin(competition)}
                            >
                              <Star size={16} fill={isPinned ? "currentColor" : "none"} aria-hidden="true" />
                            </button>
                          </header>
                          <div className="score-match-list">
                            {competition.matches.map((match) => {
                              const kind = getMatchKind(match);
                              const presentation = statusPresentation(match);
                              const hasScore = typeof match.home_score === "number" || typeof match.away_score === "number";
                              const halftimeScore = typeof match.halftime_home_score === "number" && typeof match.halftime_away_score === "number"
                                ? `Descanso ${match.halftime_home_score}-${match.halftime_away_score}` : "";
                              const scoreDescription = hasScore
                                ? `${match.home_team} ${typeof match.home_score === "number" ? match.home_score : "sin marcador"}, ${match.away_team} ${typeof match.away_score === "number" ? match.away_score : "sin marcador"}`
                                : `${match.home_team} contra ${match.away_team}`;
                              return (
                                <article className={`score-match-row is-${kind}`} key={match.id}>
                                  <Link className="score-match-link" href={`/partidos/${match.id}`} aria-label={`${presentation.primary}. ${scoreDescription}`}>
                                    <span className="score-match-state">
                                      <strong>{presentation.primary}</strong>
                                      {presentation.secondary && <small>{presentation.secondary}</small>}
                                      {halftimeScore && <small className="score-halftime">{halftimeScore}</small>}
                                    </span>
                                    <span className="score-match-teams">
                                      <span className="score-team-line">
                                        <span className="score-team-logo-shell"><SafeLogo src={match.home_logo} fallback={match.home_team} className="score-team-logo" /></span>
                                        <strong>{match.home_team}</strong>
                                      </span>
                                      <span className="score-team-line">
                                        <span className="score-team-logo-shell"><SafeLogo src={match.away_logo} fallback={match.away_team} className="score-team-logo" /></span>
                                        <strong>{match.away_team}</strong>
                                      </span>
                                    </span>
                                    <span className={`score-match-values ${hasScore ? "has-score" : ""}`} aria-hidden="true">
                                      <b>{typeof match.home_score === "number" ? match.home_score : "–"}</b>
                                      <b>{typeof match.away_score === "number" ? match.away_score : "–"}</b>
                                    </span>
                                    <ChevronRight className="score-match-arrow" size={17} aria-hidden="true" />
                                  </Link>
                                </article>
                              );
                            })}
                          </div>
                        </section>
                      );
                    })}
                  </section>
                );
              })}
            </div>
          )}
        </section>
      </div>
      <ResponsibleNote />
    </AppShell>
  );
}

function PartidosLoading() {
  return (
    <AppShell>
      <PageHeader
        eyebrow="AGENDA · RESULTADOS EN TIEMPO REAL"
        title="Partidos"
        action={<Link className="outline-link" href="/">Volver al panorama <ArrowUpRight size={15} /></Link>}
      />
      <div className="empty-state">Preparando agenda...</div>
      <ResponsibleNote />
    </AppShell>
  );
}

export default function PartidosPage() {
  return <Suspense fallback={<PartidosLoading />}><PartidosContent /></Suspense>;
}
