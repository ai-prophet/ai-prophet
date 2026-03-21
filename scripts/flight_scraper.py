"""Scan live Kiwi fares for cheap international trips from Chicago."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

MCP_URL = "https://mcp.kiwi.com"

# Curated toward destinations that are often realistic value plays from Chicago
# and meet the user's "outside the US / Canada / Mexico" constraint.
DESTINATIONS: tuple[tuple[str, str], ...] = (
    ("SDQ", "Santo Domingo"),
    ("PUJ", "Punta Cana"),
    ("POP", "Puerto Plata"),
    ("STI", "Santiago de los Caballeros"),
    ("MBJ", "Montego Bay"),
    ("KIN", "Kingston"),
    ("NAS", "Nassau"),
    ("AUA", "Aruba"),
    ("CUR", "Curacao"),
    ("BGI", "Bridgetown"),
    ("POS", "Port of Spain"),
    ("UVF", "St. Lucia"),
    ("GND", "Grenada"),
    ("SJO", "San Jose"),
    ("LIR", "Liberia"),
    ("PTY", "Panama City"),
    ("SAL", "San Salvador"),
    ("GUA", "Guatemala City"),
    ("BZE", "Belize City"),
    ("SAP", "San Pedro Sula"),
    ("RTB", "Roatan"),
    ("MGA", "Managua"),
    ("BOG", "Bogota"),
    ("MDE", "Medellin"),
    ("CTG", "Cartagena"),
    ("CLO", "Cali"),
    ("UIO", "Quito"),
    ("GYE", "Guayaquil"),
    ("LIM", "Lima"),
    ("SCL", "Santiago"),
    ("EZE", "Buenos Aires"),
    ("GIG", "Rio de Janeiro"),
    ("GRU", "Sao Paulo"),
    ("SSA", "Salvador"),
    ("KEF", "Reykjavik"),
    ("DUB", "Dublin"),
    ("SNN", "Shannon"),
    ("LIS", "Lisbon"),
    ("OPO", "Porto"),
    ("MAD", "Madrid"),
    ("BCN", "Barcelona"),
    ("PMI", "Palma de Mallorca"),
    ("AGP", "Malaga"),
    ("FAO", "Faro"),
    ("PDL", "Ponta Delgada"),
    ("FCO", "Rome"),
    ("MXP", "Milan"),
    ("VCE", "Venice"),
    ("NAP", "Naples"),
    ("CDG", "Paris"),
    ("ORY", "Paris Orly"),
    ("NCE", "Nice"),
    ("AMS", "Amsterdam"),
    ("BRU", "Brussels"),
    ("BER", "Berlin"),
    ("MUC", "Munich"),
    ("FRA", "Frankfurt"),
    ("WAW", "Warsaw"),
    ("KRK", "Krakow"),
    ("PRG", "Prague"),
    ("BUD", "Budapest"),
    ("VIE", "Vienna"),
    ("ZAG", "Zagreb"),
    ("DBV", "Dubrovnik"),
    ("SPU", "Split"),
    ("ATH", "Athens"),
    ("IST", "Istanbul"),
    ("SAW", "Istanbul Sabiha"),
    ("CMN", "Casablanca"),
    ("RAK", "Marrakesh"),
)


@dataclass(slots=True)
class FlightOption:
    destination_code: str
    destination_name: str
    destination_city: str
    origin_airport: str
    destination_airport: str
    price: float
    currency: str
    departure_local: str
    return_local: str
    total_duration_seconds: int
    outbound_duration_seconds: int
    return_duration_seconds: int
    total_layovers: int
    deep_link: str
    raw: dict[str, Any]

    @property
    def duration_label(self) -> str:
        hours, rem = divmod(self.total_duration_seconds, 3600)
        minutes = rem // 60
        return f"{hours}h {minutes:02d}m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find cheap international round-trip fares from Chicago via Kiwi MCP."
    )
    parser.add_argument("--fly-from", default="CHI", help="Origin city or airport code.")
    parser.add_argument(
        "--departure-date",
        default="26/06/2026",
        help="Departure date in dd/mm/yyyy format.",
    )
    parser.add_argument(
        "--return-date",
        default="03/07/2026",
        help="Return date in dd/mm/yyyy format.",
    )
    parser.add_argument(
        "--departure-flex-range",
        type=int,
        default=3,
        choices=range(0, 4),
        help="Flexible departure window in days.",
    )
    parser.add_argument(
        "--return-flex-range",
        type=int,
        default=3,
        choices=range(0, 4),
        help="Flexible return window in days.",
    )
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--locale", default="en")
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="How many ranked options to print.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=8,
        help="Maximum concurrent destination lookups.",
    )
    parser.add_argument(
        "--destination",
        action="append",
        default=[],
        help="Optional airport code to scan. Repeat to override the default destination set.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print ranked results as JSON instead of a table.",
    )
    return parser.parse_args()


def parse_sse_payload(body: str) -> Any:
    match = re.search(r"data: (.*)", body, re.S)
    if match is None:
        raise ValueError(f"Unexpected response body: {body[:400]}")

    payload = json.loads(match.group(1))
    result = payload["result"]
    if result.get("isError"):
        raise RuntimeError(result["content"][0]["text"])
    return json.loads(result["content"][0]["text"])


async def initialize_session(client: httpx.AsyncClient) -> str:
    response = await client.post(
        MCP_URL,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "ai-prophet-flight-scraper", "version": "0.1"},
            },
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.headers["mcp-session-id"]


async def search_destination(
    client: httpx.AsyncClient,
    session_id: str,
    args: argparse.Namespace,
    airport_code: str,
    airport_name: str,
) -> FlightOption | None:
    payload = {
        "jsonrpc": "2.0",
        "id": airport_code,
        "method": "tools/call",
        "params": {
            "name": "search-flight",
            "arguments": {
                "flyFrom": args.fly_from,
                "flyTo": airport_code,
                "departureDate": args.departure_date,
                "departureDateFlexRange": args.departure_flex_range,
                "returnDate": args.return_date,
                "returnDateFlexRange": args.return_flex_range,
                "passengers": {"adults": args.adults, "children": 0, "infants": 0},
                "cabinClass": "M",
                "sort": "price",
                "curr": args.currency,
                "locale": args.locale,
            },
        },
    }

    last_error: Exception | None = None
    for _ in range(3):
        try:
            response = await client.post(
                MCP_URL,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "mcp-session-id": session_id,
                },
                json=payload,
                timeout=90.0,
            )
            response.raise_for_status()
            flights = parse_sse_payload(response.text)
            if not flights:
                return None

            best = min(
                flights,
                key=lambda flight: (
                    flight["price"],
                    flight.get("totalDurationInSeconds", 10**12),
                    len(flight.get("layovers", [])) + len(flight.get("return", {}).get("layovers", [])),
                ),
            )
            return FlightOption(
                destination_code=airport_code,
                destination_name=airport_name,
                destination_city=best["cityTo"],
                origin_airport=best["flyFrom"],
                destination_airport=best["flyTo"],
                price=float(best["price"]),
                currency=best.get("currency", args.currency),
                departure_local=best["departure"]["local"],
                return_local=best["return"]["departure"]["local"],
                total_duration_seconds=int(best["totalDurationInSeconds"]),
                outbound_duration_seconds=int(best["durationInSeconds"]),
                return_duration_seconds=int(best["return"]["durationInSeconds"]),
                total_layovers=len(best.get("layovers", []))
                + len(best.get("return", {}).get("layovers", [])),
                deep_link=best["deepLink"],
                raw=best,
            )
        except Exception as exc:  # noqa: BLE001 - we want the last transient MCP error
            last_error = exc
            await asyncio.sleep(1.0)

    if last_error is not None:
        print(f"Skipping {airport_code}: {last_error}")
    return None


async def collect_results(args: argparse.Namespace) -> list[FlightOption]:
    selected = tuple((code.upper(), code.upper()) for code in args.destination) or DESTINATIONS
    semaphore = asyncio.Semaphore(args.max_concurrency)

    async with httpx.AsyncClient() as client:
        session_id = await initialize_session(client)

        async def guarded_lookup(code: str, name: str) -> FlightOption | None:
            async with semaphore:
                return await search_destination(client, session_id, args, code, name)

        results = await asyncio.gather(*(guarded_lookup(code, name) for code, name in selected))

    ranked = [result for result in results if result is not None]
    ranked.sort(key=lambda option: (option.price, option.total_duration_seconds, option.total_layovers))
    return ranked


def render_table(results: list[FlightOption], top_n: int) -> str:
    headers = [
        "#",
        "Destination",
        "Price",
        "Depart",
        "Return",
        "Origin",
        "Duration",
        "Stops",
        "Link",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for idx, result in enumerate(results[:top_n], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(idx),
                    f"{result.destination_city} ({result.destination_airport})",
                    f"{result.currency} {result.price:.0f}",
                    result.departure_local.replace("T", " "),
                    result.return_local.replace("T", " "),
                    result.origin_airport,
                    result.duration_label,
                    str(result.total_layovers),
                    result.deep_link,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    results = asyncio.run(collect_results(args))
    if not results:
        raise SystemExit("No flight options found.")

    if args.json:
        payload = [
            {
                "rank": idx,
                "destination_city": result.destination_city,
                "destination_airport": result.destination_airport,
                "origin_airport": result.origin_airport,
                "price": result.price,
                "currency": result.currency,
                "departure_local": result.departure_local,
                "return_local": result.return_local,
                "total_duration_seconds": result.total_duration_seconds,
                "total_layovers": result.total_layovers,
                "deep_link": result.deep_link,
            }
            for idx, result in enumerate(results[: args.top], start=1)
        ]
        print(json.dumps(payload, indent=2))
        return

    print(render_table(results, args.top))


if __name__ == "__main__":
    main()
