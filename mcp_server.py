"""
ArXiv MCP Server
================
Busca y descarga papers de arXiv.
Incluye fix SSL (certifi) y telemetría OpenTelemetry completa.
"""

import logging
import os
import pathlib
import time
import uuid
import warnings

import certifi
import httpx

# ── SSL fix: use certifi bundle (fixes macOS / corporate network SSL errors) ──
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")

import arxiv
from mcp.server.fastmcp import FastMCP

# ── OpenTelemetry ──────────────────────────────────────────────────────────────
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, StatusCode

OTLP_ENDPOINT   = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE_NAME    = "arxivsearcher-mcp"
SERVICE_VERSION = "0.1.0"


def _setup_telemetry():
    resource = Resource.create({
        "service.name":                SERVICE_NAME,
        "service.namespace":           "arxivsearcher",
        "service.version":             SERVICE_VERSION,
        "service.instance.id":         f"{SERVICE_NAME}-{uuid.uuid4().hex[:8]}",
        "deployment.environment.name": os.getenv("ENVIRONMENT", "local"),
        "ai.app.id":                   "arxivsearcher",
        "ai.use_case":                 "arxiv-research",
        "owner.team":                  "platform-ai",
    })
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
    trace.set_tracer_provider(tp)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True),
        export_interval_millis=5_000,
    )
    mp = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(mp)

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True)))
    set_logger_provider(lp)
    logging.getLogger().addHandler(LoggingHandler(level=logging.DEBUG, logger_provider=lp))

    return tp, mp, lp


_tp, _mp, _lp = _setup_telemetry()
_tracer = trace.get_tracer(SERVICE_NAME, SERVICE_VERSION)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger(SERVICE_NAME)

# ── Metrics instruments ────────────────────────────────────────────────────────
_meter = metrics.get_meter("arxivsearcher.mcp", version=SERVICE_VERSION)
_instruments = {
    "search_calls":   _meter.create_counter("arxiv.search.calls",   unit="{calls}",   description="ArXiv search invocations"),
    "search_results": _meter.create_histogram("arxiv.search.results", unit="{papers}", description="Papers returned per search"),
    "search_duration":_meter.create_histogram("arxiv.search.duration", unit="s",       description="ArXiv search duration"),
    "download_calls": _meter.create_counter("arxiv.download.calls", unit="{calls}",   description="ArXiv download invocations"),
    "download_duration": _meter.create_histogram("arxiv.download.duration", unit="s", description="ArXiv download duration"),
}

# ── Helpers ────────────────────────────────────────────────────────────────────

DOWNLOADS_DIR = pathlib.Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)


def _arxiv_client() -> arxiv.Client:
    """ArXiv client — SSL is handled via certifi env vars set at module top."""
    return arxiv.Client()


mcp = FastMCP("arxiv-searcher")


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_papers(topic: str, max_results: int = 10) -> list[dict]:
    """Search arxiv papers by topic. Returns a list with title, arxiv id, and brief description.

    Args:
        topic: The topic or keywords to search for.
        max_results: Maximum number of results to return (default 10, max 50).
    """
    max_results = min(max_results, 50)
    call_id = f"search-{uuid.uuid4().hex[:8]}"

    with _tracer.start_as_current_span(
        "execute_tool",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.tool.name":    "search_papers",
            "gen_ai.tool.type":    "function",
            "gen_ai.tool.call.id": call_id,
            "arxiv.search.topic":  topic,
            "arxiv.search.max_results": max_results,
        },
    ) as span:
        t0 = time.perf_counter()
        try:
            client = _arxiv_client()
            search = arxiv.Search(
                query=topic,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate,
            )
            results = []
            for paper in client.results(search):
                arxiv_id = paper.entry_id.split("/abs/")[-1]
                results.append({
                    "id": arxiv_id,
                    "title": paper.title,
                    "authors": [a.name for a in paper.authors[:3]],
                    "published": paper.published.strftime("%Y-%m-%d"),
                    "summary": paper.summary[:300] + "..." if len(paper.summary) > 300 else paper.summary,
                    "url": paper.entry_id,
                })
            duration = time.perf_counter() - t0
            span.set_attribute("arxiv.results.count", len(results))
            span.set_status(StatusCode.OK)
            _instruments["search_calls"].add(1, {"status": "success"})
            _instruments["search_results"].record(len(results), {"topic": topic[:50]})
            _instruments["search_duration"].record(duration)
            logger.info("ArXiv search completed", extra={
                "topic": topic, "results": len(results), "duration_s": round(duration, 3)
            })
            return results
        except Exception as e:
            duration = time.perf_counter() - t0
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, str(e))
            _instruments["search_calls"].add(1, {"status": "error"})
            _instruments["search_duration"].record(duration)
            logger.error(f"ArXiv search failed: {e}", extra={"topic": topic})
            raise


@mcp.tool()
def download_paper(arxiv_id: str) -> dict:
    """Download a paper PDF from arxiv given its ID.

    Args:
        arxiv_id: The arxiv paper ID (e.g. '2301.07041' or '2301.07041v2').
    """
    call_id = f"download-{uuid.uuid4().hex[:8]}"

    with _tracer.start_as_current_span(
        "execute_tool",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.tool.name":    "download_paper",
            "gen_ai.tool.type":    "function",
            "gen_ai.tool.call.id": call_id,
            "arxiv.paper.id":      arxiv_id,
        },
    ) as span:
        t0 = time.perf_counter()
        try:
            client = _arxiv_client()
            search = arxiv.Search(id_list=[arxiv_id])
            results = list(client.results(search))

            if not results:
                span.set_status(StatusCode.ERROR, "Paper not found")
                _instruments["download_calls"].add(1, {"status": "not_found"})
                return {"success": False, "error": f"No paper found with ID '{arxiv_id}'"}

            paper = results[0]
            safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in paper.title)[:80]
            filename = f"{arxiv_id.replace('/', '_')}_{safe_title}.pdf"
            output_path = DOWNLOADS_DIR / filename

            if output_path.exists():
                duration = time.perf_counter() - t0
                span.set_attribute("arxiv.download.cached", True)
                span.set_status(StatusCode.OK)
                _instruments["download_calls"].add(1, {"status": "cached"})
                _instruments["download_duration"].record(duration)
                logger.info("Paper already cached", extra={"arxiv_id": arxiv_id, "path": str(output_path)})
                return {"success": True, "message": "Paper already downloaded.", "path": str(output_path.resolve()), "title": paper.title}

            paper.download_pdf(dirpath=str(DOWNLOADS_DIR), filename=filename)
            duration = time.perf_counter() - t0
            size_mb = round(output_path.stat().st_size / 1_048_576, 2) if output_path.exists() else 0

            span.set_attribute("arxiv.download.cached",  False)
            span.set_attribute("arxiv.download.size_mb", size_mb)
            span.set_status(StatusCode.OK)
            _instruments["download_calls"].add(1, {"status": "success"})
            _instruments["download_duration"].record(duration)
            logger.info("Paper downloaded", extra={
                "arxiv_id": arxiv_id, "size_mb": size_mb, "duration_s": round(duration, 3)
            })
            return {
                "success": True,
                "message": "Paper downloaded successfully.",
                "path": str(output_path.resolve()),
                "title": paper.title,
                "authors": [a.name for a in paper.authors],
                "published": paper.published.strftime("%Y-%m-%d"),
            }
        except Exception as e:
            duration = time.perf_counter() - t0
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, str(e))
            _instruments["download_calls"].add(1, {"status": "error"})
            _instruments["download_duration"].record(duration)
            logger.error(f"Download failed: {e}", extra={"arxiv_id": arxiv_id})
            raise


if __name__ == "__main__":
    logger.info("Starting arxiv-searcher MCP server", extra={"otlp_endpoint": OTLP_ENDPOINT})
    mcp.run()
