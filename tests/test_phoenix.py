#!/usr/bin/env python3
"""
Phoenix Telemetry Test Suite
Tests for Phoenix OTEL integration with various configurations.
"""

import asyncio
import os
import sys
import time
import logging
from phoenix.otel import register
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_phoenix")


# =============================================================================
# Test 1: Auto Configuration (using phoenix.otel.register)
# =============================================================================
class TestPhoenixAuto:
    """Test Phoenix connectivity using phoenix.otel.register()"""
    
    PHOENIX_ENDPOINT = "http://localhost:6006/v1/traces"
    PHOENIX_KEY = os.getenv("PHOENIX_API_KEY", "")
    
    def setup_tracer(self):
        logger.info(f"Setting up AUTO tracer pointing to {self.PHOENIX_ENDPOINT}")
        
        headers = {"authorization": f"Bearer {self.PHOENIX_KEY}"} if self.PHOENIX_KEY else {}
        logger.info(f"Using headers: {list(headers.keys())}")
        
        tracer_provider = register(
            project_name="test-auto-phoenix",
            endpoint=self.PHOENIX_ENDPOINT,
            headers=headers,
            batch=True,
            auto_instrument=False,
            set_global_tracer_provider=True
        )
        
        return trace.get_tracer(__name__)
    
    async def run(self):
        tracer = self.setup_tracer()
        
        logger.info("Starting AUTO test span...")
        with tracer.start_as_current_span("auto-test-span") as span:
            span.set_attribute("method", "auto")
            span.set_attribute("status", "running")
            time.sleep(0.5)
            span.set_attribute("status", "complete")
        
        logger.info("Forcing flush...")
        trace.get_tracer_provider().force_flush()
        logger.info("✓ Auto test complete")


# =============================================================================
# Test 2: Manual Configuration (using OTLPSpanExporter directly)
# =============================================================================
class TestPhoenixManual:
    """Test Phoenix connectivity with manual OTLP exporter configuration"""
    
    PHOENIX_ENDPOINT = "http://localhost:6006"
    PHOENIX_KEY = os.getenv("PHOENIX_API_KEY", "")
    
    def setup_tracer(self):
        logger.info(f"Setting up MANUAL tracer pointing to {self.PHOENIX_ENDPOINT}")
        
        resource = Resource.create({
            "service.name": "test-manual-phoenix",
            "service.version": "1.0.0"
        })
        
        tracer_provider = TracerProvider(resource=resource)
        
        endpoint = f"{self.PHOENIX_ENDPOINT}/v1/traces"
        headers = {"authorization": f"Bearer {self.PHOENIX_KEY}"} if self.PHOENIX_KEY else {}
        
        logger.info(f"Using headers: {list(headers.keys())}")
        
        otlp_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers)
        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        
        trace.set_tracer_provider(tracer_provider)
        return trace.get_tracer(__name__)
    
    async def run(self):
        tracer = self.setup_tracer()
        
        logger.info("Starting MANUAL test span...")
        with tracer.start_as_current_span("manual-test-span") as span:
            span.set_attribute("test.attribute", "value")
            span.set_attribute("status", "running")
            time.sleep(0.5)
            span.set_attribute("status", "complete")
        
        logger.info("Forcing flush...")
        trace.get_tracer_provider().force_flush()
        logger.info("✓ Manual test complete")


# =============================================================================
# Test 3: Container Environment (using ENV vars)
# =============================================================================
class TestPhoenixContainer:
    """Test Phoenix from within Docker container using environment variables"""
    
    def setup_tracer(self):
        endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
        api_key = os.getenv("PHOENIX_API_KEY")
        
        logger.info(f"ENV PHOENIX_COLLECTOR_ENDPOINT: {endpoint}")
        logger.info(f"ENV PHOENIX_API_KEY: {api_key[:10]}..." if api_key else "None")
        
        if not endpoint or not api_key:
            raise ValueError("Missing PHOENIX_COLLECTOR_ENDPOINT or PHOENIX_API_KEY")
        
        if not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint}/v1/traces"
        
        headers = {"authorization": f"Bearer {api_key}"}
        
        tracer_provider = register(
            project_name="test-container-phoenix",
            endpoint=endpoint,
            headers=headers,
            batch=True,
            auto_instrument=False,
            set_global_tracer_provider=False
        )
        
        return tracer_provider.get_tracer(__name__), tracer_provider
    
    async def run(self):
        tracer, provider = self.setup_tracer()
        
        logger.info("Starting CONTAINER test span...")
        with tracer.start_as_current_span("container-test-span") as span:
            span.set_attribute("method", "container")
            span.set_attribute("status", "running")
            time.sleep(0.5)
            span.set_attribute("status", "complete")
        
        logger.info("Forcing flush...")
        provider.force_flush()
        logger.info("✓ Container test complete")


# =============================================================================
# Test 4: Debug OTEL Headers
# =============================================================================
class TestDebugHeaders:
    """Debug script to verify headers are being sent correctly"""
    
    def run(self):
        print("=" * 80)
        print("Testing phoenix.otel.register with headers parameter")
        print("=" * 80)
        
        api_key = os.getenv("PHOENIX_API_KEY")
        endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
        
        if not endpoint.endswith("/v1/traces"):
            endpoint = f"{endpoint}/v1/traces"
        
        print(f"\n1. API Key: {api_key[:20]}..." if api_key else "None")
        print(f"2. Endpoint: {endpoint}")
        
        headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        print(f"3. Headers: {list(headers.keys())}")
        
        print("\n4. Calling phoenix.otel.register()...")
        try:
            tracer_provider = register(
                project_name="debug-test-project",
                endpoint=endpoint,
                headers=headers,
                batch=True,
                auto_instrument=False,
                set_global_tracer_provider=True
            )
            print("✓ Register succeeded!")
            
            tracer = trace.get_tracer(__name__)
            print("\n5. Creating test span...")
            
            with tracer.start_as_current_span("debug-test-span") as span:
                span.set_attribute("test", "value")
                time.sleep(0.1)
            
            print("✓ Span created")
            
            print("\n6. Forcing flush...")
            trace.get_tracer_provider().force_flush(timeout_millis=5000)
            print("✓ Flush complete")
            
            print("\n" + "=" * 80)
            print("SUCCESS - Check Phoenix logs for any 401 errors")
            print("=" * 80)
            
        except Exception as e:
            print(f"\n✗ Error: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        return True


# =============================================================================
# Main Runner
# =============================================================================
async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Phoenix Telemetry Test Suite")
    parser.add_argument("test", choices=["auto", "manual", "container", "debug", "all"],
                        help="Which test to run")
    args = parser.parse_args()
    
    if args.test == "auto" or args.test == "all":
        print("\n=== Running Auto Test ===")
        await TestPhoenixAuto().run()
    
    if args.test == "manual" or args.test == "all":
        print("\n=== Running Manual Test ===")
        await TestPhoenixManual().run()
    
    if args.test == "container" or args.test == "all":
        print("\n=== Running Container Test ===")
        try:
            await TestPhoenixContainer().run()
        except ValueError as e:
            print(f"Skipped: {e}")
    
    if args.test == "debug" or args.test == "all":
        print("\n=== Running Debug Test ===")
        TestDebugHeaders().run()


if __name__ == "__main__":
    asyncio.run(main())
