"""Cloud Trace via OpenTelemetry (addendum 14). One span per `core.timing.stage()`.

Enabled when PEAR_TRACE=1 (default on Cloud Run). Locally, spans are created but not
exported unless you set PEAR_TRACE=1 and have ADC. Failures here never block startup.
"""

from __future__ import annotations

import logging

log = logging.getLogger("pear.tracing")


def setup_tracing(app, enabled: bool, project: str) -> str:
    if not enabled:
        return "off"
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=project)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
        return "cloud_trace"
    except Exception as e:  # pragma: no cover - depends on env
        log.warning("tracing disabled: %r", e)
        return f"off ({type(e).__name__})"
